# Ubuntu production installation

## Platform and prerequisites

Installer V2 initially targets Ubuntu Server 24.04 LTS on amd64 with active systemd. The Ubuntu
24.04 arm64 implementation exists but real-system certification is pending. Debian and other Linux
distributions are not certified. You need root access, internet access, and enough disk space for
an immutable release and backups. A managed Caddy installation also needs a DNS hostname pointing
at this host, public inbound TCP ports 80/443, and working certificate issuance. An explicitly
selected external gateway needs an operator-managed **same-host** HTTPS gateway instead. Cloudflare
Tunnel can forward to the loopback HTTP origin without a local nginx or Caddy layer.

Before any package or service change, stage zero resolves the public GitHub `refs/heads/main` to a
full lowercase SHA and downloads the pinned `scripts/bootstrap.sh` and a SHA-addressed archive over
HTTPS. Archive size, layout, member types/modes, and paths are validated in a private temporary
directory. All later source requests use that selected SHA. Git is not required to install/update;
this relies on GitHub as the source host and does not provide independent signed-source proof.

If prerequisites are missing, LCC runs normal host APT refresh and a named-package install. APT
owns repository authentication, pinning, candidate choice, and dependency resolution. LCC does
not inspect `indextargets`, compare `SIGNED_BY` strings, isolate official sources, edit repositories
or keys, or run an OS upgrade. An incomplete refresh is displayed; installation may proceed with
available authenticated metadata, but actual package installation and capability checks must pass.
Core packages include `ca-certificates`, `curl`, `python3`, `python3-venv`, `sqlite3`, `rsync`, `tar`,
`gzip`, `iproute2`, and `util-linux` where missing. Caddy is requested separately in managed mode.
Node.js/npm are build-time tools; the production frontend is bundled and verified.

## Install from public main

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh | sudo bash
```

Managed Caddy is the default gateway. Interactive fresh installation selects a gateway before
asking for a hostname; only managed Caddy requires one. The installer may also ask for an alternate
internal port if 8000 is occupied. For managed automation:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh |
  sudo bash -s -- --non-interactive --domain lcc.example.com --timezone UTC --gateway caddy
```

For an external gateway, a non-interactive installation can omit the public origin:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh |
  sudo bash -s -- --non-interactive --gateway external --app-port 8123
sudo lcc-admin public-origin set https://lcc.example.com
```

If the HTTPS origin is already known, supply `--public-origin https://lcc.example.com` during the
external install. The existing `--domain lcc.example.com` form remains supported. Use
`--app-port 8123` when another service occupies 8000. An explicit occupied port fails without
stopping its owner. `--commit FULL_SHA` asserts that the resolved public main tip equals that SHA;
it does not install an older commit. `--non-interactive` requires a supplied decision whenever
prompting would otherwise be necessary. For an existing reverse proxy, choose `--gateway external`.
The old `bootstrap-ubuntu.sh` name forwards to the generic bootstrap for compatibility; use the
new command for V1 migration. Forgejo is private development infrastructure and is not an
installer source. No automatic source-provider fallback exists.

For review before execution:

```bash
curl -fsSLo bootstrap.sh https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh
less bootstrap.sh
sudo bash bootstrap.sh
```

A piped launcher and a reviewed launcher use the same GitHub source trust boundary. The installed
release is `main-<full-sha>` with recorded repository/ref/revision, a locked Python environment,
verified frontend, dedicated unprivileged `lcc` account, root-owned configuration, persistent data
and backups, systemd application/backup timer, and `lcc-admin`. The backend binds only to
`127.0.0.1:<LCC_APP_PORT>` and serves both frontend and API there; it never listens on a public
interface. The production environment is
`/etc/learning-control-center.env`; only `LCC_APP_PORT` and application settings/secrets belong
there. The root-owned `/etc/learning-control-center.deployment` records the gateway mode.

## Public gateway modes

In default `--gateway caddy`, LCC installs Caddy if missing, reuses a compatible packaged Caddy,
manages only its own site/import, validates the complete configuration, and requires public HTTPS
health before reporting success. A different service on ports 80/443 does not block Core, but an
explicit managed Caddy choice fails with a clear partial-state result if ownership is unsafe. The
interactive installer may offer external mode; non-interactive use must select it explicitly.
After fixing a transient managed-gateway problem, rerun the same bootstrap at the selected SHA;
it can finish the missing gateway step without restaging the healthy Core.

With `--gateway external`, Core can finish when its internal health passes, even if the public
origin is not configured. Until `lcc-admin public-origin set` supplies an HTTPS origin, only the
loopback Host is accepted and public/browser readiness remains pending. LCC does not edit, reload,
or restart nginx/Apache/cloudflared/another proxy. The installer reports the same-host upstream;
[`deploy/examples/installer-v2-external-nginx.conf`](../deploy/examples/installer-v2-external-nginx.conf)
is an nginx example. Forward the **entire** HTTP request stream, preserving paths and the public
Host. The gateway needs no static root, `/api` split, or SPA fallback. Never expose the environment
file. Check public readiness afterward with `sudo lcc-admin health --public`.

For an operator-managed Cloudflare Tunnel published application, set the public hostname to
`lcc.example.com`, the service to `http://127.0.0.1:<LCC_APP_PORT>`, and no path restriction. Set
the LCC public origin to `https://lcc.example.com`. Local origin TLS is unnecessary because
cloudflared connects to loopback HTTP; the browser-facing route still requires HTTPS. Preserve
`Host: lcc.example.com`. If a tunnel Host override is configured, set its HTTP Host Header to that
public hostname rather than `127.0.0.1`. LCC neither installs cloudflared nor manages the tunnel.
Cloudflare documents the [local service route](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/)
and [HTTP Host Header override](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/).

Internal port 8000 is used when free. The effective `LCC_APP_PORT` persists through update/reboot.
Inspect with `sudo lcc-admin app-port`; change it transactionally with
`sudo lcc-admin app-port set PORT`. In managed mode the LCC-owned Caddy site changes with the port.
In external mode LCC cannot edit your proxy: acknowledge coordination (automation uses
`--yes --ack-external-proxy`), update its upstream yourself, then verify public health.
`sudo lcc-admin public-origin` shows the configured HTTPS origin or pending state.
`sudo lcc-admin public-origin set https://lcc.example.com --dry-run` validates a proposed change;
omit `--dry-run` to apply it with confirmation, or use `--yes` for deliberate automation. External
mode restarts only LCC and may finish with public gateway readiness pending. Managed mode changes
the LCC-owned Caddy hostname in the same health-checked transaction.

## Existing V1 installation

Run the **new** `bootstrap.sh` command above. Do not run the old installed updater first: it may
reach the superseded V1 APT path. V2 inspects existing source/units/Caddy ownership, preserves
secrets, data, backups, app port, and bootstrap state, stages and backs up before activation, and
migrates the deployment mechanics. Ambiguous Caddy ownership fails without taking over the proxy.
The old immutable release remains available for controlled rollback, subject to database-schema
and internal-port compatibility. See [updates](UPDATES.md).

## Pinned release snapshot

Historical published releases keep their original assets. A future V2 release may publish a
version-bound `install.sh` alongside its deterministic archive and SHA-256 file. The launcher
embeds its exact version, full commit SHA, and archive checksum, validates the downloaded archive,
and supports a **fresh** pinned installation. It rejects source/channel overrides and does not
look up a moving `main` or latest release. Existing installations update through public main;
release assets are optional snapshots, not normal update discovery. No future release is claimed
published merely because packaging is implemented.

## After installation

On managed Caddy, visit the HTTPS URL and create the first user using the one-time bootstrap token.
Retrieve it from a root terminal with `sudo lcc-admin show-bootstrap-token`; after account
creation run `sudo lcc-admin finalize-bootstrap` to remove the consumed root-owned token. A
restart before cleanup remains safe: the existing user makes the token unusable for another
bootstrap. In external mode finish HTTPS routing first.
Check `sudo lcc-admin status`, `sudo lcc-admin health --internal`, and, once integrated,
`sudo lcc-admin health --public`. See [production operations](PRODUCTION_OPERATIONS.md) for
backups, restore, and recovery.
