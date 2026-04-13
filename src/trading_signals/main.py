"""Trading Signals – Main Entrypoint.

Starts FastAPI (with uvicorn) as the main process and
APScheduler as a background scheduler. The React SPA is
served as static files from the frontend/dist directory.

Usage:
    uv run python -m trading_signals.main
"""

from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from trading_signals.api.deps import set_scheduler
from trading_signals.api.routes.dashboard import router as dashboard_router
from trading_signals.api.routes.operations import router as operations_router
from trading_signals.api.routes.signals import router as signals_router
from trading_signals.api.routes.ticker import router as ticker_router
from trading_signals.api.routes.universe import router as universe_router
from trading_signals.scheduler.jobs import (
    run_analyst_ratings_collector,
    run_ark_holdings_collector,
    run_earnings_calendar_collector,
    run_form4_collector,
    run_form13f_collector,
    run_fundamentals_collector,
    run_politician_trades_collector,
    run_price_collector,
    run_technical_indicators_computer,
)
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def create_scheduler() -> BackgroundScheduler:
    """Create and configure the APScheduler instance.

    Uses BackgroundScheduler (non-blocking) so FastAPI can run
    as the main process while jobs execute in background threads.
    """
    scheduler = BackgroundScheduler(
        timezone="Europe/Berlin",
        job_defaults={
            "coalesce": True,       # Merge missed runs into one
            "max_instances": 1,     # Only one instance per job at a time
            "misfire_grace_time": 3600,  # 1 hour grace time for misfires
        },
    )

    # ── Price Collector: Daily at 22:15 (after US market close) ──
    scheduler.add_job(
        run_price_collector,
        CronTrigger(hour=22, minute=15),
        id="price_collector",
        name="Daily OHLCV Price Collector (Alpaca)",
    )

    # ── Technical Indicators: Daily at 22:30 (after prices) ──
    scheduler.add_job(
        run_technical_indicators_computer,
        CronTrigger(hour=22, minute=30),
        id="technical_indicators_computer",
        name="Daily Technical Indicators Computation",
    )

    # ── ARK Holdings: Daily at 23:00 (after arkfunds.io aggregation) ──
    scheduler.add_job(
        run_ark_holdings_collector,
        CronTrigger(hour=23, minute=0),
        id="ark_holdings",
        name="Daily ARK ETF Holdings Snapshot",
    )

    # ── Form 4 Insider Trades: Daily at 23:30 ──
    scheduler.add_job(
        run_form4_collector,
        CronTrigger(hour=23, minute=30),
        id="form4_collector",
        name="Daily SEC Form 4 Insider Trades",
    )

    # ── Analyst Ratings: Daily at 01:00 (night slot) ──
    scheduler.add_job(
        run_analyst_ratings_collector,
        CronTrigger(hour=1, minute=0),
        id="analyst_ratings_collector",
        name="Daily Analyst Ratings (yfinance)",
    )

    # ── Form 13F: Weekly Sunday at 10:00 ──
    scheduler.add_job(
        run_form13f_collector,
        CronTrigger(day_of_week="sun", hour=10, minute=0),
        id="form13f_collector",
        name="Weekly SEC Form 13F Institutional Holdings",
    )

    # ── Politician Trades: Weekly Sunday at 11:00 ──
    scheduler.add_job(
        run_politician_trades_collector,
        CronTrigger(day_of_week="sun", hour=11, minute=0),
        id="politician_trades_collector",
        name="Weekly Politician Trades (Senate eFD)",
    )

    # ── Fundamentals: Weekly Sunday at 01:00 ──
    scheduler.add_job(
        run_fundamentals_collector,
        CronTrigger(day_of_week="sun", hour=1, minute=0),
        id="fundamentals_collector",
        name="Weekly Fundamentals (yfinance)",
    )

    # ── Earnings Calendar: Weekly Sunday at 02:00 ──
    scheduler.add_job(
        run_earnings_calendar_collector,
        CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="earnings_calendar_collector",
        name="Weekly Earnings Calendar (yfinance)",
    )

    return scheduler


# ── Application Lifecycle ────────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown.

    Startup: Start the background scheduler.
    Shutdown: Gracefully stop the scheduler.
    """
    global _scheduler

    logger.info("=" * 60)
    logger.info("Trading Signals starting...")
    logger.info("=" * 60)

    _scheduler = create_scheduler()
    set_scheduler(_scheduler)
    _scheduler.start()

    # Log all registered jobs
    for job in _scheduler.get_jobs():
        logger.info(f"  Job: {job.name}")
        logger.info(f"    Trigger: {job.trigger}")
        logger.info(f"    Next run: {job.next_run_time}")

    logger.info("Scheduler started. API ready on port 8090.")

    yield  # Application is running

    logger.info("Shutdown signal received, stopping scheduler...")
    _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped. Goodbye.")


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
)

# ── API Routes ───────────────────────────────────────────────────────────

app.include_router(dashboard_router, prefix="/api/v1", tags=["Dashboard"])
app.include_router(universe_router, prefix="/api/v1", tags=["Universe"])
app.include_router(signals_router, prefix="/api/v1", tags=["Signals"])
app.include_router(ticker_router, prefix="/api/v1", tags=["Ticker"])
app.include_router(operations_router, prefix="/api/v1", tags=["Operations"])


@app.get("/api/v1/health")
def health_check():
    """Basic health check endpoint."""
    return {
        "status": "ok",
        "scheduler_running": _scheduler is not None and _scheduler.running,
    }


# ── Static Files (React SPA) ────────────────────────────────────────────
# Mount the React build output as the root static files.
# This MUST be the last mount to avoid catching API routes.

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True))
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
