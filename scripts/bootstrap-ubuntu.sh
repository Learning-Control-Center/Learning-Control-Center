#!/usr/bin/env bash
set -euo pipefail
umask 077

# Compatibility name for the normal public-main bootstrap. Historical release
# assets keep their own immutable copy of the older installer.
script_directory="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]:-.}")" && pwd)"
if test -f "${BASH_SOURCE[0]:-}" && test -x "$script_directory/bootstrap.sh"; then
    exec "$script_directory/bootstrap.sh" "$@"
fi

# A piped or standalone compatibility launcher has no adjacent source tree.
# Fetch only the generic stage zero; it resolves main once and pins all later
# bootstrap and archive requests to that full SHA.
for argument in "$@"; do
    case "$argument" in
        --repository-url|--repository-url=*|--asset-base-url|--asset-base-url=*|--ref|--ref=*)
            echo 'The V1 source/release override is unsupported by the GitHub-only V2 installer.' >&2
            exit 1 ;;
    esac
done
command -v curl >/dev/null || { echo 'bootstrap-ubuntu.sh requires curl.' >&2; exit 1; }
temporary="$(mktemp /tmp/lcc-bootstrap-compat.XXXXXXXX)"
trap 'rm -f -- "$temporary"' EXIT
curl -q --fail --location --silent --show-error --max-time 120 --max-redirs 5 \
    --max-filesize 1048576 --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --output "$temporary" \
    https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh
bash "$temporary" "$@"
