"""SEC EDGAR filings collector - daily metadata of company filings + press releases.

For every active universe ticker with a CIK the collector reads the EDGAR
submissions JSON (``data.sec.gov/submissions/CIK##########.json``), keeps the
filings of a whitelisted form, and stores them in ``signals.sec_filings``.
For 8-Ks with a results / event item it additionally fetches the press release
(EX-99.x) or the body of the report and stores a cleaned plain-text excerpt in
``signals.sec_filing_texts``.

Form whitelist (:data:`FORMS`) - what a Wall-Street-style assessment looks at:

* current / periodic reports: ``8-K`` (+ ``8-K/A``), ``10-Q``, ``10-K`` (+ ``/A``),
  ``6-K`` / ``20-F`` / ``40-F`` (foreign private issuers in the universe),
  ``NT 10-K`` / ``NT 10-Q`` (late-filing notices - a red flag),
* ``144`` - planned insider sales (notice of proposed sale),
* ``SC 13D`` / ``SC 13G`` and, since the structured-data switch of Dec 2024,
  ``SCHEDULE 13D`` / ``SCHEDULE 13G`` (+ amendments) - stakes >= 5 %, activists,
* ``S-1`` / ``S-3`` (+ ``/A``, ``S-3ASR``) - shelf registrations / dilution,
* ``DEF 14A`` / ``DEFM14A`` - proxy statements (pay, votes, mergers).

Not collected: Forms 3/4/5 (``form4_collector``), 13F (``form13f_collector``),
424B* prospectus supplements (thousands of structured-note supplements per
day), 425 / SC TO merger communications, 10-K exhibits.

Text rules (:func:`text_plan`): ``8-K`` items ``2.02`` (results), ``7.01``
(Reg FD) and ``8.01`` (other events) -> press release excerpt (EX-99.1, then
EX-99, then any other EX-99.x; if there is none the 8-K body); the event items
in :data:`BODY_ITEMS` (agreements, acquisitions, bankruptcy, cyber incident,
delisting, officer changes, restatements ...) -> excerpt of the 8-K body
without the cover page. One request per filing: the full-submission text
(``<accession>.txt``) is streamed and closed after the wanted document.

Point-in-time: ``acceptance_datetime`` is the instant EDGAR accepted the filing
(see :func:`~trading_signals.collectors.sec_client.submission_acceptance_utc` -
the submissions JSON labels Eastern wall clock + 8/10 h as ``Z``); a filing is
public from then on. Share classes that share a CIK (GOOG/GOOGL) are fetched
once; ``tickers`` lists all of them.

Request volume per run: 1 (``company_tickers.json``) + ~650 submissions (one per
CIK, ~0.1-1 MB each) + 1 per filing text (typically 10-60 on a normal day, up
to ~150 in peak earnings weeks) at <= 9 requests/s (SECClient).
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.sec_client import (
    SECClient,
    SgmlDocument,
    submission_acceptance_utc,
)
from trading_signals.db.models.sec import SecFiling, SecFilingText
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

#: Whitelisted form types (see module docstring).
FORMS: frozenset[str] = frozenset(
    {
        "8-K", "8-K/A",
        "10-Q", "10-Q/A", "10-K", "10-K/A",
        "6-K", "20-F", "40-F",
        "NT 10-K", "NT 10-Q",
        "144",
        "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
        "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
        "S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR",
        "DEF 14A", "DEFM14A",
    }
)  # fmt: skip

#: 8-K items whose press release (EX-99.x) is stored: results, Reg FD, other events.
PRESS_RELEASE_ITEMS: frozenset[str] = frozenset({"2.02", "7.01", "8.01"})

#: 8-K items whose body (the report itself) is stored: the facts are in the text.
BODY_ITEMS: frozenset[str] = frozenset(
    {
        "1.01",  # entry into a material definitive agreement
        "1.02",  # termination of a material agreement
        "1.03",  # bankruptcy / receivership
        "1.05",  # material cybersecurity incident
        "2.01",  # completion of an acquisition / disposition
        "2.05",  # exit / disposal costs (restructuring)
        "2.06",  # material impairments
        "3.01",  # delisting notice / failure to satisfy a listing rule
        "4.01",  # change of auditor
        "4.02",  # non-reliance on previously issued financial statements
        "5.02",  # departure / appointment of directors and officers
    }
)

#: Forms for which texts are fetched.
TEXT_FORMS: frozenset[str] = frozenset({"8-K"})

#: Length of the stored excerpt (characters of cleaned text).
EXCERPT_CHARS = 3000

#: Exhibit types that end the stream early (the best press-release candidates).
_PREFERRED_EXHIBITS = frozenset({"EX-99.1", "EX-99.01", "EX-99"})

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ITEM = re.compile(r"\bItem\s+\d\.\d{2}\b", re.IGNORECASE)
_EMERGING_GROWTH = re.compile(r"emerging growth company", re.IGNORECASE)
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


# ── Pure helpers (unit-tested) ───────────────────────────────────────────


def parse_items(raw: Any) -> list[str] | None:
    """``"2.02,9.01"`` -> ``["2.02", "9.01"]``; empty -> ``None``."""
    if not isinstance(raw, str):
        return None
    items = [i.strip() for i in raw.split(",") if i.strip()]
    return items or None


def _clip(value: Any, length: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:length]


def filing_url(cik: str, accession_number: str, primary_document: str | None) -> str:
    """Document URL on sec.gov (the filing index if there is no primary document)."""
    folder = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession_number.replace('-', '')}"
    )
    return f"{folder}/{primary_document}" if primary_document else f"{folder}/"


def extract_filing_rows(
    columns: dict,
    since: date,
    forms: frozenset[str] = FORMS,
) -> list[dict]:
    """Whitelisted filings on/after ``since`` from the parallel arrays of a
    submissions JSON (``filings.recent`` or an older ``filings.files`` page).

    Returns dicts with the ``sec_filings`` columns that do not depend on the
    ticker (everything except ``cik``, ``ticker``, ``tickers``,
    ``texts_fetched_at``).
    """

    def col(name: str) -> list:
        value = columns.get(name) if isinstance(columns, dict) else None
        return value if isinstance(value, list) else []

    accessions = col("accessionNumber")
    form_col = col("form")
    filing_dates = col("filingDate")
    report_dates = col("reportDate")
    acceptances = col("acceptanceDateTime")
    items_col = col("items")
    documents = col("primaryDocument")

    def at(values: list, i: int) -> Any:
        return values[i] if i < len(values) else None

    rows: list[dict] = []
    for i, form in enumerate(form_col):
        if form not in forms:
            continue
        accession = at(accessions, i)
        filed = parse_date(at(filing_dates, i))
        if not isinstance(accession, str) or not _ACCESSION.match(accession):
            continue
        if filed is None or filed < since:
            continue
        raw_acceptance = at(acceptances, i)
        rows.append(
            {
                "accession_number": accession,
                "form": form[:20],
                "filing_date": filed,
                "acceptance_datetime": submission_acceptance_utc(raw_acceptance),
                "acceptance_raw": _clip(raw_acceptance, 32),
                "report_date": parse_date(at(report_dates, i)),
                "items": parse_items(at(items_col, i)),
                "primary_document": _clip(at(documents, i), 255),
                "is_amendment": form.endswith("/A"),
            }
        )
    return rows


def text_plan(form: str, items: list[str] | None) -> tuple[bool, bool]:
    """``(want_press_release, want_body)`` for a filing."""
    if form not in TEXT_FORMS or not items:
        return False, False
    wanted = set(items)
    return bool(wanted & PRESS_RELEASE_ITEMS), bool(wanted & BODY_ITEMS)


def html_to_text(markup: str) -> str:
    """Visible text of an HTML / iXBRL / plain-text document, whitespace-normalised.

    Drops scripts, styles, the ``<head>`` / ``<title>``, the hidden inline-XBRL header
    (``ix:header``) and any element with ``display:none``.
    """
    if "<" not in markup:
        return " ".join(markup.split())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(markup, "lxml")
    # (lxml drops a <head> that sits inside the <XBRL> wrapper of the SGML file
    # and leaves its <title> in the body, so <title> is removed by name as well)
    for tag in soup(["script", "style", "head", "title", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=_DISPLAY_NONE):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def strip_cover_page(text: str) -> str:
    """The text of an 8-K from its first ``Item x.xx`` after the cover page.

    The cover page (SEC header, check boxes, securities table, "emerging growth
    company" statement) would use up the excerpt. Without an
    "emerging growth company" marker the first ``Item`` of the text is used;
    if there is no item the text is returned unchanged.
    """
    markers = list(_EMERGING_GROWTH.finditer(text[:8000]))
    start = markers[-1].end() if markers else 0
    item = _ITEM.search(text, start)
    if item:
        return text[item.start():]
    return text[start:].strip() if markers else text


def make_excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """First ``limit`` characters, cut at a word boundary (``...`` if cut)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit * 0.8:
        cut = cut[:space]
    return cut.rstrip() + "…"


def select_press_release(documents: Iterable[SgmlDocument]) -> SgmlDocument | None:
    """Best EX-99.x document: EX-99.1, then EX-99, then the lowest other EX-99.x."""

    def rank(doc: SgmlDocument) -> tuple:
        kind = doc.type.upper()
        if kind in ("EX-99.1", "EX-99.01"):
            return (0, doc.sequence or 0)
        if kind == "EX-99":
            return (1, doc.sequence or 0)
        return (2, doc.sequence or 0)

    candidates = [d for d in documents if d.type.upper().startswith("EX-99")]
    return min(candidates, key=rank) if candidates else None


def _text_row(doc: SgmlDocument, exhibit: str, body: bool) -> dict | None:
    cleaned = html_to_text(doc.text())
    if body:
        cleaned = strip_cover_page(cleaned)
    if not cleaned:
        return None
    return {
        "exhibit": exhibit[:20],
        "filename": _clip(doc.filename, 255),
        "excerpt": make_excerpt(cleaned),
        "full_length": len(cleaned),
    }


def build_texts(
    documents: Iterable[SgmlDocument], want_press_release: bool, want_body: bool
) -> list[dict]:
    """Text rows from the documents of one filing; stops reading when done.

    ``documents`` is a lazy iterator (closing it ends the download): the loop
    breaks after the primary document if only the body is wanted, and after the
    first preferred press-release exhibit otherwise.
    """
    body: SgmlDocument | None = None
    exhibits: list[SgmlDocument] = []
    for doc in documents:
        kind = doc.type.upper()
        if kind in ("8-K", "8-K/A") and body is None:
            body = doc
            if not want_press_release:
                break
        elif kind.startswith("EX-99") and want_press_release:
            exhibits.append(doc)
            if kind in _PREFERRED_EXHIBITS:
                break

    rows: list[dict] = []
    if want_press_release:
        release = select_press_release(exhibits)
        if release is not None:
            row = _text_row(release, release.type.upper(), body=False)
            if row:
                rows.append(row)
    if body is not None and (want_body or (want_press_release and not rows)):
        row = _text_row(body, "8-K", body=True)
        if row:
            rows.append(row)
    return rows


# ── Collector ────────────────────────────────────────────────────────────


class SecFilingsCollector(BaseCollector):
    """Collect SEC filings (and 8-K texts) of the universe companies."""

    name = "sec_filings"

    def __init__(
        self,
        lookback_days: int = 14,
        fetch_texts: bool = True,
        client: SECClient | None = None,
        only_tickers: Iterable[str] | None = None,
    ) -> None:
        """
        Args:
            lookback_days: Filings of the last N days are (re-)checked; also the
                window in which a failed text fetch is retried. 14 days make the
                daily run robust against a few missed days.
            fetch_texts: ``False`` plans the text fetches without downloading
                them (dry run of the backfill script).
            client: SEC client (tests inject a mock).
            only_tickers: Restrict the run to these universe tickers (targeted
                backfill / manual check); default all active tickers.
        """
        self.lookback_days = lookback_days
        self.fetch_texts = fetch_texts
        self.sec_client = client or SECClient()
        self.only_tickers = set(only_tickers) if only_tickers else None

    # ── fetch ────────────────────────────────────────────────────────────

    def fetch(self, session: Session) -> list[dict]:
        """Download new filings (and the texts that are still missing).

        Returns one dict per filing to write: ``filing`` (row for
        ``sec_filings``, ``None`` if the filing is already stored),
        ``accession_number``, ``texts`` (rows for ``sec_filing_texts``) and
        ``texts_done`` (the text step is finished for this filing).
        """
        since = max(
            date.today() - timedelta(days=self.lookback_days), data_start_date()
        )
        stored = self._stored_filings(session, since)
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

        results: list[dict] = []
        new_filings = text_retries = text_requests = 0
        for cik, cik_tickers in groups.items():
            cik_tickers = sorted(cik_tickers)
            try:
                filings = self._filings_of(cik, since)
                self.record_success()
            except Exception as e:
                logger.warning(
                    f"[{self.name}] {cik_tickers[0]} (CIK {cik}): "
                    f"submissions error: {e}"
                )
                self.record_error()
                continue

            for filing in sorted(
                filings, key=lambda f: (f["filing_date"], f["accession_number"])
            ):
                accession = filing["accession_number"]
                is_new = accession not in stored
                want_release, want_body = text_plan(filing["form"], filing["items"])
                wants_text = want_release or want_body
                texts_done = not wants_text or (not is_new and stored[accession])
                if not is_new and texts_done:
                    continue  # nothing to do for this filing
                entry: dict[str, Any] = {
                    "accession_number": accession,
                    "filing": None,
                    "texts": [],
                    "texts_done": texts_done,
                }
                if is_new:
                    new_filings += 1
                    entry["filing"] = {
                        **filing,
                        "cik": cik,
                        "ticker": cik_tickers[0],
                        "tickers": cik_tickers,
                        "url": filing_url(cik, accession, filing["primary_document"]),
                    }
                else:
                    text_retries += 1
                if wants_text and not texts_done and self.fetch_texts:
                    text_requests += 1
                    documents = None
                    try:
                        documents = self.sec_client.iter_filing_documents(
                            cik, accession
                        )
                        entry["texts"] = build_texts(documents, want_release, want_body)
                        entry["texts_done"] = True
                        self.record_success()
                    except Exception as e:
                        logger.warning(
                            f"[{self.name}] {cik_tickers[0]} {accession}: "
                            f"text fetch error (retried by the next runs): {e}"
                        )
                        self.record_error()
                    finally:
                        # ends the download if the stream was left early
                        close = getattr(documents, "close", None)
                        if callable(close):
                            close()
                results.append(entry)

        logger.info(
            f"[{self.name}] {len(groups)} companies ({no_cik} tickers without CIK), "
            f"since {since}: {new_filings} new filings, {text_retries} text retries, "
            f"{text_requests} text requests"
        )
        return results

    def _filings_of(self, cik: str, since: date) -> list[dict]:
        """Whitelisted filings of one company since ``since`` (incl. older pages)."""
        submissions = self.sec_client.fetch_submissions(cik)
        filings_block = submissions.get("filings") or {}
        recent = filings_block.get("recent") or {}
        rows = extract_filing_rows(recent, since)
        dates = [d for d in (recent.get("filingDate") or []) if d]
        # `recent` holds the newest ~1000 filings (at least one year); busy
        # filers or a long lookback need the older pages as well.
        if dates and min(dates) > since.isoformat():
            for page in filings_block.get("files") or []:
                if (page.get("filingTo") or "") < since.isoformat():
                    continue
                name = page.get("name")
                if not name:
                    continue
                page = self.sec_client.get_submissions_page(name)
                rows.extend(extract_filing_rows(page, since))
                self.record_success()
        # (the pages do not overlap in practice; keep one row per accession anyway)
        return list({row["accession_number"]: row for row in rows}.values())

    def _stored_filings(self, session: Session, since: date) -> dict[str, bool]:
        """``accession -> texts finished`` of the filings stored since ``since``.

        Ends the read transaction afterwards. An error returns an empty dict
        (the unique key still prevents duplicates).
        """
        stmt = select(SecFiling.accession_number, SecFiling.texts_fetched_at).where(
            SecFiling.filing_date >= since
        )
        try:
            result = {a: t is not None for a, t in session.execute(stmt).all()}
        except Exception as e:
            logger.warning(f"[{self.name}] Could not load stored filings: {e}")
            session.rollback()
            return {}
        release_transaction(session)
        return result

    # ── store ────────────────────────────────────────────────────────────

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Insert new filings, upsert their texts, mark the finished text steps.

        Returns:
            ``(records_fetched, records_written)`` - written = filings + texts.
        """
        new_rows = [d["filing"] for d in data if d.get("filing")]
        text_rows = [
            {
                **text,
                "accession_number": d["accession_number"],
                "fetched_at": datetime.now(UTC),
            }
            for d in data
            for text in d.get("texts") or []
        ]
        finished = [d["accession_number"] for d in data if d.get("texts_done")]

        filings_written = self._bulk_insert(
            session, SecFiling, new_rows, conflict_cols=["accession_number"]
        )
        texts_written = self._bulk_insert(
            session,
            SecFilingText,
            text_rows,
            conflict_cols=["accession_number", "exhibit"],
            update_cols=["filename", "excerpt", "full_length", "fetched_at"],
        )
        # `finished` includes filings without any text plan; marking them is
        # harmless and keeps the retry query simple.
        if finished:
            session.execute(
                update(SecFiling)
                .where(
                    SecFiling.accession_number.in_(finished),
                    SecFiling.texts_fetched_at.is_(None),
                )
                .values(texts_fetched_at=datetime.now(UTC))
            )
        session.flush()
        logger.info(
            f"[{self.name}] Stored {filings_written} filings and {texts_written} "
            f"texts ({len(finished)} text steps finished)"
        )
        return len(data), filings_written + texts_written
