"""FRED Macro Regime Collector – daily macroeconomic indicators via FRED API.

Collects key macro indicators that serve as market context features:
- Yield curve (DGS2, DGS10) → recession probability
- High Yield spread (BAMLH0A0HYM2) → credit risk appetite
- VIX (VIXCLS) → volatility regime
- Dollar Index (DTWEXBGS) → macro headwind for exporters
- Breakeven Inflation (T10YIE) → duration/valuation pressure

Strategy:
  1. For each series, find the latest observation in DB
  2. Fetch only new observations from FRED since last date
  3. Store with ON CONFLICT DO NOTHING (idempotent)

Schedule: Daily 04:15 CET (FRED updates ~22:00 ET = 04:00 CET)
Sprint: 9.5b (Data Extension)
"""

from datetime import date, timedelta
from typing import Any

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from trading_signals.collectors.base import BaseCollector
from trading_signals.config import DATA_START_DATE, get_settings
from trading_signals.db.models.macro_series import MacroSeries
from trading_signals.utils.logging import get_logger

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

    def fetch(self, session: Session) -> Any:
        """Fetch new observations for all FRED series."""
        today = date.today()
        all_observations: list[dict] = []

        for series_id, label in FRED_SERIES.items():
            # Find latest date we already have
            latest = session.execute(
                select(func.max(MacroSeries.obs_date))
                .where(MacroSeries.series_id == series_id)
            ).scalar()

            start_date = (latest + timedelta(days=1)) if latest else DATA_START_DATE

            if start_date > today:
                logger.debug(
                    f"[fred_collector] {series_id} ({label}): up to date"
                )
                continue

            # Fetch from FRED API
            try:
                obs = self._fetch_series(series_id, start_date, today)
                all_observations.extend(obs)
                logger.info(
                    f"[fred_collector] {series_id} ({label}): "
                    f"{len(obs)} new observations since {start_date}"
                )
            except Exception as e:
                logger.warning(
                    f"[fred_collector] Failed to fetch {series_id}: {e}"
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

        observations = []
        for obs in data.get("observations", []):
            value_str = obs.get("value", ".")
            # FRED uses "." for missing values
            if value_str == "." or not value_str:
                continue

            try:
                value = float(value_str)
            except (ValueError, TypeError):
                continue

            observations.append({
                "series_id": series_id,
                "obs_date": date.fromisoformat(obs["date"]),
                "value": value,
                "source": "fred",
                "as_of": date.today(),
            })

        return observations

    def store(self, session: Session, data: Any) -> tuple[int, int]:
        """Store FRED observations with ON CONFLICT DO NOTHING."""
        if not data:
            return 0, 0

        records_fetched = len(data)
        records_written = 0

        # Batch insert with upsert
        for obs in data:
            stmt = pg_insert(MacroSeries).values(**obs)
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_macro_series_dedup"
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()
        return records_fetched, records_written
