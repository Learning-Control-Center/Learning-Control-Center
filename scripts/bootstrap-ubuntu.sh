#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly public_repository="https://github.com/Learning-Control-Center/Learning-Control-Center"
readonly maximum_archive_bytes=268435456
readonly maximum_unpacked_bytes=536870912

release_ref=""
domain=""
environment_file=""
dry_run=false

usage() {
    cat <<'EOF'
Usage: bootstrap-ubuntu.sh --ref REF --domain HOST --env-file FILE [--dry-run]

Download and verify one explicit Learning Control Center GitHub release, then
delegate installation to its canonical scripts/install-ubuntu.sh.

REF must be a semantic release tag such as v1.0.0 or a full 40-character Git
commit identity with deliberately published matching release assets. The
bootstrap never selects main or latest.

The environment file must already exist and contain the production secrets.
Secrets are never accepted as bootstrap command-line arguments.
EOF
}

die() {
    printf 'lcc-bootstrap: %s\n' "$*" >&2
    exit 1
}

note() {
    printf 'lcc-bootstrap: %s\n' "$*"
}

while (($#)); do
    case "$1" in
        --ref) release_ref="${2:?Missing --ref value}"; shift 2 ;;
        --domain) domain="${2:?Missing --domain value}"; shift 2 ;;
        --env-file) environment_file="${2:?Missing --env-file value}"; shift 2 ;;
        --dry-run) dry_run=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

test -n "$release_ref" || die "--ref is required; production never defaults to main or latest."
if [[ ! "$release_ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] && \
    [[ ! "$release_ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    die "--ref must be a semantic release tag or full 40-character commit identity."
fi
test -n "$domain" || die "--domain is required."
[[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || \
    die "--domain must be a DNS hostname without a scheme, port, path, or whitespace."
test -n "$environment_file" || die "--env-file is required."
case "$environment_file" in
    /*) ;;
    *) die "--env-file must be an absolute path." ;;
esac
test -f "$environment_file" || die "Environment file does not exist: $environment_file"

test_mode="${LCC_BOOTSTRAP_TESTING:-0}"
if ! $dry_run && test "$test_mode" != 1 && test "${EUID:-$(id -u)}" -ne 0; then
    die "Run the bootstrap as root, normally through sudo."
fi

os_release_file="/etc/os-release"
if test "$test_mode" = 1 && test -n "${LCC_BOOTSTRAP_OS_RELEASE:-}"; then
    os_release_file="$LCC_BOOTSTRAP_OS_RELEASE"
fi
test -r "$os_release_file" || die "Cannot read the operating-system identity file."
os_id="$(sed -n 's/^ID=//p' "$os_release_file" | head -n1 | tr -d '"')"
os_version="$(sed -n 's/^VERSION_ID=//p' "$os_release_file" | head -n1 | tr -d '"')"
if test "$os_id" != ubuntu || test "$os_version" != 24.04; then
    die "Supported production baseline is Ubuntu Server 24.04 LTS; found $os_id $os_version."
fi

for command_name in curl python3 sha256sum tar mktemp stat sed head tr wc; do
    command -v "$command_name" >/dev/null || die "Required acquisition command is unavailable: $command_name"
done

asset_name="learning-control-center-${release_ref}.tar.gz"
checksum_name="${asset_name}.sha256"
asset_base_url="${LCC_BOOTSTRAP_ASSET_BASE_URL:-$public_repository/releases/download}"
if test "$test_mode" != 1 && [[ "$asset_base_url" != https://* ]]; then
    die "Release assets must be downloaded over HTTPS."
fi
release_base_url="${asset_base_url%/}/$release_ref"
archive_url="$release_base_url/$asset_name"
checksum_url="$release_base_url/$checksum_name"

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/lcc-bootstrap.XXXXXXXX")"
trap 'rm -rf -- "$temporary_directory"' EXIT
archive_path="$temporary_directory/$asset_name"
checksum_path="$temporary_directory/$checksum_name"

curl_options=(--fail --location --silent --show-error --retry 3 --max-filesize "$maximum_archive_bytes")
if test "$test_mode" = 1; then
    curl_options+=(--proto '=https,file')
else
    curl_options+=(--proto '=https' --tlsv1.2)
fi

note "Requested release: $release_ref"
note "Downloading bounded release archive from $archive_url"
curl "${curl_options[@]}" --output "$checksum_path" "$checksum_url"
curl "${curl_options[@]}" --output "$archive_path" "$archive_url"

test "$(stat -c %s "$archive_path")" -le "$maximum_archive_bytes" || \
    die "Release archive exceeds the maximum allowed size."
checksum_line="$(sed -n '1p' "$checksum_path")"
test "$(wc -l < "$checksum_path")" -eq 1 || die "Checksum file must contain exactly one entry."
checksum_hash="${checksum_line%%[[:space:]]*}"
checksum_file="${checksum_line##*[[:space:]]}"
[[ "$checksum_hash" =~ ^[0-9a-fA-F]{64}$ ]] || die "Checksum file contains an invalid SHA-256 value."
test "$checksum_file" = "$asset_name" || die "Checksum file names an unexpected release asset."
printf '%s  %s\n' "$checksum_hash" "$asset_name" > "$temporary_directory/expected.sha256"
(cd "$temporary_directory" && sha256sum --check --status expected.sha256) || \
    die "Release archive SHA-256 verification failed."

expected_top_level="Learning-Control-Center-${release_ref}"
python3 - "$archive_path" "$expected_top_level" "$maximum_unpacked_bytes" <<'PY'
from pathlib import PurePosixPath
import sys
import tarfile

archive, expected_root, maximum_size = sys.argv[1], sys.argv[2], int(sys.argv[3])
seen: set[str] = set()
total_size = 0
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    if not members:
        raise SystemExit("release archive is empty")
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe release path: {name!r}")
        if path.parts[0] != expected_root:
            raise SystemExit(f"unexpected release root: {name!r}")
        if name in seen:
            raise SystemExit(f"duplicate release path: {name!r}")
        seen.add(name)
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported release member type: {name!r}")
        total_size += member.size
        if total_size > maximum_size:
            raise SystemExit("release archive exceeds the maximum unpacked size")
PY

tar --extract --gzip --file "$archive_path" --directory "$temporary_directory" \
    --no-same-owner --no-same-permissions
release_root="$temporary_directory/$expected_top_level"
test -x "$release_root/scripts/install-ubuntu.sh" || die "Release archive lacks an executable installer."
test -f "$release_root/RELEASE_ID" || die "Release archive lacks RELEASE_ID."
test -f "$release_root/SOURCE_REVISION" || die "Release archive lacks SOURCE_REVISION."
resolved_release="$(tr -d '\r\n' < "$release_root/RELEASE_ID")"
resolved_revision="$(tr -d '\r\n' < "$release_root/SOURCE_REVISION")"
test "$resolved_release" = "$release_ref" || die "Archive release identity does not match --ref."
[[ "$resolved_revision" =~ ^[0-9a-f]{40}$ ]] || die "Archive source revision is invalid."

note "Verified release: $resolved_release"
note "Resolved source revision: $resolved_revision"

installer_command=(
    "$release_root/scripts/install-ubuntu.sh"
    --domain "$domain"
    --release-id "$release_ref"
    --env-file "$environment_file"
    --source "$release_root"
)

if $dry_run; then
    printf 'lcc-bootstrap: dry-run installer command:'
    printf ' %q' "${installer_command[@]}"
    printf '\n'
    note "Dry run complete; the canonical installer was not invoked."
    exit 0
fi

note "Invoking the canonical Ubuntu installer."
"${installer_command[@]}"
note "Bootstrap completed for $release_ref ($resolved_revision)."
