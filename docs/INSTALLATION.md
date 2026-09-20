# Ubuntu production installation

## Supported platform

The supported server baseline is **Ubuntu Server 24.04 LTS (amd64 or arm64)**. It supplies Python
3.12 as the system Python and systemd versions that support the shipped sandboxing directives.

Required privileges and infrastructure:

- root access through `sudo` for installation and service management;
- a DNS `A` and/or `AAAA` record for the LCC hostname pointing to the server;
- inbound TCP ports 80 and 443 allowed by the provider firewall and host firewall;
- outbound HTTPS during source/dependency installation and Caddy certificate issuance;
- Python 3.12+, `python3-venv`, pip, SQLite CLI, CA certificates, curl, Git, tar, and rsync;
- Node.js 22 LTS or 24 LTS and npm for the source frontend build;
- Caddy 2 as the HTTPS/static-file service.

Install the base Ubuntu packages:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip sqlite3 ca-certificates curl git rsync tar
```

Install Node.js 22 LTS or 24 LTS and Caddy 2 from their upstream installation channels.
Use the current instructions from [Node.js downloads](https://nodejs.org/en/download) and
[Caddy's Debian/Ubuntu packages](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
The installer checks their versions and refuses to proceed when a prerequisite is missing; it does
not add third-party package repositories on the operator's behalf.

Verify prerequisites:

```bash
python3 --version
node --version
npm --version
caddy version
sqlite3 --version
```

## Prepare production configuration

Obtain a deliberate LCC source release or clean Git checkout. Do not deploy a working tree with
unreviewed changes. From that source directory:

```bash
cp deploy/learning-control-center.env.example /root/learning-control-center.env
sudo chmod 0600 /root/learning-control-center.env
```

Edit the file and replace every `CHANGE_ME` value. Keep the standard paths unless the systemd units
and installer are intentionally changed together. Generate independent secrets, for example:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Use one value for `LCC_BOOTSTRAP_TOKEN` and a different value for `LCC_SECURITY_SECRET`. The JSON
array values are enclosed in single quotes intentionally; this preserves their inner JSON quotes
when systemd reads the file. The public origin and allowed host must match the DNS hostname exactly.
Caddy does not read this secret-bearing environment file.

## Install

Choose a stable release identity such as a signed tag or the full Git commit SHA:

```bash
sudo ./scripts/install-ubuntu.sh \
  --domain lcc.example.com \
  --release-id v1.0.0 \
  --env-file /root/learning-control-center.env \
  --source "$PWD"
```

After installation succeeds, delete the temporary source copy; the active configuration is already
installed at `/etc/learning-control-center.env`:

```bash
sudo rm -f /root/learning-control-center.env
```

The installer is safe to rerun with the same release identity and source revision. It:

1. validates Ubuntu and every prerequisite;
2. validates the production environment without evaluating it as shell code;
3. creates the non-login `lcc` account and restrictive persistent directories;
4. copies an immutable source release under `/opt/learning-control-center/releases`;
5. creates a per-release virtual environment and installs the project editable against exact
   production and build-tool constraints without an unconstrained isolated build environment,
   intentionally preserving Alembic's source-release asset layout;
6. runs `npm ci` and the frontend production build;
7. installs the environment, hardened systemd units, and a literal host-specific Caddy site;
8. activates the release with an atomic `current` symlink;
9. validates Caddy, reloads systemd, enables Caddy/LCC/the backup timer, starts LCC, verifies the
   public HTTPS health endpoint, and only then starts the backup timer.

The Caddy site contains only the public hostname, static build path, proxy target, and security
headers. It never receives bootstrap or application secrets.

For a Git source, release content is created from committed `HEAD` with `git archive`; ignored
working files are never copied. A non-Git release artifact is copied through a restrictive filter
that excludes environment files, private keys, databases, caches, build output, and local state;
its sanitized content digest is recorded as the source revision.

For a non-mutating preview, add `--dry-run`. Deployment tests use `--root` with disposable paths;
these testing options are not substitutes for a real production install.

## Bootstrap and verify

Open `https://lcc.example.com`, enter the configured bootstrap token, and create the first user.
Then remove the one-time secret and verify restart:

```bash
sudo lcc-admin finalize-bootstrap
sudo lcc-admin status
sudo lcc-admin health
sudo lcc-admin backup
sudo lcc-admin backup-status
```

If Caddy cannot obtain a certificate, verify public DNS, inbound ports 80/443, and Caddy logs:

```bash
sudo journalctl -u caddy.service -n 200 --no-pager
```

Continue with [`PRODUCTION_OPERATIONS.md`](PRODUCTION_OPERATIONS.md).
