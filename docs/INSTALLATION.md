# Ubuntu production installation

## Supported platform

The supported server baseline is **Ubuntu Server 24.04 LTS (amd64 or arm64)**. The operator supplies
only root access through `sudo`, working internet access, a public DNS `A` and/or `AAAA` record for
the LCC hostname, and available inbound public TCP ports 80 and 443. The internal loopback
application port is separate and defaults to 8000. No separate application-prerequisite
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

Node.js/npm are build dependencies, not production-host dependencies. Public `main` commits and
stable archives carry a verified production frontend whose hash is bound to its build inputs.
Release creation rebuilds it with `npm ci` from `package-lock.json` and rejects a mismatch. The
server installs and updates that exact artifact without Node.js.

## Main-first install or update — canonical

The normal command is:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap-ubuntu.sh | sudo bash
```

For the piped command, a minimal stage zero first queries the selected host's public main-ref API,
resolves one full commit SHA, and downloads and re-runs `bootstrap-ubuntu.sh` from that immutable
commit URL before APT or other host mutation. The pinned bootstrap then fetches only
`refs/heads/main` without tags, requires the fetched tip to equal the stage-zero SHA, displays that
SHA, and asks `Continue? [y/N]`. It checks out that commit detached and verifies the final `HEAD`
and clean source tree before candidate installation code is used. The installed identity is
`main-<full-sha>` with
`RELEASE_CHANNEL=main`, `SOURCE_REF=refs/heads/main`, the selected source repository, and the full
`SOURCE_REVISION`; the moving branch name is never used as the immutable installed identity.

Rerunning the same command explicitly checks validated `main` again. The same SHA reports
`Learning Control Center is already up to date.` and exits successfully. A changed SHA is shown
alongside the installed SHA, confirmed, and delegated to the canonical updater. A historical
release-channel installation shows its current channel/release and target main SHA, then requires
explicit confirmation before migration. Main never advances in the background.

When internal port 8000 is free, the interactive installer asks only for:

1. the public DNS hostname;
2. the application timezone, defaulting to the detected server timezone or UTC;
3. confirmation of the non-secret installation summary.

The summary includes `127.0.0.1:<internal-port>`. If 8000 is occupied, the installer safely reports
the listener address and process/PID when available, then reads an alternate port from the
controlling terminal. This works with the canonical `curl | sudo bash` pipeline because prompts use
`/dev/tty`, not the script-input pipe. The suggested alternate is never selected silently. An
explicitly requested occupied port fails, and the installer never stops the conflicting service.

It reports missing packages before installing them, generates independent strong application and
bootstrap secrets in a root-only temporary directory, renders a complete production environment,
and invokes `scripts/install-ubuntu.sh` with the verified immutable source identity. The temporary
secrets file is removed after handoff. Caddy never receives the application environment.

To review before executing:

```bash
curl -fsSLo bootstrap-ubuntu.sh https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap-ubuntu.sh
less bootstrap-ubuntu.sh
sudo bash bootstrap-ubuntu.sh
```

The one-line command necessarily trusts the HTTPS-delivered stage-zero script and the selected
public repository. Download-and-review makes that initial trust decision explicit. Both paths pin
the installed source to a full SHA and verify it again during Git acquisition.

## Pinned release snapshots

Every release may publish three matching assets:

- `install.sh`, permanently bound to that release and archive digest;
- `learning-control-center-<version>.tar.gz`;
- `learning-control-center-<version>.tar.gz.sha256`.

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
the embedded GitHub release digest. A pinned installation remains on the release channel until the
operator explicitly confirms migration to `main`. Release assets remain the stronger choice when
an immutable, versioned, checksum-published snapshot is specifically required; they are not the
normal update-discovery mechanism.

For automation, identities must remain explicit:

```bash
# Canonical piped main bootstrap; stage zero resolves and pins the exact main SHA
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap-ubuntu.sh |
  sudo bash -s -- --non-interactive --domain lcc.example.com --app-port 8123

# Explicit pinned release bootstrap
sudo scripts/bootstrap-ubuntu.sh \
  --channel stable --ref v1.0.1 \
  --domain lcc.example.com --timezone UTC --non-interactive

# Canonical main, asserting the expected current remote tip
sudo scripts/bootstrap-ubuntu.sh \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --domain lcc.example.com --timezone UTC --app-port 8123 --non-interactive
```

Direct non-interactive main execution refuses to run without `--commit`, and fails if fetched
public `main` differs. The canonical piped command does not require a caller-supplied commit because
its minimal stage zero resolves `main`, downloads the bootstrap from that exact SHA, and passes the
pinned identity to the re-executed script before host mutation.
If the default internal port is occupied, non-interactive installation fails with instructions to
provide `--app-port PORT`.
Stable rejects `--commit`/`--repository-url`; main rejects `--ref`/`--asset-base-url`. Neither mode
accepts a moving identity as the installed identity. Secrets are never accepted on argv.

GitHub is the default source. Forgejo is an explicit, no-fallback alternative:

```bash
# Stable mirror assets (from a reviewed local install.sh)
sudo bash install.sh \
  --asset-base-url https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center/releases/download

# Main mirror
sudo scripts/bootstrap-ubuntu.sh \
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

`LCC_APP_PORT` in that file is the authoritative internal port. It accepts canonical decimal values
from 1024 through 65535; the bind address remains fixed at `127.0.0.1`. `--env-file` and
`--app-port` cannot be combined. Existing files without the key retain the legacy default 8000.

The tracked `deploy/learning-control-center.env.example` documents all settings and systemd/Pydantic
list quoting. A complete standard file can also be rendered without placing secrets in arguments:

```bash
sudo install -d -m 0700 /run/lcc-config
sudo scripts/generate-production-env.sh \
  --domain lcc.example.com \
  --timezone UTC \
  --app-port 8123 \
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

A first install is allowed when no active deployment exists. Rerunning the main-first bootstrap
delegates a changed exact SHA to the controlled update engine, while the same SHA is a no-op.
Rerunning a release-bound launcher retains its exact-version update/no-op/downgrade-refusal
behavior. A partial matching `.installing` directory can be resumed safely. Channel changes are
displayed with both exact identities and require explicit confirmation. For inspection, use
`--dry-run`.
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
