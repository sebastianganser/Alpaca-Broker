"""FRED Macro Regime Collector – daily macroeconomic indicators via FRED API.

Collects key macro indicators that serve as market context features:
- Yield curve (DGS2, DGS10) → recession probability
- High Yield spread (BAMLH0A0HYM2) → credit risk appetite
- VIX (VIXCLS) → volatility regime
- Dollar Index (DTWEXBGS) → macro headwind for exporters
- Breakeven Inflation (T10YIE) → duration/valuation pressure

Strategy:
  1. For each series, find the latest observation in DB (one query)
  2. Fetch only new observations from FRED since last date
     (never before the rolling retention start ``data_start_date()``)
  3. Store with ON CONFLICT DO NOTHING (idempotent)

Security (H4): FRED only accepts the key as ``api_key`` query parameter, so
request URLs contain the secret. Never log URLs or raw exception strings
of HTTP errors here – only the exception type and HTTP status.

Schedule: Daily 04:15 CET (FRED updates ~22:00 ET = 04:00 CET)
Sprint: 9.5b (Data Extension)
"""

from datetime import date, timedelta
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date, safe_float
from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.macro_series import MacroSeries
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

# FRED series to track — each maps to a key macro regime indicator
FRED_SERIES = {
    "DGS2": "2-Year Treasury Yield",
    "DGS10": "10-Year Treasury Yield",
    "BAMLH0A0HYM2": "High Yield OAS",
    "VIXCLS": "VIX Close",
    "DTWEXBGS": "Dollar Index (Broad)",
    "T10YIE": "10-Year Breakeven Inflation",
}

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _safe_error(e: Exception) -> str:
    """Describe an exception without its message (may contain the URL/key)."""
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        return f"{type(e).__name__} (HTTP {status})"
    return type(e).__name__


class FredCollector(BaseCollector):
    """Collects macroeconomic indicator time series from the FRED API.

    Uses direct REST calls (no external library dependencies).
    Fetches only incremental data since last observation per series.
    """

    name = "fred_collector"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.FRED_API_KEY
        if not self._api_key:
            raise ValueError(
                "FRED_API_KEY not configured. Register free at "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        self._session = requests.Session()

    def _latest_dates(self, session: Session) -> dict[str, date]:
        """Latest stored obs_date per series (single grouped query)."""
        rows = session.execute(
            select(MacroSeries.series_id, func.max(MacroSeries.obs_date))
            .where(MacroSeries.series_id.in_(list(FRED_SERIES)))
            .group_by(MacroSeries.series_id)
        ).all()
        latest: dict[str, date] = {}
        for row in rows:
            try:
                sid, d = row[0], row[1]
            except (TypeError, IndexError):
                continue
            if isinstance(sid, str) and isinstance(d, date):
                latest[sid] = d
        return latest

    def fetch(self, session: Session) -> Any:
        """Fetch new observations for all FRED series."""
        today = date.today()
        floor = data_start_date()
        latest_by_series = self._latest_dates(session)
        release_transaction(session)  # no open txn during HTTP

        all_observations: list[dict] = []

        for series_id, label in FRED_SERIES.items():
            latest = latest_by_series.get(series_id)
            start_date = max(latest + timedelta(days=1), floor) if latest else floor

            if start_date > today:
                logger.debug(
                    f"[fred_collector] {series_id} ({label}): up to date"
                )
                continue

            # Fetch from FRED API
            try:
                obs = self._fetch_series(series_id, start_date, today)
                self.record_success()
                all_observations.extend(obs)
                logger.info(
                    f"[fred_collector] {series_id} ({label}): "
                    f"{len(obs)} new observations since {start_date}"
                )
            except Exception as e:
                self.record_error()
                logger.warning(
                    f"[fred_collector] Failed to fetch {series_id}: {_safe_error(e)}"
                )

        return all_observations

    def _fetch_series(
        self, series_id: str, start: date, end: date
    ) -> list[dict]:
        """Fetch observations for a single FRED series."""
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "sort_order": "asc",
        }

        resp = self._session.get(FRED_API_BASE, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        as_of = date.today()
        observations = []
        for obs in data.get("observations", []):
            # FRED uses "." for missing values → safe_float returns None
            value = safe_float(obs.get("value"))
            obs_date = parse_date(obs.get("date"))
            if value is None or obs_date is None:
                continue

            observations.append({
                "series_id": series_id,
                "obs_date": obs_date,
                "value": value,
                "source": "fred",
                "as_of": as_of,
            })

        return observations

    def store(self, session: Session, data: Any) -> tuple[int, int]:
        """Store FRED observations with ON CONFLICT DO NOTHING (multi-row)."""
        if not data:
            return 0, 0

        records_written = self._bulk_insert(
            session, MacroSeries, data, conflict_cols=["series_id", "obs_date"]
        )
        session.flush()
        return len(data), records_written
