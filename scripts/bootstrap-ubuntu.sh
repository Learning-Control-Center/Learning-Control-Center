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

usage() {
    cat <<'EOF'
Usage:
  bootstrap-ubuntu.sh --channel stable --ref VERSION [options]
  bootstrap-ubuntu.sh --channel main [--commit FULL_SHA] [options]

Channels:
  stable  Published release archive and SHA-256 (default, recommended)
  main    Current public main tip, resolved once to an exact Git commit

Options:
  --channel stable|main     Installation channel (default: stable)
  --ref VERSION            Stable semantic release tag, for example v1.0.1
  --commit FULL_SHA        Expected current main tip; required for non-interactive main
  --domain HOST            Public DNS hostname (prompted interactively when omitted)
  --timezone ZONE          IANA application timezone (detected/prompted when omitted)
  --env-file FILE          Advanced root-owned production environment file
  --asset-base-url URL     Explicit HTTPS stable release mirror
  --repository-url URL     Explicit HTTPS main Git repository
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
if test "$dry_run" -eq 0 && test "$test_mode" != 1 && test "${EUID:-$(id -u)}" -ne 0; then
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

for command_name in curl python3 sha256sum tar mktemp stat sed grep head tr wc; do
    command -v "$command_name" >/dev/null || die "Required acquisition command is unavailable: $command_name"
done
if test "$channel" = main; then
    command -v git >/dev/null || die "Git is required for --channel main."
fi

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
secret_directory=""
cleanup() {
    status=$?
    rm -rf -- "$acquisition_directory"
    if test -n "$secret_directory"; then
        rm -rf -- "$secret_directory"
    fi
    exit "$status"
}
trap cleanup EXIT

source_root=""
release_id=""
source_revision=""
source_ref=""
source_origin=""
source_repository=""

if test "$channel" = stable; then
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
    source_repository="$github_repository"
    note "Verified stable release: $release_id"
    note "Exact source revision: $source_revision"
else
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
    note "DEVELOPMENT / UNSTABLE channel selected."
    note "Repository: $repository_url"
    note "Resolved main revision: $source_revision"
fi

# Do not source or execute anything from an unstable main checkout until the
# operator has seen and accepted the exact immutable revision.
if test "$channel" = main && test "$non_interactive" -eq 0; then
    exec 3<>/dev/tty || die "Interactive main installation requires a controlling terminal."
    printf '\nDEVELOPMENT / UNSTABLE installation\n' >&3
    printf '  Repository: %s\n  Exact source SHA: %s\n' "$repository_url" "$source_revision" >&3
    printf 'This build is not a published stable release and will remain pinned to this SHA.\n' >&3
    printf 'Type INSTALL MAIN to continue: ' >&3
    IFS= read -r confirmation <&3 || die "Unable to read confirmation from the terminal."
    test "$confirmation" = "INSTALL MAIN" || die "Main installation was not confirmed."
fi

if test "$channel" = main; then
    git -C "$repository_directory" checkout --quiet --detach "$source_revision"
    test "$(git -C "$repository_directory" rev-parse HEAD)" = "$source_revision" || \
        die "Detached main checkout does not match the resolved revision."
    test -z "$(git -C "$repository_directory" status --porcelain --untracked-files=all)" || \
        die "Resolved main checkout is unexpectedly dirty."
    source_root="$repository_directory"
fi

install_root="${LCC_BOOTSTRAP_INSTALL_ROOT:-/}"
if test "$install_root" != "/"; then
    test "$test_mode" = 1 || die "Alternate install roots are available only to tests."
    install_root="$(readlink -m -- "$install_root")"
    test "$install_root" != "/" && [[ "$install_root" = /tmp/* ]] || \
        die "Test install root must resolve beneath /tmp."
fi
current_release="${install_root%/}/opt/learning-control-center/current"
environment_target="${install_root%/}/etc/learning-control-center.env"

if test -L "$current_release"; then
    active_release="$(readlink -f "$current_release")"
    active_id="$(tr -d '\r\n' < "$active_release/RELEASE_ID")"
    active_revision="$(tr -d '\r\n' < "$active_release/SOURCE_REVISION")"
    active_channel="stable"
    if test -f "$active_release/RELEASE_CHANNEL"; then
        active_channel="$(tr -d '\r\n' < "$active_release/RELEASE_CHANNEL")"
    fi
    if test "$active_id" != "$release_id" || test "$active_revision" != "$source_revision" || \
        test "$active_channel" != "$channel"; then
        die "Another release is active; use the controlled update workflow instead of install.sh."
    fi
    note "The exact selected release is already active; performing an idempotent repair check."
fi

# Load validation helpers only after the selected source has been verified.
# shellcheck disable=SC1090
source "$source_root/scripts/deploy-common.sh"

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
