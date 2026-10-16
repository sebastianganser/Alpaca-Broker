"""Wikipedia Index History Parser + GitHub CSV cross-check.

Parses historical S&P 500 and Nasdaq 100 constituent changes from Wikipedia
and converts them into membership intervals.

Sources (verified 2026-10-16):
  * S&P 500 changes: "Historical components of the S&P 500" (``Added`` /
    ``Removed`` table, ~410 rows since 1957, effective dates). The change table
    is **not** on "List of S&P 500 companies" any more -- that page only holds
    the current constituents.
  * Nasdaq 100 changes: "Historical components of the Nasdaq-100".
  * Cross-check only: GitHub ``fja05680/sp500`` snapshots (dates are snapshot
    dates, +-2 days; renames show up as exits; lags the index by weeks).

Completeness contract
---------------------
A :class:`ParseResult` is ``complete`` only if the source supplied removals as
well as additions. The additions-only fallback (current members' "Date added")
can never show who left the index; using it to seed ``index_membership`` is
the survivorship bias of 2026-08 (87 removals missing). It is therefore
flagged ``complete=False`` and logged at ERROR; seeding code must call
:func:`seed_blockers` and refuse to proceed.

Renames
-------
Change tables list a ticker rename as "NEW added / OLD removed". That is not
an index exit. Events are normalized to the CURRENT symbol
(:mod:`trading_signals.universe.ticker_aliases` plus renames detected in the
table itself) and rename pairs are cancelled, so a rename never closes an
interval.

Layout Change Detection:
  If Wikipedia changes its HTML structure, parsing fails gracefully
  with logs and an incomplete result rather than crashing the sync job.
"""

import io
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime

import pandas as pd
import requests

from trading_signals.universe.ticker_aliases import (
    DEFAULT_ALIASES,
    AliasMap,
    TickerAlias,
)
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (Trading-Signals IndexHistoryParser/1.0)"

SP500_CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
SP500_CURRENT_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_CHANGES_URL = (
    "https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100"
)

# GitHub CSV (fja05680/sp500): cross-check source only, never a seeding source.
# The maintained file is the "(Updated)" one (last row 2026-08-18 on 2026-10-16).
GITHUB_SP500_CSV_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20%28Updated%29.csv"
)
#: The original file ends on 2019-01-11 and yields no change since 2021. Kept
#: only so nobody points the backup at it again by accident.
GITHUB_SP500_CSV_URL_STALE = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)
#: A snapshot file whose newest row is older than this is stale: renames and
#: exits after it are missing, so it must not be treated as complete.
GITHUB_MAX_SNAPSHOT_AGE_DAYS = 120

#: Reason texts of rows that record a pure ticker rename ("NEW added / OLD removed").
_RENAME_REASON_RE = re.compile(
    r"ticker symbol|changed (its )?ticker|ticker change|changed (its )?symbol",
    re.IGNORECASE,
)

#: Wikimedia answers a burst of requests with 403 "Too many requests".
_RETRY_STATUS = {403, 429, 500, 502, 503, 504}
_RETRY_DELAYS = (5.0, 20.0)


def fetch_html(
    url: str,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> str:
    """GET ``url`` as text; retries throttling/transient errors (403/429/5xx).

    Raises ``requests.HTTPError`` after the last attempt.
    """
    getter = session.get if session is not None else requests.get
    kwargs = {} if session is not None else {"headers": {"User-Agent": USER_AGENT}}
    for attempt in range(len(_RETRY_DELAYS) + 1):
        resp = getter(url, timeout=timeout, **kwargs)
        if resp.status_code in _RETRY_STATUS and attempt < len(_RETRY_DELAYS):
            delay = _RETRY_DELAYS[attempt]
            logger.warning(
                f"[index_history] HTTP {resp.status_code} for {url} -- "
                f"retrying in {delay:.0f}s"
            )
            time.sleep(delay)
            continue
        resp.raise_for_status()
        return resp.text
    raise RuntimeError("unreachable")  # pragma: no cover


@dataclass
class IndexChange:
    """A single index constituent change (addition or removal)."""

    change_date: date
    ticker: str  # CURRENT symbol (renames applied)
    index_name: str  # 'sp500', 'nasdaq100'
    action: str  # 'added', 'removed'
    reason: str | None = None
    replaced_by: str | None = None
    source: str = "wikipedia"
    original_ticker: str | None = None  # symbol in the source if it was re-keyed


@dataclass
class ParseResult:
    """Result of parsing index changes from a source.

    ``complete`` is True only if the source delivered additions **and**
    removals (``incomplete_reason`` says why not otherwise). Callers that seed
    or repair ``index_membership`` must check it (see :func:`seed_blockers`).
    """

    changes: list[IndexChange] = field(default_factory=list)
    source: str = ""
    warnings: list[str] = field(default_factory=list)
    complete: bool = False
    incomplete_reason: str | None = None
    #: renames found in the change table itself (not yet in the curated list)
    renames: list[TickerAlias] = field(default_factory=list)
    #: date of the newest event / snapshot in the source (all rows, no window)
    as_of: date | None = None

    @property
    def has_removals(self) -> bool:
        return any(c.action == "removed" for c in self.changes)

    def mark_incomplete(self, reason: str) -> None:
        self.complete = False
        self.incomplete_reason = reason
        self.warnings.append(f"INCOMPLETE: {reason}")
        logger.error(f"[index_history] {self.source}: INCOMPLETE -- {reason}")


def seed_blockers(*results: ParseResult) -> list[str]:
    """Reasons why ``results`` must not seed ``index_membership`` (empty = OK)."""
    return [
        f"{r.source}: {r.incomplete_reason or 'incomplete (no removal data)'}"
        for r in results
        if not r.complete
    ]


def clean_ticker(value: object) -> str | None:
    """Normalize a ticker cell: strip footnotes/junk, upper-case, ``None`` if empty."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = re.sub(r"\[.*?\]", "", str(value))  # [1] footnote markers
    text = re.sub(r"[^A-Za-z0-9.\-]", "", text).upper()  # "ALLE |" -> "ALLE"
    if not text or text in {"NAN", "NONE", "NA", "NULL"}:
        return None
    return text


def normalize_changes(
    changes: list[IndexChange], alias_map: AliasMap = DEFAULT_ALIASES
) -> list[IndexChange]:
    """Re-key events to the current symbol and cancel rename pairs.

    * ``ticker``/``replaced_by`` become the company's current symbol
      (``alias_map.canonical_symbol``, date-aware so re-used symbols stay
      untouched); the source symbol is kept in ``original_ticker``.
    * A rename shows up as "OLD removed + NEW added" on the same day. After
      re-keying both are the same ticker; such a pair is cancelled instead of
      closing and re-opening the interval.
    * Exact duplicate events are dropped.
    """
    mapped: list[IndexChange] = []
    seen: set[tuple] = set()
    for c in changes:
        current = alias_map.canonical_symbol(c.ticker, c.change_date)
        replaced_by = (
            alias_map.canonical_symbol(c.replaced_by, c.change_date)
            if c.replaced_by
            else None
        )
        new = replace(
            c,
            ticker=current,
            replaced_by=replaced_by,
            original_ticker=(c.ticker if current != c.ticker else c.original_ticker),
        )
        key = (new.index_name, new.ticker, new.change_date, new.action)
        if key in seen:
            continue
        seen.add(key)
        mapped.append(new)

    actions: dict[tuple, set[str]] = defaultdict(set)
    rekeyed: set[tuple] = set()
    for c in mapped:
        k = (c.index_name, c.ticker, c.change_date)
        actions[k].add(c.action)
        if c.original_ticker:
            rekeyed.add(k)
    cancelled = {
        k for k, a in actions.items() if a == {"added", "removed"} and k in rekeyed
    }
    return [
        c for c in mapped if (c.index_name, c.ticker, c.change_date) not in cancelled
    ]


class WikipediaIndexHistoryParser:
    """Parse historical index changes from Wikipedia."""

    SP500_CHANGES_URL = SP500_CHANGES_URL
    SP500_CURRENT_URL = SP500_CURRENT_URL
    #: kept for callers that still use the old name (= current constituents page)
    SP500_URL = SP500_CURRENT_URL
    NASDAQ100_CHANGES_URL = NASDAQ100_CHANGES_URL

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def _read_tables(self, url: str) -> list[pd.DataFrame]:
        return pd.read_html(io.StringIO(fetch_html(url, session=self._session)))

    # ── S&P 500 ──────────────────────────────────────────────────────

    def parse_sp500_changes(self, since: date | None = None) -> ParseResult:
        """Parse S&P 500 historical changes from Wikipedia.

        Strategy:
        1. Fetch "Historical components of the S&P 500" and parse its
           Added/Removed table. Tickers are normalized to current symbols,
           rename pairs are cancelled. Result is ``complete``.
        2. If the page/table is unavailable (layout change, fetch error), fall
           back to the "Date added" column of the current constituents. That
           delivers additions of *current* members only, so the result is
           flagged **incomplete** and logged at ERROR.

        Args:
            since: Earliest event date to return (default: retention window
                start ``data_start_date()``). Rename detection and
                completeness always look at the whole table.

        Returns:
            ParseResult with IndexChange objects (``changes``), the renames
            detected in the table (``renames``) and the newest event date
            (``as_of``).
        """
        since = since or data_start_date()
        result = ParseResult(source="wikipedia_sp500")

        tables: list[pd.DataFrame] = []
        try:
            tables = self._read_tables(self.SP500_CHANGES_URL)
        except Exception as e:
            msg = f"Failed to fetch/parse S&P 500 changes page: {e}"
            logger.error(f"[index_history] {msg}")
            result.warnings.append(msg)

        changes_df = self._find_changes_table(tables, "sp500") if tables else None
        if changes_df is not None:
            try:
                return self._finish_sp500(result, changes_df, since)
            except Exception as e:
                msg = (
                    "Failed to parse the S&P 500 changes table: "
                    f"{type(e).__name__}: {e}"
                )
                logger.error(f"[index_history] {msg}")
                result.warnings.append(msg)
                result.changes, result.renames, result.as_of = [], [], None
            return self._sp500_additions_fallback(result, since)

        if tables:
            msg = (
                "S&P 500 changes table (Added/Removed) not found on "
                f"{self.SP500_CHANGES_URL}. Layout may have changed. Tables: "
                + str([list(t.columns)[:4] for t in tables[:5]])
            )
            logger.error(f"[index_history] LAYOUT CHANGE DETECTED: {msg}")
            result.warnings.append(msg)
        return self._sp500_additions_fallback(result, since)

    def _finish_sp500(
        self, result: ParseResult, df: pd.DataFrame, since: date
    ) -> ParseResult:
        all_changes, renames, result.as_of = self._parse_sp500_table(df)
        result.renames = [r for r in renames if r.effective_date >= since]
        result.changes = [c for c in all_changes if c.change_date >= since]
        removals_total = sum(1 for c in all_changes if c.action == "removed")
        additions_total = sum(1 for c in all_changes if c.action == "added")
        logger.info(
            f"[index_history] Parsed {len(all_changes)} S&P 500 changes from the "
            f"Wikipedia changes table ({removals_total} removals, {additions_total} "
            f"additions, newest {result.as_of}); {len(result.changes)} since {since}; "
            f"{len(result.renames)} ticker renames detected in the table"
        )
        if removals_total and additions_total:
            result.complete = True
        else:
            result.mark_incomplete(
                f"changes table parsed but has {removals_total} removals and "
                f"{additions_total} additions"
            )
        return result

    def _sp500_additions_fallback(
        self, result: ParseResult, since: date
    ) -> ParseResult:
        """Additions of current members only -- never a complete history."""
        try:
            tables = self._read_tables(self.SP500_CURRENT_URL)
            result.changes = self._parse_sp500_date_added(tables, since)
        except Exception as e:
            msg = f"Failed to fetch/parse S&P 500 constituents page: {e}"
            logger.error(f"[index_history] {msg}")
            result.warnings.append(msg)
        result.mark_incomplete(
            "no change table available; only 'Date added' of current members "
            f"({len(result.changes)} additions, NO removals) -- must not seed "
            "index_membership"
        )
        return result

    def _parse_sp500_date_added(
        self, tables: list[pd.DataFrame], since: date | None = None
    ) -> list[IndexChange]:
        """Extract additions from the 'Date added' column in the main table.

        When the changes table is unavailable, we can still infer when
        each current member was added to the index. This gives us
        'added' events but no 'removed' events.
        """
        changes: list[IndexChange] = []
        data_start = since or data_start_date()

        # Find the main constituents table (has 'Symbol' and 'Date added')
        for df in tables:
            cols_lower = [str(c).lower() for c in df.columns]
            if "symbol" in cols_lower and any("date" in c for c in cols_lower):
                symbol_col = df.columns[cols_lower.index("symbol")]
                date_col = None
                for c in df.columns:
                    if "date" in str(c).lower() and "added" in str(c).lower():
                        date_col = c
                        break
                if date_col is None:
                    continue

                for _, row in df.iterrows():
                    ticker = clean_ticker(row[symbol_col])
                    added_date = self._parse_date(str(row[date_col]).strip())

                    if not ticker or not added_date:
                        continue
                    if added_date < data_start:
                        continue

                    changes.append(IndexChange(
                        change_date=added_date,
                        ticker=ticker,
                        index_name="sp500",
                        action="added",
                        reason="From Wikipedia 'Date added' column",
                        source="wikipedia",
                    ))

                logger.info(
                    f"[index_history] Found {len(changes)} tickers added "
                    f"to S&P 500 since {data_start}"
                )
                return changes

        return changes

    # ── Nasdaq 100 ───────────────────────────────────────────────────

    def parse_nasdaq100_changes(self, since: date | None = None) -> ParseResult:
        """Parse Nasdaq 100 historical changes from Wikipedia.

        The 'Historical components of the Nasdaq-100' page has yearly
        tables with additions and removals. ``complete`` means removals were
        found as well as additions.

        Returns:
            ParseResult with list of IndexChange objects.
        """
        result = ParseResult(source="wikipedia_nasdaq100")

        try:
            tables = self._read_tables(self.NASDAQ100_CHANGES_URL)
        except Exception as e:
            msg = f"Failed to fetch/parse Nasdaq 100 Wikipedia page: {e}"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            result.mark_incomplete(msg)
            return result

        if not tables:
            msg = (
                "No tables found on Nasdaq 100 historical changes page. "
                "Layout may have changed."
            )
            logger.warning(f"[index_history] LAYOUT CHANGE DETECTED: {msg}")
            result.warnings.append(msg)
            result.mark_incomplete(msg)
            return result

        # Parse Nasdaq 100 yearly change tables
        result.changes = self._parse_nasdaq100_tables(tables, since)
        result.as_of = max((c.change_date for c in result.changes), default=None)
        logger.info(
            f"[index_history] Parsed {len(result.changes)} Nasdaq 100 changes "
            f"from Wikipedia"
        )
        if result.has_removals and any(c.action == "added" for c in result.changes):
            result.complete = True
        else:
            result.mark_incomplete(
                "Nasdaq 100 history has no removals or no additions -- "
                "the page layout probably changed"
            )
        return result

    # ── S&P 500 changes table ────────────────────────────────────────

    @staticmethod
    def _header_tokens(columns: pd.Index) -> list[list[str]]:
        """Lower-cased header cell texts per column: ``[group, sub]`` or ``[name]``."""
        tokens = []
        for col in columns:
            parts = col if isinstance(col, tuple) else (col,)
            tokens.append([str(p).strip().lower() for p in parts])
        return tokens

    def _find_changes_table(
        self, tables: list[pd.DataFrame], index_name: str
    ) -> pd.DataFrame | None:
        """Find the 'Selected changes' table among parsed tables.

        Uses heuristics to identify the correct table:
        - Must have BOTH 'Added' and 'Removed' column groups. Wikipedia nests
          the headers (top level Added/Removed, sub level Ticker/Security), so
          every level of a MultiIndex is inspected -- flattening to the last
          level alone drops exactly the group names.
        - Must NOT have a 'Symbol' column (that's the main constituents table)
        - Must have more than 10 rows
        - 'Date added' in the main table does NOT count as a changes table
        """
        for i, df in enumerate(tables):
            tokens = self._header_tokens(df.columns)
            flat = [t for col in tokens for t in col]

            # Skip the main constituents table (has 'Symbol' column)
            if "symbol" in flat:
                continue

            has_date = any("date" in t for t in flat)
            # Must have BOTH added and removed as separate column concepts
            has_added = any(
                t == "added" or t.startswith("added ") or t.endswith(" added")
                for t in flat
            )
            has_removed = any(
                t == "removed" or t.startswith("removed ") or t.endswith(" removed")
                for t in flat
            )

            if has_date and has_added and has_removed and len(df) > 10:
                logger.debug(
                    f"[index_history] Found {index_name} changes table "
                    f"at index {i} ({len(df)} rows)"
                )
                return df

        return None

    def _resolve_sp500_columns(self, df: pd.DataFrame) -> dict[str, object]:
        """Map date / added_ticker / removed_ticker / reason to column labels."""
        col_map: dict[str, object] = {}
        headers = self._header_tokens(df.columns)
        for col, tokens in zip(df.columns, headers, strict=True):
            joined = " ".join(tokens)
            group = tokens[0]
            if "date" in joined and "date" not in col_map:
                col_map["date"] = col
            elif "ticker" in joined and group.startswith("added"):
                col_map["added_ticker"] = col
            elif "ticker" in joined and group.startswith("removed"):
                col_map["removed_ticker"] = col
            elif "reason" in joined and "reason" not in col_map:
                col_map["reason"] = col
        return col_map

    def _parse_sp500_table(
        self, df: pd.DataFrame
    ) -> tuple[list[IndexChange], list[TickerAlias], date | None]:
        """Parse the whole changes table (no date window).

        Returns ``(changes, renames, newest)``: events normalized to current
        symbols with rename pairs cancelled, the renames recognised from the
        "changed its ticker symbol" rows, and the newest row date.
        """
        col_map = self._resolve_sp500_columns(df)
        required = ("date", "added_ticker", "removed_ticker")
        missing = [k for k in required if k not in col_map]
        if missing:
            logger.error(
                f"[index_history] S&P 500 changes table: columns {missing} not found. "
                f"Columns: {list(df.columns)}"
            )
            return [], [], None

        raw: list[IndexChange] = []
        renames: list[TickerAlias] = []
        newest: date | None = None
        bad_dates = 0
        for _, row in df.iterrows():
            change_date = self._parse_date(str(row.get(col_map["date"], "")))
            if not change_date:
                bad_dates += 1
                continue
            newest = max(newest, change_date) if newest else change_date

            reason = None
            if "reason" in col_map:
                text = str(row.get(col_map["reason"], "")).strip()
                reason = None if text in ("nan", "") else text

            added = clean_ticker(row.get(col_map["added_ticker"]))
            removed = clean_ticker(row.get(col_map["removed_ticker"]))

            is_rename_row = (
                added
                and removed
                and added != removed
                and reason
                and _RENAME_REASON_RE.search(reason)
            )
            if is_rename_row:
                renames.append(TickerAlias(removed, added, change_date, reason))

            if added:
                raw.append(IndexChange(
                    change_date=change_date,
                    ticker=added,
                    index_name="sp500",
                    action="added",
                    reason=reason,
                    source="wikipedia",
                ))
            if removed:
                raw.append(IndexChange(
                    change_date=change_date,
                    ticker=removed,
                    index_name="sp500",
                    action="removed",
                    reason=reason,
                    replaced_by=added,
                    source="wikipedia",
                ))

        if bad_dates:
            logger.warning(
                f"[index_history] {bad_dates} rows of the S&P 500 changes table "
                "have no parsable date and were skipped"
            )
        alias_map = DEFAULT_ALIASES.with_extra(renames)
        changes = normalize_changes(raw, alias_map)
        changes.sort(key=lambda c: c.change_date)
        return changes, renames, newest

    def _parse_sp500_changes_df(
        self, df: pd.DataFrame, since: date | None = None
    ) -> list[IndexChange]:
        """Parse S&P 500 changes DataFrame into IndexChange objects.

        Handles MultiIndex columns from Wikipedia's nested table headers.
        The S&P 500 changes table has the structure:
          Effective Date | Added (Ticker, Security) | Removed (Ticker, Security)
          | Reason | Refs
        Events before ``since`` (default: retention window start) are dropped.
        """
        since = since or data_start_date()
        changes, _, _ = self._parse_sp500_table(df)
        return [c for c in changes if c.change_date >= since]

    # ── Nasdaq 100 tables ────────────────────────────────────────────

    def _parse_nasdaq100_tables(
        self, tables: list[pd.DataFrame], since: date | None = None
    ) -> list[IndexChange]:
        """Parse Nasdaq 100 yearly change tables.

        The Wikipedia page has multiple tables, one per year/period.
        Each table typically has 'Added' and 'Removed' columns.
        """
        changes: list[IndexChange] = []
        data_start = since or data_start_date()

        for df in tables:
            # Look for tables with 'added' and 'removed' columns
            added_col = None
            removed_col = None
            date_col = None

            for col in df.columns:
                cl = str(col).lower()
                if "added" in cl and added_col is None:
                    added_col = col
                elif "removed" in cl and removed_col is None:
                    removed_col = col
                elif "date" in cl or "year" in cl:
                    date_col = col

            if not added_col and not removed_col:
                continue

            for _, row in df.iterrows():
                # Try to extract a date
                change_date = None
                if date_col:
                    change_date = self._parse_date(str(row.get(date_col, "")))

                # Parse added tickers
                if added_col:
                    added_str = str(row.get(added_col, "")).strip()
                    if added_str and added_str != "nan":
                        tickers = self._extract_tickers_from_cell(added_str)
                        for t in tickers:
                            if change_date and change_date >= data_start:
                                changes.append(IndexChange(
                                    change_date=change_date,
                                    ticker=t,
                                    index_name="nasdaq100",
                                    action="added",
                                    source="wikipedia",
                                ))

                # Parse removed tickers
                if removed_col:
                    removed_str = str(row.get(removed_col, "")).strip()
                    if removed_str and removed_str != "nan":
                        tickers = self._extract_tickers_from_cell(removed_str)
                        for t in tickers:
                            if change_date and change_date >= data_start:
                                changes.append(IndexChange(
                                    change_date=change_date,
                                    ticker=t,
                                    index_name="nasdaq100",
                                    action="removed",
                                    source="wikipedia",
                                ))

        return changes

    def _extract_tickers_from_cell(self, cell: str) -> list[str]:
        """Extract ticker symbols from a cell that may contain multiple.

        Wikipedia cells sometimes have formats like:
        'AAPL', 'AAPL, MSFT', 'AAPL (Apple Inc.)'
        """
        # Remove parenthetical content
        cell = re.sub(r"\(.*?\)", "", cell)
        # Split on common separators
        parts = re.split(r"[,;&/\n]+", cell)
        tickers = []
        # Known false positives from pandas NaN representation
        false_positives = {"NAN", "NONE", "NA", "NULL"}
        for part in parts:
            t = part.strip().upper()
            # Valid ticker: 1-5 uppercase letters, possibly with dots
            if t and t not in false_positives and re.match(r"^[A-Z]{1,5}\.?[A-Z]?$", t):
                tickers.append(t)
        return tickers

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
        """Parse various date formats from Wikipedia tables."""
        if not date_str or date_str == "nan":
            return None

        # Common Wikipedia date formats
        for fmt in (
            "%B %d, %Y",      # January 15, 2024
            "%b %d, %Y",      # Jan 15, 2024
            "%Y-%m-%d",       # 2024-01-15
            "%m/%d/%Y",       # 01/15/2024
            "%d %B %Y",       # 15 January 2024
            "%B %Y",          # January 2024 (set to 1st)
        ):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue

        return None


class GitHubSP500Backup:
    """Cross-check source: fja05680/sp500 GitHub CSV.

    One row per snapshot date with the comma-separated list of all
    constituents at that date (1996 -> newest update). Consecutive snapshots
    are diffed into additions/removals.

    **Not a seeding source.** Dates are snapshot dates (up to 2 days off the
    effective date), a ticker rename appears as exit + addition (cancelled via
    ``ticker_aliases`` where known), mergers like PARA -> PSKY are mixed with
    renames, and the file lags the index by weeks. A file whose newest
    snapshot is older than ``GITHUB_MAX_SNAPSHOT_AGE_DAYS`` is marked
    incomplete (``GITHUB_SP500_CSV_URL_STALE`` is such a file: it ends in 2019).
    """

    def fetch_changes(self, since: date | None = None) -> ParseResult:
        """Fetch and parse S&P 500 changes from the GitHub CSV.

        The CSV has format:
          date | tickers (comma-separated list of all constituents)

        We diff consecutive dates to extract additions and removals.

        Returns:
            ParseResult with extracted changes (``as_of`` = newest snapshot).
        """
        since = since or data_start_date()
        result = ParseResult(source="github_fja05680")

        try:
            resp = requests.get(
                GITHUB_SP500_CSV_URL,
                headers={"User-Agent": USER_AGENT},
                timeout=60,
            )
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as e:
            msg = f"Failed to fetch GitHub S&P 500 CSV: {e}"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            result.mark_incomplete(msg)
            return result

        if df.empty or len(df.columns) < 2:
            msg = "GitHub S&P 500 CSV has unexpected format"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            result.mark_incomplete(msg)
            return result

        snapshots: list[tuple[date, str]] = []
        for raw_date, raw_tickers in zip(df.iloc[:, 0], df.iloc[:, 1], strict=True):
            try:
                snapshots.append((pd.to_datetime(raw_date).date(), str(raw_tickers)))
            except Exception:
                continue
        snapshots.sort(key=lambda s: s[0])
        if not snapshots:
            msg = "GitHub S&P 500 CSV has no parsable snapshot dates"
            result.warnings.append(msg)
            result.mark_incomplete(msg)
            return result

        def _tickers(raw_tickers: str) -> set[str]:
            return {t.strip().upper() for t in raw_tickers.split(",") if t.strip()}

        # Only the last snapshot before the window is needed as the baseline
        first_in_window = next(
            (i for i, (d, _) in enumerate(snapshots) if d >= since), len(snapshots)
        )
        raw: list[IndexChange] = []
        prev_tickers: set[str] | None = (
            _tickers(snapshots[first_in_window - 1][1]) if first_in_window else None
        )
        for row_date, raw_tickers in snapshots[first_in_window:]:
            current = _tickers(raw_tickers)
            if prev_tickers is not None:
                for ticker in sorted(current - prev_tickers):
                    raw.append(IndexChange(
                        change_date=row_date,
                        ticker=ticker,
                        index_name="sp500",
                        action="added",
                        source="github_csv",
                    ))
                for ticker in sorted(prev_tickers - current):
                    raw.append(IndexChange(
                        change_date=row_date,
                        ticker=ticker,
                        index_name="sp500",
                        action="removed",
                        source="github_csv",
                    ))
            prev_tickers = current

        result.changes = normalize_changes(raw)
        result.as_of = snapshots[-1][0]
        logger.info(
            f"[index_history] Parsed {len(result.changes)} S&P 500 changes "
            f"from GitHub CSV backup (newest snapshot {result.as_of})"
        )

        age_days = (date.today() - result.as_of).days
        if age_days > GITHUB_MAX_SNAPSHOT_AGE_DAYS:
            result.mark_incomplete(
                f"newest snapshot is {result.as_of} ({age_days} days old, limit "
                f"{GITHUB_MAX_SNAPSHOT_AGE_DAYS}) -- renames/exits since then "
                "are missing"
            )
        elif not result.has_removals:
            result.mark_incomplete(f"no removals found since {since}")
        else:
            result.complete = True
        return result


def generate_intervals_from_changes(
    changes: list[IndexChange],
    current_members: dict[str, set[str]],
    data_start: date,
) -> list[dict]:
    """Convert a chronological list of changes into membership intervals.

    For each (ticker, index_name) pair, generates intervals:
      - If ticker was added and later removed: [added_date, removed_date)
      - If ticker was added and is still current: [added_date, None)
      - If ticker is currently in the index but never appeared in changes:
        it's been a member since before our tracking → [DATA_START_DATE, None)

    Args:
        changes: Sorted list of IndexChange objects.
        current_members: Dict mapping index_name → set of current tickers.
        data_start: Earliest date for intervals (typically DATA_START_DATE).

    Returns:
        List of dicts ready for INSERT into index_membership.
    """
    # Sort changes chronologically
    sorted_changes = sorted(changes, key=lambda c: c.change_date)

    # Track state per (ticker, index_name)
    # open_intervals: dict mapping (ticker, index) → (valid_from, source, reason)
    open_intervals: dict[tuple[str, str], tuple[date, str, str | None]] = {}
    closed_intervals: list[dict] = []

    # Track all tickers that appear in changes
    seen_in_changes: dict[str, set[str]] = {}  # index_name → set of tickers

    for change in sorted_changes:
        key = (change.ticker, change.index_name)
        seen_in_changes.setdefault(change.index_name, set()).add(change.ticker)

        if change.action == "added":
            if key not in open_intervals:
                open_intervals[key] = (
                    change.change_date, change.source, change.reason
                )
            # If already open (duplicate add), skip

        elif change.action == "removed":
            if key in open_intervals:
                # Close the interval
                valid_from, source, reason = open_intervals.pop(key)
                closed_intervals.append({
                    "ticker": change.ticker,
                    "index_name": change.index_name,
                    "valid_from": valid_from,
                    "valid_to": change.change_date,
                    "reason": change.reason or reason,
                    "replaced_by": change.replaced_by,
                    "source": source,
                })
            else:
                # Removed but no prior "added" event → was member since before
                # our data window. Create interval from data_start
                closed_intervals.append({
                    "ticker": change.ticker,
                    "index_name": change.index_name,
                    "valid_from": data_start,
                    "valid_to": change.change_date,
                    "reason": change.reason,
                    "replaced_by": change.replaced_by,
                    "source": change.source,
                })

    # Convert still-open intervals to records (valid_to = NULL = current member)
    for (ticker, index_name), (valid_from, source, reason) in open_intervals.items():
        closed_intervals.append({
            "ticker": ticker,
            "index_name": index_name,
            "valid_from": valid_from,
            "valid_to": None,
            "reason": reason,
            "replaced_by": None,
            "source": source,
        })

    # Add intervals for tickers that are current members but never appeared
    # in any changes (long-standing members since before our data window)
    for index_name, current_set in current_members.items():
        seen = seen_in_changes.get(index_name, set())
        already_covered = {
            iv["ticker"]
            for iv in closed_intervals
            if iv["index_name"] == index_name and iv["valid_to"] is None
        }
        for ticker in current_set:
            if ticker not in seen and ticker not in already_covered:
                closed_intervals.append({
                    "ticker": ticker,
                    "index_name": index_name,
                    "valid_from": data_start,
                    "valid_to": None,
                    "reason": None,
                    "replaced_by": None,
                    "source": "initial_seed",
                })

    logger.info(
        f"[index_history] Generated {len(closed_intervals)} membership intervals "
        f"({sum(1 for iv in closed_intervals if iv['valid_to'] is None)} "
        f"currently active)"
    )
    return closed_intervals
