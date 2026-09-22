#!/usr/bin/env bash
# Installer V2 platform boundary. Source this file from the future shared
# installer, or execute it directly to inspect/provision Ubuntu prerequisites.
set -euo pipefail

readonly -a LCC_UBUNTU_CORE_PACKAGES=(
    ca-certificates curl python3 python3-venv sqlite3 rsync tar gzip
    iproute2 util-linux
)

lcc_ubuntu_die() { printf 'lcc-ubuntu-platform: %s\n' "$*" >&2; return 1; }
lcc_ubuntu_note() { printf 'lcc-ubuntu-platform: %s\n' "$*"; }

lcc_ubuntu_check_platform() {
    local os_release="${1:-/etc/os-release}" systemd_marker="${2:-/run/systemd/system}"
    local platform_id="" version="" architecture system_state key value
    test -r "$os_release" || { lcc_ubuntu_die "OS release metadata is unavailable."; return 1; }
    while IFS='=' read -r key value; do
        value="${value#\"}"
        value="${value%\"}"
        case "$key" in
            ID) platform_id="$value" ;;
            VERSION_ID) version="$value" ;;
        esac
    done < "$os_release"
    test "$platform_id" = ubuntu && test "$version" = 24.04 || {
        lcc_ubuntu_die "Only Ubuntu Server 24.04 is supported by this adapter (found ${platform_id:-unknown} ${version:-unknown})."
        return 1
    }
    for key in dpkg dpkg-query apt-get; do
        command -v "$key" >/dev/null || {
            lcc_ubuntu_die "Required package-manager command is unavailable: $key"
            return 1
        }
    done
    architecture="$(dpkg --print-architecture)" || return 1
    case "$architecture" in
        amd64|arm64) ;;
        *) lcc_ubuntu_die "Unsupported Ubuntu architecture: $architecture"; return 1 ;;
    esac
    command -v systemctl >/dev/null && test -d "$systemd_marker" || {
        lcc_ubuntu_die "A running systemd system manager is required."
        return 1
    }
    system_state="$(systemctl --system show --property=SystemState --value)" || {
        lcc_ubuntu_die "The systemd system manager is unavailable."
        return 1
    }
    test -n "$system_state" && test "$system_state" != offline || {
        lcc_ubuntu_die "The systemd system manager is unavailable."
        return 1
    }
}

lcc_ubuntu_core_packages() { printf '%s\n' "${LCC_UBUNTU_CORE_PACKAGES[@]}"; }
lcc_ubuntu_managed_caddy_packages() { printf '%s\n' caddy; }

lcc_ubuntu_provision_managed_caddy() {
    local os_release="${1:-/etc/os-release}" systemd_marker="${2:-/run/systemd/system}"
    lcc_ubuntu_check_platform "$os_release" "$systemd_marker" || return 1
    test "$(id -u)" = 0 || { lcc_ubuntu_die "Root is required to provision packages."; return 1; }
    if lcc_ubuntu_package_installed caddy; then
        lcc_ubuntu_note "The optional Caddy package is already installed."
        return 0
    fi
    lcc_ubuntu_note "Refreshing the host's configured APT metadata for optional Caddy."
    if ! apt-get -o APT::Update::Error-Mode=any update; then
        lcc_ubuntu_note "APT metadata refresh did not fully succeed; attempting required package installation using currently available APT metadata."
    fi
    DEBIAN_FRONTEND=noninteractive apt-get -y --no-install-recommends --no-remove install caddy || {
        lcc_ubuntu_die "Managed Caddy package installation failed; inspect the APT/dpkg error above."
        return 1
    }
    lcc_ubuntu_package_installed caddy || {
        lcc_ubuntu_die "Managed Caddy package is still unavailable after installation."
        return 1
    }
}

lcc_ubuntu_package_installed() {
    local package_status
    package_status="$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null || true)"
    # The first dpkg status word is the desired selection. A failed unrelated
    # removal can leave that word as "deinstall" while the package is installed.
    [[ "$package_status" == *' ok installed' ]]
}

lcc_ubuntu_missing_core_packages() {
    local package
    for package in "${LCC_UBUNTU_CORE_PACKAGES[@]}"; do
        lcc_ubuntu_package_installed "$package" || printf '%s\n' "$package"
    done
}

lcc_ubuntu_verify_core() {
    local package command_name
    for package in "${LCC_UBUNTU_CORE_PACKAGES[@]}"; do
        lcc_ubuntu_package_installed "$package" || {
            lcc_ubuntu_die "Required package is not installed: $package"
            return 1
        }
    done
    for command_name in curl python3 sqlite3 rsync tar gzip ss runuser flock systemctl; do
        command -v "$command_name" >/dev/null || {
            lcc_ubuntu_die "Required command is unavailable: $command_name"
            return 1
        }
    done
    python3 -c 'import ensurepip, sys, venv; raise SystemExit(sys.version_info < (3, 12))' || {
        lcc_ubuntu_die "Python 3.12+ with venv/ensurepip support is required."
        return 1
    }
    test -s /etc/ssl/certs/ca-certificates.crt || {
        lcc_ubuntu_die "The system CA certificate bundle is unavailable."
        return 1
    }
}

lcc_ubuntu_provision_core() {
    local os_release="${1:-/etc/os-release}" systemd_marker="${2:-/run/systemd/system}"
    local -a missing=()
    lcc_ubuntu_check_platform "$os_release" "$systemd_marker" || return 1
    test "$(id -u)" = 0 || { lcc_ubuntu_die "Root is required to provision packages."; return 1; }
    mapfile -t missing < <(lcc_ubuntu_missing_core_packages)
    if test "${#missing[@]}" -eq 0; then
        lcc_ubuntu_note "Core Ubuntu packages are already installed."
        lcc_ubuntu_verify_core
        return
    fi

    lcc_ubuntu_note "Missing core packages: ${missing[*]}"
    lcc_ubuntu_note "Refreshing the host's configured APT metadata."
    if ! apt-get -o APT::Update::Error-Mode=any update; then
        lcc_ubuntu_note "APT metadata refresh did not fully succeed; attempting required package installation using currently available APT metadata."
    fi
    DEBIAN_FRONTEND=noninteractive apt-get -y --no-install-recommends --no-remove \
        install "${missing[@]}" || {
        lcc_ubuntu_die "Required package installation failed; inspect the APT/dpkg error above."
        return 1
    }
    lcc_ubuntu_verify_core || return 1
    lcc_ubuntu_note "Core Ubuntu capabilities verified."
}

if test "${BASH_SOURCE[0]}" = "$0"; then
    case "${1:-}" in
        check-platform) shift; lcc_ubuntu_check_platform "$@" ;;
        core-packages) lcc_ubuntu_core_packages ;;
        managed-caddy-packages) lcc_ubuntu_managed_caddy_packages ;;
        missing-core) lcc_ubuntu_missing_core_packages ;;
        verify-core) lcc_ubuntu_verify_core ;;
        provision-core) shift; lcc_ubuntu_provision_core "$@" ;;
        provision-managed-caddy) shift; lcc_ubuntu_provision_managed_caddy "$@" ;;
        *) printf 'Usage: %s {check-platform|core-packages|managed-caddy-packages|missing-core|verify-core|provision-core|provision-managed-caddy}\n' "$0" >&2; exit 2 ;;
    esac
fi
