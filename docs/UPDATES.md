# Production updates, rollback, and uninstall

## Normal update

The installed user-facing updater is:

```bash
sudo /opt/learning-control-center/update.sh
```

`sudo lcc-admin update` is a thin alias to that same path. No backup, migration, staging, or source
resolution logic is duplicated in the administrator wrapper.

The updater always targets validated `main`. It reads the installed source metadata, resolves that
same GitHub or Forgejo repository's public `refs/heads/main` once to a full SHA, and compares it
with the installed source revision. If a main installation already has that SHA it prints
`Learning Control Center is already up to date.` and exits zero. Otherwise it displays the current
channel/release/SHA and target main SHA, then asks for confirmation. `--yes` is available for
deliberate automation, and `--dry-run` resolves and verifies the target without applying it.

Main advances only when the operator runs an update; there is no poller or background branch
following. A release-channel installation is a deliberate one-time channel migration: the updater
shows `stable -> main`, obtains confirmation, and passes `--confirm-channel-change` only to the
verified canonical update engine.

Rerunning the canonical main-first curl command is equivalent:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap-ubuntu.sh | sudo bash
```

It installs a fresh host, no-ops when the exact main SHA is already active, or delegates a changed
SHA to the same canonical update engine. A version-specific launcher remains available when an
operator intentionally wants an immutable pinned release snapshot:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

It no-ops at v1.0.1, refuses to downgrade a newer stable installation, and does not silently move
that installation to main. Normal update discovery does not query release APIs or `releases/latest`.

The public update resolver uses the shared bootstrap preflight to install only missing required
Ubuntu packages from the already documented signed Ubuntu source; it never performs an OS-wide
upgrade. The canonical update engine then verifies those runtime prerequisites and the candidate's
source-bound frontend artifact. It creates a new exact-constrained Python virtual environment but
does not run Node.js/npm on the production host. It then compares the deployed database revision
with the candidate Alembic graph and classifies the transition:

- same revision: allowed;
- candidate is a forward descendant: allowed;
- candidate is backward: refused; use database-aware rollback;
- divergent or unknown: refused before service stop.

Only after the candidate is fully staged does the engine stop the timer/application, create an
offline `pre-update` backup, migrate with candidate code, activate atomically, update units/Caddy,
start services, check public HTTPS health, and record channel/source/schema/backup identity under
`/var/lib/learning-control-center`. Failed
activation restores the previous release; if migrations changed the database, it also restores the
exact pre-update backup before old code restarts.

The configured internal application port is persisted in
`/etc/learning-control-center.env`. Bootstrap reruns, exact-SHA no-ops, changed-SHA updates,
`update.sh`, `lcc-admin update`, service restarts, and reboots preserve it. Updates do not regenerate
the environment or reset a custom port to 8000. An older environment without `LCC_APP_PORT`
continues to resolve to the legacy default 8000 without being rewritten.

The main-first resolver and release-bound bootstrap acquire and verify their candidate, then
delegate the complete immutable identity to `scripts/update-ubuntu.sh apply`. That script remains
the sole backup/migration/staging/activation engine. It recognizes v1.0.0's legacy
artifact-content revision for migration/history/rollback, while every newly staged deployment
carries a full Git commit SHA.

## Channel and pinned-release rules

- Main to a newly resolved exact main SHA is the normal update path.
- Release to main is the normal migration path but always requires explicit confirmation.
- Main to a pinned release is never automatic; use that release's exact bound `install.sh`, which
  also requires explicit channel-change confirmation.
- Pinned release-to-release changes use exact version-bound installers, not latest-release
  discovery.
- The same immutable identity is a successful no-op and is not rebuilt.
- Downgrades are refused as updates and use rollback instead.
- Any transition requiring a database downgrade is refused before service stop.

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

A historical release that predates configurable ports cannot honor a custom non-8000 port, so that
rollback is refused before services stop. At effective port 8000, rollback may transactionally
remove the new key for an old strict environment parser; failed activation restores the current
environment and key before recovery.

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
