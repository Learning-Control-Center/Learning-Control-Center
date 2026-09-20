#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly public_repository="https://github.com/Learning-Control-Center/Learning-Control-Center.git"

release_id=""
source_ref="HEAD"
repository_root=""
output_directory=""
force=false

usage() {
    cat <<'EOF'
Usage: scripts/package-release.sh --release-id VERSION --output-dir DIR [options]

Create the deterministic public source archive and SHA-256 file uploaded to the
matching GitHub release. VERSION must be a semantic release tag such as v1.0.0.

Options:
  --release-id VERSION  Required public release identity.
  --output-dir DIR      Required destination outside the source repository.
  --source-ref REF      Git commit or tag to package (default: HEAD).
  --repository DIR      Git repository to package (default: script repository).
  --force               Replace different assets already in the output directory.
  -h, --help            Show this help.
EOF
}

die() {
    printf 'package-release: %s\n' "$*" >&2
    exit 1
}

note() {
    printf 'package-release: %s\n' "$*"
}

while (($#)); do
    case "$1" in
        --release-id) release_id="${2:?Missing --release-id value}"; shift 2 ;;
        --output-dir) output_directory="${2:?Missing --output-dir value}"; shift 2 ;;
        --source-ref) source_ref="${2:?Missing --source-ref value}"; shift 2 ;;
        --repository) repository_root="${2:?Missing --repository value}"; shift 2 ;;
        --force) force=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ "$release_id" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] || \
    die "--release-id must be an explicit semantic release tag such as v1.0.0."
test -n "$output_directory" || die "--output-dir is required."

for command_name in git tar gzip sha256sum mktemp touch find; do
    command -v "$command_name" >/dev/null || die "Required command is unavailable: $command_name"
done

if test -z "$repository_root"; then
    repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
else
    repository_root="$(cd "$repository_root" && pwd -P)"
fi

git -C "$repository_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
    die "--repository must be a Git working tree."
source_commit="$(git -C "$repository_root" rev-parse --verify "${source_ref}^{commit}")" || \
    die "Unable to resolve source ref: $source_ref"

if test -n "$(git -C "$repository_root" status --porcelain --untracked-files=all)"; then
    die "The source repository must be clean before release packaging."
fi

tag_ref="refs/tags/$release_id"
git -C "$repository_root" show-ref --verify --quiet "$tag_ref" || \
    die "Release tag $release_id does not exist in the source repository."
tag_commit="$(git -C "$repository_root" rev-parse --verify "${tag_ref}^{commit}")"
test "$tag_commit" = "$source_commit" || \
    die "Release tag $release_id does not identify source ref $source_ref."

output_directory="$(mkdir -p "$output_directory" && cd "$output_directory" && pwd -P)"
case "$output_directory/" in
    "$repository_root/"*) die "--output-dir must be outside the source repository." ;;
esac

archive_name="learning-control-center-${release_id}.tar.gz"
checksum_name="${archive_name}.sha256"
top_level="Learning-Control-Center-${release_id}"
final_archive="$output_directory/$archive_name"
final_checksum="$output_directory/$checksum_name"
temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/lcc-release.XXXXXXXX")"
trap 'rm -rf -- "$temporary_directory"' EXIT

staging_root="$temporary_directory/$top_level"
mkdir -p "$staging_root"

git -C "$repository_root" archive --format=tar "$source_commit" | \
    tar -xf - -C "$staging_root"

# Public release assets deliberately omit contributor-only fixtures and every private/runtime path,
# even if one is accidentally tracked in the source repository.
rm -rf -- \
    "$staging_root/memory-bank" \
    "$staging_root/data" \
    "$staging_root/backups" \
    "$staging_root/tmp" \
    "$staging_root/.git" \
    "$staging_root/.abacusai" \
    "$staging_root/.agents" \
    "$staging_root/.codex" \
    "$staging_root/backend/tests" \
    "$staging_root/frontend/e2e" \
    "$staging_root/frontend/performance" \
    "$staging_root/frontend/qa"

while IFS= read -r -d '' candidate; do
    basename="${candidate##*/}"
    case "$basename" in
        .env.example|learning-control-center.env.example) continue ;;
        .env|.env.*|*.env|*.pem|*.key|*.p12|*.pfx|*.db|*.db-*|*.sqlite|*.sqlite-*|*.sqlite3|*.sqlite3-*)
            rm -f -- "$candidate"
            ;;
    esac
done < <(find "$staging_root" -type f -print0)

find "$staging_root/frontend/src" -type f \
    \( -name '*.test.ts' -o -name '*.test.tsx' \) -delete 2>/dev/null || true
rm -rf -- "$staging_root/frontend/src/test"

printf '%s\n' "$release_id" > "$staging_root/RELEASE_ID"
printf '%s\n' "$source_commit" > "$staging_root/SOURCE_REVISION"
cat > "$staging_root/RELEASE_MANIFEST" <<EOF
release_id=$release_id
source_revision=$source_commit
public_repository=$public_repository
EOF

required_paths=(
    AGENTS.md CHANGELOG.md README.md LICENSE SECURITY.md logo.png alembic.ini pyproject.toml
    requirements-production.lock backend/app/main.py backend/alembic/env.py
    deploy/Caddyfile.template deploy/learning-control-center.env.example
    deploy/learning-control-center.service deploy/learning-control-center-backup.service
    deploy/learning-control-center-backup.timer docs/INSTALLATION.md docs/PRODUCTION_OPERATIONS.md
    docs/UPDATES.md docs/RELEASING.md frontend/index.html frontend/package.json
    frontend/package-lock.json frontend/vite.config.ts frontend/src/main.tsx frontend/public/logo.png
    scripts/bootstrap-ubuntu.sh scripts/deploy-common.sh scripts/install-ubuntu.sh scripts/lcc-admin
    scripts/operational-backup.sh scripts/package-release.sh scripts/uninstall-ubuntu.sh
    scripts/update-ubuntu.sh RELEASE_ID SOURCE_REVISION RELEASE_MANIFEST
)
for relative_path in "${required_paths[@]}"; do
    test -f "$staging_root/$relative_path" || die "Required release file is missing: $relative_path"
done

test -x "$staging_root/scripts/bootstrap-ubuntu.sh" || die "Bootstrap script is not executable."
test -x "$staging_root/scripts/install-ubuntu.sh" || die "Installer is not executable."

if find "$staging_root" -type l -print -quit | grep -q .; then
    die "Release staging contains a symbolic link."
fi
if find "$staging_root" \( -path "$staging_root/memory-bank/*" -o \
    -path "$staging_root/.git/*" -o -path "$staging_root/data/*" -o \
    -path "$staging_root/backups/*" -o -path "$staging_root/tmp/*" -o \
    -name '*.db' -o -name '*.db-*' -o -name '*.sqlite' -o -name '*.sqlite-*' -o \
    -name '*.sqlite3' -o -name '*.sqlite3-*' -o -name '*.pem' -o -name '*.key' \) \
    -print -quit | grep -q .; then
    die "Release staging contains a forbidden private or runtime path."
fi

source_epoch="$(git -C "$repository_root" show -s --format=%ct "$source_commit")"
find "$staging_root" -exec touch -h -d "@$source_epoch" {} +

temporary_archive="$temporary_directory/$archive_name"
LC_ALL=C tar --sort=name --format=gnu --mtime="@$source_epoch" \
    --owner=0 --group=0 --numeric-owner -C "$temporary_directory" -cf - "$top_level" | \
    gzip -n -9 > "$temporary_archive"
temporary_checksum="$temporary_directory/$checksum_name"
(cd "$temporary_directory" && sha256sum "$archive_name" > "$checksum_name")

if test -e "$final_archive" || test -e "$final_checksum"; then
    if test -f "$final_archive" && test -f "$final_checksum" && \
        cmp -s "$temporary_archive" "$final_archive" && \
        cmp -s "$temporary_checksum" "$final_checksum"; then
        note "Existing release assets are byte-identical."
        exit 0
    fi
    $force || die "Release assets already exist with different content; pass --force to replace them."
fi

install -m 0644 "$temporary_archive" "$final_archive"
install -m 0644 "$temporary_checksum" "$final_checksum"

note "Created $final_archive"
note "Created $final_checksum"
note "Release identity: $release_id"
note "Source revision: $source_commit"
