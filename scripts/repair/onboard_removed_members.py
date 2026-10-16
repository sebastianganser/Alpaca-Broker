"""Onboard removed S&P 500 members: universe rows, prices, indicators (repair step 2b).

Run AFTER ``rebuild_sp500_membership.py --apply``. It reads the ``sp500``
intervals from the DB (they are the ground truth after step 2a) and, for every
ticker with an interval inside the retention window:

1. **Universe** -- a ticker missing in ``signals.universe`` gets an inactive row
   (``is_active=False``, ``added_by='sp500_history'``, company name from the
   Wikipedia change table or the Alpaca asset). Current members that are missing
   (e.g. TWLO, VYLR, SKYD) are only listed: they must be ACTIVE so that the
   collectors pick them up -- the monthly ``index_sync`` adds them, or pass
   ``--add-current-members`` to insert them as active rows here.
2. **Prices** -- tickers whose real (non-extrapolated) ``prices_daily`` rows do not
   cover the membership sessions inside the window are downloaded from Alpaca
   with the collector's parameters (``adjustment=all``, feed from
   ``ALPACA_DATA_FEED``, normally ``sip``; no silent IEX fallback) for
   ``[data_start_date(), last membership day]``. The ``asof`` parameter makes a
   re-used symbol resolve to the company that held it (INFO = IHS Markit, not
   today's ETF). Default ``--asof first`` = first member day of the request: the
   company certainly traded then. The literal "last membership day" (``--asof
   last``) is WRONG whenever the index removal comes after the delisting (INFO:
   IHS Markit's last bar is 2022-02-25, the S&P removal 2022-03-02;
   ``asof=2022-03-01`` returns 2 bars of the Harbor ETF that took the symbol,
   ``asof=2021-10-01`` the clean 102 bars; verified against Alpaca 2026-10-16).
   For a renamed ticker each symbol of ``symbol_history()`` is requested for its own
   period (CDAY before 2024-02-01, DAY after) and everything is stored under the
   CURRENT symbol. Rows are written like the collector does (upsert) with
   ``source='alpaca_backfill'``, but only rows that are new or differ from what is
   stored, so a second run changes nothing. Per ticker the report shows sessions
   expected / stored / fetched / written; unresolved gaps (head, internal, tail)
   are listed, never silently ignored.
3. **Technical indicators** -- ``TechnicalIndicatorsComputer.recompute_ticker`` for
   exactly the tickers that received price rows (plus onboarded tickers that still
   have no indicator rows).

``--fix-para``: ``universe.PARA`` today is Banzai International (BNZI took the
symbol on 2026-08-07) and every stored PARA price row is Banzai's series (closes
> 1,000). The option makes PARA the Paramount Global entity again: universe row
(inactive, company name, Banzai's sector/CUSIP cleared), PARA rows in
``prices_daily`` / ``technical_indicators`` with ``trade_date >= 2025-08-08`` and
every earlier row that is not a Paramount bar are deleted (CSV backup first),
Paramount bars are refetched (VIAC for the days before 2022-02-17, PARA for
2022-02-17..2025-08-07; ``asof=2025-08-07`` and ``asof=2022-02-17`` return the
same 869 bars), indicators recomputed. Rows of PARA in all other
tables are only counted and printed, never deleted.

Dry run (default) = read-only DB session (``SET TRANSACTION READ ONLY``) plus the
read-only Alpaca/Wikipedia GETs needed for the report (``--no-fetch`` skips all
network access). ``--apply`` writes one transaction per logical step: universe
rows, PARA fix, price backfill, indicators; each step is idempotent, so an
interrupted run can simply be repeated.

Usage (inside the container; ``alpaca-ops run`` refuses 22:15-05:15)::

    PY=.venv/bin/python
    $PY scripts/repair/onboard_removed_members.py                      # dry run
    OB=scripts/repair/onboard_removed_members.py
    $PY $OB --apply --fix-para --add-current-members
    $PY $OB --tickers DAY,INFO --apply

Expected effect on the production DB (derived from the 2026-10-16 inventory and a
read-only dry run against the live Alpaca data on a throwaway DB; the dry run on
production is authoritative):

* universe: INSERT 68 inactive rows (the exits that are absent today); with
  ``--add-current-members`` also the current members that are missing (SKYD,
  TWLO, VYLR if not yet there) as ACTIVE rows;
* prices: ~72 tickers in the table (68 absent + ANSS, ATVI, AVB, XLNX which miss
  1-2 tail sessions; their ``write`` is 0 unless the stored rows came from the old
  IEX backfill), about 39,130 bars to write for the 68 (inventory 38,916 + 214
  warm-up bars before the membership start of MOH, SBNY, SEDG) and 965 for PARA
  (VIAC 96 + PARA 869) = ~40,100 rows, +4 % of ``prices_daily``;
* no head or internal gaps; tail gaps (delisted before the index removal) for
  CTLT, CTXS, DAY (3), FRC, HES, HOLX, INFO (2), JNPR, MRO, PXD, SIVB, TWTR;
* PARA: 300 price rows (299 from 2025-08-08 + the foreign 2025-08-07) and the TA
  rows of those days deleted, 965 Paramount rows written, universe row becomes
  "Paramount Global (Class B)", inactive; rows in other tables are only counted;
* technical indicators for ~70 tickers (every ticker with written rows + PARA).

Verify: dry run again (every ticker ``write 0``, PARA ``already fixed``, 0 tickers
for TA), and ``select count(*), count(distinct ticker) from signals.prices_daily
where source = 'alpaca_backfill'``; ``select * from signals.universe where ticker
= 'PARA'``; no PARA price/indicator row from 2025-08-08 on.

Rollback: the onboarded rows are new (``universe.added_by='sp500_history'``,
``prices_daily.source='alpaca_backfill'``) and can be deleted by those markers
(their indicator rows by ticker); the deleted PARA rows and the old PARA universe
row are saved as CSV in ``--backup-dir`` (default
/mnt/user/Workfiles/AlpacaBroker/ops).
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests

from trading_signals.collectors.prices_alpaca import (
    BATCH_SIZE,
    DATA_BASE_URL,
    bars_to_rows,
    upsert_price_rows,
)
from trading_signals.universe.ticker_aliases import (
    DEFAULT_ALIASES,
    AliasMap,
)
from trading_signals.universe.wikipedia_index_history import (
    SP500_CHANGES_URL,
    WikipediaIndexHistoryParser,
    clean_ticker,
    fetch_html,
)
from trading_signals.utils.logging import get_logger, setup_logging
from trading_signals.utils.repair_support import (
    BackupError,
    announce_db_target,
    out,
    repair_session,
    write_csv_backup,
)
from trading_signals.utils.retry import retry

logger = get_logger("repair.onboard_removed_members")

INDEX_NAME = "sp500"
#: ``prices_daily.source`` of rows written by this script.
SOURCE = "alpaca_backfill"
#: ``universe.added_by`` of rows inserted by this script.
ADDED_BY = "sp500_history"
DEFAULT_BACKUP_DIR = "/mnt/user/Workfiles/AlpacaBroker/ops"

PARA = "PARA"
#: First day PARA is no longer an S&P 500 member (Paramount Global merged into
#: Skydance on 2025-08-07); rows of Paramount Global end before this date.
PARA_EXIT_DATE = date(2025, 8, 8)
PARA_COMPANY_NAME = "Paramount Global (Class B)"
#: The refetched Paramount series is rejected if it still looks like Banzai.
PARA_REFERENCE_MAX_CLOSE = 500.0
PARA_REFERENCE_MIN_BARS = 800
#: Stored close vs. refetched Paramount close: relative difference that flags a row.
PARA_MISMATCH_TOLERANCE = 0.05
#: Consecutive stored closes that jump by more than this factor are a discontinuity.
PARA_JUMP_RATIO = 3.0
PARA_UNIVERSE_NOTE = (
    "Paramount Global (Class B), ticker VIAC until 2022-02-17. The row held Banzai "
    "International (BNZI) from 2026-08-07 until the 2026-10 survivorship repair."
)

#: Fewest price rows from which indicators exist (= MIN_DAYS_RSI of the TA computer).
MIN_TA_ROWS = 14

#: Price equality tolerance (columns are NUMERIC(16,4)).
PRICE_TOLERANCE = 5e-5
#: Pause between Alpaca requests (free tier: 200 requests/minute).
REQUEST_PAUSE_SECONDS = 0.3
#: Below this many closed sp500 intervals in the window the rebuild was not run yet.
MIN_CLOSED_INTERVALS = 20

#: Tables that are not part of the "other tables" count of the PARA fix.
_PARA_OWN_TABLES = {"prices_daily", "technical_indicators", "universe"}
#: Column names tried (in this order) to date the rows of a ticker table.
_DATE_COLUMNS = (
    "trade_date",
    "snapshot_date",
    "filing_date",
    "disclosure_date",
    "transaction_date",
    "rating_date",
    "delta_date",
    "published_at",
    "settlement_date",
    "as_of",
    "earnings_date",
    "cluster_start",
    "decision_date",
    "created_at",
)


class AbortError(RuntimeError):
    """A precondition failed; nothing (more) is written."""


class FeedError(AbortError):
    """Alpaca refused the requested feed / credentials."""


# ── Calendar and membership ranges ───────────────────────────────────────

Intervals = dict[str, list[tuple[date, date | None]]]


class SessionCalendar:
    """NYSE sessions in ``[start, end]`` with cheap range slices."""

    def __init__(self, start: date, end: date) -> None:
        from trading_signals.utils.market_calendar import trading_days

        self.days = trading_days(start, end)

    def between(self, start: date, end: date) -> list[date]:
        i = bisect_left(self.days, start)
        j = bisect_right(self.days, end)
        return self.days[i:j]


def member_range(
    interval: tuple[date, date | None], ds: date, last_session: date
) -> tuple[date, date] | None:
    """First and last member day of an interval inside ``[ds, last_session]``.

    ``valid_to`` is the first day that is NOT a member, so the last member day
    is the day before it. ``None`` if nothing is left.
    """
    valid_from, valid_to = interval
    start = max(valid_from, ds)
    end = (
        last_session
        if valid_to is None
        else min(valid_to - timedelta(days=1), last_session)
    )
    return (start, end) if end >= start else None


def window_intervals(intervals: Intervals, ds: date) -> Intervals:
    """Only the tickers that have an interval reaching into the window."""
    result: Intervals = {}
    for ticker, ivs in intervals.items():
        kept = [iv for iv in ivs if iv[1] is None or iv[1] > ds]
        if kept:
            result[ticker] = sorted(kept, key=lambda iv: iv[0])
    return result


def expected_sessions(
    ivs: Iterable[tuple[date, date | None]],
    cal: SessionCalendar,
    ds: date,
    last_session: date,
) -> list[date]:
    """NYSE sessions that lie inside a membership interval and the window."""
    days: set[date] = set()
    for iv in ivs:
        rng = member_range(iv, ds, last_session)
        if rng:
            days.update(cal.between(*rng))
    return sorted(days)


def need_range(
    ivs: Sequence[tuple[date, date | None]], ds: date, last_session: date
) -> tuple[date, date, date] | None:
    """``(data_start_date(), last member day, first member day)`` of a ticker."""
    ranges = [r for r in (member_range(iv, ds, last_session) for iv in ivs) if r]
    if not ranges:
        return None
    return (ds, max(r[1] for r in ranges), min(r[0] for r in ranges))


# ── Fetch plan ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FetchTask:
    """One Alpaca request for one symbol; the rows are stored under ``store_ticker``."""

    store_ticker: str
    symbol: str
    start: date
    end: date
    asof: date

    @property
    def key(self) -> tuple[date, date, date]:
        return (self.start, self.end, self.asof)


def task_asof(start: date, end: date, member_start: date | None, mode: str) -> date:
    """``asof`` of one request.

    ``last`` = last day of the request (the literal "last membership day"
    rule). ``first`` (default) = first member day inside the request, clamped to
    the request: a day on which the company certainly traded under the symbol.
    The difference matters when the index removal comes after the delisting: INFO
    was removed from the S&P 500 on 2022-03-02 but IHS Markit traded until
    2022-02-25, so ``asof=2022-03-01`` resolves INFO to the Harbor ETF that
    re-used the symbol (2 foreign bars) while ``asof=2021-10-01`` returns
    exactly the 102 IHS Markit bars (verified against Alpaca 2026-10-16).
    """
    if mode == "last":
        return end
    first_member_day = max(start, member_start) if member_start else start
    return min(first_member_day, end)


def build_fetch_tasks(
    store_ticker: str,
    need_from: date,
    need_to: date,
    member_start: date | None = None,
    asof_mode: str = "first",
    alias_map: AliasMap = DEFAULT_ALIASES,
) -> list[FetchTask]:
    """Requests that cover ``[need_from, need_to]`` for the company of ``store_ticker``.

    Each symbol the company traded under (``symbol_history``) is requested for
    its own period; ``asof`` (see :func:`task_asof`) makes Alpaca resolve the
    symbol to the company that held it then.
    """
    tasks: list[FetchTask] = []
    for symbol, first_day, next_symbol_day in alias_map.symbol_history(store_ticker):
        start = max(need_from, first_day) if first_day else need_from
        end = (
            min(need_to, next_symbol_day - timedelta(days=1))
            if next_symbol_day
            else need_to
        )
        if start <= end:
            asof = task_asof(start, end, member_start, asof_mode)
            tasks.append(FetchTask(store_ticker, symbol, start, end, asof=asof))
    return tasks


def group_tasks(
    tasks: Iterable[FetchTask], size: int = BATCH_SIZE
) -> list[list[FetchTask]]:
    """Tasks that share ``(start, end, asof)`` go into one multi-symbol request."""
    by_key: dict[tuple, list[FetchTask]] = defaultdict(list)
    for t in tasks:
        by_key[t.key].append(t)
    groups: list[list[FetchTask]] = []
    for key in sorted(by_key):
        members = sorted(by_key[key], key=lambda t: (t.symbol, t.store_ticker))
        for i in range(0, len(members), size):
            groups.append(members[i : i + size])
    return groups


@retry(max_attempts=3, base_delay=2.0)
def _get_page(params: dict, headers: dict) -> dict:
    response = requests.get(
        f"{DATA_BASE_URL}/v2/stocks/bars", headers=headers, params=params, timeout=60
    )
    response.raise_for_status()
    return response.json()


def fetch_bars(
    symbols: list[str],
    start: date,
    end: date,
    asof: date,
    headers: dict[str, str],
    feed: str,
) -> dict[str, list[dict]]:
    """Daily bars of ``symbols`` for ``[start, end]`` (all pages).

    Same parameters as ``prices_alpaca._fetch_bars_batch`` (1Day,
    ``adjustment=all``, ``limit=10000``, ``feed``) plus ``asof``.
    """
    bars: dict[str, list[dict]] = {}
    token: str | None = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": f"{start.isoformat()}T00:00:00Z",
            "end": f"{end.isoformat()}T23:59:59Z",
            "limit": 10000,
            "adjustment": "all",
            "feed": feed,
            "asof": asof.isoformat(),
        }
        if token:
            params["page_token"] = token
        payload = _get_page(params, headers)
        for symbol, items in (payload.get("bars") or {}).items():
            bars.setdefault(symbol, []).extend(items)
        token = payload.get("next_page_token")
        if not token:
            return bars


def fetch_task_bars(
    tasks: Sequence[FetchTask],
    headers: dict[str, str],
    feed: str,
) -> tuple[dict[FetchTask, list[dict]], list[str]]:
    """Run all requests. Returns ``(bars per task, error messages)``."""
    result: dict[FetchTask, list[dict]] = {t: [] for t in tasks}
    errors: list[str] = []
    groups = group_tasks(tasks)
    for n, group in enumerate(groups, 1):
        start, end, asof = group[0].key
        symbols = sorted({t.symbol for t in group})
        logger.info(
            f"Alpaca bars {n}/{len(groups)}: {len(symbols)} symbols "
            f"{start}..{end} asof={asof}"
        )
        try:
            bars = fetch_bars(symbols, start, end, asof, headers, feed)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (403, 422):
                raise FeedError(
                    f"Alpaca refused the request (HTTP {status}) for feed={feed}: "
                    "subscription/credentials problem. This script does not fall "
                    "back to IEX silently; fix the access or pass --feed iex."
                ) from e
            errors.append(f"{symbols} {start}..{end} asof={asof}: HTTP {status}")
            continue
        except requests.exceptions.RequestException as e:
            errors.append(
                f"{symbols} {start}..{end} asof={asof}: {type(e).__name__}: {e}"
            )
            continue
        for t in group:
            result[t] = bars.get(t.symbol, [])
        if n < len(groups):
            time.sleep(REQUEST_PAUSE_SECONDS)
    return result, errors


def task_rows(task: FetchTask, bars: list[dict]) -> list[dict]:
    """``prices_daily`` rows of one task, stored under the current symbol."""
    rows = bars_to_rows(task.store_ticker, bars, max_date=task.end, min_date=task.start)
    for r in rows:
        r["source"] = SOURCE
    return rows


# ── Stored prices ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoredPrice:
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    is_extrapolated: bool


def _num(value) -> float | None:
    return None if value is None else float(value)


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= PRICE_TOLERANCE


def row_is_current(stored: StoredPrice | None, row: dict) -> bool:
    """True if ``row`` would not change the stored real row."""
    if stored is None or stored.is_extrapolated:
        return False
    return all(
        _same(getattr(stored, col), _num(row.get(col)))
        for col in ("open", "high", "low", "close")
    ) and (stored.volume or 0) == int(row.get("volume") or 0)


def rows_to_write(rows: Iterable[dict], stored: dict[date, StoredPrice]) -> list[dict]:
    """Rows that are new, replace a gap filler or differ from the stored value."""
    return [r for r in rows if not row_is_current(stored.get(r["trade_date"]), r)]


def classify_gaps(
    expected: Sequence[date], available: set[date]
) -> dict[str, list[date]]:
    """Expected sessions without a bar, split into head / internal / tail gaps.

    head = before the first available bar (listing later than the membership
    start), tail = after the last bar (delisted before the index removal),
    internal = a hole between bars (suspicious).
    """
    missing = [d for d in expected if d not in available]
    gaps: dict[str, list[date]] = {"head": [], "internal": [], "tail": []}
    if not missing:
        return gaps
    if not available:
        gaps["head"] = missing
        return gaps
    first, last = min(available), max(available)
    for d in missing:
        if d < first:
            gaps["head"].append(d)
        elif d > last:
            gaps["tail"].append(d)
        else:
            gaps["internal"].append(d)
    return gaps


# ── Plan ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NameInfo:
    name: str | None = None
    exchange: str | None = None


@dataclass
class UniverseInsert:
    ticker: str
    added_date: date
    active: bool
    name: str | None = None
    exchange: str | None = None


@dataclass
class TickerPlan:
    ticker: str
    expected: list[date]
    stored_real: set[date]
    tasks: list[FetchTask]
    fetched: dict[date, dict] = field(default_factory=dict)
    to_write: list[dict] = field(default_factory=list)
    gaps: dict[str, list[date]] = field(default_factory=dict)

    @property
    def missing(self) -> list[date]:
        return [d for d in self.expected if d not in self.stored_real]


def plan_universe_inserts(
    intervals: Intervals,
    universe: set[str],
    names: dict[str, NameInfo],
    add_current: bool = False,
) -> tuple[list[UniverseInsert], list[str]]:
    """Universe rows to insert and current members that are left to ``index_sync``.

    Closed-interval tickers become inactive rows. A ticker with an open interval is
    a current member: it is only inserted (ACTIVE) with ``add_current``.
    """
    inserts: list[UniverseInsert] = []
    skipped_current: list[str] = []
    for ticker in sorted(intervals):
        if ticker in universe:
            continue
        ivs = intervals[ticker]
        is_current = any(iv[1] is None for iv in ivs)
        if is_current and not add_current:
            skipped_current.append(ticker)
            continue
        info = names.get(ticker, NameInfo())
        inserts.append(
            UniverseInsert(
                ticker=ticker,
                added_date=min(iv[0] for iv in ivs),
                active=is_current,
                name=info.name,
                exchange=info.exchange,
            )
        )
    return inserts, skipped_current


def plan_prices(
    intervals: Intervals,
    stored_dates: dict[str, set[date]],
    cal: SessionCalendar,
    ds: date,
    last_session: date,
    skip: set[str] = frozenset(),
    asof_mode: str = "first",
    alias_map: AliasMap = DEFAULT_ALIASES,
) -> list[TickerPlan]:
    """Tickers whose membership sessions are not all covered by real price rows."""
    plans: list[TickerPlan] = []
    for ticker in sorted(intervals):
        if ticker in skip:
            continue
        ivs = intervals[ticker]
        expected = expected_sessions(ivs, cal, ds, last_session)
        stored = stored_dates.get(ticker, set())
        if not expected or all(d in stored for d in expected):
            continue
        rng = need_range(ivs, ds, last_session)
        assert rng is not None
        plans.append(
            TickerPlan(
                ticker=ticker,
                expected=expected,
                stored_real=stored,
                tasks=build_fetch_tasks(
                    ticker, rng[0], rng[1], rng[2], asof_mode, alias_map
                ),
            )
        )
    return plans


def apply_fetch(
    plan: TickerPlan,
    bars: dict[FetchTask, list[dict]],
    stored_rows: dict[date, StoredPrice],
) -> None:
    """Fill ``fetched``, ``to_write`` and ``gaps`` of one ticker from the bars."""
    rows: list[dict] = []
    for task in plan.tasks:
        rows.extend(task_rows(task, bars.get(task, [])))
    plan.fetched = {r["trade_date"]: r for r in rows}
    plan.to_write = rows_to_write(
        sorted(plan.fetched.values(), key=lambda r: r["trade_date"]), stored_rows
    )
    available = set(plan.stored_real) | set(plan.fetched)
    plan.gaps = classify_gaps(plan.expected, available)


# ── PARA fix ─────────────────────────────────────────────────────────────


@dataclass
class ParaState:
    universe: dict | None = None
    prices: dict[date, StoredPrice] = field(default_factory=dict)
    ta_dates: set[date] = field(default_factory=set)
    other_tables: list[tuple[str, int, int | None]] = field(default_factory=list)


@dataclass
class ParaFixPlan:
    exit_dates: list[date] = field(default_factory=list)
    foreign_dates: list[date] = field(default_factory=list)
    mismatched: list[tuple[date, float, float]] = field(default_factory=list)
    jumps: list[tuple[date, float, float]] = field(default_factory=list)
    delete_price_dates: list[date] = field(default_factory=list)
    delete_ta_dates: list[date] = field(default_factory=list)
    reference: dict[date, float] = field(default_factory=dict)
    rows_to_write: list[dict] = field(default_factory=list)
    reference_problem: str | None = None
    #: 'insert' (no PARA row), 'update' (row holds Banzai) or None (already fixed)
    universe_action: str | None = None
    ta_needed: bool = False

    @property
    def changes_anything(self) -> bool:
        return bool(
            self.universe_action
            or self.delete_price_dates
            or self.delete_ta_dates
            or self.rows_to_write
        )


def check_reference(reference: dict[date, float], ds: date) -> str | None:
    """Why the refetched Paramount series cannot be trusted (``None`` = fine)."""
    if len(reference) < PARA_REFERENCE_MIN_BARS:
        return (
            f"only {len(reference)} Paramount bars fetched "
            f"(need >= {PARA_REFERENCE_MIN_BARS})"
        )
    top = max(reference.values())
    if top > PARA_REFERENCE_MAX_CLOSE:
        return (
            f"fetched series has a close of {top:,.2f} -- that is not Paramount "
            "(asof did not resolve to the old entity?)"
        )
    if min(reference) > ds + timedelta(days=10):
        return f"fetched series starts {min(reference)}, expected about {ds}"
    return None


def plan_para_fix(
    state: ParaState,
    reference_rows: Sequence[dict] | None,
    ds: date,
    exit_date: date = PARA_EXIT_DATE,
) -> ParaFixPlan:
    """Which PARA rows are foreign and what replaces them (pure function).

    * rows with ``trade_date >= exit_date`` are always deleted (Paramount Global
      is gone, whatever trades under PARA afterwards is Banzai);
    * rows before it that have no bar in the refetched Paramount series are
      deleted as well (they cannot be Paramount);
    * rows that do have a Paramount bar are overwritten by it (``mismatched``
      lists those whose close differs by more than 5 %);
    * ``jumps`` lists price discontinuities of the stored series for the report.
    """
    plan = ParaFixPlan()
    dates = sorted(state.prices)
    plan.exit_dates = [d for d in dates if d >= exit_date]

    rows = list(reference_rows or [])
    plan.reference = {
        r["trade_date"]: float(r["close"]) for r in rows if r.get("close")
    }
    plan.reference_problem = (
        "no Paramount reference fetched (--no-fetch)"
        if reference_rows is None
        else check_reference(plan.reference, ds)
    )
    ref = plan.reference

    prev: tuple[date, float] | None = None
    for d in dates:
        close = state.prices[d].close
        if close is None:
            continue
        if prev and prev[1] > 0:
            ratio = close / prev[1]
            if ratio > PARA_JUMP_RATIO or ratio < 1 / PARA_JUMP_RATIO:
                plan.jumps.append((d, prev[1], close))
        prev = (d, close)
        if d in ref and ref[d] > 0:
            if abs(close - ref[d]) / ref[d] > PARA_MISMATCH_TOLERANCE:
                plan.mismatched.append((d, close, ref[d]))

    if not plan.reference_problem:
        plan.foreign_dates = [d for d in dates if d < exit_date and d not in ref]
    plan.delete_price_dates = sorted(set(plan.exit_dates) | set(plan.foreign_dates))
    plan.delete_ta_dates = sorted(
        d
        for d in state.ta_dates
        if d >= exit_date or (not plan.reference_problem and d not in ref)
    )
    deleted = set(plan.delete_price_dates)
    remaining = {d: p for d, p in state.prices.items() if d not in deleted}
    plan.rows_to_write = rows_to_write(rows, remaining)

    u = state.universe
    if u is None:
        plan.universe_action = "insert"
    elif (
        u["company_name"] != PARA_COMPANY_NAME
        or u["is_active"]
        or any(u[k] for k in ("sector", "industry", "cusip", "isin"))
    ):
        plan.universe_action = "update"
    plan.ta_needed = bool(
        plan.rows_to_write
        or plan.delete_price_dates
        or plan.delete_ta_dates
        or (not state.ta_dates and len(remaining) >= MIN_TA_ROWS)
    )
    return plan


# ── Company names ────────────────────────────────────────────────────────


def security_names_from_table(df) -> dict[str, str]:
    """``{ticker: company name}`` from the Added/Removed columns of the change table.

    The table is newest first and ``setdefault`` keeps the first hit, so the
    most recent name wins. Each name is stored under the source symbol and
    under the company's current symbol.
    """
    header = WikipediaIndexHistoryParser._header_tokens(df.columns)
    cols: dict[tuple[str, str], object] = {}
    for col, tokens in zip(df.columns, header, strict=True):
        joined = " ".join(tokens)
        kind = (
            "ticker"
            if "ticker" in joined
            else "name"
            if ("security" in joined or "company" in joined)
            else None
        )
        if kind is None:
            continue
        for side in ("added", "removed"):
            if tokens[0].startswith(side):
                cols[(side, kind)] = col
    names: dict[str, str] = {}
    for _, row in df.iterrows():
        for side in ("added", "removed"):
            tcol, ncol = cols.get((side, "ticker")), cols.get((side, "name"))
            if tcol is None or ncol is None:
                continue
            ticker, name = clean_ticker(row[tcol]), row[ncol]
            if not ticker or not isinstance(name, str):
                continue
            name = re.sub(r"\[.*?\]", "", name).strip()
            if name and name.lower() != "nan":
                names.setdefault(ticker, name)
                names.setdefault(DEFAULT_ALIASES.current_symbol(ticker), name)
    return names


def wikipedia_security_names() -> dict[str, str]:
    import pandas as pd

    parser = WikipediaIndexHistoryParser()
    tables = pd.read_html(io.StringIO(fetch_html(SP500_CHANGES_URL)))
    df = parser._find_changes_table(tables, INDEX_NAME)
    return security_names_from_table(df) if df is not None else {}


def alpaca_asset(
    symbol: str, headers: dict[str, str], endpoint: str
) -> NameInfo | None:
    """Name/exchange of an Alpaca asset (also inactive ones); ``None`` if unknown."""
    try:
        resp = requests.get(
            f"{endpoint}/v2/assets/{symbol}", headers=headers, timeout=30
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return NameInfo(data.get("name") or None, data.get("exchange") or None)
    except (requests.exceptions.RequestException, ValueError):
        return None


def collect_names(
    tickers: Sequence[str], headers: dict, endpoint: str
) -> dict[str, NameInfo]:
    """Company names from the Wikipedia change table, Alpaca assets as fallback."""
    if not tickers:
        return {}
    wiki: dict[str, str] = {}
    try:
        wiki = wikipedia_security_names()
    except Exception as e:  # names are cosmetic: never stop the repair for them
        logger.warning(f"Wikipedia company names unavailable: {type(e).__name__}: {e}")
    names: dict[str, NameInfo] = {}
    for t in tickers:
        asset = None if t in wiki else alpaca_asset(t, headers, endpoint)
        names[t] = NameInfo(
            name=wiki.get(t) or (asset.name if asset else None),
            exchange=asset.exchange if asset else None,
        )
    return names


# ── Database access ──────────────────────────────────────────────────────


def load_intervals(session) -> Intervals:
    from sqlalchemy import select

    from trading_signals.db.models.index_membership import IndexMembership as M

    rows = session.execute(
        select(M.ticker, M.valid_from, M.valid_to).where(M.index_name == INDEX_NAME)
    ).all()
    result: Intervals = defaultdict(list)
    for ticker, valid_from, valid_to in rows:
        result[ticker].append((valid_from, valid_to))
    return {t: sorted(v, key=lambda iv: iv[0]) for t, v in result.items()}


def load_universe(session) -> set[str]:
    from sqlalchemy import select

    from trading_signals.db.models.universe import Universe

    return {r[0] for r in session.execute(select(Universe.ticker)).all()}


def load_real_dates(
    session, tickers: Sequence[str], ds: date, last_session: date
) -> dict[str, set[date]]:
    """Dates of the real (non-extrapolated) price rows per ticker."""
    from sqlalchemy import select

    from trading_signals.db.models.prices import PriceDaily as P

    result: dict[str, set[date]] = defaultdict(set)
    if not tickers:
        return result
    stmt = select(P.ticker, P.trade_date).where(
        P.ticker.in_(list(tickers)),
        P.trade_date >= ds,
        P.trade_date <= last_session,
        P.is_extrapolated.is_not(True),
    )
    for ticker, trade_date in session.execute(stmt).all():
        result[ticker].add(trade_date)
    return result


def load_stored_prices(
    session, ticker: str, start: date | None = None
) -> dict[date, StoredPrice]:
    from sqlalchemy import select

    from trading_signals.db.models.prices import PriceDaily as P

    stmt = select(
        P.trade_date, P.open, P.high, P.low, P.close, P.volume, P.is_extrapolated
    ).where(P.ticker == ticker)
    if start:
        stmt = stmt.where(P.trade_date >= start)
    return {
        r[0]: StoredPrice(
            _num(r[1]),
            _num(r[2]),
            _num(r[3]),
            _num(r[4]),
            None if r[5] is None else int(r[5]),
            bool(r[6]),
        )
        for r in session.execute(stmt).all()
    }


def insert_universe_rows(session, inserts: Sequence[UniverseInsert]) -> int:
    """Insert rows; existing tickers are left untouched (ON CONFLICT DO NOTHING)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from trading_signals.db.models.universe import Universe

    if not inserts:
        return 0
    rows = [
        {
            "ticker": i.ticker,
            "company_name": i.name,
            "exchange": i.exchange,
            "added_date": i.added_date,
            "added_by": ADDED_BY,
            "is_active": i.active,
            "last_seen": date.today() if i.active else None,
        }
        for i in inserts
    ]
    result = session.execute(
        pg_insert(Universe)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["ticker"])
    )
    return int(result.rowcount or 0)


def _all_tables() -> list:
    """Every table of the signals schema (ORM metadata, all models registered)."""
    import trading_signals.db.models  # noqa: F401  (registers the tables)
    import trading_signals.db.models.short_interest  # noqa: F401
    from trading_signals.db.base import Base

    return list(Base.metadata.sorted_tables)


def ticker_tables() -> list:
    """Tables with a ``ticker`` column, except the ones the PARA fix repairs."""
    return [
        t for t in _all_tables() if "ticker" in t.c and t.name not in _PARA_OWN_TABLES
    ]


def symbol_array_tables() -> list:
    """Tables that list tickers in a ``symbols`` array column (``news_articles``)."""
    from sqlalchemy.dialects.postgresql import ARRAY

    return [
        t
        for t in _all_tables()
        if "symbols" in t.c and isinstance(t.c.symbols.type, ARRAY)
    ]


def date_column(table):
    """The column used to date the rows of ``table`` (``None`` = count only)."""
    from sqlalchemy import Date, DateTime

    for name in _DATE_COLUMNS:
        if name in table.c:
            return table.c[name]
    for col in table.c:
        if isinstance(col.type, (Date, DateTime)):
            return col
    return None


def count_ticker_rows(
    session, ticker: str, from_date: date
) -> list[tuple[str, int, int | None]]:
    """``(table, rows of ticker, rows dated >= from_date or None)`` per table.

    Covers every table with a ``ticker`` column and the ``symbols`` array of
    ``news_articles``; tables without rows of the ticker are left out.
    """
    from sqlalchemy import func, select

    targets = [(t, t.c.ticker == ticker) for t in ticker_tables()]
    targets += [(t, t.c.symbols.any(ticker)) for t in symbol_array_tables()]
    result: list[tuple[str, int, int | None]] = []
    for table, match in targets:
        total = session.execute(
            select(func.count()).select_from(table).where(match)
        ).scalar_one()
        after: int | None = None
        col = date_column(table)
        if col is not None:
            after = session.execute(
                select(func.count()).select_from(table).where(match, col >= from_date)
            ).scalar_one()
        if total:
            result.append((table.name, int(total), after))
    return result


def load_para_state(session, exit_date: date = PARA_EXIT_DATE) -> ParaState:
    from sqlalchemy import select

    from trading_signals.db.models.technical_indicators import TechnicalIndicator as T
    from trading_signals.db.models.universe import Universe

    state = ParaState()
    row = session.execute(
        select(Universe).where(Universe.ticker == PARA)
    ).scalar_one_or_none()
    if row is not None:
        state.universe = {
            "company_name": row.company_name,
            "is_active": row.is_active,
            "added_by": row.added_by,
            "added_date": row.added_date,
            "exchange": row.exchange,
            "sector": row.sector,
            "industry": row.industry,
            "cusip": row.cusip,
            "isin": row.isin,
        }
    state.prices = load_stored_prices(session, PARA)
    state.ta_dates = {
        r[0]
        for r in session.execute(select(T.trade_date).where(T.ticker == PARA)).all()
    }
    state.other_tables = count_ticker_rows(session, PARA, exit_date)
    return state


def _backup_rows(session, model, where, backup_dir: str, prefix: str) -> int:
    """Write the rows of ``model`` matching ``where`` to a verified CSV backup."""
    from sqlalchemy import inspect, select

    columns = [a.key for a in inspect(model).column_attrs]
    rows = session.execute(select(model).where(*where)).scalars().all()
    data = [{c: getattr(r, c) for c in columns} for r in rows]
    try:
        path = write_csv_backup(data, columns, backup_dir, prefix)
    except BackupError as e:
        raise AbortError(str(e)) from e
    out(f"  backup of {len(data)} rows ({prefix}): {path}")
    return len(data)


def apply_para_fix(session, plan: ParaFixPlan, backup_dir: str) -> dict[str, int]:
    """One transaction step: back up, fix the universe row, delete, refill prices.

    Technical indicators are recomputed in the later TA step (which deletes and
    rebuilds every PARA row from the corrected prices).
    """
    from sqlalchemy import delete, update

    from trading_signals.db.models.prices import PriceDaily
    from trading_signals.db.models.technical_indicators import TechnicalIndicator
    from trading_signals.db.models.universe import Universe

    if plan.reference_problem:
        raise AbortError(f"cannot fix PARA: {plan.reference_problem}")
    counts = {"prices_deleted": 0, "ta_deleted": 0, "prices_written": 0}
    if not plan.changes_anything:
        out("  PARA is already fixed -- nothing to do")
        return counts

    price_where = (
        PriceDaily.ticker == PARA,
        PriceDaily.trade_date.in_(plan.delete_price_dates),
    )
    ta_where = (
        TechnicalIndicator.ticker == PARA,
        TechnicalIndicator.trade_date.in_(plan.delete_ta_dates),
    )
    if plan.delete_price_dates:
        _backup_rows(session, PriceDaily, price_where, backup_dir, "para_prices_daily")
    if plan.delete_ta_dates:
        _backup_rows(
            session,
            TechnicalIndicator,
            ta_where,
            backup_dir,
            "para_technical_indicators",
        )

    if plan.universe_action == "update":
        _backup_rows(
            session, Universe, (Universe.ticker == PARA,), backup_dir, "para_universe"
        )
        session.execute(
            update(Universe)
            .where(Universe.ticker == PARA)
            .values(
                company_name=PARA_COMPANY_NAME,
                is_active=False,
                index_membership=None,
                sector=None,
                industry=None,
                cusip=None,
                isin=None,
                metadata_json={"note": PARA_UNIVERSE_NOTE},
            )
        )
    elif plan.universe_action == "insert":
        out("  PARA is not in the universe -- inserting the Paramount row (inactive)")
        added = PARA_EXIT_DATE - timedelta(days=1)
        insert_universe_rows(
            session, [UniverseInsert(PARA, added, False, PARA_COMPANY_NAME)]
        )

    if plan.delete_price_dates:
        result = session.execute(delete(PriceDaily).where(*price_where))
        counts["prices_deleted"] = int(result.rowcount or 0)
    if plan.delete_ta_dates:
        result = session.execute(delete(TechnicalIndicator).where(*ta_where))
        counts["ta_deleted"] = int(result.rowcount or 0)
    if plan.rows_to_write:
        counts["prices_written"] = upsert_price_rows(session, plan.rows_to_write)
    session.flush()
    return counts


# ── Technical indicators ─────────────────────────────────────────────────


def ta_missing_tickers(session) -> list[str]:
    """Onboarded tickers that have price rows but no indicator rows yet."""
    from sqlalchemy import exists, func, select

    from trading_signals.db.models.prices import PriceDaily as P
    from trading_signals.db.models.technical_indicators import TechnicalIndicator as T
    from trading_signals.db.models.universe import Universe
    from trading_signals.derived.technical_indicators import MIN_DAYS_RSI

    n_prices = (
        select(func.count())
        .select_from(P)
        .where(P.ticker == Universe.ticker, P.is_extrapolated.is_not(True))
        .scalar_subquery()
    )
    stmt = select(Universe.ticker).where(
        Universe.added_by == ADDED_BY,
        ~exists().where(T.ticker == Universe.ticker),
        n_prices >= MIN_DAYS_RSI,
    )
    return sorted(r[0] for r in session.execute(stmt).all())


def recompute_ta(session, tickers: Sequence[str]) -> dict[str, int]:
    """Delete + recompute the indicators of ``tickers`` (one savepoint each)."""
    from trading_signals.derived.technical_indicators import TechnicalIndicatorsComputer

    ta = TechnicalIndicatorsComputer(session)
    rows = errors = 0
    for ticker in sorted(set(tickers)):
        try:
            with session.begin_nested():
                rows += ta.recompute_ticker(ticker)
        except Exception as e:  # keep going; the failure is reported
            errors += 1
            logger.error(f"TA recompute failed for {ticker}: {type(e).__name__}: {e}")
    session.flush()
    return {"tickers": len(set(tickers)), "ta_rows": rows, "errors": errors}


# ── Reporting ────────────────────────────────────────────────────────────


def _range(d: Sequence[date]) -> str:
    return f"{d[0]}..{d[-1]}" if d else "-"


def print_universe_section(
    inserts: Sequence[UniverseInsert], skipped_current: Sequence[str], n_tickers: int
) -> None:
    out(f"[1] Universe  ({n_tickers} tickers with an sp500 interval in the window)")
    out(f"  INSERT {len(inserts)} rows ({sum(i.active for i in inserts)} active)")
    for i in inserts:
        out(
            f"    {i.ticker:<7} {'ACTIVE ' if i.active else 'inactive'} "
            f"added_date={i.added_date}  {i.name or '?'}"
            + (f"  [{i.exchange}]" if i.exchange else "")
        )
    if skipped_current:
        out(
            f"  {len(skipped_current)} CURRENT members are missing in the universe and "
            f"are NOT inserted: {', '.join(skipped_current)}"
        )
        out(
            "    -> they must be active: let index_sync add them or use "
            "--add-current-members"
        )


def print_price_section(plans: Sequence[TickerPlan], fetched: bool) -> None:
    out(f"[2] Prices  ({len(plans)} tickers whose membership sessions are not covered)")
    if not plans:
        return
    out(
        f"    {'ticker':<7} {'expected':>8} {'stored':>7} {'missing':>8} "
        f"{'fetched':>8} {'write':>6}  gaps (head/internal/tail)   requests"
    )
    for p in plans:
        stored = sum(1 for d in p.expected if d in p.stored_real)
        fetched_txt = f"{len(p.fetched):>8}" if fetched else f"{'-':>8}"
        write_txt = f"{len(p.to_write):>6}" if fetched else f"{'-':>6}"
        gaps = (
            f"{len(p.gaps.get('head', []))}/{len(p.gaps.get('internal', []))}/"
            f"{len(p.gaps.get('tail', []))}"
            if fetched
            else "-"
        )
        reqs = "; ".join(
            f"{t.symbol} {t.start}..{t.end} asof={t.asof}" for t in p.tasks
        )
        out(
            f"    {p.ticker:<7} {len(p.expected):>8} {stored:>7} {len(p.missing):>8} "
            f"{fetched_txt} {write_txt}  {gaps:<25} {reqs}"
        )
    out(
        f"    totals: expected {sum(len(p.expected) for p in plans)}, "
        f"stored {sum(sum(1 for d in p.expected if d in p.stored_real) for p in plans)}"
        + (
            f", fetched {sum(len(p.fetched) for p in plans)}, "
            f"rows to write {sum(len(p.to_write) for p in plans)}"
            if fetched
            else ""
        )
    )
    if fetched:
        unresolved = [p for p in plans if any(p.gaps.get(k) for k in p.gaps)]
        no_data = [p.ticker for p in plans if not p.fetched and not p.stored_real]
        if no_data:
            out(f"    NO DATA at all for: {', '.join(no_data)}")
        if unresolved:
            out(
                f"    unresolved gaps ({len(unresolved)} tickers; sessions "
                "neither stored nor fetched):"
            )
            for p in unresolved:
                for kind in ("internal", "head", "tail"):
                    days = p.gaps.get(kind) or []
                    if days:
                        shown = ", ".join(str(d) for d in days[:6])
                        more = f" (+{len(days) - 6} more)" if len(days) > 6 else ""
                        flag = "  <-- CHECK" if kind == "internal" else ""
                        out(
                            f"      {p.ticker:<7} {kind:<8} {len(days):>4}: "
                            f"{shown}{more}{flag}"
                        )


def print_para_section(state: ParaState, plan: ParaFixPlan, fix: bool) -> None:
    out(
        "[3] PARA  ("
        + ("--fix-para" if fix else "check only; pass --fix-para to repair")
        + ")"
    )
    u = state.universe
    if u is None:
        out("  universe: no PARA row")
    else:
        out(
            f"  universe row today: {u['company_name']!r} active={u['is_active']} "
            f"added_by={u['added_by']} sector={u['sector']} cusip={u['cusip']}"
        )
    if fix:
        action = {
            "insert": "INSERT inactive Paramount row",
            "update": "UPDATE -> Paramount Global, inactive, Banzai data cleared",
            None: "already fixed",
        }[plan.universe_action]
        out(f"  universe row: {action}")
    closes = [p.close for p in state.prices.values() if p.close is not None]
    if closes:
        high = sum(1 for c in closes if c > 1000)
        out(
            f"  stored prices: {len(state.prices)} rows "
            f"{_range(sorted(state.prices))}, close min {min(closes):,.2f} "
            f"max {max(closes):,.2f}, {high} rows with close > 1,000"
        )
    if plan.jumps:
        shown = ", ".join(f"{d} {a:,.2f}->{b:,.2f}" for d, a, b in plan.jumps[:5])
        out(
            f"  price discontinuities in the stored series ({len(plan.jumps)}): {shown}"
        )
    high = sum(1 for p in state.prices.values() if p.close and p.close > 1000)
    if not fix:
        if high or plan.jumps:
            out(
                "  WARNING: the stored PARA series is not Paramount's (another "
                "company holds the symbol) -- run with --fix-para"
            )
    else:
        if plan.reference_problem:
            out(f"  REFERENCE UNUSABLE: {plan.reference_problem}")
        else:
            out(
                f"  Paramount reference: {len(plan.reference)} bars "
                f"{_range(sorted(plan.reference))}, "
                f"max close {max(plan.reference.values()):,.2f}"
            )
        out(
            f"  stored rows to delete: {len(plan.delete_price_dates)} "
            f"(>= {PARA_EXIT_DATE}: {len(plan.exit_dates)}, earlier without a "
            f"Paramount bar: {len(plan.foreign_dates)}); technical_indicators to "
            f"delete: {len(plan.delete_ta_dates)}"
        )
        if plan.foreign_dates:
            out(
                "  foreign rows before the exit date: "
                + ", ".join(str(d) for d in plan.foreign_dates[:10])
                + (" ..." if len(plan.foreign_dates) > 10 else "")
            )
        if not plan.reference_problem:
            out(
                f"  stored rows differing >{PARA_MISMATCH_TOLERANCE:.0%} from "
                f"Paramount (overwritten): {len(plan.mismatched)}; Paramount rows "
                f"to write: {len(plan.rows_to_write)}"
            )
    if state.other_tables:
        out(
            "  PARA rows in other tables (NOT touched; second number = rows dated "
            f">= {PARA_EXIT_DATE}):"
        )
        for name, total, after in state.other_tables:
            out(
                f"    {name:<24} {total:>7} rows, "
                f"{'?' if after is None else after:>7} after exit"
            )


# ── Orchestration ────────────────────────────────────────────────────────


@dataclass
class Context:
    ds: date
    last_session: date
    feed: str
    headers: dict
    endpoint: str


def make_context(feed_override: str | None) -> Context:
    from trading_signals.config import get_settings
    from trading_signals.utils.market_calendar import last_completed_session
    from trading_signals.utils.retention import data_start_date

    settings = get_settings()
    settings.validate_alpaca_safety()  # paper endpoint only
    feed = (feed_override or settings.ALPACA_DATA_FEED or "sip").lower()
    return Context(
        ds=data_start_date(),
        last_session=last_completed_session(),
        feed=feed,
        headers={
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        },
        endpoint=settings.ALPACA_ENDPOINT,
    )


@dataclass
class Plan:
    """Everything one run intends to do (filled in three phases)."""

    intervals: Intervals = field(default_factory=dict)
    inserts: list[UniverseInsert] = field(default_factory=list)
    skipped_current: list[str] = field(default_factory=list)
    prices: list[TickerPlan] = field(default_factory=list)
    stored_rows: dict[str, dict[date, StoredPrice]] = field(default_factory=dict)
    para_state: ParaState = field(default_factory=ParaState)
    para_plan: ParaFixPlan = field(default_factory=ParaFixPlan)
    para_tasks: list[FetchTask] = field(default_factory=list)
    ta_pending: list[str] = field(default_factory=list)
    fetched: bool = False
    fetch_errors: list[str] = field(default_factory=list)

    @property
    def price_tickers(self) -> list[str]:
        return sorted({p.ticker for p in self.prices if p.to_write})

    def ta_targets(self, fix_para: bool) -> list[str]:
        targets = set(self.price_tickers) | set(self.ta_pending)
        if fix_para and self.para_plan.ta_needed:
            targets.add(PARA)
        return sorted(targets)


def wanted_tickers(args: argparse.Namespace) -> set[str]:
    return {t.strip().upper() for t in args.tickers.split(",") if t.strip()}


def build_plan(session, ctx: Context, args: argparse.Namespace) -> Plan:
    """Read phase: what the DB says (``session`` is read-only)."""
    wanted = wanted_tickers(args)
    cal = SessionCalendar(ctx.ds, ctx.last_session)
    plan = Plan()

    intervals = window_intervals(load_intervals(session), ctx.ds)
    if wanted:
        intervals = {t: v for t, v in intervals.items() if t in wanted}
    plan.intervals = intervals
    universe = load_universe(session)
    closed = sum(1 for ivs in intervals.values() if all(iv[1] for iv in ivs))
    if not wanted and closed < MIN_CLOSED_INTERVALS:
        out(
            f"WARNING: only {closed} tickers have closed sp500 intervals in the "
            "window -- run rebuild_sp500_membership.py --apply first"
        )

    plan.inserts, plan.skipped_current = plan_universe_inserts(
        intervals, universe, {}, add_current=args.add_current_members
    )
    # a ticker can only get prices if it is (or is about to be) in the universe
    insertable = universe | {i.ticker for i in plan.inserts}
    stored_dates = load_real_dates(session, sorted(intervals), ctx.ds, ctx.last_session)
    plan.prices = [
        p
        for p in plan_prices(
            intervals,
            stored_dates,
            cal,
            ctx.ds,
            ctx.last_session,
            skip={PARA},
            asof_mode=args.asof,
        )
        if p.ticker in insertable
    ]
    plan.stored_rows = {
        p.ticker: load_stored_prices(session, p.ticker, ctx.ds) for p in plan.prices
    }
    plan.para_state = load_para_state(session)
    plan.para_plan = plan_para_fix(plan.para_state, None, ctx.ds)
    plan.ta_pending = [
        t for t in ta_missing_tickers(session) if not wanted or t in wanted
    ]
    if args.fix_para:
        plan.para_tasks = build_fetch_tasks(
            PARA,
            ctx.ds,
            PARA_EXIT_DATE - timedelta(days=1),
            member_start=ctx.ds,
            asof_mode=args.asof,
        )
    return plan


def fetch_phase(plan: Plan, ctx: Context, args: argparse.Namespace) -> None:
    """Network phase (read-only GETs): names, bars, Paramount reference."""
    names = collect_names([i.ticker for i in plan.inserts], ctx.headers, ctx.endpoint)
    for i in plan.inserts:
        info = names.get(i.ticker)
        if info:
            i.name, i.exchange = info.name, info.exchange
    tasks = [t for p in plan.prices for t in p.tasks] + plan.para_tasks
    bars, plan.fetch_errors = fetch_task_bars(tasks, ctx.headers, ctx.feed)
    if plan.fetch_errors and args.apply:
        raise AbortError(
            f"{len(plan.fetch_errors)} Alpaca request(s) failed: "
            + "; ".join(plan.fetch_errors)
            + " -- nothing written"
        )
    for p in plan.prices:
        apply_fetch(p, bars, plan.stored_rows[p.ticker])
    reference = (
        [r for t in plan.para_tasks for r in task_rows(t, bars.get(t, []))]
        if args.fix_para
        else None
    )
    plan.para_plan = plan_para_fix(plan.para_state, reference, ctx.ds)
    plan.fetched = True


def print_report(plan: Plan, args: argparse.Namespace) -> None:
    wanted = wanted_tickers(args)
    out("")
    print_universe_section(plan.inserts, plan.skipped_current, len(plan.intervals))
    out("")
    print_price_section(plan.prices, plan.fetched)
    for e in plan.fetch_errors:
        out(f"  FAILED REQUEST: {e}")
    out("")
    if args.fix_para or not wanted or PARA in wanted:
        print_para_section(plan.para_state, plan.para_plan, args.fix_para)
        out("")
    targets = plan.ta_targets(args.fix_para)
    out(
        f"[4] Technical indicators: recompute for {len(targets)} tickers "
        f"({len(set(targets) - set(plan.ta_pending))} with new price rows, "
        f"{len(set(plan.ta_pending) - set(plan.price_tickers))} onboarded without "
        "indicators)"
    )


def apply_steps(plan: Plan, args: argparse.Namespace) -> int:
    """One transaction per logical step; every step is idempotent."""
    with repair_session(read_only=False) as session:
        n = insert_universe_rows(session, plan.inserts)
        session.commit()
    out(f"STEP universe: {n} rows inserted")

    if args.fix_para:
        with repair_session(read_only=False) as session:
            counts = apply_para_fix(session, plan.para_plan, args.backup_dir)
            session.commit()
        out(f"STEP fix-para: {counts}")

    rows = [r for p in plan.prices for r in p.to_write]
    with repair_session(read_only=False) as session:
        written = upsert_price_rows(session, rows) if rows else 0
        session.commit()
    out(f"STEP prices: {written} rows upserted for {len(plan.price_tickers)} tickers")

    with repair_session(read_only=False) as session:
        stats = recompute_ta(session, plan.ta_targets(args.fix_para))
        session.commit()
    out(f"STEP technical indicators: {stats}")
    return 1 if stats["errors"] else 0


def run(args: argparse.Namespace) -> int:
    ctx = make_context(args.feed)
    out(
        f"data window {ctx.ds} .. {ctx.last_session}, feed={ctx.feed}, asof={args.asof}"
    )
    if ctx.feed != "sip":
        out(
            "WARNING: feed is not 'sip' -- the collectors use SIP; mixed feeds "
            "distort prices"
        )
    with repair_session(read_only=True) as session:
        plan = build_plan(session, ctx, args)
    if not args.no_fetch:
        fetch_phase(plan, ctx, args)
    print_report(plan, args)
    if not args.apply:
        out("")
        out("DRY RUN -- nothing written. Use --apply to write.")
        return 0
    if not plan.fetched and (plan.prices or args.fix_para):
        raise AbortError(
            "--apply needs the Alpaca data; do not combine it with --no-fetch"
        )
    out("")
    return apply_steps(plan, args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="Write to the DB (default: dry run)"
    )
    p.add_argument(
        "--fix-para",
        action="store_true",
        help="Make PARA the Paramount Global entity again (see the module docstring)",
    )
    p.add_argument(
        "--tickers", default="", help="Comma separated subset of tickers (default: all)"
    )
    p.add_argument(
        "--add-current-members",
        action="store_true",
        help="Also insert current members missing in the universe, as ACTIVE rows",
    )
    p.add_argument(
        "--no-fetch",
        action="store_true",
        help="Dry run without any network access (no bars, names or reference series)",
    )
    p.add_argument(
        "--asof",
        choices=("first", "last"),
        default="first",
        help=(
            "asof of the Alpaca requests: the first member day of the request "
            "(default; resolves reused symbols such as INFO correctly) or its "
            "last day"
        ),
    )
    p.add_argument(
        "--feed",
        default=None,
        choices=("sip", "iex"),
        help="Override ALPACA_DATA_FEED (default: the collector's feed)",
    )
    p.add_argument(
        "--backup-dir",
        default=DEFAULT_BACKUP_DIR,
        help="Directory for the CSV backups of deleted PARA rows (default: "
        "%(default)s)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target("onboard_removed_members")
    out(f"mode: {'APPLY' if args.apply else 'DRY RUN (read-only)'}")
    try:
        return run(args)
    except AbortError as e:
        out(f"ABORT: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
