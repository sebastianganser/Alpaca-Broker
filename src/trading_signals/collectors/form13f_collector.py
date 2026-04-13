"""Form 13F Institutional Holdings Collector – quarterly SEC filings.

Downloads and parses SEC Form 13F-HR filings for a configurable list
of top institutional investors ("smart money"). Extracts their
portfolio holdings and stores them in the database.

Strategy: Filer-driven approach
  1. Iterate over TOP_FILERS list (configurable, ~20 institutions)
  2. Fetch submissions JSON → filter for 13F-HR filings
  3. For each new filing: download infotable XML → parse holdings
  4. Map CUSIP → ticker where possible
  5. Store with ON CONFLICT DO NOTHING (dedup via unique constraint)

Frequency: Weekly (Sundays), since 13F filings are quarterly.

Data Source: https://data.sec.gov/submissions/
SEC Rate Limit: 10 requests/second (enforced by SECClient)
"""

import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.sec_client import SECClient
from trading_signals.db.models.form13f import Form13FHolding
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Top institutional filers to track (CIK → name)
# These are the most watched "smart money" institutional investors
TOP_FILERS: dict[str, str] = {
    "0001067983": "Berkshire Hathaway (Buffett)",
    "0001649339": "Scion Asset Management (Burry)",
    "0001336528": "Pershing Square (Ackman)",
    "0001037389": "Renaissance Technologies",
    "0001167483": "Tiger Global Management",
    "0001350694": "Bridgewater Associates",
    "0001423053": "Citadel Advisors",
    "0001179392": "Two Sigma Investments",
    "0001009268": "D.E. Shaw",
    "0001273087": "Millennium Management",
    "0001603466": "Point72 Asset Management",
    "0001079114": "Greenlight Capital (Einhorn)",
    "0001061768": "Baupost Group (Klarman)",
    "0001040273": "Third Point (Loeb)",
    "0000921669": "Icahn Capital",
    "0001791786": "Elliott Investment Management",
    "0001536411": "Duquesne Family Office (Druckenmiller)",
    "0001135730": "Coatue Management",
    "0001656456": "Appaloosa Management (Tepper)",
    "0001364742": "ARK Investment Management",
}

# XML namespace used in 13F infotable documents
NS_13F = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


class Form13FCollector(BaseCollector):
    """Collect quarterly 13F institutional holdings."""

    name = "form13f_collector"

    def __init__(self, lookback_days: int = 90) -> None:
        """Initialize with a lookback window.

        Args:
            lookback_days: How far back to look for new filings.
                          Default 90 covers a full quarter.
        """
        self.lookback_days = lookback_days
        self.sec_client = SECClient()

    def fetch(self, session: Session) -> list[dict]:
        """Fetch 13F holdings for all top filers.

        Returns:
            List of holding dicts ready for storage.
        """
        since_date = date.today() - timedelta(days=self.lookback_days)

        all_holdings: list[dict] = []
        filers_processed = 0
        filers_with_filings = 0
        errors = 0

        for cik, name in TOP_FILERS.items():
            filers_processed += 1

            try:
                filings = self.sec_client.get_recent_13f_filings(
                    cik, since_date=since_date
                )
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {name} (CIK {cik}): submissions error: {e}"
                )
                errors += 1
                continue

            if not filings:
                logger.debug(
                    f"[{self.name}] {name}: no new 13F filings since {since_date}"
                )
                continue

            filers_with_filings += 1

            # Process only the most recent filing per filer
            latest_filing = filings[0]
            try:
                holdings = self._process_filing(cik, name, latest_filing)
                all_holdings.extend(holdings)
                logger.info(
                    f"[{self.name}] {name}: {len(holdings)} holdings from "
                    f"filing {latest_filing['filing_date']}"
                )
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {name}: filing parse error: {e}"
                )
                errors += 1

        logger.info(
            f"[{self.name}] Processed {filers_processed} filers "
            f"({filers_with_filings} with new filings). "
            f"Found {len(all_holdings)} total holdings. Errors: {errors}"
        )
        return all_holdings

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store 13F holdings with dedup via unique constraint.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0

        for holding in data:
            stmt = (
                pg_insert(Form13FHolding)
                .values(**holding)
                .on_conflict_do_nothing(
                    constraint="uq_13f_holding_dedup"
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"holdings ({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written

    def _process_filing(
        self, cik: str, filer_name: str, filing: dict
    ) -> list[dict]:
        """Download and parse a single 13F filing's infotable.

        Args:
            cik: 10-digit CIK of the filer.
            filer_name: Human-readable name of the filer.
            filing: Filing metadata dict from SECClient.

        Returns:
            List of holding dicts ready for DB insertion.
        """
        accession = filing["accession_number"]
        filing_date_str = filing.get("filing_date", "")
        report_period_str = filing.get("report_period", "")

        # Find the infotable XML document
        infotable_doc = self.sec_client.find_infotable_document(cik, accession)
        if not infotable_doc:
            # Try the primary document as fallback
            infotable_doc = filing.get("primary_document", "")
            if not infotable_doc:
                logger.warning(
                    f"[{self.name}] {filer_name}: no infotable found for "
                    f"filing {accession}"
                )
                return []

        # Download the infotable XML
        xml_content = self.sec_client.download_filing_document(
            cik, accession, infotable_doc
        )

        # Build source URL
        acc_no_dashes = accession.replace("-", "")
        source_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{self.sec_client.pad_cik(cik)}/{acc_no_dashes}/{infotable_doc}"
        )

        # Parse dates
        filing_date = _parse_date(filing_date_str)
        report_period = _parse_date(report_period_str)

        # Parse the XML
        return parse_13f_infotable(
            xml_content,
            filer_name=filer_name,
            filer_cik=cik,
            filing_date=filing_date,
            report_period=report_period,
            source_url=source_url,
            sec_client=self.sec_client,
        )


def parse_13f_infotable(
    xml_content: str,
    *,
    filer_name: str | None = None,
    filer_cik: str | None = None,
    filing_date: date | None = None,
    report_period: date | None = None,
    source_url: str | None = None,
    sec_client: SECClient | None = None,
) -> list[dict]:
    """Parse a 13F infotable XML into holding dicts.

    Args:
        xml_content: Raw XML string of the infotable document.
        filer_name: Name of the filing institution.
        filer_cik: CIK of the filing institution.
        filing_date: Date the filing was submitted.
        report_period: End of the reporting quarter.
        source_url: URL to the original filing.
        sec_client: SECClient for CUSIP→ticker mapping.

    Returns:
        List of dicts ready for Form13FHolding insertion.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"[13f_parser] XML parse error: {e}")
        return []

    holdings: list[dict] = []

    # Try both namespaced and non-namespaced element names
    info_entries = root.findall(".//ns:infoTable", NS_13F)
    if not info_entries:
        info_entries = root.findall(".//{*}infoTable")
    if not info_entries:
        # Try without namespace
        info_entries = root.findall(".//infoTable")

    for entry in info_entries:
        # Extract fields (try both namespaced and unnamespaced)
        name_of_issuer = _find_text(entry, "nameOfIssuer")
        cusip = _find_text(entry, "cusip")
        value_str = _find_text(entry, "value")
        shares_str = (
            _find_text(entry, "sshPrnamt")
            or _find_nested_text(entry, "shrsOrPrnAmt", "sshPrnamt")
        )
        put_call = _find_text(entry, "putCall")

        # Try to resolve CUSIP to ticker
        ticker = None
        if cusip and sec_client:
            # The SEC CIK mapper doesn't map CUSIPs, but we can try
            # matching the issuer name against our ticker mapping.
            # For now, we store the CUSIP and resolve later if needed.
            pass

        # Parse values
        market_value = None
        if value_str:
            try:
                # 13F values are in thousands
                market_value = float(value_str) * 1000
            except (ValueError, TypeError):
                pass

        shares = None
        if shares_str:
            try:
                shares = float(shares_str)
            except (ValueError, TypeError):
                pass

        holdings.append({
            "filer_name": filer_name,
            "filer_cik": filer_cik,
            "report_period": report_period,
            "filing_date": filing_date,
            "ticker": ticker,
            "cusip": cusip,
            "shares": shares,
            "market_value": market_value,
            "put_call": put_call,
            "source_url": source_url,
        })

    return holdings


def _find_text(element: ET.Element, tag: str) -> str | None:
    """Find text for a tag, trying with and without namespace."""
    # Try with namespace
    found = element.find(f"ns:{tag}", NS_13F)
    if found is None:
        # Try with wildcard namespace
        found = element.find(f"{{*}}{tag}")
    if found is None:
        # Try without namespace
        found = element.find(tag)
    if found is not None and found.text:
        return found.text.strip()
    return None


def _find_nested_text(
    element: ET.Element, parent_tag: str, child_tag: str
) -> str | None:
    """Find text in a nested element (parent/child), handling namespaces."""
    # Try with namespace
    parent = element.find(f"ns:{parent_tag}", NS_13F)
    if parent is None:
        parent = element.find(f"{{*}}{parent_tag}")
    if parent is None:
        parent = element.find(parent_tag)

    if parent is not None:
        return _find_text(parent, child_tag)
    return None


def _parse_date(date_str: str | None) -> date | None:
    """Parse a date string (YYYY-MM-DD) safely."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None
