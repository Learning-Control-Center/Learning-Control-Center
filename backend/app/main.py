from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401
from app.analytics import router as analytics_router
from app.auth import router as auth_router
from app.config import get_settings
from app.database import SessionLocal, run_migrations
from app.errors import install_error_handlers
from app.import_export import router as import_export_router
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
    run_migrations()
    with SessionLocal() as db:
        get_or_create_profile(db)
        backfill_reports(db)
    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(report_scheduler(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await scheduler_task


app = FastAPI(
    title="Learning-Control-Center API",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url=None,
    lifespan=lifespan,
)
settings = get_settings()
if settings.environment != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

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
