#!/usr/bin/env python3
"""Disposable full-stack product fixture harness for Phase 3 browser scenarios.

This is intentionally separate from the serialized production TLS/security/recovery
journey. Every invocation owns its processes, ports, logs, and SQLite database.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
PYTHON = REPOSITORY_ROOT / ".venv" / "bin" / "python"
DEFAULT_CLOCK = "2026-09-19T10:00:00Z"
DEFAULT_TIMEZONE = "UTC"
BOOTSTRAP_TOKEN = "fixture-bootstrap-token-with-enough-entropy-47"
USERNAME = "fixture-learner"
PASSWORD = "fixture-password-with-enough-entropy"

JsonObject = dict[str, Any]
ScenarioName = Literal[
    "legacy-shell",
    "v2-shell",
    "roadmap-25",
    "roadmap-100",
    "roadmap-250",
]
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
INSTANT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
SENSITIVE_FIXTURE_KEYS = {"password", "bootstrap_token", "csrf_token"}


def _sanitize_fixture_value(value: Any, *, key: str | None = None) -> Any:
    """Retain semantic fixture inputs while removing secrets and run-specific values."""

    if key in SENSITIVE_FIXTURE_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_fixture_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_fixture_value(item) for item in value]
    if isinstance(value, str):
        normalized = UUID_PATTERN.sub("<id>", value)
        return "<instant>" if INSTANT_PATTERN.fullmatch(normalized) else normalized
    return value


def _dynamic_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _parse_clock(value: str | None) -> str | None:
    if value is None or value == "real":
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Fixture clock must include a timezone offset.")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _fixture_hash(
    scenario: ScenarioName,
    timezone: str,
    clock_at: str | None,
    calls: list[dict[str, Any]],
) -> str:
    normalized_calls = [_sanitize_fixture_value(call) for call in calls]
    payload = {
        "scenario": scenario,
        "timezone": timezone,
        "clockAt": clock_at,
        "publicContractCalls": normalized_calls,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _repository_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _production_build_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted((FRONTEND_ROOT / "dist").rglob("*")):
        if not path.is_file():
            continue
        digest.update(path.relative_to(FRONTEND_ROOT / "dist").as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _wait_for_url(url: str, process: subprocess.Popen[bytes], timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Process exited before {url} became ready (code {process.returncode})."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


class PublicApiClient:
    """Small public-contract client shared by fixture scenarios and browser setup."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.csrf_token = ""
        self.calls: list[dict[str, Any]] = []
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )

    def request(
        self,
        method: str,
        path: str,
        payload: JsonObject | None = None,
        *,
        expected: int = 200,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if method not in {"GET", "HEAD", "OPTIONS"} and self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=20) as response:
                content = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            content = exc.read()
            status = exc.code
        parsed = json.loads(content) if content else None
        call: dict[str, Any] = {"method": method, "path": path, "status": status}
        if payload is not None:
            call["payload"] = _sanitize_fixture_value(payload)
        self.calls.append(call)
        if status != expected:
            raise RuntimeError(
                f"{method} {path} returned {status}, expected {expected}: {parsed!r}"
            )
        return parsed

    def bootstrap(self) -> JsonObject:
        result = self.request(
            "POST",
            "/api/v1/auth/bootstrap",
            {
                "username": USERNAME,
                "password": PASSWORD,
                "bootstrap_token": BOOTSTRAP_TOKEN,
            },
            expected=201,
        )
        assert isinstance(result, dict)
        self.csrf_token = str(result["csrf_token"])
        return result

    def login(self) -> JsonObject:
        self._cookies.clear()
        self.csrf_token = ""
        result = self.request(
            "POST",
            "/api/v1/auth/login",
            {"username": USERNAME, "password": PASSWORD},
        )
        assert isinstance(result, dict)
        self.csrf_token = str(result["csrf_token"])
        return result


@dataclass
class FixtureRun:
    scenario: ScenarioName
    timezone: str = DEFAULT_TIMEZONE
    clock_at: str | None = DEFAULT_CLOCK
    root: Path | None = None
    backend_port: int = field(default_factory=_dynamic_port)
    frontend_port: int = field(default_factory=_dynamic_port)
    processes: list[subprocess.Popen[bytes]] = field(default_factory=list)
    _logs: list[Any] = field(default_factory=list)
    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    declared_clock_at: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.clock_at = _parse_clock(self.clock_at)
        self.declared_clock_at = self.clock_at
        if self.root is None:
            self.root = Path(tempfile.mkdtemp(prefix=f"lcc-product-{self.scenario}-"))
        else:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        (self.root / "data").mkdir(mode=0o700)
        (self.root / "backups").mkdir(mode=0o700)
        (self.root / "artifacts").mkdir(mode=0o700)

    @property
    def backend_url(self) -> str:
        return f"http://127.0.0.1:{self.backend_port}"

    @property
    def frontend_url(self) -> str:
        return f"http://127.0.0.1:{self.frontend_port}"

    @property
    def database_path(self) -> Path:
        assert self.root is not None
        return self.root / "data" / "fixture.sqlite3"

    def backend_environment(self) -> dict[str, str]:
        assert self.root is not None
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(BACKEND_ROOT),
                "LCC_ENVIRONMENT": "test",
                "LCC_DATABASE_URL": f"sqlite:///{self.database_path}",
                "LCC_BACKUP_DIRECTORY": str(self.root / "backups"),
                "LCC_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
                "LCC_SECURITY_SECRET": "fixture-process-security-key-with-enough-entropy",
                "LCC_APP_TIMEZONE": self.timezone,
                "LCC_PUBLIC_ORIGIN": self.frontend_url,
                "LCC_ALLOWED_ORIGINS": json.dumps([self.frontend_url]),
            }
        )
        if self.clock_at is not None:
            environment["LCC_FIXTURE_CLOCK_AT"] = self.clock_at
            environment["LCC_FIXTURE_CLOCK_STEP_MS"] = "10"
        else:
            environment.pop("LCC_FIXTURE_CLOCK_AT", None)
            environment.pop("LCC_FIXTURE_CLOCK_STEP_MS", None)
        return environment

    def _start_backend(self) -> None:
        assert self.root is not None
        backend_log = (self.root / "backend.log").open("ab")
        self._logs.append(backend_log)
        backend = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.backend_port),
                "--workers",
                "1",
                "--no-proxy-headers",
            ],
            cwd=BACKEND_ROOT,
            env=self.backend_environment(),
            stdout=backend_log,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(backend)
        self.backend_process = backend
        _wait_for_url(f"{self.backend_url}/api/v1/health", backend)

    def _start_frontend(self) -> None:
        assert self.root is not None
        build_log_path = self.root / "build.log"
        with build_log_path.open("wb") as build_log:
            build = subprocess.run(
                ["npm", "run", "build"],
                cwd=FRONTEND_ROOT,
                env=os.environ.copy(),
                stdout=build_log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if build.returncode != 0:
            raise RuntimeError(f"Frontend production build failed; see {build_log_path}.")
        frontend_log = (self.root / "frontend.log").open("ab")
        self._logs.append(frontend_log)
        frontend_environment = os.environ.copy()
        frontend_environment["LCC_VITE_BACKEND_URL"] = self.backend_url
        frontend = subprocess.Popen(
            [
                "npm",
                "run",
                "preview",
                "--",
                "--port",
                str(self.frontend_port),
                "--strictPort",
            ],
            cwd=FRONTEND_ROOT,
            env=frontend_environment,
            stdout=frontend_log,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(frontend)
        self.frontend_process = frontend
        _wait_for_url(self.frontend_url, frontend)

    def start(self) -> PublicApiClient:
        if not PYTHON.is_file():
            raise RuntimeError(f"Repository virtual environment is missing: {PYTHON}")
        self._start_backend()
        self._start_frontend()
        return PublicApiClient(self.backend_url)

    def restart_backend(self, clock_at: str) -> PublicApiClient:
        if self.backend_process is None:
            raise RuntimeError("Cannot restart a backend that is not running.")
        process = self.backend_process
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.processes.remove(process)
        self.backend_process = None
        self.clock_at = _parse_clock(clock_at)
        self._start_backend()
        return PublicApiClient(self.backend_url)

    def stop(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        self.processes.clear()
        self.backend_process = None
        self.frontend_process = None
        for handle in self._logs:
            handle.close()
        self._logs.clear()

    def finish(self, *, success: bool) -> None:
        # A fixture database is never removed or replaced while its application is alive.
        self.stop()
        assert self.root is not None
        if success:
            shutil.rmtree(self.root)

    def write_metadata(
        self,
        client: PublicApiClient,
        authority: JsonObject,
        expected_authority: str,
    ) -> Path:
        assert self.root is not None
        metadata = {
            "scenarioId": self.scenario,
            "databasePath": str(self.database_path),
            "timezone": self.timezone,
            "clockMode": "fixed" if self.clock_at is not None else "real",
            "declaredClockAt": self.declared_clock_at,
            "clockAt": self.clock_at,
            "backendUrl": self.backend_url,
            "frontendUrl": self.frontend_url,
            "authority": authority,
            "expectedAuthority": expected_authority,
            "authorityReadParity": {
                "matchesExpectedCanonicalAuthority": True,
                "matchesSeedResponse": True,
            },
            "repositoryRevision": _repository_revision(),
            "productionBuildHash": _production_build_hash(),
            "publicContractCalls": client.calls,
            "fixtureHash": _fixture_hash(self.scenario, self.timezone, self.clock_at, client.calls),
        }
        path = self.root / "artifacts" / "fixture-metadata.json"
        path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _seed_legacy_shell(client: PublicApiClient) -> JsonObject:
    client.bootstrap()
    client.login()
    authority = client.request("GET", "/api/v2/authority")
    if not isinstance(authority, dict):
        raise RuntimeError("Legacy shell authority response was not an object.")
    if authority["canonicalLearningAuthority"] != "legacy_v1":
        raise RuntimeError("Legacy shell fixture did not retain legacy authority.")
    return authority


def _seed_v2_shell(client: PublicApiClient, run: FixtureRun) -> tuple[JsonObject, PublicApiClient]:
    client.bootstrap()
    client.login()
    clock_cursor = (
        datetime.fromisoformat(run.clock_at.replace("Z", "+00:00"))
        if run.clock_at is not None
        else None
    )

    def advance_fixture_clock(active_client: PublicApiClient) -> PublicApiClient:
        nonlocal clock_cursor
        if clock_cursor is None:
            return active_client
        clock_cursor += timedelta(minutes=1)
        restarted = run.restart_backend(clock_cursor.isoformat().replace("+00:00", "Z"))
        restarted.calls = active_client.calls
        restarted.login()
        return restarted

    def current_fixture_instant() -> str:
        return run.clock_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # The application creates an initial empty Analysis snapshot on startup.
    # Advance before adding effective-dated facts so that the fixture never
    # backdates new canonical history into that immutable startup cutoff.
    client = advance_fixture_clock(client)
    effective_at = current_fixture_instant()

    competency = client.request(
        "POST",
        "/api/v2/competencies",
        {"stable_key": "fixture.shell", "creation_source": "product_fixture"},
        expected=201,
    )
    competency_id = str(competency["id"])
    definition = client.request(
        "POST",
        f"/api/v2/competencies/{competency_id}/definitions",
        {
            "title": "Fixture shell capability",
            "description": "Minimal canonical capability for the V2 shell scenario.",
            "scope": "Fixture-only public API readiness proof",
            "scale_stable_key": "technical",
            "scale_version": "v1",
            "dimension_keys": [],
            "effective_at": effective_at,
            "creation_source": "product_fixture",
            "criteria": [
                {
                    "stable_key": "fixture.shell.independent",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Demonstrate the shell fixture capability independently.",
                }
            ],
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/competencies/{competency_id}/definitions/{definition['id']}/activate",
        {
            "reason": "Fixture readiness",
            "source": "product_fixture",
            "idempotency_key": "fixture-definition-active",
        },
    )
    client = advance_fixture_clock(client)

    profile = client.request(
        "POST",
        "/api/v2/target-profiles",
        {
            "stable_key": "fixture-shell-profile",
            "creation_source": "product_fixture",
            "version": {
                "title": "Fixture shell profile",
                "description": "Minimal V2 shell profile.",
                "creation_source": "product_fixture",
                "effective_at": current_fixture_instant(),
                "domains": [
                    {
                        "stable_key": "learning",
                        "title": "Learning",
                        "minimum_percent": 100,
                        "maximum_percent": 100,
                        "order_index": 0,
                    }
                ],
                "targets": [
                    {
                        "stable_key": "fixture-shell-target",
                        "competency_identity_id": competency_id,
                        "dimension_key": None,
                        "domain_stable_key": "learning",
                        "scale_stable_key": "technical",
                        "scale_version": "v1",
                        "target_level_stable_key": "independent",
                        "priority": "core",
                    }
                ],
                "milestones": [],
                "readiness_gates": [],
            },
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/target-profiles/{profile['profileId']}/versions/{profile['versionId']}/activate",
        {
            "reason": "Fixture readiness",
            "source": "product_fixture",
            "idempotency_key": "fixture-profile-active",
        },
    )
    client = advance_fixture_clock(client)

    graph = client.request(
        "POST",
        "/api/v2/learning-graphs",
        {"stable_key": "fixture-shell-graph", "creation_source": "product_fixture"},
        expected=201,
    )
    graph_version = client.request(
        "POST",
        f"/api/v2/learning-graphs/{graph['id']}/versions",
        {
            "title": "Fixture shell graph",
            "description": "Minimal V2 shell graph.",
            "effective_at": current_fixture_instant(),
            "creation_source": "product_fixture",
            "edges": [],
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/learning-graphs/{graph['id']}/versions/{graph_version['id']}/activate",
        {
            "reason": "Fixture readiness",
            "source": "product_fixture",
            "idempotency_key": "fixture-graph-active",
        },
        expected=201,
    )
    client = advance_fixture_clock(client)

    curriculum = client.request(
        "POST",
        "/api/v2/curricula",
        {"stable_key": "fixture-shell-curriculum", "creation_source": "product_fixture"},
        expected=201,
    )
    curriculum_version = client.request(
        "POST",
        f"/api/v2/curricula/{curriculum['id']}/versions",
        {
            "title": "Fixture shell curriculum",
            "description": "Minimal authored catalog for V2 readiness.",
            "effective_at": current_fixture_instant(),
            "creation_source": "product_fixture",
            "objectives": [],
            "units": [],
            "assessment_rubrics": [],
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/curricula/{curriculum['id']}/versions/{curriculum_version['id']}/activate",
        {
            "reason": "Fixture readiness",
            "source": "product_fixture",
            "idempotency_key": "fixture-curriculum-active",
        },
    )
    client = advance_fixture_clock(client)

    scales = client.request("GET", "/api/v2/capability-scales")
    technical = next(
        item for item in scales if item["stableKey"] == "technical" and item["version"] == "v1"
    )
    independent = next(item for item in technical["levels"] if item["stableKey"] == "independent")
    criterion = definition["criteria"][0]
    for index in range(2):
        occurred_at = (
            (
                datetime.fromisoformat(current_fixture_instant().replace("Z", "+00:00"))
                - timedelta(seconds=5)
            )
            .isoformat()
            .replace("+00:00", "Z")
        )
        client.request(
            "POST",
            "/api/v2/evidence",
            {
                "idempotency_key": f"fixture-shell-evidence-{index}",
                "evidence_type": "assessment",
                "title": f"Fixture readiness evidence {index + 1}",
                "description": "Independent public-API fixture evidence.",
                "strength": "moderate",
                "independence": "independent",
                "occurred_at": occurred_at,
                "capture_method": "product_fixture",
                "authoritative_reassessment": False,
                "maximum_supported_level_id": None,
                "links": [
                    {
                        "competency_identity_id": competency_id,
                        "criterion_identity_id": criterion["identityId"],
                        "criterion_definition_id": criterion["id"],
                        "scale_version_id": technical["id"],
                        "dimension_id": None,
                        "level_id": independent["id"],
                        "effect": "supports",
                        "relevance": "primary",
                    }
                ],
            },
            expected=201,
        )
        client = advance_fixture_clock(client)
    client.request(
        "POST",
        "/api/v2/analysis/runs",
        {
            "idempotency_key": "fixture-shell-analysis",
            "purpose": "learning_control",
        },
        expected=201,
    )
    readiness = client.request("GET", "/api/v2/authority/readiness")
    if not readiness["ready"]:
        raise RuntimeError(f"V2 shell readiness failed: {readiness!r}")
    authority = client.request(
        "POST",
        "/api/v2/authority/activate-v2",
        {
            "idempotency_key": "fixture-shell-activate-v2",
            "reason": "Disposable product fixture passed runtime readiness.",
        },
    )
    if authority["canonicalLearningAuthority"] != "v2":
        raise RuntimeError("V2 shell fixture did not activate V2 authority.")
    return authority, client


def _seed_roadmap_scale(client: PublicApiClient, run: FixtureRun, size: int) -> JsonObject:
    """Build a representative journey scenario exclusively through public APIs."""
    _authority, client = _seed_v2_shell(client, run)
    effective_at = run.clock_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scales = client.request("GET", "/api/v2/capability-scales")
    technical = next(
        item for item in scales if item["stableKey"] == "technical" and item["version"] == "v1"
    )
    familiar = next(item for item in technical["levels"] if item["stableKey"] == "familiar")
    independent = next(item for item in technical["levels"] if item["stableKey"] == "independent")
    cefr = next(item for item in scales if item["stableKey"] == "cefr" and item["version"] == "v1")
    cefr_b1 = next(item for item in cefr["levels"] if item["stableKey"] == "b1")
    speaking = next(item for item in cefr["dimensions"] if item["stableKey"] == "speaking")
    foundation_count = 4
    dimension_index = foundation_count + 1
    competency_ids: list[str] = []
    definition_ids: list[str] = []
    criterion_ids: list[str] = []
    criterion_identity_ids: list[str] = []
    prerequisite_requirements: list[JsonObject] = []
    for index in range(size):
        stable_key = f"fixture.roadmap.{index:03d}"
        competency = client.request(
            "POST",
            "/api/v2/competencies",
            {"stable_key": stable_key, "creation_source": "roadmap_fixture"},
            expected=201,
        )
        competency_id = str(competency["id"])
        is_dimension_node = index == dimension_index
        criteria = (
            [
                {
                    "stable_key": f"{stable_key}.speaking",
                    "level_stable_key": "b1",
                    "dimension_key": "speaking",
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Demonstrate representative speaking capability.",
                },
                {
                    "stable_key": f"{stable_key}.reading",
                    "level_stable_key": "b2",
                    "dimension_key": "reading",
                    "requirement_type": "required",
                    "demonstration_rule": "authoritative_assessment",
                    "description": "Demonstrate representative reading capability.",
                },
            ]
            if is_dimension_node
            else [
                {
                    "stable_key": f"{stable_key}.independent",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": f"Demonstrate Roadmap fixture capability {index + 1}.",
                }
            ]
        )
        definition = client.request(
            "POST",
            f"/api/v2/competencies/{competency_id}/definitions",
            {
                "title": f"Roadmap capability {index + 1} with a representative long title",
                "description": f"Deterministic Roadmap fixture node {index + 1}.",
                "scope": f"Roadmap stress fixture scope {index + 1}",
                "scale_stable_key": "cefr" if is_dimension_node else "technical",
                "scale_version": "v1",
                "dimension_keys": ["speaking", "reading"] if is_dimension_node else [],
                "effective_at": effective_at,
                "creation_source": "roadmap_fixture",
                "criteria": criteria,
            },
            expected=201,
        )
        client.request(
            "POST",
            f"/api/v2/competencies/{competency_id}/definitions/{definition['id']}/activate",
            {
                "reason": "Roadmap fixture activation",
                "source": "roadmap_fixture",
                "idempotency_key": f"roadmap-fixture-definition-{index:03d}",
            },
        )
        competency_ids.append(competency_id)
        definition_ids.append(str(definition["id"]))
        criterion_ids.append(str(definition["criteria"][0]["id"]))
        criterion_identity_ids.append(str(definition["criteria"][0]["identityId"]))
        prerequisite_requirements.append(
            {
                "kind": "capability_at_least",
                "scale_version_id": cefr["id"] if is_dimension_node else technical["id"],
                "dimension_id": speaking["id"] if is_dimension_node else None,
                "minimum_level_id": cefr_b1["id"] if is_dimension_node else familiar["id"],
                "review_requirement": "none",
            }
        )

    domains = [
        {
            "stable_key": f"domain-{index}",
            "title": f"Learning domain {index + 1}",
            "minimum_percent": 0,
            "maximum_percent": 100,
            "order_index": index,
        }
        for index in range(4)
    ]
    branch_size = size - foundation_count

    def domain_index_for(node_index: int) -> int:
        return min(3, ((node_index - foundation_count) * 4) // branch_size)

    targets: list[JsonObject] = []
    for index in range(foundation_count, size):
        if index == dimension_index:
            targets.extend(
                [
                    {
                        "stable_key": f"target-{index:03d}-speaking",
                        "competency_identity_id": competency_ids[index],
                        "dimension_key": "speaking",
                        "domain_stable_key": "domain-0",
                        "scale_stable_key": "cefr",
                        "scale_version": "v1",
                        "target_level_stable_key": "b1",
                        "priority": "critical",
                    },
                    {
                        "stable_key": f"target-{index:03d}-reading",
                        "competency_identity_id": competency_ids[index],
                        "dimension_key": "reading",
                        "domain_stable_key": "domain-1",
                        "scale_stable_key": "cefr",
                        "scale_version": "v1",
                        "target_level_stable_key": "b2",
                        "priority": "important",
                    },
                ]
            )
            continue
        targets.append(
            {
                "stable_key": f"target-{index:03d}",
                "competency_identity_id": competency_ids[index],
                "dimension_key": None,
                "domain_stable_key": f"domain-{domain_index_for(index)}",
                "scale_stable_key": "technical",
                "scale_version": "v1",
                "target_level_stable_key": "independent",
                "priority": "critical" if index % 17 == 0 else "core",
            }
        )
    profile = client.request(
        "POST",
        "/api/v2/target-profiles",
        {
            "stable_key": f"roadmap-fixture-{size}",
            "creation_source": "roadmap_fixture",
            "version": {
                "title": f"Roadmap fixture {size}",
                "description": "Public-API Roadmap scale fixture.",
                "creation_source": "roadmap_fixture",
                "effective_at": effective_at,
                "domains": domains,
                "targets": targets,
                "milestones": [],
                "readiness_gates": [],
            },
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/target-profiles/{profile['profileId']}/versions/{profile['versionId']}/activate",
        {
            "reason": "Roadmap fixture activation",
            "source": "roadmap_fixture",
            "idempotency_key": f"roadmap-fixture-profile-{size}",
        },
    )

    edges: list[JsonObject] = []

    def add_prerequisite(source_index: int, target_index: int) -> None:
        edges.append(
            {
                "stable_key": f"requires-{source_index:03d}-{target_index:03d}",
                "edge_type": "prerequisite",
                "source_semantic_definition_id": definition_ids[source_index],
                "target_semantic_definition_id": definition_ids[target_index],
                "satisfaction_scope_key": (
                    f"dimension:{speaking['id']}" if source_index == dimension_index else "overall"
                ),
                "requirement": prerequisite_requirements[source_index],
                "provenance": "roadmap_fixture",
                "meaning_key": "fixture-prerequisite",
                "order_index": len(edges),
            }
        )

    for index in range(1, foundation_count):
        add_prerequisite(index - 1, index)
    branches = {
        domain_index: [
            index
            for index in range(foundation_count, size)
            if domain_index_for(index) == domain_index
        ]
        for domain_index in range(4)
    }
    for branch in branches.values():
        if not branch:
            continue
        add_prerequisite(foundation_count - 1, branch[0])
        if len(branch) >= 4:
            add_prerequisite(branch[0], branch[1])
            add_prerequisite(branch[0], branch[2])
            add_prerequisite(branch[1], branch[3])
            add_prerequisite(branch[2], branch[3])
            for index in range(4, len(branch)):
                add_prerequisite(branch[index - 1], branch[index])
        else:
            for index in range(1, len(branch)):
                add_prerequisite(branch[index - 1], branch[index])
        if len(branch) >= 3:
            for parent in branch[:2]:
                edges.append(
                    {
                        "stable_key": f"specializes-{parent:03d}-{branch[2]:03d}",
                        "edge_type": "specialization",
                        "source_semantic_definition_id": definition_ids[parent],
                        "target_semantic_definition_id": definition_ids[branch[2]],
                        "satisfaction_scope_key": "overall",
                        "requirement": None,
                        "provenance": "roadmap_fixture",
                        "meaning_key": "fixture-specialization",
                        "order_index": len(edges),
                    }
                )

    first_branch = branches[0]
    second_branch = branches[1]
    if first_branch and second_branch:
        edges.extend(
            [
                {
                    "stable_key": "cross-domain-support",
                    "edge_type": "supports",
                    "source_semantic_definition_id": definition_ids[first_branch[-1]],
                    "target_semantic_definition_id": definition_ids[second_branch[-1]],
                    "satisfaction_scope_key": "overall",
                    "requirement": None,
                    "provenance": "roadmap_fixture",
                    "meaning_key": "fixture-cross-domain-support",
                    "order_index": len(edges),
                },
                {
                    "stable_key": "opposite-domain-related",
                    "edge_type": "related",
                    "source_semantic_definition_id": definition_ids[second_branch[-1]],
                    "target_semantic_definition_id": definition_ids[first_branch[-1]],
                    "satisfaction_scope_key": "overall",
                    "requirement": None,
                    "provenance": "roadmap_fixture",
                    "meaning_key": "fixture-opposite-domain-related",
                    "order_index": len(edges) + 1,
                },
            ]
        )
    graph = client.request(
        "POST",
        "/api/v2/learning-graphs",
        {"stable_key": f"roadmap-fixture-{size}", "creation_source": "roadmap_fixture"},
        expected=201,
    )
    graph_version = client.request(
        "POST",
        f"/api/v2/learning-graphs/{graph['id']}/versions",
        {
            "title": f"Roadmap fixture {size}",
            "description": "Public-API Roadmap scale fixture.",
            "effective_at": effective_at,
            "creation_source": "roadmap_fixture",
            "edges": edges,
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/learning-graphs/{graph['id']}/versions/{graph_version['id']}/activate",
        {
            "reason": "Roadmap fixture activation",
            "source": "roadmap_fixture",
            "idempotency_key": f"roadmap-fixture-graph-{size}",
        },
        expected=201,
    )

    journey_curriculum = client.request(
        "POST",
        "/api/v2/curricula",
        {"stable_key": f"roadmap-journey-{size}", "creation_source": "roadmap_fixture"},
        expected=201,
    )
    journey_version = client.request(
        "POST",
        f"/api/v2/curricula/{journey_curriculum['id']}/versions",
        {
            "title": f"Roadmap journey actions {size}",
            "description": "Representative available work for the Roadmap Today overlay.",
            "effective_at": effective_at,
            "creation_source": "roadmap_fixture",
            "objectives": [
                {
                    "stable_key": "journey",
                    "title": "Journey",
                    "description": "Continue the target path.",
                    "order_index": 0,
                }
            ],
            "units": [
                {
                    "stable_key": "journey-practice",
                    "objective_stable_key": "journey",
                    "kind": "practice_task",
                    "title": "Practice the next Roadmap capability",
                    "description": "Representative target-linked learning action.",
                    "action": {
                        "kind": "practice_task",
                        "instructions": "Practice the capability with a small deliverable.",
                    },
                    "status": "active",
                    "provenance": "roadmap_fixture",
                    "order_index": 0,
                    "minimum_useful_duration_ms": 900000,
                    "preferred_duration_ms": 1800000,
                    "maximum_useful_duration_ms": 2700000,
                    "targets": [
                        {
                            "semantic_definition_id": definition_ids[foundation_count],
                            "criterion_definition_id": criterion_ids[foundation_count],
                            "scale_version_id": technical["id"],
                            "dimension_id": None,
                            "intended_learning_outcome": "Advance one target capability.",
                            "minimum_level_id": familiar["id"],
                            "maximum_level_id": independent["id"],
                            "supports_unassessed": True,
                            "role": "primary",
                            "order_index": 0,
                        }
                    ],
                    "requirements": [],
                    "evidence_opportunities": [],
                }
            ],
            "assessment_rubrics": [],
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/curricula/{journey_curriculum['id']}/versions/{journey_version['id']}/activate",
        {
            "reason": "Roadmap fixture action",
            "source": "roadmap_fixture",
            "idempotency_key": f"roadmap-fixture-curriculum-{size}",
        },
    )
    evidence_source_index = foundation_count - 1
    evidence_project = client.request(
        "POST",
        "/api/v2/projects",
        {"stable_key": f"roadmap-evidence-{size}", "creation_source": "roadmap_fixture"},
        expected=201,
    )
    evidence_project_version = client.request(
        "POST",
        f"/api/v2/projects/{evidence_project['id']}/versions",
        {
            "title": "Roadmap prerequisite evidence",
            "description": (
                "A completed fixture project that establishes a known current capability."
            ),
            "effective_at": effective_at,
            "creation_source": "roadmap_fixture",
            "goals": [
                {
                    "stable_key": "prove",
                    "title": "Prove the prerequisite",
                    "description": "Demonstrate the shared foundation.",
                    "order_index": 0,
                }
            ],
            "milestones": [
                {
                    "stable_key": "evidence",
                    "title": "Evidence",
                    "description": "Capture an assessed artifact.",
                    "order_index": 0,
                }
            ],
            "tasks": [
                {
                    "stable_key": "demonstrate",
                    "title": "Demonstrate the shared foundation",
                    "description": "Produce an independently assessed artifact.",
                    "instructions": "Complete and assess the representative artifact.",
                    "milestone_stable_key": "evidence",
                    "status": "active",
                    "order_index": 0,
                    "minimum_useful_duration_ms": 300000,
                    "preferred_duration_ms": 600000,
                    "maximum_useful_duration_ms": 900000,
                }
            ],
            "criteria": [
                {
                    "stable_key": "artifact-passes",
                    "title": "Artifact passes",
                    "description": "The representative artifact satisfies the fixture rubric.",
                    "milestone_stable_key": "evidence",
                    "evaluation_policy_version": "project-criterion-policy/v1",
                    "order_index": 0,
                }
            ],
            "targets": [
                {
                    "semantic_definition_id": definition_ids[evidence_source_index],
                    "criterion_definition_id": criterion_ids[evidence_source_index],
                    "scale_version_id": technical["id"],
                    "dimension_id": None,
                    "level_id": independent["id"],
                    "intended_outcome": "Practice the shared foundation.",
                    "role": "primary",
                    "task_stable_key": "demonstrate",
                    "order_index": 0,
                },
                {
                    "semantic_definition_id": definition_ids[evidence_source_index],
                    "criterion_definition_id": criterion_ids[evidence_source_index],
                    "scale_version_id": technical["id"],
                    "dimension_id": None,
                    "level_id": independent["id"],
                    "intended_outcome": "Demonstrate the shared foundation.",
                    "role": "primary",
                    "project_criterion_stable_key": "artifact-passes",
                    "order_index": 1,
                },
            ],
            "requirements": [],
            "dependencies": [],
            "evidence_opportunities": [
                {
                    "stable_key": "assessed-artifact",
                    "task_stable_key": "demonstrate",
                    "project_criterion_stable_key": "artifact-passes",
                    "evidence_kind": "project",
                    "intended_strengths": ["strong"],
                    "intended_independence_modes": ["independent"],
                    "requires_artifact": True,
                    "order_index": 0,
                }
            ],
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/projects/{evidence_project['id']}/versions/{evidence_project_version['id']}/activate",
        {
            "reason": "Roadmap fixture evidence",
            "source": "roadmap_fixture",
            "idempotency_key": f"roadmap-fixture-project-{size}",
        },
    )
    client.request(
        "POST",
        f"/api/v2/projects/{evidence_project['id']}/events",
        {
            "event_type": "project_lifecycle",
            "project_lifecycle_state": "active",
            "source": "roadmap_fixture",
            "idempotency_key": f"roadmap-fixture-project-active-{size}",
        },
        expected=201,
    )
    evidence_task = next(
        item for item in evidence_project_version["tasks"] if item["stableKey"] == "demonstrate"
    )
    evidence_opportunity = evidence_project_version["evidenceOpportunities"][0]
    evidence_activity = client.request(
        "POST",
        "/api/v2/activities",
        {
            "title": "Completed Roadmap prerequisite",
            "category_stable_key": "project",
            "occurred_at": effective_at,
            "outcome_classification": "completed",
        },
        expected=201,
    )
    evidence_activity_link = client.request(
        "POST",
        "/api/v2/projects/activity-links",
        {
            "activity_id": evidence_activity["id"],
            "task_definition_id": evidence_task["id"],
            "provenance": "user_confirmed",
            "idempotency_key": f"roadmap-fixture-activity-link-{size}",
        },
        expected=201,
    )
    evidence_session_started_at = str(evidence_activity_link["createdAt"])
    evidence_occurred_at = (
        (
            datetime.fromisoformat(evidence_session_started_at.replace("Z", "+00:00"))
            + timedelta(milliseconds=2)
        )
        .isoformat()
        .replace("+00:00", "Z")
    )
    client.request(
        "POST",
        "/api/v2/sessions/manual",
        {
            "activity_id": evidence_activity["id"],
            "assistance_mode": "none",
            "started_at": evidence_session_started_at,
            "duration_ms": 1,
            "outcome": "completed",
            "contributions": [
                {
                    "target_type": "competency",
                    "competency_identity_id": competency_ids[evidence_source_index],
                    "relevance": "primary",
                },
                {
                    "target_type": "project",
                    "project_id": evidence_project["id"],
                    "project_version_id": evidence_project_version["id"],
                    "project_task_definition_id": evidence_task["id"],
                    "relevance": "primary",
                },
            ],
        },
        expected=201,
    )
    client.request(
        "POST",
        f"/api/v2/projects/{evidence_project['id']}/evidence",
        {
            "activity_project_task_link_id": evidence_activity_link["id"],
            "opportunity_id": evidence_opportunity["id"],
            "title": "Passing Roadmap prerequisite artifact",
            "description": "Representative independent project evidence.",
            "occurred_at": evidence_occurred_at,
            "artifact_hash": "a" * 64,
            "rubric_result": "passed",
            "idempotency_key": f"roadmap-fixture-project-evidence-{size}",
        },
        expected=201,
    )
    analysis = client.request(
        "POST",
        "/api/v2/analysis/runs",
        {"idempotency_key": f"roadmap-fixture-analysis-{size}", "purpose": "learning_control"},
        expected=201,
    )
    client.request(
        "POST",
        "/api/v2/today/generations",
        {
            "idempotency_key": f"roadmap-fixture-today-{size}",
            "analysis_snapshot_id": analysis["id"],
            "available_time_ms": 3600000,
            "context_costs": [],
        },
        expected=201,
    )
    projection = client.request("POST", "/api/v2/roadmap-projection/rebuild")
    if not isinstance(projection, dict):
        raise RuntimeError("Roadmap fixture projection was not an object.")
    if projection.get("layoutPolicyVersion") != "roadmap-layout/v3.0":
        raise RuntimeError("Roadmap fixture did not use the active v3 layout policy.")
    if len(projection.get("nodes", [])) != size:
        raise RuntimeError("Roadmap fixture projection lost nodes.")
    cross_domain = next(
        item
        for item in projection["nodes"]
        if item["competencyIdentityId"] == competency_ids[dimension_index]
    )
    target_dimensions = {
        item.get("dimensionKey") for item in cross_domain.get("profileTargets", [])
    }
    if target_dimensions != {"speaking", "reading"}:
        raise RuntimeError("Roadmap fixture lost cross-domain dimension targets.")
    if sum(not item.get("isTargeted", False) for item in projection["nodes"]) != foundation_count:
        raise RuntimeError("Roadmap fixture lost its shared foundation entry structure.")
    if not any(item.get("isToday", False) for item in projection["nodes"]):
        raise RuntimeError("Roadmap fixture did not expose a Today journey marker.")
    if not any(item.get("capability", {}).get("scopes") for item in projection["nodes"]):
        raise RuntimeError("Roadmap fixture did not expose a known current capability.")
    repeated = client.request("POST", "/api/v2/roadmap-projection/rebuild")
    if not isinstance(repeated, dict):
        raise RuntimeError("Repeated Roadmap projection was not an object.")

    def canonical_positions(value: JsonObject) -> dict[str, tuple[int, int]]:
        return {
            str(item["stableKey"]): (
                int(item["canonicalPosition"]["x"]),
                int(item["canonicalPosition"]["y"]),
            )
            for item in value["nodes"]
        }

    positions = canonical_positions(projection)
    if repeated.get("outputHash") != projection.get("outputHash"):
        raise RuntimeError("Repeated Roadmap fixture rebuild changed its output hash.")
    if canonical_positions(repeated) != positions:
        raise RuntimeError("Repeated Roadmap fixture rebuild changed canonical positions.")
    nodes = list(repeated["nodes"])
    for item in nodes:
        canonical = item["canonicalPosition"]
        if int(canonical["x"]) != 64 + int(item["layoutColumn"]) * 320:
            raise RuntimeError("Roadmap fixture violated the fixed canonical column slots.")
        if item["position"] != canonical:
            raise RuntimeError("Roadmap fixture unexpectedly contained a manual position.")
    for index, left in enumerate(nodes):
        left_position = left["canonicalPosition"]
        for right in nodes[index + 1 :]:
            right_position = right["canonicalPosition"]
            separated = (
                int(left_position["x"]) + 240 <= int(right_position["x"])
                or int(right_position["x"]) + 240 <= int(left_position["x"])
                or int(left_position["y"]) + 140 <= int(right_position["y"])
                or int(right_position["y"]) + 140 <= int(left_position["y"])
            )
            if not separated:
                raise RuntimeError(
                    "Roadmap fixture canonical slots overlap: "
                    f"{left['stableKey']} and {right['stableKey']}."
                )
    return repeated


def _verify_authority_read_parity(
    client: PublicApiClient,
    seed_authority: JsonObject,
    expected_authority: str,
) -> JsonObject:
    public_read = client.request("GET", "/api/v2/authority")
    if not isinstance(public_read, dict):
        raise RuntimeError("Authority public read did not return an object.")
    if public_read.get("canonicalLearningAuthority") != expected_authority:
        raise RuntimeError(
            f"Authority public read did not match the scenario expectation: {public_read!r}."
        )
    if public_read != seed_authority:
        raise RuntimeError(
            "Authority public read did not match the seed/activation response: "
            f"seed={seed_authority!r}, read={public_read!r}."
        )
    return public_read


def run_smoke(scenario: ScenarioName, timezone: str, clock_at: str | None) -> JsonObject:
    run = FixtureRun(scenario=scenario, timezone=timezone, clock_at=clock_at)
    success = False
    try:
        client = run.start()
        if scenario == "legacy-shell":
            authority = _seed_legacy_shell(client)
            expected_authority = "legacy_v1"
        else:
            authority, client = _seed_v2_shell(client, run)
            expected_authority = "v2"
        authority = _verify_authority_read_parity(client, authority, expected_authority)
        with urllib.request.urlopen(run.frontend_url, timeout=10) as response:
            if response.status != 200 or b'<div id="root"></div>' not in response.read():
                raise RuntimeError("The product frontend did not serve its application shell.")
        metadata_path = run.write_metadata(client, authority, expected_authority)
        result = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError("Fixture metadata was not an object.")
        success = True
        return result
    finally:
        run.finish(success=success)
        if not success:
            print(f"Fixture failure artifacts retained at {run.root}", file=sys.stderr)


def run_playwright(scenario: ScenarioName, timezone: str, clock_at: str | None) -> JsonObject:
    run = FixtureRun(scenario=scenario, timezone=timezone, clock_at=clock_at)
    success = False
    try:
        client = run.start()
        if scenario == "legacy-shell":
            authority = _seed_legacy_shell(client)
            expected_authority = "legacy_v1"
        else:
            authority, client = _seed_v2_shell(client, run)
            expected_authority = "v2"
        authority = _verify_authority_read_parity(client, authority, expected_authority)
        metadata_path = run.write_metadata(client, authority, expected_authority)
        assert run.root is not None
        run_root = run.root
        environment = os.environ.copy()
        environment.update(
            {
                "LCC_PRODUCT_BASE_URL": run.frontend_url,
                "LCC_PRODUCT_SCENARIO": scenario,
                "LCC_PRODUCT_ARTIFACT_DIR": str(run_root / "artifacts" / "playwright"),
            }
        )
        playwright_log = (run_root / "playwright.log").open("wb")
        try:
            result = subprocess.run(
                ["npm", "run", "test:e2e:product"],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=playwright_log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        finally:
            playwright_log.close()
        if result.returncode != 0:
            raise RuntimeError(f"Product Playwright failed; see {run_root / 'playwright.log'}.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise RuntimeError("Fixture metadata was not an object.")
        success = True
        return metadata
    finally:
        run.finish(success=success)
        if not success:
            print(f"Fixture failure artifacts retained at {run.root}", file=sys.stderr)


def run_roadmap_fixture(size: int, timezone: str, clock_at: str | None) -> JsonObject:
    if size not in {25, 100, 250}:
        raise ValueError("Roadmap fixture size must be 25, 100, or 250.")
    scenario = cast(ScenarioName, f"roadmap-{size}")
    run = FixtureRun(
        scenario=scenario,
        timezone=timezone,
        clock_at=clock_at,
    )
    success = False
    try:
        client = run.start()
        projection = _seed_roadmap_scale(client, run, size)
        authority = client.request("GET", "/api/v2/authority")
        if not isinstance(authority, dict):
            raise RuntimeError("Roadmap fixture authority response was not an object.")
        authority = _verify_authority_read_parity(client, authority, "v2")
        metadata_path = run.write_metadata(client, authority, "v2")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise RuntimeError("Roadmap fixture metadata was not an object.")
        metadata["roadmapProjection"] = {
            "size": size,
            "scopeKey": projection["scopeKey"],
            "layoutPolicyVersion": projection["layoutPolicyVersion"],
            "outputHash": projection["outputHash"],
            "nodeCount": len(projection["nodes"]),
            "edgeCount": len(projection["edges"]),
        }
        success = True
        return metadata
    finally:
        run.finish(success=success)
        if not success:
            print(f"Fixture failure artifacts retained at {run.root}", file=sys.stderr)


def run_roadmap_playwright(size: int, timezone: str, clock_at: str | None) -> JsonObject:
    if size not in {25, 100, 250}:
        raise ValueError("Roadmap fixture size must be 25, 100, or 250.")
    scenario = cast(ScenarioName, f"roadmap-{size}")
    run = FixtureRun(scenario=scenario, timezone=timezone, clock_at=clock_at)
    success = False
    try:
        client = run.start()
        projection = _seed_roadmap_scale(client, run, size)
        authority = client.request("GET", "/api/v2/authority")
        authority = _verify_authority_read_parity(client, authority, "v2")
        metadata_path = run.write_metadata(client, authority, "v2")
        assert run.root is not None
        environment = os.environ.copy()
        environment.update(
            {
                "LCC_PRODUCT_BASE_URL": run.frontend_url,
                "LCC_PRODUCT_SCENARIO": scenario,
                "LCC_PRODUCT_ARTIFACT_DIR": str(run.root / "artifacts" / "playwright"),
            }
        )
        with (run.root / "playwright.log").open("wb") as playwright_log:
            result = subprocess.run(
                ["npm", "run", "test:e2e:product", "--", "--grep", "Roadmap"],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=playwright_log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(f"Roadmap Playwright failed; see {run.root / 'playwright.log'}.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["roadmapProjection"] = {
            "size": size,
            "scopeKey": projection["scopeKey"],
            "layoutPolicyVersion": projection["layoutPolicyVersion"],
            "outputHash": projection["outputHash"],
            "nodeCount": len(projection["nodes"]),
            "edgeCount": len(projection["edges"]),
        }
        success = True
        return metadata
    finally:
        run.finish(success=success)
        if not success:
            print(f"Fixture failure artifacts retained at {run.root}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="Seed and verify one disposable shell scenario.")
    smoke.add_argument("--scenario", choices=("legacy-shell", "v2-shell"), required=True)
    smoke.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    smoke.add_argument(
        "--clock",
        default=DEFAULT_CLOCK,
        help="Timezone-aware RFC3339 instant, or 'real' to omit LCC_FIXTURE_CLOCK_AT.",
    )
    browser = subparsers.add_parser(
        "playwright", help="Run the isolated desktop/mobile product shell browser smoke."
    )
    browser.add_argument("--scenario", choices=("legacy-shell", "v2-shell"), required=True)
    browser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    browser.add_argument("--clock", default=DEFAULT_CLOCK)
    roadmap = subparsers.add_parser(
        "roadmap", help="Seed and verify a disposable public-API Roadmap scale fixture."
    )
    roadmap.add_argument("--size", type=int, choices=(25, 100, 250), required=True)
    roadmap.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    roadmap.add_argument("--clock", default=DEFAULT_CLOCK)
    roadmap_browser = subparsers.add_parser(
        "roadmap-playwright",
        help="Run Roadmap journey browser checks on a disposable scale fixture.",
    )
    roadmap_browser.add_argument("--size", type=int, choices=(25, 100, 250), default=25)
    roadmap_browser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    roadmap_browser.add_argument("--clock", default=DEFAULT_CLOCK)
    arguments = parser.parse_args()
    if arguments.command == "smoke":
        result = run_smoke(arguments.scenario, arguments.timezone, arguments.clock)
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "playwright":
        result = run_playwright(arguments.scenario, arguments.timezone, arguments.clock)
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "roadmap":
        result = run_roadmap_fixture(arguments.size, arguments.timezone, arguments.clock)
        print(
            json.dumps(
                {
                    "scenarioId": result["scenarioId"],
                    "fixtureHash": result["fixtureHash"],
                    "productionBuildHash": result["productionBuildHash"],
                    "repositoryRevision": result["repositoryRevision"],
                    "expectedAuthority": result["expectedAuthority"],
                    "roadmapProjection": result["roadmapProjection"],
                },
                sort_keys=True,
            )
        )
        return 0
    if arguments.command == "roadmap-playwright":
        result = run_roadmap_playwright(arguments.size, arguments.timezone, arguments.clock)
        print(
            json.dumps(
                {
                    "scenarioId": result["scenarioId"],
                    "fixtureHash": result["fixtureHash"],
                    "productionBuildHash": result["productionBuildHash"],
                    "roadmapProjection": result["roadmapProjection"],
                },
                sort_keys=True,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
