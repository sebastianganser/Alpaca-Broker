"""Politician Trades Collector – US Congress stock disclosures.

Collects Periodic Transaction Reports (PTRs) from the official
Senate Electronic Financial Disclosure portal (efdsearch.senate.gov).

Strategy:
  1. Search Senate eFD for PTR filings in the lookback window
  2. For each electronic filing → parse HTML transaction table
  3. Store with ON CONFLICT DO NOTHING (dedup via unique constraint)

House PTRs are PDF-only and not yet supported (future enhancement).

Schedule: Weekly Sunday 11:00 MEZ (trades are 30-45 days delayed anyway)
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.disclosure_client import DisclosureClient
from trading_signals.db.models.politicians import PoliticianTrade
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class PoliticianTradesCollector(BaseCollector):
    """Collect politician stock trades from official disclosure portals."""

    name = "politician_trades_collector"

    def __init__(self, lookback_days: int = 365) -> None:
        """Initialize with a lookback window.

        Args:
            lookback_days: How many days back to search for filings.
                          Default 365 ensures we catch delayed disclosures
                          (STOCK Act allows up to 45 days).
        """
        self.lookback_days = lookback_days
        self.client = DisclosureClient()

    def fetch(self, session: Session) -> list[dict]:
        """Fetch politician trades from Senate eFD.

        Returns:
            List of transaction dicts ready for storage.
        """
        since_date = date.today() - timedelta(days=self.lookback_days)
        all_trades: list[dict] = []
        errors = 0

        # ── Senate PTRs ──────────────────────────────────────────
        logger.info(f"[{self.name}] Fetching Senate PTR filings since {since_date}...")

        try:
            filings = self.client.fetch_senate_ptrs(
                from_date=since_date,
                to_date=date.today(),
            )
        except Exception as e:
            logger.error(f"[{self.name}] Failed to fetch Senate PTR list: {e}")
            filings = []
            errors += 1

        logger.info(
            f"[{self.name}] Senate PTR search returned {len(filings)} filings"
        )
        for i, f in enumerate(filings[:5]):  # Log first 5 for debugging
            logger.info(
                f"[{self.name}]   Filing {i+1}: "
                f"{f.get('first_name', '')} {f.get('last_name', '')} "
                f"({f.get('date_filed', '')}) -> {f.get('ptr_link', '')}"
            )

        filings_processed = 0
        for filing in filings:
            try:
                transactions = self.client.fetch_senate_ptr_transactions(
                    filing["ptr_link"]
                )
            except Exception as e:
                logger.info(
                    f"[{self.name}] Failed to parse PTR for "
                    f"{filing.get('first_name', '')} {filing.get('last_name', '')}: {e}"
                )
                errors += 1
                continue

            filings_processed += 1
            politician_name = (
                f"{filing.get('first_name', '')} {filing.get('last_name', '')}"
            ).strip()

            # Parse disclosure date
            disclosure_date = self._parse_disclosure_date(
                filing.get("date_filed", "")
            )

            for txn in transactions:
                trade = {
                    "politician_name": politician_name,
                    "chamber": "Senate",
                    "party": None,  # Not available from eFD search results
                    "state": self._extract_state(filing.get("office", "")),
                    "ticker": txn.get("ticker", ""),
                    "transaction_date": txn.get("transaction_date"),
                    "disclosure_date": disclosure_date,
                    "transaction_type": txn.get("transaction_type", ""),
                    "amount_range": txn.get("amount", ""),
                    "owner": txn.get("owner", ""),
                    "asset_description": txn.get("asset_name", ""),
                    "comment": txn.get("comment", ""),
                    "source_url": filing.get("ptr_link", ""),
                    "raw_data": {
                        "asset_type": txn.get("asset_type", ""),
                        "office": filing.get("office", ""),
                        "report_type": filing.get("report_type", ""),
                    },
                }

                # Only include trades with a valid ticker
                if trade["ticker"] and len(trade["ticker"]) <= 10:
                    all_trades.append(trade)

        logger.info(
            f"[{self.name}] Senate: processed {filings_processed}/{len(filings)} "
            f"filings, found {len(all_trades)} stock transactions. "
            f"Errors: {errors}"
        )

        # ── House PTRs (future) ──────────────────────────────────
        # House filings are PDF-only. To be implemented when PDF
        # parsing is added. For now, Senate-only.
        logger.info(
            f"[{self.name}] House PTRs skipped (PDF-only, not yet supported)"
        )

        return all_trades

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store politician trades with dedup via unique constraint.

        After storing, checks all traded tickers against the universe
        and auto-onboards any new tickers (Alpaca validation + backfill).

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0

        # Collect all unique tickers for universe expansion
        all_tickers: set[str] = set()

        for trade in data:
            ticker = trade.get("ticker", "")
            if ticker:
                all_tickers.add(ticker)

            stmt = (
                pg_insert(PoliticianTrade)
                .values(**trade)
                .on_conflict_do_nothing(
                    constraint="uq_politician_trade_dedup"
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"trades ({records_fetched - records_written} already existed)"
        )

        # Expand universe with new tickers + auto-backfill
        if all_tickers:
            from trading_signals.universe.onboarder import NewTickerOnboarder

            onboarder = NewTickerOnboarder(session)
            new_tickers = onboarder.onboard(
                tickers=all_tickers,
                source="politician_trades",
            )
            if new_tickers:
                logger.info(
                    f"[{self.name}] Auto-onboarded {len(new_tickers)} new "
                    f"tickers: {new_tickers}"
                )

        return records_fetched, records_written

    @staticmethod
    def _parse_disclosure_date(date_str: str) -> date | None:
        """Parse the disclosure/filing date from search results."""
        if not date_str:
            return None
        from datetime import datetime

        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_state(office_str: str) -> str | None:
        """Extract 2-letter state code from office string.

        Senate office strings often contain state info like
        'United States Senate (CA)' or similar patterns.
        """
        if not office_str:
            return None

        import re

        # Look for 2-letter state code in parentheses
        match = re.search(r"\(([A-Z]{2})\)", office_str.upper())
        if match:
            return match.group(1)

        # Some formats list the state directly
        state_match = re.search(r"\b([A-Z]{2})\b", office_str.upper())
        if state_match and len(office_str) <= 5:
            return state_match.group(1)

        return None
