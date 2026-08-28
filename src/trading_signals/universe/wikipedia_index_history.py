"""Wikipedia Index History Parser + GitHub CSV Backup.

Parses historical S&P 500 and Nasdaq 100 constituent changes from Wikipedia's
"Selected changes" tables and converts them into membership intervals.

Data flow:
  Wikipedia HTML → pandas.read_html() → Changes DataFrame
    → Interval generation → INSERT into index_membership

Backup source:
  GitHub fja05680/sp500 CSV (historical S&P 500 components since 1996)

Layout Change Detection:
  If Wikipedia changes its HTML structure, parsing fails gracefully
  with WARNING logs rather than crashing the sync job.
"""

import io
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
import requests

from trading_signals.config import DATA_START_DATE
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (Trading-Signals IndexHistoryParser/1.0)"

# GitHub CSV backup for S&P 500 (fja05680/sp500)
GITHUB_SP500_CSV_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/"
    "master/S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)


@dataclass
class IndexChange:
    """A single index constituent change (addition or removal)."""

    change_date: date
    ticker: str
    index_name: str  # 'sp500', 'nasdaq100'
    action: str  # 'added', 'removed'
    reason: str | None = None
    replaced_by: str | None = None
    source: str = "wikipedia"


@dataclass
class ParseResult:
    """Result of parsing index changes from a source."""

    changes: list[IndexChange] = field(default_factory=list)
    source: str = ""
    warnings: list[str] = field(default_factory=list)


class WikipediaIndexHistoryParser:
    """Parse historical index changes from Wikipedia."""

    SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    NASDAQ100_CHANGES_URL = (
        "https://en.wikipedia.org/wiki/"
        "Historical_components_of_the_Nasdaq-100"
    )

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def parse_sp500_changes(self) -> ParseResult:
        """Parse S&P 500 historical changes from Wikipedia.

        Strategy:
        1. Try to find the 'Selected changes' table (tables[1+])
        2. If not found (layout changed), fall back to extracting
           'Date added' from the main constituents table (table[0])

        Returns:
            ParseResult with list of IndexChange objects.
        """
        result = ParseResult(source="wikipedia_sp500")

        try:
            resp = self._session.get(self.SP500_URL, timeout=15)
            resp.raise_for_status()
            tables = pd.read_html(io.StringIO(resp.text))
        except Exception as e:
            msg = f"Failed to fetch/parse S&P 500 Wikipedia page: {e}"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            return result

        # Strategy 1: Find the dedicated changes table
        changes_df = self._find_changes_table(tables, "sp500")
        if changes_df is not None:
            result.changes = self._parse_sp500_changes_df(changes_df)
            logger.info(
                f"[index_history] Parsed {len(result.changes)} S&P 500 changes "
                f"from Wikipedia changes table"
            )
            return result

        # Strategy 2: Fall back to 'Date added' column in main table
        logger.info(
            "[index_history] S&P 500 changes table not found. "
            "Falling back to 'Date added' column from constituents table."
        )
        result.changes = self._parse_sp500_date_added(tables)
        if result.changes:
            logger.info(
                f"[index_history] Extracted {len(result.changes)} S&P 500 additions "
                f"from 'Date added' column (no removal data available)"
            )
        else:
            msg = (
                "S&P 500: Neither changes table nor 'Date added' column found. "
                f"Layout may have changed. Tables: "
                + str([list(t.columns)[:5] for t in tables[:5]])
            )
            logger.warning(f"[index_history] LAYOUT CHANGE DETECTED: {msg}")
            result.warnings.append(msg)

        return result

    def _parse_sp500_date_added(
        self, tables: list[pd.DataFrame]
    ) -> list[IndexChange]:
        """Extract additions from the 'Date added' column in the main table.

        When the changes table is unavailable, we can still infer when
        each current member was added to the index. This gives us
        'added' events but no 'removed' events.
        """
        changes: list[IndexChange] = []
        data_start = DATA_START_DATE

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
                    ticker = str(row[symbol_col]).strip().upper()
                    date_str = str(row[date_col]).strip()
                    added_date = self._parse_date(date_str)

                    if not ticker or ticker == "NAN" or not added_date:
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

    def parse_nasdaq100_changes(self) -> ParseResult:
        """Parse Nasdaq 100 historical changes from Wikipedia.

        The 'Historical components of the Nasdaq-100' page has yearly
        tables with additions and removals.

        Returns:
            ParseResult with list of IndexChange objects.
        """
        result = ParseResult(source="wikipedia_nasdaq100")

        try:
            resp = self._session.get(self.NASDAQ100_CHANGES_URL, timeout=15)
            resp.raise_for_status()
            tables = pd.read_html(io.StringIO(resp.text))
        except Exception as e:
            msg = f"Failed to fetch/parse Nasdaq 100 Wikipedia page: {e}"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            return result

        if not tables:
            msg = (
                "No tables found on Nasdaq 100 historical changes page. "
                "Layout may have changed."
            )
            logger.warning(f"[index_history] LAYOUT CHANGE DETECTED: {msg}")
            result.warnings.append(msg)
            return result

        # Parse Nasdaq 100 yearly change tables
        result.changes = self._parse_nasdaq100_tables(tables)
        logger.info(
            f"[index_history] Parsed {len(result.changes)} Nasdaq 100 changes "
            f"from Wikipedia"
        )
        return result

    def _find_changes_table(
        self, tables: list[pd.DataFrame], index_name: str
    ) -> pd.DataFrame | None:
        """Find the 'Selected changes' table among parsed tables.

        Uses heuristics to identify the correct table:
        - Must have BOTH 'Added' and 'Removed' column groups
        - Must NOT have a 'Symbol' column (that's the main constituents table)
        - Must have more than 10 rows
        - 'Date added' in the main table does NOT count as a changes table
        """
        for i, df in enumerate(tables):
            cols_lower = [str(c).lower() for c in df.columns]
            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                cols_lower = [
                    str(c).lower()
                    for c in df.columns.get_level_values(-1)
                ]

            # Skip the main constituents table (has 'Symbol' column)
            if "symbol" in cols_lower:
                continue

            has_date = any("date" in c for c in cols_lower)
            # Must have BOTH added and removed as separate column concepts
            has_added = any(
                c == "added" or c.startswith("added ") or c.endswith(" added")
                for c in cols_lower
            )
            has_removed = any(
                c == "removed" or c.startswith("removed ") or c.endswith(" removed")
                for c in cols_lower
            )

            if has_date and has_added and has_removed and len(df) > 10:
                logger.debug(
                    f"[index_history] Found {index_name} changes table "
                    f"at index {i} ({len(df)} rows)"
                )
                return df

        return None

    def _parse_sp500_changes_df(
        self, df: pd.DataFrame
    ) -> list[IndexChange]:
        """Parse S&P 500 changes DataFrame into IndexChange objects.

        Handles MultiIndex columns from Wikipedia's nested table headers.
        The S&P 500 changes table typically has structure:
          Date | Added (Ticker, Security) | Removed (Ticker, Security) | Reason
        """
        changes: list[IndexChange] = []
        data_start = DATA_START_DATE

        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            # Join multi-level column names
            df.columns = [
                "_".join(str(c).strip() for c in col if str(c).strip())
                for col in df.columns
            ]

        # Normalize column names for robust matching
        col_map = {}
        for col in df.columns:
            col_lower = str(col).lower()
            if "date" in col_lower and "date" not in col_map:
                col_map["date"] = col
            elif "added" in col_lower and "ticker" in col_lower:
                col_map["added_ticker"] = col
            elif "removed" in col_lower and "ticker" in col_lower:
                col_map["removed_ticker"] = col
            elif "added" in col_lower and "security" in col_lower:
                col_map["added_name"] = col
            elif "removed" in col_lower and "security" in col_lower:
                col_map["removed_name"] = col
            elif "reason" in col_lower:
                col_map["reason"] = col

        if "date" not in col_map:
            logger.warning(
                "[index_history] Could not find 'Date' column in S&P 500 "
                f"changes table. Columns: {list(df.columns)}"
            )
            return changes

        for _, row in df.iterrows():
            change_date = self._parse_date(str(row.get(col_map["date"], "")))
            if not change_date or change_date < data_start:
                continue

            reason = str(row.get(col_map.get("reason", ""), "")).strip()
            if reason in ("nan", ""):
                reason = None

            # Added ticker
            added = str(row.get(col_map.get("added_ticker", ""), "")).strip()
            if added and added != "nan":
                changes.append(IndexChange(
                    change_date=change_date,
                    ticker=added.upper(),
                    index_name="sp500",
                    action="added",
                    reason=reason,
                    source="wikipedia",
                ))

            # Removed ticker
            removed = str(row.get(col_map.get("removed_ticker", ""), "")).strip()
            if removed and removed != "nan":
                # Check if replaced by the added ticker
                replaced_by = added.upper() if (added and added != "nan") else None
                changes.append(IndexChange(
                    change_date=change_date,
                    ticker=removed.upper(),
                    index_name="sp500",
                    action="removed",
                    reason=reason,
                    replaced_by=replaced_by,
                    source="wikipedia",
                ))

        return changes

    def _parse_nasdaq100_tables(
        self, tables: list[pd.DataFrame]
    ) -> list[IndexChange]:
        """Parse Nasdaq 100 yearly change tables.

        The Wikipedia page has multiple tables, one per year/period.
        Each table typically has 'Added' and 'Removed' columns.
        """
        changes: list[IndexChange] = []
        data_start = DATA_START_DATE

        for df in tables:
            cols_lower = [str(c).lower() for c in df.columns]

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
        import re
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
    """Backup data source: fja05680/sp500 GitHub CSV.

    Contains S&P 500 historical components since 1996 as a CSV file
    with columns representing dates and values being comma-separated
    ticker lists for that date.

    This is used as a fallback/validation source when Wikipedia
    parsing fails or returns incomplete data.
    """

    def fetch_changes(self) -> ParseResult:
        """Fetch and parse S&P 500 changes from the GitHub CSV.

        The CSV has format:
          date | tickers (comma-separated list of all constituents)

        We diff consecutive dates to extract additions and removals.

        Returns:
            ParseResult with extracted changes.
        """
        result = ParseResult(source="github_fja05680")

        try:
            resp = requests.get(
                GITHUB_SP500_CSV_URL,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as e:
            msg = f"Failed to fetch GitHub S&P 500 CSV: {e}"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            return result

        if df.empty or len(df.columns) < 2:
            msg = "GitHub S&P 500 CSV has unexpected format"
            logger.warning(f"[index_history] {msg}")
            result.warnings.append(msg)
            return result

        # The CSV has date as first column and ticker lists as values
        # Parse consecutive snapshots to find additions/removals
        data_start = DATA_START_DATE
        prev_tickers: set[str] | None = None
        prev_date: date | None = None

        for _, row in df.iterrows():
            try:
                row_date = pd.to_datetime(row.iloc[0]).date()
            except Exception:
                continue

            if row_date < data_start:
                # Still track for diffing, but skip creating changes
                tickers_str = str(row.iloc[1]) if len(row) > 1 else ""
                prev_tickers = set(
                    t.strip() for t in tickers_str.split(",") if t.strip()
                )
                prev_date = row_date
                continue

            # Current constituents from this row
            tickers_str = str(row.iloc[1]) if len(row) > 1 else ""
            current_tickers = set(
                t.strip() for t in tickers_str.split(",") if t.strip()
            )

            if prev_tickers is not None:
                # Find additions and removals
                added = current_tickers - prev_tickers
                removed = prev_tickers - current_tickers

                for ticker in added:
                    result.changes.append(IndexChange(
                        change_date=row_date,
                        ticker=ticker.upper(),
                        index_name="sp500",
                        action="added",
                        source="github_csv",
                    ))

                for ticker in removed:
                    result.changes.append(IndexChange(
                        change_date=row_date,
                        ticker=ticker.upper(),
                        index_name="sp500",
                        action="removed",
                        source="github_csv",
                    ))

            prev_tickers = current_tickers
            prev_date = row_date

        logger.info(
            f"[index_history] Parsed {len(result.changes)} S&P 500 changes "
            f"from GitHub CSV backup"
        )
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
        f"({sum(1 for iv in closed_intervals if iv['valid_to'] is None)} currently active)"
    )
    return closed_intervals
