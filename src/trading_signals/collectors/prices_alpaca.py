"""Price Collector using Alpaca Market Data API.

Downloads daily OHLCV data for all active tickers in the universe.
Uses the multi-symbol bars endpoint for efficient batch downloads.

Features:
  - Multi-symbol batch endpoint (100 tickers per request)
  - Split + dividend adjusted prices (adjustment=all)
  - Feed from ``ALPACA_DATA_FEED`` (default ``sip`` = consolidated tape;
    free tier allows SIP when ``end`` is >= 15 min in the past). If Alpaca
    rejects SIP for the subscription (403/422), the run falls back to
    ``iex`` once and is reported as PARTIAL.
  - Gap detection & repair before each run (re-fetch via Alpaca,
    extrapolation only for old gaps after a successful empty answer)
  - Lookback window is upserted (ON CONFLICT DO UPDATE) so incomplete
    early bars are corrected
  - Re-adjustment detection (C3): with ``adjustment=all`` Alpaca rewrites
    the whole history after splits/dividends. If the freshly fetched
    lookback bars deviate > 0.1 % from stored ``adj_close`` for a ticker,
    that ticker's full history (from ``data_start_date()``) is re-downloaded
    and upserted, then ``derived.recompute.recompute_after_price_refresh``
    recomputes TA + targets.

Data endpoint:
  GET https://data.alpaca.markets/v2/stocks/bars
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import bulk_insert, release_transaction
from trading_signals.collectors._parsing import parse_date
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.gap_detector import GapDetector, GapRepairResult
from trading_signals.config import get_settings
from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import get_logger
from trading_signals.utils.market_calendar import last_completed_session
from trading_signals.utils.retention import data_start_date
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Alpaca Market Data base URL (different from paper trading API!)
DATA_BASE_URL = "https://data.alpaca.markets"

# Number of tickers per Alpaca batch request
BATCH_SIZE = 100

# Number of days to look back for daily price fetching
DEFAULT_LOOKBACK_DAYS = 10

# Relative deviation between stored and fresh adjusted close that signals
# a re-adjustment of the history (split / dividend)
ADJ_DEVIATION_THRESHOLD = 0.001

# Free-tier SIP data must end at least 15 minutes in the past
SIP_DELAY_MINUTES = 16

# Gap dates younger than this are never extrapolated (data may still come)
GAP_EXTRAPOLATE_MIN_AGE_DAYS = 7

#: Columns overwritten on conflict (prices are always fully replaced)
PRICE_UPDATE_COLS = (
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "source",
    "is_extrapolated",
    "fetched_at",
)


class AlpacaSubscriptionError(RuntimeError):
    """Alpaca rejected the requested feed for this subscription."""


def _is_subscription_error(exc: Exception) -> bool:
    if not isinstance(exc, requests.exceptions.HTTPError) or exc.response is None:
        return False
    if exc.response.status_code not in (403, 422):
        return False
    try:
        text = (exc.response.text or "").lower()
    except Exception:
        text = ""
    return "subscription" in text or "sip" in text or "permit" in text or not text


def bars_to_rows(
    ticker: str,
    bars: list[dict],
    max_date: date | None = None,
    min_date: date | None = None,
) -> list[dict]:
    """Convert Alpaca bar dicts to ``prices_daily`` row dicts.

    Bars without close or timestamp are skipped; dates outside
    [min_date, max_date] are dropped (e.g. today's incomplete bar).
    """
    rows = []
    for bar in bars:
        close_val = bar.get("c")
        if close_val is None:
            continue
        trade_date = _parse_bar_timestamp(bar.get("t", ""))
        if trade_date is None:
            continue
        if max_date and trade_date > max_date:
            continue
        if min_date and trade_date < min_date:
            continue
        rows.append(
            {
                "ticker": ticker,
                "trade_date": trade_date,
                "open": bar.get("o"),
                "high": bar.get("h"),
                "low": bar.get("l"),
                "close": close_val,
                "adj_close": close_val,  # adjustment=all -> close IS adjusted
                "volume": bar.get("v"),
                "source": "alpaca",
                "is_extrapolated": False,
                "fetched_at": func.now(),
            }
        )
    return rows


def upsert_price_rows(session: Session, rows: list[dict]) -> int:
    """Upsert price rows (ON CONFLICT (ticker, trade_date) DO UPDATE)."""
    return bulk_insert(
        session,
        PriceDaily,
        rows,
        ["ticker", "trade_date"],
        update_cols=PRICE_UPDATE_COLS,
    )


class PriceCollectorAlpaca(BaseCollector):
    """Collect daily OHLCV prices from Alpaca Market Data API."""

    name = "prices_alpaca"

    def __init__(
        self, lookback_days: int = DEFAULT_LOOKBACK_DAYS, feed: str | None = None
    ) -> None:
        """Initialize the Alpaca price collector.

        Args:
            lookback_days: Number of calendar days to look back.
                           Default 10 provides buffer for weekends/holidays.
            feed: Override for ``ALPACA_DATA_FEED`` ("sip" or "iex").
        """
        self.lookback_days = lookback_days
        settings = get_settings()
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }
        self.feed = (feed or getattr(settings, "ALPACA_DATA_FEED", "") or "sip").lower()
        self._fell_back = False
        self.refresh_tickers: set[str] = set()

    # ── HTTP helpers ─────────────────────────────────────────────────
    def _end_param(self) -> str:
        """RFC-3339 end timestamp (SIP: now - 16 min)."""
        delay = SIP_DELAY_MINUTES if self.feed == "sip" else 0
        end = datetime.now(UTC) - timedelta(minutes=delay)
        return end.strftime("%Y-%m-%dT%H:%M:%SZ")

    def fetch_bars(self, symbols: list[str], start: date) -> dict[str, list[dict]]:
        """Fetch bars for ``symbols`` since ``start`` with SIP→IEX fallback."""
        try:
            return _fetch_bars_batch(
                symbols=symbols,
                start=start.isoformat(),
                end=self._end_param(),
                headers=self._headers,
                feed=self.feed,
            )
        except requests.exceptions.HTTPError as e:
            if self.feed != "sip" or not _is_subscription_error(e):
                raise
            logger.error(
                f"[{self.name}] Alpaca rejected feed=sip "
                f"(HTTP {e.response.status_code}) – falling back to feed=iex "
                f"for this run. Check subscription / ALPACA_DATA_FEED."
            )
            self.feed = "iex"
            self._fell_back = True
            self.mark_partial("SIP feed not permitted – fell back to IEX")
            return _fetch_bars_batch(
                symbols=symbols,
                start=start.isoformat(),
                end=self._end_param(),
                headers=self._headers,
                feed=self.feed,
            )

    def _fetch_many(
        self, tickers: list[str], start: date, label: str
    ) -> tuple[dict[str, list[dict]], int]:
        """Fetch ``tickers`` in batches. Returns (bars per ticker, failed tickers)."""
        out: dict[str, list[dict]] = {}
        failed = 0
        total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i : i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            logger.info(
                f"[{self.name}] {label} batch {batch_num}/{total_batches}: "
                f"{len(batch)} tickers"
            )
            try:
                bars = self.fetch_bars(batch, start)
                self.record_success()
                for ticker, ticker_bars in bars.items():
                    if ticker_bars:
                        out[ticker] = ticker_bars
            except Exception as e:
                self.record_error()
                logger.error(
                    f"[{self.name}] {label} batch {batch_num} failed: "
                    f"{type(e).__name__}: {e}"
                )
                failed += len(batch)
        return out, failed

    # ── Template-method steps ────────────────────────────────────────
    def check_and_repair_gaps(self, session: Session) -> GapRepairResult | None:
        """Detect missing sessions (retention window) and re-fetch them via Alpaca."""
        try:
            tickers = self.get_active_tickers(session)
            end = last_completed_session()
            detector = GapDetector(session, source="alpaca")
            gaps = detector.detect_gaps_bulk(
                tickers, start_bound=data_start_date(), end=end
            )
            release_transaction(session)
        except Exception as e:
            logger.warning(
                f"[{self.name}] Gap detection failed: {type(e).__name__}: {e}"
            )
            return None
        if not gaps:
            return GapRepairResult()
        logger.info(
            f"[{self.name}] Gap detection: "
            f"{sum(len(v) for v in gaps.values())} missing "
            f"sessions across {len(gaps)} tickers"
        )
        return detector.repair_gaps(
            gaps,
            batch_fetch_fn=self._gap_fetch,
            batch_size=BATCH_SIZE,
            min_extrapolate_age_days=GAP_EXTRAPOLATE_MIN_AGE_DAYS,
        )

    def _gap_fetch(self, tickers: list[str], start: date, end: date):
        """Batch fetch adapter for GapDetector → {ticker: DataFrame}."""
        import pandas as pd

        bars = self.fetch_bars(tickers, start)
        out = {}
        for ticker, ticker_bars in bars.items():
            records = []
            for bar in ticker_bars:
                d = _parse_bar_timestamp(bar.get("t", ""))
                if d is None or d >= end:
                    continue
                records.append(
                    {
                        "date": pd.Timestamp(d),
                        "Open": bar.get("o"),
                        "High": bar.get("h"),
                        "Low": bar.get("l"),
                        "Close": bar.get("c"),
                        "Adj Close": bar.get("c"),
                        "Volume": bar.get("v"),
                    }
                )
            if records:
                out[ticker] = pd.DataFrame(records).set_index("date")
        return out

    def fetch(self, session: Session) -> dict[str, list[dict]]:
        """Fetch OHLCV data for all active tickers in batches.

        1. Lookback window for all tickers.
        2. Compare with stored adj_close (one short read) → tickers whose
           history was re-adjusted by Alpaca.
        3. Re-download the full history (from ``data_start_date()``) for those.

        Returns:
            Dict mapping ticker -> list of bar dicts.
        """
        self.refresh_tickers = set()
        tickers = self.get_active_tickers(session)
        last_session = last_completed_session()
        start_date = last_session - timedelta(days=self.lookback_days)
        logger.info(
            f"[{self.name}] Fetching {len(tickers)} tickers "
            f"(lookback={self.lookback_days}d, feed={self.feed})"
        )

        all_data, failed_count = self._fetch_many(tickers, start_date, "Daily")

        # Re-adjustment check against stored prices (short read txn).
        # Skipped after an IEX fallback: IEX closes differ from stored SIP
        # closes and would trigger needless full-history refreshes.
        try:
            if not self._fell_back:
                self.refresh_tickers = self._detect_readjusted(
                    session, all_data, last_session
                )
        finally:
            release_transaction(session)

        if self.refresh_tickers:
            refresh = sorted(self.refresh_tickers)
            logger.warning(
                f"[{self.name}] Adjustment change detected for {len(refresh)} tickers "
                f"{refresh[:20]} – re-downloading full history from {data_start_date()}"
            )
            full, _failed = self._fetch_many(
                refresh, data_start_date(), "History refresh"
            )
            for ticker in refresh:
                if ticker in full:
                    all_data[ticker] = full[ticker]
                else:
                    self.refresh_tickers.discard(ticker)

        logger.info(
            f"[{self.name}] Fetched data for {len(all_data)} tickers. "
            f"Failed: {failed_count}"
        )
        return all_data

    def _detect_readjusted(
        self, session: Session, data: dict[str, list[dict]], last_session: date
    ) -> set[str]:
        """Tickers whose fresh bars deviate > threshold from stored adj_close."""
        fresh: dict[tuple[str, date], float] = {}
        for ticker, bars in data.items():
            for row in bars_to_rows(ticker, bars, max_date=last_session):
                try:
                    fresh[(ticker, row["trade_date"])] = float(row["adj_close"])
                except (TypeError, ValueError):
                    continue
        if not fresh:
            return set()
        min_d = min(k[1] for k in fresh)
        stmt = (
            select(PriceDaily.ticker, PriceDaily.trade_date, PriceDaily.adj_close)
            .where(PriceDaily.ticker.in_(list(data)))
            .where(PriceDaily.trade_date >= min_d)
            .where(PriceDaily.trade_date <= last_session)
            .where(PriceDaily.is_extrapolated.is_(False))
        )
        flagged: set[str] = set()
        for ticker, trade_date, stored in session.execute(stmt).all():
            new = fresh.get((ticker, trade_date))
            if new is None or stored is None:
                continue
            stored_f = float(stored)
            if stored_f == 0:
                continue
            if abs(new - stored_f) / abs(stored_f) > ADJ_DEVIATION_THRESHOLD:
                flagged.add(ticker)
        return flagged

    def store(self, session: Session, data: dict[str, list[dict]]) -> tuple[int, int]:
        """Store fetched price data in the database.

        Upserts (ON CONFLICT DO UPDATE) – the lookback window and refreshed
        histories always replace stored values. Afterwards triggers the
        downstream recompute for re-adjusted tickers.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = 0
        rows: list[dict] = []
        last_session = last_completed_session()
        min_date = data_start_date()
        for ticker, bars in data.items():
            records_fetched += len(bars)
            rows.extend(
                bars_to_rows(ticker, bars, max_date=last_session, min_date=min_date)
            )

        records_written = upsert_price_rows(session, rows) if rows else 0
        session.flush()
        logger.info(
            f"[{self.name}] Upserted {records_written}/{records_fetched} records"
        )

        if self.refresh_tickers:
            self._recompute(session, sorted(self.refresh_tickers))
        return records_fetched, records_written

    def _recompute(self, session: Session, tickers: list[str]) -> None:
        """Recompute TA + targets for refreshed tickers (savepoint-protected)."""
        try:
            from trading_signals.derived.recompute import recompute_after_price_refresh

            with session.begin_nested():
                stats = recompute_after_price_refresh(session, tickers)
            logger.info(f"[{self.name}] Recompute after price refresh: {stats}")
        except Exception as e:
            logger.error(
                f"[{self.name}] Recompute after price refresh failed for "
                f"{len(tickers)} tickers: {type(e).__name__}: {e}"
            )
            self.mark_partial("recompute after price refresh failed")

    # ── Utilities used by onboarder / repair scripts ─────────────────
    def refresh_full_history(
        self, session: Session, tickers: list[str], recompute: bool = True
    ) -> int:
        """Download full history (from ``data_start_date()``) and upsert it.

        HTTP work happens before the writes. Returns rows upserted.
        """
        tickers = sorted(set(tickers))
        if not tickers:
            return 0
        data, _failed = self._fetch_many(tickers, data_start_date(), "Full history")
        last_session = last_completed_session()
        rows: list[dict] = []
        for ticker, bars in data.items():
            rows.extend(
                bars_to_rows(
                    ticker, bars, max_date=last_session, min_date=data_start_date()
                )
            )
        written = upsert_price_rows(session, rows) if rows else 0
        session.flush()
        if recompute and data:
            self._recompute(session, sorted(data))
        return written


@retry(max_attempts=3, base_delay=2.0)
def _fetch_bars_batch(
    symbols: list[str],
    start: str,
    end: str,
    headers: dict[str, str],
    feed: str = "sip",
) -> dict[str, list[dict]]:
    """Fetch bars for multiple symbols from Alpaca.

    Args:
        symbols: List of ticker symbols (max 100).
        start: Start date (YYYY-MM-DD) or RFC-3339 timestamp.
        end: End date (YYYY-MM-DD) or RFC-3339 timestamp.
        headers: Alpaca auth headers.
        feed: "sip" or "iex".

    Returns:
        Dict mapping symbol -> list of bar dicts.
    """
    all_bars: dict[str, list[dict]] = {}
    next_page_token = None

    while True:
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": "all",
            "feed": feed,
        }
        if next_page_token:
            params["page_token"] = next_page_token

        response = requests.get(
            f"{DATA_BASE_URL}/v2/stocks/bars",
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        bars = payload.get("bars", {}) or {}
        for symbol, symbol_bars in bars.items():
            all_bars.setdefault(symbol, []).extend(symbol_bars)

        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break

    return all_bars


def _parse_bar_timestamp(timestamp: str) -> date | None:
    """Parse Alpaca bar timestamp (ISO format) to date."""
    if not timestamp:
        return None
    # Alpaca returns: "2026-04-07T04:00:00Z"
    return parse_date(timestamp[:10], formats=("%Y-%m-%d",))
