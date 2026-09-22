#!/usr/bin/env bash
# Installer V2 gateway layer. Source after Core installation and environment validation.
# shellcheck source=scripts/deploy-common.sh

lcc_gateway_listener_conflict() {
    local port listener
    for port in 80 443; do
        listener="$(ss -H -ltnp "sport = :$port")" ||
            lcc_die "Unable to inspect public port $port listeners safely."
        test -z "$listener" && continue
        if ! lcc_app_port_owned_by_service "$port" caddy.service; then
            lcc_describe_app_port_listener "$port"
            return 0
        fi
    done
    return 1
}

lcc_verify_managed_caddy() {
    test -x "$LCC_CADDY_BINARY" || lcc_die "Package-owned Caddy is required at $LCC_CADDY_BINARY."
    dpkg-query -S "$LCC_CADDY_BINARY" 2>/dev/null |
        grep -Eq '^caddy(:[^:]+)?: /usr/bin/caddy$' ||
        lcc_die "Refusing an unmanaged Caddy executable."
    "$LCC_CADDY_BINARY" version | grep -Eq '^v?2\.' || lcc_die "Caddy 2 is required."
    test -f /lib/systemd/system/caddy.service || test -f /usr/lib/systemd/system/caddy.service ||
        lcc_die "The packaged Caddy systemd service is required."
}

lcc_report_external_gateway() {
    local port hostname
    port="$(lcc_effective_app_port)"
    hostname="$(lcc_public_hostname)"
    cat <<EOF
LCC Core is internally healthy. Public gateway/TLS: pending operator integration.
Gateway mode: external (same-host reverse proxy only).
Upstream: http://127.0.0.1:$port
Public origin: $LCC_PUBLIC_ORIGIN
Configure HTTPS for $hostname; preserve Host: $hostname.
Forward the client IP and HTTPS scheme through X-Forwarded-For and X-Forwarded-Proto.
Proxy /api/* to the loopback upstream without stripping /api.
Serve /opt/learning-control-center/current/frontend/dist as the static root,
with unknown frontend paths falling back to /index.html (SPA routing).
The external proxy must not receive /etc/learning-control-center.env.
Example: $LCC_CURRENT_RELEASE/deploy/examples/installer-v2-external-nginx.conf
After operator configuration, run: lcc-admin health --public
EOF
}

lcc_install_managed_caddy() (
    set -euo pipefail
    local release_root="$1" mode_state="$2"
    local site_dir caddy_dir backup_dir="" rendered="" original_active=0 active_after_provision=0
    local transaction_active=0
    local main_existed=0 temporary_site="" temporary_main=""
    site_dir="$(dirname -- "$LCC_CADDY_SITE")"
    caddy_dir="$(dirname -- "$LCC_CADDY_MAIN")"
    test "$caddy_dir" = "$(dirname -- "$site_dir")" &&
        test ! -L "$caddy_dir" && test ! -L "$LCC_CADDY_MAIN" && test ! -L "$LCC_CADDY_SITE" &&
        test ! -L "$site_dir" || lcc_die "Refusing symlinked Caddy configuration paths."
    if test -e "$LCC_CADDY_SITE"; then
        if test "$mode_state" != caddy || ! test -f "$LCC_CADDY_SITE" ||
            ! grep -Fqx '# Managed by Learning Control Center Installer V2' "$LCC_CADDY_SITE"; then
            lcc_die "Caddy site path is occupied by an unmanaged or V1 configuration."
        fi
    fi
    if lcc_gateway_listener_conflict; then
        lcc_die "Ports 80/443 belong to another service. Choose --gateway external explicitly; Core remains healthy."
    fi
    if systemctl is-active --quiet caddy.service; then original_active=1; fi
    if ! lcc_ubuntu_provision_managed_caddy; then
        if test "$original_active" -eq 0 && systemctl is-active --quiet caddy.service; then
            systemctl stop caddy.service || true
        fi
        lcc_die "Managed Caddy package provisioning failed; Core remains healthy."
    fi
    if ! (lcc_verify_managed_caddy); then
        if test "$original_active" -eq 0 && systemctl is-active --quiet caddy.service; then
            systemctl stop caddy.service || true
        fi
        lcc_die "Managed Caddy runtime verification failed; Core remains healthy."
    fi
    if lcc_gateway_listener_conflict; then
        if test "$original_active" -eq 0 && systemctl is-active --quiet caddy.service; then
            systemctl stop caddy.service || true
        fi
        lcc_die "A non-Caddy listener appeared on ports 80/443 during provisioning; Core remains healthy."
    fi
    test ! -e "$LCC_CADDY_MAIN" || test -f "$LCC_CADDY_MAIN" ||
        lcc_die "Caddy main configuration is not a regular file."
    test ! -e "$site_dir" || test -d "$site_dir" ||
        lcc_die "Caddy site directory is not a directory."
    if systemctl is-active --quiet caddy.service; then active_after_provision=1; fi
    backup_dir="$(mktemp -d "${LCC_TRANSACTION_DIRECTORY_PARENT:-/run}/lcc-gateway.XXXXXXXX")"
    chmod 0700 "$backup_dir"
    trap 'rm -rf -- "$backup_dir"' EXIT
    rendered="$backup_dir/site.rendered"
    lcc_render_caddy_site "$release_root" "$LCC_CURRENT_RELEASE/frontend/dist" "$rendered"
    sed -i '1i# Managed by Learning Control Center Installer V2' "$rendered"
    "$LCC_CADDY_BINARY" fmt --overwrite "$rendered"
    if test "$mode_state" = caddy && cmp -s "$rendered" "$LCC_CADDY_SITE" &&
        { grep -Fqx "$LCC_V2_CADDY_IMPORT" "$LCC_CADDY_MAIN" ||
            grep -Fqx "$LCC_CADDY_IMPORT" "$LCC_CADDY_MAIN"; } &&
        "$LCC_CADDY_BINARY" validate --config "$LCC_CADDY_MAIN" --adapter caddyfile &&
        systemctl is-active --quiet caddy.service &&
        lcc_wait_for_internal_health "$(lcc_effective_app_port)" 1 0 &&
        lcc_wait_for_health 1 0; then
        rm -rf -- "$backup_dir"
        trap - EXIT
        echo "Managed Caddy gateway already healthy at $LCC_PUBLIC_ORIGIN; no changes made."
        exit 0
    fi
    if test -f "$LCC_CADDY_SITE"; then
        cp -a -- "$LCC_CADDY_SITE" "$backup_dir/site"
    fi
    if test -f "$LCC_CADDY_MAIN"; then
        main_existed=1
        cp -a -- "$LCC_CADDY_MAIN" "$backup_dir/main"
    fi
    # Invoked indirectly by the EXIT trap below.
    # shellcheck disable=SC2329
    cleanup_managed_caddy() {
        local original_status=$? recovery_failed=0
        trap - EXIT
        if test -n "$temporary_site"; then rm -f -- "$temporary_site"; fi
        if test -n "$temporary_main"; then rm -f -- "$temporary_main"; fi
        if test "$transaction_active" -eq 1; then
            lcc_note "Managed Caddy failed; restoring prior Caddy configuration. Core remains installed."
            lcc_restore_caddy_configuration "$backup_dir" "$LCC_CADDY_SITE" "$LCC_CADDY_MAIN" || recovery_failed=1
            if test "$original_active" -eq 1; then
                systemctl reload caddy.service || recovery_failed=1
            else
                systemctl stop caddy.service || recovery_failed=1
            fi
        fi
        if test "$recovery_failed" -eq 0; then
            rm -rf -- "$backup_dir"
        else
            lcc_note "Caddy recovery needs operator attention; protected backup remains at $backup_dir"
        fi
        exit "$original_status"
    }
    trap cleanup_managed_caddy EXIT
    if ! test -d "$site_dir"; then
        install -d -m 0755 "$site_dir"
    fi
    temporary_site="${LCC_CADDY_SITE}.next.$$"
    install -m 0644 "$rendered" "$temporary_site"
    transaction_active=1
    mv -T -- "$temporary_site" "$LCC_CADDY_SITE"
    if test "$main_existed" -eq 0; then
        temporary_main="${LCC_CADDY_MAIN}.next.$$"
        printf '%s\n' "$LCC_V2_CADDY_IMPORT" > "$temporary_main"
        chmod 0644 "$temporary_main"
        mv -T -- "$temporary_main" "$LCC_CADDY_MAIN"
    elif ! grep -Fqx "$LCC_CADDY_IMPORT" "$LCC_CADDY_MAIN" &&
        ! grep -Fqx "$LCC_V2_CADDY_IMPORT" "$LCC_CADDY_MAIN"; then
        temporary_main="${LCC_CADDY_MAIN}.next.$$"
        cp -a -- "$LCC_CADDY_MAIN" "$temporary_main"
        printf '\n# Learning Control Center managed site\n%s\n' "$LCC_V2_CADDY_IMPORT" >> "$temporary_main"
        mv -T -- "$temporary_main" "$LCC_CADDY_MAIN"
    fi
    "$LCC_CADDY_BINARY" validate --config "$LCC_CADDY_MAIN" --adapter caddyfile ||
        lcc_die "Complete Caddy configuration validation failed."
    if test "$active_after_provision" -eq 1; then
        systemctl reload caddy.service || lcc_die "Caddy reload failed."
    else
        systemctl enable --now caddy.service || lcc_die "Caddy service start failed."
    fi
    systemctl is-active --quiet caddy.service || lcc_die "Caddy service is not active."
    lcc_wait_for_internal_health "$(lcc_effective_app_port)" 20 0.5 ||
        lcc_die "Core internal health failed during managed gateway activation."
    lcc_wait_for_health 120 0.5 ||
        lcc_die "Public HTTPS health failed; check DNS, TLS and Caddy logs."
    lcc_write_gateway_state caddy
    transaction_active=0
    rm -rf -- "$backup_dir"
    trap - EXIT
    echo "Managed Caddy gateway and public HTTPS health passed at $LCC_PUBLIC_ORIGIN."
)
