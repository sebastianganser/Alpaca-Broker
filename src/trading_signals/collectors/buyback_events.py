"""Share buyback authorizations from 8-K filings (concept §7.4, step D1).

Searches SEC EDGAR full-text search (free, history from 2001) for 8-K
documents mentioning a repurchase programme, keeps filings of universe
companies and classifies the text: an event is a **new or increased
authorization** by the board. Execution reports ("repurchased 1.2 million
shares during the quarter"), remaining-capacity statements and debt
repurchases do not count.

Pure helpers (unit-tested): :func:`html_to_text`, :func:`split_sentences`,
:func:`classify_buyback_text`, :func:`parse_fts_hit`.
Network: :class:`BuybackEventSearcher` (rate-limited via :class:`SECClient`).

Timing: the event date is the 8-K filing date. EDGAR assigns filings
accepted after 17:30 ET to the next business day, so a trade entered at the
open of the session after the filing date never uses future information.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from trading_signals.collectors.sec_client import SECClient
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
FTS_PAGE_SIZE = 100
#: EDGAR full-text search returns at most 10,000 hits per query.
FTS_MAX_HITS = 10_000

#: Phrase queries (results are united per filing) – as fixed in concept §7.4.
SEARCH_PHRASES: tuple[str, ...] = (
    '"share repurchase program"',
    '"stock repurchase program"',
    '"share buyback program"',
    '"repurchase authorization"',
)

EVENT_TYPE = "buyback_authorization"

_REP = re.compile(r"\b(re-?purchas\w*|buy-?backs?|buy back)\b", re.I)
_AUTH = re.compile(r"\b(authoriz\w*|authoris\w*|approv\w*)\b", re.I)
_POS = re.compile(
    r"\b(new|additional|increas\w*|up to|expand\w*|replac\w*)\b"
    r"|\$\s?[\d.,]+\s*(million|billion|B|M)\b",
    re.I,
)
_NEG = re.compile(
    r"\b(previously\s+(authoriz\w*|approv\w*|announc\w*|reported|disclosed)"
    r"|as previously|remain\w*|available|subject to (the )?approval|will be subject"
    r"|repurchased|repurchasing|purchased|bought back"
    r"|notes|debentures|bonds|debt|convertible|preferred)\b",
    re.I,
)
_AMOUNT = re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(million|billion|B|M)\b", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
#: "January 2023", "December 14, 2023", "Nov. 27, 2023"
_MONTH_DATE = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s+(?:(\d{1,2}),?\s+)?(20\d{2})\b", re.I
)
#: "in December" (no year) – month case-sensitive to avoid the verb "may"
_MONTH_ONLY = re.compile(
    r"\b(?:[Ii]n|[Dd]uring|[Ss]ince|[Oo]n)\s+("
    + "|".join(m.capitalize() for m in _MONTH_NAMES)
    + r")\b(?!\.?\s+(?:\d{1,2},?\s+)?20\d{2})"
)
_SENT_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z“\"(•])|\s*•\s*")

#: Max characters of the qualifying sentence that are kept as evidence.
SNIPPET_LEN = 400
#: Longer "sentences" are merged boilerplate / tables, not announcements.
MAX_SENTENCE_LEN = 600
#: A sentence whose latest date lies before (filing date − this) is history.
STALE_DAYS = 30


@dataclass(frozen=True)
class BuybackClassification:
    """Result of :func:`classify_buyback_text`."""

    is_event: bool
    amount_usd: float | None = None
    snippet: str | None = None


def html_to_text(html: str) -> str:
    """Visible text of an HTML/TXT filing document, whitespace-normalised."""
    if "<" not in html:
        return " ".join(html.split())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def split_sentences(text: str) -> list[str]:
    """Rough sentence split (also on bullet points)."""
    return [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]


def parse_amount(sentence: str) -> float | None:
    """First ``$X million|billion`` amount in a sentence, in USD."""
    m = _AMOUNT.search(sentence)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return value * (1e9 if m.group(2).lower() in ("billion", "b") else 1e6)


def _latest_month_occurrence(month: int, as_of: date) -> date:
    """Mid-month date of the latest ``month`` that starts on or before ``as_of``."""
    year = as_of.year if month <= as_of.month else as_of.year - 1
    return date(year, month, 15)


def is_stale(sentence: str, as_of: date | None) -> bool:
    """True if the sentence's first date lies before ``as_of − STALE_DAYS``.

    The first date decides because announcements start with it ("On
    February 2, 2024, the Board approved …"), later dates are usually end
    dates ("… through Dec. 26, 2026"). Month dates: "January 2023",
    "Nov. 27, 2023" (a month without day counts as its 15th); month-only
    references: "in December" = the latest December before the filing.
    Without any month, bare years count as stale if all lie before the year
    of the cutoff.
    """
    if as_of is None:
        return False
    cutoff = as_of - timedelta(days=STALE_DAYS)
    dated: list[tuple[int, date]] = []
    for m in _MONTH_DATE.finditer(sentence):
        month = _MONTHS[m.group(1).lower()]
        day = int(m.group(2)) if m.group(2) else 15
        try:
            dated.append((m.start(), date(int(m.group(3)), month, min(day, 28))))
        except ValueError:
            continue
    for m in _MONTH_ONLY.finditer(sentence):
        month = _MONTHS[m.group(1).lower()]
        dated.append((m.start(), _latest_month_occurrence(month, as_of)))
    if dated:
        return min(dated)[1] < cutoff
    years = [int(y) for y in _YEAR.findall(sentence)]
    return bool(years) and max(years) < cutoff.year


def is_authorization_sentence(sentence: str, as_of: date | None = None) -> bool:
    """True if a sentence announces a new or increased buyback authorization."""
    return bool(
        len(sentence) <= MAX_SENTENCE_LEN
        and _REP.search(sentence)
        and _AUTH.search(sentence)
        and _POS.search(sentence)
        and not _NEG.search(sentence)
        and not is_stale(sentence, as_of)
    )


def candidate_sentences(text: str) -> list[str]:
    """Sentences mentioning a repurchase/buyback (input for classification)."""
    return [s for s in split_sentences(text) if _REP.search(s)]


def classify_sentences(
    sentences: Iterable[str], as_of: date | None = None
) -> BuybackClassification:
    """Classify candidate sentences of one document (first match wins)."""
    for sentence in sentences:
        if is_authorization_sentence(sentence, as_of):
            return BuybackClassification(
                is_event=True,
                amount_usd=parse_amount(sentence),
                snippet=sentence[:SNIPPET_LEN],
            )
    return BuybackClassification(is_event=False)


def classify_buyback_text(text: str, as_of: date | None = None) -> BuybackClassification:
    """Classify a filing document text (see module docstring).

    Args:
        text: Visible document text.
        as_of: Filing date – sentences about earlier dates are ignored.
    """
    return classify_sentences(split_sentences(text), as_of)


@dataclass(frozen=True)
class FtsHit:
    """One document hit of the EDGAR full-text search."""

    accession_number: str
    document: str
    cik: str  # 10-digit, zero-padded
    file_date: date
    form: str
    file_type: str
    items: tuple[str, ...]
    display_name: str


def parse_fts_hit(hit: dict) -> FtsHit | None:
    """Parse one ``hits.hits`` entry; ``None`` if essential fields are missing."""
    src = hit.get("_source") or {}
    hit_id = hit.get("_id") or ""
    if ":" not in hit_id:
        return None
    adsh, document = hit_id.split(":", 1)
    ciks = src.get("ciks") or []
    try:
        file_date = date.fromisoformat(src.get("file_date", ""))
    except ValueError:
        return None
    if not ciks or not adsh:
        return None
    names = src.get("display_names") or [""]
    return FtsHit(
        accession_number=adsh,
        document=document,
        cik=str(ciks[0]).zfill(10),
        file_date=file_date,
        form=src.get("form") or "",
        file_type=src.get("file_type") or "",
        items=tuple(src.get("items") or ()),
        display_name=names[0],
    )


def month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Calendar-month windows ``[a, b]`` covering ``start`` … ``end``."""
    a = start
    while a <= end:
        nxt = (a.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield a, min(nxt - timedelta(days=1), end)
        a = nxt


class BuybackEventSearcher:
    """EDGAR full-text search + document classification for buyback events."""

    def __init__(self, client: SECClient | None = None) -> None:
        self.client = client or SECClient()

    def search(
        self, start: date, end: date, phrases: Iterable[str] = SEARCH_PHRASES
    ) -> list[FtsHit]:
        """All 8-K document hits for ``phrases`` in ``[start, end]`` (deduplicated)."""
        seen: dict[tuple[str, str], FtsHit] = {}
        for a, b in month_windows(start, end):
            for phrase in phrases:
                for hit in self._search_window(phrase, a, b):
                    seen.setdefault((hit.accession_number, hit.document), hit)
        return list(seen.values())

    def _search_window(self, phrase: str, a: date, b: date) -> Iterator[FtsHit]:
        offset = 0
        while True:
            params = {
                "q": phrase, "forms": "8-K", "dateRange": "custom",
                "startdt": a.isoformat(), "enddt": b.isoformat(), "from": offset,
            }
            data = self.client._get_json(f"{FTS_URL}?{urlencode(params)}")
            hits = (data.get("hits") or {}).get("hits") or []
            total = ((data.get("hits") or {}).get("total") or {}).get("value", 0)
            for raw in hits:
                hit = parse_fts_hit(raw)
                if hit is not None and hit.form == "8-K":
                    yield hit
            offset += len(hits)
            if not hits or offset >= min(total, FTS_MAX_HITS):
                if total >= FTS_MAX_HITS:
                    logger.warning(
                        f"[buyback] {phrase} {a}..{b}: hit cap reached, "
                        "window may be incomplete"
                    )
                return

    def classify(self, hit: FtsHit) -> BuybackClassification:
        """Download one hit document and classify it."""
        raw = self.client.download_filing_document(
            hit.cik, hit.accession_number, hit.document
        )
        return classify_buyback_text(html_to_text(raw), as_of=hit.file_date)
