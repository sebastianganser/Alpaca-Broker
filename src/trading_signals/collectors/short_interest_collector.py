"""Short Interest Collector – daily short volume via Massive API (formerly Polygon).

Collects daily short volume data for active tickers.
Rate limit: 5 calls/min = 12 seconds between requests.
Sprint 9.5c (B5).
"""

import time
from datetime import date, timedelta
from typing import Any

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.short_interest import ShortVolume
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


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
        self._api_base = "https://api.massive.com"
        self._first_response_logged = False
    
    def fetch(self, session: Session) -> list[dict]:
        """Fetch yesterday's short volume for all active tickers."""
        target_date = date.today() - timedelta(days=1)
        
        tickers = [
            r[0]
            for r in session.execute(
                Universe.__table__.select()
                .with_only_columns(Universe.ticker)
                .where(Universe.is_active.is_(True))
                .order_by(Universe.ticker)
            ).all()
        ]
        
        results = []
        errors = 0
        
        for i, ticker in enumerate(tickers):
            if i > 0 and i % 50 == 0:
                logger.info(f"[{self.name}] Progress: {i}/{len(tickers)} tickers processed")
                
            try:
                data = self._fetch_ticker_short_volume(ticker, target_date)
                if data:
                    results.append(data)
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(f"[{self.name}] {ticker} failed: {e}")
            
            # Rate limit: 5 calls/min -> 12s sleep
            time.sleep(12)
            
        if errors > 5:
            logger.warning(
                f"[{self.name}] {errors} total errors (showing first 5)"
            )
            
        logger.info(
            f"[{self.name}] Collected {len(results)}/{len(tickers)} "
            f"short volume snapshots (errors: {errors})"
        )
            
        return results
    
    def _fetch_ticker_short_volume(self, ticker: str, target_date: date) -> dict | None:
        """Fetch short volume for a single ticker and date."""
        url = f"{self._api_base}/stocks/v1/short-volume"
        params = {
            "ticker": ticker,
            "date": target_date.isoformat(),
            "apiKey": self._api_key
        }
        
        resp = self._session.get(url, params=params, timeout=15)
        
        if resp.status_code == 403 or resp.status_code == 429:
            logger.warning(f"[{self.name}] Massive API error {resp.status_code}: Check API tier/key")
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
        if "results" in data and isinstance(data["results"], list) and len(data["results"]) > 0:
            item = data["results"][0]
        elif "result" in data and isinstance(data["result"], dict):
            item = data["result"]
        elif "short_volume" in data:
            item = data
            
        if not item:
            return None
            
        short_volume = int(item.get("short_volume", 0) or 0)
        total_volume = int(item.get("total_volume", 0) or 0)
        
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
        """Upsert into short_volume table."""
        if not data:
            return 0, 0
            
        written = 0
        for obs in data:
            stmt = pg_insert(ShortVolume).values(**obs)
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_short_volume_dedup"
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                written += 1
                
        session.flush()
        return len(data), written
