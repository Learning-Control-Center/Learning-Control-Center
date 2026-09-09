# Production operations

Run LCC as one supervised Uvicorn worker bound to loopback, with proxy-header rewriting disabled.
The supplied Caddy configuration is the only trusted TLS reverse proxy; configure the application
with its exact HTTPS public origin, host, and the proxy's loopback CIDR.

Store the SQLite database and backup directory at separate absolute paths outside the application
and frontend trees. Keep the environment file readable only by the service account. After the first
successful bootstrap, remove `LCC_BOOTSTRAP_TOKEN` before restarting the service.

Enable `learning-control-center-backup.timer` and monitor failed systemd units. The backup script
uses the same `LCC_DATABASE_URL` as the application, performs SQLite's online backup operation,
validates integrity, foreign keys, and schema revision, writes a checksum/revision/app-version
manifest, and retains the newest configured count.
Treat every operational backup as sensitive because it contains password hashes and session state.

For offline password recovery, stop the service and run `lcc-ops recover-password` from a TTY. The
command verifies the schema and single-user invariant, creates and checks a pre-recovery backup,
changes the password, increments credential generation, revokes every session, and writes a safe
audit event. Start the service only after the command succeeds.

To restore, stop the service and run `lcc-ops restore --from /absolute/path/to/backup.sqlite3`.
The command validates integrity, foreign keys, migration revision, and the single-user invariant;
creates a pre-restore operational backup; installs the restored database atomically; and invalidates
every restored session before restart. Never restore a portable JSON package as a SQLite file.
