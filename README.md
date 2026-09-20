# Learning-Control-Center

Learning-Control-Center is a single-user, self-hosted system for competency-based roadmaps, daily learning recommendations, session tracking, verification, deterministic analytics, reports, and safe data transfer.

## V2 product

The application uses canonical V2 authority for versioned Target Profiles and capability semantics, Activities and exact-duration Sessions, immutable Evidence, Curriculum, Projects, the native Learning Graph, Roadmap Projection, Analysis V3, Recommendation V2, and Today V2. Historical V1 records remain immutable and readable in explicitly labeled compatibility views.

The Phase 3 frontend organizes the product around Today, Activity, Profile, Roadmap, Learn, Projects, and Insights. Roadmap presents the projection as a left-to-right learning journey with synchronized Map and accessible Outline experiences. The deterministic core has no LLM dependency.

## Local development

Prerequisites: Python 3.12 or newer and Node.js 20 or newer.

1. Create a Python virtual environment and install the project with development dependencies:

   ```bash
   python -m venv .venv
   .venv/bin/pip install -e '.[dev]'
   ```

2. Copy `.env.example` to `.env`, replace the bootstrap token with a long random value, and create the configured data and backup directories if needed.

3. Apply the database migration and run the API:

   ```bash
   .venv/bin/alembic upgrade head
   .venv/bin/uvicorn app.main:app --app-dir backend --reload
   ```

4. In another terminal, install and run the frontend:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

Open `http://127.0.0.1:5173`. The first-user screen requires the bootstrap token configured on the server. Vite proxies `/api` requests to the local API.

After installing the backend and frontend dependencies, the root helper scripts can run both development servers in the background:

```bash
./start.sh
./stop.sh
```

Runtime PID files and logs are stored in the ignored `tmp/learning-control-center/` directory.

## Validation

Run the backend checks from the repository root:

```bash
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy backend/app
.venv/bin/pytest
.venv/bin/alembic check
```

Run the frontend checks from `frontend/`:

```bash
npm run lint
npm run test
npm run build
```

The isolated product fixture runs against disposable temporary databases. Its aggregate final Phase 3 gate uses fresh stacks for the Profile/Learn/Projects, daily-control-loop, Roadmap/shell, and release-matrix stages; it includes primary Chromium flows, touch-oriented Chromium coverage, focused Firefox and Playwright WebKit compatibility checks, accessibility scans, and executable smoke validation of the later visual-QA manifest:

```bash
.venv/bin/python scripts/product_fixture_harness.py checkpoint6-playwright \
  --timezone UTC \
  --clock 2026-09-19T10:00:00Z
```

See `docs/phase3-product-qa.md` for the reproducible fixture-inspection command and real-platform browser/screen-reader checklist. Automated WebKit is not Safari certification, Chromium emulation is not Android certification, and unavailable real OS/device/assistive-technology checks remain explicitly `Not executed`.

With Caddy and Chrome installed, run the production-like TLS, authentication, timer, backup, and restore flow from the repository root:

```bash
./scripts/run-production-e2e.sh
```

## Data safety and production

Canonical application data lives in the configured SQLite database; operational backups are written outside the public frontend. Portable logical backups intentionally exclude passwords and live authentication sessions, while replacement restore preserves the existing local authentication state.

Production deployment must provide HTTPS/TLS, a strong bootstrap secret, restricted filesystem permissions, a persistent database volume, and a persistent backup directory. Complete a deployment-specific security review before exposing the application to the public internet.

The canonical product and engineering specifications are maintained under `memory-bank/`.
