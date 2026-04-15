"""Shared yfinance client with rate-limiting, batching, and graceful error handling.

Used by all Sprint 5 collectors (Fundamentals, Analyst Ratings, Earnings Calendar)
to avoid redundant rate-limiting and error-handling code.

Key design decisions:
  - Each ticker is a separate yfinance.Ticker() call (no batch endpoint exists)
  - Rate-limiting: configurable delay between individual ticker calls
  - Batch pauses: longer delay between batches to avoid Yahoo rate-limits
  - Graceful errors: individual ticker failures are logged and skipped
"""

import math
import time
from typing import Any

import yfinance as yf

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# yfinance info keys we extract for fundamentals
FUNDAMENTALS_KEYS = {
    "marketCap": "market_cap",
    "trailingPE": "pe_ratio",
    "forwardPE": "forward_pe",
    "priceToSalesTrailing12Months": "ps_ratio",
    "priceToBook": "pb_ratio",
    "enterpriseToEbitda": "ev_ebitda",
    "profitMargins": "profit_margin",
    "operatingMargins": "operating_margin",
    "returnOnEquity": "return_on_equity",
    "totalRevenue": "revenue_ttm",
    "revenueGrowth": "revenue_growth_yoy",
    "trailingEps": "eps_ttm",
    "debtToEquity": "debt_to_equity",
    "currentRatio": "current_ratio",
    "dividendYield": "dividend_yield",
    "beta": "beta",
}


class YFinanceClient:
    """Shared yfinance client with rate-limiting and batch support.

    Args:
        batch_size: Number of tickers per batch before a longer pause.
        delay_between_tickers: Seconds to wait between individual ticker calls.
        delay_between_batches: Seconds to wait between batches.
    """

    def __init__(
        self,
        batch_size: int = 50,
        delay_between_tickers: float = 0.5,
        delay_between_batches: float = 3.0,
    ) -> None:
        self.batch_size = batch_size
        self.delay_between_tickers = delay_between_tickers
        self.delay_between_batches = delay_between_batches

    def _iterate_with_rate_limit(
        self,
        tickers: list[str],
        fetch_fn: Any,
        label: str,
    ) -> list[dict]:
        """Iterate over tickers with rate-limiting and batch pauses.

        Args:
            tickers: List of ticker symbols to process.
            fetch_fn: Callable(ticker_str) -> dict | list[dict] | None.
                      Returns extracted data or None on failure.
            label: Label for logging (e.g., "fundamentals").

        Returns:
            Flat list of all successfully extracted records.
        """
        results: list[dict] = []
        errors = 0
        n_batches = math.ceil(len(tickers) / self.batch_size)

        for batch_idx in range(n_batches):
            start = batch_idx * self.batch_size
            end = start + self.batch_size
            batch = tickers[start:end]

            logger.info(
                f"[yfinance/{label}] Batch {batch_idx + 1}/{n_batches} "
                f"({len(batch)} tickers)"
            )

            for i, ticker_str in enumerate(batch):
                try:
                    result = fetch_fn(ticker_str)
                    if result is not None:
                        if isinstance(result, list):
                            results.extend(result)
                        else:
                            results.append(result)
                except Exception as e:
                    errors += 1
                    logger.debug(
                        f"[yfinance/{label}] Failed for {ticker_str}: "
                        f"{type(e).__name__}: {e}"
                    )

                # Rate-limit between tickers (skip after last in batch)
                if i < len(batch) - 1:
                    time.sleep(self.delay_between_tickers)

            # Pause between batches (skip after last batch)
            if batch_idx < n_batches - 1:
                time.sleep(self.delay_between_batches)

        logger.info(
            f"[yfinance/{label}] Completed: {len(results)} records from "
            f"{len(tickers)} tickers ({errors} errors)"
        )
        return results

    def fetch_fundamentals(self, tickers: list[str]) -> list[dict]:
        """Fetch fundamental data via ticker.info for each ticker.

        Extracts key financial metrics defined in FUNDAMENTALS_KEYS.
        Also attempts to fetch eps_growth_yoy from earnings estimates.

        Returns:
            List of dicts with fundamental data, one per ticker.
        """

        def _fetch_single(ticker_str: str) -> dict | None:
            t = yf.Ticker(ticker_str)
            info = t.info

            if not info or info.get("regularMarketPrice") is None:
                return None

            record: dict[str, Any] = {"ticker": ticker_str}
            for yf_key, db_key in FUNDAMENTALS_KEYS.items():
                value = info.get(yf_key)
                record[db_key] = _clean_numeric(value)

            # yfinance returns dividendYield in percent form (0.4 = 0.4%)
            # while all other ratio fields are in decimal form (0.451 = 45.1%).
            # Normalize to decimal for consistent storage and display.
            if record.get("dividend_yield") is not None:
                record["dividend_yield"] = record["dividend_yield"] / 100

            # Plausibility checks – catch yfinance format changes / bad data
            _validate_fundamentals(record)

            # Attempt to get eps_growth_yoy from earnings estimate
            try:
                est = t.get_earnings_estimate()
                if est is not None and not est.empty:
                    # Look for the current quarter (0q) growth
                    if "growth" in est.columns:
                        growth_val = est.loc["0q", "growth"] if "0q" in est.index else None
                        record["eps_growth_yoy"] = _clean_numeric(growth_val)
            except Exception:
                pass  # eps_growth_yoy stays as extracted from info (None)

            return record

        return self._iterate_with_rate_limit(tickers, _fetch_single, "fundamentals")

    def fetch_sector_info(self, tickers: list[str]) -> list[dict]:
        """Fetch sector and industry classification for each ticker.

        Uses ticker.info to extract sector/industry and quoteType.
        quoteType is used downstream to detect ETFs that slipped
        past the name-based heuristic.

        Returns:
            List of dicts with 'ticker', 'sector', 'industry', 'quote_type'.
        """

        def _fetch_single(ticker_str: str) -> dict | None:
            t = yf.Ticker(ticker_str)
            info = t.info

            if not info:
                return None

            sector = info.get("sector")
            industry = info.get("industry")
            quote_type = info.get("quoteType")  # 'EQUITY', 'ETF', 'MUTUALFUND', etc.

            if not sector and not industry and not quote_type:
                return None

            return {
                "ticker": ticker_str,
                "sector": sector,
                "industry": industry,
                "quote_type": quote_type,
            }

        return self._iterate_with_rate_limit(tickers, _fetch_single, "sector_info")

    def fetch_analyst_ratings(
        self, tickers: list[str], lookback_days: int = 30
    ) -> list[dict]:
        """Fetch analyst upgrades/downgrades via ticker.upgrades_downgrades.

        Args:
            tickers: List of ticker symbols.
            lookback_days: Only return ratings from the last N days.

        Returns:
            List of dicts with rating data.
        """
        from datetime import date, timedelta

        cutoff = date.today() - timedelta(days=lookback_days)

        def _fetch_single(ticker_str: str) -> list[dict] | None:
            t = yf.Ticker(ticker_str)
            ud = t.upgrades_downgrades

            if ud is None or ud.empty:
                return None

            records = []
            for idx, row in ud.iterrows():
                # idx is a Timestamp (the date of the rating)
                try:
                    rating_date = idx.date() if hasattr(idx, "date") else None
                except Exception:
                    rating_date = None

                if rating_date is None or rating_date < cutoff:
                    continue

                records.append({
                    "ticker": ticker_str,
                    "firm": row.get("Firm", None),
                    "analyst": None,  # yfinance doesn't provide analyst names
                    "rating_date": rating_date,
                    "rating_new": row.get("ToGrade", None),
                    "rating_old": row.get("FromGrade", None),
                    "price_target_new": None,  # Not in upgrades_downgrades
                    "price_target_old": None,
                    "action": row.get("Action", None),
                    "raw_data": {
                        k: str(v) for k, v in row.to_dict().items()
                    },
                })

            return records if records else None

        return self._iterate_with_rate_limit(tickers, _fetch_single, "analyst_ratings")

    def fetch_earnings_dates(self, tickers: list[str], limit: int = 4) -> list[dict]:
        """Fetch earnings dates with EPS estimates and surprises.

        Args:
            tickers: List of ticker symbols.
            limit: Max number of earnings dates per ticker.

        Returns:
            List of dicts with earnings calendar data.
        """

        def _fetch_single(ticker_str: str) -> list[dict] | None:
            t = yf.Ticker(ticker_str)
            ed = t.get_earnings_dates(limit=limit)

            if ed is None or ed.empty:
                return None

            records = []
            for idx, row in ed.iterrows():
                try:
                    earnings_date = idx.date() if hasattr(idx, "date") else None
                except Exception:
                    earnings_date = None

                if earnings_date is None:
                    continue

                records.append({
                    "ticker": ticker_str,
                    "earnings_date": earnings_date,
                    "time_of_day": None,  # Not reliably available
                    "eps_estimate": _clean_numeric(row.get("EPS Estimate")),
                    "eps_actual": _clean_numeric(row.get("Reported EPS")),
                    "revenue_estimate": None,  # Not in get_earnings_dates
                    "revenue_actual": None,
                    "surprise_pct": _clean_numeric(row.get("Surprise(%)")),
                })

            return records if records else None

        return self._iterate_with_rate_limit(tickers, _fetch_single, "earnings_dates")


def _clean_numeric(value: Any) -> float | None:
    """Clean a numeric value from yfinance, returning None for invalid values."""
    if value is None:
        return None
    try:
        import math as _math

        val = float(value)
        if _math.isnan(val) or _math.isinf(val):
            return None
        return val
    except (ValueError, TypeError):
        return None


# Plausibility ranges for fundamental fields.
# Format: field_name -> (min_value, max_value, description)
# Values outside these ranges are suspicious and get nulled with a warning.
_PLAUSIBILITY_RULES: dict[str, tuple[float, float, str]] = {
    # Percentage fields (stored as decimal, 0-1 range)
    "profit_margin":     (-2.0,  1.0,  "Gewinnmarge -200% bis 100%"),
    "operating_margin":  (-2.0,  1.0,  "Operative Marge -200% bis 100%"),
    "return_on_equity":  (-5.0,  10.0, "ROE -500% bis 1000%"),
    "revenue_growth_yoy": (-1.0, 10.0, "Umsatzwachstum -100% bis 1000%"),
    "dividend_yield":    (0.0,   0.25, "Dividendenrendite 0% bis 25%"),
    "eps_growth_yoy":    (-5.0,  20.0, "EPS-Wachstum -500% bis 2000%"),
    # Ratio fields (no fixed percentage scale)
    "pe_ratio":          (0.0,   2000.0, "KGV 0 bis 2000"),
    "forward_pe":        (0.0,   500.0,  "Forward KGV 0 bis 500"),
    "ps_ratio":          (0.0,   500.0,  "KUV 0 bis 500"),
    "pb_ratio":          (0.0,   500.0,  "KBV 0 bis 500"),
    "ev_ebitda":         (-50.0, 500.0,  "EV/EBITDA -50 bis 500"),
    "debt_to_equity":    (0.0,   2000.0, "Debt/Equity 0 bis 2000"),
    "current_ratio":     (0.0,   50.0,   "Current Ratio 0 bis 50"),
    "beta":              (-3.0,  5.0,    "Beta -3 bis 5"),
    # Absolute fields (revenue_ttm excluded: ADRs report in local currency)
    "market_cap":        (0.0, 50e12,   "Market Cap 0 bis 50T$"),
    "eps_ttm":           (-500.0, 5000.0, "EPS -500 bis 5000"),
}


def _validate_fundamentals(record: dict) -> None:
    """Validate fundamental values against plausibility ranges.

    Values outside plausible ranges are set to None and logged as warnings.
    This protects against yfinance format changes and Yahoo data quality issues.

    Modifies the record dict in-place.
    """
    ticker = record.get("ticker", "???")

    for field, (min_val, max_val, desc) in _PLAUSIBILITY_RULES.items():
        value = record.get(field)
        if value is None:
            continue

        if not (min_val <= value <= max_val):
            logger.warning(
                f"[yfinance/plausibility] {ticker}.{field}={value} "
                f"outside plausible range [{min_val}, {max_val}] ({desc}). "
                f"Setting to None."
            )
            record[field] = None
