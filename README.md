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

## Quick install or update

The supported production baseline is **Ubuntu Server 24.04 LTS** with internet access, `sudo`/root,
a DNS hostname pointing to the server, and inbound public ports 80/443 available. The canonical command
installs or updates from the latest validated public `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/Learning-Control-Center/main/scripts/bootstrap-ubuntu.sh | sudo bash
```

On a fresh host it asks for the public hostname, application timezone, and confirmation, then
uses a minimal stage zero to resolve `refs/heads/main` and re-run the bootstrap from that immutable
commit before APT or other host mutation. It displays the exact 40-character SHA, checks out and
verifies that commit, installs required packages, and generates production configuration privately.
Every installed identity is `main-<full-sha>`; it is never merely the moving branch name.
Missing OS prerequisites use an isolated, signed Ubuntu 24.04 APT view. Existing third-party
repositories remain enabled and untouched; LCC does not perform a system-wide upgrade.

Uvicorn remains private on IPv4 loopback. A fresh install uses internal port `8000` when available;
if it is occupied, the interactive installer reports the listener and asks for another port. For
automation, pass `--app-port PORT` to the bootstrap. Public HTTPS remains on Caddy ports 80/443.

Rerunning the command explicitly checks `main` again. The same SHA is a clean no-op; a changed SHA
is shown and confirmed before the canonical backup/migration/activation workflow runs. An existing
release-channel installation is changed to `main` only after explicit confirmation. LCC never
follows `main` automatically in the background.

Like every `curl | sudo bash` workflow, the initial stage-zero bytes trust HTTPS and the named
GitHub repository. Operators who do not want that trust model should use the download-and-review
procedure in the installation guide.

Node.js/npm are not installed on the server: supported `main` commits and release archives contain
a source-bound, verified production frontend. For review-first and manual alternatives, see the
complete [installation guide](docs/INSTALLATION.md).

## Update an installed server

Every v1.0.1-or-newer installation provides two equivalent entrypoints:

```bash
sudo /opt/learning-control-center/update.sh
# Thin administrator alias for the same updater:
sudo lcc-admin update
```

Both commands use the recorded repository, resolve its public `main` to one exact SHA, and delegate
to the same safe update engine. A release-channel installation requires confirmation before its
one-time migration to `main`. See [updates, rollback, and uninstall](docs/UPDATES.md) for the
database-aware update and rollback contract.

Inspect or safely change the private loopback port without changing the public URL:

```bash
sudo lcc-admin app-port
sudo lcc-admin app-port set 8123
```

## Pinned releases

Git tags and Releases are immutable version snapshots for release history, reproducible archive
distribution, rollback/reference, and intentionally pinned deployments. They are not the normal
installation or update-discovery channel.

To install or update to one exact release, use its versioned launcher:

```bash
curl -fsSL https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download/v1.0.1/install.sh | sudo bash
```

That launcher remains permanently bound to v1.0.1: it installs or updates older stable versions,
does nothing when v1.0.1 is active, and refuses to downgrade a newer stable installation.
It remains on the release channel until the operator deliberately runs the main-first installer or
updater and confirms migration. Release archives retain their bounded-download, SHA-256, safe
extraction, and embedded-digest protections.

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
