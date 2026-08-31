"""Options IV Collector — daily ATM implied volatility from Alpaca.

Fetches the options chain snapshot for each active ticker, extracts
ATM (at-the-money) implied volatility for near-term (~30d) and
next-term (~60d) expirations, computes 25-delta skew, term structure
slope, and put/call open interest ratio.

Sprint 9.5b D3.

API: GET https://data.alpaca.markets/v1beta1/options/snapshots/{ticker}
Auth: APCA-API-KEY-ID + APCA-API-SECRET-KEY headers
Feed: 'indicative' (free tier, delayed)
Rate limit: 200 requests/minute on free tier
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.options_iv import OptionsIVSnapshot
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

DATA_BASE_URL = "https://data.alpaca.markets"

# Batch size for rate limit management (200 req/min)
BATCH_PAUSE_SECS = 0.35  # ~170 req/min, safe margin


class OptionsIVCollector(BaseCollector):
    """Collect daily ATM implied volatility snapshots from Alpaca Options API."""

    name = "options_iv_collector"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

    def fetch(self, session: Session) -> list[dict]:
        """Fetch ATM IV for all active tickers."""
        import time

        tickers = [
            r[0]
            for r in session.execute(
                Universe.__table__.select()
                .with_only_columns(Universe.ticker)
                .where(Universe.is_active.is_(True))
                .order_by(Universe.ticker)
            ).all()
        ]

        # Filter out ETFs/benchmarks that may not have options
        from trading_signals.universe.blacklist import BENCHMARK_TICKERS
        # Include benchmarks too — SPY, QQQ have very liquid options
        today = date.today()

        results = []
        errors = 0

        for i, ticker in enumerate(tickers):
            try:
                snapshot = self._fetch_ticker_iv(ticker, today)
                if snapshot:
                    results.append(snapshot)
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(
                        f"[{self.name}] {ticker} failed: {e}"
                    )
            # Rate limit pause
            if (i + 1) % 10 == 0:
                time.sleep(BATCH_PAUSE_SECS * 3)  # ~1s pause every 10

        if errors > 5:
            logger.warning(
                f"[{self.name}] {errors} total errors (showing first 5)"
            )

        return results

    @retry(max_attempts=2, base_delay=1.0)
    def _fetch_ticker_iv(self, ticker: str, today: date) -> dict | None:
        """Fetch options chain for one ticker and extract ATM IV metrics."""
        import time
        time.sleep(BATCH_PAUSE_SECS)

        url = f"{DATA_BASE_URL}/v1beta1/options/snapshots/{ticker}"

        # Filter: only standard monthly options, near-term
        # Look for expirations 20-45 days out (30d) and 50-80 days (60d)
        exp_min_30 = today + timedelta(days=20)
        exp_max_30 = today + timedelta(days=45)
        exp_min_60 = today + timedelta(days=50)
        exp_max_60 = today + timedelta(days=80)

        params = {
            "feed": "indicative",
            "limit": 500,
            "expiration_date_gte": exp_min_30.isoformat(),
            "expiration_date_lte": exp_max_60.isoformat(),
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=self._headers, params=params)

        if resp.status_code == 404:
            return None  # No options for this ticker
        if resp.status_code == 422:
            return None  # Invalid/delisted ticker
        resp.raise_for_status()

        data = resp.json()
        snapshots = data.get("snapshots", {})
        if not snapshots:
            return None

        # Parse contracts into structured data
        contracts = []
        for symbol, snap in snapshots.items():
            greeks = snap.get("greeks", {})
            iv = greeks.get("implied_volatility")
            delta = greeks.get("delta")
            if iv is None or delta is None:
                continue

            # Parse expiration from contract symbol
            # Format: AAPL250919C00230000
            try:
                exp_str = symbol[len(ticker):][:6]
                exp_date = date(
                    2000 + int(exp_str[:2]),
                    int(exp_str[2:4]),
                    int(exp_str[4:6]),
                )
            except (ValueError, IndexError):
                continue

            is_call = "C" in symbol[len(ticker) + 6:]
            oi = snap.get("latestQuote", {}).get("open_interest") or 0

            contracts.append({
                "symbol": symbol,
                "expiration": exp_date,
                "is_call": is_call,
                "delta": float(delta),
                "iv": float(iv),
                "oi": int(oi) if oi else 0,
            })

        if not contracts:
            return None

        # Split into 30d and 60d buckets
        contracts_30d = [
            c for c in contracts
            if exp_min_30 <= c["expiration"] <= exp_max_30
        ]
        contracts_60d = [
            c for c in contracts
            if exp_min_60 <= c["expiration"] <= exp_max_60
        ]

        # Extract ATM IV (contracts closest to delta 0.50 for calls)
        atm_iv_30d = self._extract_atm_iv(contracts_30d)
        atm_iv_60d = self._extract_atm_iv(contracts_60d)

        # 25-delta skew: IV of 25d put minus IV of 25d call
        skew = self._extract_skew(contracts_30d)

        # Term structure slope
        term_slope = None
        if atm_iv_30d is not None and atm_iv_60d is not None:
            term_slope = round(atm_iv_60d - atm_iv_30d, 4)

        # Open interest
        total_oi_call = sum(c["oi"] for c in contracts if c["is_call"])
        total_oi_put = sum(c["oi"] for c in contracts if not c["is_call"])
        put_call_oi = None
        if total_oi_call > 0:
            put_call_oi = round(total_oi_put / total_oi_call, 4)

        return {
            "ticker": ticker,
            "snapshot_date": today,
            "atm_iv_30d": atm_iv_30d,
            "atm_iv_60d": atm_iv_60d,
            "skew_25d": skew,
            "term_slope": term_slope,
            "total_oi_call": total_oi_call if total_oi_call > 0 else None,
            "total_oi_put": total_oi_put if total_oi_put > 0 else None,
            "put_call_oi": put_call_oi,
        }

    def _extract_atm_iv(self, contracts: list[dict]) -> float | None:
        """Find ATM IV: call with delta closest to 0.50."""
        calls = [c for c in contracts if c["is_call"] and c["iv"] > 0]
        if not calls:
            return None

        atm = min(calls, key=lambda c: abs(c["delta"] - 0.50))
        # Only accept if delta is reasonably close to ATM
        if abs(atm["delta"] - 0.50) > 0.15:
            return None
        return round(atm["iv"], 4)

    def _extract_skew(self, contracts: list[dict]) -> float | None:
        """Compute 25-delta skew: IV(put 25d) - IV(call 25d)."""
        puts = [c for c in contracts if not c["is_call"] and c["iv"] > 0]
        calls = [c for c in contracts if c["is_call"] and c["iv"] > 0]

        if not puts or not calls:
            return None

        # Find 25-delta put (delta ~ -0.25)
        put_25 = min(puts, key=lambda c: abs(c["delta"] + 0.25))
        # Find 25-delta call (delta ~ 0.25)
        call_25 = min(calls, key=lambda c: abs(c["delta"] - 0.25))

        # Only if deltas are reasonable
        if abs(put_25["delta"] + 0.25) > 0.10 or abs(call_25["delta"] - 0.25) > 0.10:
            return None

        return round(put_25["iv"] - call_25["iv"], 4)

    def store(self, session: Session, records: list[dict]) -> int:
        """Upsert IV snapshots."""
        written = 0
        for record in records:
            stmt = (
                pg_insert(OptionsIVSnapshot)
                .values(**record)
                .on_conflict_do_update(
                    index_elements=["ticker", "snapshot_date"],
                    set_={
                        k: v for k, v in record.items()
                        if k not in ("ticker", "snapshot_date") and v is not None
                    },
                )
            )
            session.execute(stmt)
            written += 1

        session.flush()
        return written
