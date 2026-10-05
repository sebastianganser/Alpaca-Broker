"""Form 13F Institutional Holdings Collector – quarterly SEC filings.

Downloads and parses SEC Form 13F-HR filings for a configurable list
of top institutional investors ("smart money"). Extracts their
portfolio holdings and stores them in the database.

Strategy: Filer-driven approach
  1. Iterate over TOP_FILERS list (configurable, ~20 institutions)
  2. Fetch submissions JSON → filter for 13F-HR / 13F-HR/A filings
  3. Per report period, process the filings of the lookback window
     oldest first (see ``Form13FCollector._process_period``):
       - the latest original 13F-HR or RESTATEMENT amendment is the
         "base" – if it is new, the period's stored rows are replaced
       - NEW HOLDINGS amendments filed after the base add rows
  4. Download infotable XML → parse holdings; lines with the same
     (CUSIP, put/call) are summed (several discretion/manager lines)
  5. Store with ON CONFLICT DO NOTHING on
     (filer_cik, report_period, cusip, put_call)

Values: 13F filings submitted before 2023-01-03 report ``value`` in
thousands of dollars, later filings in whole dollars (SEC Form 13F
change). ``market_value`` is always stored in whole dollars.

Frequency: Weekly (Sundays), since 13F filings are quarterly.

Data Source: https://data.sec.gov/submissions/
SEC Rate Limit: 10 requests/second (enforced by SECClient)
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date, safe_float
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.cusip_resolver import CusipResolver
from trading_signals.collectors.sec_client import SECClient
from trading_signals.db.models.form13f import Form13FHolding
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

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

#: Unique key of ``form13f_holdings`` (constraint ``uq_13f_holding_key``).
HOLDING_KEY = ("filer_cik", "report_period", "cusip", "put_call")

#: put_call value for plain share / principal positions.
PUT_CALL_SHARES = "SH"

#: Filings submitted on/after this date report ``value`` in whole dollars.
DOLLAR_VALUES_SINCE_FILING = date(2023, 1, 3)
#: Fallback (no filing date): first report period filed in whole dollars.
DOLLAR_VALUES_SINCE_PERIOD = date(2022, 12, 31)

RESTATEMENT = "RESTATEMENT"


def value_multiplier(
    filing_date: date | None, report_period: date | None
) -> int:
    """Factor that converts the infotable ``value`` to whole dollars.

    1000 for filings in the old format ($ thousands), else 1. Decided by
    the filing date (amendments of old periods filed after 2023-01-03 use
    the new format); falls back to the report period.
    """
    if filing_date is not None:
        return 1 if filing_date >= DOLLAR_VALUES_SINCE_FILING else 1000
    if report_period is not None:
        return 1 if report_period >= DOLLAR_VALUES_SINCE_PERIOD else 1000
    return 1


def _sum_optional(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def aggregate_holdings(rows: Iterable[dict]) -> list[dict]:
    """Sum shares/market_value of rows sharing the same HOLDING_KEY.

    13F infotables list one line per (CUSIP, put/call, investment
    discretion, other managers); we keep one row per (filer, period,
    CUSIP, put/call). Metadata of the first row wins.
    """
    agg: dict[tuple, dict] = {}
    for r in rows:
        key = tuple(r.get(c) for c in HOLDING_KEY)
        if key not in agg:
            agg[key] = dict(r)
            continue
        a = agg[key]
        a["shares"] = _sum_optional(a.get("shares"), r.get("shares"))
        a["market_value"] = _sum_optional(
            a.get("market_value"), r.get("market_value")
        )
    return list(agg.values())


def _filing_sort_key(filing: dict) -> tuple:
    """Oldest first: filing date, acceptance time, accession number."""
    acc_dt = filing.get("acceptance_datetime")
    return (
        filing.get("filing_date") or "",
        acc_dt.isoformat() if isinstance(acc_dt, datetime) else "",
        filing.get("accession_number") or "",
    )


def _is_base_filing(filing: dict) -> bool:
    """Original 13F-HR or RESTATEMENT amendment (complete holdings list)."""
    if filing.get("form_type") == "13F-HR":
        return True
    return (filing.get("amendment_type") or "").upper() == RESTATEMENT


class Form13FCollector(BaseCollector):
    """Collect quarterly 13F institutional holdings."""

    name = "form13f_collector"

    # (filer_cik, report_period) pairs whose stored rows are replaced in
    # store(); set by fetch() (instance attribute), consumed by store().
    _replace_keys: frozenset = frozenset()

    def __init__(
        self,
        lookback_days: int = 90,
        filers: dict[str, str] | None = None,
        cusip_resolver: CusipResolver | None = None,
        until_date: date | None = None,
    ) -> None:
        """Initialize with a lookback window.

        Args:
            lookback_days: How far back to look for new filings.
                          Default 90 covers a full quarter.
            filers: Optional subset of TOP_FILERS (CIK → name), e.g. for a
                    filer-by-filer history backfill. Default: all.
            cusip_resolver: CUSIP → ticker resolver (injectable for tests).
            until_date: Optional upper bound for the filing date (history
                        backfills in windows to bound memory).
        """
        self.lookback_days = lookback_days
        self.filers = dict(filers) if filers is not None else dict(TOP_FILERS)
        self.sec_client = SECClient()
        self.cusip_resolver = cusip_resolver or CusipResolver()
        self.until_date = until_date

    def fetch(self, session: Session) -> list[dict]:
        """Fetch 13F holdings for all top filers.

        Returns:
            List of holding dicts (one per infotable line) ready for
            storage.
        """
        self.sec_client.clear_cache()
        self._replace_keys = set()
        since_date = max(
            date.today() - timedelta(days=self.lookback_days),
            data_start_date(),
        )
        # DB read first, then release the transaction before HTTP work
        stored = self._stored_accessions(session, since_date)

        all_holdings: list[dict] = []
        filers_processed = 0
        filers_with_filings = 0
        errors = 0

        for cik, name in self.filers.items():
            filers_processed += 1

            try:
                filings = self.sec_client.get_recent_13f_filings(
                    cik, since_date=since_date
                )
                self.record_success()
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {name} (CIK {cik}): submissions error: {e}"
                )
                self.record_error()
                errors += 1
                continue

            if self.until_date is not None:
                filings = [
                    f for f in filings
                    if (parse_date(f.get("filing_date") or "") or date.min)
                    <= self.until_date
                ]

            if not filings:
                logger.debug(
                    f"[{self.name}] {name}: no new 13F filings since {since_date}"
                )
                continue

            filers_with_filings += 1

            by_period: dict[str, list[dict]] = {}
            for filing in filings:
                by_period.setdefault(filing.get("report_period") or "", []).append(
                    filing
                )

            for period_str in sorted(by_period):
                holdings, replace, period_errors = self._process_period(
                    cik, name, by_period[period_str], stored
                )
                errors += period_errors
                all_holdings.extend(holdings)
                period = parse_date(period_str)
                if replace and period is not None:
                    self._replace_keys.add((cik, period))

        logger.info(
            f"[{self.name}] Processed {filers_processed} filers "
            f"({filers_with_filings} with filings in window). "
            f"Found {len(all_holdings)} total holdings. Errors: {errors}"
        )
        self._resolve_tickers(session, all_holdings)
        return all_holdings

    def _resolve_tickers(self, session: Session, holdings: list[dict]) -> None:
        """Set ``ticker`` on the fetched rows via the CUSIP resolver.

        Never raises – unresolved rows keep ``ticker`` NULL and are filled
        later by ``CusipResolver.apply_to_holdings``.
        """
        if not holdings:
            return
        try:
            mapping = self.cusip_resolver.resolve(
                session, (h.get("cusip") for h in holdings)
            )
        except Exception as e:
            logger.warning(f"[{self.name}] CUSIP resolution failed: {e}")
            session.rollback()
            return
        for h in holdings:
            if not h.get("ticker"):
                h["ticker"] = mapping.get(h.get("cusip") or "")

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store 13F holdings with dedup via unique constraint.

        Periods flagged for replacement by fetch() (new original filing or
        RESTATEMENT amendment) are deleted first, then all rows are
        inserted (aggregated per HOLDING_KEY).

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)

        replace_keys = sorted(self._replace_keys)
        for filer_cik, period in replace_keys:
            session.execute(
                delete(Form13FHolding).where(
                    Form13FHolding.filer_cik == filer_cik,
                    Form13FHolding.report_period == period,
                )
            )
        self._replace_keys = set()

        rows = aggregate_holdings(data)
        records_written = self._bulk_insert(
            session, Form13FHolding, rows, conflict_cols=list(HOLDING_KEY)
        )

        session.flush()
        try:
            # Older rows whose CUSIP got resolved in the meantime (savepoint:
            # a failure must not abort the insert transaction)
            with session.begin_nested():
                self.cusip_resolver.apply_to_holdings(session)
        except Exception as e:
            logger.warning(f"[{self.name}] ticker back-fill failed: {e}")

        logger.info(
            f"[{self.name}] Stored {records_written}/{len(rows)} aggregated "
            f"holdings from {records_fetched} infotable lines "
            f"({len(replace_keys)} filer periods replaced)"
        )
        return records_fetched, records_written

    def _stored_accessions(self, session: Session, since_date: date) -> set[str]:
        """Accessions already stored by this collector version.

        Legacy rows (``form_type`` NULL, written before migration 029) are
        ignored so that their filings are re-fetched once and replaced.
        Ends the read transaction afterwards.
        """
        stmt = (
            select(Form13FHolding.accession_number)
            .where(
                Form13FHolding.filer_cik.in_(list(self.filers)),
                Form13FHolding.filing_date >= since_date,
                Form13FHolding.accession_number.isnot(None),
                Form13FHolding.form_type.isnot(None),
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

    def _process_period(
        self,
        cik: str,
        filer_name: str,
        filings: list[dict],
        stored: set[str],
    ) -> tuple[list[dict], bool, int]:
        """Decide which filings of one report period to apply and fetch them.

        Rules (filings sorted oldest first):
          * base = latest original 13F-HR or RESTATEMENT amendment.
          * base not stored yet → replace mode: fetch base + all later
            amendments; store() deletes the period's rows first.
          * base already stored (or no base in window) → add mode: fetch
            only not-yet-stored NEW HOLDINGS amendments (after the base);
            existing keys are kept (ON CONFLICT DO NOTHING).
          * 13F-HR/A with unknown amendment type is treated as NEW HOLDINGS.
          * If the base download fails nothing of the period is stored.

        Returns:
            (holdings, replace, errors)
        """
        group = sorted(filings, key=_filing_sort_key)
        errors = 0

        # Amendment types only matter after the latest original filing
        last_original = max(
            (i for i, f in enumerate(group) if f.get("form_type") == "13F-HR"),
            default=-1,
        )
        for i, f in enumerate(group):
            f.setdefault("amendment_type", None)
            if i < last_original or not str(f.get("form_type", "")).endswith("/A"):
                continue
            try:
                f["amendment_type"] = self.sec_client.get_13f_amendment_type(
                    cik, f["accession_number"]
                )
                self.record_success()
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {filer_name}: amendment type of "
                    f"{f.get('accession_number')} unknown: {e}"
                )
                self.record_error()
                errors += 1

        base_idx = max(
            (i for i, f in enumerate(group) if _is_base_filing(f)), default=None
        )
        if base_idx is None:
            replace = False
            to_fetch = [f for f in group if f["accession_number"] not in stored]
        elif group[base_idx]["accession_number"] in stored:
            replace = False
            to_fetch = [
                f for f in group[base_idx + 1:]
                if f["accession_number"] not in stored
            ]
        else:
            replace = True
            to_fetch = group[base_idx:]

        base = group[base_idx] if base_idx is not None else None
        holdings: list[dict] = []
        for filing in to_fetch:
            try:
                rows = self._process_filing(cik, filer_name, filing)
                self.record_success()
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {filer_name}: filing parse error "
                    f"({filing.get('accession_number')}): {e}"
                )
                self.record_error()
                errors += 1
                if replace and filing is base:
                    return [], False, errors
                continue
            if replace and filing is base and not rows:
                logger.warning(
                    f"[{self.name}] {filer_name}: base filing "
                    f"{filing.get('accession_number')} has no holdings – "
                    f"stored period not replaced"
                )
                replace = False
            logger.info(
                f"[{self.name}] {filer_name}: {len(rows)} holdings from "
                f"{filing.get('form_type')} {filing.get('accession_number')} "
                f"(filed {filing.get('filing_date')})"
            )
            holdings.extend(rows)
        return holdings, replace, errors

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

        # Parse the XML
        return parse_13f_infotable(
            xml_content,
            filer_name=filer_name,
            filer_cik=cik,
            filing_date=parse_date(filing_date_str),
            report_period=parse_date(report_period_str),
            source_url=source_url,
            sec_client=self.sec_client,
            accession_number=accession,
            form_type=filing.get("form_type"),
            amendment_type=filing.get("amendment_type"),
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
    accession_number: str | None = None,
    form_type: str | None = None,
    amendment_type: str | None = None,
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
        accession_number: Accession number of the filing.
        form_type: "13F-HR" or "13F-HR/A".
        amendment_type: "RESTATEMENT" / "NEW HOLDINGS" for amendments.

    Returns:
        List of dicts (one per infotable line, not aggregated) ready for
        Form13FHolding insertion. ``market_value`` is in whole dollars,
        ``put_call`` is "PUT"/"CALL" or "SH" for share positions.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"[13f_parser] XML parse error: {e}")
        return []

    holdings: list[dict] = []
    multiplier = value_multiplier(filing_date, report_period)

    # Try both namespaced and non-namespaced element names
    info_entries = root.findall(".//ns:infoTable", NS_13F)
    if not info_entries:
        info_entries = root.findall(".//{*}infoTable")
    if not info_entries:
        # Try without namespace
        info_entries = root.findall(".//infoTable")

    for entry in info_entries:
        # Extract fields (try both namespaced and unnamespaced)
        cusip = _find_text(entry, "cusip")
        value_str = _find_text(entry, "value")
        shares_str = (
            _find_text(entry, "sshPrnamt")
            or _find_nested_text(entry, "shrsOrPrnAmt", "sshPrnamt")
        )
        put_call = (_find_text(entry, "putCall") or "").upper() or PUT_CALL_SHARES

        # CUSIP → ticker: the SEC CIK mapper doesn't map CUSIPs. For now we
        # store the CUSIP and resolve later if needed.
        ticker = None

        # Parse values (whole dollars, see value_multiplier)
        market_value = safe_float(value_str)
        if market_value is not None:
            market_value *= multiplier

        shares = safe_float(shares_str)

        holdings.append({
            "filer_name": filer_name,
            "filer_cik": filer_cik,
            "report_period": report_period,
            "filing_date": filing_date,
            "ticker": ticker,
            "cusip": cusip.upper() if cusip else cusip,
            "shares": shares,
            "market_value": market_value,
            "put_call": put_call,
            "accession_number": accession_number,
            "form_type": form_type,
            "amendment_type": amendment_type,
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
