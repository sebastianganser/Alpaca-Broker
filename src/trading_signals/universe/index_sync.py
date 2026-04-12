"""Index Syncer – synchronize S&P 500 / Nasdaq 100 membership.

Downloads current index constituent lists from Wikipedia and updates
the universe table with index_membership info. New tickers are validated
against Alpaca before being added.

Sources:
  - S&P 500: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  - Nasdaq 100: https://en.wikipedia.org/wiki/Nasdaq-100
"""

import io
from dataclasses import dataclass, field

import pandas as pd
import requests
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trading_signals.db.models.universe import Universe
from trading_signals.universe.alpaca_validator import AlpacaAssetValidator
from trading_signals.universe.manager import UniverseManager
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (Trading-Signals IndexSyncer/1.0)"


@dataclass
class SyncResult:
    """Result of an index sync operation."""

    sp500_count: int = 0
    nasdaq100_count: int = 0
    already_existed: int = 0
    newly_added: int = 0
    not_tradeable: int = 0
    membership_updated: int = 0
    new_tickers: list[str] = field(default_factory=list)


class IndexSyncer:
    """Synchronize index constituent lists with the universe."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._manager = UniverseManager(session)

    def sync(self, dry_run: bool = False) -> SyncResult:
        """Fetch index lists and sync with universe.

        Args:
            dry_run: If True, only report what would change.

        Returns:
            SyncResult with details.
        """
        result = SyncResult()

        # Fetch index lists
        sp500 = self._fetch_sp500()
        nasdaq100 = self._fetch_nasdaq100()
        result.sp500_count = len(sp500)
        result.nasdaq100_count = len(nasdaq100)

        logger.info(
            f"[index_sync] Fetched S&P 500: {len(sp500)}, "
            f"Nasdaq 100: {len(nasdaq100)}"
        )

        # Build membership map: ticker -> set of indexes
        membership: dict[str, set[str]] = {}
        for ticker in sp500:
            membership.setdefault(ticker, set()).add("sp500")
        for ticker in nasdaq100:
            membership.setdefault(ticker, set()).add("nasdaq100")

        # Get existing universe
        existing = {
            row[0] for row in
            self.session.execute(select(Universe.ticker)).all()
        }

        # Validate new tickers against Alpaca
        new_tickers = set(membership.keys()) - existing
        tradeable_new: dict[str, dict] = {}

        if new_tickers:
            logger.info(
                f"[index_sync] {len(new_tickers)} new tickers to validate "
                f"against Alpaca..."
            )
            try:
                validator = AlpacaAssetValidator()
                assets = validator.fetch_all_assets()
                for ticker in new_tickers:
                    asset = assets.get(ticker)
                    if asset and asset.tradable:
                        tradeable_new[ticker] = {
                            "exchange": asset.exchange,
                            "name": asset.name,
                        }
                    else:
                        result.not_tradeable += 1
                        logger.debug(
                            f"[index_sync] Skipping {ticker}: "
                            f"not tradeable on Alpaca"
                        )
            except Exception as e:
                logger.warning(
                    f"[index_sync] Alpaca validation failed: {e}. "
                    f"Skipping new ticker additions."
                )
                tradeable_new = {}

        result.newly_added = len(tradeable_new)
        result.already_existed = len(membership) - len(new_tickers)
        result.new_tickers = sorted(tradeable_new.keys())

        if dry_run:
            logger.info(
                f"[index_sync] DRY RUN: would add {len(tradeable_new)} "
                f"new tickers and update {len(membership)} memberships"
            )
            return result

        # Add new tickers
        for ticker, info in tradeable_new.items():
            indexes = sorted(membership[ticker])
            added_by = indexes[0]  # Primary index
            self._manager.add_ticker(
                ticker=ticker,
                company_name=info.get("name"),
                exchange=info.get("exchange"),
                added_by=added_by,
            )
            # Set index_membership
            self.session.execute(
                update(Universe)
                .where(Universe.ticker == ticker)
                .values(index_membership=indexes)
            )

        # Update index_membership for existing tickers
        for ticker, indexes in membership.items():
            if ticker in existing:
                sorted_indexes = sorted(indexes)
                self.session.execute(
                    update(Universe)
                    .where(Universe.ticker == ticker)
                    .values(index_membership=sorted_indexes)
                )
                result.membership_updated += 1

        self.session.flush()
        logger.info(
            f"[index_sync] Done: {result.newly_added} added, "
            f"{result.membership_updated} memberships updated"
        )
        return result

    def _fetch_sp500(self) -> set[str]:
        """Fetch S&P 500 tickers from Wikipedia."""
        r = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        df = tables[0]
        tickers = set(df["Symbol"].str.strip().tolist())
        return tickers

    def _fetch_nasdaq100(self) -> set[str]:
        """Fetch Nasdaq 100 tickers from Wikipedia."""
        r = requests.get(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        for t in tables:
            if len(t) > 90:
                for col in t.columns:
                    if "ticker" in str(col).lower() or "symbol" in str(col).lower():
                        return set(t[col].dropna().str.strip().tolist())
        logger.warning("[index_sync] Nasdaq 100 table not found on Wikipedia")
        return set()
