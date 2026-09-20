# Production updates, rollback, and uninstall

## Controlled update

Obtain the deliberate tagged release archive and companion checksum from the canonical GitHub
release. Review its release notes and migration implications. For example:

```bash
mkdir -p /srv/releases/v1.1.0
cd /srv/releases/v1.1.0
curl -fLO https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.1.0/learning-control-center-v1.1.0.tar.gz
curl -fLO https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.1.0/learning-control-center-v1.1.0.tar.gz.sha256
sha256sum --check learning-control-center-v1.1.0.tar.gz.sha256
tar -xzf learning-control-center-v1.1.0.tar.gz
```

Inspect the extracted `RELEASE_ID`, `SOURCE_REVISION`, `RELEASE_MANIFEST`, changelog, and archive
contents before proceeding. The update command never fetches a remote or chooses `main`, `latest`,
or another implicit version automatically.

Run the updater from the currently installed release or the reviewed candidate source:

```bash
sudo /opt/learning-control-center/current/scripts/update-ubuntu.sh apply \
  --source /srv/releases/v1.1.0/Learning-Control-Center-v1.1.0 \
  --release-id v1.1.0
```

The updater stages the candidate in a new immutable release directory, creates its constrained
Python environment, runs `pip check`, performs `npm ci` and a production build, and determines the
candidate Alembic head before interrupting service. It then:

1. stops the backup timer and application;
2. creates an exact offline `pre-update` operational backup;
3. runs guarded migrations with the candidate code;
4. atomically changes the `current` symlink;
5. installs the candidate units and rendered Caddy site;
6. starts LCC and the timer, reloads Caddy, and verifies the public HTTPS health endpoint;
7. records release/source/schema/backup identity under `/var/lib/learning-control-center`.

If activation fails, the updater automatically returns to the previous release. When the candidate
has a different schema head, automatic recovery also restores the exact pre-update database backup
before restarting old code. Candidate files remain available for diagnosis.

## Explicit rollback

When the installed target release expects the database's current schema, code-only rollback is:

```bash
sudo /opt/learning-control-center/current/scripts/update-ubuntu.sh rollback --to v1.0.0
```

If schema heads differ, rollback is intentionally refused unless the operator supplies a database
backup from the target release and explicitly accepts database replacement:

```bash
sudo /opt/learning-control-center/current/scripts/update-ubuntu.sh rollback \
  --to v1.0.0 \
  --database-backup /var/backups/learning-control-center/lcc-pre-update-...sqlite3 \
  --confirm-database-replacement
```

Schema-crossing rollback restores the supplied database and therefore loses application changes
made after that backup. It revokes restored sessions. A new `pre-rollback` backup of the current
release/database is created first. Do not claim or attempt code-only rollback across incompatible or
irreversible migrations.

## Uninstall

Default uninstall preserves the database, operational backups, and `lcc` account:

```bash
sudo /opt/learning-control-center/current/scripts/uninstall-ubuntu.sh
```

It disables and removes LCC units, removes application releases, administrator command, production
environment, and LCC Caddy site, reloads systemd/Caddy, and prints the preserved paths. Keeping the
service account preserves meaningful ownership for later reinstall or manual recovery.

If a reinstall is planned, securely copy `/etc/learning-control-center.env` before uninstall or
prepare a replacement with the same paths and a new security secret. An existing initialized
database must be configured without `LCC_BOOTSTRAP_TOKEN`; the startup invariant deliberately
rejects a bootstrap token once a user exists. Reinstall a schema-compatible release first, then use
the controlled updater for newer releases.

Destructive removal requires two explicit flags:

```bash
sudo /path/to/reviewed/source/scripts/uninstall-ubuntu.sh \
  --purge-data \
  --confirm-purge=DELETE-LCC-DATA
```

That command permanently removes the SQLite data directory, operational backups, and service
account. Copy any required recovery artifacts off the server first.
