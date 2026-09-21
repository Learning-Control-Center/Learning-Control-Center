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
readonly github_main_ref_api="https://api.github.com/repos/Learning-Control-Center/Learning-Control-Center/git/ref/heads/main"
readonly forgejo_main_ref_api="https://forgejo.waqsea.com/api/v1/repos/Learning-Control-Center/Learning-Control-Center/git/refs/heads/main"
bootstrap_from_stdin=0
if test -z "${BASH_SOURCE[0]:-}"; then
    bootstrap_from_stdin=1
fi
readonly bootstrap_from_stdin
original_arguments=("$@")
if test "${LCC_BOOTSTRAP_TESTING:-0}" = 1; then
    maximum_archive_bytes="${LCC_BOOTSTRAP_TEST_MAXIMUM_ARCHIVE_BYTES:-268435456}"
    maximum_unpacked_bytes="${LCC_BOOTSTRAP_TEST_MAXIMUM_UNPACKED_BYTES:-536870912}"
else
    maximum_archive_bytes=268435456
    maximum_unpacked_bytes=536870912
fi
readonly maximum_archive_bytes maximum_unpacked_bytes

channel="main"
release_ref=""
expected_commit=""
domain=""
timezone=""
environment_file=""
app_port=""
app_port_was_set=0
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
apt_view_directory=""
apt_architecture=""
apt_options=()

usage() {
    cat <<'EOF'
Usage:
  bootstrap-ubuntu.sh [--channel main] [--commit FULL_SHA] [options]
  bootstrap-ubuntu.sh --channel stable --ref VERSION [options]

Channels:
  main    Current validated main, resolved once to an exact Git commit (default)
  stable  Explicit pinned release archive and SHA-256

Options:
  --channel stable|main     Installation channel (default: main)
  --ref VERSION            Stable semantic release tag, for example v1.0.1
  --commit FULL_SHA        Expected main tip; required for direct non-interactive main
  --domain HOST            Public DNS hostname (prompted interactively when omitted)
  --timezone ZONE          IANA application timezone (detected/prompted when omitted)
  --env-file FILE          Advanced root-owned production environment file
  --app-port PORT          Internal loopback application port (default: 8000)
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

run_pinned_bootstrap_stage() {
    local ref_api raw_url stage_directory ref_payload pinned_bootstrap stage_revision stage_status
    case "$repository_url" in
        "$github_repository")
            ref_api="$github_main_ref_api"
            ;;
        "$forgejo_repository")
            ref_api="$forgejo_main_ref_api"
            ;;
        *) die "Main acquisition is supported only from the explicit GitHub or Forgejo repository." ;;
    esac
    for command_name in curl sed head mktemp stat chmod bash cp; do
        command -v "$command_name" >/dev/null || \
            die "Bootstrap stage command is unavailable: $command_name"
    done
    stage_directory="$(mktemp -d "${TMPDIR:-/tmp}/lcc-bootstrap-stage.XXXXXXXX")"
    chmod 0700 "$stage_directory"
    trap 'rm -rf -- "$stage_directory"' EXIT
    ref_payload="$stage_directory/main-ref.json"
    pinned_bootstrap="$stage_directory/bootstrap-ubuntu.sh"

    if test "${LCC_BOOTSTRAP_TESTING:-0}" = 1 && \
        test -n "${LCC_BOOTSTRAP_STAGE_TEST_RESOLVED_SHA:-}" && \
        test -n "${LCC_BOOTSTRAP_STAGE_TEST_SCRIPT:-}"; then
        stage_revision="$LCC_BOOTSTRAP_STAGE_TEST_RESOLVED_SHA"
        cp -- "$LCC_BOOTSTRAP_STAGE_TEST_SCRIPT" "$pinned_bootstrap"
    else
        curl --fail --location --silent --show-error --retry 3 --max-filesize 1048576 \
            --proto '=https' --proto-redir '=https' --tlsv1.2 \
            --header 'Accept: application/json' \
            --header 'User-Agent: Learning-Control-Center-Bootstrap/1.0.1' \
            --output "$ref_payload" "$ref_api"
        stage_revision="$(
            sed -nE 's/.*"sha"[[:space:]]*:[[:space:]]*"([0-9a-f]{40})".*/\1/p' \
                "$ref_payload" | head -n1
        )"
        [[ "$stage_revision" =~ ^[0-9a-f]{40}$ ]] || \
            die "Unable to resolve public main to one full Git commit SHA."
        case "$repository_url" in
            "$github_repository")
                raw_url="https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/$stage_revision/scripts/bootstrap-ubuntu.sh"
                ;;
            "$forgejo_repository")
                raw_url="https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center/raw/commit/$stage_revision/scripts/bootstrap-ubuntu.sh"
                ;;
        esac
        curl --fail --location --silent --show-error --retry 3 --max-filesize 1048576 \
            --proto '=https' --proto-redir '=https' --tlsv1.2 \
            --output "$pinned_bootstrap" "$raw_url"
    fi

    [[ "$stage_revision" =~ ^[0-9a-f]{40}$ ]] || \
        die "Resolved bootstrap revision is not a full Git commit SHA."
    test -f "$pinned_bootstrap" && test "$(stat -c %s "$pinned_bootstrap")" -le 1048576 || \
        die "Pinned bootstrap is missing or exceeds the maximum allowed size."
    test "$(sed -n '1p' "$pinned_bootstrap")" = '#!/usr/bin/env bash' || \
        die "Pinned bootstrap has an invalid script header."
    chmod 0700 "$pinned_bootstrap"
    note "Stage zero resolved main revision: $stage_revision"
    note "Continuing with the bootstrap fetched from that immutable commit."
    set +e
    LCC_BOOTSTRAP_PINNED_REVISION="$stage_revision" \
    LCC_BOOTSTRAP_PINNED_REPOSITORY="$repository_url" \
        bash "$pinned_bootstrap" "${original_arguments[@]}"
    stage_status=$?
    set -e
    rm -rf -- "$stage_directory"
    trap - EXIT
    exit "$stage_status"
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
    if test "$test_mode" = 1 && test "${LCC_BOOTSTRAP_TEST_EXECUTE_APT:-0}" != 1; then
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
            APT_CONFIG="$apt_view_directory/apt.conf" apt-get "${apt_options[@]}" update
            ;;
        check)
            APT_CONFIG="$apt_view_directory/apt.conf" apt-get "${apt_options[@]}" check
            ;;
        install)
            shift
            APT_CONFIG="$apt_view_directory/apt.conf" DEBIAN_FRONTEND=noninteractive apt-get \
                "${apt_options[@]}" \
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
}

verify_host_shape() {
    local architecture available_kib
    current_phase="OS preflight"
    architecture="${LCC_BOOTSTRAP_TEST_ARCHITECTURE:-$(uname -m)}"
    case "$architecture" in
        x86_64) apt_architecture=amd64 ;;
        aarch64) apt_architecture=arm64 ;;
        *) die "Supported architectures are amd64 and arm64; found $architecture." ;;
    esac
    if test "$test_mode" != 1; then
        test "$(dpkg --print-architecture)" = "$apt_architecture" || \
            die "The dpkg architecture does not match the supported host architecture."
    fi
    available_kib="${LCC_BOOTSTRAP_TEST_AVAILABLE_KIB:-$(df -Pk / | awk 'NR == 2 {print $4}')}"
    [[ "$available_kib" =~ ^[0-9]+$ ]] || die "Unable to determine available disk space."
    test "$available_kib" -ge 1048576 || \
        die "At least 1 GiB of free disk space is required before installation."
}

prepare_ubuntu_apt_view() {
    local source_root=/etc/apt
    local use_test_default=0
    apt_view_directory="$(mktemp -d /tmp/lcc-ubuntu-apt.XXXXXXXX)"
    if test "$test_mode" = 1; then
        source_root="${LCC_BOOTSTRAP_TEST_APT_SOURCE_ROOT:-$apt_view_directory/default-test-sources}"
        test -n "${LCC_BOOTSTRAP_TEST_APT_SOURCE_ROOT:-}" || use_test_default=1
    fi
    chmod 0755 "$apt_view_directory"
    mkdir -m 0755 "$apt_view_directory/sources.list.d" "$apt_view_directory/empty.d" \
        "$apt_view_directory/lists" "$apt_view_directory/archives"
    mkdir -m 0700 "$apt_view_directory/lists/partial" \
        "$apt_view_directory/archives/partial"
    touch "$apt_view_directory/empty.list" "$apt_view_directory/empty.conf" \
        "$apt_view_directory/empty.pref" "$apt_view_directory/empty.gpg" \
        "$apt_view_directory/empty.auth"
    printf 'Dir::Etc::parts "%s";\nDir::Etc::main "%s";\n' \
        "$apt_view_directory/empty.d" "$apt_view_directory/empty.conf" > \
        "$apt_view_directory/apt.conf"
    chmod 0644 "$apt_view_directory/empty.list" "$apt_view_directory/empty.conf" \
        "$apt_view_directory/empty.pref" "$apt_view_directory/empty.gpg" \
        "$apt_view_directory/empty.auth" "$apt_view_directory/apt.conf"
    if test "$test_mode" != 1; then
        chown _apt:root "$apt_view_directory/lists/partial" \
            "$apt_view_directory/archives/partial" || \
            die "Unable to prepare the isolated Ubuntu APT download directories."
    fi

    python3 - "$source_root" "$apt_view_directory/sources.list.d/ubuntu.sources" \
        "$apt_architecture" "$use_test_default" <<'PY'
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

source_root = Path(sys.argv[1])
output = Path(sys.argv[2])
architecture = sys.argv[3]
use_test_default = sys.argv[4] == "1"
keyring = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
suites = {"noble", "noble-updates", "noble-security", "noble-backports"}
components = {"main", "universe", "restricted", "multiverse"}
selected = set()


def fail(message):
    raise SystemExit(f"lcc-bootstrap: {message}")


def official_uri(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    allowed_host = host in {
        "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com"
    } or re.fullmatch(r"[a-z]{2}\.archive\.ubuntu\.com", host)
    if not allowed_host:
        return None
    path = "/ubuntu-ports" if host == "ports.ubuntu.com" else "/ubuntu"
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != path
    ):
        fail(f"Invalid official Ubuntu APT source URI: {value}")
    return f"{parsed.scheme}://{host}{path}"


def add_source(uri, source_suites, source_components, signed_by, source):
    normalized = official_uri(uri)
    if normalized is None:
        return
    if signed_by != keyring:
        fail(f"Official Ubuntu APT source {source} must use the package-owned archive keyring.")
    if not source_suites or any(suite not in suites for suite in source_suites):
        fail(f"Official Ubuntu APT source {source} must target Ubuntu 24.04 (Noble).")
    if not source_components or any(part not in components for part in source_components):
        fail(f"Official Ubuntu APT source {source} has unsupported components.")
    for suite in source_suites:
        selected.add((normalized, suite, tuple(source_components)))


def parse_one_line(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if re.match(r"deb\s", line) is None:
            continue
        match = re.fullmatch(r"deb\s+(?:\[([^]]+)\]\s+)?(\S+)\s+(\S+)\s+(.+)", line)
        if match is None:
            continue
        options = {}
        for item in (match.group(1) or "").split():
            if "=" in item:
                key, value = item.split("=", 1)
                options[key.lower()] = value
        if options.get("arch") and architecture not in options["arch"].split(","):
            continue
        add_source(
            match.group(2), [match.group(3)], match.group(4).split(),
            options.get("signed-by", ""), str(path)
        )


def parse_deb822(path):
    paragraphs = re.split(r"\n\s*\n", path.read_text(encoding="utf-8"))
    for paragraph in paragraphs:
        fields = {}
        previous = None
        for line in paragraph.splitlines():
            if not line or line.lstrip().startswith("#"):
                continue
            if line[0].isspace() and previous is not None:
                fields[previous] += " " + line.strip()
            elif ":" in line:
                previous, value = line.split(":", 1)
                previous = previous.lower()
                fields[previous] = value.strip()
        if fields.get("enabled", "yes").lower() == "no":
            continue
        if "deb" not in fields.get("types", "").split():
            continue
        if fields.get("architectures") and architecture not in fields["architectures"].split():
            continue
        for uri in fields.get("uris", "").split():
            add_source(
                uri, fields.get("suites", "").split(),
                fields.get("components", "").split(),
                fields.get("signed-by", ""), str(path)
            )


if use_test_default:
    selected.add(("http://archive.ubuntu.com/ubuntu", "noble", ("main", "universe")))
else:
    paths = [source_root / "sources.list"]
    paths.extend(sorted((source_root / "sources.list.d").glob("*.list")))
    paths.extend(sorted((source_root / "sources.list.d").glob("*.sources")))
    for path in paths:
        if not path.is_file():
            continue
        try:
            if path.suffix == ".sources":
                parse_deb822(path)
            else:
                parse_one_line(path)
        except (OSError, UnicodeError, ValueError) as error:
            fail(f"Cannot validate APT source {path}: {error}")

if not selected:
    fail("No trusted Ubuntu 24.04 APT source with the package-owned archive keyring is enabled.")
output.write_text(
    "".join(
        "Types: deb\n"
        f"URIs: {uri}\n"
        f"Suites: {suite}\n"
        f"Components: {' '.join(parts)}\n"
        f"Signed-By: {keyring}\n\n"
        for uri, suite, parts in sorted(selected)
    ),
    encoding="utf-8",
)
PY
    chmod 0644 "$apt_view_directory/sources.list.d/ubuntu.sources"

    if test "$test_mode" = 1 && test -n "${LCC_BOOTSTRAP_TEST_APT_SOURCE_CAPTURE:-}"; then
        cp -- "$apt_view_directory/sources.list.d/ubuntu.sources" \
            "$LCC_BOOTSTRAP_TEST_APT_SOURCE_CAPTURE"
    fi
    apt_options=(
        -o "Dir::Etc::sourcelist=$apt_view_directory/empty.list"
        -o "Dir::Etc::sourceparts=$apt_view_directory/sources.list.d"
        -o "Dir::Etc::main=$apt_view_directory/empty.conf"
        -o "Dir::Etc::parts=$apt_view_directory/empty.d"
        -o "Dir::Etc::preferences=$apt_view_directory/empty.pref"
        -o "Dir::Etc::preferencesparts=$apt_view_directory/empty.d"
        -o "Dir::Etc::trusted=$apt_view_directory/empty.gpg"
        -o "Dir::Etc::trustedparts=$apt_view_directory/empty.d"
        -o "Dir::Etc::netrc=$apt_view_directory/empty.auth"
        -o "Dir::Etc::netrcparts=$apt_view_directory/empty.d"
        -o "Dir::State::lists=$apt_view_directory/lists"
        -o "Dir::State::status=/var/lib/dpkg/status"
        -o "Dir::Cache::archives=$apt_view_directory/archives"
        -o "Dir::Cache::pkgcache=$apt_view_directory/pkgcache.bin"
        -o "Dir::Cache::srcpkgcache=$apt_view_directory/srcpkgcache.bin"
        -o "APT::Architecture=$apt_architecture"
        -o "APT::Update::Error-Mode=any"
        -o "APT::Get::AllowUnauthenticated=false"
        -o "Acquire::AllowInsecureRepositories=false"
        -o "Acquire::AllowDowngradeToInsecureRepositories=false"
        -o "DPkg::Lock::Timeout=0"
    )
}

verify_ubuntu_package_indexes() {
    local targets identifier origin label codename site signed_by
    local seen_packages=0
    if test "$test_mode" = 1 && test "${LCC_BOOTSTRAP_TEST_EXECUTE_APT:-0}" != 1; then
        targets="${LCC_BOOTSTRAP_TEST_APT_INDEX_TARGETS:-Packages|Ubuntu|Ubuntu|noble|http://archive.ubuntu.com/ubuntu|/usr/share/keyrings/ubuntu-archive-keyring.gpg}"
    else
        # apt-get expands these indextarget placeholders, not the shell.
        # shellcheck disable=SC2016
        targets="$(APT_CONFIG="$apt_view_directory/apt.conf" apt-get "${apt_options[@]}" indextargets \
            --format '$(IDENTIFIER)|$(ORIGIN)|$(LABEL)|$(CODENAME)|$(SITE)|$(SIGNED_BY)')"
    fi
    while IFS='|' read -r identifier origin label codename site signed_by; do
        test "$identifier" = Packages || continue
        seen_packages=1
        if test "$origin" != Ubuntu || test "$label" != Ubuntu; then
            die "The isolated Ubuntu APT view returned an unexpected package index (${site:-unknown site})."
        fi
        case "$codename" in
            noble|noble-updates|noble-security|noble-backports) ;;
            *)
                die "APT package index ${site:-unknown site} targets unsupported suite ${codename:-unknown}; LCC prerequisite provisioning requires Ubuntu 24.04 (Noble) indexes."
                ;;
        esac
        test "$signed_by" = /usr/share/keyrings/ubuntu-archive-keyring.gpg || \
            die "APT package index ${site:-unknown site} is not bound to Ubuntu's package-owned archive keyring."
        grep -Fqx "URIs: $site" "$apt_view_directory/sources.list.d/ubuntu.sources" || \
            die "The isolated Ubuntu APT view returned an unselected package source (${site:-unknown site})."
    done <<< "$targets"
    if test "$seen_packages" -eq 0; then
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
    note "Package source: isolated Ubuntu 24.04 signed repositories only; third-party sources remain untouched."
    current_phase="package metadata"
    prepare_ubuntu_apt_view
    if test "$dry_run" -eq 1; then
        note "DRY-RUN: real installation will refresh and verify isolated Ubuntu-only package indexes."
        note "DRY-RUN: would run apt-get update and install: ${missing_packages[*]}"
        return
    fi

    run_apt update || die "Trusted Ubuntu package metadata refresh failed; inspect the Ubuntu source and signature error."
    verify_ubuntu_package_indexes
    if test "$test_mode" != 1 || test "${LCC_BOOTSTRAP_TEST_EXECUTE_APT:-0}" = 1; then
        run_apt check >/dev/null || \
            die "APT dependency state is broken or locked; resolve it explicitly before installing LCC."
        for package_name in "${missing_packages[@]}"; do
            candidate="$(APT_CONFIG="$apt_view_directory/apt.conf" apt-cache "${apt_options[@]}" policy \
                "$package_name" | sed -n 's/^[[:space:]]*Candidate:[[:space:]]*//p')" || \
                die "Ubuntu package candidate inspection failed for $package_name."
            test -n "$candidate" && test "$candidate" != "(none)" || \
                die "Ubuntu package $package_name has no installable candidate. Ensure the standard Ubuntu 24.04 repositories, including universe, are enabled."
        done
    fi
    current_phase="prerequisite install"
    run_apt install "${missing_packages[@]}" || \
        die "Trusted Ubuntu prerequisite installation failed; inspect the APT/dpkg error."
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
        die "Caddy must resolve to the package-owned /usr/bin/caddy; found ${resolved_caddy:-none}."
    dpkg-query -S /usr/bin/caddy 2>/dev/null | grep -Eq '^caddy(:[^:]+)?: /usr/bin/caddy$' || \
        die "/usr/bin/caddy must be owned by the Ubuntu caddy package."
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || \
        die "Ubuntu Python 3.12 or newer is required."
    /usr/bin/caddy version 2>/dev/null | grep -Eq '^v?2\.' || die "Caddy 2 is required."
    test -f /lib/systemd/system/caddy.service || test -f /usr/lib/systemd/system/caddy.service || \
        die "The package-managed Caddy systemd service is required."
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
            die "Caddy must resolve to the package-owned /usr/bin/caddy; found $resolved_caddy."
        fi
    else
        resolved_caddy="$(command -v caddy 2>/dev/null || true)"
        if test -n "$resolved_caddy" && test "$resolved_caddy" != /usr/bin/caddy; then
            die "Caddy must resolve to the package-owned /usr/bin/caddy; found $resolved_caddy."
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
            channel="${2:?Missing --channel value}"; shift 2 ;;
        --ref)
            test "$stable_only_launcher" = 0 || die "This release-bound install.sh does not accept --ref."
            release_ref="${2:?Missing --ref value}"; shift 2 ;;
        --commit)
            test "$stable_only_launcher" = 0 || die "This release-bound install.sh does not accept --commit."
            expected_commit="${2:?Missing --commit value}"; shift 2 ;;
        --domain) domain="${2:?Missing --domain value}"; shift 2 ;;
        --timezone) timezone="${2:?Missing --timezone value}"; shift 2 ;;
        --env-file) environment_file="${2:?Missing --env-file value}"; shift 2 ;;
        --app-port) app_port="${2:?Missing --app-port value}"; app_port_was_set=1; shift 2 ;;
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

if test "$app_port_was_set" -eq 1 && test -n "$environment_file"; then
    die "--app-port cannot be combined with --env-file; the environment file is the configuration authority."
fi

if test "$stable_only_launcher" = 1; then
    test -n "$embedded_stable_ref" && test -n "$embedded_archive_sha256" || \
        die "The generated stable installer lacks its embedded release binding."
    channel="stable"
    release_ref="$embedded_stable_ref"
fi

pinned_bootstrap_revision="${LCC_BOOTSTRAP_PINNED_REVISION:-}"
pinned_bootstrap_repository="${LCC_BOOTSTRAP_PINNED_REPOSITORY:-}"
if test -n "$pinned_bootstrap_revision" || test -n "$pinned_bootstrap_repository"; then
    [[ "$pinned_bootstrap_revision" =~ ^[0-9a-f]{40}$ ]] || \
        die "Pinned bootstrap revision is not a full Git commit SHA."
    test "$channel" = main || die "A pinned main bootstrap cannot install the stable channel."
    test "$repository_url" = "$pinned_bootstrap_repository" || \
        die "Pinned bootstrap repository does not match the selected source."
    if test -n "$expected_commit" && test "$expected_commit" != "$pinned_bootstrap_revision"; then
        die "--commit does not match the pinned bootstrap revision."
    fi
    expected_commit="$pinned_bootstrap_revision"
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
        if test "$non_interactive" -eq 1 && test -z "$expected_commit" && \
            test "$bootstrap_from_stdin" -ne 1; then
            die "Direct non-interactive main installation requires --commit; the canonical piped bootstrap pins main during stage zero."
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
if test "$bootstrap_from_stdin" = 1 && test "$channel" = main && \
    test -z "$pinned_bootstrap_revision"; then
    run_pinned_bootstrap_stage
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
    if test -n "$apt_view_directory"; then
        rm -rf -- "$apt_view_directory"
    fi
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

for command_name in apt-get apt-cache dpkg dpkg-query python3 uname df awk sed grep; do
    command -v "$command_name" >/dev/null || die "Ubuntu base command is unavailable: $command_name"
done
verify_host_shape
verify_apt_state
verify_existing_lcc_shape
if test "$existing_installation" -eq 1 && test "$app_port_was_set" -eq 1; then
    die "--app-port cannot change an existing installation; use: sudo lcc-admin app-port set $app_port"
fi
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
    note "Current main — latest validated code, resolved to an exact commit at install/update time."
    note "Repository: $repository_url"
    note "Resolved main revision: $source_revision"
fi

# Resolve identical reruns before asking for confirmation or checking out the
# already-installed source again.
if test "$existing_installation" -eq 1 && test "$active_id" = "$release_id" && \
    test "$active_revision" = "$source_revision" && test "$active_channel" = "$channel"; then
    echo "Learning Control Center is already up to date."
    exit 0
fi

# Do not source or execute anything from main until the operator has seen and
# accepted the exact immutable revision.
if test "$channel" = main && test "$non_interactive" -eq 0; then
    exec 3<>/dev/tty || die "Interactive main installation requires a controlling terminal."
    printf '\nCurrent main — latest validated code, resolved to an exact commit at install/update time\n' >&3
    printf '  Repository: %s\n' "$repository_url" >&3
    if test "$existing_installation" -eq 1; then
        printf '  Current channel: %s\n  Current release: %s\n' "$active_channel" "$active_id" >&3
        if [[ "$active_revision" =~ ^[0-9a-f]{40}$ ]]; then
            printf '  Current source SHA: %s\n' "$active_revision" >&3
        fi
    fi
    printf '  Target source SHA: %s\n' "$source_revision" >&3
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

bootstrap_app_port_is_available() {
    local candidate="$1"
    if test "$test_mode" = 1; then
        case ",${LCC_BOOTSTRAP_TEST_OCCUPIED_APP_PORTS:-}," in
            *",$candidate,"*) return 1 ;;
            *) return 0 ;;
        esac
    fi
    lcc_app_port_is_available "$candidate"
}

bootstrap_describe_app_port_listener() {
    local candidate="$1"
    if test "$test_mode" = 1; then
        printf 'Listener: 127.0.0.1:%s (process=test-listener pid=4242)\n' "$candidate" >&2
        return
    fi
    lcc_describe_app_port_listener "$candidate"
}

bootstrap_find_available_app_port() {
    local candidate
    if test "$test_mode" != 1; then
        lcc_find_available_app_port 8001
        return
    fi
    for ((candidate = 8001; candidate <= 65535; candidate++)); do
        if bootstrap_app_port_is_available "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    return 1
}

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
    selected_app_port="$(lcc_effective_app_port)"
    if ! bootstrap_app_port_is_available "$selected_app_port"; then
        bootstrap_describe_app_port_listener "$selected_app_port"
        die "Internal application port $selected_app_port from the production environment is already occupied."
    fi
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
    selected_app_port="${app_port:-$LCC_APP_PORT_DEFAULT}"
    selected_app_port="$(lcc_validate_app_port "$selected_app_port")"
    if ! bootstrap_app_port_is_available "$selected_app_port"; then
        bootstrap_describe_app_port_listener "$selected_app_port"
        if test "$app_port_was_set" -eq 1; then
            die "Explicitly requested internal application port $selected_app_port is already occupied."
        fi
        if test "$non_interactive" -eq 1; then
            die "Default internal application port 8000 is occupied; rerun with --app-port PORT."
        fi
        if ! { true >&3; } 2>/dev/null; then
            exec 3<>/dev/tty || die "Interactive app-port selection requires a controlling terminal."
        fi
        suggested_app_port="$(bootstrap_find_available_app_port)" || \
            die "No available internal application port was found in range 8001-65535."
        while true; do
            printf 'Internal application port [%s]: ' "$suggested_app_port" >&3
            IFS= read -r selected_input <&3 || \
                die "Unable to read the internal application port from the terminal."
            selected_input="${selected_input:-$suggested_app_port}"
            if ! selected_app_port="$(lcc_validate_app_port "$selected_input")"; then
                continue
            fi
            if bootstrap_app_port_is_available "$selected_app_port"; then
                break
            fi
            bootstrap_describe_app_port_listener "$selected_app_port"
            printf 'Internal application port %s is occupied; choose another port.\n' \
                "$selected_app_port" >&3
        done
    fi
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
        --app-port "$selected_app_port"
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
    printf '  Channel: %s\n  Release: %s\n  Source SHA: %s\n  Public URL: https://%s\n  Internal endpoint: 127.0.0.1:%s\n  Timezone: %s\n' \
        "$channel" "$release_id" "$source_revision" "$domain" "$selected_app_port" "$timezone" >&3
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
