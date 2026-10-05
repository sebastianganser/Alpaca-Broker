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

Labelling (H3): snapshots are labelled with the last COMPLETED NYSE session
(``market_calendar.last_completed_session``), not the Berlin calendar date
of the run. The 04:30-Berlin run on Saturday therefore stores Friday's
close, and the runs on Sunday/Monday morning find Friday already stored
and return immediately.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import (
    YFRateLimitError,
    _try_get,
    call_with_rate_limit_retry,
    to_yahoo_symbol,
)
from trading_signals.db.models.options_iv import OptionsIVSnapshot
from trading_signals.utils import market_calendar
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Rate limit: pause between tickers
TICKER_PAUSE_SECS = 0.5

#: Skip the run if this share of active tickers is already stored for the
#: target session (otherwise only the missing tickers are fetched).
SKIP_IF_STORED_SHARE = 0.9


class OptionsIVCollector(BaseCollector):
    """Collect daily ATM implied volatility snapshots via yfinance."""

    name = "options_iv_collector"

    def fetch(self, session: Session) -> list[dict]:
        """Fetch ATM IV for all active tickers not yet stored for the session."""
        snapshot_date = market_calendar.last_completed_session()

        stored = {
            r[0]
            for r in session.execute(
                select(distinct(OptionsIVSnapshot.ticker)).where(
                    OptionsIVSnapshot.snapshot_date == snapshot_date
                )
            ).all()
        }
        release_transaction(session)
        all_tickers = self.get_active_tickers(session)

        if all_tickers and len(stored) >= SKIP_IF_STORED_SHARE * len(all_tickers):
            logger.info(
                f"[{self.name}] Session {snapshot_date} already stored for "
                f"{len(stored)}/{len(all_tickers)} tickers — skipping"
            )
            return []
        tickers = [t for t in all_tickers if t not in stored]

        results = []
        errors = 0
        no_options = 0

        for ticker in tickers:
            try:
                snapshot = call_with_rate_limit_retry(
                    self._fetch_ticker_iv, ticker, snapshot_date, label=self.name
                )
                self.record_success()
                if snapshot:
                    results.append(snapshot)
                else:
                    no_options += 1
            except Exception as e:
                errors += 1
                self.record_error()
                if errors <= 5:
                    logger.warning(
                        f"[{self.name}] {ticker} failed: {type(e).__name__}: {e}"
                    )
            # Rate limit
            time.sleep(TICKER_PAUSE_SECS)

        if errors > 5:
            logger.warning(
                f"[{self.name}] {errors} total errors (showing first 5)"
            )

        # Alert if trading day but no data collected
        if len(results) == 0 and tickers:
            logger.warning(
                f"[{self.name}] Session {snapshot_date} but 0 IV snapshots "
                f"collected from {len(tickers)} tickers — possible API issue"
            )
        else:
            logger.info(
                f"[{self.name}] Collected {len(results)}/{len(tickers)} "
                f"IV snapshots for {snapshot_date} "
                f"({no_options} no options, {errors} errors)"
            )

        return results

    def _fetch_ticker_iv(self, ticker: str, today: date) -> dict | None:
        """Fetch options chain for one ticker via yfinance.

        ``today`` is the snapshot (session) date. Exceptions from the
        primary requests propagate (counted as errors); an empty options
        list is a valid "no options" answer.
        """
        import yfinance as yf

        yticker = yf.Ticker(to_yahoo_symbol(ticker))

        # Get available expiration dates
        expirations = yticker.options

        if not expirations:
            return None

        # Find 30d expiry (14-50 days out) and 60d expiry (45-100 days out)
        # Ranges are wide to reliably capture monthly expirations (3rd Friday)
        exp_30d = self._find_expiry(expirations, today, 14, 50)
        exp_60d = self._find_expiry(expirations, today, 45, 100)

        if not exp_30d:
            return None

        # Get 30d chain
        chain_30d = yticker.option_chain(exp_30d)

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

        # Extract ATM IV from 60d chain (optional – errors ignored, but rate
        # limits propagate so the whole ticker is retried)
        atm_iv_60d = None
        if exp_60d:
            chain_60d = _try_get(lambda: yticker.option_chain(exp_60d))
            if chain_60d is not None:
                try:
                    atm_iv_60d = self._extract_atm_iv(chain_60d.calls, current_price)
                except YFRateLimitError:
                    raise
                except Exception:
                    pass

        # Skew: OTM put IV vs OTM call IV (approximation of 25-delta skew)
        skew = self._extract_skew(calls_30d, puts_30d, current_price)

        # Term structure slope
        term_slope = None
        if atm_iv_30d is not None and atm_iv_60d is not None:
            term_slope = round(atm_iv_60d - atm_iv_30d, 4)

        # Open interest totals (from 30d chain)
        total_oi_call = None
        if "openInterest" in calls_30d:
            total_oi_call = int(calls_30d["openInterest"].sum())
        total_oi_put = None
        if "openInterest" in puts_30d:
            total_oi_put = int(puts_30d["openInterest"].sum())

        put_call_oi = None
        if total_oi_call and total_oi_call > 0 and total_oi_put is not None:
            put_call_oi = round(total_oi_put / total_oi_call, 4)

        # Skip if we got nothing useful
        if atm_iv_30d is None and not total_oi_call:
            return None

        return {
            "ticker": ticker,  # original universe ticker (not Yahoo symbol)
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
        """Insert IV snapshots (multi-row). Returns (fetched, written).

        fetch() only requests tickers that are not yet stored for the
        session, so ON CONFLICT DO NOTHING is sufficient (a concurrent or
        repeated run never overwrites an existing snapshot).
        """
        if not records:
            return 0, 0
        written = self._bulk_insert(
            session,
            OptionsIVSnapshot,
            records,
            conflict_cols=["ticker", "snapshot_date"],
        )
        session.flush()
        return len(records), written
