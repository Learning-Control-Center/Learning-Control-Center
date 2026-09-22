from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from app import models  # noqa: F401
from app.analysis.v3 import models as analysis_v3_models  # noqa: F401
from app.analysis.v3.api import router as analysis_v3_router
from app.analysis.v3.service import drain_analysis_invalidations, initialize_analysis_v3
from app.analytics import router as analytics_router
from app.auth import router as auth_router
from app.authority import models as authority_models  # noqa: F401
from app.authority.api import router as authority_router
from app.capability import drain_projection_invalidations
from app.capability import router as capability_router
from app.compatibility.v1.roadmap_graph import router as legacy_roadmap_graph_router
from app.config import get_settings, is_strong_operator_secret
from app.curriculum import models as curriculum_models  # noqa: F401
from app.curriculum.api import router as curriculum_router
from app.database import SessionLocal, initialize_database, run_migrations
from app.errors import install_error_handlers
from app.evidence import router as evidence_router
from app.frontend import FRONTEND_CSP, install_frontend_routes
from app.import_export import router as import_export_router
from app.learning_graph import models as learning_graph_models  # noqa: F401
from app.learning_graph.api import router as learning_graph_router
from app.models import User
from app.operation_lock import exclusive_operation_lock
from app.projects import models as project_models  # noqa: F401
from app.projects.api import router as project_router
from app.recommendation.v2 import models as recommendation_v2_models  # noqa: F401
from app.recommendation.v2.api import router as recommendation_v2_router
from app.recommendations import router as recommendation_router
from app.reflections import router as reflection_router
from app.reports import backfill_reports, report_scheduler
from app.reports import router as report_router
from app.roadmap import router as roadmap_router
from app.roadmap_projection import models as roadmap_projection_models  # noqa: F401
from app.roadmap_projection.api import router as roadmap_projection_router
from app.roadmap_projection.service import (
    drain_projection_invalidations as drain_roadmap_projection_invalidations,
)
from app.sessions import router as session_router
from app.settings_api import get_or_create_profile
from app.settings_api import router as settings_router
from app.time_utils import configure_process_clock
from app.today import models as today_models  # noqa: F401
from app.today.api import router as today_router
from app.v2_activities import router as v2_activity_router
from app.v2_profiles import router as v2_profile_router
from app.verification import router as verification_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    with exclusive_operation_lock(settings.database_url):
        run_migrations()
        initialize_database()
        with SessionLocal() as db:
            _validate_database_state(db)
            get_or_create_profile(db)
            backfill_reports(db)
            drain_projection_invalidations(db, recover_running=True)
            drain_roadmap_projection_invalidations(db, recover_running=True)
            initialize_analysis_v3(db)
            drain_analysis_invalidations(db, recover_running=True)
        stop_event = asyncio.Event()
        scheduler_task = asyncio.create_task(report_scheduler(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            await scheduler_task


settings = get_settings()
configure_process_clock(
    environment=settings.environment,
    fixed_utc_now=settings.fixture_clock_at,
    step_ms=settings.fixture_clock_step_ms,
)


def _validate_database_state(db: Session) -> None:
    configured = get_settings()
    if configured.environment != "production":
        return
    user_count = db.scalar(select(func.count(User.id))) or 0
    if user_count > 1:
        raise RuntimeError("Production database violates the single-user invariant.")
    if user_count == 0:
        if not is_strong_operator_secret(configured.bootstrap_token):
            raise RuntimeError("Uninitialized production requires a strong bootstrap token.")
    # A committed first user consumes bootstrap authority. The still-configured
    # token is inert because the bootstrap API refuses every request with a user.
    # Removing it from the root-owned environment remains an operator cleanup.


app = FastAPI(
    title="Learning-Control-Center API",
    version="1.0.1",
    openapi_url=None if settings.environment == "production" else "/api/v1/openapi.json",
    docs_url=None if settings.environment == "production" else "/api/v1/docs",
    redoc_url=None,
    lifespan=lifespan,
)
if settings.environment != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
else:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


@app.middleware("http")
async def security_boundary(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response: Response
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_values = request.headers.getlist("origin")
        origin = origin_values[0] if len(origin_values) == 1 else None
        allowed = settings.allowed_origins
        origin_required = settings.environment == "production"
        if (
            len(origin_values) > 1
            or (origin_required and origin is None)
            or (origin is not None and origin not in allowed)
        ):
            response = JSONResponse(
                status_code=403,
                content={
                    "error": {"code": "ORIGIN_INVALID", "message": "The request origin is invalid."}
                },
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.scope.get("lcc_frontend"):
        response.headers["Content-Security-Policy"] = FRONTEND_CSP
    else:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/v1/auth"):
        response.headers["Cache-Control"] = "no-store"
    return response


install_error_handlers(app)


@app.get("/api/v1/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


for api_router in (
    auth_router,
    roadmap_router,
    verification_router,
    session_router,
    analytics_router,
    recommendation_router,
    report_router,
    reflection_router,
    import_export_router,
    settings_router,
):
    app.include_router(api_router, prefix="/api/v1")

app.include_router(v2_profile_router, prefix="/api/v2")
app.include_router(v2_activity_router, prefix="/api/v2")
app.include_router(evidence_router, prefix="/api/v2")
app.include_router(capability_router, prefix="/api/v2")
app.include_router(curriculum_router, prefix="/api/v2")
app.include_router(project_router, prefix="/api/v2")
app.include_router(learning_graph_router, prefix="/api/v2")
app.include_router(roadmap_projection_router, prefix="/api/v2")
app.include_router(legacy_roadmap_graph_router, prefix="/api/v2")
app.include_router(analysis_v3_router, prefix="/api/v2")
app.include_router(recommendation_v2_router, prefix="/api/v2")
app.include_router(today_router, prefix="/api/v2")
app.include_router(authority_router, prefix="/api/v2")

install_frontend_routes(app, Path(__file__).resolve().parents[2])
