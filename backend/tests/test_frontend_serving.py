"""The immutable frontend and API share one safe application origin."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.config import Settings
from app.frontend import FRONTEND_CSP, install_frontend_routes
from app.main import app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def test_pending_production_origin_never_accepts_wildcard_trust(tmp_path: Path) -> None:
    settings = Settings(
        environment="production",
        database_url=f"sqlite:///{tmp_path / 'data/lcc.sqlite3'}",
        backup_directory=tmp_path / "backups",
        public_origin="",
        allowed_origins=[],
        allowed_hosts=["127.0.0.1"],
        trusted_proxy_cidrs=["127.0.0.1/32"],
        security_secret="Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa",
    )
    assert settings.secure_cookies is True
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), "allowed_hosts": ["*"]})
    unsafe_origin = "https://lcc.example.test\nINJECTED=1"
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                **settings.model_dump(),
                "public_origin": unsafe_origin,
                "allowed_origins": [unsafe_origin],
                "allowed_hosts": ["lcc.example.test"],
            }
        )
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            database_url=f"sqlite:///{tmp_path / 'data/lcc.sqlite3'}",
            backup_directory=tmp_path / "backups",
            public_origin="https://*.example.test",
            allowed_origins=["https://*.example.test"],
            allowed_hosts=["*.example.test"],
            trusted_proxy_cidrs=["127.0.0.1/32"],
            security_secret="Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa",
        )


@pytest.mark.asyncio
async def test_frontend_api_precedence_headers_and_cache() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        index = await client.get("/")
        assert index.status_code == 200
        assert index.headers["content-type"].startswith("text/html")
        assert b"Learning Control Center" in index.content
        assert index.headers["content-security-policy"] == FRONTEND_CSP
        assert index.headers["cache-control"] == "no-store"
        assert index.headers["x-frame-options"] == "DENY"
        assert (await client.get("/roadmap/details")).content == index.content
        head = await client.head("/roadmap/details")
        assert head.status_code == 200 and head.content == b""
        assert head.headers["content-length"] == str(len(index.content))

        asset = next((ROOT / "frontend/dist/assets").glob("index-*.js"))
        served = await client.get(f"/assets/{asset.name}")
        assert served.status_code == 200 and served.content == asset.read_bytes()
        assert "javascript" in served.headers["content-type"]
        assert served.headers["cache-control"] == "public, max-age=31536000, immutable"
        logo = await client.get("/logo.png")
        assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
        guide = await client.get("/IMPORT_EXPORT_FORMAT.md")
        assert guide.status_code == 200
        assert guide.content == (ROOT / "frontend/dist/IMPORT_EXPORT_FORMAT.md").read_bytes()

        health = await client.get("/api/v1/health")
        assert health.json() == {"status": "ok"}
        assert health.headers["content-security-policy"].startswith("default-src 'none'")
        missing_api = await client.get("/api/v1/missing")
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"] == "application/json"
        assert missing_api.content != index.content
        for path in (
            "/assets/missing.js",
            "/logo-missing.png",
            "/.vite/manifest.json",
            "/LCC_FRONTEND_ARTIFACT.json",
            "/backend/app/main.py",
            "/data/lcc.db",
            "/etc/learning-control-center.env",
        ):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.content != index.content
        assert (
            await client.post("/roadmap/details", headers={"Origin": "http://localhost:5173"})
        ).status_code == 405


@pytest.mark.asyncio
async def test_frontend_never_follows_symlinks_or_serves_private_files(tmp_path: Path) -> None:
    dist = tmp_path / "frontend/dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>safe</title>")
    private = tmp_path / "private.txt"
    private.write_text("secret")
    (dist / "logo.png").symlink_to(private)
    (assets / "bundle-12345678.js").symlink_to(private)
    isolated = FastAPI()
    install_frontend_routes(isolated, tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=isolated), base_url="http://testserver"
    ) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/logo.png")).status_code == 404
        assert (await client.get("/assets/bundle-12345678.js")).status_code == 404
        assert (await client.get("/../private.txt")).status_code == 404


@pytest.mark.parametrize("configured", [False, True])
def test_production_host_origin_and_frontend_policy(tmp_path: Path, configured: bool) -> None:
    origin = "https://lcc.example.test" if configured else ""
    host = "lcc.example.test" if configured else "127.0.0.1"
    script = """
import asyncio
from httpx import ASGITransport, AsyncClient
from app.main import app

async def check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://127.0.0.1:8123') as client:
        good = {
            'Host': HOST,
            'X-Forwarded-Proto': 'https',
            'X-Forwarded-For': '198.51.100.8',
        }
        bad = {'Host': '127.0.0.1' if HOST != '127.0.0.1' else 'unconfigured.example.test'}
        index = await client.get('/', headers=good)
        assert index.status_code == 200, index.text
        assert index.headers['content-security-policy'].startswith("default-src 'self'")
        assert index.headers['strict-transport-security'].startswith('max-age=')
        assert (await client.get('/roadmap/overview', headers=good)).content == index.content
        assert (await client.get('/api/v1/health', headers=good)).json() == {'status': 'ok'}
        bad_host = await client.get('/', headers=bad)
        assert bad_host.status_code == 400
        assert bad_host.headers['content-security-policy'].startswith("default-src 'none'")
        rejected = await client.post('/api/v1/auth/logout', headers={**good, 'Origin': 'https://attacker.example.test'})
        assert rejected.status_code == 403
asyncio.run(check())
""".replace("HOST", repr(host))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "LCC_ENVIRONMENT": "production",
            "LCC_DATABASE_URL": f"sqlite:///{tmp_path / 'data/lcc.sqlite3'}",
            "LCC_BACKUP_DIRECTORY": str(tmp_path / "backups"),
            "LCC_PUBLIC_ORIGIN": origin,
            "LCC_ALLOWED_ORIGINS": f'["{origin}"]' if configured else "[]",
            "LCC_ALLOWED_HOSTS": f'["{host}"]',
            "LCC_TRUSTED_PROXY_CIDRS": '["127.0.0.1/32"]',
            "LCC_SECURITY_SECRET": "Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
