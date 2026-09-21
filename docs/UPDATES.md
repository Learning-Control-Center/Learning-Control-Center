# Production updates, rollback, and uninstall

## Controlled update

Updates always target reviewed source and a complete immutable identity. The updater never fetches
`latest`, follows `main`, or resolves a release automatically.

For stable releases, download the deliberate archive and checksum from the canonical GitHub
release, verify them, and extract into an operator-owned staging directory:

```bash
mkdir -p "$HOME/lcc-releases/v1.1.0"
cd "$HOME/lcc-releases/v1.1.0"
curl -fLO https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.1.0/learning-control-center-v1.1.0.tar.gz
curl -fLO https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.1.0/learning-control-center-v1.1.0.tar.gz.sha256
sha256sum --check learning-control-center-v1.1.0.tar.gz.sha256
tar -xzf learning-control-center-v1.1.0.tar.gz
```

Inspect `RELEASE_ID`, `RELEASE_CHANNEL`, `SOURCE_REVISION`, `RELEASE_MANIFEST`, the changelog, and
archive contents. Then pass the manifest identity explicitly:

```bash
sudo "$HOME/lcc-releases/v1.1.0/Learning-Control-Center-v1.1.0/scripts/update-ubuntu.sh" apply \
  --source "$HOME/lcc-releases/v1.1.0/Learning-Control-Center-v1.1.0" \
  --channel stable \
  --release-id v1.1.0 \
  --source-revision FULL_40_CHARACTER_TAG_COMMIT_SHA \
  --source-repository https://github.com/Learning-Control-Center/Learning-Control-Center.git \
  --source-ref refs/tags/v1.1.0 \
  --source-origin https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download
```

For `main`, first perform a deliberate shallow fetch of public `refs/heads/main`, record the exact
`FETCH_HEAD^{commit}`, and leave a clean detached checkout. Apply it as channel `main`, release ID
`main-<full-sha>`, source ref `refs/heads/main`, and that exact source revision. The updater verifies
the checkout SHA; a moving branch name is never the installed identity.

The updater stages and fully builds the candidate before interrupting service. It compares the
deployed database revision with the candidate Alembic graph and classifies the transition:

- same revision: allowed;
- candidate is a forward descendant: allowed;
- candidate is backward: refused; use database-aware rollback;
- divergent or unknown: refused before service stop.

It then stops the timer/application, creates an offline `pre-update` backup, migrates with candidate
code, activates atomically, updates units/Caddy, starts services, checks public HTTPS health, and
records channel/source/schema/backup identity under `/var/lib/learning-control-center`. Failed
activation restores the previous release; if migrations changed the database, it also restores the
exact pre-update backup before old code restarts.

Run the updater from the verified candidate tree when crossing from v1.0.0 to v1.0.1 so the new
channel/source contract is available. It recognizes v1.0.0's legacy artifact-content revision for
history/rollback purposes, while every newly staged release must carry a full Git commit SHA.

## Channel-transition rules

- Stable to a newer stable tag is the normal update path. Older/equal stable identities are refused.
- Main to a different exact main SHA is allowed only when deliberately supplied; there is no poller
  or auto-follow behavior.
- Stable to main and main to stable require `--confirm-channel-change` in addition to the complete
  target identity.
- The same release ID or source SHA is reported as already active and is not rebuilt silently.
- Any transition requiring a database downgrade is refused as an update, including channel changes.

GitHub is the canonical default acquisition source. Forgejo may be selected explicitly, but there
is no silent fallback and its repository/asset origin must be recorded. If mirror `main` differs,
its different exact SHA is a different candidate.

## Explicit rollback

Rollback targets an already installed immutable release directory, never a remote ref. This means a
previous main installation returns to its stored exact SHA without resolving the network branch.

When the target code expects the current database schema:

```bash
sudo /opt/learning-control-center/current/scripts/update-ubuntu.sh rollback --to v1.0.1
```

If schema heads differ, rollback is refused unless the matching database backup is supplied and
database replacement is explicitly accepted:

```bash
sudo /opt/learning-control-center/current/scripts/update-ubuntu.sh rollback \
  --to main-FULL_40_CHARACTER_SHA \
  --database-backup /var/backups/learning-control-center/lcc-pre-update-...sqlite3 \
  --confirm-database-replacement
```

Schema-crossing rollback loses application changes made after the selected backup and revokes
restored sessions. A new `pre-rollback` backup of the current code/database pairing is created
first. Deployment records retain previous/target channel, release ID, source SHA, schema revisions,
backup path, and activation time.

## Uninstall

Default uninstall preserves the database, operational backups, and `lcc` account:

```bash
sudo /opt/learning-control-center/current/scripts/uninstall-ubuntu.sh
```

It disables and removes LCC units, application releases, administrator command, production
environment, and LCC Caddy site, then reports preserved paths. Keeping the service account preserves
meaningful ownership for recovery.

If reinstalling an initialized database, securely preserve or recreate the environment without
`LCC_BOOTSTRAP_TOKEN`; startup rejects a bootstrap token after the user exists. Reinstall a
schema-compatible release first, then use the controlled updater.

Destructive removal requires two explicit flags:

```bash
sudo /path/to/reviewed/source/scripts/uninstall-ubuntu.sh \
  --purge-data \
  --confirm-purge=DELETE-LCC-DATA
```

That permanently removes SQLite data, operational backups, and the service account. Copy recovery
artifacts off the server first.
