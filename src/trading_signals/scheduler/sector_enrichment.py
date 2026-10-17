"""Shared sector/industry enrichment (used by index_sync job and the UI backfill).

Fetches ``sector``/``industry``/``quoteType`` from yfinance and applies them
to the universe. Non-equity tickers (ETFs, funds, …) are blacklisted and
deactivated ("learned ETF filter") -- except current index members, which are
equities by definition (see :func:`apply_sector_records`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Tickers per yfinance request batch (pause between batches).
BATCH_SIZE = 50


@dataclass
class SectorEnrichmentResult:
    checked: int = 0
    enriched: int = 0
    deactivated: list[str] = field(default_factory=list)
    failed: int = 0  # tickers without any yfinance result


def open_index_members(session, tickers: list[str]) -> set[str]:
    """Tickers of ``tickers`` that are current members of any index.

    A current member has an ``index_membership`` interval that is still open
    (``valid_to IS NULL``), whatever the index (S&P 500, Nasdaq 100). One query.
    """
    from sqlalchemy import select

    from trading_signals.db.models.index_membership import IndexMembership

    if not tickers:
        return set()
    stmt = (
        select(IndexMembership.ticker)
        .where(
            IndexMembership.ticker.in_(tickers),
            IndexMembership.valid_to.is_(None),
        )
        .distinct()
    )
    return {row[0] for row in session.execute(stmt).all()}


def apply_sector_records(
    session, records: list[dict], source: str
) -> SectorEnrichmentResult:
    """Write fetched sector records to the universe (no commit).

    The learned ETF filter never blacklists or deactivates a current index
    member: membership of the S&P 500 / Nasdaq 100 proves the ticker is an
    equity, so a non-equity ``quoteType`` is a wrong lookup (BF.B was
    blacklisted as MUTUALFUND on 2026-04-15 after a lookup with the wrong
    Yahoo symbol). Such a record is logged and takes the normal sector path.
    The benchmark/sector ETFs (``BENCHMARK_TICKERS``) are kept the same way:
    they cannot be blacklisted and must never be deactivated.
    """
    from sqlalchemy import update

    from trading_signals.db.models.universe import Universe
    from trading_signals.universe.blacklist import BENCHMARK_TICKERS, add_to_blacklist

    def is_non_equity(record: dict) -> bool:
        quote_type = record.get("quote_type") or ""
        return bool(quote_type) and quote_type.upper() != "EQUITY"

    # One query for the whole call, and only if there is a candidate at all
    index_members = open_index_members(
        session, [r["ticker"] for r in records if is_non_equity(r)]
    )

    result = SectorEnrichmentResult()
    for record in records:
        ticker = record["ticker"]
        quote_type = record.get("quote_type") or ""

        if is_non_equity(record):
            if ticker in BENCHMARK_TICKERS:
                # Wanted ETF (SPY, QQQ, sector SPDRs): keep it active
                logger.debug(f"[{source}] {ticker} is a protected benchmark ETF")
            elif ticker in index_members:
                # Wrong lookup, not a fund: keep the ticker and update its sector
                logger.warning(
                    f"[{source}] quoteType={quote_type} for index member {ticker}"
                    " - not blacklisted"
                )
            else:
                # Learned ETF filter: blacklist + deactivate non-equity tickers
                add_to_blacklist(
                    session, ticker, quote_type=quote_type, source=source
                )
                session.execute(
                    update(Universe)
                    .where(Universe.ticker == ticker)
                    .values(is_active=False)
                )
                result.deactivated.append(ticker)
                logger.warning(
                    f"[{source}] blacklisted + deactivated {ticker}: "
                    f"quoteType={quote_type}"
                )
                continue

        # Never overwrite existing data with empty values
        if record.get("sector") or record.get("industry"):
            session.execute(
                update(Universe)
                .where(Universe.ticker == ticker)
                .values(sector=record.get("sector"), industry=record.get("industry"))
            )
            result.enriched += 1
    return result


def enrich_sectors(
    tickers: list[str],
    source: str,
    progress: Callable[[int, int, str | None], None] | None = None,
    client=None,
    batch_pause: float = 3.0,
) -> SectorEnrichmentResult:
    """Fetch sector info for ``tickers`` and apply it (one commit at the end).

    Args:
        tickers: Tickers to (re-)check.
        source: Label for logs/blacklist entries ("index_sync", "manual_enrichment").
        progress: Optional callback ``(processed, total, current_ticker)``.
        client: Optional ``YFinanceClient`` (for tests).
        batch_pause: Seconds to pause between batches.
    """
    from trading_signals.db.session import get_session

    if client is None:
        from trading_signals.collectors.yfinance_client import YFinanceClient

        client = YFinanceClient(
            batch_size=BATCH_SIZE,
            delay_between_tickers=0.5,
            delay_between_batches=batch_pause,
        )

    records: list[dict] = []
    total = len(tickers)
    for start in range(0, total, BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        if progress:
            progress(start, total, batch[0] if batch else None)
        records.extend(client.fetch_sector_info(batch))
        if start + BATCH_SIZE < total and batch_pause:
            time.sleep(batch_pause)
    if progress:
        progress(total, total, None)

    with get_session() as session:
        result = apply_sector_records(session, records, source)
    result.checked = total
    result.failed = total - len({r["ticker"] for r in records})
    logger.info(
        f"[{source}] sector enrichment: {result.enriched}/{total} enriched, "
        f"{len(result.deactivated)} blacklisted, {result.failed} without data"
    )
    return result
