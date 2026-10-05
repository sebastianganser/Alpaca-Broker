"""Trading Signals – Main Entrypoint.

Starts FastAPI (with uvicorn) as the main process and
APScheduler as a background scheduler. The React SPA is
served as static files from the frontend/dist directory.

Usage:
    uv run python -m trading_signals.main
"""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from trading_signals.api.deps import require_write_access, set_scheduler
from trading_signals.api.job_tracker import job_tracker
from trading_signals.api.routes.analysis import router as analysis_router
from trading_signals.api.routes.dashboard import router as dashboard_router
from trading_signals.api.routes.features import router as features_router
from trading_signals.api.routes.logs import router as logs_router
from trading_signals.api.routes.operations import router as operations_router
from trading_signals.api.routes.signals import router as signals_router
from trading_signals.api.routes.ticker import router as ticker_router
from trading_signals.api.routes.universe import router as universe_router
from trading_signals.scheduler.runner import (
    mark_stale_runs,
    register_alert_listeners,
    schedule_startup_catchup,
)
from trading_signals.scheduler.setup import create_scheduler
from trading_signals.utils.logging import get_logger, setup_logging


def _configured_log_level() -> str:
    """LOG_LEVEL from settings (falls back to env / INFO if settings are incomplete)."""
    try:
        from trading_signals.config import get_settings

        return get_settings().LOG_LEVEL
    except Exception:
        return os.environ.get("LOG_LEVEL", "INFO")


setup_logging(_configured_log_level())
logger = get_logger(__name__)

__all__ = ["app", "create_scheduler", "main"]

#: Max seconds to wait for running jobs on shutdown (Docker stop_grace_period
#: must be larger, see infra/docker-compose.yml).
SHUTDOWN_TIMEOUT_SECONDS = 25


# ── Application Lifecycle ────────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


def _shutdown_scheduler(scheduler: BackgroundScheduler, timeout: float) -> bool:
    """Stop the scheduler, waiting at most ``timeout`` s for running jobs.

    Returns True if all jobs finished in time. Jobs still running are killed
    with the process; their RUNNING log rows are marked stale on next start.
    """
    worker = threading.Thread(
        target=lambda: scheduler.shutdown(wait=True),
        daemon=True,
        name="scheduler-shutdown",
    )
    worker.start()
    worker.join(timeout)
    return not worker.is_alive()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown.

    Startup: Safety check, start the background scheduler, recover orphaned
    runs and schedule catch-up runs for missed critical jobs.
    Shutdown: Gracefully stop the scheduler (bounded wait).
    """
    global _scheduler

    logger.info("=" * 60)
    logger.info("Trading Signals starting...")
    logger.info("=" * 60)

    # SAFETY: refuse to start against a live-trading endpoint (fail hard)
    from trading_signals.config import get_settings

    get_settings().validate_alpaca_safety()

    _scheduler = create_scheduler()
    set_scheduler(_scheduler)
    job_tracker.register(_scheduler)
    register_alert_listeners(_scheduler)
    _scheduler.start()

    try:
        mark_stale_runs()
    except Exception as e:
        logger.error(f"Could not mark stale collection_log runs: {e}")
    try:
        schedule_startup_catchup(_scheduler)
    except Exception as e:
        logger.error(f"Startup catch-up failed: {e}")

    # Log all registered jobs
    for job in _scheduler.get_jobs():
        logger.info(f"  Job: {job.name}")
        logger.info(f"    Trigger: {job.trigger}")
        logger.info(
            f"    Next run: {job.next_run_time or 'paused (via nightly_chain)'}"
        )

    logger.info("Scheduler started. API ready on port 8090.")

    yield  # Application is running

    logger.info("Shutdown signal received, stopping scheduler...")
    if _shutdown_scheduler(_scheduler, SHUTDOWN_TIMEOUT_SECONDS):
        logger.info("Scheduler stopped. Goodbye.")
    else:
        logger.warning(
            f"Jobs still running after {SHUTDOWN_TIMEOUT_SECONDS}s – exiting anyway "
            "(runs will be marked stale on next start)."
        )


# ── FastAPI Application ──────────────────────────────────────────────────

app = FastAPI(
    title="Trading Signals",
    description="Signal Warehouse Dashboard & API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware (for local dev with Vite on different port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8090"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# ── API Routes ───────────────────────────────────────────────────────────
# Mutating requests on /ops/* and /analysis/trigger require X-API-Key (if
# API_KEY is set) or X-Requested-With (CSRF guard); GETs stay open.
_write_guard = [Depends(require_write_access)]

app.include_router(
    analysis_router, prefix="/api/v1", tags=["Analysis"], dependencies=_write_guard
)
app.include_router(dashboard_router, prefix="/api/v1", tags=["Dashboard"])
app.include_router(features_router, prefix="/api/v1", tags=["Features"])
app.include_router(universe_router, prefix="/api/v1", tags=["Universe"])
app.include_router(signals_router, prefix="/api/v1", tags=["Signals"])
app.include_router(ticker_router, prefix="/api/v1", tags=["Ticker"])
app.include_router(
    operations_router, prefix="/api/v1", tags=["Operations"], dependencies=_write_guard
)
app.include_router(logs_router, prefix="/api/v1", tags=["Logs"])


def _db_ping() -> bool:
    try:
        from sqlalchemy import text

        from trading_signals.db.session import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@app.get("/api/v1/health")
def health_check():
    """Health check: scheduler running + DB reachable (503 if degraded).

    Used by the Docker HEALTHCHECK.
    """
    scheduler_running = _scheduler is not None and _scheduler.running
    db_ok = _db_ping()
    healthy = scheduler_running and db_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "scheduler_running": scheduler_running,
            "db_connected": db_ok,
        },
    )


# ── Static Files (React SPA) ────────────────────────────────────────────
# Mount static assets (JS, CSS, images) and add a catch-all fallback
# that serves index.html for any client-side route (SPA pattern).
# This MUST be after all API routes to avoid catching them.

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    # Serve static assets (JS, CSS, fonts, images)
    app.mount(
        "/assets",
        StaticFiles(directory=str(_frontend_dist / "assets")),
        name="assets",
    )

    # SPA fallback: serve index.html for all non-API routes
    _index_html = _frontend_dist / "index.html"

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA index.html for all client-side routes.

        This enables direct URL access and Ctrl+F5 refresh on any page
        (e.g. /universe, /settings, /ticker/AAPL).
        """
        from fastapi.responses import FileResponse

        # If a real file exists in dist/ (e.g. favicon.ico), serve it
        file_path = _frontend_dist / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html and let React Router handle it
        return FileResponse(_index_html)

    logger.info(f"Serving frontend from {_frontend_dist}")
else:
    logger.warning(
        f"Frontend dist not found at {_frontend_dist}. "
        "API-only mode (run 'npm run build' in frontend/)."
    )


# ── Main Entry Point ────────────────────────────────────────────────────


def main() -> None:
    """Start the application with uvicorn."""
    import uvicorn

    uvicorn.run(
        "trading_signals.main:app",
        host="0.0.0.0",
        port=8090,
        log_level="info",
        # reload=True,  # Enable for development only
    )


if __name__ == "__main__":
    main()
