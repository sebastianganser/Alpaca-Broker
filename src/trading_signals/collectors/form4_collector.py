"""Form 4 Insider Trades Collector – daily SEC EDGAR filings.

Downloads and parses SEC Form 4 filings for all tickers in our universe.
Extracts insider purchase/sale transactions and stores them in the database.

Strategy: Universe-driven approach
  1. For each active ticker → look up CIK via company_tickers.json
  2. Fetch submissions JSON from SEC → filter for Form 4 filings
  3. Download each new filing XML → parse transactions
  4. Store with ON CONFLICT DO NOTHING (dedup via unique constraint)

Also expands the universe when new tickers are found in insider filings,
validated against Alpaca (same pattern as ARK collector).

Data Source: https://data.sec.gov/submissions/
SEC Rate Limit: 10 requests/second (enforced by SECClient)
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.sec_client import SECClient
from trading_signals.db.models.insider import InsiderTrade
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Transaction codes that represent real market transactions
# P = Purchase, S = Sale (open market)
# We also capture A (grant/award) and D (disposition to issuer)
# but mark them for downstream filtering
TRANSACTION_CODES = {
    "P": "Purchase",
    "S": "Sale",
    "A": "Grant/Award",
    "D": "Disposition",
    "F": "Tax Withholding",
    "M": "Option Exercise",
    "G": "Gift",
    "J": "Other",
    "C": "Conversion",
    "W": "Will/Inheritance",
}


class Form4Collector(BaseCollector):
    """Collect SEC Form 4 insider trades for universe tickers."""

    name = "form4_collector"

    def __init__(self, lookback_days: int = 7) -> None:
        """Initialize with a lookback window for filing dates.

        Args:
            lookback_days: How many days back to search for new filings.
                          Default 7 ensures we catch late filings.
        """
        self.lookback_days = lookback_days
        self.sec_client = SECClient()

    def fetch(self, session: Session) -> list[dict]:
        """Fetch Form 4 filings for all universe tickers with CIK mappings.

        Returns:
            List of parsed transaction dicts ready for storage.
        """
        # Load CIK mapping
        self.sec_client.load_cik_mapping()

        # Get active tickers from universe
        stmt = select(Universe.ticker).where(Universe.is_active == True)  # noqa: E712
        active_tickers = [row[0] for row in session.execute(stmt).all()]

        since_date = date.today() - timedelta(days=self.lookback_days)

        all_transactions: list[dict] = []
        tickers_processed = 0
        tickers_with_filings = 0
        errors = 0

        for ticker in active_tickers:
            cik = self.sec_client.get_cik(ticker)
            if not cik:
                continue

            tickers_processed += 1

            try:
                filings = self.sec_client.get_recent_form4_filings(
                    cik, since_date=since_date
                )
            except Exception as e:
                logger.info(
                    f"[{self.name}] {ticker} (CIK {cik}): submissions error: {e}"
                )
                errors += 1
                continue

            if not filings:
                continue

            tickers_with_filings += 1

            for filing in filings:
                try:
                    transactions = self._process_filing(
                        ticker, cik, filing
                    )
                    all_transactions.extend(transactions)
                except Exception as e:
                    logger.info(
                        f"[{self.name}] {ticker}: filing parse error: {e}"
                    )
                    errors += 1

        logger.info(
            f"[{self.name}] Processed {tickers_processed} tickers "
            f"({tickers_with_filings} with filings). "
            f"Found {len(all_transactions)} transactions. "
            f"Errors: {errors}"
        )
        return all_transactions

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store insider transactions with dedup via unique constraint.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0

        for txn in data:
            stmt = (
                pg_insert(InsiderTrade)
                .values(**txn)
                .on_conflict_do_nothing(
                    constraint="uq_insider_trade_dedup"
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"transactions ({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written

    def _process_filing(
        self, ticker: str, cik: str, filing: dict
    ) -> list[dict]:
        """Download and parse a single Form 4 filing.

        Args:
            ticker: Stock ticker symbol.
            cik: 10-digit CIK for the issuing company.
            filing: Filing metadata dict from SECClient.

        Returns:
            List of transaction dicts ready for DB insertion.
        """
        accession = filing["accession_number"]
        doc_name = filing["primary_document"]
        filing_date_str = filing["filing_date"]

        if not accession or not doc_name:
            return []

        # SEC's primaryDocument field often contains XSLT-transformed paths
        # like "xslF345X06/ownership.xml" - these are virtual paths that 404.
        # Strip the XSLT prefix to get the actual raw XML filename.
        if "/" in doc_name:
            doc_name = doc_name.rsplit("/", 1)[-1]

        # SEC archives filings under the SUBJECT COMPANY CIK (the `cik` param),
        # NOT the filer CIK from the accession number. The filer might be a
        # third-party agent (law firm), but files are in the company's directory.
        # Download the XML filing using the company's CIK
        xml_content = self.sec_client.download_filing_document(
            cik, accession, doc_name
        )

        # Build the URL for reference
        acc_no_dashes = accession.replace("-", "")
        form4_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{self.sec_client.pad_cik(cik)}/{acc_no_dashes}/{doc_name}"
        )

        # Parse filing date
        filing_date = None
        if filing_date_str:
            try:
                filing_date = date.fromisoformat(filing_date_str)
            except ValueError:
                pass

        # Parse the XML
        return parse_form4_xml(
            xml_content,
            ticker=ticker,
            filing_date=filing_date,
            form4_url=form4_url,
        )


def parse_form4_xml(
    xml_content: str,
    ticker: str | None = None,
    filing_date: date | None = None,
    form4_url: str | None = None,
) -> list[dict]:
    """Parse a Form 4 XML document into transaction dicts.

    Extracts both non-derivative and derivative transactions.

    Args:
        xml_content: Raw XML string of the Form 4 filing.
        ticker: Override ticker (from our CIK mapping).
        filing_date: Filing date from submissions API.
        form4_url: URL to the original filing.

    Returns:
        List of dicts ready for InsiderTrade insertion.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"[form4_parser] XML parse error: {e}")
        return []

    # Issuer info
    issuer_cik = _text(root, ".//issuer/issuerCik")
    issuer_name = _text(root, ".//issuer/issuerName")
    issuer_ticker = _text(root, ".//issuer/issuerTradingSymbol")

    # Use our ticker if provided, otherwise fall back to the filing's ticker
    effective_ticker = ticker or (issuer_ticker.upper() if issuer_ticker else None)

    # Reporting owner info (can be multiple owners, take first)
    owner_name = _text(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    owner_cik = _text(root, ".//reportingOwner/reportingOwnerId/rptOwnerCik")

    # Owner title/relationship
    owner_title = _text(
        root,
        ".//reportingOwner/reportingOwnerRelationship/officerTitle"
    )
    is_director = _text(
        root,
        ".//reportingOwner/reportingOwnerRelationship/isDirector"
    ) == "1"
    is_officer = _text(
        root,
        ".//reportingOwner/reportingOwnerRelationship/isOfficer"
    ) == "1"
    is_ten_pct = _text(
        root,
        ".//reportingOwner/reportingOwnerRelationship/isTenPercentOwner"
    ) == "1"

    # Build title string if not explicitly given
    if not owner_title:
        parts = []
        if is_director:
            parts.append("Director")
        if is_officer:
            parts.append("Officer")
        if is_ten_pct:
            parts.append("10% Owner")
        owner_title = ", ".join(parts) if parts else None

    transactions: list[dict] = []

    # Non-derivative transactions (the most important ones)
    for txn in root.findall(".//nonDerivativeTransaction"):
        parsed = _parse_transaction_element(
            txn,
            is_derivative=False,
            issuer_cik=issuer_cik or owner_cik,
            issuer_name=issuer_name,
            effective_ticker=effective_ticker,
            owner_name=owner_name,
            owner_title=owner_title,
            filing_date=filing_date,
            form4_url=form4_url,
        )
        if parsed:
            transactions.append(parsed)

    # Derivative transactions (options, warrants, etc.)
    for txn in root.findall(".//derivativeTransaction"):
        parsed = _parse_transaction_element(
            txn,
            is_derivative=True,
            issuer_cik=issuer_cik or owner_cik,
            issuer_name=issuer_name,
            effective_ticker=effective_ticker,
            owner_name=owner_name,
            owner_title=owner_title,
            filing_date=filing_date,
            form4_url=form4_url,
        )
        if parsed:
            transactions.append(parsed)

    return transactions


def _parse_transaction_element(
    txn_element: ET.Element,
    *,
    is_derivative: bool,
    issuer_cik: str | None,
    issuer_name: str | None,
    effective_ticker: str | None,
    owner_name: str | None,
    owner_title: str | None,
    filing_date: date | None,
    form4_url: str | None,
) -> dict | None:
    """Parse a single transaction XML element into a dict.

    Works for both nonDerivativeTransaction and derivativeTransaction elements.
    """
    # Transaction date
    txn_date_str = _text(txn_element, ".//transactionDate/value")
    txn_date = None
    if txn_date_str:
        try:
            txn_date = date.fromisoformat(txn_date_str)
        except ValueError:
            pass

    # Transaction code (P=Purchase, S=Sale, etc.)
    txn_code = _text(txn_element, ".//transactionCoding/transactionCode")
    if not txn_code:
        return None  # Skip transactions without a code

    # Shares
    shares_str = _text(txn_element, ".//transactionAmounts/transactionShares/value")
    shares = _safe_float(shares_str)

    # Price per share
    price_str = _text(
        txn_element,
        ".//transactionAmounts/transactionPricePerShare/value"
    )
    price = _safe_float(price_str)

    # Acquired/Disposed
    acq_disp = _text(
        txn_element,
        ".//transactionAmounts/transactionAcquiredDisposedCode/value"
    )

    # Post-transaction shares owned
    shares_after_str = _text(
        txn_element,
        ".//postTransactionAmounts/sharesOwnedFollowingTransaction/value"
    )
    shares_after = _safe_float(shares_after_str)

    # Calculate total value
    total_value = None
    if shares is not None and price is not None:
        total_value = abs(shares * price)

    # Build raw_data for audit trail
    raw_data = {
        "transaction_code": txn_code,
        "acquired_disposed": acq_disp,
        "is_derivative": is_derivative,
        "transaction_code_description": TRANSACTION_CODES.get(txn_code, "Unknown"),
    }

    return {
        "ticker": effective_ticker,
        "company_name": issuer_name,
        "cik": issuer_cik,
        "insider_name": owner_name,
        "insider_title": owner_title,
        "transaction_date": txn_date,
        "filing_date": filing_date,
        "transaction_type": txn_code,
        "shares": shares,
        "price_per_share": price,
        "total_value": total_value,
        "shares_owned_after": shares_after,
        "is_derivative": is_derivative,
        "form4_url": form4_url,
        "raw_data": raw_data,
    }


def _text(element: ET.Element, path: str) -> str | None:
    """Safely extract text from an XML element path."""
    found = element.find(path)
    if found is not None and found.text:
        return found.text.strip()
    return None


def _safe_float(value: str | None) -> float | None:
    """Safely convert a string to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
