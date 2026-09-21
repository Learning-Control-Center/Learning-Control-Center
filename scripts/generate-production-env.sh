#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage: generate-production-env.sh --domain HOST --timezone ZONE --output FILE [--root DIR]

Generate one complete Learning Control Center production environment file. Secrets
are created inside this process and are never accepted as command-line arguments.

--root is restricted to disposable paths beneath /tmp and exists only for tests.
EOF
}

domain=""
timezone=""
output_file=""
install_root="/"

while test "$#" -gt 0; do
    case "$1" in
        --domain) domain="${2:?Missing --domain value}"; shift 2 ;;
        --timezone) timezone="${2:?Missing --timezone value}"; shift 2 ;;
        --output) output_file="${2:?Missing --output value}"; shift 2 ;;
        --root) install_root="${2:?Missing --root value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; lcc_die "Unknown option: $1" ;;
    esac
done

test -n "$domain" || lcc_die "--domain is required."
test -n "$timezone" || lcc_die "--timezone is required."
test -n "$output_file" || lcc_die "--output is required."
lcc_validate_public_hostname "$domain"
[[ "$output_file" = /* ]] || lcc_die "--output must be an absolute path."
test ! -e "$output_file" || lcc_die "Refusing to replace existing environment file: $output_file"

if test "$install_root" != "/"; then
    [[ "$install_root" = /* ]] || lcc_die "--root must be absolute."
    install_root="$(readlink -m -- "$install_root")"
    test "$install_root" != "/" && [[ "$install_root" = /tmp/* ]] || \
        lcc_die "--root must resolve beneath /tmp and must not resolve to /tmp itself or /."
fi

current_release="$(lcc_prefixed_path "$install_root" "$LCC_CURRENT_RELEASE")"
database_file="$(lcc_prefixed_path "$install_root" "$LCC_DATABASE_FILE")"
backup_directory="$(lcc_prefixed_path "$install_root" "$LCC_BACKUP_DIRECTORY_DEFAULT")"

python3 - "$domain" "$timezone" "$output_file" "$current_release" \
    "$database_file" "$backup_directory" <<'PY'
import json
import os
import secrets
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

domain, timezone, output, release_root, database, backups = sys.argv[1:]
try:
    ZoneInfo(timezone)
except Exception as exc:
    raise SystemExit(f"Invalid IANA application timezone: {timezone}") from exc


def strong_secret() -> str:
    markers = ("change", "replace", "example", "password", "secret")
    while True:
        value = secrets.token_urlsafe(48)
        if len(value) >= 32 and len(set(value)) >= 12 and not any(
            marker in value.lower() for marker in markers
        ):
            return value


security_secret = strong_secret()
bootstrap_token = strong_secret()
while bootstrap_token == security_secret:
    bootstrap_token = strong_secret()

origin = f"https://{domain}"
lines = [
    "# Generated Learning Control Center production environment.",
    "# Managed by the operator; keep this file root:lcc 0640 after installation.",
    "LCC_ENVIRONMENT=production",
    f"LCC_DATABASE_URL=sqlite:///{database}",
    f"LCC_BACKUP_DIRECTORY={backups}",
    f"LCC_RELEASE_ROOT={release_root}",
    f"LCC_PUBLIC_ORIGIN={origin}",
    f"LCC_ALLOWED_ORIGINS='{json.dumps([origin], separators=(',', ':'))}'",
    f"LCC_ALLOWED_HOSTS='{json.dumps([domain], separators=(',', ':'))}'",
    "LCC_TRUSTED_PROXY_CIDRS='[\"127.0.0.1/32\"]'",
    f"LCC_BOOTSTRAP_TOKEN={bootstrap_token}",
    f"LCC_SECURITY_SECRET={security_secret}",
    f"LCC_APP_TIMEZONE={timezone}",
    "LCC_SESSION_COOKIE_NAME=lcc_session",
    "LCC_SESSION_IDLE_TIMEOUT_MS=604800000",
    "LCC_SESSION_ABSOLUTE_TIMEOUT_MS=2592000000",
    "LCC_MAX_IMPORT_BYTES=10485760",
    "LCC_LOGIN_RATE_LIMIT_ATTEMPTS=5",
    "LCC_LOGIN_RATE_LIMIT_WINDOW_MS=300000",
    "LCC_IMPORT_RATE_LIMIT_ATTEMPTS=10",
    "LCC_IMPORT_RATE_LIMIT_WINDOW_MS=600000",
    "LCC_BACKUP_RETENTION_COUNT=14",
]

descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
os.chmod(output, 0o600)
PY

lcc_load_environment "$output_file"
lcc_validate_environment "$current_release"
test "$(lcc_public_hostname)" = "$domain" || lcc_die "Generated hostname validation failed."
test "$LCC_DATABASE_URL" = "sqlite:///$database_file" || \
    lcc_die "Generated database path validation failed."
test "$LCC_BACKUP_DIRECTORY" = "$backup_directory" || \
    lcc_die "Generated backup path validation failed."
