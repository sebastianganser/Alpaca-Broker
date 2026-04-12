"""ARK Holdings Collector – daily snapshots from arkfunds.io API.

Downloads holdings data for all active ARK ETFs, stores snapshots,
and expands the universe with new tickers that are tradeable on Alpaca.

Data Source: https://arkfunds.io/api/v2/etf/holdings?symbol={ETF}
"""

import re
from datetime import date, datetime
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.db.models.ark import ARKHolding
from trading_signals.universe.alpaca_validator import AlpacaAssetValidator
from trading_signals.universe.manager import UniverseManager
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Active ARK ETFs to track
ARK_ETFS = ["ARKK", "ARKQ", "ARKW", "ARKG", "ARKF", "ARKX", "PRNT", "IZRL"]

# arkfunds.io API base URL
API_BASE = "https://arkfunds.io/api/v2/etf/holdings"

# Regex to identify non-equity rows (cash positions, money market, etc.)
NON_EQUITY_RE = re.compile(
    r"(^$|CASH|TRSY|TREASURY|MONEY MARKET|GOLDMAN FS|US DOLLAR|FUTURE)",
    re.IGNORECASE,
)


class ARKHoldingsCollector(BaseCollector):
    """Collect daily ARK ETF holdings from arkfunds.io."""

    name = "ark_holdings"

    def fetch(self, session: Session) -> dict[str, list[dict]]:
        """Fetch holdings for all ARK ETFs.

        Returns:
            Dict mapping ETF ticker -> list of holding dicts.
        """
        all_data: dict[str, list[dict]] = {}
        failed_etfs: list[str] = []

        for etf in ARK_ETFS:
            try:
                holdings = _fetch_etf_holdings(etf)
                if holdings:
                    # Filter out non-equity positions (cash, treasuries, etc.)
                    equity_holdings = [
                        h for h in holdings
                        if h.get("ticker")
                        and not NON_EQUITY_RE.search(h.get("ticker", ""))
                        and not NON_EQUITY_RE.search(h.get("company", ""))
                    ]
                    all_data[etf] = equity_holdings
                    logger.info(
                        f"[{self.name}] {etf}: {len(equity_holdings)} positions "
                        f"({len(holdings) - len(equity_holdings)} non-equity filtered)"
                    )
                else:
                    logger.warning(f"[{self.name}] {etf}: no data returned")
                    failed_etfs.append(etf)
            except Exception as e:
                logger.error(f"[{self.name}] {etf} failed: {e}")
                failed_etfs.append(etf)

        total = sum(len(h) for h in all_data.values())
        logger.info(
            f"[{self.name}] Fetched {total} positions across "
            f"{len(all_data)} ETFs. Failed: {len(failed_etfs)}"
        )
        return all_data

    def store(
        self, session: Session, data: dict[str, list[dict]]
    ) -> tuple[int, int]:
        """Store holdings and expand universe with new tickers.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = 0
        records_written = 0

        # Collect all unique tickers from ARK data for universe expansion
        ark_tickers: set[str] = set()

        for etf, holdings in data.items():
            for h in holdings:
                ticker = h.get("ticker", "").strip()
                if not ticker:
                    continue

                records_fetched += 1
                ark_tickers.add(ticker)

                snapshot_date = _parse_date(h.get("date", ""))
                if not snapshot_date:
                    continue

                stmt = (
                    pg_insert(ARKHolding)
                    .values(
                        snapshot_date=snapshot_date,
                        etf_ticker=etf,
                        ticker=ticker,
                        company_name=h.get("company"),
                        cusip=h.get("cusip"),
                        shares=_safe_numeric(h.get("shares")),
                        market_value=_safe_numeric(h.get("market_value")),
                        weight_pct=_safe_numeric(h.get("weight")),
                        weight_rank=h.get("weight_rank"),
                        share_price=_safe_numeric(h.get("share_price")),
                        source="arkfunds.io",
                    )
                    .on_conflict_do_nothing(
                        index_elements=["snapshot_date", "etf_ticker", "ticker"]
                    )
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
                    records_written += 1

        session.flush()

        # Expand universe with new Alpaca-tradeable tickers
        if ark_tickers:
            new_tickers = self._expand_universe(session, ark_tickers, data)
            if new_tickers:
                logger.info(
                    f"[{self.name}] Added {len(new_tickers)} new tickers to universe: "
                    f"{sorted(new_tickers)}"
                )

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} holdings "
            f"({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written

    def _expand_universe(
        self,
        session: Session,
        ark_tickers: set[str],
        data: dict[str, list[dict]],
    ) -> list[str]:
        """Add new Alpaca-tradeable tickers from ARK to the universe.

        Only adds tickers that:
          1. Don't already exist in our universe
          2. Are tradeable on Alpaca (validates via API)
        """
        manager = UniverseManager(session)

        # Get existing universe tickers
        existing = {t.ticker for t in manager.get_active_tickers()}

        # Also check inactive tickers (they might just need reactivation)
        from trading_signals.db.models.universe import Universe
        all_stmt = select(Universe.ticker)
        all_existing = {row[0] for row in session.execute(all_stmt).all()}

        new_tickers = ark_tickers - all_existing
        if not new_tickers:
            return []

        logger.info(
            f"[{self.name}] Found {len(new_tickers)} new tickers in ARK data. "
            f"Validating against Alpaca..."
        )

        # Validate against Alpaca
        try:
            validator = AlpacaAssetValidator()
            alpaca_assets = validator.fetch_all_assets()
        except Exception as e:
            logger.warning(f"[{self.name}] Alpaca validation failed: {e}. Skipping universe expansion.")
            return []

        added: list[str] = []
        for ticker in sorted(new_tickers):
            asset = alpaca_assets.get(ticker)
            if asset and asset.tradable:
                # Find company name from ARK data
                company_name = None
                for holdings in data.values():
                    for h in holdings:
                        if h.get("ticker") == ticker:
                            company_name = h.get("company")
                            break
                    if company_name:
                        break

                manager.add_ticker(
                    ticker=ticker,
                    company_name=company_name,
                    added_by="ark_etf",
                    exchange=asset.exchange,
                )
                added.append(ticker)
            else:
                reason = "not found" if not asset else "not tradeable"
                logger.debug(
                    f"[{self.name}] Skipping {ticker}: {reason} on Alpaca"
                )

        return added


@retry(max_attempts=3, base_delay=2.0)
def _fetch_etf_holdings(etf: str) -> list[dict]:
    """Fetch holdings for a single ARK ETF from arkfunds.io."""
    response = requests.get(
        API_BASE,
        params={"symbol": etf},
        headers={"User-Agent": "Trading-Signals/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("holdings", [])


def _parse_date(date_str: str) -> date | None:
    """Parse a date string (YYYY-MM-DD) from the API."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_numeric(value) -> float | None:
    """Safely convert to float, handling None and invalid values."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
