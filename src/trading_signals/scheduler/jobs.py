"""Scheduler job definitions.

Each job is a simple function that instantiates a collector (or derived
computer) and runs it. Jobs are registered with APScheduler in
:mod:`trading_signals.scheduler.setup`.

* Collector jobs (``BaseCollector`` subclasses) write their own
  ``collection_log`` entry and alert themselves.
* All other jobs run through :func:`~trading_signals.scheduler.runner.run_logged_job`
  (collection_log with ``job_status`` constants, log capture, alerting).
* Feature pipeline, target backfill and context pack run sequentially in the
  :func:`run_nightly_chain` orchestrator after all feature inputs are in.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

from trading_signals.collectors.prices_alpaca import PriceCollectorAlpaca
from trading_signals.db.base import now_local
from trading_signals.scheduler.registry import NIGHTLY_UPSTREAM, get_collector_name
from trading_signals.scheduler.runner import (
    JobOutcome,
    mark_stale_runs,
    run_logged_job,
    write_log,
)
from trading_signals.utils import job_status
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import DATA_RETENTION_QUARTERS, quarter_cutoff

logger = get_logger(__name__)

__all__ = ["DATA_RETENTION_QUARTERS", "quarter_cutoff"]  # re-exported for scripts/tests


# ── Collector helper ─────────────────────────────────────────────────────


def _run_collector(
    job_id: str,
    factory: Callable[[], object],
    *,
    skip_on_value_error: bool = False,
    post: Callable[[], None] | None = None,
    post_statuses: tuple[str, ...] = (job_status.SUCCESS,),
):
    """Run a BaseCollector (which logs + alerts itself) and an optional post step.

    Args:
        job_id: Scheduler job id (for logging).
        factory: Creates the collector instance.
        skip_on_value_error: Treat ``ValueError`` from the constructor (missing
            API key) as SKIPPED instead of failing.
        post: Follow-up computation run only if the collector status is in
            ``post_statuses``.
    """
    logger.info(f"Scheduler triggered: {job_id}")
    try:
        collector = factory()
    except ValueError as e:
        if not skip_on_value_error:
            raise
        logger.warning(f"{job_id} skipped: {e}")
        write_log(
            get_collector_name(job_id),
            now_local(),
            JobOutcome(status=job_status.SKIPPED, notes=str(e)),
        )
        return None

    log = collector.run()
    status = job_status.normalize(getattr(log, "status", None))
    logger.info(
        f"{job_id} finished: status={status}, "
        f"written={getattr(log, 'records_written', None)}"
    )
    if post is not None and status in post_statuses:
        post()
    return log


def run_price_collector() -> None:
    """Daily price collection job.

    Scheduled for 22:30 Europe/Berlin (after US market close at 22:00 MEZ,
    plus buffer for the SIP 15-minute rule / session-completion check).
    Uses Alpaca Market Data API (replaced yfinance in Sprint 1b).
    """
    _run_collector("price_collector", lambda: PriceCollectorAlpaca(lookback_days=10))


def run_ark_holdings_collector() -> None:
    """Daily ARK holdings snapshot + delta computation.

    Scheduled for 23:00 Europe/Berlin (ARK publishes after US close,
    arkfunds.io needs time to aggregate).
    """
    from trading_signals.collectors.ark_holdings import ARKHoldingsCollector

    def _deltas() -> None:
        from trading_signals.db.session import get_session
        from trading_signals.derived.ark_deltas import ARKDeltaComputer

        def _body() -> int:
            with get_session() as session:
                return ARKDeltaComputer(session).compute_all()

        run_logged_job("ark_holdings", "ark_deltas", _body)

    # Deltas after SUCCESS or PARTIAL (deltas are computed per ETF/date)
    _run_collector(
        "ark_holdings",
        ARKHoldingsCollector,
        post=_deltas,
        post_statuses=(job_status.SUCCESS, job_status.PARTIAL),
    )


def run_form4_collector() -> None:
    """Daily SEC Form 4 insider trades collection + cluster computation.

    Scheduled for 23:30 Europe/Berlin (after ARK, to spread API load).
    SEC filings are available ~2 business days after transactions.
    """
    from trading_signals.collectors.form4_collector import Form4Collector

    def _clusters() -> None:
        from trading_signals.db.session import get_session
        from trading_signals.derived.insider_clusters import InsiderClusterComputer

        def _body() -> int:
            with get_session() as session:
                return InsiderClusterComputer(session).compute_new()

        run_logged_job("form4_collector", "insider_clusters", _body)

    _run_collector(
        "form4_collector",
        lambda: Form4Collector(lookback_days=7),
        post=_clusters,
        post_statuses=(job_status.SUCCESS, job_status.PARTIAL),
    )


def run_form13f_collector() -> None:
    """Weekly SEC Form 13F institutional holdings collection.

    Scheduled for Sundays at 10:00 Europe/Berlin.
    13F filings are quarterly – weekly check catches new filings promptly.
    """
    from trading_signals.collectors.form13f_collector import Form13FCollector

    _run_collector("form13f_collector", lambda: Form13FCollector(lookback_days=120))


def run_sec_filings_collector() -> None:
    """Daily SEC EDGAR filings (8-K + press releases, 10-Q/K, 144, 13D/G, ...).

    Scheduled for 04:00 Europe/Berlin = 22:00 ET, EDGAR's cut-off for
    same-day filings (8-Ks are accepted until 22:00 ET and get that day's
    filing date), so the 04:30 nightly chain sees the complete US day - in
    particular after-the-close earnings 8-Ks (16:00-17:30 ET). Outside the
    22:30-03:00 collector load, 30 minutes before the chain. A non-critical
    nightly upstream (``NIGHTLY_UPSTREAM["sec_filings"] = False``): the context
    pack reads the table (section 15), so the chain waits for a still running
    job, and a failed night only makes the chain ``partial``. The daily
    04:00 run is always after the close of the session the chain targets (also
    on weekends: Saturday 04:00 is Friday 22:00 ET). ~650 submissions
    requests + one request per 8-K text, well under 15 minutes.
    """
    from trading_signals.collectors.sec_filings_collector import SecFilingsCollector

    _run_collector(
        "sec_filings_collector", lambda: SecFilingsCollector(lookback_days=14)
    )


def run_sec_financials_collector() -> None:
    """Weekly SEC XBRL quarterly financials (companyfacts API).

    Scheduled for Sundays at 14:00 Europe/Berlin (after the other Sunday
    collectors, daytime, far from the nightly load and the seal runs).
    ~650 companyfacts requests (one per CIK, 0.3-0.5 MB each) ≈ 5-10 minutes.
    """
    from trading_signals.collectors.sec_financials_collector import (
        SecFinancialsCollector,
    )

    _run_collector("sec_financials_collector", SecFinancialsCollector)


def run_politician_trades_collector() -> None:
    """Weekly politician trades collection from official disclosure portals.

    Scheduled for Sundays at 11:00 Europe/Berlin (after Form 13F).
    Politician trades are 30-45 days delayed, weekly check is sufficient.
    """
    from trading_signals.collectors.politician_trades_collector import (
        PoliticianTradesCollector,
    )

    _run_collector(
        "politician_trades_collector",
        lambda: PoliticianTradesCollector(lookback_days=365),
    )


def run_fundamentals_collector() -> None:
    """Weekly fundamentals collection via yfinance.

    Scheduled for Sundays at 01:00 Europe/Berlin (night slot).
    Fetches P/E, margins, revenue growth, EPS, etc. for all active tickers.
    """
    from trading_signals.collectors.fundamentals_collector import (
        FundamentalsCollectorYF,
    )

    _run_collector("fundamentals_collector", FundamentalsCollectorYF)


def run_analyst_ratings_collector() -> None:
    """Daily analyst ratings collection via yfinance.

    Scheduled for 01:00 Europe/Berlin (night slot, after daily collectors).
    Fetches analyst upgrades/downgrades for the last 30 days.
    """
    from trading_signals.collectors.analyst_ratings_collector import (
        AnalystRatingsCollector,
    )

    _run_collector(
        "analyst_ratings_collector", lambda: AnalystRatingsCollector(lookback_days=30)
    )


def run_estimates_collector() -> None:
    """Daily estimates collection via yfinance.

    Scheduled for 01:30 Europe/Berlin (night slot, after analyst ratings).
    Fetches EPS/Revenue consensus, revisions, and trend data.

    CRITICAL: Yahoo provides a rolling 90-day window for EPS revisions.
    Every day this collector doesn't run, one day of irrecoverable
    revision history is permanently lost. (Startup catch-up re-runs it
    if the container was down at 01:30.)
    """
    from trading_signals.collectors.estimates_collector import (
        EstimatesCollector,
    )

    _run_collector("estimates_collector", EstimatesCollector)


def run_earnings_calendar_collector() -> None:
    """Weekly earnings calendar update via yfinance.

    Scheduled for Sundays at 02:00 Europe/Berlin (after fundamentals).
    Fetches past and upcoming earnings dates with EPS surprise data.
    """
    from trading_signals.collectors.earnings_calendar_collector import (
        EarningsCalendarCollector,
    )

    _run_collector(
        "earnings_calendar_collector",
        lambda: EarningsCalendarCollector(earnings_limit=4),
    )


def run_news_collector() -> None:
    """Daily news collection from Alpaca News API.

    Scheduled for 00:00 Europe/Berlin (night slot).
    Fetches ticker-specific + global market news from the last 36 hours.
    """
    from trading_signals.collectors.news_collector import NewsCollectorAlpaca

    _run_collector("news_collector", lambda: NewsCollectorAlpaca(lookback_hours=36))


def run_fred_collector() -> None:
    """Daily FRED macro indicator collection.

    Scheduled for 04:15 Europe/Berlin (FRED updates ~22:00 ET = 04:00 CET),
    right before the nightly chain.
    Fetches 6 macro series: VIX, Treasury yields, HY spread,
    Dollar index, Breakeven Inflation.
    Very lightweight: 6 API calls, ~2 seconds total.
    """
    from trading_signals.collectors.fred_collector import FredCollector

    # FRED_API_KEY not configured → ValueError → SKIPPED
    _run_collector("fred_collector", FredCollector, skip_on_value_error=True)


# ── Options IV (Sprint 9.5b D3) ─────────────────────────────────────────


def run_options_iv_collector() -> None:
    """Daily options IV snapshot collection.

    Scheduled for 23:15 Europe/Berlin (shortly after the US close, so the
    snapshot reflects the closing option chain of the session). The
    collector labels snapshots with ``last_completed_session()``.
    Fetches ATM implied volatility, skew, term structure, and OI
    for all universe tickers.

    Rate-limited: ~750 tickers ≈ 20 minutes.
    IV-Rank needs ~1 year of daily data to be meaningful.
    """
    from trading_signals.collectors.options_iv_collector import OptionsIVCollector

    _run_collector("options_iv_collector", OptionsIVCollector, skip_on_value_error=True)


def run_short_interest_collector() -> None:
    """Daily short interest/volume collection via Massive API.

    Scheduled for 00:15 Europe/Berlin (FINRA short volume of the session is
    published ~18:00 ET = 00:00 CET). Rate-limited to 5 req/min — expect
    ~2.5 hours for ~750 tickers, i.e. done well before the nightly chain.
    """
    from trading_signals.collectors.short_interest_collector import (
        ShortInterestCollector,
    )

    _run_collector("short_interest_collector", ShortInterestCollector)


def run_short_interest_positions_collector() -> None:
    """Weekly FINRA short interest (settlement-date positions) via Massive API.

    Scheduled for Sundays at 12:00 Europe/Berlin (after the politician trades
    collector). FINRA publishes twice a month; a weekly run picks up new
    settlement dates and re-fetches the two most recent ones (revisions).
    ~3 requests per run. Not an upstream of the nightly chain: the feature
    pipeline applies a publication lag, so staleness is tolerated.
    """
    from trading_signals.collectors.short_interest_positions_collector import (
        ShortInterestPositionsCollector,
    )

    _run_collector(
        "short_interest_positions_collector",
        ShortInterestPositionsCollector,
        skip_on_value_error=True,
    )


# ── Derived computations ────────────────────────────────────────────────


def _technical_indicators_body() -> int:
    from trading_signals.db.session import get_session
    from trading_signals.derived.technical_indicators import (
        TechnicalIndicatorsComputer,
    )

    with get_session() as session:
        return TechnicalIndicatorsComputer(session).compute_catchup()


def run_technical_indicators_computer() -> None:
    """Daily technical indicators computation.

    Scheduled for 22:50 Europe/Berlin (after Price Collector at 22:30) and
    re-run (cheap, incremental) as first step of the nightly chain.
    Computes SMA, EMA, RSI, MACD, Bollinger, ATR, Volume SMA,
    and Relative Strength vs SPY from prices_daily data.

    Uses catch-up logic: automatically detects and fills any gaps
    between the latest computed indicator date and the latest price
    date. This handles missed runs, container restarts, and weekends.
    """
    run_logged_job(
        "technical_indicators_computer",
        "technical_indicators",
        _technical_indicators_body,
    )


def _sentiment_body() -> int:
    from trading_signals.db.session import get_session
    from trading_signals.derived.sentiment_computer import SentimentComputer
    from trading_signals.derived.sentiment_scorer import FinBERTScorer

    scorer = FinBERTScorer(batch_size=32)
    with get_session() as session:
        return SentimentComputer(session, scorer).compute()


def run_sentiment_computer() -> None:
    """Daily sentiment scoring of unscored news articles.

    Scheduled for 00:30 Europe/Berlin (after news collector).
    Uses FinBERT (CPU) to score all articles not yet processed.
    """
    run_logged_job("sentiment_computer", "sentiment_computer", _sentiment_body)


def _feature_pipeline_body(target_date: date) -> JobOutcome:
    from trading_signals.db.session import get_session
    from trading_signals.derived.feature_pipeline import FeaturePipeline

    with get_session() as session:
        written = FeaturePipeline(session).compute_daily(target_date)
    notes = f"session={target_date.isoformat()}"
    if not written:
        # 0 rows = non-trading day or no price bars for that session yet
        return JobOutcome(
            status=job_status.SKIPPED, records_written=0, notes=f"{notes} no rows"
        )
    return JobOutcome(records_written=written, notes=notes)


_DEADLOCK_RETRIES = 3
_DEADLOCK_BACKOFF_SECONDS = (5, 15)


def _target_backfill_body() -> int:
    """Run the backfill; retry on PostgreSQL deadlock (SQLSTATE 40P01).

    Retrying is safe: ``backfill_all`` only processes rows whose targets are
    still NULL, so a rolled-back attempt is simply redone. Deadlocks occur
    when another writer (e.g. a manual backfill script) touches
    feature_snapshots concurrently.
    """
    import time as _time

    from sqlalchemy.exc import OperationalError

    from trading_signals.db.session import get_session
    from trading_signals.derived.target_backfill import TargetBackfillComputer

    for attempt in range(1, _DEADLOCK_RETRIES + 1):
        try:
            with get_session() as session:
                return TargetBackfillComputer(session).backfill_all()
        except OperationalError as e:
            is_deadlock = getattr(e.orig, "pgcode", None) == "40P01"
            if not is_deadlock or attempt == _DEADLOCK_RETRIES:
                raise
            wait = _DEADLOCK_BACKOFF_SECONDS[
                min(attempt - 1, len(_DEADLOCK_BACKOFF_SECONDS) - 1)
            ]
            logger.warning(
                f"[target_backfill] deadlock detected (attempt "
                f"{attempt}/{_DEADLOCK_RETRIES}), retrying in {wait}s"
            )
            _time.sleep(wait)
    raise RuntimeError("unreachable")  # pragma: no cover


def _context_pack_body(target_date: date) -> JobOutcome:
    """Generate the pack of ``target_date`` - unless it exists already.

    A pack that has been assessed (``forecasts.yaml`` in its folder) or whose
    candidates are stored (``pack_candidates``) is never regenerated: neither
    its files nor its candidates are replaced or deleted
    (``ContextPackGenerator.skip_reason``). This is a successful outcome - the
    pack exists -, noted as ``pack kept``, so a PARTIAL chain run for the same
    session on a later day (weekend, holiday) cannot rewrite a pack Claude has
    already worked on.
    """
    from trading_signals.db.session import get_session
    from trading_signals.derived.context_pack import ContextPackGenerator

    with get_session() as session:
        generator = ContextPackGenerator(session)
        written = generator.generate_daily(target_date)
        kept = generator.skip_reason
    notes = f"session={target_date.isoformat()}"
    if kept:
        notes += f"; pack kept ({kept}) - not regenerated"
    return JobOutcome(records_written=written or 0, notes=notes)


def _forecast_review_body() -> JobOutcome:
    """Read ``forecasts.yaml`` of the skill, update the criterion and the report.

    ``docs/FORECASTS.md``: phase 1 stores new files append-only (own
    transaction), phase 2 freezes a decisive sample / computes its verdict --
    each written once -- and rewrites ``context_packs/forecast_auswertung.md``.
    Rejected files or rows → PARTIAL (alert); an evaluation error fails the run
    but keeps the ingested forecasts.
    """
    from pathlib import Path

    from trading_signals.analysis.forecast_review import (
        REPORT_FILE,
        evaluate_and_persist,
        ingest_new_files,
    )
    from trading_signals.config import get_settings

    root = Path(get_settings().CONTEXT_PACK_PATH)
    ingest = ingest_new_files(root)
    review = evaluate_and_persist(ingest, now_local().date(), now_local())
    if root.is_dir():
        (root / REPORT_FILE).write_text(review.report, encoding="utf-8-sig")
    notes = ingest.notes()
    update_notes = review.update.notes()
    if update_notes:
        notes += f"; criterion: {update_notes}"
    problems = bool(ingest.errors or review.update.problems)
    return JobOutcome(
        status=job_status.PARTIAL if problems else job_status.SUCCESS,
        records_written=ingest.forecasts,
        records_fetched=ingest.files,
        notes=notes,
    )


def _forecast_seal_body() -> JobOutcome:
    """Seal ``forecasts.yaml`` of the pack that is due at the coming open.

    ``docs/FORECASTS.md`` section 2: the file counts as on time only if its
    SHA-256 was recorded before 09:30 New York of the next session. Fires at
    09:00, 09:15 and 09:25 New York on every weekday; a holiday, a missing
    file and a delayed run (at or after the open: nothing is sealed) are
    reported, none of them can seal anything.
    """
    from pathlib import Path

    from trading_signals.config import get_settings
    from trading_signals.db.session import get_session
    from trading_signals.derived import forecast_seal as seal

    root = Path(get_settings().CONTEXT_PACK_PATH)
    with get_session() as session:
        result = seal.seal_forecast_file(seal.SqlSealStore(session), root)
    if result.status in (seal.SEALED, seal.UNCHANGED):
        status = job_status.SUCCESS
    elif result.status == seal.TOO_LATE:
        status = job_status.PARTIAL  # a seal opportunity was lost: alert
    else:  # no NYSE session today / no forecasts.yaml (yet)
        status = job_status.SKIPPED
    return JobOutcome(
        status=status,
        records_written=1 if result.status == seal.SEALED else 0,
        records_fetched=0 if result.sha256 is None else 1,
        notes=result.notes(),
    )


def _target_session() -> date:
    from trading_signals.utils.market_calendar import last_completed_session

    return last_completed_session()


def run_feature_pipeline() -> None:
    """Feature pipeline – computes feature snapshots for all tickers.

    Normally executed as step 2 of :func:`run_nightly_chain`; this entry
    point is the manual trigger (Settings → Scheduler). Target date is the
    last completed NYSE session (not ``today - 1``).
    Aggregates all raw + derived signals into feature_snapshots table.
    """
    target = _target_session()
    run_logged_job(
        "feature_pipeline", "feature_pipeline", lambda: _feature_pipeline_body(target)
    )


def run_target_backfill() -> None:
    """Target backfill – fills forward returns retrospectively.

    Normally executed as step 3 of :func:`run_nightly_chain` (manual
    trigger otherwise). Computes return_1d/5d/20d/60d for feature snapshots
    where sufficient future price data now exists.
    """
    run_logged_job("target_backfill", "target_backfill", _target_backfill_body)


def run_forecast_review() -> None:
    """Forecast review: read ``forecasts.yaml`` files, update the criterion, report.

    Normally executed as step 4 of :func:`run_nightly_chain` (manual
    trigger otherwise). Format and evaluation: ``docs/FORECASTS.md``.
    """
    run_logged_job("forecast_review", "forecast_review", _forecast_review_body)


def run_forecast_seal() -> None:
    """Forecast seal: SHA-256 of ``forecasts.yaml`` before the open (3x per session).

    Scheduled in America/New_York at 09:00, 09:15 and 09:25 on weekdays (NYSE
    sessions only); not part of the nightly chain. Without a seal a file is
    late (``docs/FORECASTS.md`` section 2).
    """
    run_logged_job("forecast_seal", "forecast_seal", _forecast_seal_body)


def run_context_pack_generator() -> None:
    """Context pack v2 for the last completed session.

    Normally executed as step 5 of :func:`run_nightly_chain` (manual
    trigger otherwise). Writes ``00_uebersicht.md``, ``screen.csv`` and one
    file per screened ticker (``docs/CONTEXT_PACK.md``) and stores the
    candidates in ``pack_candidates``.
    """
    target = _target_session()
    run_logged_job(
        "context_pack_generator",
        "context_pack_generator",
        lambda: _context_pack_body(target),
    )


# ── Nightly chain ────────────────────────────────────────────────────────

#: Max time the chain waits for upstream collectors that are still running.
CHAIN_MAX_WAIT = timedelta(hours=2)
CHAIN_POLL_SECONDS = 300

#: Minimum share of the baseline price rows the target session must reach
#: before the chain builds features on it (see :func:`coverage_ok`).
MIN_PRICE_COVERAGE = 0.8

#: The baseline is the maximum real price-row count over this many NYSE
#: sessions before the target. A single previous session is not enough: after
#: a multi-night outage of the same tickers every night would be compared with
#: an equally broken night (467 vs 467 = 100 %).
COVERAGE_BASELINE_SESSIONS = 5

#: The nightly chain looks for missing feature snapshots in this many NYSE
#: sessions before the target (automatic catch-up, see
#: :func:`catchup_candidates`).
CATCHUP_LOOKBACK_SESSIONS = 10

#: A session in the catch-up window counts as a gap if its feature-snapshot
#: row count is below this share of the window's maximum.
MIN_SNAPSHOT_COVERAGE = 0.8


def _session_close(session_date: date) -> datetime:
    """Regular NYSE close (16:00 ET) of ``session_date`` as aware datetime."""
    from trading_signals.utils.market_calendar import NY_TZ

    return datetime.combine(session_date, time(16, 0), tzinfo=NY_TZ)


def check_upstream(target: date) -> tuple[list[str], list[str]]:
    """Return ``(stale, running)`` upstream collector names for ``target``.

    A collector is *fresh* if it has a SUCCESS/PARTIAL run that started after
    the close of the target session; *running* if such a run is in progress.
    """
    from sqlalchemy import select

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    names = list(NIGHTLY_UPSTREAM)
    with get_session() as session:
        rows = session.execute(
            select(CollectionLog.collector_name, CollectionLog.status)
            .where(CollectionLog.collector_name.in_(names))
            .where(CollectionLog.started_at >= _session_close(target))
        ).all()
    fresh = {
        n
        for n, s in rows
        if job_status.normalize(s) in (job_status.SUCCESS, job_status.PARTIAL)
    }
    running = {n for n, s in rows if s is None or s == job_status.RUNNING}
    stale = [n for n in names if n not in fresh]
    return stale, [n for n in stale if n in running]


def wait_for_upstream(
    target: date,
    max_wait: timedelta = CHAIN_MAX_WAIT,
    poll_seconds: float = CHAIN_POLL_SECONDS,
    sleep: Callable[[float], None] = _time.sleep,
    clock: Callable[[], float] = _time.monotonic,
) -> list[str]:
    """Wait (bounded) while stale upstream collectors are still running.

    Returns the list of collector names that are still stale afterwards.
    """
    deadline = clock() + max_wait.total_seconds()
    while True:
        stale, running = check_upstream(target)
        if not running or clock() >= deadline:
            return stale
        logger.info(
            f"[nightly_chain] waiting for running upstream collectors: {running}"
        )
        sleep(poll_seconds)


def coverage_ok(
    current: int, baseline: int, min_ratio: float = MIN_PRICE_COVERAGE
) -> bool:
    """True if ``current`` reaches ``min_ratio`` of the ``baseline`` price rows.

    No rows for the target session are never OK. ``baseline == 0`` with rows
    on the target (bootstrap, nothing to compare against) counts as OK.
    """
    if current <= 0:
        return False
    if baseline <= 0:
        return True
    return current / baseline >= min_ratio


def price_coverage(target: date) -> tuple[int, int]:
    """Return ``(target count, baseline)`` of real price rows.

    The baseline is the maximum per-date count over the
    ``COVERAGE_BASELINE_SESSIONS`` NYSE sessions before ``target`` (0 if there
    are none). Both come from one grouped query.

    Completeness is per-date ticker coverage, not ``MAX(trade_date)``: a fresh
    prices run can still cover only part of the universe (e.g. IEX fallback).
    Gap-filled rows (``is_extrapolated``; NULL counts as real) are not counted.
    """
    from sqlalchemy import func, select

    from trading_signals.db.models.prices import PriceDaily
    from trading_signals.db.session import get_session
    from trading_signals.utils.market_calendar import trading_days

    # 21 calendar days always hold >= 5 sessions, even around holidays
    window = trading_days(target - timedelta(days=21), target - timedelta(days=1))
    baseline_days = window[-COVERAGE_BASELINE_SESSIONS:]
    with get_session() as session:
        rows = session.execute(
            select(PriceDaily.trade_date, func.count())
            .where(PriceDaily.trade_date.in_([target, *baseline_days]))
            .where(PriceDaily.is_extrapolated.is_not(True))
            .group_by(PriceDaily.trade_date)
        ).all()
    counts = dict(rows)
    baseline = max((counts.get(d, 0) for d in baseline_days), default=0)
    return counts.get(target, 0), baseline


def _coverage_pct(current: int, baseline: int) -> int:
    """``current`` as whole percent of ``baseline`` (rounded down; 0 if no baseline)."""
    return current * 100 // baseline if baseline else 0


def _critical_stale(target: date, stale: list[str]) -> list[str]:
    """Critical upstream problems for ``target`` (empty = features may run).

    Stale collectors flagged critical in ``NIGHTLY_UPSTREAM`` plus a price
    coverage check: the prices run may count as fresh (SUCCESS/PARTIAL) while
    the target session is still mostly missing. If the coverage query itself
    fails the inputs cannot be vouched for, so that counts as critical too
    (the chain must not crash before TA/target backfill/forecast review run).
    """
    critical = [n for n in stale if NIGHTLY_UPSTREAM.get(n)]
    if "prices_alpaca" not in critical:
        try:
            current, baseline = price_coverage(target)
        except Exception as e:
            logger.exception(
                f"[nightly_chain] price coverage check failed for {target}"
            )
            critical.append(
                f"prices_alpaca (coverage check failed: {type(e).__name__})"
            )
        else:
            if not coverage_ok(current, baseline):
                pct = _coverage_pct(current, baseline)
                critical.append(
                    f"prices_alpaca (coverage {current}/{baseline} = {pct}% "
                    f"of {COVERAGE_BASELINE_SESSIONS}-session max)"
                )
    return critical


def _skipped_step(job_id: str, collector_name: str, note: str) -> JobOutcome:
    """Record a chain step that is deliberately not run as a SKIPPED log row.

    Without the row the dashboard keeps showing the previous night's success
    as the step's last run.
    """
    return run_logged_job(
        job_id,
        collector_name,
        lambda: JobOutcome(status=job_status.SKIPPED, notes=note),
        alert=False,
    )


def _chain_done_for(target: date) -> bool:
    """True if a nightly_chain run already completed successfully for ``target``."""
    from sqlalchemy import select

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    with get_session() as session:
        row = session.execute(
            select(CollectionLog.id)
            .where(CollectionLog.collector_name == "nightly_chain")
            .where(CollectionLog.status == job_status.SUCCESS)
            .where(CollectionLog.notes.like(f"session={target.isoformat()}%"))
            .limit(1)
        ).first()
    return row is not None


def _catchup_window(target: date) -> list[date]:
    """The last ``CATCHUP_LOOKBACK_SESSIONS`` NYSE sessions before ``target``."""
    from trading_signals.utils.market_calendar import trading_days

    # 28 calendar days always hold >= 10 sessions, even around holidays
    window = trading_days(target - timedelta(days=28), target - timedelta(days=1))
    return window[-CATCHUP_LOOKBACK_SESSIONS:]


def snapshot_counts(sessions: list[date]) -> dict[date, int]:
    """Feature-snapshot row count per session date (one grouped query).

    Every date in ``sessions`` is a key; dates without snapshots count 0.
    """
    from sqlalchemy import func, select

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.db.session import get_session

    if not sessions:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(FeatureSnapshot.snapshot_date, func.count())
            .where(FeatureSnapshot.snapshot_date.in_(sessions))
            .group_by(FeatureSnapshot.snapshot_date)
        ).all()
    counts = dict(rows)
    return {d: counts.get(d, 0) for d in sessions}


def catchup_candidates(
    sessions: list[date],
    counts: dict[date, int],
    min_ratio: float = MIN_SNAPSHOT_COVERAGE,
) -> list[date]:
    """Sessions whose feature snapshots are missing, oldest first.

    The baseline is the maximum snapshot count over ``sessions``; a session is
    a candidate if its count (missing = 0) is below ``min_ratio`` of it. No
    baseline (nothing computed in the whole window, e.g. a fresh system) means
    no candidates: that is a job for the rebuild scripts, not the nightly chain.

    Completeness is per-date coverage, not run status or logs.
    """
    baseline = max((counts.get(d, 0) for d in sessions), default=0)
    if baseline <= 0:
        return []
    return [d for d in sorted(sessions) if counts.get(d, 0) / baseline < min_ratio]


def _find_catchup(target: date) -> tuple[list[date], str | None]:
    """Gap sessions before ``target`` as ``(candidates, error)``.

    A failing detection (DB, calendar) must not abort the chain: it is logged
    and returned as the exception type name (``candidates`` is empty then).
    """
    try:
        sessions = _catchup_window(target)
        return catchup_candidates(sessions, snapshot_counts(sessions)), None
    except Exception as e:
        logger.exception(f"[nightly_chain] catch-up detection failed for {target}")
        return [], type(e).__name__


def _run_catchup(
    candidates: list[date], detect_error: str | None = None
) -> tuple[str, bool]:
    """Build the missing feature snapshots of ``candidates`` (oldest first).

    A session is only processed once its own prices are complete
    (:func:`price_coverage` / :func:`coverage_ok`, checked per session);
    otherwise it is noted as ``pending (prices X%)`` and retried the next
    night (the original night already alerted). No context packs for past
    sessions. Nothing here raises: a failing session is logged, recorded as
    ``failed`` and the next one still runs.

    Returns ``(note, failed)``: ``note`` is ``catch-up: <date>=<result>, ...``
    (empty without candidates), ``failed`` is True if a session or the
    detection (``detect_error``) failed.
    """
    if detect_error:
        return f"catch-up: detection failed ({detect_error})", True
    if not candidates:
        return "", False
    results: list[str] = []
    failed = False
    for d in candidates:
        try:
            current, baseline = price_coverage(d)
            if coverage_ok(current, baseline):
                result = run_logged_job(
                    "feature_pipeline",
                    "feature_pipeline",
                    lambda d=d: _feature_pipeline_body(d),
                    alert=False,
                ).status
            else:
                result = f"pending (prices {_coverage_pct(current, baseline)}%)"
        except Exception:
            logger.exception(f"[nightly_chain] catch-up of {d} failed")
            result = job_status.FAILED
        failed = failed or result == job_status.FAILED
        results.append(f"{d.isoformat()}={result}")
    return "catch-up: " + ", ".join(results), failed


def _catchup_only_chain(
    target: date, candidates: list[date], detect_error: str | None
) -> JobOutcome:
    """Chain run for a session that is already processed: gaps only.

    TA catch-up, feature catch-up of earlier sessions and target backfill; no
    feature pipeline, forecast review or context pack for the target.
    """
    d = target.isoformat()
    if not candidates:
        # detection failed: nothing can run, but the failure must be visible
        note, _ = _run_catchup([], detect_error)
        return JobOutcome(
            status=job_status.PARTIAL, notes=f"session={d}; no new session; {note}"
        )
    logger.info(
        f"[nightly_chain] session {target} already processed – catch-up only "
        f"for {', '.join(c.isoformat() for c in candidates)}"
    )
    ta = run_logged_job(
        "technical_indicators_computer",
        "technical_indicators",
        _technical_indicators_body,
        alert=False,
    )
    catchup_note, catchup_failed = _run_catchup(candidates)
    tb = run_logged_job(
        "target_backfill", "target_backfill", _target_backfill_body, alert=False
    )
    steps = {"technical_indicators": ta, "target_backfill": tb}
    if catchup_failed or any(o.status != job_status.SUCCESS for o in steps.values()):
        overall = job_status.PARTIAL
    else:
        overall = job_status.SUCCESS
    notes = f"session={d}; catch-up only; " + ", ".join(
        f"{k}={o.status}" for k, o in steps.items()
    )
    notes += f"; {catchup_note}"
    failed_notes = [
        f"{k}: {o.notes}"
        for k, o in steps.items()
        if o.status == job_status.FAILED and o.notes
    ]
    if failed_notes:
        notes += "; " + "; ".join(failed_notes)
    return JobOutcome(status=overall, notes=notes)


def _nightly_chain_body() -> JobOutcome:
    target = _target_session()
    candidates, detect_error = _find_catchup(target)
    if _chain_done_for(target):
        if candidates or detect_error:
            return _catchup_only_chain(target, candidates, detect_error)
        logger.info(f"[nightly_chain] session {target} already processed – skipping")
        return JobOutcome(
            status=job_status.SKIPPED,
            notes=f"session={target.isoformat()}; no new session",
        )

    stale = wait_for_upstream(target)
    if stale:
        logger.warning(f"[nightly_chain] stale inputs for {target}: {', '.join(stale)}")
    critical = _critical_stale(target, stale)
    skip_note = f"critical input stale: {', '.join(critical)}"
    if critical:
        logger.warning(
            f"[nightly_chain] {skip_note} for {target} – "
            "skipping feature pipeline and context pack"
        )

    ta = run_logged_job(
        "technical_indicators_computer",
        "technical_indicators",
        _technical_indicators_body,
        alert=False,
    )
    # earlier gaps first, so the target's own row stays the last feature_pipeline
    # log entry; each gap session checks its own prices, also if the target's
    # prices are critical
    catchup_note, catchup_failed = _run_catchup(candidates, detect_error)
    # a feature row built on missing prices would silently carry the previous
    # session's price features, and the next night targets the next session
    if critical:
        fp = _skipped_step("feature_pipeline", "feature_pipeline", skip_note)
    else:
        fp = run_logged_job(
            "feature_pipeline",
            "feature_pipeline",
            lambda: _feature_pipeline_body(target),
            alert=False,
        )
    tb = run_logged_job(
        "target_backfill", "target_backfill", _target_backfill_body, alert=False
    )
    # after the target update, so newly realised returns are scored
    fr = run_logged_job(
        "forecast_review", "forecast_review", _forecast_review_body, alert=False
    )
    if critical:
        cp = _skipped_step(
            "context_pack_generator", "context_pack_generator", skip_note
        )
    elif fp.status in (job_status.SUCCESS, job_status.PARTIAL):
        cp = run_logged_job(
            "context_pack_generator",
            "context_pack_generator",
            lambda: _context_pack_body(target),
            alert=False,
        )
    else:
        cp = JobOutcome(
            status=job_status.SKIPPED, notes=f"feature pipeline {fp.status}"
        )

    steps = {
        "technical_indicators": ta,
        "feature_pipeline": fp,
        "target_backfill": tb,
        "forecast_review": fr,
        "context_pack": cp,
    }
    if critical or fp.status == job_status.FAILED:
        overall = job_status.FAILED
    elif (
        stale
        or catchup_failed
        or any(o.status != job_status.SUCCESS for o in steps.values())
    ):
        overall = job_status.PARTIAL
    else:
        overall = job_status.SUCCESS

    notes = f"session={target.isoformat()}; " + ", ".join(
        f"{k}={o.status}" for k, o in steps.items()
    )
    if critical:
        d = target.isoformat()
        notes += (
            f"; critical inputs stale: {', '.join(critical)}; "
            "features/context pack skipped – repair: automatic catch-up on the "
            f"next chain runs once prices are complete ({CATCHUP_LOOKBACK_SESSIONS} "
            "sessions), manually scripts/recompute_features.py "
            f"--start {d} --end {d} --all-trading-days"
        )
    if cp.notes and "pack kept" in cp.notes:
        notes += f"; context pack: {cp.notes.split('; ', 1)[1]}"
    if catchup_note:
        notes += f"; {catchup_note}"
    non_critical = [n for n in stale if not NIGHTLY_UPSTREAM.get(n)]
    if non_critical:
        notes += f"; stale inputs: {', '.join(non_critical)}"
    failed_notes = [
        f"{k}: {o.notes}"
        for k, o in steps.items()
        if o.status == job_status.FAILED and o.notes
    ]
    if failed_notes:
        notes += "; " + "; ".join(failed_notes)
    return JobOutcome(status=overall, records_written=fp.records_written, notes=notes)


def run_nightly_chain() -> None:
    """Nightly orchestrator: TA → features → targets → forecast review → context pack.

    Scheduled for 04:30 Europe/Berlin (after FRED at 04:15; options IV,
    short interest, estimates etc. ran earlier in the night).
    Target = last completed NYSE session; skipped if that session was already
    processed successfully (weekends/holidays) and there is nothing to catch
    up. Waits up to ``CHAIN_MAX_WAIT`` for upstream collectors that are still
    running, then proceeds and records stale inputs (status PARTIAL → alert).

    Critical inputs (``NIGHTLY_UPSTREAM`` ``True``, plus price coverage of the
    target session below ``MIN_PRICE_COVERAGE`` of the max of the last
    ``COVERAGE_BASELINE_SESSIONS`` sessions, or a failing coverage check) are
    different: feature pipeline and context pack are skipped (recorded as
    SKIPPED ``collection_log`` rows), the other steps still run, and the
    chain ends FAILED (alert). A FAILED chain is not "done", so re-running it
    for the same session once prices are complete repairs the gap.

    Automatic catch-up: the next night already targets the next session, so a
    skipped session would stay a gap. Therefore every run also checks the
    ``CATCHUP_LOOKBACK_SESSIONS`` sessions before the target for missing
    feature snapshots (per-date row count below ``MIN_SNAPSHOT_COVERAGE`` of
    the window maximum, see :func:`catchup_candidates`). Gap sessions whose own
    prices are complete get their feature pipeline run (oldest first, after
    TA, before the target backfill, no context packs); incomplete ones are
    noted as ``pending`` and retried the next night. A failed catch-up makes
    the chain at least PARTIAL; it never aborts the chain. If the target is
    already processed but gaps exist, the run does only TA, catch-up and
    target backfill (``catch-up only``) and ends SUCCESS or PARTIAL.
    """
    run_logged_job("nightly_chain", "nightly_chain", _nightly_chain_body)


# ── Index Sync ───────────────────────────────────────────────────────────


def _index_sync_body() -> JobOutcome:
    from sqlalchemy import select

    from trading_signals.db.models.universe import Universe
    from trading_signals.db.session import get_session
    from trading_signals.scheduler.sector_enrichment import enrich_sectors
    from trading_signals.universe.index_sync import IndexSyncer

    collector_name = "index_sync"
    records_written = 0

    # Step 1: Sync index membership
    with get_session() as session:
        result = IndexSyncer(session).sync()
        session.commit()
        records_written += result.newly_added + result.membership_updated
        logger.info(
            f"[{collector_name}] S&P 500={result.sp500_count}, "
            f"Nasdaq 100={result.nasdaq100_count}, "
            f"added={result.newly_added}, updated={result.membership_updated}"
        )
        if result.new_tickers:
            logger.info(
                f"[{collector_name}] new tickers: {', '.join(result.new_tickers)}"
            )
        notes = [
            f"S&P 500={result.sp500_count}, Nasdaq 100={result.nasdaq100_count}, "
            f"added={result.newly_added}, updated={result.membership_updated}"
        ]
        if result.renamed:
            notes.append("renamed (interval re-keyed): " + ", ".join(result.renamed))
        if result.date_fallbacks:
            notes.append(
                "sync date used (not in changes table): "
                + ", ".join(result.date_fallbacks)
            )

    # Step 2: Enrich tickers missing sector/industry data
    with get_session() as session:
        missing = [
            row[0]
            for row in session.execute(
                select(Universe.ticker)
                .where(Universe.is_active.is_(True))
                .where((Universe.sector.is_(None)) | (Universe.sector == ""))
                .order_by(Universe.ticker)
            ).all()
        ]
    if missing:
        enrichment = enrich_sectors(missing, source=collector_name)
        records_written += enrichment.enriched
    else:
        logger.info(f"[{collector_name}] all tickers have sector data")
    return JobOutcome(records_written=records_written, notes="; ".join(notes))


def run_index_sync() -> None:
    """Monthly index membership sync + sector enrichment.

    Scheduled for 1st of each month at 03:00 Europe/Berlin.
    Updates S&P 500 / Nasdaq 100 membership from Wikipedia,
    validates new tickers against Alpaca, adds them to the
    universe, and enriches any tickers missing sector data.
    """
    run_logged_job("index_sync", "index_sync", _index_sync_body)


# ── Universe Validation ──────────────────────────────────────────────────


def _universe_validation_body() -> JobOutcome:
    from trading_signals.db.session import get_session
    from trading_signals.universe.validation import validate_universe

    with get_session() as session:
        result = validate_universe(session)
    return JobOutcome(
        # warnings (open index interval on a delisted ticker, rename target
        # missing from the universe) need a human or the monthly index sync
        status=job_status.PARTIAL if result.warnings else job_status.SUCCESS,
        records_written=len(result.deactivated),
        records_fetched=result.checked,
        notes=result.notes(),
    )


def run_universe_validation() -> None:
    """Weekly validation of the active universe against Alpaca assets.

    Scheduled for Sundays at 13:00 Europe/Berlin (after the weekly
    collectors). Deactivates active tickers that Alpaca no longer lists as
    tradable -- except tickers with an open ``index_membership`` interval,
    which only produce a warning (PARTIAL) -- and notes renames/mergers from
    Alpaca corporate actions in the run notes. Not an upstream of the nightly
    chain.
    """
    run_logged_job(
        "universe_validation", "universe_validation", _universe_validation_body
    )


# ── Log Retention ────────────────────────────────────────────────────────

LOG_RETENTION_DAYS = 90


def _log_retention_body() -> JobOutcome:
    from sqlalchemy import delete

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    stale = mark_stale_runs()
    cutoff = now_local() - timedelta(days=LOG_RETENTION_DAYS)
    with get_session() as session:
        deleted = (
            session.execute(
                delete(CollectionLog).where(CollectionLog.started_at < cutoff)
            ).rowcount
            or 0
        )
    logger.info(
        f"[log_retention] {deleted} log entries older than {cutoff.date()} deleted"
    )
    return JobOutcome(
        records_written=deleted,
        notes=f"deleted={deleted} (older than {cutoff.date()}), stale_marked={stale}",
    )


def run_log_retention() -> None:
    """Delete collection_logs older than LOG_RETENTION_DAYS.

    Scheduled for 03:30 Europe/Berlin (daily).
    Keeps the database lean by pruning old log entries and marks orphaned
    RUNNING rows (process crash/restart) as failed.
    """
    run_logged_job("log_retention", "log_retention", _log_retention_body)


# ── Data Retention ───────────────────────────────────────────────────────


def retention_statements(cutoff: date) -> list[tuple[str, object]]:
    """DELETE statements of the data-retention job, in FK-safe order.

    Date columns are chosen so that rows with garbage *event* dates are not
    lost early: insider trades by ``filing_date`` (transaction_date contains
    bogus years), politician trades by ``disclosure_date``, 13F by
    ``filing_date``. News sentiment is deleted via its article (FK); SEC
    filings by ``filing_date`` (their text excerpts via the filing, deleted
    first), quarterly financials by ``period_end``.
    """
    from sqlalchemy import delete, select

    from trading_signals.db.models.analysis import AnalysisReport
    from trading_signals.db.models.ark import ARKDelta, ARKHolding
    from trading_signals.db.models.estimates import EstimatesSnapshot
    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.db.models.form13f import Form13FHolding
    from trading_signals.db.models.fundamentals import (
        AnalystRating,
        FundamentalsSnapshot,
    )
    from trading_signals.db.models.insider import InsiderCluster, InsiderTrade
    from trading_signals.db.models.macro_series import MacroSeries
    from trading_signals.db.models.news import NewsArticle, NewsSentiment
    from trading_signals.db.models.options_iv import OptionsIVSnapshot
    from trading_signals.db.models.politicians import PoliticianTrade
    from trading_signals.db.models.prices import PriceDaily
    from trading_signals.db.models.sec import (
        FinancialsQuarterly,
        SecFiling,
        SecFilingText,
    )
    from trading_signals.db.models.short_interest import ShortInterest, ShortVolume
    from trading_signals.db.models.technical_indicators import TechnicalIndicator

    old_articles = select(NewsArticle.id).where(NewsArticle.published_at < cutoff)
    old_filings = select(SecFiling.accession_number).where(
        SecFiling.filing_date < cutoff
    )
    simple = [
        ("news_articles", NewsArticle, NewsArticle.published_at),
        ("prices_daily", PriceDaily, PriceDaily.trade_date),
        ("technical_indicators", TechnicalIndicator, TechnicalIndicator.trade_date),
        ("insider_trades", InsiderTrade, InsiderTrade.filing_date),
        ("insider_clusters", InsiderCluster, InsiderCluster.cluster_start),
        ("analyst_ratings", AnalystRating, AnalystRating.rating_date),
        ("ark_holdings", ARKHolding, ARKHolding.snapshot_date),
        ("ark_deltas", ARKDelta, ARKDelta.delta_date),
        (
            "fundamentals_snapshot",
            FundamentalsSnapshot,
            FundamentalsSnapshot.snapshot_date,
        ),
        ("form13f_holdings", Form13FHolding, Form13FHolding.filing_date),
        ("politician_trades", PoliticianTrade, PoliticianTrade.disclosure_date),
        ("feature_snapshots", FeatureSnapshot, FeatureSnapshot.snapshot_date),
        ("options_iv_snapshot", OptionsIVSnapshot, OptionsIVSnapshot.snapshot_date),
        ("estimates_snapshot", EstimatesSnapshot, EstimatesSnapshot.as_of),
        ("short_volume", ShortVolume, ShortVolume.trade_date),
        ("short_interest", ShortInterest, ShortInterest.settlement_date),
        ("macro_series", MacroSeries, MacroSeries.obs_date),
        ("analysis_reports", AnalysisReport, AnalysisReport.report_date),
        ("sec_filings", SecFiling, SecFiling.filing_date),
        ("financials_quarterly", FinancialsQuarterly, FinancialsQuarterly.period_end),
    ]
    stmts: list[tuple[str, object]] = [
        (
            "news_sentiment",
            delete(NewsSentiment).where(NewsSentiment.article_id.in_(old_articles)),
        ),
        # texts before their filings (the FK would cascade anyway; this keeps
        # the per-table counts of the run notes)
        (
            "sec_filing_texts",
            delete(SecFilingText).where(
                SecFilingText.accession_number.in_(old_filings)
            ),
        ),
    ]
    stmts += [
        (label, delete(model).where(col < cutoff)) for label, model, col in simple
    ]
    return [
        (label, stmt.execution_options(synchronize_session=False))
        for label, stmt in stmts
    ]


def purge_old_data(cutoff: date) -> tuple[dict[str, int], dict[str, str]]:
    """Execute the retention deletes; each table in its own savepoint + commit.

    A failing table is rolled back on its own and does not undo or hide the
    deletes of other tables. Returns ``(deleted_per_table, errors_per_table)``.
    """
    from trading_signals.db.session import get_session

    deleted: dict[str, int] = {}
    errors: dict[str, str] = {}
    with get_session() as session:
        for label, stmt in retention_statements(cutoff):
            try:
                with session.begin_nested():
                    count = session.execute(stmt).rowcount or 0
                session.commit()
            except Exception as e:
                session.rollback()
                errors[label] = f"{type(e).__name__}: {e}"[:300]
                logger.warning(f"[data_retention] {label} — skipped: {errors[label]}")
                continue
            deleted[label] = count
            if count:
                logger.info(
                    f"[data_retention] {label} — {count} rows deleted (< {cutoff})"
                )
    return deleted, errors


def _data_retention_body() -> JobOutcome:
    cutoff = quarter_cutoff(DATA_RETENTION_QUARTERS)
    logger.info(
        f"[data_retention] cutoff={cutoff}, "
        f"retention={DATA_RETENTION_QUARTERS} quarters"
    )
    deleted, errors = purge_old_data(cutoff)
    total = sum(deleted.values())
    if errors and not deleted:
        status = job_status.FAILED
    elif errors:
        status = job_status.PARTIAL
    else:
        status = job_status.SUCCESS
    notes = f"cutoff={cutoff}; deleted={total}"
    if errors:
        notes += "; failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items())
    return JobOutcome(status=status, records_written=total, notes=notes)


def run_data_retention() -> None:
    """Delete data older than DATA_RETENTION_QUARTERS from all tables.

    Scheduled for Sunday 03:00 Europe/Berlin (weekly).
    Excludes: earnings_calendar (historical value), collection_log
    (has its own 90-day retention), universe/blacklist/index_membership, and
    the forecast evaluation records ``forecast_reviews``, ``forecasts``,
    ``forecast_criterion_samples``, ``forecast_file_seals`` and
    ``pack_candidates`` (research results of a pre-registered test, written
    once and never purged: ``retention_statements`` lists the purged tables
    explicitly and none of these is among them).

    Uses quarter-based cutoff so boundaries align with calendar quarters.
    """
    run_logged_job("data_retention", "data_retention", _data_retention_body)


# ── Feature Analysis ─────────────────────────────────────────────────────


def _feature_analysis_body() -> JobOutcome:
    from trading_signals.analysis.feature_report import FeatureAnalysisEngine
    from trading_signals.db.session import get_session

    with get_session() as session:
        report = FeatureAnalysisEngine(session).run()
        if report is None:
            logger.warning("[feature_analysis] insufficient data (<30 dates), skipped")
            return JobOutcome(
                status=job_status.SKIPPED,
                notes="Insufficient snapshot data (<30 distinct dates)",
            )
        # Extract values while still in session context
        snap_count = report.snapshot_count
        tick_count = report.ticker_count
        comp_time = report.computation_time_seconds or 0.0

    logger.info(
        f"[feature_analysis] {snap_count} snapshots analyzed, "
        f"{tick_count} tickers, {comp_time:.0f}s"
    )
    return JobOutcome(records_written=1, records_fetched=snap_count)


def run_feature_analysis() -> None:
    """Monthly feature analysis – correlations, importance, hypothesis tests.

    Scheduled for 1st of each month at 07:00 Europe/Berlin (after the
    nightly chain). Also triggered via ``POST /analysis/trigger``.
    Runs Spearman correlations, Random Forest + LASSO feature importance,
    and hypothesis tests (H1–H13 exploratory, H14 pre-registered and tracked
    automatically, see LEARNINGS_HYPOTHESES.md). Stores structured results in
    analysis_reports table and generates an HTML report.

    CPU-intensive (~2–5 min depending on data volume).
    """
    run_logged_job("feature_analysis", "feature_analysis", _feature_analysis_body)
