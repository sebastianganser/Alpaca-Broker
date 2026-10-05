"""Form 4 Insider Trades Collector – daily SEC EDGAR filings.

Downloads and parses SEC Form 4 filings for all tickers in our universe.
Extracts insider purchase/sale transactions and stores them in the database.

Strategy: Universe-driven approach
  1. For each active ticker → look up CIK via company_tickers.json
  2. Fetch submissions JSON from SEC → filter for Form 4 filings
  3. Download each new (not yet stored) filing XML → parse transactions
  4. Store with ON CONFLICT DO NOTHING on the filing-based key
     (accession_number, is_derivative, row_index)
  5. Form 4/A amendments replace the rows of the amended filing
     (see :meth:`Form4Collector._apply_amendments`)

Also expands the universe when new tickers are found in insider filings,
validated against Alpaca (same pattern as ARK collector).

Data Source: https://data.sec.gov/submissions/
SEC Rate Limit: 10 requests/second (enforced by SECClient)
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date, safe_float
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.sec_client import SECClient
from trading_signals.db.models.insider import InsiderTrade
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

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

#: Columns of the partial unique index ``uq_insider_trade_filing_row``.
DEDUP_KEY = ("accession_number", "is_derivative", "row_index")


def _filing_sort_key(filing: dict) -> tuple:
    """Oldest first: filing date, acceptance time, accession number."""
    acc_dt = filing.get("acceptance_datetime")
    return (
        filing.get("filing_date") or "",
        acc_dt.isoformat() if isinstance(acc_dt, datetime) else "",
        filing.get("accession_number") or "",
    )


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

        DB reads (already stored accessions, active tickers) happen first;
        the read transaction is released before the HTTP phase.

        Returns:
            List of parsed transaction dicts ready for storage.
        """
        self.sec_client.clear_cache()
        since_date = max(
            date.today() - timedelta(days=self.lookback_days),
            data_start_date(),
        )

        stored = self._stored_accessions(session, since_date)
        # Ends the read transaction → no open txn during HTTP work
        active_tickers = self.get_active_tickers(session)

        # Load CIK mapping
        self.sec_client.load_cik_mapping()

        all_transactions: list[dict] = []
        tickers_processed = 0
        tickers_with_filings = 0
        filings_skipped = 0
        errors = 0
        seen_ciks: set[str] = set()

        for ticker in active_tickers:
            cik = self.sec_client.get_cik(ticker)
            if not cik:
                continue
            # Share classes (GOOG/GOOGL) map to the same CIK – process once
            if cik in seen_ciks:
                continue
            seen_ciks.add(cik)

            tickers_processed += 1

            try:
                filings = self.sec_client.get_recent_form4_filings(
                    cik, since_date=since_date
                )
                self.record_success()
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {ticker} (CIK {cik}): submissions error: {e}"
                )
                self.record_error()
                errors += 1
                continue

            new_filings = [
                f for f in filings if f.get("accession_number") not in stored
            ]
            filings_skipped += len(filings) - len(new_filings)
            if not new_filings:
                continue

            tickers_with_filings += 1

            for filing in sorted(new_filings, key=_filing_sort_key):
                try:
                    transactions = self._process_filing(
                        ticker, cik, filing
                    )
                    all_transactions.extend(transactions)
                    self.record_success()
                except Exception as e:
                    logger.warning(
                        f"[{self.name}] {ticker}: filing parse error "
                        f"({filing.get('accession_number')}): {e}"
                    )
                    self.record_error()
                    errors += 1

        logger.info(
            f"[{self.name}] Processed {tickers_processed} tickers "
            f"({tickers_with_filings} with new filings, "
            f"{filings_skipped} filings already stored). "
            f"Found {len(all_transactions)} transactions. "
            f"Errors: {errors}"
        )
        return all_transactions

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store insider transactions with dedup via the filing-based key.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        rows = [r for r in data if r.get("accession_number")]
        if len(rows) < records_fetched:
            logger.warning(
                f"[{self.name}] Skipping {records_fetched - len(rows)} "
                f"transactions without accession number"
            )

        records_written = self._bulk_insert(
            session,
            InsiderTrade,
            rows,
            conflict_cols=list(DEDUP_KEY),
            index_where=InsiderTrade.accession_number.isnot(None),
        )
        replaced = self._apply_amendments(session, rows)

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"transactions ({records_fetched - records_written} already "
            f"existed); {replaced} rows superseded by 4/A amendments"
        )
        return records_fetched, records_written

    # ── Helpers ───────────────────────────────────────────────────

    def _stored_accessions(self, session: Session, since_date: date) -> set[str]:
        """Accession numbers already stored for filings since ``since_date``.

        Ends the read transaction afterwards. Errors → empty set (the
        unique index still prevents duplicates, we just re-download).
        """
        stmt = (
            select(InsiderTrade.accession_number)
            .where(
                InsiderTrade.accession_number.isnot(None),
                InsiderTrade.filing_date >= since_date,
            )
            .distinct()
        )
        try:
            result = {r[0] for r in session.execute(stmt).all() if r[0]}
        except Exception as e:
            logger.warning(f"[{self.name}] Could not load stored accessions: {e}")
            session.rollback()
            return set()
        release_transaction(session)
        return result

    @staticmethod
    def _apply_amendments(session: Session, rows: list[dict]) -> int:
        """Delete rows superseded by Form 4/A amendments in ``rows``.

        Rule: for every distinct (issuer cik, owner, transaction_date,
        transaction_type, is_derivative) of a 4/A transaction, delete rows
        with the same values that come from a DIFFERENT accession and were
        filed earlier (same-day rows only if they are not 4/A themselves),
        and – if the 4/A states ``dateOfOriginalSubmission`` – not before
        that date. Owner match: same ``insider_cik``, or same
        ``insider_name`` for legacy rows without ``insider_cik``.
        Amendments are applied oldest first, so a later 4/A also supersedes
        an earlier 4/A. Must run after the amendment rows were inserted.

        Returns:
            Number of deleted rows (as reported by the driver).
        """
        targets: dict[tuple, tuple] = {}
        for r in rows:
            if (r.get("form_type") or "").upper() != "4/A":
                continue
            if not (r.get("cik") and r.get("transaction_date")
                    and r.get("transaction_type")):
                continue
            if not (r.get("insider_cik") or r.get("insider_name")):
                continue
            key = (
                r["accession_number"], r["cik"], r.get("insider_cik"),
                r.get("insider_name"), r["transaction_date"],
                r["transaction_type"], bool(r.get("is_derivative")),
            )
            orig = parse_date((r.get("raw_data") or {}).get(
                "date_of_original_submission"
            ))
            targets[key] = (r.get("filing_date"), orig)

        deleted = 0
        ordered = sorted(
            targets.items(), key=lambda kv: (kv[1][0] or date.min, kv[0][0])
        )
        for key, (filed, orig) in ordered:
            acc, cik, owner_cik, owner_name, txn_date, txn_type, deriv = key
            if owner_cik:
                owner_cond = or_(
                    InsiderTrade.insider_cik == owner_cik,
                    and_(
                        InsiderTrade.insider_cik.is_(None),
                        InsiderTrade.insider_name == owner_name,
                    ),
                )
            else:
                owner_cond = InsiderTrade.insider_name == owner_name
            conds = [
                InsiderTrade.cik == cik,
                owner_cond,
                InsiderTrade.transaction_date == txn_date,
                InsiderTrade.transaction_type == txn_type,
                InsiderTrade.is_derivative == deriv,
                or_(
                    InsiderTrade.accession_number.is_(None),
                    InsiderTrade.accession_number != acc,
                ),
            ]
            if filed:
                conds.append(or_(
                    InsiderTrade.filing_date < filed,
                    and_(
                        InsiderTrade.filing_date == filed,
                        InsiderTrade.form_type.is_distinct_from("4/A"),
                    ),
                ))
            if orig:
                conds.append(InsiderTrade.filing_date >= orig)
            result = session.execute(delete(InsiderTrade).where(*conds))
            rc = getattr(result, "rowcount", 0)
            if isinstance(rc, int) and rc > 0:
                deleted += rc
        return deleted

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

        # Parse the XML. `issuer_cik` = the company whose submissions listed
        # the filing: filings where that company is only the *reporting
        # owner* of another issuer are skipped by the parser.
        return parse_form4_xml(
            xml_content,
            ticker=ticker,
            filing_date=parse_date(filing_date_str),
            form4_url=form4_url,
            accession_number=accession,
            form_type=filing.get("form_type"),
            acceptance_datetime=filing.get("acceptance_datetime"),
            issuer_cik=cik,
        )


def _same_cik(a: str | None, b: str | None) -> bool:
    """Compare CIKs ignoring leading zeros."""
    return (a or "").strip().lstrip("0") == (b or "").strip().lstrip("0")


def _parse_owner(owner: ET.Element) -> dict:
    """Extract name/CIK/title of one ``reportingOwner`` element."""
    name = _text(owner, "reportingOwnerId/rptOwnerName")
    owner_cik = _text(owner, "reportingOwnerId/rptOwnerCik")
    rel = "reportingOwnerRelationship/"
    title = _text(owner, rel + "officerTitle")
    is_director = _text(owner, rel + "isDirector") in ("1", "true")
    is_officer = _text(owner, rel + "isOfficer") in ("1", "true")
    is_ten_pct = _text(owner, rel + "isTenPercentOwner") in ("1", "true")

    # Build title string if not explicitly given
    if not title:
        parts = []
        if is_director:
            parts.append("Director")
        if is_officer:
            parts.append("Officer")
        if is_ten_pct:
            parts.append("10% Owner")
        title = ", ".join(parts) if parts else None
    return {"name": name, "cik": owner_cik, "title": title}


def parse_form4_xml(
    xml_content: str,
    ticker: str | None = None,
    filing_date: date | None = None,
    form4_url: str | None = None,
    *,
    accession_number: str | None = None,
    form_type: str | None = None,
    acceptance_datetime: datetime | None = None,
    issuer_cik: str | None = None,
) -> list[dict]:
    """Parse a Form 4 XML document into transaction dicts.

    Extracts both non-derivative and derivative transactions.

    Args:
        xml_content: Raw XML string of the Form 4 filing.
        ticker: Override ticker (from our CIK mapping).
        filing_date: Filing date from submissions API.
        form4_url: URL to the original filing.
        accession_number: Accession number of the filing (dedup key).
        form_type: "4" or "4/A" (falls back to the XML documentType).
        acceptance_datetime: SEC acceptance timestamp of the filing.
        issuer_cik: Expected issuer CIK (company we fetched the filing
            for). If the XML names a different issuer, the filing belongs
            to another company and ``[]`` is returned. Used as issuer CIK
            when the XML has none.

    Returns:
        List of dicts ready for InsiderTrade insertion.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"[form4_parser] XML parse error: {e}")
        return []

    # Issuer info
    xml_issuer_cik = _text(root, ".//issuer/issuerCik")
    issuer_name = _text(root, ".//issuer/issuerName")
    issuer_ticker = _text(root, ".//issuer/issuerTradingSymbol")

    if issuer_cik and xml_issuer_cik and not _same_cik(issuer_cik, xml_issuer_cik):
        # The company of `issuer_cik` is only a reporting owner here.
        logger.debug(
            f"[form4_parser] {accession_number}: issuer {xml_issuer_cik} != "
            f"{issuer_cik} – filing belongs to another issuer, skipped"
        )
        return []

    effective_issuer_cik = xml_issuer_cik or issuer_cik
    if not effective_issuer_cik:
        logger.warning(
            f"[form4_parser] {accession_number or form4_url}: no issuer CIK "
            f"in filing – storing cik=NULL"
        )

    # Use our ticker if provided, otherwise fall back to the filing's ticker
    effective_ticker = ticker or (issuer_ticker.upper() if issuer_ticker else None)

    # Reporting owners (joint filings can list several): the row carries
    # the first owner, owner_count + raw_data keep the full picture.
    owners = [_parse_owner(o) for o in root.findall(".//reportingOwner")]
    first = owners[0] if owners else {"name": None, "cik": None, "title": None}

    doc_type = _text(root, ".//documentType")
    effective_form_type = form_type or doc_type
    original_date = _text(root, ".//dateOfOriginalSubmission")

    common = {
        "issuer_cik": effective_issuer_cik,
        "issuer_name": issuer_name,
        "effective_ticker": effective_ticker,
        "owner_name": first["name"],
        "owner_title": first["title"],
        "filing_date": filing_date,
        "form4_url": form4_url,
    }
    extra = {
        "insider_cik": first["cik"],
        "owner_count": len(owners) or None,
        "accession_number": accession_number,
        "form_type": effective_form_type,
        "acceptance_datetime": acceptance_datetime,
    }

    transactions: list[dict] = []

    # Non-derivative transactions (the most important ones), then derivative
    # transactions (options, warrants, etc.). row_index counts per table.
    for path, is_derivative in (
        (".//nonDerivativeTransaction", False),
        (".//derivativeTransaction", True),
    ):
        for row_index, txn in enumerate(root.findall(path)):
            parsed = _parse_transaction_element(
                txn, is_derivative=is_derivative, **common
            )
            if not parsed:
                continue
            parsed.update(extra)
            parsed["row_index"] = row_index
            if len(owners) > 1:
                parsed["raw_data"]["reporting_owners"] = owners
            if original_date:
                parsed["raw_data"]["date_of_original_submission"] = original_date
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
    txn_date = parse_date(_text(txn_element, ".//transactionDate/value"))

    # Transaction code (P=Purchase, S=Sale, etc.)
    txn_code = _text(txn_element, ".//transactionCoding/transactionCode")
    if not txn_code:
        return None  # Skip transactions without a code

    # Shares
    shares_str = _text(txn_element, ".//transactionAmounts/transactionShares/value")
    shares = safe_float(shares_str)

    # Price per share
    price_str = _text(
        txn_element,
        ".//transactionAmounts/transactionPricePerShare/value"
    )
    price = safe_float(price_str)

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
    shares_after = safe_float(shares_after_str)

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
