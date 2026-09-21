# Production updates, rollback, and uninstall

## Normal update

The installed user-facing updater is:

```bash
sudo /opt/learning-control-center/update.sh
```

`sudo lcc-admin update` is a thin alias to that same path. No backup, migration, staging, or source
resolution logic is duplicated in the administrator wrapper.

The default never changes channel:

- stable resolves the newest final semantic release from the recorded GitHub or Forgejo source;
- main resolves the recorded repository's public `refs/heads/main` once to a full SHA.

The updater displays current and target identities and asks for confirmation. `--yes` is available
for deliberate automation, and `--dry-run` resolves and verifies the target without applying it.
If the exact release/SHA is already active it prints `Learning Control Center is already up to
date.` and exits zero. Main advances only when the operator runs an update; there is no poller or
background branch following.

Stable discovery reads the host's public release API, ignores drafts and prereleases, selects the
highest final `vMAJOR.MINOR.PATCH`, and then downloads that exact release's bound `install.sh`.
The launcher's embedded archive digest must agree with the published checksum. GitHub is the
default; Forgejo is used only when it is the installation's explicitly recorded source. There is
no cross-host fallback.

Rerunning the stable curl command is equivalent:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/latest/download/install.sh | sudo bash
```

The downloaded release-bound launcher installs a fresh host or delegates an older installation to
the same canonical update engine. A version-specific launcher targets only its embedded release:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

It no-ops at v1.0.1 and refuses to downgrade a newer stable installation.

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

Only after the candidate is fully staged does it stop the timer/application, create an offline `pre-update` backup, migrate with candidate
code, activates atomically, updates units/Caddy, starts services, checks public HTTPS health, and
records channel/source/schema/backup identity under `/var/lib/learning-control-center`. Failed
activation restores the previous release; if migrations changed the database, it also restores the
exact pre-update backup before old code restarts.

The public resolver and release-bound bootstrap acquire and verify the candidate, then delegate the
complete immutable identity to `scripts/update-ubuntu.sh apply`. That script remains the sole
backup/migration/staging/activation engine. It recognizes v1.0.0's legacy artifact-content
revision for history/rollback, while every newly staged release carries a full Git commit SHA.

## Channel-transition rules

- Stable to newer stable and main to a newly resolved exact main SHA are the default paths.
- Stable to main requires `sudo /opt/learning-control-center/update.sh --channel main`.
- Main to stable requires `sudo /opt/learning-control-center/update.sh --channel stable`.
- The thin aliases `sudo lcc-admin update --channel main` and `sudo lcc-admin update --channel
  stable` forward those exact requests to the same resolver.
- Both channel changes show exact current/target identities and require confirmation. The internal
  engine additionally requires `--confirm-channel-change` from its verified caller.
- The same release ID or source SHA is a successful no-op and is not rebuilt.
- Stable downgrades are refused and use rollback instead.
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
