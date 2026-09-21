#!/usr/bin/env bash
set -euo pipefail
umask 077

# package-release.sh replaces these exact assignments when it renders the
# stable-only, release-bound install.sh asset.
readonly embedded_stable_ref=""
readonly embedded_archive_sha256=""
readonly stable_only_launcher="0"

readonly github_repository="https://github.com/Learning-Control-Center/Learning-Control-Center.git"
readonly github_asset_base="https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download"
readonly forgejo_repository="https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center.git"
if test "${LCC_BOOTSTRAP_TESTING:-0}" = 1; then
    maximum_archive_bytes="${LCC_BOOTSTRAP_TEST_MAXIMUM_ARCHIVE_BYTES:-268435456}"
    maximum_unpacked_bytes="${LCC_BOOTSTRAP_TEST_MAXIMUM_UNPACKED_BYTES:-536870912}"
else
    maximum_archive_bytes=268435456
    maximum_unpacked_bytes=536870912
fi
readonly maximum_archive_bytes maximum_unpacked_bytes

channel="stable"
channel_was_set=0
release_ref=""
expected_commit=""
domain=""
timezone=""
environment_file=""
asset_base_url="$github_asset_base"
repository_url="$github_repository"
asset_base_was_set=0
repository_was_set=0
non_interactive=0
dry_run=0
confirm_channel_change=0
channel_change_confirmed=0
existing_installation=0
active_release=""
active_id=""
active_revision=""
active_channel=""
current_phase="argument validation"

usage() {
    cat <<'EOF'
Usage:
  bootstrap-ubuntu.sh --channel stable --ref VERSION [options]
  bootstrap-ubuntu.sh --channel main [--commit FULL_SHA] [options]

Channels:
  stable  Published release archive and SHA-256 (default, recommended)
  main    Current validated main, resolved once to an exact Git commit

Options:
  --channel stable|main     Installation channel (default: stable)
  --ref VERSION            Stable semantic release tag, for example v1.0.1
  --commit FULL_SHA        Expected current main tip; required for non-interactive main
  --domain HOST            Public DNS hostname (prompted interactively when omitted)
  --timezone ZONE          IANA application timezone (detected/prompted when omitted)
  --env-file FILE          Advanced root-owned production environment file
  --asset-base-url URL     Explicit HTTPS stable release mirror
  --repository-url URL     Explicit HTTPS main Git repository
  --confirm-channel-change Confirm an explicit non-interactive channel change
  --non-interactive        Disable prompts; require every deliberate choice
  --dry-run                Validate/acquire and invoke the canonical installer in dry-run mode

The generated release asset install.sh is stable-only and rejects channel,
release, and commit overrides. Secrets are generated into a root-only temporary
environment file unless --env-file is supplied; secret values are never argv.
EOF
}

die() {
    printf 'lcc-bootstrap: %s\n' "$*" >&2
    exit 1
}

note() {
    printf 'lcc-bootstrap: %s\n' "$*"
}

package_is_installed() {
    local package_name="$1"
    if test "$test_mode" = 1; then
        case ",${LCC_BOOTSTRAP_TEST_MISSING_PACKAGES:-}," in
            *",$package_name,"*) return 1 ;;
            *) return 0 ;;
        esac
    fi
    test "$(dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null || true)" = \
        "install ok installed"
}

record_apt_command() {
    local log_path="${LCC_BOOTSTRAP_APT_LOG:-}"
    test -n "$log_path" || return 0
    {
        printf '%q' "$1"
        shift
        if test "$#" -gt 0; then
            printf ' %q' "$@"
        fi
        printf '\n'
    } >> "$log_path"
}

run_apt() {
    record_apt_command "$@"
    if test "$test_mode" = 1; then
        case "$1" in
            update)
                test "${LCC_BOOTSTRAP_TEST_APT_UPDATE_FAILURE:-0}" != 1 || \
                    die "Ubuntu package metadata refresh failed (simulated)."
                ;;
            install)
                test "${LCC_BOOTSTRAP_TEST_APT_INSTALL_FAILURE:-0}" != 1 || \
                    die "Ubuntu prerequisite installation failed (simulated)."
                ;;
        esac
        return 0
    fi
    case "$1" in
        update)
            apt-get -o DPkg::Lock::Timeout=0 update
            ;;
        install)
            shift
            DEBIAN_FRONTEND=noninteractive apt-get \
                -o DPkg::Lock::Timeout=0 \
                -o Dpkg::Options::=--force-confold \
                --no-install-recommends --yes install "$@"
            ;;
        *) die "Internal package operation is invalid: $1" ;;
    esac
}

verify_apt_state() {
    current_phase="OS package preflight"
    if test "$test_mode" = 1; then
        test "${LCC_BOOTSTRAP_TEST_DPKG_AUDIT_FAILURE:-0}" != 1 || \
            die "dpkg reports unfinished or broken package operations."
        test "${LCC_BOOTSTRAP_TEST_APT_CHECK_FAILURE:-0}" != 1 || \
            die "APT dependency state is broken; repair it explicitly before installing LCC."
        test "${LCC_BOOTSTRAP_TEST_UBUNTU_KEYRING_FAILURE:-0}" != 1 || \
            die "The package-owned Ubuntu archive keyring is missing or invalid."
        return
    fi
    if test ! -r /usr/share/keyrings/ubuntu-archive-keyring.gpg || \
        ! dpkg-query -S /usr/share/keyrings/ubuntu-archive-keyring.gpg 2>/dev/null | \
            grep -Eq '^ubuntu-keyring(:[^:]+)?: /usr/share/keyrings/ubuntu-archive-keyring.gpg$'; then
        die "The package-owned Ubuntu archive keyring is missing or invalid."
    fi
    test -z "$(dpkg --audit 2>&1)" || \
        die "dpkg reports unfinished or broken package operations; resolve them before installing LCC."
    apt-get -o DPkg::Lock::Timeout=0 check >/dev/null || \
        die "APT dependency state is broken or locked; resolve it explicitly before installing LCC."
}

verify_host_shape() {
    local architecture available_kib
    current_phase="OS preflight"
    architecture="${LCC_BOOTSTRAP_TEST_ARCHITECTURE:-$(uname -m)}"
    case "$architecture" in
        x86_64|aarch64) ;;
        *) die "Supported architectures are amd64 and arm64; found $architecture." ;;
    esac
    available_kib="${LCC_BOOTSTRAP_TEST_AVAILABLE_KIB:-$(df -Pk / | awk 'NR == 2 {print $4}')}"
    [[ "$available_kib" =~ ^[0-9]+$ ]] || die "Unable to determine available disk space."
    test "$available_kib" -ge 1048576 || \
        die "At least 1 GiB of free disk space is required before installation."
}

verify_ubuntu_package_indexes() {
    local allow_missing="${1:-0}"
    local targets identifier origin label codename site signed_by
    local seen_packages=0
    if test "$test_mode" = 1; then
        targets="${LCC_BOOTSTRAP_TEST_APT_INDEX_TARGETS:-Packages|Ubuntu|Ubuntu|noble|http://archive.ubuntu.com/ubuntu|/usr/share/keyrings/ubuntu-archive-keyring.gpg}"
    else
        # apt-get expands these indextarget placeholders, not the shell.
        # shellcheck disable=SC2016
        targets="$(apt-get indextargets \
            --format '$(IDENTIFIER)|$(ORIGIN)|$(LABEL)|$(CODENAME)|$(SITE)|$(SIGNED_BY)')"
    fi
    while IFS='|' read -r identifier origin label codename site signed_by; do
        test "$identifier" = Packages || continue
        seen_packages=1
        if test "$origin" != Ubuntu || test "$label" != Ubuntu; then
            die "Non-Ubuntu APT package index is enabled (${site:-unknown site}); disable third-party package sources before LCC provisions prerequisites."
        fi
        case "$codename" in
            noble|noble-updates|noble-security|noble-backports) ;;
            *)
                die "APT package index ${site:-unknown site} targets unsupported suite ${codename:-unknown}; LCC prerequisite provisioning requires Ubuntu 24.04 (Noble) indexes."
                ;;
        esac
        test "$signed_by" = /usr/share/keyrings/ubuntu-archive-keyring.gpg || \
            die "APT package index ${site:-unknown site} is not bound to Ubuntu's package-owned archive keyring."
    done <<< "$targets"
    if test "$seen_packages" -eq 0 && test "$allow_missing" -ne 1; then
        die "No Ubuntu 24.04 package index is available; enable the standard signed Ubuntu repositories, including universe."
    fi
}

verify_source_url_shape() {
    local selected_url authority
    selected_url="$asset_base_url"
    if test "$channel" = main; then
        selected_url="$repository_url"
    fi
    [[ "$selected_url" =~ ^https://[^/?#]+(/[^?#]*)?$ ]] || \
        die "Selected source must be a public HTTPS URL without credentials, query, or fragment."
    authority="${selected_url#https://}"
    authority="${authority%%/*}"
    [[ "$authority" != *@* ]] || \
        die "Selected source must be a public HTTPS URL without credentials, query, or fragment."
}

verify_existing_lcc_shape() {
    local current_path
    current_path="${install_root%/}/opt/learning-control-center/current"
    if test -e "$current_path" && test ! -L "$current_path"; then
        die "Existing LCC current path is not a symbolic link; inspect it before retrying."
    fi
    test -L "$current_path" || return 0
    active_release="$(readlink -f "$current_path")"
    test -n "$active_release" && test -d "$active_release" && \
        test -f "$active_release/RELEASE_ID" && test -f "$active_release/SOURCE_REVISION" || \
        die "Existing LCC installation has incomplete release metadata."
    existing_installation=1
    active_id="$(tr -d '\r\n' < "$active_release/RELEASE_ID")"
    active_revision="$(tr -d '\r\n' < "$active_release/SOURCE_REVISION")"
    if [[ ! "$active_revision" =~ ^[0-9a-f]{40}$ ]]; then
        if test -f "$active_release/RELEASE_CHANNEL" || \
            [[ ! "$active_revision" =~ ^artifact-sha256-[0-9a-f]{64}$ ]]; then
            die "Existing LCC installation has an invalid source revision."
        fi
        note "Existing v1.0.0 installation uses its legacy artifact-content revision."
    fi
    active_channel="stable"
    if test -f "$active_release/RELEASE_CHANNEL"; then
        active_channel="$(tr -d '\r\n' < "$active_release/RELEASE_CHANNEL")"
    fi
    case "$active_channel" in stable|main) ;; *) die "Existing LCC channel metadata is invalid." ;; esac
    note "Existing LCC installation detected: channel=$active_channel release=$active_id"
    if test "$channel" = stable && test "$active_channel" = stable; then
        comparison="$(compare_stable_release_ids "$active_id" "$release_ref")" || \
            die "Existing or requested stable release identity is invalid."
        if test "$comparison" -gt 0; then
            die "Installed stable release $active_id is newer than requested $release_ref; use the rollback workflow for downgrades."
        fi
    fi
}

compare_stable_release_ids() {
    python3 - "$1" "$2" <<'PY'
import re
import sys

pattern = re.compile(
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)


def parse(value):
    match = pattern.fullmatch(value)
    if match is None:
        raise SystemExit(1)
    core = tuple(int(part) for part in match.groups()[:3])
    prerelease = match.group(4)
    if prerelease is None:
        return core, None
    identifiers = []
    for item in prerelease.split("."):
        identifiers.append((0, int(item)) if item.isdigit() else (1, item))
    return core, tuple(identifiers)


def compare(left, right):
    left_core, left_pre = parse(left)
    right_core, right_pre = parse(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        if left_item[0] != right_item[0]:
            return -1 if left_item[0] < right_item[0] else 1
        return -1 if left_item[1] < right_item[1] else 1
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


print(compare(sys.argv[1], sys.argv[2]))
PY
}

repository_for_asset_base() {
    python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse

value = sys.argv[1].rstrip("/")
suffix = "/releases/download"
parsed = urlparse(value)
if parsed.scheme != "https" or not parsed.netloc or not parsed.path.endswith(suffix):
    raise SystemExit("stable asset base must end with /releases/download")
print(f"https://{parsed.netloc}{parsed.path.removesuffix(suffix)}.git")
PY
}

provision_prerequisites() {
    local package_name candidate
    local -a required_packages missing_packages
    required_packages=(
        ca-certificates curl python3 python3-venv sqlite3 rsync tar gzip git caddy iproute2
    )
    missing_packages=()
    for package_name in "${required_packages[@]}"; do
        if ! package_is_installed "$package_name"; then
            missing_packages+=("$package_name")
        fi
    done

    note "Production frontend: verified release artifact (Node.js/npm are not installed on the server)."
    if test "${#missing_packages[@]}" -eq 0; then
        note "Ubuntu prerequisites are already satisfied."
        return
    fi
    note "Missing Ubuntu packages: ${missing_packages[*]}"
    note "Package source: Ubuntu 24.04 signed repositories only; enabled third-party package indexes are refused."
    if test "$dry_run" -eq 1; then
        verify_ubuntu_package_indexes 1
        note "DRY-RUN: real installation will refresh and re-verify Ubuntu-only package indexes."
        note "DRY-RUN: would run apt-get update and install: ${missing_packages[*]}"
        return
    fi

    current_phase="package metadata"
    run_apt update
    verify_ubuntu_package_indexes
    if test "$test_mode" != 1; then
        for package_name in "${missing_packages[@]}"; do
            candidate="$(apt-cache policy "$package_name" | sed -n 's/^[[:space:]]*Candidate:[[:space:]]*//p')"
            test -n "$candidate" && test "$candidate" != "(none)" || \
                die "Ubuntu package $package_name has no installable candidate. Ensure the standard Ubuntu 24.04 repositories, including universe, are enabled."
        done
    fi
    current_phase="prerequisite install"
    run_apt install "${missing_packages[@]}"
}

verify_provisioned_commands() {
    local command_name resolved_caddy
    test "$test_mode" = 1 && return 0
    for command_name in curl python3 sha256sum tar gzip mktemp stat sed grep head tr wc; do
        command -v "$command_name" >/dev/null || \
            die "Acquisition command is unavailable: $command_name"
    done
    command -v git >/dev/null || die "Provisioned Git command is unavailable."
    test "$dry_run" -eq 1 && return 0
    for command_name in sqlite3 rsync systemctl runuser flock ss; do
        command -v "$command_name" >/dev/null || \
            die "Provisioned prerequisite command is unavailable: $command_name"
    done
    resolved_caddy="$(command -v caddy 2>/dev/null || true)"
    test "$resolved_caddy" = /usr/bin/caddy || \
        die "Caddy must resolve to the Ubuntu package-owned /usr/bin/caddy; found ${resolved_caddy:-none}."
    dpkg-query -S /usr/bin/caddy 2>/dev/null | grep -Eq '^caddy(:[^:]+)?: /usr/bin/caddy$' || \
        die "/usr/bin/caddy must be owned by the Ubuntu caddy package."
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || \
        die "Ubuntu Python 3.12 or newer is required."
    /usr/bin/caddy version 2>/dev/null | grep -Eq '^v?2\.' || die "Caddy 2 is required."
    test -f /lib/systemd/system/caddy.service || test -f /usr/lib/systemd/system/caddy.service || \
        die "The Ubuntu Caddy package did not install its systemd service."
}

verify_existing_caddy() {
    local resolved_caddy=""
    current_phase="Caddy preflight"
    if test "${LCC_BOOTSTRAP_TEST_UNMANAGED_CADDY:-0}" = 1; then
        die "An unmanaged Caddy executable conflicts with Ubuntu package ownership."
    fi
    if test "$test_mode" = 1; then
        resolved_caddy="${LCC_BOOTSTRAP_TEST_CADDY_PATH:-/usr/bin/caddy}"
        if test "$resolved_caddy" != /usr/bin/caddy; then
            die "Caddy must resolve to the Ubuntu package-owned /usr/bin/caddy; found $resolved_caddy."
        fi
    else
        resolved_caddy="$(command -v caddy 2>/dev/null || true)"
        if test -n "$resolved_caddy" && test "$resolved_caddy" != /usr/bin/caddy; then
            die "Caddy must resolve to the Ubuntu package-owned /usr/bin/caddy; found $resolved_caddy."
        fi
        if test -n "$resolved_caddy" && ! package_is_installed caddy; then
            die "An unmanaged Caddy executable is present. Remove it or install the Ubuntu caddy package explicitly before retrying."
        fi
        if package_is_installed caddy; then
            if test ! -x /usr/bin/caddy || \
                ! dpkg-query -S /usr/bin/caddy 2>/dev/null | \
                    grep -Eq '^caddy(:[^:]+)?: /usr/bin/caddy$'; then
                die "/usr/bin/caddy must be owned by the Ubuntu caddy package."
            fi
        fi
    fi
    if test "${LCC_BOOTSTRAP_TEST_CADDY_CONFIG_FAILURE:-0}" = 1; then
        die "The existing Caddy configuration is invalid; correct it before installing LCC."
    fi
    if test "$test_mode" != 1 && package_is_installed caddy && test -f /etc/caddy/Caddyfile; then
        /usr/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || \
            die "The existing Caddy configuration is invalid; correct it before installing LCC."
    fi
}

verify_caddy_and_ports() {
    current_phase="Caddy and port preflight"
    if test "${LCC_BOOTSTRAP_TEST_PORT_CONFLICT:-0}" = 1; then
        die "Ports 80 or 443 are already owned by a service other than Caddy."
    fi
    if test "$test_mode" != 1 && command -v ss >/dev/null; then
        listeners="$(ss -H -ltnp '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
        if test -n "$listeners"; then
            if printf '%s\n' "$listeners" | grep -q 'users:'; then
                if printf '%s\n' "$listeners" | grep -Fv '(("caddy",' | grep -q .; then
                    die "Ports 80 or 443 are already owned by a service other than Caddy."
                fi
            elif ! systemctl is-active --quiet caddy.service; then
                die "Ports 80 or 443 are already in use while Caddy is inactive; resolve the conflict before installing LCC."
            fi
        fi
    fi
}

while test "$#" -gt 0; do
    case "$1" in
        --channel)
            test "$stable_only_launcher" = 0 || die "This release-bound install.sh is stable-only."
            channel="${2:?Missing --channel value}"; channel_was_set=1; shift 2 ;;
        --ref)
            test "$stable_only_launcher" = 0 || die "This release-bound install.sh does not accept --ref."
            release_ref="${2:?Missing --ref value}"; shift 2 ;;
        --commit)
            test "$stable_only_launcher" = 0 || die "This release-bound install.sh does not accept --commit."
            expected_commit="${2:?Missing --commit value}"; shift 2 ;;
        --domain) domain="${2:?Missing --domain value}"; shift 2 ;;
        --timezone) timezone="${2:?Missing --timezone value}"; shift 2 ;;
        --env-file) environment_file="${2:?Missing --env-file value}"; shift 2 ;;
        --asset-base-url) asset_base_url="${2:?Missing --asset-base-url value}"; asset_base_was_set=1; shift 2 ;;
        --repository-url)
            test "$stable_only_launcher" = 0 || die "This release-bound install.sh does not accept --repository-url."
            repository_url="${2:?Missing --repository-url value}"; repository_was_set=1; shift 2 ;;
        --confirm-channel-change) confirm_channel_change=1; shift ;;
        --non-interactive) non_interactive=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done

if test "$stable_only_launcher" = 1; then
    test -n "$embedded_stable_ref" && test -n "$embedded_archive_sha256" || \
        die "The generated stable installer lacks its embedded release binding."
    channel="stable"
    release_ref="$embedded_stable_ref"
elif test "$channel_was_set" -eq 0; then
    channel="stable"
fi

case "$channel" in
    stable)
        test -z "$expected_commit" || die "--commit is valid only with --channel main."
        test "$repository_was_set" -eq 0 || die "--repository-url is valid only with --channel main."
        test -n "$release_ref" || die "Stable bootstrap requires --ref; no latest release is selected implicitly."
        [[ "$release_ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] || \
            die "Stable --ref must be an explicit semantic release tag such as v1.0.1."
        ;;
    main)
        test "$asset_base_was_set" -eq 0 || die "--asset-base-url is valid only with --channel stable."
        test -z "$release_ref" || die "--ref is valid only with --channel stable."
        if test -n "$expected_commit"; then
            [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] || \
                die "--commit must be one lowercase 40-character Git commit SHA."
        fi
        if test "$non_interactive" -eq 1 && test -z "$expected_commit"; then
            die "Non-interactive main installation requires --commit with the expected current main tip."
        fi
        ;;
    *) die "--channel must be stable or main." ;;
esac

if test -n "$domain"; then
    domain="${domain,,}"
fi

test_mode="${LCC_BOOTSTRAP_TESTING:-0}"
if test "$test_mode" = 1 && test "$asset_base_was_set" -eq 0 && \
    test -n "${LCC_BOOTSTRAP_ASSET_BASE_URL:-}"; then
    asset_base_url="$LCC_BOOTSTRAP_ASSET_BASE_URL"
fi
if test "$test_mode" = 1 && test "$repository_was_set" -eq 0 && \
    test -n "${LCC_BOOTSTRAP_REPOSITORY_URL:-}"; then
    repository_url="$LCC_BOOTSTRAP_REPOSITORY_URL"
fi
asset_base_url="${asset_base_url%/}"
repository_url="${repository_url%/}"
if test "$test_mode" != 1; then
    verify_source_url_shape
fi
if test "$dry_run" -eq 0 && test "$test_mode" != 1 && test "${EUID:-$(id -u)}" -ne 0; then
    die "Run the bootstrap as root, normally through sudo."
fi

install_root="${LCC_BOOTSTRAP_INSTALL_ROOT:-/}"
if test "$install_root" != "/"; then
    test "$test_mode" = 1 || die "Alternate install roots are available only to tests."
    install_root="$(readlink -m -- "$install_root")"
    test "$install_root" != "/" && [[ "$install_root" = /tmp/* ]] || \
        die "Test install root must resolve beneath /tmp."
fi

acquisition_directory=""
secret_directory=""
cleanup() {
    status=$?
    if test -n "$acquisition_directory"; then
        rm -rf -- "$acquisition_directory"
    fi
    if test -n "$secret_directory"; then
        rm -rf -- "$secret_directory"
    fi
    if test "$status" -ne 0; then
        printf 'lcc-bootstrap: failed during phase: %s\n' "$current_phase" >&2
        printf 'lcc-bootstrap: installed Ubuntu packages are retained; correct the error and rerun safely.\n' >&2
    fi
    exit "$status"
}
trap cleanup EXIT

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

for command_name in apt-get apt-cache dpkg dpkg-query uname df awk sed grep; do
    command -v "$command_name" >/dev/null || die "Ubuntu base command is unavailable: $command_name"
done
verify_host_shape
verify_apt_state
verify_existing_lcc_shape
verify_existing_caddy
provision_prerequisites
verify_provisioned_commands
verify_caddy_and_ports

if test "$test_mode" != 1; then
    python3 - "$asset_base_url" "$repository_url" "$channel" <<'PY'
import sys
from urllib.parse import urlparse

asset_base, repository, channel = sys.argv[1:]
value = asset_base if channel == "stable" else repository
parsed = urlparse(value)
if (
    value != value.strip()
    or any(ord(character) < 32 or ord(character) == 127 for character in value)
    or parsed.scheme != "https"
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
):
    raise SystemExit("Selected source must be a public HTTPS URL without credentials, query, or fragment")
PY
fi

acquisition_directory="$(mktemp -d "${TMPDIR:-/tmp}/lcc-bootstrap.XXXXXXXX")"

source_root=""
release_id=""
source_revision=""
source_ref=""
source_origin=""
source_repository=""

if test "$channel" = stable; then
    current_phase="stable release acquisition"
    asset_name="learning-control-center-${release_ref}.tar.gz"
    checksum_name="${asset_name}.sha256"
    release_base_url="${asset_base_url%/}/$release_ref"
    archive_url="$release_base_url/$asset_name"
    checksum_url="$release_base_url/$checksum_name"
    archive_path="$acquisition_directory/$asset_name"
    checksum_path="$acquisition_directory/$checksum_name"
    curl_options=(--fail --location --silent --show-error --retry 3 --max-filesize "$maximum_archive_bytes")
    if test "$test_mode" = 1; then
        curl_options+=(--proto '=https,file')
    else
        curl_options+=(--proto '=https' --tlsv1.2)
    fi

    note "Requested stable release: $release_ref"
    note "Downloading bounded release archive from $archive_url"
    curl "${curl_options[@]}" --output "$checksum_path" "$checksum_url"
    curl "${curl_options[@]}" --output "$archive_path" "$archive_url"
    test "$(stat -c %s "$archive_path")" -le "$maximum_archive_bytes" || \
        die "Release archive exceeds the maximum allowed size."
    checksum_line="$(sed -n '1p' "$checksum_path")"
    test "$(wc -l < "$checksum_path")" -eq 1 || die "Checksum file must contain exactly one entry."
    checksum_hash="${checksum_line%%[[:space:]]*}"
    checksum_file="${checksum_line##*[[:space:]]}"
    checksum_hash="${checksum_hash,,}"
    [[ "$checksum_hash" =~ ^[0-9a-f]{64}$ ]] || die "Checksum file contains an invalid SHA-256 value."
    test "$checksum_file" = "$asset_name" || die "Checksum file names an unexpected release asset."
    if test -n "$embedded_archive_sha256"; then
        test "$checksum_hash" = "$embedded_archive_sha256" || \
            die "Published checksum does not match this install.sh release binding."
    fi
    printf '%s  %s\n' "$checksum_hash" "$asset_name" > "$acquisition_directory/expected.sha256"
    (cd "$acquisition_directory" && sha256sum --check --status expected.sha256) || \
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
    tar --extract --gzip --file "$archive_path" --directory "$acquisition_directory" \
        --no-same-owner --no-same-permissions
    source_root="$acquisition_directory/$expected_top_level"
    test -x "$source_root/scripts/install-ubuntu.sh" || die "Release archive lacks an executable installer."
    test -x "$source_root/scripts/generate-production-env.sh" || die "Release archive lacks the environment generator."
    test -f "$source_root/RELEASE_ID" || die "Release archive lacks RELEASE_ID."
    test -f "$source_root/SOURCE_REVISION" || die "Release archive lacks SOURCE_REVISION."
    test -f "$source_root/RELEASE_MANIFEST" || die "Release archive lacks RELEASE_MANIFEST."
    release_id="$(tr -d '\r\n' < "$source_root/RELEASE_ID")"
    source_revision="$(tr -d '\r\n' < "$source_root/SOURCE_REVISION")"
    test "$release_id" = "$release_ref" || die "Archive release identity does not match --ref."
    [[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || die "Archive source revision is invalid."
    if test -f "$source_root/RELEASE_CHANNEL"; then
        test "$(tr -d '\r\n' < "$source_root/RELEASE_CHANNEL")" = stable || \
            die "Release archive channel is not stable."
    fi
    manifest="$source_root/RELEASE_MANIFEST"
    if ! { grep -Fqx 'metadata_version=1' "$manifest" && \
        grep -Fqx 'channel=stable' "$manifest" && \
        grep -Fqx "release_id=$release_id" "$manifest" && \
        grep -Fqx "source_repository=$github_repository" "$manifest" && \
        grep -Fqx "source_ref=refs/tags/$release_ref" "$manifest" && \
        grep -Fqx "source_revision=$source_revision" "$manifest" && \
        grep -Fqx "source_origin=$github_asset_base" "$manifest"; }; then
        die "Release archive manifest does not match its stable identity."
    fi
    source_ref="refs/tags/$release_ref"
    source_origin="$asset_base_url"
    if test "$test_mode" = 1 && [[ "$asset_base_url" = file://* ]]; then
        source_repository="$github_repository"
    else
        source_repository="$(repository_for_asset_base "$asset_base_url")" || \
            die "Unable to derive the stable source repository from --asset-base-url."
        case "$source_repository" in
            "$github_repository"|"$forgejo_repository") ;;
            *) die "Stable mirrors are supported only from the explicit GitHub or Forgejo source." ;;
        esac
    fi
    note "Verified stable release: $release_id"
    note "Exact source revision: $source_revision"
else
    current_phase="main source acquisition"
    if test "$test_mode" != 1; then
        case "$repository_url" in
            "$github_repository"|"$forgejo_repository") ;;
            *) die "Main acquisition is supported only from the explicit GitHub or Forgejo repository." ;;
        esac
    fi
    source_repository="$repository_url"
    source_origin="$repository_url"
    source_ref="refs/heads/main"
    repository_directory="$acquisition_directory/repository"
    git init --quiet "$repository_directory"
    if test "$test_mode" = 1; then
        git -C "$repository_directory" -c protocol.file.allow=always fetch --quiet \
            --depth=1 --no-tags "$repository_url" "$source_ref"
    else
        git -C "$repository_directory" fetch --quiet --depth=1 --no-tags \
            "$repository_url" "$source_ref"
    fi
    source_revision="$(git -C "$repository_directory" rev-parse --verify 'FETCH_HEAD^{commit}')"
    [[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || die "Resolved main revision is not a full Git SHA."
    if test -n "$expected_commit" && test "$source_revision" != "$expected_commit"; then
        die "Remote main resolved to $source_revision, not expected commit $expected_commit."
    fi
    release_id="main-$source_revision"
    note "Current main channel selected — latest validated code, not release-pinned."
    note "Repository: $repository_url"
    note "Resolved main revision: $source_revision"
fi

# Do not source or execute anything from main until the operator has seen and
# accepted the exact immutable revision.
if test "$channel" = main && test "$non_interactive" -eq 0; then
    exec 3<>/dev/tty || die "Interactive main installation requires a controlling terminal."
    printf '\nCurrent main — latest validated code, not release-pinned\n' >&3
    printf '  Repository: %s\n  Exact source SHA: %s\n' "$repository_url" "$source_revision" >&3
    printf 'This installation will remain pinned to this SHA until an explicit update.\n' >&3
    if test "$existing_installation" -eq 1 && test "$active_channel" != main; then
        printf '  Channel change: %s -> main\n' "$active_channel" >&3
    fi
    printf 'Continue? [y/N]: ' >&3
    IFS= read -r confirmation <&3 || die "Unable to read confirmation from the terminal."
    case "${confirmation,,}" in y|yes) ;; *) die "Main installation was not confirmed." ;; esac
    if test "$existing_installation" -eq 1 && test "$active_channel" != main; then
        channel_change_confirmed=1
    fi
fi

if test "$channel" = main; then
    git -C "$repository_directory" checkout --quiet --detach "$source_revision"
    test "$(git -C "$repository_directory" rev-parse HEAD)" = "$source_revision" || \
        die "Detached main checkout does not match the resolved revision."
    test -z "$(git -C "$repository_directory" status --porcelain --untracked-files=all)" || \
        die "Resolved main checkout is unexpectedly dirty."
    source_root="$repository_directory"
fi

current_release="${install_root%/}/opt/learning-control-center/current"
environment_target="${install_root%/}/etc/learning-control-center.env"

if test "$existing_installation" -eq 1; then
    if test "$active_id" = "$release_id" && test "$active_revision" = "$source_revision" && \
        test "$active_channel" = "$channel"; then
        echo "Learning Control Center is already up to date."
        exit 0
    fi
    if test "$active_channel" != "$channel" && test "$channel_change_confirmed" -ne 1 && \
        test "$confirm_channel_change" -ne 1; then
        if test "$non_interactive" -eq 1; then
            die "Changing from $active_channel to $channel requires --confirm-channel-change."
        fi
        exec 3<>/dev/tty || die "Channel-change confirmation requires a controlling terminal."
        printf '\nChannel change: %s -> %s\n' "$active_channel" "$channel" >&3
        printf '  Current: %s (%s)\n  Target: %s (%s)\n' \
            "$active_id" "$active_revision" "$release_id" "$source_revision" >&3
        printf 'Continue? [y/N]: ' >&3
        IFS= read -r confirmation <&3 || die "Unable to read channel-change confirmation."
        case "${confirmation,,}" in y|yes) channel_change_confirmed=1 ;; \
            *) die "Channel change was not confirmed." ;; esac
    fi
    updater_command=(
        "$source_root/scripts/update-ubuntu.sh" apply
        --source "$source_root"
        --channel "$channel"
        --release-id "$release_id"
        --source-revision "$source_revision"
        --source-repository "$source_repository"
        --source-ref "$source_ref"
        --source-origin "$source_origin"
    )
    if test "$active_channel" != "$channel"; then
        updater_command+=(--confirm-channel-change)
    fi
    if test "$dry_run" -eq 1; then
        printf 'DRY-RUN: would delegate to canonical update engine:'
        printf ' %q' "${updater_command[@]}"
        printf '\n'
        exit 0
    fi
    note "Delegating immutable target $release_id to the canonical update engine."
    if test "$test_mode" = 1 && test -n "${LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG:-}"; then
        printf '%s\n' "${updater_command[@]}" > "$LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG"
        exit 0
    fi
    "${updater_command[@]}"
    exit 0
fi

# Load validation helpers only after the selected source has been verified.
# shellcheck disable=SC1090
source "$source_root/scripts/deploy-common.sh"
lcc_verify_frontend_artifact "$source_root"

if test -z "$environment_file" && test -f "$environment_target"; then
    environment_file="$environment_target"
    note "Reusing the existing production environment for this matching install."
fi

if test -n "$environment_file"; then
    [[ "$environment_file" = /* ]] || die "--env-file must be an absolute path."
    test -e "$environment_file" || die "Environment file does not exist: $environment_file"
    allow_installed=0
    expected_owner=0
    if test "$test_mode" = 1; then
        expected_owner="$(id -u)"
    fi
    if test "$(readlink -m -- "$environment_file")" = "$(readlink -m -- "$environment_target")"; then
        allow_installed=1
    fi
    lcc_validate_environment_file_security "$environment_file" "$allow_installed" "$expected_owner"
    lcc_load_environment "$environment_file"
    lcc_validate_environment "$current_release"
    configured_domain="$(lcc_public_hostname)"
    if test -n "$domain" && test "$domain" != "$configured_domain"; then
        die "--domain does not match the supplied production environment."
    fi
    domain="$configured_domain"
    if test -n "$timezone" && test "$timezone" != "$LCC_APP_TIMEZONE"; then
        die "--timezone does not match the supplied production environment."
    fi
    timezone="$LCC_APP_TIMEZONE"
else
    if test "$non_interactive" -eq 0; then
        exec 3<>/dev/tty || die "Interactive installation requires a controlling terminal."
        if test -z "$domain"; then
            printf 'Public hostname (for example lcc.example.com): ' >&3
            IFS= read -r domain <&3 || die "Unable to read hostname from the terminal."
            domain="${domain,,}"
        fi
    elif test -z "$domain"; then
        die "Non-interactive generated configuration requires --domain."
    fi
    lcc_validate_public_hostname "$domain"
    if test "$test_mode" != 1 && ! getent ahosts "$domain" >/dev/null 2>&1; then
        note "WARNING: $domain does not currently resolve in DNS; Caddy cannot issue public HTTPS until DNS is correct."
    fi

    if test -z "$timezone"; then
        timezone="${LCC_BOOTSTRAP_DEFAULT_TIMEZONE:-}"
        if test -z "$timezone" && test -r /etc/timezone; then
            timezone="$(tr -d '\r\n' < /etc/timezone)"
        fi
        timezone="${timezone:-UTC}"
    fi
    if test "$non_interactive" -eq 0; then
        printf 'Application timezone [%s]: ' "$timezone" >&3
        IFS= read -r selected_timezone <&3 || die "Unable to read timezone from the terminal."
        timezone="${selected_timezone:-$timezone}"
    fi

    secret_parent="/run"
    if test "$test_mode" = 1; then
        secret_parent="${LCC_BOOTSTRAP_SECRET_TMPDIR:-${TMPDIR:-/tmp}}"
    fi
    secret_directory="$(mktemp -d "$secret_parent/lcc-install-config.XXXXXXXX")"
    chmod 0700 "$secret_directory"
    environment_file="$secret_directory/learning-control-center.env"
    generator_command=(
        "$source_root/scripts/generate-production-env.sh"
        --domain "$domain"
        --timezone "$timezone"
        --output "$environment_file"
    )
    if test "$install_root" != "/"; then
        generator_command+=(--root "$install_root")
    fi
    "${generator_command[@]}"
fi

if test "$non_interactive" -eq 0; then
    if ! { true >&3; } 2>/dev/null; then
        exec 3<>/dev/tty || die "Interactive installation requires a controlling terminal."
    fi
    printf '\nLearning Control Center installation summary\n' >&3
    printf '  Channel: %s\n  Release: %s\n  Source SHA: %s\n  Public URL: https://%s\n  Timezone: %s\n' \
        "$channel" "$release_id" "$source_revision" "$domain" "$timezone" >&3
    if test "$channel" = stable; then
        printf 'Continue with this stable installation? [y/N]: ' >&3
        IFS= read -r confirmation <&3 || die "Unable to read confirmation from the terminal."
        case "${confirmation,,}" in y|yes) ;; *) die "Stable installation was cancelled." ;; esac
    fi
fi

installer_command=(
    "$source_root/scripts/install-ubuntu.sh"
    --domain "$domain"
    --channel "$channel"
    --release-id "$release_id"
    --source-revision "$source_revision"
    --source-repository "$source_repository"
    --source-ref "$source_ref"
    --source-origin "$source_origin"
    --env-file "$environment_file"
    --source "$source_root"
)
if test "$dry_run" -eq 1; then
    installer_command+=(--dry-run)
fi
if test "$install_root" != "/"; then
    installer_command+=(--root "$install_root" --skip-build --skip-prerequisites)
fi

note "Invoking the canonical Ubuntu installer for $release_id."
current_phase="host installation"
"${installer_command[@]}"
note "Installation handoff completed for $release_id ($source_revision)."

if test "$dry_run" -eq 0 && test "$test_mode" != 1 && test "$non_interactive" -eq 0; then
    /usr/local/sbin/lcc-admin show-bootstrap-token
    printf '\nCreate the first account at https://%s, then run:\n  sudo lcc-admin finalize-bootstrap\n' \
        "$domain" >&3
elif test "$dry_run" -eq 0 && test "$test_mode" != 1; then
    note "Retrieve the one-time token from an interactive terminal with: sudo lcc-admin show-bootstrap-token"
    note "After creating the first account, run: sudo lcc-admin finalize-bootstrap"
fi
