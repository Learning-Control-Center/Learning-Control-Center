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

Create the deterministic public source archive, SHA-256 file, and release-bound
stable install.sh uploaded to the matching release. VERSION must be a semantic
release tag such as v1.0.1.

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
    die "--release-id must be an explicit semantic release tag such as v1.0.1."
test -n "$output_directory" || die "--output-dir is required."

for command_name in git tar gzip sha256sum mktemp touch find cut sed grep node npm python3; do
    command -v "$command_name" >/dev/null || die "Required command is unavailable: $command_name"
done
node -e 'const major=Number(process.versions.node.split(".")[0]); process.exit([22, 24].includes(major) ? 0 : 1)' || \
    die "Release packaging requires Node.js 22 LTS or 24 LTS."

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

test -x "$staging_root/scripts/frontend-artifact.py" || \
    die "Release source lacks the executable frontend artifact verifier."
"$staging_root/scripts/frontend-artifact.py" verify --root "$staging_root" >/dev/null || \
    die "Tracked frontend artifact is stale or invalid."
tracked_frontend_manifest="$(cat "$staging_root/frontend/dist/LCC_FRONTEND_ARTIFACT.json")"
note "Rebuilding the production frontend from package-lock.json"
npm --prefix "$staging_root/frontend" ci --ignore-scripts >/dev/null
npm --prefix "$staging_root/frontend" run build >/dev/null
"$staging_root/scripts/frontend-artifact.py" write --root "$staging_root" >/dev/null
test "$(cat "$staging_root/frontend/dist/LCC_FRONTEND_ARTIFACT.json")" = \
    "$tracked_frontend_manifest" || \
    die "Fresh frontend build does not match the tracked verified artifact."
rm -rf -- "$staging_root/frontend/node_modules"

# Public release assets deliberately omit contributor-only fixtures and every private/runtime path,
# even if one is accidentally tracked in the source repository.
rm -rf -- \
    "$staging_root/AGENTS.md" \
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

find "$staging_root" -depth -type d \
    \( -name .aws -o -name .ssh -o -name .docker \) \
    -exec rm -rf -- {} +

while IFS= read -r -d '' candidate; do
    basename="${candidate##*/}"
    case "$basename" in
        .env.example|learning-control-center.env.example) continue ;;
        .env|.env.*|*.env|.npmrc|.pypirc|.netrc|.git-credentials|pip.conf|*.pem|*.key|*.p12|*.pfx|*.db|*.db-*|*.sqlite|*.sqlite-*|*.sqlite3|*.sqlite3-*)
            rm -f -- "$candidate"
            ;;
    esac
done < <(find "$staging_root" -type f -print0)

find "$staging_root/frontend/src" -type f \
    \( -name '*.test.ts' -o -name '*.test.tsx' \) -delete 2>/dev/null || true
rm -rf -- "$staging_root/frontend/src/test"

printf '%s\n' "$release_id" > "$staging_root/RELEASE_ID"
printf 'stable\n' > "$staging_root/RELEASE_CHANNEL"
printf '%s\n' "$source_commit" > "$staging_root/SOURCE_REVISION"
cat > "$staging_root/RELEASE_MANIFEST" <<EOF
metadata_version=1
channel=stable
release_id=$release_id
source_repository=$public_repository
source_ref=refs/tags/$release_id
source_revision=$source_commit
source_origin=https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download
EOF

required_paths=(
    CHANGELOG.md README.md LICENSE SECURITY.md logo.png alembic.ini pyproject.toml
    requirements-production.lock backend/app/main.py backend/alembic/env.py
    deploy/Caddyfile.template deploy/learning-control-center.env.example
    deploy/learning-control-center-update.sh
    deploy/learning-control-center.service deploy/learning-control-center-backup.service
    deploy/learning-control-center-backup.timer docs/INSTALLATION.md docs/PRODUCTION_OPERATIONS.md
    docs/UPDATES.md docs/RELEASING.md frontend/index.html frontend/package.json
    frontend/package-lock.json frontend/vite.config.ts frontend/src/main.tsx frontend/public/logo.png
    frontend/dist/index.html frontend/dist/LCC_FRONTEND_ARTIFACT.json
    scripts/bootstrap-ubuntu.sh scripts/deploy-common.sh scripts/generate-production-env.sh
    scripts/frontend-artifact.py scripts/install-ubuntu.sh scripts/lcc-admin
    scripts/operational-backup.sh scripts/package-release.sh scripts/prepare-public-promotion.sh
    scripts/uninstall-ubuntu.sh
    scripts/update.sh scripts/update-ubuntu.sh RELEASE_ID RELEASE_CHANNEL SOURCE_REVISION RELEASE_MANIFEST
)
for relative_path in "${required_paths[@]}"; do
    test -f "$staging_root/$relative_path" || die "Required release file is missing: $relative_path"
done

test -x "$staging_root/scripts/bootstrap-ubuntu.sh" || die "Bootstrap script is not executable."
test -x "$staging_root/scripts/generate-production-env.sh" || \
    die "Environment generator is not executable."
test -x "$staging_root/scripts/install-ubuntu.sh" || die "Installer is not executable."
test -x "$staging_root/scripts/update.sh" || die "User-facing updater is not executable."
test -x "$staging_root/scripts/update-ubuntu.sh" || die "Canonical updater is not executable."
test -x "$staging_root/scripts/prepare-public-promotion.sh" || \
    die "Public promotion tool is not executable."
test -x "$staging_root/deploy/learning-control-center-update.sh" || \
    die "Persistent update wrapper is not executable."
test -x "$staging_root/scripts/frontend-artifact.py" || \
    die "Frontend artifact verifier is not executable."
"$staging_root/scripts/frontend-artifact.py" verify --root "$staging_root" >/dev/null || \
    die "Packaged frontend artifact failed verification."

if find "$staging_root" -type l -print -quit | grep -q .; then
    die "Release staging contains a symbolic link."
fi
if test -e "$staging_root/AGENTS.md" || \
    find "$staging_root" \( -path "$staging_root/memory-bank" -o \
    -path "$staging_root/memory-bank/*" -o \
    -path "$staging_root/.git/*" -o -path "$staging_root/data/*" -o \
    -path "$staging_root/backups/*" -o -path "$staging_root/tmp/*" -o \
    -name .aws -o -name .ssh -o -name .docker -o \
    -name .npmrc -o -name .pypirc -o -name .netrc -o \
    -name .git-credentials -o -name pip.conf -o \
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
archive_sha256="$(sha256sum "$temporary_archive" | cut -d' ' -f1)"
temporary_install="$temporary_directory/install.sh"
sed \
    -e "s|^readonly embedded_stable_ref=\"\"$|readonly embedded_stable_ref=\"$release_id\"|" \
    -e "s|^readonly embedded_archive_sha256=\"\"$|readonly embedded_archive_sha256=\"$archive_sha256\"|" \
    -e 's|^readonly stable_only_launcher="0"$|readonly stable_only_launcher="1"|' \
    "$staging_root/scripts/bootstrap-ubuntu.sh" > "$temporary_install"
chmod 0755 "$temporary_install"
grep -Fqx "readonly embedded_stable_ref=\"$release_id\"" "$temporary_install" || \
    die "Failed to bind install.sh to the release identity."
grep -Fqx "readonly embedded_archive_sha256=\"$archive_sha256\"" "$temporary_install" || \
    die "Failed to bind install.sh to the release archive checksum."
grep -Fqx 'readonly stable_only_launcher="1"' "$temporary_install" || \
    die "Failed to render install.sh as stable-only."

final_install="$output_directory/install.sh"

if test -e "$final_archive" || test -e "$final_checksum" || test -e "$final_install"; then
    if test -f "$final_archive" && test -f "$final_checksum" && test -f "$final_install" && \
        cmp -s "$temporary_archive" "$final_archive" && \
        cmp -s "$temporary_checksum" "$final_checksum" && \
        cmp -s "$temporary_install" "$final_install"; then
        note "Existing release assets are byte-identical."
        exit 0
    fi
    $force || die "Release assets already exist with different content; pass --force to replace them."
fi

install -m 0644 "$temporary_archive" "$final_archive"
install -m 0644 "$temporary_checksum" "$final_checksum"
install -m 0755 "$temporary_install" "$final_install"

note "Created $final_archive"
note "Created $final_checksum"
note "Created $final_install"
note "Release identity: $release_id"
note "Source revision: $source_commit"
