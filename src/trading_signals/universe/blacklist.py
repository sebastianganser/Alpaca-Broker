"""Ticker Blacklist – learning filter for non-equity tickers.

Provides helper functions to check tickers against the blacklist
and to add newly discovered non-equities. The blacklist grows over
time as the sector enrichment identifies ETFs, mutual funds, etc.

Usage:
    from trading_signals.universe.blacklist import is_blacklisted, add_to_blacklist

    # Check before onboarding
    if is_blacklisted(session, "SPY"):
        skip(...)

    # Add during sector enrichment
    add_to_blacklist(session, "SPY", quote_type="ETF", source="sector_enrichment")
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.blacklist import TickerBlacklist
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Benchmark tickers that must NEVER be blacklisted.
# These are needed as reference instruments (e.g. relative strength vs SPY,
# sector-relative performance via GICS SPDR ETFs) even though they are ETFs.
# Sprint 9.5b B3: Extended from {SPY, QQQ} to include IWM + 11 GICS sectors.
BENCHMARK_TICKERS = {
    # Market benchmarks
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "IWM",   # Russell 2000 (small-cap)
    # GICS Sector SPDRs (11 sectors)
    "XLB",   # Materials
    "XLC",   # Communication Services
    "XLE",   # Energy
    "XLF",   # Financials
    "XLI",   # Industrials
    "XLK",   # Technology
    "XLP",   # Consumer Staples
    "XLRE",  # Real Estate
    "XLU",   # Utilities
    "XLV",   # Health Care
    "XLY",   # Consumer Discretionary
}


def is_blacklisted(session: Session, ticker: str) -> bool:
    """Check if a ticker is on the blacklist."""
    if ticker in BENCHMARK_TICKERS:
        return False
    stmt = select(TickerBlacklist.ticker).where(TickerBlacklist.ticker == ticker)
    return session.execute(stmt).first() is not None


def get_blacklisted_tickers(session: Session) -> set[str]:
    """Return all blacklisted ticker symbols as a set."""
    stmt = select(TickerBlacklist.ticker)
    return {row[0] for row in session.execute(stmt).all()} - BENCHMARK_TICKERS


def add_to_blacklist(
    session: Session,
    ticker: str,
    *,
    quote_type: str | None = None,
    source: str = "unknown",
    reason: str | None = None,
) -> bool:
    """Add a ticker to the blacklist. Returns True if newly added.

    Uses INSERT ... ON CONFLICT DO NOTHING so it's safe to call
    multiple times for the same ticker.
    Benchmark tickers (SPY, QQQ) are protected and cannot be blacklisted.
    """
    if ticker in BENCHMARK_TICKERS:
        logger.debug(
            f"[blacklist] Skipping {ticker}: protected benchmark ticker"
        )
        return False

    if reason is None:
        reason = f"quoteType={quote_type}" if quote_type else "non-equity"

    stmt = (
        pg_insert(TickerBlacklist)
        .values(
            ticker=ticker,
            reason=reason,
            quote_type=quote_type,
            source=source,
        )
        .on_conflict_do_nothing(index_elements=["ticker"])
    )
    result = session.execute(stmt)
    if result.rowcount > 0:
        logger.info(f"[blacklist] Added {ticker} (quote_type={quote_type}, source={source})")
        return True
    return False


def filter_blacklisted(session: Session, tickers: set[str]) -> tuple[set[str], set[str]]:
    """Split tickers into allowed and blacklisted sets.

    Returns:
        (allowed, blocked) tuple of ticker sets.
    """
    blacklisted = get_blacklisted_tickers(session)
    blocked = tickers & blacklisted
    allowed = tickers - blacklisted
    return allowed, blocked
