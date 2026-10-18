"""SEC XBRL quarterly financials collector - weekly standardised fundamentals.

For every active universe ticker with a CIK the collector downloads the XBRL
``companyfacts`` JSON (``data.sec.gov/api/xbrl/companyfacts/CIK##########.json``,
one request per company, 0.3-0.5 MB compressed), turns it into standalone fiscal
quarters (:func:`~trading_signals.collectors.sec_xbrl.build_quarterly_rows`:
revenue, gross profit, operating income, net income, diluted EPS, operating
cash flow, capex, free cash flow, cash, debt, shares) and upserts them into
``signals.financials_quarterly``. Only quarters inside the retention window
(``data_start_date()``) are stored. The facts of a company are re-read in full
every week: a new 10-Q/10-K adds a quarter, nothing else changes (values are
"as first reported", see ``sec_xbrl``).

Share classes that share a CIK (GOOG/GOOGL) are downloaded once and stored for
each ticker.

Request volume per run: 1 (``company_tickers.json``) + ~650 ``companyfacts``
(one per CIK) at <= 9 requests/s (SECClient): ~5-10 minutes, ~250 MB.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.sec_client import SECClient
from trading_signals.collectors.sec_xbrl import VALUE_COLUMNS, build_quarterly_rows
from trading_signals.db.models.sec import FinancialsQuarterly
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

#: Columns replaced when a quarter is written again (everything but the key).
UPDATE_COLUMNS = [
    "cik",
    "fiscal_year",
    "fiscal_period",
    "form",
    "filed",
    "accession_number",
    *VALUE_COLUMNS,
    "raw_tags",
    "fetched_at",
]


class SecFinancialsCollector(BaseCollector):
    """Collect standardised quarterly financials from SEC XBRL companyfacts."""

    name = "sec_financials"

    def __init__(
        self,
        client: SECClient | None = None,
        only_tickers: Iterable[str] | None = None,
    ) -> None:
        """
        Args:
            client: SEC client (tests inject a mock).
            only_tickers: Restrict the run to these universe tickers (targeted
                backfill / manual check); default all active tickers.
        """
        self.sec_client = client or SECClient()
        self.only_tickers = set(only_tickers) if only_tickers else None

    def fetch(self, session: Session) -> list[dict]:
        """Download the facts of every company and build the quarterly rows."""
        window_start = data_start_date()
        tickers = self.get_active_tickers(session)
        if self.only_tickers:
            tickers = [t for t in tickers if t in self.only_tickers]

        self.sec_client.load_cik_mapping()
        groups: dict[str, list[str]] = {}
        no_cik = 0
        for ticker in tickers:
            cik = self.sec_client.get_cik(ticker)
            if cik:
                groups.setdefault(cik, []).append(ticker)
            else:
                no_cik += 1

        fetched_at = datetime.now(UTC)
        rows: list[dict] = []
        no_facts = no_rows = 0
        for cik, cik_tickers in groups.items():
            cik_tickers = sorted(cik_tickers)
            try:
                facts = self.sec_client.get_company_facts(cik)
                self.record_success()
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {cik_tickers[0]} (CIK {cik}): "
                    f"companyfacts error: {e}"
                )
                self.record_error()
                continue
            if facts is None:
                no_facts += 1
                continue
            try:
                quarters = build_quarterly_rows(facts, window_start)
            except Exception as e:  # a parser bug must not stop the whole run
                logger.warning(
                    f"[{self.name}] {cik_tickers[0]} (CIK {cik}): parse error: "
                    f"{type(e).__name__}: {e}"
                )
                self.record_error()
                continue
            if not quarters:
                no_rows += 1
                continue
            for ticker in cik_tickers:
                rows.extend(
                    {**q, "ticker": ticker, "cik": cik, "fetched_at": fetched_at}
                    for q in quarters
                )

        logger.info(
            f"[{self.name}] {len(groups)} companies ({no_cik} tickers without CIK, "
            f"{no_facts} without XBRL facts, {no_rows} without quarters in the "
            f"window since {window_start}): {len(rows)} quarterly rows"
        )
        return rows

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Upsert the quarterly rows on (ticker, period_end)."""
        written = self._bulk_insert(
            session,
            FinancialsQuarterly,
            data,
            conflict_cols=["ticker", "period_end"],
            update_cols=UPDATE_COLUMNS,
        )
        session.flush()
        logger.info(f"[{self.name}] Stored {written}/{len(data)} quarterly rows")
        return len(data), written
