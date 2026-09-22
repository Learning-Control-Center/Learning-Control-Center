# Production operations

This runbook applies to the supported Ubuntu 24.04 LTS installation described in
[`INSTALLATION.md`](INSTALLATION.md). Systemd is the canonical process manager. Do not use the
repository-root `start.sh` or `stop.sh` helpers in production; they run development servers.

## Installed layout

| Purpose | Path |
| --- | --- |
| Versioned releases | `/opt/learning-control-center/releases/<release-id>` |
| Active release | `/opt/learning-control-center/current` |
| Application environment | `/etc/learning-control-center.env` (`root:lcc`, `0640`) |
| SQLite database | `/var/lib/learning-control-center/lcc.sqlite3` |
| Operational backups | `/var/backups/learning-control-center` |
| Caddy site | `/etc/caddy/Caddyfile.d/learning-control-center.caddy` |
| Gateway mode | `/etc/learning-control-center.deployment` (root-owned) |
| Administrator command | `/usr/local/sbin/lcc-admin` |
| Persistent update entrypoint | `/opt/learning-control-center/update.sh` |

Every immutable release stores `RELEASE_ID`, `RELEASE_CHANNEL`, `SOURCE_REVISION`, and a
`RELEASE_MANIFEST` containing the selected repository, source ref, revision, and acquisition
origin. Activation/update records under `/var/lib/learning-control-center` add timestamps, schema
revisions, and backup lineage. `sudo lcc-admin status` reports the active channel, release ID, and
full source SHA before the systemd status.

Production releases also contain `frontend/dist/LCC_FRONTEND_ARTIFACT.json`. It binds the served
static files to the public frontend build inputs and is verified during acquisition, installation,
and update. The application serves those files and its API from the same loopback port. No gateway
needs filesystem access to `frontend/dist`. Node.js/npm are not installed or used on the server.

The `lcc` account is a non-login system account. Application releases are read-only to that
account. Only the data and backup directories are writable. Operational backups contain password
hashes and authentication/session state and must remain protected.

Mutating `lcc-admin`, install, update, and uninstall commands take one non-blocking deployment lock.
Concurrent administrator workflows are refused instead of racing service or database transitions.

## Service operation

Use normal systemd commands:

```bash
sudo systemctl start learning-control-center.service
sudo systemctl stop learning-control-center.service
sudo systemctl restart learning-control-center.service
sudo systemctl status learning-control-center.service
sudo systemctl enable learning-control-center.service
```

The service runs one Uvicorn worker on `127.0.0.1:LCC_APP_PORT`; the default is 8000. Managed Caddy
or an explicitly selected same-host external gateway provides public TLS and forwards the entire
site to that loopback origin. The internal port is stored in the root-owned production
environment and is never a configurable bind address.
Uvicorn proxy-header rewriting stays disabled; LCC accepts forwarded client addresses only from the
configured loopback proxy CIDR. The public Host and exact HTTPS Origin are checked against
administrator-controlled configuration; an unconfigured origin permits only loopback Host and no
browser writes. Standard output and errors go to journald.

Managed gateway mode uses a compatible package-managed Caddy service and imports only the LCC site
file into the administrator's main Caddyfile. It does not expose the application environment to
Caddy and does not execute a PATH-shadowing Caddy binary: production operations use the
package-owned `/usr/bin/caddy`. Formatting and validation are transactional; either failure
restores the prior Caddy site and main configuration. The installer does not replace unrelated
sites. Review OS package updates through the server's normal Ubuntu patching policy; LCC never
performs an OS-wide upgrade.

When LCC must provision a missing prerequisite, normal host APT authenticates and selects named
packages under the administrator's configured repository/pinning policy. The installer leaves
repository definitions and keys untouched. If metadata refresh is incomplete, it reports that
fact and lets the named package install and capability checks determine whether to proceed.
In external gateway mode LCC does not edit or reload the operator's proxy; public status stays
pending until its HTTPS route is verified.

Useful administrator commands:

```bash
sudo lcc-admin status
sudo lcc-admin health
sudo lcc-admin logs 200
sudo lcc-admin app-port
sudo lcc-admin app-port set 8123
sudo lcc-admin public-origin
sudo lcc-admin public-origin set https://lcc.example.com
sudo /opt/learning-control-center/update.sh
# Exact thin alias to the same updater:
sudo lcc-admin update
```

The persistent update entrypoint delegates through the active `current` release, so an atomic
release switch also switches the implementation it invokes without leaving a path to an obsolete
release. Normal updates resolve GitHub public `main` to one exact SHA. A
release-channel installation migrates to main only after the operator confirms the displayed
channel and immutable target. Exact pinned releases remain available through their version-bound
installers. The complete safety and rollback contract is in [`UPDATES.md`](UPDATES.md).

`lcc-admin app-port` reports the effective port and whether it is explicit or the legacy 8000
default. `lcc-admin app-port set PORT` validates and probes the new loopback port, stages the
environment, restarts only LCC, and checks internal health. In managed mode it also updates the
LCC Caddy site, validates the complete configuration, reloads Caddy, and checks public HTTPS.
In external mode the operator must coordinate the proxy upstream; automation requires
`--ack-external-proxy` with `--yes`, and public readiness may be pending. `--dry-run` performs validation
without mutation; `--yes` enables deliberate automation. Any activation failure restores the old
environment, Caddy site, service port, and health path. Recovery artifacts are retained with a
prominent path only if automatic rollback itself cannot complete.

`lcc-admin public-origin set HTTPS_ORIGIN` validates an HTTPS DNS origin without a path, query, or
fragment; `--dry-run` checks the staged change and `--yes` permits non-interactive confirmation.
It atomically updates the public origin and its derived allowed Host/Origin lists. External mode
restarts LCC and checks internal health, then reports public health separately; it never edits the
operator's tunnel or proxy. Managed mode also stages and validates the complete Caddy config,
changes the LCC-owned hostname, reloads Caddy, and requires public HTTPS health. On failure it
restores the prior environment and managed Caddy configuration and verifies recovery.

Startup holds an exclusive database-operation lock, upgrades the configured database to Alembic
head, verifies the production bootstrap/single-user invariant, recovers projection work, backfills
completed reports, and starts the report scheduler. A startup failure leaves the service failed;
inspect `sudo lcc-admin logs` rather than bypassing the check.

## Bootstrap finalization

The initial installation starts with a strong `LCC_BOOTSTRAP_TOKEN`. Open the configured HTTPS URL,
create the first user, and immediately run:

```bash
sudo lcc-admin show-bootstrap-token
sudo lcc-admin finalize-bootstrap
```

`show-bootstrap-token` requires root and a controlling terminal, writes the value only to that
terminal, and refuses once any user exists. Managed installation shows it after the HTTPS
health check; non-interactive installation never prints it. `finalize-bootstrap` stops LCC,
verifies that the current database is at the expected schema with exactly one user, removes only
the bootstrap-token line atomically, restarts the service, and verifies internal health (and public
HTTPS in managed mode). An initialized production database intentionally refuses to restart while a bootstrap
token remains configured.

## Scheduled and manual backups

The installer enables `learning-control-center-backup.timer`. It runs daily, catches up after a
missed schedule, and applies up to 15 minutes of randomized delay.

```bash
sudo lcc-admin backup-status
sudo lcc-admin backup
sudo journalctl -u learning-control-center-backup.service
```

`backup-status` reports enablement, last/next activation, configured location and retention, the
last backup-unit result, and recent files. `backup` starts the same systemd oneshot used by the
timer; it is safe while LCC is running.

The backup script uses SQLite's online backup operation, then requires successful integrity and
foreign-key checks. It also requires the copied database revision to equal the active release's
Alembic head. Each `0600` database file has a `0600` manifest containing its checksum, schema
revision, channel, release ID, full source revision, source repository/ref/origin, and database
path. `LCC_BACKUP_RETENTION_COUNT` defaults to 14 and applies only to scheduled backups. Pre-update,
pre-rollback, pre-migration, pre-import, pre-restore, and pre-recovery backups are not removed by
scheduled retention.

## Offline restore

Restore replaces the deployed SQLite database. Stop the application first and provide an absolute
backup path readable by `lcc`:

```bash
sudo systemctl stop learning-control-center.service
sudo lcc-admin restore /var/backups/learning-control-center/lcc-scheduled-...sqlite3
```

The administrator tool stops the backup timer for the operation and refuses to continue if LCC or
a backup is running. The restore command validates integrity, foreign keys, supported migration
revision, and the single-user invariant; creates a pre-restore backup; upgrades a supported older
backup in staging; installs it atomically; increments credential generation; and revokes every
restored session. It then starts LCC and checks internal health; managed Caddy also requires public
HTTPS health.

Portable JSON restore is a separate authenticated application workflow. Never pass a portable JSON
package to the SQLite restore command.

## Password recovery

From an interactive terminal:

```bash
sudo systemctl stop learning-control-center.service
sudo lcc-admin recover-password
sudo systemctl start learning-control-center.service
```

Recovery verifies the database, creates and checks a pre-recovery backup, changes the Argon2id
password, increments credential generation, revokes every session, and writes a safe audit event.

## Projection repair

Normal startup drains durable projection work. For operator-directed recovery:

```bash
sudo systemctl stop learning-control-center.service
sudo lcc-admin repair-projections
# Or enqueue every active competency first:
sudo lcc-admin repair-projections --full
sudo systemctl start learning-control-center.service
```

Both the administrator wrapper and the CLI enforce offline/exclusive operation, preventing a race
with the server. The command emits JSON. Treat `permanentFailures` as an operator-visible failure:
preserve the database and logs, correct the underlying canonical-data or software problem, and
rerun rather than editing projection tables directly.

## Updates, rollback, and uninstall

Use the controlled workflows in [`UPDATES.md`](UPDATES.md). Never update an active release in
place, run `git pull` under `/opt/learning-control-center/current`, or point old code at a database
that has crossed an incompatible migration.
