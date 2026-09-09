from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from app import models  # noqa: F401
from app.analytics import router as analytics_router
from app.auth import router as auth_router
from app.config import get_settings, is_strong_operator_secret
from app.database import SessionLocal, initialize_database, run_migrations
from app.errors import install_error_handlers
from app.import_export import router as import_export_router
from app.models import User
from app.operation_lock import exclusive_operation_lock
from app.recommendations import router as recommendation_router
from app.reflections import router as reflection_router
from app.reports import backfill_reports, report_scheduler
from app.reports import router as report_router
from app.roadmap import router as roadmap_router
from app.sessions import router as session_router
from app.settings_api import get_or_create_profile
from app.settings_api import router as settings_router
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
        stop_event = asyncio.Event()
        scheduler_task = asyncio.create_task(report_scheduler(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            await scheduler_task


settings = get_settings()


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
    elif configured.bootstrap_token:
        raise RuntimeError("Remove the bootstrap token after production initialization.")


app = FastAPI(
    title="Learning-Control-Center API",
    version="1.0.0",
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
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
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
