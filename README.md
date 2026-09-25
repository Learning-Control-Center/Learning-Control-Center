<p align="center">
  <img src="logo.png" alt="Learning Control Center logo" width="180">
</p>

<h1 align="center">Learning Control Center</h1>

<p align="center">
  A private, single-user system for turning learning goals into deliberate work, evidence, and explainable progress.
</p>

Learning Control Center (LCC) is a self-hosted web application for people who want more structure
than a checklist or generic habit tracker. It connects competency targets, learning material,
projects, actual work, verification, deterministic analysis, and daily recommendations without
requiring an external AI service.

## Project status

LCC 1.0 is prepared for self-hosted server testing on Ubuntu Server 24.04 LTS. It is intended for
one operator and one learner, normally the same person. There is no public registration or
multi-user mode.

The core application, import/export safeguards, operational backup and restore flows, and Ubuntu
deployment tooling are implemented. Automated browser coverage includes Chromium, Firefox, and
Playwright WebKit. Real Windows, macOS, Android, iOS/iPadOS, and screen-reader rows that have not
been exercised remain explicitly `Not executed`; see the [product QA record](docs/PRODUCT_QA.md).

## What it does

- Models target profiles, competencies, capability criteria, prerequisites, and review state.
- Presents a navigable Roadmap with synchronized visual Map and accessible Outline views.
- Connects Curriculum and Projects to the competencies they can develop or demonstrate.
- Records Activities and exact-duration Sessions, including refresh-safe timers.
- Preserves Evidence, verification, capability history, and correction lineage.
- Produces deterministic Analysis, Recommendations, Today suggestions, and historical reports.
- Explains why recommendations were selected and keeps advisory choices separate from actual work.
- Supports validated import previews, transactional apply, portable export, operational backup,
  restore, and password recovery.
- Keeps the deterministic core fully functional without an LLM or cloud service.

For an unassessed learner, Recommendation V2 permits assessment-first work. When eligible
assessments have equal scores and existing urgency/activity rank facts, policy registry v4 orders
them by five direct structural signals: active Curriculum units with exactly one unresolved hard
criterion requirement that the assessment explicitly covers, active units with any matching
unresolved hard criterion requirement, Unknown active targets downstream through hard Graph
prerequisites, through `recommended_before`, then through `supports`. Criterion coverage comes
only from explicit rubric criterion references compatible with the target; capability requirements
do not contribute to these Curriculum counts. Equal-priority assessment targets use their Profile
target stable keys for selection. Each unit or target counts once per signal. These facts and the
portable semantic final tie key appear in the run's `ASSESSMENT_STRUCTURAL_ORDER` rationale. They
advise ordering; they do not satisfy requirements or establish capability. Earlier runs retain
their recorded policy registry and replay semantics.

## Quick install or update

On **Ubuntu Server 24.04 LTS (amd64)** with systemd, root access, and internet access, run:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh | sudo bash
```

This resolves public GitHub `main` once to a full commit SHA, downloads the bootstrap and source
archive at that exact SHA over HTTPS, validates/extracts the source, and installs LCC. It installs
only missing named prerequisites through the host's configured APT policy. Third-party repositories
are left untouched; there is no system-wide upgrade, Git requirement, or Node.js requirement on
the production host. The source identity is recorded in the immutable release.

The application serves its frontend and API from one loopback origin. The default gateway is
managed Caddy, which needs a DNS hostname pointing at this host and ports 80/443 available. Choose
`--gateway external` for an operator-managed same-host HTTPS gateway, including Cloudflare Tunnel:
forward the entire site to `http://127.0.0.1:<LCC_APP_PORT>`. External installation can finish
without a public origin; set it later with `sudo lcc-admin public-origin set https://lcc.example.com`.
The backend binds only to `127.0.0.1`, on port 8000 by default. An occupied default port prompts
for another; automation supplies `--app-port PORT` explicitly.

For non-interactive installation, pass arguments to Bash after `-s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap.sh |
  sudo bash -s -- --non-interactive --domain lcc.example.com --gateway caddy
```

Rerunning the command at the same SHA is a no-op. A changed SHA is confirmed, staged, backed up,
migration-checked, activated, health-checked, and rolled back on failure. There is no background
update. Existing V1 installations migrate through this **new** bootstrap, avoiding the old
installed updater's prerequisite path. The old `*-ubuntu.sh` names are compatibility entry points.
See the [installation guide](docs/INSTALLATION.md) for the gateway, port, migration, and review
paths. Published Installer V2 has passed real-server GitHub acquisition, host APT, Core/systemd,
internal health, external API reachability, and app-port checks. This frontend/origin fix still
awaits sanitized publication and changed-SHA real-server acceptance; public ACME remains untested.

## Update an installed server

```bash
sudo /opt/learning-control-center/update.sh
# Equivalent administrator entry point:
sudo lcc-admin update
```

Both resolve public GitHub `main` to an exact SHA and use the same deployment transition. See
[updates, rollback, and uninstall](docs/UPDATES.md) for recovery details. Inspect or change the
private loopback port with `sudo lcc-admin app-port` and `sudo lcc-admin app-port set 8123`.

## Pinned releases

Tags and Releases are optional immutable snapshots, not normal install/update dependencies.
Historical `v1.0.0` assets remain immutable. Future version-bound release installers support a
fresh pinned installation from their embedded version, commit and archive digest; existing
installations update through the public-main path. See [releasing](docs/RELEASING.md).

Production administration uses systemd and `lcc-admin`:

- [Installation](docs/INSTALLATION.md)
- [Production operations](docs/PRODUCTION_OPERATIONS.md)
- [Updates, rollback, and uninstall](docs/UPDATES.md)
- [Public release process](docs/RELEASING.md)
- [Import/export format](docs/IMPORT_EXPORT_FORMAT.md)
- [Security policy](SECURITY.md)

## Architecture and data ownership

LCC is a modular monolith:

- React 19, TypeScript, Vite, and Tailwind CSS provide the browser application.
- FastAPI, Pydantic, SQLAlchemy 2, and Alembic provide the server and migration boundary.
- SQLite is the single source of truth for the single-user installation.
- One Uvicorn worker serves the verified frontend and API; managed Caddy or an operator-owned
  same-host gateway forwards the complete site over HTTPS.
- systemd owns the application process and scheduled operational backups.

Canonical facts and immutable history are persisted. Capability, availability, Roadmap, Analysis,
and Today projections are deterministic and repairable from those facts. Operational SQLite
backups are separate from portable logical exports.

## Security and privacy

LCC has no public registration. Initial account creation requires a one-time operator bootstrap
secret, passwords use Argon2id, sessions are server-side, mutations are CSRF-protected, and
production configuration requires HTTPS. The database and backup directories are not served by
Caddy.

Portable logical backups intentionally exclude passwords, live sessions, and application secrets.
Production operators remain responsible for server patching, DNS, firewall policy, certificate
operation, and protecting `/etc/learning-control-center.env`, the SQLite database, and backups.

Report security issues privately according to [SECURITY.md](SECURITY.md).

## Development

Prerequisites are Python 3.12 or newer and Node.js 22 LTS or 24 LTS.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The root `start.sh` and `stop.sh` helpers are development-only; systemd
is the production process manager.

## Validation

Backend checks:

```bash
.venv/bin/ruff format --check backend scripts
.venv/bin/ruff check backend scripts
.venv/bin/mypy backend/app
.venv/bin/pytest
```

Frontend checks:

```bash
cd frontend
npm run lint
npm run test
npm run build
```

The [product QA guide](docs/PRODUCT_QA.md) documents the disposable fixture, automated browser
evidence, completed visual review, and outstanding real-platform rows. Playwright WebKit is not
Safari certification, and Chromium emulation is not Android certification.

## License

Learning Control Center is licensed under the [GNU General Public License v3.0 only](LICENSE)
(`GPL-3.0-only`).

Copyright (C) 2026 WaqSea.
