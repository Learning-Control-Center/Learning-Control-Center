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

## Quick self-hosting

The supported production baseline is **Ubuntu Server 24.04 LTS** with a DNS hostname pointing to
the server and inbound ports 80/443 available. Published releases provide a versioned archive and
SHA-256 checksum. Production installs never default to `main` or an unspecified latest build.

Prepare the production environment file first; it contains secrets and must remain root-readable:

```bash
sudo curl -fsSLo /root/learning-control-center.env \
  https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.0/deploy/learning-control-center.env.example
sudo chmod 0600 /root/learning-control-center.env
sudo editor /root/learning-control-center.env
```

After replacing every `CHANGE_ME` value, run the release-pinned bootstrap:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/v1.0.0/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- \
      --ref v1.0.0 \
      --domain lcc.example.com \
      --env-file /root/learning-control-center.env
```

The bootstrap downloads and verifies the matching GitHub release archive, then delegates all host
provisioning to the canonical installer. Do not use this command until the referenced GitHub tag
and release assets have been published. Review the complete [installation guide](docs/INSTALLATION.md)
for prerequisites, DNS, firewall, configuration, bootstrap finalization, and the manual source
installation path.

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
