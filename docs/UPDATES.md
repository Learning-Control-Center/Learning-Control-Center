# Updates, rollback, and uninstall

## Normal update from public main

```bash
sudo /opt/learning-control-center/update.sh
# Equivalent administrator entry point:
sudo lcc-admin update
```

The installed updater invokes the generic bootstrap. It resolves GitHub public `refs/heads/main`
once to a full SHA, retrieves pinned bootstrap/source over HTTPS, validates the archive, then
enters the shared deployment transition. No Git installation, GitHub release lookup, or background
branch tracking is required. A same-SHA V2 rerun is a true no-op. A changed SHA displays the old
and new identities and asks for confirmation (`--yes` for deliberate automation; `--dry-run` for
preflight). On a V1 server use the **new public bootstrap** from [installation](INSTALLATION.md)
for the initial migration; do not first invoke its old installed updater.

Before stopping LCC, the transition validates ownership and configuration, provisions any missing
named dependencies through normal host APT, stages the immutable release and locked Python
runtime, verifies the bundled frontend and migration relation, and checks schema compatibility.
It then takes an offline `pre-update` backup, runs candidate migrations where necessary, atomically
activates, updates LCC-owned units/gateway configuration, restarts, and checks health. Failure
restores the old release and the matching database backup if migration changed the schema. The
existing public origin (including a pending unconfigured origin), timezone, secrets, initialized
account, bootstrap state, SQLite data, backup history, gateway mode, and `LCC_APP_PORT` remain in
place.

An update can start from an installation whose service is failed or auto-restarting after first
account creation with the bootstrap token still configured. The changed-SHA transition stops that
service, backs up and preserves the user and token configuration, activates the fixed release, and
verifies internal health. The token remains consumed by the existing user; remove its environment
line afterward with `sudo lcc-admin finalize-bootstrap`.

In managed Caddy mode both loopback and public HTTPS health are required. In external mode loopback
health is required and public routing is checked/reported separately; LCC does not touch the
operator's reverse proxy. If it remains pending, check `sudo lcc-admin health --public` after
external integration. There is no automatic nginx/Apache edit or OS-wide package upgrade.

## Rollback

Rollback uses an already installed immutable release, not a remote ref:

```bash
sudo /opt/learning-control-center/update.sh rollback --to main-FULL_40_CHARACTER_SHA
```

A schema-crossing rollback needs the exact matching operational backup and an explicit destructive
opt-in:

```bash
sudo /opt/learning-control-center/update.sh rollback \
  --to main-FULL_40_CHARACTER_SHA \
  --database-backup /var/backups/learning-control-center/lcc-pre-update-...sqlite3 \
  --confirm-database-replacement
```

The backup manifest, checksum, schema, and SQLite integrity are checked. Rollback first saves a
`pre-rollback` backup. Database replacement loses writes since the selected backup and revokes
restored sessions. A historical release without a configurable app port cannot serve a custom
non-8000 port; this is refused before services stop. In managed Caddy mode the owned site is
restored and public health is verified; external proxy configuration remains the operator's.

## Uninstall

The generic uninstaller removes LCC-owned application releases, tooling, units, environment, and
managed gateway integration. By default it preserves the database, operational backups, and
service account; it does not remove shared distro packages or external proxy configuration:

```bash
sudo /opt/learning-control-center/current/scripts/uninstall.sh
```

Reinstallation against preserved initialized data requires a compatible schema and secrets; remove
any consumed bootstrap token from the root-owned environment after recovery. The external proxy must be
cleaned up by its operator. Destructive removal requires both explicit flags:

```bash
sudo /path/to/reviewed/source/scripts/uninstall.sh \
  --purge-data --confirm-purge=DELETE-LCC-DATA
```

Purge deletes the protected database and backup directories and removes the dedicated service
account/group. Copy anything needed for recovery
before using it. Historical `v1.0.0` assets remain immutable; old `*-ubuntu.sh` script names in
current source are compatibility forwarders.
