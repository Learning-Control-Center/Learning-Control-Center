# Ubuntu production installation

## Supported platform

The supported server baseline is **Ubuntu Server 24.04 LTS (amd64 or arm64)**. The operator supplies
only root access through `sudo`, working internet access, a public DNS `A` and/or `AAAA` record for
the LCC hostname, and available inbound TCP ports 80 and 443. No separate application-prerequisite
installation step is required on a normal Ubuntu Server image.

The bootstrap checks the OS, architecture, free space, APT/dpkg state, conflicting listeners, and
existing Caddy ownership. It then installs only missing packages from Ubuntu 24.04's signed
repositories: `ca-certificates`, `curl`, `python3`, `python3-venv`, `sqlite3`, `rsync`, `tar`,
`gzip`, `git`, `caddy`, and `iproute2`. Git is installed for both channels so an operator can later
request an exact-SHA `main` update or channel change without a separate prerequisite step. Caddy comes from
Ubuntu's `universe` component. The installer adds no third-party APT repository, imports no external
signing key, does not use `apt-key`, and never upgrades the whole operating system. When packages
are missing, provisioning refuses to continue if any enabled package index is not an Ubuntu 24.04
(Noble) index; temporarily disable third-party APT sources and rerun. This conservative check keeps
candidate selection inside Ubuntu's signed repositories. Caddy must resolve to the package-owned
`/usr/bin/caddy`; a shadowing or unmanaged executable is rejected. The source check also requires
each package index to use the `ubuntu-keyring`-owned
`/usr/share/keyrings/ubuntu-archive-keyring.gpg`, rather than trusting repository labels alone.

Node.js/npm are release-build dependencies, not production-host dependencies. Stable archives and
public `main` commits carry a verified production frontend whose hash is bound to its build inputs.
Release creation rebuilds it with `npm ci` from `package-lock.json` and rejects a mismatch. The
server installs and updates that exact artifact without Node.js.

## Stable install or update — recommended

Every stable release publishes three matching assets:

- `install.sh`, permanently bound to that release and archive digest;
- `learning-control-center-<version>.tar.gz`;
- `learning-control-center-<version>.tar.gz.sha256`.

After v1.0.1 has been published, the normal command is:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/latest/download/install.sh | sudo bash
```

GitHub's `latest` release redirect excludes drafts and prereleases and resolves to a version-bound
`install.sh`. On a fresh server it installs that release. On an older stable installation it
delegates the immutable target to the canonical updater. The same release reports
`Learning Control Center is already up to date.` and exits successfully. If the installed stable
release is newer, the launcher refuses to downgrade and directs the operator to rollback.

The interactive installer asks only for:

1. the public DNS hostname;
2. the application timezone, defaulting to the detected server timezone or UTC;
3. confirmation of the non-secret installation summary.

It reports missing packages before installing them, generates independent strong application and
bootstrap secrets in a root-only temporary directory, renders a complete production environment,
downloads the bounded release archive and checksum, requires the published checksum to match the
digest embedded in `install.sh`, safely extracts the archive, and invokes
`scripts/install-ubuntu.sh`. The temporary secrets file is removed after handoff. Caddy never
receives the application environment.

To review before executing:

```bash
curl -fsSLo install.sh https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh
less install.sh
sudo bash install.sh
```

For one exact version, use the version-specific URL instead:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

A pinned launcher never resolves another version. It installs on a fresh host, updates an older
stable installation to its embedded release, no-ops on the same release, and refuses a downgrade.

The release-bound launcher cannot be redirected to another target channel, release, or commit and
does not accept a Git-repository override. Switching an existing main installation to its bound
stable release still requires explicit channel-change confirmation. The explicit supported Forgejo
mirror may be selected with `--asset-base-url`; its archive and checksum bytes must still agree with
the embedded GitHub release digest, and that mirror becomes the recorded source for future updates.

## Current `main` — latest validated code

Stable is always the default and remains the recommended reproducible production path. To
deliberately install the current validated public `main` branch after v1.0.1 is published:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.1/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- --channel main
```

The script fetches only `refs/heads/main` without tags, resolves `FETCH_HEAD` to one full commit
SHA, displays the repository and SHA, asks `Continue? [y/N]`, and checks out that commit detached.
The installed identity is `main-<full-sha>`; it does not auto-update when the remote branch moves.
Main is validated current code, not an unfinished branch, but it lacks the immutable versioned
archive and published digest of stable and is therefore less reproducible.

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
accepts a moving identity as the installed identity. Secrets are never accepted on argv.

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

`scripts/bootstrap-ubuntu.sh` owns human interaction, acquisition verification, and the minimum
Ubuntu package provisioning needed before verified source can run. `scripts/install-ubuntu.sh` is
the canonical non-interactive LCC host-mutation layer. Bootstrap passes it the verified source
directory, production environment path, channel, release ID, full source revision, repository,
source ref, and source origin. The canonical installer does not fetch releases, prompt humans, or
modify APT state.

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

The installer validates Ubuntu and the provisioned runtime; creates the non-login `lcc` account and
private data/backup directories; verifies and stages a per-identity release; installs exact Python
constraints into an isolated virtual environment; uses the packaged frontend; installs hardened
systemd and Caddy assets; validates Caddy before activation; activates atomically; enables Caddy,
LCC, and the backup timer; and verifies the public HTTPS health endpoint. Existing administrator
Caddy configuration is preserved; if the LCC site conflicts, validation fails and the previous
Caddy files are restored.

A first install is allowed when no active release exists. Rerunning a release-bound launcher with
an older installation delegates to the controlled update engine; the same identity is a no-op;
and a newer stable identity refuses downgrade. A partial matching `.installing` directory can be
resumed safely. Channel changes are displayed with both exact identities and require explicit
confirmation. For inspection, use `--dry-run`.
The isolated `--root`/skip options are test-only and must not be used as production substitutes.
Dry-run reports missing packages, the exact APT plan and trust source, release/channel/domain,
frontend-artifact policy, and intended host/service actions without changing packages,
repositories, services, production configuration, or secrets.

Failures name the active phase (OS preflight, package metadata, prerequisite install, acquisition,
or host installation). Successfully installed shared Ubuntu packages are intentionally retained if
a later phase fails; correct the reported issue and rerun. The package step and exact-identity
installer are idempotent.

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
