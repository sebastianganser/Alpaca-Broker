"""Options IV Collector — daily ATM implied volatility via yfinance.

Fetches the options chain for each active ticker, extracts
ATM (at-the-money) implied volatility for near-term (~30d) and
next-term (~60d) expirations, computes 25-delta skew (approximated
via OTM put vs call IV), term structure slope, and put/call OI ratio.

Sprint 9.5b D3.

Data source: Yahoo Finance (free, via yfinance library).
Alpaca's free 'indicative' feed was tested but does NOT provide
Greeks, IV, OI, or volume — only bid/ask quotes. OPRA requires
a paid subscription. yfinance provides IV, OI, and volume for free.

Rate limiting: ~2 req/s to avoid Yahoo throttling.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.db.models.options_iv import OptionsIVSnapshot
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Rate limit: pause between tickers
TICKER_PAUSE_SECS = 0.5


class OptionsIVCollector(BaseCollector):
    """Collect daily ATM implied volatility snapshots via yfinance."""

    name = "options_iv_collector"

    def fetch(self, session: Session) -> list[dict]:
        """Fetch ATM IV for all active tickers."""
        import pandas as pd
        import pandas_market_calendars as mcal

        today = date.today()

        # Skip weekends + NYSE holidays
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(
            start_date=pd.Timestamp(today),
            end_date=pd.Timestamp(today),
        )
        if len(schedule) == 0:
            logger.info(
                f"[{self.name}] {today} is not a NYSE trading day — skipping"
            )
            return []

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
        no_options = 0

        for i, ticker in enumerate(tickers):
            try:
                snapshot = self._fetch_ticker_iv(ticker, today)
                if snapshot:
                    results.append(snapshot)
                else:
                    no_options += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(f"[{self.name}] {ticker} failed: {e}")
            # Rate limit
            time.sleep(TICKER_PAUSE_SECS)

        if errors > 5:
            logger.warning(
                f"[{self.name}] {errors} total errors (showing first 5)"
            )

        # Alert if trading day but no data collected
        if len(results) == 0:
            logger.warning(
                f"[{self.name}] Trading day {today} but 0 IV snapshots "
                f"collected from {len(tickers)} tickers — possible API issue"
            )
        else:
            logger.info(
                f"[{self.name}] Collected {len(results)}/{len(tickers)} "
                f"IV snapshots ({no_options} no options, {errors} errors)"
            )

        return results

    def _fetch_ticker_iv(self, ticker: str, today: date) -> dict | None:
        """Fetch options chain for one ticker via yfinance."""
        import yfinance as yf

        yticker = yf.Ticker(ticker)

        # Get available expiration dates
        try:
            expirations = yticker.options
        except Exception:
            return None

        if not expirations:
            return None

        # Find 30d expiry (20-45 days out) and 60d expiry (50-80 days out)
        exp_30d = self._find_expiry(expirations, today, 20, 45)
        exp_60d = self._find_expiry(expirations, today, 50, 80)

        if not exp_30d:
            return None

        # Get 30d chain
        try:
            chain_30d = yticker.option_chain(exp_30d)
        except Exception:
            return None

        calls_30d = chain_30d.calls
        puts_30d = chain_30d.puts

        if calls_30d.empty and puts_30d.empty:
            return None

        # Get current price for ATM determination
        info = yticker.fast_info
        current_price = getattr(info, "last_price", None)
        if not current_price:
            # Fallback: use most recent close
            current_price = getattr(info, "previous_close", None)
        if not current_price:
            return None

        # Extract ATM IV from 30d chain
        atm_iv_30d = self._extract_atm_iv(calls_30d, current_price)

        # Extract ATM IV from 60d chain
        atm_iv_60d = None
        if exp_60d:
            try:
                chain_60d = yticker.option_chain(exp_60d)
                atm_iv_60d = self._extract_atm_iv(chain_60d.calls, current_price)
            except Exception:
                pass

        # Skew: OTM put IV vs OTM call IV (approximation of 25-delta skew)
        skew = self._extract_skew(calls_30d, puts_30d, current_price)

        # Term structure slope
        term_slope = None
        if atm_iv_30d is not None and atm_iv_60d is not None:
            term_slope = round(atm_iv_60d - atm_iv_30d, 4)

        # Open interest totals (from 30d chain)
        total_oi_call = int(calls_30d["openInterest"].sum()) if "openInterest" in calls_30d else None
        total_oi_put = int(puts_30d["openInterest"].sum()) if "openInterest" in puts_30d else None

        put_call_oi = None
        if total_oi_call and total_oi_call > 0 and total_oi_put is not None:
            put_call_oi = round(total_oi_put / total_oi_call, 4)

        # Skip if we got nothing useful
        if atm_iv_30d is None and not total_oi_call:
            return None

        return {
            "ticker": ticker,
            "snapshot_date": today,
            "atm_iv_30d": atm_iv_30d,
            "atm_iv_60d": atm_iv_60d,
            "skew_25d": skew,
            "term_slope": term_slope,
            "total_oi_call": total_oi_call if total_oi_call else None,
            "total_oi_put": total_oi_put if total_oi_put else None,
            "put_call_oi": put_call_oi,
        }

    def _find_expiry(
        self, expirations: tuple[str, ...], today: date,
        min_days: int, max_days: int
    ) -> str | None:
        """Find the best expiration within [min_days, max_days] from today."""
        target_min = today + timedelta(days=min_days)
        target_max = today + timedelta(days=max_days)
        target_mid = today + timedelta(days=(min_days + max_days) // 2)

        candidates = []
        for exp_str in expirations:
            exp_date = date.fromisoformat(exp_str)
            if target_min <= exp_date <= target_max:
                candidates.append(exp_str)

        if not candidates:
            return None

        # Pick closest to midpoint
        return min(
            candidates,
            key=lambda e: abs((date.fromisoformat(e) - target_mid).days)
        )

    def _extract_atm_iv(self, calls, current_price: float) -> float | None:
        """Find ATM implied volatility from calls dataframe."""
        if calls.empty or "impliedVolatility" not in calls.columns:
            return None

        # Filter for valid IV
        valid = calls[calls["impliedVolatility"] > 0.001]
        if valid.empty:
            return None

        # Find strike closest to current price
        atm_idx = (valid["strike"] - current_price).abs().idxmin()
        atm_row = valid.loc[atm_idx]

        # Only accept if strike is within 5% of current price
        if abs(atm_row["strike"] - current_price) / current_price > 0.05:
            return None

        iv = float(atm_row["impliedVolatility"])
        return round(iv, 4) if iv > 0.001 else None

    def _extract_skew(self, calls, puts, current_price: float) -> float | None:
        """Compute skew: OTM put IV minus OTM call IV.

        Uses ~5% OTM strikes as approximation of 25-delta skew.
        Positive = puts more expensive (hedging demand).
        """
        if calls.empty or puts.empty:
            return None
        if "impliedVolatility" not in calls.columns:
            return None

        # OTM call: strike ~5% above current price
        otm_call_strike = current_price * 1.05
        valid_calls = calls[calls["impliedVolatility"] > 0.001]
        if valid_calls.empty:
            return None
        call_idx = (valid_calls["strike"] - otm_call_strike).abs().idxmin()
        call_iv = float(valid_calls.loc[call_idx, "impliedVolatility"])

        # OTM put: strike ~5% below current price
        otm_put_strike = current_price * 0.95
        valid_puts = puts[puts["impliedVolatility"] > 0.001]
        if valid_puts.empty:
            return None
        put_idx = (valid_puts["strike"] - otm_put_strike).abs().idxmin()
        put_iv = float(valid_puts.loc[put_idx, "impliedVolatility"])

        if call_iv < 0.001 or put_iv < 0.001:
            return None

        return round(put_iv - call_iv, 4)

    def store(self, session: Session, records: list[dict]) -> tuple[int, int]:
        """Upsert IV snapshots. Returns (fetched, written)."""
        written = 0
        for record in records:
            update_fields = {
                k: v for k, v in record.items()
                if k not in ("ticker", "snapshot_date") and v is not None
            }

            if update_fields:
                stmt = (
                    pg_insert(OptionsIVSnapshot)
                    .values(**record)
                    .on_conflict_do_update(
                        index_elements=["ticker", "snapshot_date"],
                        set_=update_fields,
                    )
                )
            else:
                # All optional fields are None — just insert, skip on conflict
                stmt = (
                    pg_insert(OptionsIVSnapshot)
                    .values(**record)
                    .on_conflict_do_nothing(
                        index_elements=["ticker", "snapshot_date"],
                    )
                )

            session.execute(stmt)
            written += 1

        session.flush()
        return len(records), written
