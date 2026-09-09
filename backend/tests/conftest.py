from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("LCC_DATABASE_URL", "sqlite:////tmp/lcc-test-process.sqlite3")
os.environ.setdefault("LCC_BOOTSTRAP_TOKEN", "test-bootstrap-token-with-enough-entropy")

from app.config import Settings, get_settings_dependency  # noqa: E402
from app.database import create_database_engine, get_db, run_migrations  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DisciplineProfile  # noqa: E402


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    database_url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with maker() as session:
        session.add(
            DisciplineProfile(
                id=1,
                weekly_target_active_days=5,
                target_duration_ms_per_active_day=3_600_000,
                timezone="UTC",
            )
        )
        session.commit()
        yield session
    engine.dispose()


@pytest_asyncio.fixture
async def client(db: Session, tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    async def override_db() -> AsyncGenerator[Session, None]:
        yield db

    settings = Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        bootstrap_token="test-bootstrap-token-with-enough-entropy",
        backup_directory=tmp_path / "backups",
        allowed_origins=["http://testserver"],
    )
    app.dependency_overrides[get_db] = override_db

    async def override_settings() -> Settings:
        return settings

    app.dependency_overrides[get_settings_dependency] = override_settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def authenticated_client(client: AsyncClient) -> tuple[AsyncClient, str]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "learner",
            "password": "correct horse battery staple",
            "bootstrap_token": "test-bootstrap-token-with-enough-entropy",
        },
    )
    assert response.status_code == 201, response.text
    return client, response.json()["csrf_token"]


@pytest.fixture
def roadmap_payload() -> dict[str, object]:
    return {
        "stable_key": "technical-foundations",
        "title": "Technical Foundations",
        "description": "A deterministic demo roadmap.",
        "version": "1.0.0",
        "current_phase_stable_key": "phase-1",
        "phases": [
            {
                "stable_key": "phase-1",
                "title": "Foundation",
                "order_index": 0,
                "tracks": [
                    {
                        "stable_key": "python",
                        "title": "Python",
                        "order_index": 0,
                        "competencies": [
                            {
                                "stable_key": "python.basics",
                                "title": "Python basics",
                                "priority": "core",
                                "weight": 5,
                                "order_index": 0,
                                "must_understand": ["Values and control flow"],
                                "must_be_able_to": ["Write a small program"],
                                "exit_criteria": [
                                    {
                                        "stable_key": "python.basics.program",
                                        "text": "Write a small program independently",
                                        "required": True,
                                    }
                                ],
                            },
                            {
                                "stable_key": "python.functions",
                                "title": "Functions",
                                "priority": "important",
                                "weight": 4,
                                "order_index": 1,
                                "prerequisite_stable_keys": ["python.basics"],
                                "exit_criteria": [
                                    {
                                        "stable_key": "python.functions.compose",
                                        "text": "Compose functions",
                                        "required": True,
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            {
                "stable_key": "phase-2",
                "title": "Applied work",
                "order_index": 1,
                "tracks": [
                    {
                        "stable_key": "projects",
                        "title": "Projects",
                        "order_index": 0,
                        "competencies": [
                            {
                                "stable_key": "project.delivery",
                                "title": "Project delivery",
                                "priority": "core",
                                "weight": 5,
                                "order_index": 0,
                            }
                        ],
                    }
                ],
            },
        ],
    }


@pytest_asyncio.fixture
async def configured_client(
    authenticated_client: tuple[AsyncClient, str], roadmap_payload: dict[str, object]
) -> tuple[AsyncClient, str, dict[str, object]]:
    client, csrf = authenticated_client
    response = await client.post(
        "/api/v1/roadmap",
        json=roadmap_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return client, csrf, response.json()
