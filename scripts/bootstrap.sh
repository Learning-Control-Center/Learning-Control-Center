#!/usr/bin/env bash
set -euo pipefail
umask 077

# Resolve public main once, validate its immutable source, then hand off to
# the common installer. The selected GitHub repository is the source boundary.
readonly source_repository=https://github.com/Learning-Control-Center/Learning-Control-Center.git
readonly source_ref=refs/heads/main
readonly ref_url=https://api.github.com/repos/Learning-Control-Center/Learning-Control-Center/git/ref/heads/main
readonly raw_base=https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center
readonly archive_base=https://api.github.com/repos/Learning-Control-Center/Learning-Control-Center/tarball
readonly maximum_bootstrap_bytes=1048576
readonly maximum_archive_bytes=268435456
readonly maximum_unpacked_bytes=536870912
readonly maximum_members=20000

die() { printf 'lcc-bootstrap-v2: %s\n' "$*" >&2; exit 1; }
note() { printf 'lcc-bootstrap-v2: %s\n' "$*"; }

usage() {
    cat <<'EOF'
Usage: bootstrap.sh [--domain HOST|--public-origin HTTPS_ORIGIN] [--timezone ZONE] [--app-port PORT]
                    [--gateway caddy|external] [--non-interactive] [--yes]

Install or update from public GitHub main, selected once by full commit SHA.
Managed Caddy is the default gateway. An external same-host reverse proxy can
be selected explicitly; its public HTTPS integration is operator-managed.

--commit FULL_SHA  Require public main to equal this full commit SHA.
--help             Show this information without contacting GitHub.

The V2 source is fixed to public GitHub. The V1 --repository-url and stable
release options are not supported by this new entry point.
EOF
}

original_arguments=("$@")
expected_commit=""
commit_was_set=0
channel=main
installer_arguments=()
while test "$#" -gt 0; do
    case "$1" in
        --commit)
            test "$#" -ge 2 || die "--commit requires a full SHA."
            test "$commit_was_set" -eq 0 || die "--commit may be supplied only once."
            expected_commit="$2"
            commit_was_set=1
            installer_arguments+=("$1" "$2")
            shift 2 ;;
        --commit=*)
            test "$commit_was_set" -eq 0 || die "--commit may be supplied only once."
            expected_commit="${1#*=}"
            commit_was_set=1
            installer_arguments+=("$1")
            shift ;;
        --channel)
            test "$#" -ge 2 || die "--channel requires a value."
            channel="$2"
            installer_arguments+=("$1" "$2")
            shift 2 ;;
        --channel=*) channel="${1#*=}"; installer_arguments+=("$1"); shift ;;
        --repository-url|--repository-url=*|--asset-base-url|--asset-base-url=*|--ref|--ref=*)
            die "$1 belongs to the V1 source/release interface; Installer V2 main source is fixed to public GitHub." ;;
        -h|--help) usage; exit 0 ;;
        *) installer_arguments+=("$1"); shift ;;
    esac
done
test "$channel" = main || die "Installer V2 supports only public main."
if test "$commit_was_set" -eq 1; then
    [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] || die "--commit must be a lowercase full 40-character SHA."
fi

for command_name in bash curl python3 tar mktemp stat chmod head rm; do
    command -v "$command_name" >/dev/null || die "Required pre-provisioning tool is unavailable: $command_name"
done

temporary_parent=/tmp
if test "${EUID:-$(id -u)}" -ne 0; then
    temporary_parent="${TMPDIR:-/tmp}"
fi
temporary_directory="$(mktemp -d "$temporary_parent/lcc-bootstrap-v2.XXXXXXXX")"
chmod 0700 "$temporary_directory"
trap 'rm -rf -- "$temporary_directory"' EXIT

download() {
    local limit="$1" destination="$2" url="$3"
    curl -q --fail --location --silent --show-error --retry 3 --max-time 120 \
        --max-redirs 5 --max-filesize "$limit" \
        --proto '=https' --proto-redir '=https' --tlsv1.2 \
        --output "$destination" "$url" || die "HTTPS source request failed: $url"
    test -s "$destination" && test "$(stat -c %s "$destination")" -le "$limit" || \
        die "Source response is empty or exceeds its size limit: $url"
}

selected_sha="${LCC_V2_PINNED_SHA:-}"
if test -n "$selected_sha"; then
    test -n "${LCC_V2_PINNED_BOOTSTRAP_PATH:-}" && \
        test "${BASH_SOURCE[0]:-}" = "$LCC_V2_PINNED_BOOTSTRAP_PATH" || \
        die "Pinned state is valid only during the exact-SHA bootstrap handoff."
fi
if test -z "$selected_sha"; then
    download "$maximum_bootstrap_bytes" "$temporary_directory/main-ref.json" "$ref_url"
    selected_sha="$(python3 -I - "$temporary_directory/main-ref.json" <<'PY'
import json
import re
import sys

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        response = json.load(stream, object_pairs_hook=unique_object)
    if not isinstance(response, dict) or response.get("ref") != "refs/heads/main":
        raise ValueError("unexpected ref")
    obj = response["object"]
    if not isinstance(obj, dict) or obj.get("type") != "commit":
        raise ValueError("ref does not identify a commit")
    sha = obj["sha"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("noncanonical commit SHA")
except (KeyError, TypeError, ValueError, UnicodeError) as error:
    raise SystemExit(f"Invalid or ambiguous GitHub main ref response: {error}") from error
print(sha)
PY
    )" || die "Could not select one full GitHub main commit SHA."
    [[ "$selected_sha" =~ ^[0-9a-f]{40}$ ]] || die "GitHub returned a noncanonical main SHA."
    test -z "$expected_commit" || test "$expected_commit" = "$selected_sha" || \
        die "--commit does not match the selected public main SHA."

    pinned_bootstrap="$temporary_directory/bootstrap.sh"
    download "$maximum_bootstrap_bytes" "$pinned_bootstrap" \
        "$raw_base/$selected_sha/scripts/bootstrap.sh"
    test "$(head -n 1 "$pinned_bootstrap")" = '#!/usr/bin/env bash' || \
        die "The SHA-addressed bootstrap has an invalid script header."
    chmod 0700 "$pinned_bootstrap"
    note "Selected public GitHub main SHA: $selected_sha"
    LCC_V2_PINNED_SHA="$selected_sha" \
    LCC_V2_PINNED_BOOTSTRAP_PATH="$pinned_bootstrap" \
        bash "$pinned_bootstrap" "${original_arguments[@]}"
    exit $?
fi

[[ "$selected_sha" =~ ^[0-9a-f]{40}$ ]] || die "Inherited SHA is invalid."
test -z "$expected_commit" || test "$expected_commit" = "$selected_sha" || \
    die "--commit does not match the inherited SHA."
archive="$temporary_directory/source.tar.gz"
download "$maximum_archive_bytes" "$archive" "$archive_base/$selected_sha"

archive_root="$(python3 -I - "$archive" "$selected_sha" "$maximum_unpacked_bytes" "$maximum_members" <<'PY'
from pathlib import PurePosixPath
import gzip
import re
import sys
import tarfile

archive, sha = sys.argv[1:3]
max_size, max_members = map(int, sys.argv[3:5])
expected_prefix = "Learning-Control-Center-Learning-Control-Center-"
seen = set()
regular_files = set()
executable_files = set()
parent_paths = set()
root = None
expanded_size = 0

try:
    # Consume the complete gzip stream, including its CRC/trailer, before tar
    # extraction. tarfile can otherwise stop at the tar end marker early.
    stream_size = 0
    with gzip.open(archive, "rb") as compressed:
        while chunk := compressed.read(1024 * 1024):
            stream_size += len(chunk)
            if stream_size > 671088640:
                raise ValueError("archive decompressed stream exceeds limit")
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            name = member.name.rstrip("/")
            path = PurePosixPath(name)
            if (
                not name or "\\" in name or path.is_absolute()
                or ".." in path.parts or "/".join(path.parts) != name
                or len(name) > 4096 or len(path.parts) > 64
                or any(ord(char) < 32 for char in name)
            ):
                raise ValueError(f"unsafe archive path: {member.name!r}")
            parts = path.parts
            if root is None:
                root = parts[0]
                suffix = root.removeprefix(expected_prefix)
                if (
                    not root.startswith(expected_prefix)
                    or not re.fullmatch(r"[0-9a-f]{7,40}", suffix)
                    or not sha.startswith(suffix)
                ):
                    raise ValueError("unexpected GitHub archive root")
            if parts[0] != root or name in seen:
                raise ValueError(f"archive root or member is duplicated: {name!r}")
            seen.add(name)
            if len(seen) > max_members:
                raise ValueError("archive has too many members")
            if not (member.isdir() or member.isfile()) or member.mode & 0o7000:
                raise ValueError(f"unsupported archive member or privileged mode: {name!r}")
            parents = {"/".join(parts[:index]) for index in range(1, len(parts))}
            if parents & regular_files or (member.isfile() and name in parent_paths):
                raise ValueError(f"archive member has a file/directory collision: {name!r}")
            parent_paths.update(parents)
            if member.isfile():
                if member.size < 0:
                    raise ValueError(f"negative archive member size: {name!r}")
                regular_files.add(name)
                if member.mode & 0o100:
                    executable_files.add(name)
                expanded_size += member.size
                if expanded_size > max_size:
                    raise ValueError("archive expanded size exceeds limit")
    if root is None:
        raise ValueError("archive is empty")
    required = {
        "README.md", "pyproject.toml", "scripts/bootstrap.sh",
        "scripts/install.sh", "scripts/install/transition.sh",
        "scripts/install/platforms/ubuntu-24.04.sh",
    }
    if not all(f"{root}/{path}" in regular_files for path in required):
        raise ValueError("archive lacks required installer source files")
    if not all(f"{root}/{path}" in executable_files for path in ("scripts/bootstrap.sh", "scripts/install.sh")):
        raise ValueError("archive bootstrap executable mode is missing")
except (tarfile.TarError, EOFError, OSError, ValueError) as error:
    raise SystemExit(f"Unsafe or invalid GitHub source archive: {error}") from error
print(root)
PY
)" || die "Source archive validation failed before extraction."

tar --extract --gzip --file "$archive" --directory "$temporary_directory" \
    --no-same-owner --no-same-permissions || die "Source archive extraction failed."
source_root="$temporary_directory/$archive_root"
test -d "$source_root" && test -x "$source_root/scripts/bootstrap.sh" && \
    test -x "$source_root/scripts/install.sh" || \
    die "Extracted source does not retain its required layout and executable modes."

# This private tree is cleaned after the common installer exits.
export LCC_V2_SOURCE_ROOT="$source_root"
export LCC_V2_SOURCE_REPOSITORY="$source_repository"
export LCC_V2_SOURCE_REF="$source_ref"
export LCC_V2_SOURCE_SHA="$selected_sha"
note "Validated exact-SHA source: $LCC_V2_SOURCE_SHA"
if test "${LCC_BOOTSTRAP_TEST_ACQUIRE_ONLY:-0}" = 1 &&
    test "${LCC_BOOTSTRAP_TESTING:-0}" = 1; then
    note 'Validated source acquisition only (test fixture).'
    exit 0
fi
"$source_root/scripts/install.sh" "${installer_arguments[@]}"
