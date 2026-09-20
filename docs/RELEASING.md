# Public release process

The canonical public repository is:

`https://github.com/Learning-Control-Center/Learning-Control-Center.git`

This document describes the intended process; it does not mean that a tag or GitHub release has
already been published.

## Trust boundary

Prepare public releases only from the separately sanitized public repository. Its selected public
history must contain no `memory-bank/` path, private author email, internal refs, local databases,
backups, or secrets. Never publish the private source repository with `--mirror` or `--all`.

The release scripts do not fetch or select a new source revision. The operator chooses and reviews
the exact commit before tagging it.

## Validate the candidate

From a clean sanitized `main` checkout:

```bash
.venv/bin/ruff format --check backend scripts
.venv/bin/ruff check backend scripts
.venv/bin/mypy backend/app
.venv/bin/pytest
cd frontend
npm ci
npm run lint
npm run test
npm run build
```

Also run ShellCheck, Gitleaks against the current tree and full intended public history, documentation
link checks, the disposable deployment test, and `git diff --check`. Record the exact candidate SHA
and verify that the configured development database hash and modification time did not change.

## Prepare the tag and assets

Confirm that `pyproject.toml`, `frontend/package.json`, and the API version all identify `1.0.0`, then
create the annotated tag in the sanitized repository:

```bash
git tag -a v1.0.0 -m "Learning Control Center v1.0.0"
```

Do not sign the tag unless an appropriate signing key is deliberately configured. Package exactly
the tagged tree into a destination outside the repository:

```bash
scripts/package-release.sh \
  --release-id v1.0.0 \
  --source-ref v1.0.0 \
  --output-dir /tmp/lcc-v1.0.0-release
```

The command produces these GitHub release assets:

- `learning-control-center-v1.0.0.tar.gz`
- `learning-control-center-v1.0.0.tar.gz.sha256`

The archive has one top-level directory, `Learning-Control-Center-v1.0.0/`, plus `RELEASE_ID`,
`SOURCE_REVISION`, and `RELEASE_MANIFEST` identity files. It is built from tracked content, omits
private/runtime/test-fixture paths, preserves executable modes, normalizes timestamps and ownership,
and is gzip-encoded without a variable header timestamp.

Verify the checksum and inspect the complete member list before publication:

```bash
cd /tmp/lcc-v1.0.0-release
sha256sum --check learning-control-center-v1.0.0.tar.gz.sha256
tar -tzf learning-control-center-v1.0.0.tar.gz
```

## Publication order

1. Reconfirm that only sanitized `main` and the intended annotated tag will be selected.
2. Push sanitized `main` explicitly to the canonical GitHub repository.
3. Push only `refs/tags/v1.0.0`.
4. Create the GitHub release for `v1.0.0` and upload both generated assets without renaming them.
5. Re-download both assets and repeat checksum and archive inspection.
6. Exercise `scripts/bootstrap-ubuntu.sh --dry-run` against the published assets.
7. Publish release notes based on `CHANGELOG.md`; only then advertise the quick-install command.

Never use `main`, `latest`, an unreviewed moving ref, or an automatically resolved tag as the
production install identity.

The release-pinned bootstrap URL is:

`https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.0/scripts/bootstrap-ubuntu.sh`

## Updates and rollback

The bootstrap is for initial acquisition and installation. Existing installations use the
controlled update workflow in `UPDATES.md` with an extracted, checksum-verified release archive.
The updater creates a pre-update operational backup before migration and activation.

Code-only rollback is allowed only when the target code expects the current database schema. A
schema-crossing rollback requires the matching pre-update database backup and explicit acceptance
of database replacement. Do not describe rollback as lossless when application changes occurred
after that backup.
