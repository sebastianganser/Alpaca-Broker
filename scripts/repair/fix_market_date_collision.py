"""Close the IV/estimates gap of Monday 2026-10-05 caused by the H3 relabel.

Why: before the deploy on 2026-10-05 the old code labelled options-IV and
estimates rows with the Berlin fetch date. The early-morning runs on
2026-10-05 therefore stored FRIDAY's data (session 2026-10-02) under the
label ``2026-10-05``. The new code keys on ``last_completed_session()``
(= 2026-10-05 for the Monday-evening/Tuesday-night runs), found that label
already ≥ 90 % filled and skipped – Monday's IV and estimates are missing.

This script

  1. relabels the stale ``2026-10-05`` rows to ``2026-10-02`` (the session
     they actually describe). Rows whose key already exists under
     ``2026-10-02`` are deleted instead (that session is already covered).
       * options_iv_snapshot: all rows with snapshot_date = 2026-10-05
         (refused if a successful post-deploy options run exists – then
         those rows could already be genuine Monday data).
       * estimates_snapshot: rows with as_of = 2026-10-05 and
         fetched_at < 2026-10-05 12:00 (old code only).
  2. (``--rerun``) re-runs OptionsIVCollector and EstimatesCollector, which
     now fetch session 2026-10-05. Must happen BEFORE the next US close
     (Tue 2026-10-06 22:00 Berlin) – afterwards ``last_completed_session``
     moves on and Yahoo no longer shows Monday's chain/estimates.
     Ideally before the US open (15:30 Berlin) so the IV values are
     Monday's closing values.
  3. (``--context-packs 2026-10-02,2026-10-05``) regenerates context packs
     for the given session dates (failed due to file permissions).

Default is a DRY RUN that only prints statistics.

Usage (inside the container):
    .venv/bin/python scripts/repair/fix_market_date_collision.py
    .venv/bin/python scripts/repair/fix_market_date_collision.py --apply --rerun
    .venv/bin/python scripts/repair/fix_market_date_collision.py --context-packs 2026-10-02,2026-10-05
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("repair.market_date_collision")

STALE_LABEL = date(2026, 10, 5)
TRUE_SESSION = date(2026, 10, 2)
#: Old-code estimates rows were fetched 2026-10-05 01:30 Berlin.
ESTIMATES_FETCHED_BEFORE = "2026-10-05 12:00:00"
#: Deploy of the new labelling code (collection_log.started_at is tz-aware).
DEPLOY_CUTOFF = "2026-10-05 12:00:00+02"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="Relabel rows (default: dry run)")
    p.add_argument(
        "--rerun", action="store_true",
        help="After relabelling: re-run options IV + estimates collectors",
    )
    p.add_argument(
        "--context-packs", default="",
        help="Comma-separated session dates to regenerate context packs for",
    )
    return p.parse_args(argv)


def parse_dates(value: str) -> list[date]:
    return [date.fromisoformat(v.strip()) for v in value.split(",") if v.strip()]


# ── SQL ──────────────────────────────────────────────────────────────────

OPTIONS_STATS = """
    SELECT count(*) AS n,
           count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM signals.options_iv_snapshot o2
               WHERE o2.ticker = o.ticker AND o2.snapshot_date = :true_session
           )) AS collide
    FROM signals.options_iv_snapshot o
    WHERE o.snapshot_date = :stale
"""
OPTIONS_DELETE = """
    DELETE FROM signals.options_iv_snapshot o
    WHERE o.snapshot_date = :stale
      AND EXISTS (SELECT 1 FROM signals.options_iv_snapshot o2
                  WHERE o2.ticker = o.ticker AND o2.snapshot_date = :true_session)
"""
OPTIONS_UPDATE = """
    UPDATE signals.options_iv_snapshot SET snapshot_date = :true_session
    WHERE snapshot_date = :stale
"""

ESTIMATES_FILTER = "e.as_of = :stale AND e.fetched_at < CAST(:fetched_before AS timestamp)"
ESTIMATES_STATS = f"""
    SELECT count(*) AS n,
           count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM signals.estimates_snapshot e2
               WHERE e2.ticker = e.ticker AND e2.period = e.period
                 AND e2.source = e.source AND e2.as_of = :true_session
           )) AS collide
    FROM signals.estimates_snapshot e
    WHERE {ESTIMATES_FILTER}
"""
ESTIMATES_DELETE = f"""
    DELETE FROM signals.estimates_snapshot e
    WHERE {ESTIMATES_FILTER}
      AND EXISTS (SELECT 1 FROM signals.estimates_snapshot e2
                  WHERE e2.ticker = e.ticker AND e2.period = e.period
                    AND e2.source = e.source AND e2.as_of = :true_session)
"""
ESTIMATES_UPDATE = f"""
    UPDATE signals.estimates_snapshot e SET as_of = :true_session
    WHERE {ESTIMATES_FILTER}
"""

POST_DEPLOY_RUNS = """
    SELECT count(*) FROM signals.collection_log
    WHERE collector_name = :name AND coalesce(records_written, 0) > 0
      AND started_at >= CAST(:cutoff AS timestamptz)
"""


def _params() -> dict:
    return {
        "stale": STALE_LABEL,
        "true_session": TRUE_SESSION,
        "fetched_before": ESTIMATES_FETCHED_BEFORE,
    }


def print_stats(session) -> tuple[int, int]:
    from sqlalchemy import text

    p = _params()
    o_n, o_col = session.execute(text(OPTIONS_STATS), p).one()
    e_n, e_col = session.execute(text(ESTIMATES_STATS), p).one()
    logger.info(
        f"options_iv_snapshot {STALE_LABEL}: {o_n} stale rows "
        f"({o_col} collide with {TRUE_SESSION} → delete, {o_n - o_col} → relabel)"
    )
    logger.info(
        f"estimates_snapshot as_of {STALE_LABEL} (fetched < {ESTIMATES_FETCHED_BEFORE}): "
        f"{e_n} stale rows ({e_col} collide → delete, {e_n - e_col} → relabel)"
    )
    return o_n, e_n


def post_deploy_runs(session, collector_name: str) -> int:
    from sqlalchemy import text

    return session.execute(
        text(POST_DEPLOY_RUNS), {"name": collector_name, "cutoff": DEPLOY_CUTOFF}
    ).scalar_one()


def relabel(session) -> None:
    from sqlalchemy import text

    p = _params()
    if post_deploy_runs(session, "options_iv_collector"):
        logger.warning(
            "options_iv_collector already wrote rows after the deploy – the "
            f"{STALE_LABEL} rows may be genuine Monday data. Skipping options relabel."
        )
    else:
        d = session.execute(text(OPTIONS_DELETE), p).rowcount
        u = session.execute(text(OPTIONS_UPDATE), p).rowcount
        logger.info(f"options_iv_snapshot: {d} deleted, {u} relabelled → {TRUE_SESSION}")
    d = session.execute(text(ESTIMATES_DELETE), p).rowcount
    u = session.execute(text(ESTIMATES_UPDATE), p).rowcount
    logger.info(f"estimates_snapshot: {d} deleted, {u} relabelled → {TRUE_SESSION}")
    session.commit()


def rerun_collectors() -> None:
    from trading_signals.collectors.estimates_collector import EstimatesCollector
    from trading_signals.collectors.options_iv_collector import OptionsIVCollector

    for cls in (OptionsIVCollector, EstimatesCollector):
        log = cls().run()
        logger.info(
            f"{cls.name}: status={log.status} fetched={log.records_fetched} "
            f"written={log.records_written} notes={log.notes}"
        )


def regenerate_context_packs(dates: list[date]) -> None:
    from trading_signals.db.session import get_session
    from trading_signals.derived.context_pack_generator import ContextPackGenerator

    for d in dates:
        with get_session() as session:
            written = ContextPackGenerator(session).generate_daily(d)
        logger.info(f"context pack {d}: {written} files written")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.db.session import get_session
    from trading_signals.utils import market_calendar

    pack_dates = parse_dates(args.context_packs)
    session_now = market_calendar.last_completed_session()
    logger.info(f"last_completed_session() = {session_now}")

    with get_session() as session:
        print_stats(session)

    if args.apply or args.rerun:
        if session_now != STALE_LABEL:
            logger.error(
                f"last_completed_session() is {session_now}, not {STALE_LABEL}: "
                "Monday's data can no longer be fetched – aborting relabel/rerun."
            )
            return 1
        if args.apply:
            with get_session() as session:
                relabel(session)
            with get_session() as session:
                print_stats(session)
        if args.rerun:
            rerun_collectors()
    else:
        logger.info("DRY RUN – use --apply --rerun to relabel and re-fetch.")

    if pack_dates:
        regenerate_context_packs(pack_dates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
