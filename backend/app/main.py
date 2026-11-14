"""Spot the Spin FastAPI app: ``uvicorn app.main:app --app-dir backend``.

API routers first, then the static frontend mounted at "/" (so it never shadows /api).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import analyze, answer, dispute, health, metrics
from app.api import round as round_api
from app.api.errors import (
    BodySizeLimitMiddleware,
    SafetyMiddleware,
    install_error_handlers,
)
from app.config import REPO_ROOT, Settings, get_settings
from app.news import daily
from app.scheduler import Scheduler, utc_now
from app.security import ssrf
from app.security.ratelimit import RateLimiter, analyze_limit_from_env

log = logging.getLogger(__name__)

FRONTEND_DIR = REPO_ROOT / "frontend"
PAGES = ("analyze", "metrics", "label")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db(app.state.db_path)
    settings: Settings = app.state.settings
    if settings.core_mode == "live" and not settings.ai_router_api_key:
        log.warning("CORE_MODE=live without AI_ROUTER_API_KEY: analyze will answer 503")
    scheduler: Scheduler | None = None
    if settings.daily_job_enabled:
        scheduler = Scheduler(settings, app.state.daily_runner, clock=app.state.clock)
        scheduler.start()
        log.info(
            "daily job scheduled at %02d:%02d %s",
            settings.daily_job_hour,
            settings.daily_job_minute,
            settings.daily_tz,
        )
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()


def _page_redirect(page: str) -> Callable[[Request], RedirectResponse]:
    def redirect(request: Request) -> RedirectResponse:
        query = request.url.query
        return RedirectResponse(
            f"/{page}/" + (f"?{query}" if query else ""), status_code=307
        )

    return redirect


def create_app(
    settings: Settings | None = None,
    *,
    db_path: Path | None = None,
    metrics_path: Path | None = None,
    analyze_limit: int | None = None,
    fetch_url: Callable[[str], ssrf.FetchedPage] | None = None,
    frontend_dir: Path = FRONTEND_DIR,
    daily_runner: Callable[[date], daily.RunOutcome] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the app. Keyword arguments are for tests; production uses the defaults."""
    settings = settings or get_settings()
    app = FastAPI(
        title="Spot the Spin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.db_path = Path(db_path or settings.db_path)
    app.state.metrics_path = Path(metrics_path or metrics.LATEST_RESULTS)
    app.state.analyze_limiter = RateLimiter(analyze_limit or analyze_limit_from_env())
    app.state.fetch_url = fetch_url or ssrf.fetch_url
    app.state.clock = clock or utc_now
    app.state.daily_runner = daily_runner or partial(
        daily.run_daily_job, settings, app.state.db_path
    )

    install_error_handlers(app)
    for module in (health, round_api, answer, dispute, analyze, metrics):
        app.include_router(module.router)
    for page in PAGES:
        app.add_api_route(
            f"/{page}", _page_redirect(page), methods=["GET"], include_in_schema=False
        )

    # Static files last: the "/" mount would otherwise match every path.
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    # Added last = outermost: the safety net wraps everything, including the size limit.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SafetyMiddleware)
    return app


app = create_app()
