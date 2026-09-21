# Public release process

Canonical public repository:

`https://github.com/Learning-Control-Center/Learning-Control-Center.git`

Explicit Forgejo mirror:

`https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center.git`

This is the preparation workflow for v1.0.1; it does not claim that its tag or remote releases
already exist.

## Trust and branch model

Development occurs on sanitized `dev`; reviewed release work flows `dev` to sanitized `main`.
Public history must contain no private `memory-bank/` path, private author email, internal refs,
databases, backups, or secrets. Push only explicitly selected refs. Never use `--all`, `--mirror`,
or wildcard branch refspecs against the public remote.

Because `dev` and `main` now both descend from the sanitized public history, the one-time v1.0.0
history rewrite, private-history email rewrite, separate sanitized-clone regeneration, and branch
replacement are not repeated for v1.0.1. Current-tree and reachable-history privacy/secret checks
remain mandatory before every release.

## Validate and merge the candidate

1. Complete implementation and focused tests on `dev`.
2. Run backend/frontend, deployment, bootstrap, packaging, ShellCheck, Gitleaks, documentation-link,
   disposable-deployment, and diff/line-ending checks.
3. Review exact changes, dependency locks, migrations, update/rollback behavior, and release notes.
4. Merge reviewed `dev` into `main` without importing any private archived history.
5. Re-run critical validation and public-history/privacy checks on clean `main`.
6. Confirm `pyproject.toml`, frontend package metadata, API/export version metadata, and changelog all
   identify 1.0.1.

Release packaging requires Node.js 22 LTS or 24 LTS and installs the locked frontend dependency
graph with `npm ci --ignore-scripts`. The committed production frontend must already verify against
its source inputs. Packaging rebuilds it in isolation and fails unless the rebuilt manifest is
byte-identical, then includes that artifact in the release archive. Node.js is never required on
the production host.

Record the exact clean `main` candidate SHA. Confirm the configured development database hash and
mtime did not change.

## Tag and deterministic assets

Only after validation succeeds, create the annotated tag on the exact public candidate:

```bash
git tag -a v1.0.1 -m "Learning Control Center v1.0.1"
```

Do not sign unless an appropriate signing key is deliberately configured. Package the tagged tree
outside the repository:

```bash
scripts/package-release.sh \
  --release-id v1.0.1 \
  --source-ref v1.0.1 \
  --output-dir /tmp/lcc-v1.0.1-release
```

The command produces exactly:

- `install.sh` — deterministic stable-only launcher with embedded `v1.0.1` and archive SHA-256;
- `learning-control-center-v1.0.1.tar.gz` — deterministic public release tree;
- `learning-control-center-v1.0.1.tar.gz.sha256` — published archive checksum.

The archive has top-level directory `Learning-Control-Center-v1.0.1/` and immutable
`RELEASE_ID`, `RELEASE_CHANNEL`, `SOURCE_REVISION`, and `RELEASE_MANIFEST` files. It excludes Git
metadata, contributor-only tests, private/runtime state, local environments, databases, and backups;
preserves executable modes; contains the verified production frontend; normalizes
ownership/timestamps; and uses deterministic gzip headers.

Package twice into empty directories and require byte-identical archives, checksums, and launchers.
Then inspect all assets:

```bash
cd /tmp/lcc-v1.0.1-release
sha256sum --check learning-control-center-v1.0.1.tar.gz.sha256
tar -tzf learning-control-center-v1.0.1.tar.gz
grep -E '^readonly (embedded_stable_ref|embedded_archive_sha256|stable_only_launcher)=' install.sh
```

Exercise the bound `install.sh` against local assets through the dry-run/test seam. Exercise generic
stable bootstrap and both interactive and commit-asserted main acquisition. Reconfirm no secret is
printed or passed on argv.

## Publication order

1. Reconfirm clean sanitized `main`, exact tag target, intended refs, Gitleaks/history results, and
   all three asset hashes.
2. Explicitly push only `main` and `refs/tags/v1.0.1` to Forgejo; create the Forgejo release and
   upload the three assets without renaming them.
3. Explicitly push only `main` and `refs/tags/v1.0.1` to GitHub; create the GitHub release and upload
   the same three byte-identical assets.
4. Re-download every asset from each host and verify hashes, archive member safety, embedded
   `install.sh` identity/digest, and exact tag/source revision.
5. Run the stable installer test from the canonical GitHub URL.
6. Run the main-channel installer test and verify the recorded SHA equals the deliberately resolved
   public `main` tip.
7. Complete a real clean Ubuntu Server 24.04 acceptance test: prerequisite provisioning, install,
   HTTPS health, first-user bootstrap,
   finalization, systemd restart, scheduled/manual backup, update preflight, and preserve-by-default
   uninstall.
8. Publish release notes from `CHANGELOG.md` and advertise the quick-install command only after the
   assets and acceptance checks succeed.

The stable command is:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

The explicit unstable-main command uses the immutable v1.0.1 bootstrap implementation:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.1/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- --channel main
```

## Updates and rollback

Bootstrap is for fresh install or exact-identity repair, never an implicit update. Existing
installations use the controlled workflow in [`UPDATES.md`](UPDATES.md). The updater verifies and
stages the candidate before stopping services, classifies Alembic compatibility, creates a
pre-update backup, and records immutable source identity. Code-only rollback is allowed only at the current database schema;
schema-crossing rollback requires the matching database backup and explicit replacement acceptance.
