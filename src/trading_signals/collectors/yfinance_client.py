"""Shared yfinance client with rate-limiting, batching, and graceful error handling.

Used by all Sprint 5 collectors (Fundamentals, Analyst Ratings, Earnings Calendar)
to avoid redundant rate-limiting and error-handling code.

Key design decisions:
  - Each ticker is a separate yfinance.Ticker() call (no batch endpoint exists)
  - Rate-limiting: configurable delay between individual ticker calls
  - Batch pauses: longer delay between batches to avoid Yahoo rate-limits
  - Graceful errors: individual ticker failures are logged and skipped
  - Yahoo throttling (``YFRateLimitError``) is retried with exponential backoff
  - Dotted share-class tickers are mapped to Yahoo symbols (BRK.B → BRK-B)
    via :func:`to_yahoo_symbol`; results are always stored under the
    ORIGINAL universe ticker.
  - Per-ticker outcomes are reported via optional ``on_success`` /
    ``on_error`` callbacks (used for collector run status, H5). A valid
    empty answer (no data for a ticker) counts as success.
"""
import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from trading_signals.collectors._parsing import safe_float
from trading_signals.utils.logging import get_logger

try:  # yfinance >= 0.2.5x
    from yfinance.exceptions import YFRateLimitError
except Exception:  # pragma: no cover - older/newer yfinance without the class

    class YFRateLimitError(Exception):  # type: ignore[no-redef]
        """Fallback so ``except YFRateLimitError`` always works."""


logger = get_logger(__name__)

NY_TZ = ZoneInfo("America/New_York")

#: Retries after a Yahoo rate-limit error (per ticker) and base backoff (s).
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 30.0


def to_yahoo_symbol(ticker: str) -> str:
    """Map a universe/Alpaca ticker to its Yahoo Finance symbol.

    Yahoo uses ``-`` for share classes (``BRK.B`` → ``BRK-B``,
    ``BF/B`` → ``BF-B``). Everything else is passed through unchanged.
    """
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


def call_with_rate_limit_retry(
    fn: Callable[..., Any],
    *args: Any,
    retries: int = RATE_LIMIT_RETRIES,
    base_delay: float = RATE_LIMIT_BASE_DELAY,
    label: str = "yfinance",
) -> Any:
    """Call ``fn(*args)``; on ``YFRateLimitError`` back off exponentially.

    Delays: ``base_delay * 2**attempt`` (30s, 60s, 120s by default). After
    ``retries`` retries the last ``YFRateLimitError`` is re-raised.
    """
    for attempt in range(retries + 1):
        try:
            return fn(*args)
        except YFRateLimitError:
            if attempt >= retries:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                f"[{label}] Yahoo rate limit hit – retry {attempt + 1}/{retries} "
                f"in {delay:.0f}s"
            )
            time.sleep(delay)
    return None  # pragma: no cover - loop always returns or raises


def _try_get(getter: Callable[[], Any]) -> Any:
    """Return ``getter()`` or ``None`` on error – but never hide rate limits."""
    try:
        return getter()
    except YFRateLimitError:
        raise
    except Exception:
        return None


def _to_ny(ts: Any) -> Any:
    """Convert a tz-aware timestamp to New York time (naive passes through)."""
    if getattr(ts, "tzinfo", None) is not None:
        if hasattr(ts, "tz_convert"):
            return ts.tz_convert(NY_TZ)
        return ts.astimezone(NY_TZ)
    return ts


def _time_of_day(ts: Any) -> str | None:
    """Derive BMO/AMC from a yfinance earnings timestamp (ET).

    BMO if before 12:00 ET, AMC if at/after 16:00 ET, ``DMH`` (during
    market hours) in between. Midnight timestamps carry no time
    information → ``None``.
    """
    try:
        ts = _to_ny(ts)
        hour, minute = int(ts.hour), int(ts.minute)
    except Exception:
        return None
    if hour == 0 and minute == 0:
        return None
    if hour < 12:
        return "BMO"
    if hour >= 16:
        return "AMC"
    return "DMH"


def _ny_date(ts: Any) -> date | None:
    """Calendar date of a (possibly tz-aware) timestamp in New York time."""
    try:
        ts = _to_ny(ts)
        return ts.date() if hasattr(ts, "date") else None
    except Exception:
        return None


def _epoch_to_date(value: Any) -> date | None:
    """Convert epoch seconds (yfinance ``mostRecentQuarter``) to a date."""
    secs = safe_float(value)
    if secs is None or secs <= 0:
        return None
    try:
        return datetime.fromtimestamp(secs, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None

# Suppress yfinance's internal ERROR logging for expected cases like
# "No earnings dates found, symbol may be delisted". These are not
# real errors (just missing data for small-cap/special tickers) but
# yfinance logs them at ERROR level, polluting our log capture.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

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
        rate_limit_retries: Retries per ticker after ``YFRateLimitError``.
        rate_limit_base_delay: Base delay (s) of the exponential backoff.
    """

    def __init__(
        self,
        batch_size: int = 50,
        delay_between_tickers: float = 0.5,
        delay_between_batches: float = 3.0,
        rate_limit_retries: int = RATE_LIMIT_RETRIES,
        rate_limit_base_delay: float = RATE_LIMIT_BASE_DELAY,
    ) -> None:
        self.batch_size = batch_size
        self.delay_between_tickers = delay_between_tickers
        self.delay_between_batches = delay_between_batches
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_base_delay = rate_limit_base_delay

    def _iterate_with_rate_limit(
        self,
        tickers: list[str],
        fetch_fn: Any,
        label: str,
        on_success: Callable[[], Any] | None = None,
        on_error: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Iterate over tickers with rate-limiting and batch pauses.

        Args:
            tickers: List of ticker symbols to process (universe format).
            fetch_fn: Callable(ticker_str) -> dict | list[dict] | None.
                      Returns extracted data or None if the ticker has no data.
            label: Label for logging (e.g., "fundamentals").
            on_success: Called once per ticker that was fetched without an
                exception (also for valid empty answers).
            on_error: Called once per ticker whose fetch raised (incl.
                exhausted rate-limit retries).

        Returns:
            Flat list of all successfully extracted records.
        """
        results: list[dict] = []
        errors = 0
        skipped = 0
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
                    result = call_with_rate_limit_retry(
                        fetch_fn,
                        ticker_str,
                        retries=self.rate_limit_retries,
                        base_delay=self.rate_limit_base_delay,
                        label=f"yfinance/{label}",
                    )
                    if on_success:
                        on_success()
                    if result is not None:
                        if isinstance(result, list):
                            results.extend(result)
                        else:
                            results.append(result)
                    else:
                        skipped += 1
                        logger.debug(
                            f"[yfinance/{label}] {ticker_str}: no data returned"
                        )
                except Exception as e:
                    errors += 1
                    if on_error:
                        on_error()
                    logger.warning(
                        f"[yfinance/{label}] {ticker_str}: "
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
            f"{len(tickers)} tickers "
            f"({skipped} skipped, {errors} errors)"
        )
        return results

    def fetch_fundamentals(
        self,
        tickers: list[str],
        on_success: Callable[[], Any] | None = None,
        on_error: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Fetch fundamental data via ticker.info for each ticker.

        Extracts key financial metrics defined in FUNDAMENTALS_KEYS.
        Also attempts to fetch eps_growth_yoy from earnings estimates and
        stores ``mostRecentQuarter`` (epoch → date) as ``most_recent_quarter``.

        Returns:
            List of dicts with fundamental data, one per ticker.
        """

        def _fetch_single(ticker_str: str) -> dict | None:
            t = yf.Ticker(to_yahoo_symbol(ticker_str))
            info = t.info

            if not info or info.get("regularMarketPrice") is None:
                return None

            record: dict[str, Any] = {"ticker": ticker_str}
            for yf_key, db_key in FUNDAMENTALS_KEYS.items():
                value = info.get(yf_key)
                record[db_key] = _clean_numeric(value)

            # Fiscal quarter the TTM figures refer to (point-in-time context)
            record["most_recent_quarter"] = _epoch_to_date(
                info.get("mostRecentQuarter")
            )

            # yfinance returns dividendYield in percent form (0.4 = 0.4%)
            # while all other ratio fields are in decimal form (0.451 = 45.1%).
            # Normalize to decimal for consistent storage and display.
            if record.get("dividend_yield") is not None:
                record["dividend_yield"] = record["dividend_yield"] / 100

            # Plausibility checks – catch yfinance format changes / bad data
            _validate_fundamentals(record)

            # Attempt to get eps_growth_yoy from earnings estimate
            est = _try_get(t.get_earnings_estimate)
            try:
                if est is not None and not est.empty:
                    # Look for the current quarter (0q) growth
                    if "growth" in est.columns:
                        growth_val = (
                            est.loc["0q", "growth"] if "0q" in est.index else None
                        )
                        record["eps_growth_yoy"] = _clean_numeric(growth_val)
            except Exception:
                pass  # eps_growth_yoy stays as extracted from info (None)

            # Analyst consensus price targets
            targets = _try_get(lambda: t.analyst_price_targets)
            if targets and isinstance(targets, dict):
                record["target_price_low"] = _clean_numeric(targets.get("low"))
                record["target_price_mean"] = _clean_numeric(targets.get("mean"))
                record["target_price_median"] = _clean_numeric(targets.get("median"))
                record["target_price_high"] = _clean_numeric(targets.get("high"))

            return record

        return self._iterate_with_rate_limit(
            tickers, _fetch_single, "fundamentals", on_success, on_error
        )

    def fetch_sector_info(
        self,
        tickers: list[str],
        on_success: Callable[[], Any] | None = None,
        on_error: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Fetch sector and industry classification for each ticker.

        Uses ticker.info to extract sector/industry and quoteType.
        quoteType is used downstream to detect ETFs that slipped
        past the name-based heuristic.

        Returns:
            List of dicts with 'ticker', 'sector', 'industry', 'quote_type'.
        """

        def _fetch_single(ticker_str: str) -> dict | None:
            t = yf.Ticker(to_yahoo_symbol(ticker_str))
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

        return self._iterate_with_rate_limit(
            tickers, _fetch_single, "sector_info", on_success, on_error
        )

    def fetch_analyst_ratings(
        self,
        tickers: list[str],
        lookback_days: int = 30,
        on_success: Callable[[], Any] | None = None,
        on_error: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Fetch analyst upgrades/downgrades via ticker.upgrades_downgrades.

        Args:
            tickers: List of ticker symbols.
            lookback_days: Only return ratings from the last N days.

        Returns:
            List of dicts with rating data.
        """
        from datetime import timedelta

        cutoff = date.today() - timedelta(days=lookback_days)

        def _fetch_single(ticker_str: str) -> list[dict] | None:
            t = yf.Ticker(to_yahoo_symbol(ticker_str))
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

        return self._iterate_with_rate_limit(
            tickers, _fetch_single, "analyst_ratings", on_success, on_error
        )

    def fetch_earnings_dates(
        self,
        tickers: list[str],
        limit: int = 4,
        on_success: Callable[[], Any] | None = None,
        on_error: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Fetch earnings dates with EPS estimates and surprises.

        ``earnings_date`` is the New York calendar date of the announcement;
        ``time_of_day`` is derived from the announcement time (BMO < 12:00
        ET, AMC >= 16:00 ET, DMH in between, None if no time is known).

        Args:
            tickers: List of ticker symbols.
            limit: Max number of earnings dates per ticker.

        Returns:
            List of dicts with earnings calendar data.
        """

        def _fetch_single(ticker_str: str) -> list[dict] | None:
            t = yf.Ticker(to_yahoo_symbol(ticker_str))
            ed = t.get_earnings_dates(limit=limit)

            if ed is None or ed.empty:
                return None

            records = []
            for idx, row in ed.iterrows():
                earnings_date = _ny_date(idx)
                if earnings_date is None:
                    continue

                records.append({
                    "ticker": ticker_str,
                    "earnings_date": earnings_date,
                    "time_of_day": _time_of_day(idx),
                    "eps_estimate": _clean_numeric(row.get("EPS Estimate")),
                    "eps_actual": _clean_numeric(row.get("Reported EPS")),
                    "revenue_estimate": None,  # Not in get_earnings_dates
                    "revenue_actual": None,
                    "surprise_pct": _clean_numeric(row.get("Surprise(%)")),
                })

            return records if records else None

        return self._iterate_with_rate_limit(
            tickers, _fetch_single, "earnings_dates", on_success, on_error
        )

    def fetch_estimates(
        self,
        tickers: list[str],
        on_success: Callable[[], Any] | None = None,
        on_error: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Fetch EPS/Revenue consensus, revisions, and trend data.

        For each ticker, extracts data from four yfinance properties:
          - eps_trend:       EPS consensus at current, 7d, 30d, 60d, 90d ago
          - eps_revisions:   Count of up/down revisions in 7d and 30d
          - earnings_estimate: Avg/low/high EPS, analyst count, year-ago, growth
          - revenue_estimate:  Avg/low/high revenue, analyst count, year-ago, growth

        Returns one record per ticker per period (0q, +1q, 0y, +1y).

        CRITICAL: This data has a rolling 90-day window at Yahoo.
        What is not captured today is permanently lost in 90 days.

        Returns:
            List of dicts with estimate data, multiple per ticker (one per period).
        """
        # Period codes used by yfinance DataFrames
        periods = ["0q", "+1q", "0y", "+1y"]

        def _safe_df_value(df, row_key: str, col_key: str) -> Any:
            """Safely extract a value from a yfinance DataFrame."""
            try:
                if df is None or (hasattr(df, "empty") and df.empty):
                    return None
                if row_key in df.index and col_key in df.columns:
                    return _clean_numeric(df.loc[row_key, col_key])
            except Exception:
                pass
            return None

        def _fetch_single(ticker_str: str) -> list[dict] | None:
            t = yf.Ticker(to_yahoo_symbol(ticker_str))

            # Fetch all four data sources (rate limits propagate → retry)
            eps_trend = _try_get(lambda: t.eps_trend)
            eps_revisions = _try_get(lambda: t.eps_revisions)
            earnings_est = _try_get(lambda: t.earnings_estimate)
            revenue_est = _try_get(lambda: t.revenue_estimate)

            # If all sources returned nothing, skip this ticker
            all_empty = all(
                df is None or (hasattr(df, "empty") and df.empty)
                for df in [eps_trend, eps_revisions, earnings_est, revenue_est]
            )
            if all_empty:
                return None

            # Build raw dict for JSONB storage (future-proofing)
            raw_data: dict[str, Any] = {}
            for name, df in [
                ("eps_trend", eps_trend),
                ("eps_revisions", eps_revisions),
                ("earnings_estimate", earnings_est),
                ("revenue_estimate", revenue_est),
            ]:
                if df is not None and hasattr(df, "to_dict"):
                    try:
                        raw_data[name] = {
                            str(k): {str(k2): str(v2) for k2, v2 in v.items()}
                            for k, v in df.to_dict().items()
                        }
                    except Exception:
                        raw_data[name] = str(df)

            records = []
            for period in periods:
                record = {
                    "ticker": ticker_str,
                    "period": period,
                    # EPS Consensus (from earnings_estimate)
                    "eps_avg": _safe_df_value(earnings_est, period, "avg"),
                    "eps_low": _safe_df_value(earnings_est, period, "low"),
                    "eps_high": _safe_df_value(earnings_est, period, "high"),
                    "eps_n_analysts": None,
                    "eps_year_ago": _safe_df_value(earnings_est, period, "yearAgoEps"),
                    "eps_growth": _safe_df_value(earnings_est, period, "growth"),
                    # EPS Trend (rolling 90-day window)
                    "eps_current": _safe_df_value(eps_trend, period, "current"),
                    "eps_7d_ago": _safe_df_value(eps_trend, period, "7daysAgo"),
                    "eps_30d_ago": _safe_df_value(eps_trend, period, "30daysAgo"),
                    "eps_60d_ago": _safe_df_value(eps_trend, period, "60daysAgo"),
                    "eps_90d_ago": _safe_df_value(eps_trend, period, "90daysAgo"),
                    # Revision counts
                    "rev_up_7d": None,
                    "rev_up_30d": None,
                    "rev_down_7d": None,
                    "rev_down_30d": None,
                    # Revenue Consensus (from revenue_estimate)
                    "revenue_avg": _safe_df_value(revenue_est, period, "avg"),
                    "revenue_low": _safe_df_value(revenue_est, period, "low"),
                    "revenue_high": _safe_df_value(revenue_est, period, "high"),
                    "revenue_n_analysts": None,
                    "revenue_year_ago": _safe_df_value(
                        revenue_est, period, "yearAgoRevenue"
                    ),
                    "revenue_growth": _safe_df_value(revenue_est, period, "growth"),
                    # Raw data (same for all periods of this ticker)
                    "raw": raw_data,
                }

                # Extract numberOfAnalysts (column name varies)
                for col in ["numberOfAnalysts", "numberOfAnalyst"]:
                    val = _safe_df_value(earnings_est, period, col)
                    if val is not None:
                        record["eps_n_analysts"] = int(val)
                        break

                for col in ["numberOfAnalysts", "numberOfAnalyst"]:
                    val = _safe_df_value(revenue_est, period, col)
                    if val is not None:
                        record["revenue_n_analysts"] = int(val)
                        break

                # Extract revision counts (eps_revisions has different row keys)
                rev_up_7d = _safe_df_value(eps_revisions, period, "upLast7days")
                rev_up_30d = _safe_df_value(eps_revisions, period, "upLast30days")
                rev_down_7d = _safe_df_value(eps_revisions, period, "downLast7days")
                rev_down_30d = _safe_df_value(eps_revisions, period, "downLast30days")
                if rev_up_7d is not None:
                    record["rev_up_7d"] = int(rev_up_7d)
                if rev_up_30d is not None:
                    record["rev_up_30d"] = int(rev_up_30d)
                if rev_down_7d is not None:
                    record["rev_down_7d"] = int(rev_down_7d)
                if rev_down_30d is not None:
                    record["rev_down_30d"] = int(rev_down_30d)

                # Only add record if it has at least some data
                has_data = any(
                    record.get(k) is not None
                    for k in ["eps_avg", "eps_current", "revenue_avg", "rev_up_30d"]
                )
                if has_data:
                    records.append(record)

            return records if records else None

        return self._iterate_with_rate_limit(
            tickers, _fetch_single, "estimates", on_success, on_error
        )


# Backwards-compatible alias (scripts/debug_dividend.py, tests): the shared
# implementation lives in collectors/_parsing.py.
_clean_numeric = safe_float


# Plausibility ranges for fundamental fields.
# Format: field_name -> (min_value, max_value, description)
# Values outside these ranges are suspicious and get nulled with a warning.
#
# IMPORTANT: These ranges are designed to catch DATA CORRUPTION and
# yfinance FORMAT CHANGES (like the dividendYield scaling bug), NOT to
# filter legitimate extreme values. Pre-revenue biotechs, aggressive
# buyback companies, and high-growth SaaS firms regularly produce
# extreme-but-real ratios.
#
# The dividend_yield range [0, 0.25] is intentionally tight – it's the
# regression guard for the /100 normalization fix (Migration 013).
# See DECISIONS.md [2026-04-15] and LEARNINGS.md for context.
_PLAUSIBILITY_RULES: dict[str, tuple[float, float, str]] = {
    # Percentage fields (stored as decimal)
    # Negative margins are common for pre-revenue companies (ACHR -781%, AUR -238%)
    "profit_margin":     (-50.0,  5.0,   "Gewinnmarge -5000% bis 500%"),
    "operating_margin":  (-1000.0, 5.0,  "Operative Marge -100000% bis 500%"),
    "return_on_equity":  (-100.0, 100.0, "ROE -10000% bis 10000%"),
    "revenue_growth_yoy": (-1.0, 1000.0, "Umsatzwachstum -100% bis 100000%"),
    "dividend_yield":    (0.0,   0.25,   "Dividendenrendite 0% bis 25% (Format-Guard)"),
    "eps_growth_yoy":    (-50.0, 100.0,  "EPS-Wachstum -5000% bis 10000%"),
    # Ratio fields – negative values are legitimate for many of these
    # Negative P/B: companies with negative equity (buybacks: MCD, SBUX, BKNG)
    # Negative Forward P/E: expected losses (MRNA, OKLO, RBLX)
    "pe_ratio":          (0.0,    10000.0,  "KGV 0 bis 10000"),
    "forward_pe":        (-10000.0, 10000.0, "Forward KGV -10000 bis 10000"),
    "ps_ratio":          (0.0,    50000.0,  "KUV 0 bis 50000"),
    "pb_ratio":          (-5000.0, 5000.0,  "KBV -5000 bis 5000"),
    "ev_ebitda":         (-10000.0, 10000.0, "EV/EBITDA -10000 bis 10000"),
    "debt_to_equity":    (0.0,    50000.0,  "Debt/Equity 0 bis 50000"),
    "current_ratio":     (0.0,    500.0,    "Current Ratio 0 bis 500"),
    "beta":              (-10.0,  10.0,     "Beta -10 bis 10"),
    # Absolute fields (revenue_ttm excluded: ADRs report in local currency)
    "market_cap":        (0.0, 100e12,  "Market Cap 0 bis 100T$"),
    "eps_ttm":           (-1000.0, 10000.0, "EPS -1000 bis 10000"),
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
