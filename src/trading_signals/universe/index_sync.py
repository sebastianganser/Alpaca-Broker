"""Index Syncer – synchronize S&P 500 / Nasdaq 100 membership.

Downloads current index constituent lists from Wikipedia and updates
the universe table with index_membership info. New tickers are validated
against Alpaca before being added.

Sources:
  - S&P 500: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  - Nasdaq 100: https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies
  - Effective dates of the changes: "Historical components of the S&P 500" /
    "...of the Nasdaq-100" (``wikipedia_index_history``)
  - Ticker renames: ``ticker_aliases`` and Alpaca ``name_change`` corporate
    actions (a rename re-keys the open interval, it is not an exit)

Interval maintenance (``_update_membership_intervals``): a member that
disappears while its new symbol appears is a rename -> the open interval is
re-keyed. Otherwise additions/removals open/close intervals at the effective
date from the changes table; only if a change is not in the table the sync
date is used (WARNING per ticker).
"""

import io
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trading_signals.db.models.universe import Universe
from trading_signals.universe.alpaca_validator import AlpacaAssetValidator
from trading_signals.universe.corporate_actions import AlpacaCorporateActions
from trading_signals.universe.manager import UniverseManager
from trading_signals.universe.ticker_aliases import DEFAULT_ALIASES
from trading_signals.universe.wikipedia_index_history import (
    SP500_CURRENT_URL,
    WikipediaIndexHistoryParser,
    fetch_html,
)
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

NASDAQ100_CURRENT_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"

#: How far back Alpaca ``name_change`` actions are searched for a disappeared
#: member (covers a monthly job that was down for a while).
RENAME_LOOKBACK_DAYS = 400

#: ``index_membership.reason`` column length.
_REASON_MAX = 200

#: (index_name, ticker, 'added' | 'removed') -> effective date from the changes table
EffectiveDates = dict[tuple[str, str, str], date]

#: Plausible constituent counts. A Wikipedia layout change that yields an
#: empty/partial table must never close all membership intervals (H2).
EXPECTED_COUNTS: dict[str, tuple[int, int]] = {
    "sp500": (480, 520),
    "nasdaq100": (95, 110),
}


class IndexSyncSanityError(ValueError):
    """Fetched index list has an implausible size – sync aborted."""


@dataclass
class SyncResult:
    """Result of an index sync operation."""

    sp500_count: int = 0
    nasdaq100_count: int = 0
    already_existed: int = 0
    newly_added: int = 0
    not_tradeable: int = 0
    membership_updated: int = 0
    membership_cleared: int = 0
    new_tickers: list[str] = field(default_factory=list)
    #: "OLD->NEW" renames whose open interval was re-keyed (no exit)
    renamed: list[str] = field(default_factory=list)
    #: "index TICKER added|removed" dated with the sync date (not in the table)
    date_fallbacks: list[str] = field(default_factory=list)


def check_index_sizes(lists: dict[str, set[str]]) -> None:
    """Raise :class:`IndexSyncSanityError` if any list size is implausible."""
    problems = []
    for index_name, tickers in lists.items():
        lo, hi = EXPECTED_COUNTS[index_name]
        if not lo <= len(tickers) <= hi:
            problems.append(f"{index_name}={len(tickers)} (expected {lo}-{hi})")
    if problems:
        raise IndexSyncSanityError(
            "[index_sync] Implausible index sizes, aborting before any update: "
            + ", ".join(problems)
        )


class IndexSyncer:
    """Synchronize index constituent lists with the universe."""

    def __init__(
        self,
        session: Session,
        history_parser: WikipediaIndexHistoryParser | None = None,
        corporate_actions: AlpacaCorporateActions | None = None,
    ) -> None:
        self.session = session
        self._manager = UniverseManager(session)
        self._history_parser = history_parser
        self._corporate_actions = corporate_actions
        self._effective_dates_cache: EffectiveDates | None = None

    def sync(self, dry_run: bool = False, sanity_check: bool = True) -> SyncResult:
        """Fetch index lists and sync with universe.

        Args:
            dry_run: If True, only report what would change.
            sanity_check: Abort (raise) if a list size is implausible.

        Returns:
            SyncResult with details.
        """
        result = SyncResult()

        # Fetch index lists
        sp500 = self._fetch_sp500()
        nasdaq100 = self._fetch_nasdaq100()
        result.sp500_count = len(sp500)
        result.nasdaq100_count = len(nasdaq100)

        logger.info(
            f"[index_sync] Fetched S&P 500: {len(sp500)}, "
            f"Nasdaq 100: {len(nasdaq100)}"
        )
        if sanity_check:
            check_index_sizes({"sp500": sp500, "nasdaq100": nasdaq100})

        # Build membership map: ticker -> set of indexes
        membership: dict[str, set[str]] = {}
        for ticker in sp500:
            membership.setdefault(ticker, set()).add("sp500")
        for ticker in nasdaq100:
            membership.setdefault(ticker, set()).add("nasdaq100")

        # Get existing universe (+ current membership arrays)
        existing_rows = self.session.execute(
            select(Universe.ticker, Universe.index_membership)
        ).all()
        existing = {row[0] for row in existing_rows}
        stale_members = [
            row[0] for row in existing_rows
            if len(row) > 1 and row[1] and row[0] not in membership
        ]

        # Validate new tickers against Alpaca
        new_tickers = set(membership.keys()) - existing
        tradeable_new: dict[str, dict] = {}

        if new_tickers:
            logger.info(
                f"[index_sync] {len(new_tickers)} new tickers to validate "
                f"against Alpaca..."
            )
            try:
                validator = AlpacaAssetValidator()
                assets = validator.fetch_all_assets()
                for ticker in new_tickers:
                    asset = assets.get(ticker)
                    if asset and asset.tradable:
                        tradeable_new[ticker] = {
                            "exchange": asset.exchange,
                            "name": asset.name,
                        }
                    else:
                        result.not_tradeable += 1
                        logger.debug(
                            f"[index_sync] Skipping {ticker}: "
                            f"not tradeable on Alpaca"
                        )
            except Exception as e:
                logger.warning(
                    f"[index_sync] Alpaca validation failed: {e}. "
                    f"Skipping new ticker additions."
                )
                tradeable_new = {}

        result.newly_added = len(tradeable_new)
        result.already_existed = len(membership) - len(new_tickers)
        result.new_tickers = sorted(tradeable_new.keys())

        if dry_run:
            logger.info(
                f"[index_sync] DRY RUN: would add {len(tradeable_new)} "
                f"new tickers and update {len(membership)} memberships"
            )
            return result

        # Add new tickers
        for ticker, info in tradeable_new.items():
            indexes = sorted(membership[ticker])
            added_by = indexes[0]  # Primary index
            self._manager.add_ticker(
                ticker=ticker,
                company_name=info.get("name"),
                exchange=info.get("exchange"),
                added_by=added_by,
            )
            # Set index_membership
            self.session.execute(
                update(Universe)
                .where(Universe.ticker == ticker)
                .values(index_membership=indexes)
            )

        # Update index_membership for existing tickers
        for ticker, indexes in membership.items():
            if ticker in existing:
                sorted_indexes = sorted(indexes)
                self.session.execute(
                    update(Universe)
                    .where(Universe.ticker == ticker)
                    .values(index_membership=sorted_indexes)
                )
                result.membership_updated += 1

        # Clear membership for tickers that left all tracked indexes
        # (they stay in the universe, but must not count as members).
        for ticker in stale_members:
            self.session.execute(
                update(Universe)
                .where(Universe.ticker == ticker)
                .values(index_membership=None)
            )
        result.membership_cleared = len(stale_members)

        self.session.flush()
        logger.info(
            f"[index_sync] Done: {result.newly_added} added, "
            f"{result.membership_updated} memberships updated, "
            f"{result.membership_cleared} cleared"
        )

        # ── Maintain index_membership intervals ──────────────────────
        # Compare current index composition against open intervals
        # to detect additions and removals for point-in-time tracking.
        self._update_membership_intervals(membership, result)

        return result

    # ── Membership intervals ────────────────────────────────────────

    def _update_membership_intervals(
        self,
        current_membership: dict[str, set[str]],
        result: SyncResult | None = None,
    ) -> None:
        """Maintain index_membership intervals based on detected changes.

        Compares current index composition (from Wikipedia) against
        open intervals in the index_membership table.

        * A disappeared member whose new symbol appeared (known alias or
          Alpaca ``name_change``) is a rename: the open interval is re-keyed.
        * Other additions open an interval, other removals close one, both at
          the effective date of the changes table (sync date + WARNING if the
          change is not in the table or the date is implausible).
        """
        from sqlalchemy import and_

        from trading_signals.db.models.index_membership import IndexMembership

        result = result if result is not None else SyncResult()
        today = date.today()

        for index_name in ("sp500", "nasdaq100"):
            # The membership dict is keyed by ticker, values are sets of indexes
            current_for_index = set()
            for ticker, indexes in current_membership.items():
                if index_name in indexes:
                    current_for_index.add(ticker)

            # Get all currently-open intervals for this index
            open_intervals = self.session.execute(
                select(
                    IndexMembership.ticker,
                    IndexMembership.valid_from,
                    IndexMembership.reason,
                ).where(
                    and_(
                        IndexMembership.index_name == index_name,
                        IndexMembership.valid_to.is_(None),
                    )
                )
            ).all()
            open_info = {r[0]: (r[1], r[2]) for r in open_intervals}
            previously_active = set(open_info)

            additions = current_for_index - previously_active
            removals = previously_active - current_for_index

            # Renames first: re-key instead of close + open
            renames = self._detect_renames(removals, additions, today)
            for old, (new, effective) in sorted(renames.items()):
                valid_from, old_reason = open_info[old]
                self._rekey_interval(
                    index_name, old, new, effective, valid_from, old_reason
                )
                result.renamed.append(f"{old}->{new}")
                logger.info(
                    f"[index_sync] {index_name}: {old} renamed to {new}"
                    + (f" ({effective})" if effective else "")
                    + " -- open interval re-keyed, no exit"
                )
            additions -= {new for new, _ in renames.values()}
            removals -= set(renames)

            effective_dates: EffectiveDates = (
                self._effective_dates() if (additions or removals) else {}
            )

            # Detect additions: in current but not in open intervals
            for ticker in sorted(additions):
                valid_from = self._resolve_date(
                    effective_dates, index_name, ticker, "added", today, None, result
                )
                self.session.add(IndexMembership(
                    ticker=ticker,
                    index_name=index_name,
                    valid_from=valid_from,
                    valid_to=None,
                    reason="Monthly index sync detected addition",
                    source="index_sync",
                ))

            # Detect removals: in open intervals but not in current
            for ticker in sorted(removals):
                valid_to = self._resolve_date(
                    effective_dates,
                    index_name,
                    ticker,
                    "removed",
                    today,
                    open_info[ticker][0],
                    result,
                )
                # Close the open interval
                self.session.execute(
                    update(IndexMembership)
                    .where(
                        and_(
                            IndexMembership.ticker == ticker,
                            IndexMembership.index_name == index_name,
                            IndexMembership.valid_to.is_(None),
                        )
                    )
                    .values(
                        valid_to=valid_to,
                        reason="Monthly index sync detected removal",
                    )
                )

            if additions or removals or renames:
                self.session.flush()
                logger.info(
                    f"[index_sync] {index_name}: "
                    f"{len(additions)} new intervals, "
                    f"{len(removals)} intervals closed, "
                    f"{len(renames)} intervals re-keyed (rename)"
                )

    def _resolve_date(
        self,
        effective_dates: EffectiveDates,
        index_name: str,
        ticker: str,
        action: str,
        today: date,
        not_after: date | None,
        result: SyncResult,
    ) -> date:
        """Effective date of a change, or ``today`` (WARNING) if unusable.

        Unusable: not in the changes table, in the future, or (for removals)
        not after the interval's ``valid_from`` (``not_after``).
        """
        found = effective_dates.get((index_name, ticker, action))
        if found is None:
            problem = "not in the changes table"
        elif found > today:
            problem = f"effective date {found} is in the future"
        elif not_after is not None and found <= not_after:
            problem = f"effective date {found} is not after valid_from {not_after}"
        else:
            return found
        logger.warning(
            f"[index_sync] {index_name} {ticker} {action}: {problem} -- "
            f"using sync date {today}"
        )
        result.date_fallbacks.append(f"{index_name} {ticker} {action}")
        return today

    def _rekey_interval(
        self,
        index_name: str,
        old: str,
        new: str,
        effective: date | None,
        valid_from: date,
        old_reason: str | None,
    ) -> None:
        """Move the open interval of ``old`` to the ticker ``new``."""
        from sqlalchemy import and_

        from trading_signals.db.models.index_membership import IndexMembership

        note = f"ticker renamed {old}->{new}" + (f" {effective}" if effective else "")
        reason = f"{old_reason}; {note}" if old_reason else note
        self.session.execute(
            update(IndexMembership)
            .where(
                and_(
                    IndexMembership.ticker == old,
                    IndexMembership.index_name == index_name,
                    IndexMembership.valid_to.is_(None),
                    IndexMembership.valid_from == valid_from,
                )
            )
            .values(ticker=new, reason=reason[:_REASON_MAX])
        )

    def _detect_renames(
        self, removals: set[str], additions: set[str], today: date
    ) -> dict[str, tuple[str, date | None]]:
        """Pair disappeared members with their new symbol.

        1. ``ticker_aliases``: ``current_symbol(old)`` is in ``additions``.
        2. Alpaca ``name_change`` actions for the still unpaired removals.

        Returns ``{old: (new, effective_date_or_None)}``.
        """
        renames: dict[str, tuple[str, date | None]] = {}
        if not removals or not additions:
            return renames

        for old in sorted(removals):
            new = DEFAULT_ALIASES.current_symbol(old)
            if new != old and new in additions:
                effective = next(
                    (
                        a.effective_date
                        for a in DEFAULT_ALIASES.aliases
                        if a.old == old and a.new == new
                    ),
                    None,
                )
                renames[old] = (new, effective)

        unpaired = removals - set(renames)
        open_new = additions - {new for new, _ in renames.values()}
        if unpaired and open_new:
            try:
                client = self._corporate_actions or AlpacaCorporateActions()
                events = client.fetch(
                    unpaired,
                    start=today - timedelta(days=RENAME_LOOKBACK_DAYS),
                    end=today,
                    include_mergers=False,
                )
            except Exception as e:
                logger.warning(
                    f"[index_sync] Alpaca name_change lookup failed: "
                    f"{type(e).__name__}: {e} -- unpaired removals are closed"
                )
                return renames
            for old in sorted(unpaired):
                for change in events.renames_of(old):
                    new = change.new_symbol.upper()
                    if new in open_new:
                        renames[old] = (new, change.process_date)
                        open_new.discard(new)
                        break
        return renames

    def _effective_dates(self) -> EffectiveDates:
        """Newest ``added``/``removed`` date per (index, ticker) from Wikipedia.

        Fetched once per sync and only when there is something to date. A
        failing fetch is not fatal: callers fall back to the sync date.
        """
        if self._effective_dates_cache is not None:
            return self._effective_dates_cache
        dates: EffectiveDates = {}
        parser = self._history_parser or WikipediaIndexHistoryParser()
        for label, parse in (
            ("sp500", parser.parse_sp500_changes),
            ("nasdaq100", parser.parse_nasdaq100_changes),
        ):
            try:
                changes = parse().changes
            except Exception as e:
                logger.warning(
                    f"[index_sync] {label} changes table unavailable: "
                    f"{type(e).__name__}: {e}"
                )
                continue
            for c in changes:
                key = (c.index_name, c.ticker, c.action)
                if key not in dates or c.change_date > dates[key]:
                    dates[key] = c.change_date
        self._effective_dates_cache = dates
        return dates

    # ── Wikipedia fetchers ──────────────────────────────────────────

    def _fetch_sp500(self) -> set[str]:
        """Fetch S&P 500 tickers from Wikipedia."""
        tables = pd.read_html(io.StringIO(fetch_html(SP500_CURRENT_URL)))
        df = tables[0]
        tickers = set(df["Symbol"].str.strip().tolist())
        return tickers

    def _fetch_nasdaq100(self) -> set[str]:
        """Fetch Nasdaq 100 tickers from Wikipedia.

        Uses the dedicated list page (same pattern as S&P 500).
        Wikipedia moved the components table from the main Nasdaq-100
        article to this separate page around July/August 2026.
        """
        tables = pd.read_html(io.StringIO(fetch_html(NASDAQ100_CURRENT_URL)))
        for t in tables:
            if "Ticker" in t.columns and len(t) > 90:
                return set(t["Ticker"].dropna().str.strip().tolist())
        logger.warning("[index_sync] Nasdaq 100 table not found on Wikipedia")
        return set()
