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

## Install or update — stable

The supported production baseline is **Ubuntu Server 24.04 LTS** with internet access, `sudo`/root,
a DNS hostname pointing to the server, and inbound ports 80/443 available. The recommended
stable entrypoint always redirects to the newest final release's checksum-bound installer:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/latest/download/install.sh | sudo bash
```

On a fresh host it asks for the public hostname, application timezone, and confirmation, then
installs the required Ubuntu packages, generates production configuration privately, and verifies
the matching release archive. Rerunning the same command updates an older stable installation
through the canonical backup/migration/activation workflow. The same version is a clean no-op;
prereleases are never selected; and an older pinned installer cannot silently downgrade a newer
installation. This command becomes available when the `v1.0.1` assets are published.

Node.js/npm are not installed on the server: every release contains a source-bound, verified
production frontend built during release packaging. For review-first and manual alternatives, see
the complete [installation guide](docs/INSTALLATION.md).

## Update an installed server

Every v1.0.1-or-newer installation provides two equivalent entrypoints:

```bash
sudo /opt/learning-control-center/update.sh
# Thin administrator alias for the same updater:
sudo lcc-admin update
```

Stable installations resolve the newest final stable release. Main installations resolve the
recorded repository's current public `main` to one exact SHA. Neither command changes channel by
default. See [updates, rollback, and uninstall](docs/UPDATES.md) for explicit channel changes and
database-aware rollback.

To install or update to one exact release, use its versioned launcher:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

That launcher remains permanently bound to v1.0.1: it installs or updates older stable versions,
does nothing when v1.0.1 is active, and refuses to downgrade a newer stable installation.

## Current `main` — latest validated code

After v1.0.1 is published, operators who deliberately prefer the latest validated public code over
a versioned release can use its reviewed bootstrap implementation:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.1/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- --channel main
```

The installer displays the repository and exact 40-character SHA and asks for normal confirmation.
It checks out that SHA detached, records it, and never follows the moving branch automatically.
Stable remains the recommended production path because it is immutable, versioned,
checksum-bound, and easier to reproduce and roll back; `main` is validated current code but is not
release-pinned.

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
- Caddy serves the production frontend and proxies `/api` to one Uvicorn worker.
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
