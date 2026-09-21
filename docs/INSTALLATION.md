# Ubuntu production installation

## Supported platform

The supported server baseline is **Ubuntu Server 24.04 LTS (amd64 or arm64)**. Installation
requires root access through `sudo`, a public DNS `A` and/or `AAAA` record for the LCC hostname,
inbound TCP ports 80 and 443, and outbound HTTPS for dependencies and Caddy certificate issuance.

Required software is Python 3.12+, `python3-venv`, pip, SQLite CLI, CA certificates, curl, Git,
tar, rsync, Node.js 22 LTS or 24 LTS with npm, and Caddy 2. Install the Ubuntu packages first:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip sqlite3 ca-certificates curl git rsync tar
```

Install Node.js and Caddy from their supported upstream channels. See
[Node.js downloads](https://nodejs.org/en/download) and
[Caddy's Debian/Ubuntu packages](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
The LCC installer validates prerequisites but does not add third-party package repositories.

## Stable quick install — recommended

Every stable release publishes three matching assets:

- `install.sh`, permanently bound to that release and archive digest;
- `learning-control-center-<version>.tar.gz`;
- `learning-control-center-<version>.tar.gz.sha256`.

For v1.0.1, after those assets have been published, run:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

The interactive installer asks only for:

1. the public DNS hostname;
2. the application timezone, defaulting to the detected server timezone or UTC;
3. confirmation of the non-secret installation summary.

It generates independent strong application and bootstrap secrets in a root-only temporary
directory, renders a complete production environment, downloads the bounded release archive and
checksum, requires the published checksum to match the digest embedded in `install.sh`, safely
extracts the archive, and invokes `scripts/install-ubuntu.sh`. The temporary secrets file is
removed after handoff. Caddy never receives the application environment.

To review before executing:

```bash
curl -fsSLo install.sh https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh
less install.sh
sudo bash install.sh
```

The release-bound launcher cannot switch channel, release, commit, or Git repository. An explicit
stable asset mirror may be selected with `--asset-base-url`; mirror archive and checksum bytes must
still agree with the embedded GitHub release digest.

## Development `main` install — unstable

Stable is always the default. To deliberately test the current public `main` branch:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.1/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- --channel main
```

The script fetches only `refs/heads/main` without tags, resolves `FETCH_HEAD` to one full commit
SHA, displays the repository and SHA, checks out that commit detached, and requires the operator to
type exactly `INSTALL MAIN`. The installed identity is `main-<full-sha>`; it does not auto-update
when the remote branch moves. This channel has HTTPS/Git transport trust but not a published
release checksum, so it is not equivalent to the stable trust path.

For automation, identities must remain explicit:

```bash
# Generic stable bootstrap
sudo scripts/bootstrap-ubuntu.sh \
  --channel stable --ref v1.0.1 \
  --domain lcc.example.com --timezone UTC --non-interactive

# Main, asserting the expected current remote tip
sudo scripts/bootstrap-ubuntu.sh \
  --channel main --commit 0123456789abcdef0123456789abcdef01234567 \
  --domain lcc.example.com --timezone UTC --non-interactive
```

Non-interactive main refuses to run without `--commit`, and fails if fetched public `main` differs.
Stable rejects `--commit`/`--repository-url`; main rejects `--ref`/`--asset-base-url`. Neither mode
accepts `latest` or another implicit moving identity. Secrets are never accepted on argv.

GitHub is the default source. Forgejo is an explicit, no-fallback alternative:

```bash
# Stable mirror assets (from a reviewed local install.sh)
sudo bash install.sh \
  --asset-base-url https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center/releases/download

# Main mirror
sudo scripts/bootstrap-ubuntu.sh --channel main \
  --repository-url https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center.git
```

Production source URLs must use HTTPS and cannot contain embedded credentials, query strings, or
fragments. There is no automatic failover between GitHub and Forgejo; mirror divergence is visible
in the recorded source repository/origin and exact SHA.

## Advanced production configuration

The normal installer creates `/etc/learning-control-center.env` automatically. Operators who need
custom settings may supply `--env-file /absolute/path`. That file must be a regular, non-symlink,
root-owned file with no group/other permissions (or the installed `root:lcc` `0640` file on a
matching repair). It is strictly parsed and must contain every required production value. The
installer never silently replaces an existing installed environment; replacement is a separate
advanced `install-ubuntu.sh --replace-env` operation.

The tracked `deploy/learning-control-center.env.example` documents all settings and systemd/Pydantic
list quoting. A complete standard file can also be rendered without placing secrets in arguments:

```bash
sudo install -d -m 0700 /run/lcc-config
sudo scripts/generate-production-env.sh \
  --domain lcc.example.com \
  --timezone UTC \
  --output /run/lcc-config/learning-control-center.env
```

## Canonical installer

`scripts/install-ubuntu.sh` is the only host-mutation layer. Acquisition scripts pass it the
verified source directory, production environment path, channel, release ID, full source revision,
repository, source ref, and source origin. It does not fetch releases or prompt humans.

For a manually reviewed stable source tree, the complete contract is:

```bash
sudo ./scripts/install-ubuntu.sh \
  --domain lcc.example.com \
  --channel stable \
  --release-id v1.0.1 \
  --source-revision FULL_40_CHARACTER_TAG_COMMIT_SHA \
  --source-repository https://github.com/Learning-Control-Center/Learning-Control-Center.git \
  --source-ref refs/tags/v1.0.1 \
  --source-origin https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download \
  --env-file /root/learning-control-center.env \
  --source "$PWD"
```

The installer validates Ubuntu and prerequisites; creates the non-login `lcc` account and private
data/backup directories; stages a per-identity release; installs exact Python constraints; runs
`npm ci` and a production build; installs hardened systemd and Caddy assets; activates atomically;
enables Caddy, LCC, and the backup timer; and verifies the public HTTPS health endpoint.

A first install is allowed when no active release exists. The same exact identity can be rerun for
repair. A partial matching `.installing` directory can be resumed safely. A different active
release is refused and must use the controlled update workflow. For inspection, use `--dry-run`.
The isolated `--root`/skip options are test-only and must not be used as production substitutes.

## Create the first user

After an interactive fresh install succeeds, the one-time bootstrap token is displayed only on the
controlling terminal, not stdout, stderr, argv, journald, or dry-run output. Open the displayed
HTTPS URL and create the first account. If installing non-interactively, retrieve the pending token
later from a root interactive terminal:

```bash
sudo lcc-admin show-bootstrap-token
```

Then immediately remove it and verify operation:

```bash
sudo lcc-admin finalize-bootstrap
sudo lcc-admin status
sudo lcc-admin health
sudo lcc-admin backup
sudo lcc-admin backup-status
```

`show-bootstrap-token` works only while no user exists. `finalize-bootstrap` is the canonical
workflow after first-user creation. If HTTPS certificate issuance fails, check DNS, ports 80/443,
and `sudo journalctl -u caddy.service -n 200 --no-pager`.

Continue with [`PRODUCTION_OPERATIONS.md`](PRODUCTION_OPERATIONS.md).
