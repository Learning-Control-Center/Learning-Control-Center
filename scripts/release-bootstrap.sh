#!/usr/bin/env bash
set -euo pipefail
umask 077

# Rendered only by package-release.sh after the public archive is finalized.
readonly release_id='@LCC_RELEASE_ID@'
readonly source_sha='@LCC_SOURCE_SHA@'
readonly archive_sha256='@LCC_ARCHIVE_SHA256@'
readonly repository=https://github.com/Learning-Control-Center/Learning-Control-Center.git
readonly asset_origin=https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download

die() { printf 'lcc-release: %s\n' "$*" >&2; exit 1; }

for argument in "$@"; do
    case "$argument" in
        -h|--help)
            printf 'Usage: install.sh [--domain HOST] [--timezone ZONE] [--app-port PORT] [--gateway caddy|external] [--env-file PATH] [--non-interactive]\n'
            printf 'Fresh pinned release: %s (%s). Existing installations update from public main.\n' "$release_id" "$source_sha"
            exit 0 ;;
        --repository-url*|--asset-base-url*|--ref*|--channel*|--commit*)
            die 'The release-bound install.sh does not accept source or channel overrides.' ;;
    esac
done
for required in bash curl python3 tar sha256sum mktemp stat chmod cut cat rm; do
    command -v "$required" >/dev/null || die "Required pre-provisioning tool is unavailable: $required"
done

temporary="$(mktemp -d /tmp/lcc-release.XXXXXXXX)"
chmod 0700 "$temporary"
trap 'rm -rf -- "$temporary"' EXIT
archive="$temporary/source.tar.gz"
curl -q --fail --location --silent --show-error --retry 3 --max-time 120 \
    --max-redirs 5 --max-filesize 268435456 \
    --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --output "$archive" "$asset_origin/$release_id/learning-control-center-$release_id.tar.gz" ||
    die 'Pinned release archive could not be downloaded.'
test -s "$archive" && test "$(stat -c %s "$archive")" -le 268435456 ||
    die 'Pinned release archive is empty or exceeds the size limit.'
test "$(sha256sum "$archive" | cut -d' ' -f1)" = "$archive_sha256" ||
    die 'Pinned release archive SHA-256 does not match its release-bound installer.'

python3 -I - "$archive" "$release_id" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

archive, release_id = sys.argv[1:]
root = f"Learning-Control-Center-{release_id}"
seen: set[str] = set()
files: set[str] = set()
directories: set[str] = set()
expanded = 0
try:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            name = member.name
            path = PurePosixPath(name)
            if (not name or name.startswith("/") or ".." in path.parts
                    or any(ord(character) < 32 for character in name)
                    or name.rstrip("/") != path.as_posix()
                    or path.parts[0] != root or path.as_posix() in seen):
                raise ValueError(f"unsafe or duplicate archive path: {name!r}")
            seen.add(path.as_posix())
            if len(seen) > 20000:
                raise ValueError("too many archive members")
            if not (member.isdir() or member.isfile()) or member.mode & 0o7000:
                raise ValueError(f"unsafe archive member type or mode: {name!r}")
            parents = {"/".join(path.parts[:n]) for n in range(1, len(path.parts))}
            if parents & files or (member.isfile() and name in directories):
                raise ValueError(f"file/directory collision: {name!r}")
            directories.update(parents)
            if member.isfile():
                files.add(name)
                expanded += member.size
                if member.size < 0 or expanded > 536870912:
                    raise ValueError("invalid or excessive expanded archive size")
        required = ("README.md", "pyproject.toml", "scripts/install.sh",
                    "scripts/install/transition.sh", "RELEASE_MANIFEST", "SOURCE_REVISION")
        if not all(f"{root}/{path}" in files for path in required):
            raise ValueError("release archive lacks required installer files")
        installer = bundle.getmember(f"{root}/scripts/install.sh")
        if not installer.mode & 0o100:
            raise ValueError("release installer is not executable")
except (OSError, EOFError, ValueError, tarfile.TarError) as error:
    raise SystemExit(f"Invalid release archive: {error}") from error
PY
tar -xzf "$archive" -C "$temporary" --no-same-owner --no-same-permissions ||
    die 'Validated release archive extraction failed.'
source_root="$temporary/Learning-Control-Center-$release_id"
test -x "$source_root/scripts/install.sh" || die 'Release installer executable mode was not preserved.'
test "$(cat "$source_root/SOURCE_REVISION")" = "$source_sha" ||
    die 'Release source revision does not match the bound commit.'
export LCC_V2_SOURCE_ROOT="$source_root"
export LCC_V2_SOURCE_REPOSITORY="$repository"
export LCC_V2_SOURCE_REF="refs/tags/$release_id"
export LCC_V2_SOURCE_SHA="$source_sha"
export LCC_V2_RELEASE_ID="$release_id"
"$source_root/scripts/install.sh" "$@"
