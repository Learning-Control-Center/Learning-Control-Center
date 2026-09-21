#!/usr/bin/env bash
set -euo pipefail

umask 077

repository_root=""
source_ref="dev"
public_base="main"
output_directory=""

usage() {
    cat <<'EOF'
Usage: scripts/prepare-public-promotion.sh --output-dir DIR [options]

Create an isolated public-main candidate without making private development
ancestry reachable. The candidate starts from the existing public main branch,
then receives a sanitized tree export from the private development ref.

Options:
  --output-dir DIR       Required new candidate repository outside the source repository.
  --repository DIR       Source Git repository (default: script repository).
  --source-ref REF       Private source commit/ref to export (default: dev).
  --public-base BRANCH   Existing sanitized public branch (default: main).
  -h, --help             Show this help.
EOF
}

die() {
    printf 'prepare-public-promotion: %s\n' "$*" >&2
    exit 1
}

note() {
    printf 'prepare-public-promotion: %s\n' "$*"
}

history_contains_private_path() {
    local repository="$1"
    local revision="$2"
    local commit

    while IFS= read -r commit; do
        if git -C "$repository" ls-tree -r --name-only "$commit" -- \
            AGENTS.md memory-bank | grep -q .; then
            return 0
        fi
    done < <(git -C "$repository" rev-list "$revision")
    return 1
}

history_contains_private_email() {
    local repository="$1"
    local revision="$2"

    git -C "$repository" log "$revision" --format='%ae%n%ce' | \
        grep -Fqi 'waqsea@waqsea.com'
}

while (($#)); do
    case "$1" in
        --output-dir) output_directory="${2:?Missing --output-dir value}"; shift 2 ;;
        --repository) repository_root="${2:?Missing --repository value}"; shift 2 ;;
        --source-ref) source_ref="${2:?Missing --source-ref value}"; shift 2 ;;
        --public-base) public_base="${2:?Missing --public-base value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

test -n "$output_directory" || die "--output-dir is required."
[[ "$source_ref" =~ ^[A-Za-z0-9._/-]+$ ]] || die "--source-ref contains unsafe characters."
[[ "$public_base" =~ ^[A-Za-z0-9._/-]+$ ]] || die "--public-base contains unsafe characters."

for command_name in git tar rsync mktemp sed grep gitleaks; do
    command -v "$command_name" >/dev/null || die "Required command is unavailable: $command_name"
done

if test -z "$repository_root"; then
    repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
else
    repository_root="$(cd "$repository_root" && pwd -P)"
fi

git -C "$repository_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
    die "--repository must be a Git working tree."
test -z "$(git -C "$repository_root" status --porcelain --untracked-files=all)" || \
    die "The source repository must be clean before public promotion."

source_commit="$(git -C "$repository_root" rev-parse --verify "${source_ref}^{commit}")" || \
    die "Unable to resolve source ref: $source_ref"
public_base_ref="refs/heads/$public_base"
git -C "$repository_root" show-ref --verify --quiet "$public_base_ref" || \
    die "Public base branch does not exist: $public_base"
public_base_commit="$(git -C "$repository_root" rev-parse --verify "${public_base_ref}^{commit}")"

if history_contains_private_path "$repository_root" "$public_base_commit"; then
    die "The public base history already contains AGENTS.md or memory-bank/. Sanitize it first."
fi
if history_contains_private_email "$repository_root" "$public_base_commit"; then
    die "The public base history contains the private author email. Sanitize it first."
fi

output_parent="$(mkdir -p "$(dirname "$output_directory")" && \
    cd "$(dirname "$output_directory")" && pwd -P)"
output_directory="$output_parent/$(basename "$output_directory")"
case "$output_directory/" in
    "$repository_root/"*) die "--output-dir must be outside the source repository." ;;
esac
test ! -e "$output_directory" || die "--output-dir must not already exist."

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/lcc-public-promotion.XXXXXXXX")"
trap 'rm -rf -- "$temporary_directory"' EXIT
export_root="$temporary_directory/export"
mkdir -p "$export_root"

note "Exporting private source revision $source_commit"
git -C "$repository_root" archive --format=tar "$source_commit" | \
    tar -xf - -C "$export_root"

# These paths are intentionally valid on private dev and categorically forbidden publicly.
rm -rf -- "$export_root/AGENTS.md" "$export_root/memory-bank"
test -f "$export_root/.gitignore" || die "The source export lacks .gitignore."
sed -E -i \
    -e '\|^/?AGENTS\.md$|d' \
    -e '\|^!?/?memory-bank(/.*)?$|d' \
    "$export_root/.gitignore"
cat >> "$export_root/.gitignore" <<'EOF'

# Public branch guard: private agent context must never be tracked.
/AGENTS.md
/memory-bank/
EOF

note "Cloning sanitized public base $public_base_commit"
git clone --quiet --no-local --no-tags --single-branch --branch "$public_base" -- \
    "$repository_root" "$output_directory"
git -C "$output_directory" remote remove origin
test "$(git -C "$output_directory" rev-parse HEAD)" = "$public_base_commit" || \
    die "Candidate did not start from the expected public base commit."

rsync -a --delete --exclude=.git/ "$export_root/" "$output_directory/"
test ! -e "$output_directory/AGENTS.md" || die "AGENTS.md survived public-tree sanitization."
test ! -e "$output_directory/memory-bank" || die "memory-bank/ survived public-tree sanitization."
grep -Fqx '/AGENTS.md' "$output_directory/.gitignore" || \
    die "Public .gitignore does not guard AGENTS.md."
grep -Fqx '/memory-bank/' "$output_directory/.gitignore" || \
    die "Public .gitignore does not guard memory-bank/."

(cd "$output_directory" && \
    gitleaks dir --no-banner --redact --exit-code 1 --config .gitleaks.toml . >/dev/null)

git -C "$output_directory" add -A
git -C "$output_directory" diff --cached --check
if git -C "$output_directory" diff --cached --quiet; then
    note "The sanitized source tree already matches public main; no commit was created."
else
    source_date="$(git -C "$repository_root" show -s --format=%aI "$source_commit")"
    GIT_AUTHOR_NAME='WaqSea' \
    GIT_AUTHOR_EMAIL='contact@waqsea.com' \
    GIT_AUTHOR_DATE="$source_date" \
    GIT_COMMITTER_NAME='WaqSea' \
    GIT_COMMITTER_EMAIL='contact@waqsea.com' \
    GIT_COMMITTER_DATE="$source_date" \
        git -C "$output_directory" commit --quiet \
            -m "chore: promote validated public source"
fi

if history_contains_private_path "$output_directory" main; then
    die "Candidate public history contains AGENTS.md or memory-bank/."
fi
if history_contains_private_email "$output_directory" main; then
    die "Candidate public history contains the private author email."
fi
test "$(git -C "$output_directory" for-each-ref --format='%(refname)' | sort)" = \
    'refs/heads/main' || die "Candidate contains an unintended ref."
(cd "$output_directory" && \
    gitleaks git --no-banner --redact --exit-code 1 --config .gitleaks.toml . >/dev/null)
test -z "$(git -C "$output_directory" status --porcelain)" || \
    die "Candidate repository is not clean."

note "Public candidate is ready at $output_directory"
note "Private source revision: $source_commit"
note "Public base revision: $public_base_commit"
note "Candidate public revision: $(git -C "$output_directory" rev-parse HEAD)"
