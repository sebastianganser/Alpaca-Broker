"""Short Interest Collector – daily short volume via Massive API (formerly Polygon).

Collects daily short volume data for active tickers.
Rate limit: 5 calls/min = 12 seconds between requests.
Sprint 9.5c (B5).

Target date (H3/M9): the last completed NYSE session
(``market_calendar.last_completed_session``) instead of ``today - 1``.
Tickers already stored for that session are not requested again, and if
(almost) all are stored the run returns immediately – this saves ~2.5h on
every Sunday/Monday morning and after holidays.

Auth (H4): the API key is sent as ``Authorization: Bearer`` header, never
as a query parameter (URLs end up in logs/exception messages).
"""

import time
from datetime import date

import requests
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import safe_int
from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.short_interest import ShortVolume
from trading_signals.utils import market_calendar
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Seconds between requests (free tier: 5 calls/min).
REQUEST_PAUSE_SECS = 12

#: Skip the run if this share of active tickers is already stored for the
#: target session (otherwise only the missing tickers are fetched).
SKIP_IF_STORED_SHARE = 0.9


class ShortInterestCollector(BaseCollector):
    name = "short_interest_collector"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.POLYGON_API_KEY
        if not self._api_key:
            raise ValueError(
                "POLYGON_API_KEY not configured. Register free at "
                "https://massive.com (formerly polygon.io)"
            )
        self._session = requests.Session()
        # H4: key in header, not in the URL
        self._session.headers.update({"Authorization": f"Bearer {self._api_key}"})
        self._api_base = "https://api.massive.com"
        self._first_response_logged = False

    def fetch(self, session: Session) -> list[dict]:
        """Fetch short volume of the last completed session for active tickers."""
        target_date = market_calendar.last_completed_session()

        stored = {
            r[0]
            for r in session.execute(
                select(distinct(ShortVolume.ticker)).where(
                    ShortVolume.trade_date == target_date
                )
            ).all()
        }
        release_transaction(session)
        all_tickers = self.get_active_tickers(session)

        if all_tickers and len(stored) >= SKIP_IF_STORED_SHARE * len(all_tickers):
            logger.info(
                f"[{self.name}] Session {target_date} already stored for "
                f"{len(stored)}/{len(all_tickers)} tickers — skipping"
            )
            return []
        tickers = [t for t in all_tickers if t not in stored]

        results = []
        errors = 0

        for i, ticker in enumerate(tickers):
            if i > 0 and i % 50 == 0:
                logger.info(
                    f"[{self.name}] Progress: {i}/{len(tickers)} tickers processed"
                )

            try:
                data = self._fetch_ticker_short_volume(ticker, target_date)
                self.record_success()
                if data:
                    results.append(data)
            except Exception as e:
                errors += 1
                self.record_error()
                if errors <= 5:
                    logger.warning(f"[{self.name}] {ticker} failed: {_describe(e)}")

            # Rate limit: 5 calls/min -> 12s sleep
            if i < len(tickers) - 1:
                time.sleep(REQUEST_PAUSE_SECS)

        if errors > 5:
            logger.warning(
                f"[{self.name}] {errors} total errors (showing first 5)"
            )

        logger.info(
            f"[{self.name}] Collected {len(results)}/{len(tickers)} "
            f"short volume snapshots for {target_date} (errors: {errors})"
        )

        return results

    def _fetch_ticker_short_volume(self, ticker: str, target_date: date) -> dict | None:
        """Fetch short volume for a single ticker and date."""
        url = f"{self._api_base}/stocks/v1/short-volume"
        params = {
            "ticker": ticker,
            "date": target_date.isoformat(),
        }

        resp = self._session.get(url, params=params, timeout=15)

        if resp.status_code == 403 or resp.status_code == 429:
            logger.warning(
                f"[{self.name}] Massive API error {resp.status_code}: "
                f"Check API tier/key"
            )
            resp.raise_for_status()

        if not resp.ok:
            if resp.status_code == 404:
                return None
            resp.raise_for_status()

        data = resp.json()

        if not self._first_response_logged:
            logger.debug(f"[{self.name}] Sample API response for {ticker}: {data}")
            self._first_response_logged = True

        # Handle both results array and result object patterns defensively
        item = None
        if (
            "results" in data
            and isinstance(data["results"], list)
            and len(data["results"]) > 0
        ):
            item = data["results"][0]
        elif "result" in data and isinstance(data["result"], dict):
            item = data["result"]
        elif "short_volume" in data:
            item = data

        if not item:
            return None

        short_volume = safe_int(item.get("short_volume")) or 0
        total_volume = safe_int(item.get("total_volume")) or 0

        if short_volume == 0 or total_volume == 0:
            return None

        ratio = round(short_volume / total_volume, 4)

        return {
            "ticker": ticker,
            "trade_date": target_date,
            "short_volume": short_volume,
            "total_volume": total_volume,
            "short_volume_ratio": ratio,
            "source": "massive"
        }

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Insert into short_volume table (multi-row, ON CONFLICT DO NOTHING)."""
        if not data:
            return 0, 0

        written = self._bulk_insert(
            session, ShortVolume, data, conflict_cols=["ticker", "trade_date"]
        )
        session.flush()
        return len(data), written


def _describe(e: Exception) -> str:
    """Exception summary without URLs (H4: never log request URLs)."""
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        return f"{type(e).__name__} (HTTP {status})"
    return type(e).__name__
