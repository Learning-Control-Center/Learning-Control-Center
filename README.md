# Learning-Control-Center

Learning-Control-Center is a single-user, self-hosted system for competency-based roadmaps, daily learning recommendations, session tracking, verification, deterministic analytics, reports, and safe data transfer.

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

## Data safety and production

Canonical application data lives in the configured SQLite database; operational backups are written outside the public frontend. Portable logical backups intentionally exclude passwords and live authentication sessions, while replacement restore preserves the existing local authentication state.

Production deployment must provide HTTPS/TLS, a strong bootstrap secret, restricted filesystem permissions, a persistent database volume, and a persistent backup directory. Complete the dedicated security-hardening and deployment review before exposing the application to a network.

The canonical product and engineering specifications are maintained under `memory-bank/`.
