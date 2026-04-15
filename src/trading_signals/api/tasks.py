"""Background task management for long-running operations.

Manages backfill operations that run in background threads
with progress tracking accessible via API.
"""

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class TaskStatus(StrEnum):
    """Status of a background task."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackfillTask:
    """Tracks a single backfill operation."""
    task_id: str
    operation: str
    status: TaskStatus = TaskStatus.IDLE
    progress_pct: float = 0.0
    current_ticker: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    eta_seconds: float | None = None
    error: str | None = None
    total_items: int = 0
    processed_items: int = 0


class BackfillManager:
    """Manages long-running backfill operations in background threads.

    Provides start/stop semantics and progress tracking for:
    - Price backfill (historical prices from Alpaca)
    - Technical indicator backfill (recompute from prices)
    """

    def __init__(self):
        self._tasks: dict[str, BackfillTask] = {}
        self._lock = threading.Lock()

    def get_all_status(self) -> list[BackfillTask]:
        """Get status of all tasks."""
        with self._lock:
            return list(self._tasks.values())

    def get_status(self, task_id: str) -> BackfillTask | None:
        """Get status of a specific task."""
        with self._lock:
            return self._tasks.get(task_id)

    def is_operation_running(self, operation: str) -> bool:
        """Check if a specific operation type is already running."""
        with self._lock:
            return any(
                t.operation == operation and t.status == TaskStatus.RUNNING
                for t in self._tasks.values()
            )

    def start_price_backfill(self, start_date: str = "2021-01-01") -> str:
        """Start price backfill in a background thread.

        Args:
            start_date: Start date for backfill (ISO format).

        Returns:
            task_id for tracking progress.

        Raises:
            RuntimeError: If a price backfill is already running.
        """
        if self.is_operation_running("price_backfill"):
            raise RuntimeError("Price backfill is already running")

        task_id = f"bf_price_{uuid.uuid4().hex[:8]}"
        task = BackfillTask(task_id=task_id, operation="price_backfill")

        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._run_price_backfill,
            args=(task_id, start_date),
            daemon=True,
        )
        thread.start()
        return task_id

    def start_indicator_backfill(self) -> str:
        """Start technical indicator backfill in a background thread.

        Returns:
            task_id for tracking progress.
        """
        if self.is_operation_running("indicator_backfill"):
            raise RuntimeError("Indicator backfill is already running")

        task_id = f"bf_ta_{uuid.uuid4().hex[:8]}"
        task = BackfillTask(task_id=task_id, operation="indicator_backfill")

        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._run_indicator_backfill,
            args=(task_id,),
            daemon=True,
        )
        thread.start()
        return task_id

    def _update_progress(
        self,
        task_id: str,
        processed: int,
        total: int,
        current_ticker: str | None = None,
        start_time: float | None = None,
    ) -> None:
        """Thread-safe progress update for a running task."""
        task = self._tasks.get(task_id)
        if not task:
            return

        with self._lock:
            task.processed_items = processed
            task.total_items = total
            task.current_ticker = current_ticker
            task.progress_pct = (processed / total * 100) if total > 0 else 0.0

            # ETA calculation
            if start_time and processed > 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed  # items per second
                remaining = total - processed
                task.eta_seconds = remaining / rate if rate > 0 else None

    def _run_price_backfill(self, task_id: str, start_date: str) -> None:
        """Execute price backfill with per-batch progress tracking."""
        task = self._tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)

        try:
            from trading_signals.collectors.prices_alpaca import (
                BATCH_SIZE,
                PriceCollectorAlpaca,
                _fetch_bars_batch,
            )
            from trading_signals.db.models.prices import PriceDaily
            from trading_signals.db.models.universe import Universe
            from trading_signals.db.session import get_session

            # Calculate lookback days from start_date
            from datetime import date as date_type

            from sqlalchemy import select
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            start = date_type.fromisoformat(start_date)
            end_date = date_type.today()
            lookback = (end_date - start).days

            # Get ticker list
            with get_session() as session:
                stmt = (
                    select(Universe.ticker)
                    .where(Universe.is_active.is_(True))
                    .order_by(Universe.ticker)
                )
                tickers = [row[0] for row in session.execute(stmt).all()]

            total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
            task.total_items = total_batches

            logger.info(
                f"[Backfill {task_id}] Starting price backfill: "
                f"{len(tickers)} tickers in {total_batches} batches, "
                f"{lookback} days lookback"
            )

            start_time = time.time()
            collector = PriceCollectorAlpaca(lookback_days=lookback)
            total_written = 0

            # Process batch by batch with progress updates
            for i in range(0, len(tickers), BATCH_SIZE):
                batch = tickers[i : i + BATCH_SIZE]
                batch_num = (i // BATCH_SIZE) + 1

                self._update_progress(
                    task_id,
                    processed=batch_num - 1,
                    total=total_batches,
                    current_ticker=f"Batch {batch_num}/{total_batches} ({batch[0]}...{batch[-1]})",
                    start_time=start_time,
                )

                try:
                    bars = _fetch_bars_batch(
                        symbols=batch,
                        start=start.isoformat(),
                        end=end_date.isoformat(),
                        headers=collector._headers,
                    )

                    # Store the batch
                    with get_session() as session:
                        batch_written = 0
                        for ticker, ticker_bars in bars.items():
                            for bar in ticker_bars:
                                close_val = bar.get("c")
                                if close_val is None:
                                    continue
                                from trading_signals.collectors.prices_alpaca import (
                                    _parse_bar_timestamp,
                                )
                                trade_date = _parse_bar_timestamp(bar.get("t", ""))
                                if trade_date is None:
                                    continue

                                stmt = (
                                    pg_insert(PriceDaily)
                                    .values(
                                        ticker=ticker,
                                        trade_date=trade_date,
                                        open=bar.get("o"),
                                        high=bar.get("h"),
                                        low=bar.get("l"),
                                        close=close_val,
                                        adj_close=close_val,
                                        volume=bar.get("v"),
                                        source="alpaca",
                                        is_extrapolated=False,
                                    )
                                    .on_conflict_do_nothing(
                                        index_elements=["ticker", "trade_date"]
                                    )
                                )
                                result = session.execute(stmt)
                                if result.rowcount > 0:
                                    batch_written += 1

                        total_written += batch_written

                    logger.info(
                        f"[Backfill {task_id}] Batch {batch_num}/{total_batches}: "
                        f"{batch_written} records written"
                    )

                except Exception as e:
                    logger.error(
                        f"[Backfill {task_id}] Batch {batch_num} failed: {e}"
                    )

            # Final update
            self._update_progress(
                task_id,
                processed=total_batches,
                total=total_batches,
                start_time=start_time,
            )
            task.progress_pct = 100.0
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)
            task.current_ticker = None

            logger.info(
                f"[Backfill {task_id}] Price backfill completed: "
                f"{total_written} records in "
                f"{time.time() - start_time:.0f}s"
            )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(UTC)
            logger.error(f"[Backfill {task_id}] Price backfill failed: {e}")

    def _run_indicator_backfill(self, task_id: str) -> None:
        """Execute technical indicator backfill with per-ticker progress."""
        task = self._tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)

        try:
            from sqlalchemy import select

            from trading_signals.db.models.universe import Universe
            from trading_signals.db.session import get_session
            from trading_signals.derived.technical_indicators import (
                TechnicalIndicatorsComputer,
            )

            # Get ticker list for progress tracking
            with get_session() as session:
                stmt = (
                    select(Universe.ticker)
                    .where(Universe.is_active.is_(True))
                    .order_by(Universe.ticker)
                )
                tickers = [row[0] for row in session.execute(stmt).all()]

            task.total_items = len(tickers)

            logger.info(
                f"[Backfill {task_id}] Starting TA indicator backfill: "
                f"{len(tickers)} tickers"
            )

            start_time = time.time()
            total_written = 0
            errors = 0

            with get_session() as session:
                computer = TechnicalIndicatorsComputer(session)

                # Pre-load SPY prices
                computer._spy_df = computer._load_price_history("SPY")

                for i, ticker in enumerate(tickers, 1):
                    self._update_progress(
                        task_id,
                        processed=i - 1,
                        total=len(tickers),
                        current_ticker=ticker,
                        start_time=start_time,
                    )

                    try:
                        written = computer._compute_backfill(ticker)
                        total_written += written
                    except Exception as e:
                        errors += 1
                        logger.error(
                            f"[Backfill {task_id}] Error for {ticker}: {e}"
                        )
                        continue

                    # Flush every 50 tickers to avoid memory buildup
                    if i % 50 == 0:
                        session.flush()
                        logger.info(
                            f"[Backfill {task_id}] Progress: "
                            f"{i}/{len(tickers)} tickers, "
                            f"{total_written} records"
                        )

                session.flush()

            # Final update
            self._update_progress(
                task_id,
                processed=len(tickers),
                total=len(tickers),
                start_time=start_time,
            )
            task.progress_pct = 100.0
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)
            task.current_ticker = None

            logger.info(
                f"[Backfill {task_id}] TA backfill completed: "
                f"{total_written} records in {time.time() - start_time:.0f}s "
                f"({errors} errors)"
            )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(UTC)
            logger.error(f"[Backfill {task_id}] TA backfill failed: {e}")

    def start_sector_enrichment(self) -> str:
        """Start sector/industry enrichment in a background thread.

        Fetches missing sector/industry data from yfinance for all
        active tickers that lack this information.

        Returns:
            task_id for tracking progress.
        """
        if self.is_operation_running("sector_enrichment"):
            raise RuntimeError("Sector enrichment is already running")

        task_id = f"enrich_{uuid.uuid4().hex[:8]}"
        task = BackfillTask(task_id=task_id, operation="sector_enrichment")

        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._run_sector_enrichment,
            args=(task_id,),
            daemon=True,
        )
        thread.start()
        return task_id

    def _run_sector_enrichment(self, task_id: str) -> None:
        """Execute sector enrichment with per-ticker progress."""
        task = self._tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)

        try:
            from sqlalchemy import select, update

            import yfinance as yf

            from trading_signals.db.models.universe import Universe
            from trading_signals.db.session import get_session

            # Find tickers with missing sector
            with get_session() as session:
                stmt = (
                    select(Universe.ticker)
                    .where(Universe.is_active.is_(True))
                    .where(
                        (Universe.sector.is_(None)) | (Universe.sector == "")
                    )
                    .order_by(Universe.ticker)
                )
                missing = [row[0] for row in session.execute(stmt).all()]

            if not missing:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC)
                task.progress_pct = 100.0
                logger.info(f"[Enrichment {task_id}] No tickers need enrichment")
                return

            task.total_items = len(missing)
            logger.info(
                f"[Enrichment {task_id}] Starting sector enrichment: "
                f"{len(missing)} tickers"
            )

            start_time = time.time()
            enriched: list[dict] = []

            # Process ticker by ticker with progress updates
            for i, ticker_str in enumerate(missing):
                self._update_progress(
                    task_id,
                    processed=i,
                    total=len(missing),
                    current_ticker=ticker_str,
                    start_time=start_time,
                )

                try:
                    t = yf.Ticker(ticker_str)
                    info = t.info
                    sector = info.get("sector") if info else None
                    industry = info.get("industry") if info else None
                    quote_type = info.get("quoteType") if info else None

                    enriched.append({
                        "ticker": ticker_str,
                        "sector": sector,
                        "industry": industry,
                        "quote_type": quote_type,
                    })
                except Exception as e:
                    logger.debug(
                        f"[Enrichment {task_id}] Failed for {ticker_str}: "
                        f"{type(e).__name__}: {e}"
                    )

                # Rate-limit: 0.5s between tickers
                time.sleep(0.5)

                # Batch pause every 50 tickers
                if (i + 1) % 50 == 0 and i < len(missing) - 1:
                    time.sleep(3.0)

            # Update universe (with ETF deactivation)
            updated = 0
            deactivated_etfs: list[str] = []
            with get_session() as session:
                for record in enriched:
                    ticker = record["ticker"]
                    quote_type = record.get("quote_type", "")

                    # Learned ETF filter: deactivate non-equity tickers
                    if quote_type and quote_type.upper() != "EQUITY":
                        session.execute(
                            update(Universe)
                            .where(Universe.ticker == ticker)
                            .values(is_active=False)
                        )
                        deactivated_etfs.append(ticker)
                        logger.warning(
                            f"[Enrichment {task_id}] Deactivating {ticker}: "
                            f"quoteType={quote_type} (not EQUITY)"
                        )
                        continue

                    if record.get("sector") or record.get("industry"):
                        session.execute(
                            update(Universe)
                            .where(Universe.ticker == ticker)
                            .values(
                                sector=record.get("sector"),
                                industry=record.get("industry"),
                            )
                        )
                        updated += 1

            # Final update
            self._update_progress(
                task_id,
                processed=len(missing),
                total=len(missing),
                start_time=start_time,
            )
            task.progress_pct = 100.0
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)
            task.current_ticker = None

            if deactivated_etfs:
                logger.info(
                    f"[Enrichment {task_id}] Deactivated "
                    f"{len(deactivated_etfs)} non-equity tickers: "
                    f"{deactivated_etfs}"
                )

            logger.info(
                f"[Enrichment {task_id}] Completed: "
                f"{updated}/{len(missing)} tickers enriched in "
                f"{time.time() - start_time:.0f}s"
            )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(UTC)
            logger.error(f"[Enrichment {task_id}] Failed: {e}")


# Singleton instance
backfill_manager = BackfillManager()
