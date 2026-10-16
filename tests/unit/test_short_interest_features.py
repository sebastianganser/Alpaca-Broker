"""Tests for the FINRA short-interest features (migration 036, no DB).

Covers the availability rule (9-session publication lag incl. holidays),
the max age, the 1-month change edge cases, NULL handling, the equality of
the pure (nightly) and vectorised (backfill) implementations, the pipeline
wiring and the backfill script helpers.
"""

from __future__ import annotations

import importlib.util
import math
import random
import re
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trading_signals.analysis.feature_groups import feature_group_of
from trading_signals.db.models.features import FEATURE_COLUMNS, FeatureSnapshot
from trading_signals.derived.feature_pipeline import (
    FEATURE_VERSION,
    FeaturePipeline,
    build_upsert_values,
)
from trading_signals.derived.short_interest_features import (
    SETTLEMENT_COLUMNS,
    SHORT_INTEREST_COLUMNS,
    SI_CHANGE_MAX_SPAN_DAYS,
    SI_LOAD_WINDOW_DAYS,
    SI_MAX_AGE_DAYS,
    SI_PUBLICATION_LAG_SESSIONS,
    compute_short_interest_frame,
    publication_cutoff,
    publication_cutoffs,
    short_interest_features_at,
)
from trading_signals.utils.market_calendar import previous_trading_day, trading_days

ROOT = Path(__file__).resolve().parents[2]
DTC, CHG = SHORT_INTEREST_COLUMNS


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backfill = _load("scripts/repair/backfill_short_interest_features.py", "si_backfill")
mig036 = _load("src/alembic/versions/036_short_interest_features.py", "mig036")

# Cutoff of d = 2026-10-16 is 2026-10-05 (9 sessions back, Columbus Day open).
D = date(2026, 10, 16)
CUTOFF = date(2026, 10, 5)


def _row(settlement: date, si=1000, dtc=2.0):
    return (settlement, si, dtc)


def _quad(newest_si=1100, newest_dtc=2.5):
    """Four usable settlements; the comparison row (2 before the newest) is
    2026-08-31 with 1000."""
    return [
        _row(date(2026, 8, 14), 900, 1.0),
        _row(date(2026, 8, 31), 1000, 1.5),
        _row(date(2026, 9, 15), 1050, 2.0),
        _row(date(2026, 9, 30), newest_si, newest_dtc),
    ]


# ── Definitions ──────────────────────────────────────────────────────


class TestDefinitions:
    def test_constants(self):
        assert SI_PUBLICATION_LAG_SESSIONS == 9
        assert SI_MAX_AGE_DAYS == 40
        assert SI_CHANGE_MAX_SPAN_DAYS == 45
        assert SI_LOAD_WINDOW_DAYS == 85

    def test_columns_registered_and_grouped(self):
        assert SHORT_INTEREST_COLUMNS == (
            "short_interest_days_to_cover",
            "short_interest_change_1m",
        )
        for col in SHORT_INTEREST_COLUMNS:
            assert col in FEATURE_COLUMNS
            assert feature_group_of(col) == "Short Interest"

    def test_orm_columns_are_numeric_10_4_nullable(self):
        for col in SHORT_INTEREST_COLUMNS:
            c = FeatureSnapshot.__table__.columns[col]
            assert c.nullable is True
            assert (c.type.precision, c.type.scale) == (10, 4)

    def test_feature_version_bumped(self):
        assert FEATURE_VERSION == "2026.10-3"

    def test_upsert_values_contain_the_new_columns(self):
        values = build_upsert_values("AAPL", D, {DTC: 3.1})
        assert values[DTC] == 3.1
        assert values[CHG] is None
        assert values["feature_version"] == "2026.10-3"


# ── Availability cutoff (lag in NYSE sessions, holidays) ─────────────


class TestPublicationCutoff:
    @pytest.mark.parametrize(
        ("d", "expected"),
        [
            # plain week, Columbus Day is an NYSE session
            (date(2026, 10, 16), date(2026, 10, 5)),
            # Monday: skips the weekend
            (date(2026, 10, 19), date(2026, 10, 6)),
            # Labor Day 2026-09-07: a calendar-weekday count would give 08-24
            (date(2026, 9, 8), date(2026, 8, 25)),
            # Good Friday 2026-04-03
            (date(2026, 4, 7), date(2026, 3, 24)),
            # Independence Day observed Fri 2026-07-03
            (date(2026, 7, 6), date(2026, 6, 22)),
            # Thanksgiving 2026-11-26 (Friday after is a session)
            (date(2026, 11, 30), date(2026, 11, 16)),
            # Christmas / year end
            (date(2025, 12, 26), date(2025, 12, 12)),
        ],
    )
    def test_nine_sessions_back(self, d, expected):
        assert publication_cutoff(d) == expected

    def test_non_session_day_uses_sessions_strictly_before(self):
        # Saturday 2026-10-17 -> same as the next session after it would NOT
        # shift the window: 9th session strictly before the Saturday.
        assert publication_cutoff(date(2026, 10, 17)) == date(2026, 10, 6)

    def test_matches_stepwise_reference_for_a_year(self):
        days = [date(2025, 1, 1) + timedelta(days=i) for i in range(400)]
        got = publication_cutoffs(days)

        def reference(d: date) -> date:
            x = d
            for _ in range(SI_PUBLICATION_LAG_SESSIONS):
                x = previous_trading_day(x)
            return x

        for d in days[::7] + [date(2025, 12, 26), date(2026, 1, 2)]:
            assert got[d] == reference(d), d

    def test_cutoff_is_always_a_session_before_d(self):
        sessions = set(trading_days(date(2025, 6, 1), date(2026, 6, 1)))
        days = trading_days(date(2025, 9, 1), date(2026, 3, 1))
        for d, c in publication_cutoffs(days).items():
            assert c in sessions and c < d

    def test_empty(self):
        assert publication_cutoffs([]) == {}


# ── Pure implementation ──────────────────────────────────────────────


class TestAvailability:
    def test_settlement_exactly_at_cutoff_is_usable(self):
        out = short_interest_features_at([_row(CUTOFF, 1000, 4.0)], D)
        assert out[DTC] == 4.0

    def test_settlement_after_cutoff_is_not_usable(self):
        # one day newer than the cutoff would be lookahead
        rows = [_row(CUTOFF + timedelta(days=1), 1000, 4.0)]
        assert short_interest_features_at(rows, D) == {DTC: None, CHG: None}

    def test_newer_settlement_is_ignored_in_favour_of_usable_one(self):
        rows = [_row(date(2026, 9, 15), 1000, 1.5), _row(date(2026, 9, 30), 1, 9.9)]
        # d = 2026-10-12 -> cutoff 2026-09-29: only 09-15 is usable
        out = short_interest_features_at(rows, date(2026, 10, 12))
        assert out[DTC] == 1.5
        # next session: cutoff 2026-09-30, the 09-30 settlement becomes usable
        out = short_interest_features_at(rows, date(2026, 10, 13))
        assert out[DTC] == 9.9

    def test_holiday_shifts_availability(self):
        # Settlement 2026-08-24 is usable on 2026-09-08 only if the lag is
        # counted in sessions: Labor Day (09-07) must not count as a session.
        rows = [_row(date(2026, 8, 24), 1000, 3.0)]
        assert short_interest_features_at(rows, date(2026, 9, 8))[DTC] == 3.0
        rows = [_row(date(2026, 8, 26), 1000, 3.0)]  # after cutoff 08-25
        assert short_interest_features_at(rows, date(2026, 9, 8))[DTC] is None

    def test_unsorted_input_and_precomputed_cutoff(self):
        rows = list(reversed(_quad()))
        assert short_interest_features_at(rows, D) == short_interest_features_at(
            _quad(), D, cutoff=CUTOFF
        )

    def test_no_rows(self):
        assert short_interest_features_at([], D) == {DTC: None, CHG: None}

    def test_always_returns_both_keys(self):
        out = short_interest_features_at(_quad(), D)
        assert set(out) == set(SHORT_INTEREST_COLUMNS)

    def test_duplicate_settlement_last_row_wins(self):
        rows = [_row(date(2026, 9, 30), 1, 1.0), _row(date(2026, 9, 30), 2, 7.0)]
        assert short_interest_features_at(rows, D)[DTC] == 7.0


class TestMaxAge:
    def test_exactly_40_days_before_cutoff_is_fresh(self):
        s = CUTOFF - timedelta(days=SI_MAX_AGE_DAYS)
        assert short_interest_features_at([_row(s, 1000, 2.0)], D)[DTC] == 2.0

    def test_41_days_before_cutoff_is_stale(self):
        s = CUTOFF - timedelta(days=SI_MAX_AGE_DAYS + 1)
        rows = [_row(s - timedelta(days=30), 900, 1.0), _row(s, 1000, 2.0)]
        assert short_interest_features_at(rows, D) == {DTC: None, CHG: None}

    def test_stale_newest_nulls_change_too(self):
        # Three old settlements, all stale at D
        rows = [
            _row(date(2026, 7, 15), 1000, 1.0),
            _row(date(2026, 7, 31), 1100, 1.1),
            _row(date(2026, 8, 14), 1200, 1.2),
        ]
        assert short_interest_features_at(rows, D) == {DTC: None, CHG: None}


class TestDaysToCover:
    def test_newest_row_value(self):
        assert short_interest_features_at(_quad(), D)[DTC] == 2.5

    def test_null_stays_null_and_is_not_filled_from_older_row(self):
        out = short_interest_features_at(_quad(newest_dtc=None), D)
        assert out[DTC] is None
        # the change feature is independent of days_to_cover
        assert out[CHG] == pytest.approx(0.1)

    def test_decimal_and_nan_inputs(self):
        rows = [_row(date(2026, 9, 30), 5, Decimal("1.23456"))]
        assert short_interest_features_at(rows, D)[DTC] == 1.2346
        rows = [_row(date(2026, 9, 30), 5, float("nan"))]
        assert short_interest_features_at(rows, D)[DTC] is None

    def test_zero_is_a_value(self):
        rows = [_row(date(2026, 9, 30), 5, 0)]
        assert short_interest_features_at(rows, D)[DTC] == 0.0


class TestChange1m:
    def test_two_settlements_earlier(self):
        out = short_interest_features_at(_quad(newest_si=1100), D)
        # newest 1100 vs. the settlement two earlier (08-31: 1000), not the
        # previous one (09-15: 1050) and not the oldest (08-14: 900)
        assert out[CHG] == 0.1

    def test_rounded_to_four_decimals(self):
        rows = [
            _row(date(2026, 8, 31), 3),
            _row(date(2026, 9, 15), 1),
            _row(date(2026, 9, 30), 4),
        ]
        assert short_interest_features_at(rows, D)[CHG] == 0.3333

    def test_decrease_and_shrink_to_zero(self):
        assert short_interest_features_at(_quad(newest_si=500), D)[CHG] == -0.5
        assert short_interest_features_at(_quad(newest_si=0), D)[CHG] == -1.0

    def test_less_than_three_usable_settlements(self):
        assert short_interest_features_at(_quad()[2:], D)[CHG] is None

    def test_earlier_settlement_exactly_45_days_back_is_ok(self):
        newest = date(2026, 9, 30)
        rows = [
            _row(newest - timedelta(days=SI_CHANGE_MAX_SPAN_DAYS), 1000),
            _row(newest - timedelta(days=20), 1000),
            _row(newest, 1500),
        ]
        assert short_interest_features_at(rows, D)[CHG] == 0.5

    def test_earlier_settlement_46_days_back_is_null(self):
        newest = date(2026, 9, 30)
        rows = [
            _row(newest - timedelta(days=SI_CHANGE_MAX_SPAN_DAYS + 1), 1000),
            _row(newest - timedelta(days=20), 1000),
            _row(newest, 1500),
        ]
        out = short_interest_features_at(rows, D)
        assert out[CHG] is None
        assert out[DTC] == 2.0  # days_to_cover is unaffected

    @pytest.mark.parametrize("old", [None, 0, -5])
    def test_earlier_short_interest_missing_or_not_positive(self, old):
        rows = _quad()
        rows[1] = _row(rows[1][0], old, 2.0)
        assert short_interest_features_at(rows, D)[CHG] is None

    def test_newest_short_interest_null(self):
        assert short_interest_features_at(_quad(newest_si=None), D)[CHG] is None

    def test_gap_in_history_uses_position_not_calendar(self):
        # 08-31 is missing: "2 usable settlements earlier" is 08-14 (47 days)
        rows = [
            _row(date(2026, 8, 14), 1000),
            _row(date(2026, 9, 15), 1100),
            _row(date(2026, 9, 30), 1200),
        ]
        assert short_interest_features_at(rows, D)[CHG] is None

    def test_loading_only_the_window_gives_the_same_result(self):
        # the pipeline loads [cutoff - SI_LOAD_WINDOW_DAYS, cutoff] only
        full = [
            _row(date(2026, 1, 15) + timedelta(days=15 * i), 1000 + 7 * i, 1.0 + i)
            for i in range(18)
        ]
        days = trading_days(date(2026, 8, 3), date(2026, 10, 16))
        for d, c in publication_cutoffs(days).items():
            lower = c - timedelta(days=SI_LOAD_WINDOW_DAYS)
            window = [r for r in full if lower <= r[0] <= c]
            assert len(window) < len(full)
            assert short_interest_features_at(window, d, cutoff=c) == (
                short_interest_features_at(full, d, cutoff=c)
            ), d


# ── Pure == vectorised ───────────────────────────────────────────────


def _synthetic(seed: int = 3):
    """Semi-monthly settlements for 6 tickers with gaps, NULLs and zeros."""
    rng = random.Random(seed)
    settle_dates = []
    for y, m in [(y, m) for y in (2024, 2025, 2026) for m in range(1, 13)]:
        if (y, m) > (2026, 9):
            break
        settle_dates += [date(y, m, 15), date(y, m, 28)]
    rows = []
    for t in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        si = rng.randint(1_000, 50_000)
        for s in settle_dates:
            if rng.random() < 0.12:  # missing settlement (gap)
                continue
            si = max(0, int(si * rng.uniform(0.6, 1.5)))
            r = rng.random()
            row_si = None if r < 0.06 else (0 if r < 0.12 else si)
            row_dtc = None if rng.random() < 0.1 else round(rng.uniform(0.1, 30), 4)
            rows.append((t, s, row_si, row_dtc))
    # duplicate settlement date (later row wins) and a ticker with one row
    rows.append(("AAA", date(2026, 3, 15), 777, 5.5))
    rows.append(("FFF", date(2026, 9, 15), 100, 1.0))
    settlements = pd.DataFrame(rows, columns=list(SETTLEMENT_COLUMNS))
    snaps = trading_days(date(2024, 2, 1), date(2026, 10, 16))
    tickers = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "ZZZ")
    keys = pd.DataFrame(
        [(t, d) for t in tickers for d in snaps],
        columns=["ticker", "snapshot_date"],
    )
    return settlements, keys


def _is_missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


class TestPureEqualsVectorised:
    def test_identical_on_synthetic_history(self):
        settlements, keys = _synthetic()
        frame = compute_short_interest_frame(settlements, keys)
        assert len(frame) == len(keys)
        assert list(frame.columns) == [
            "ticker", "snapshot_date", *SHORT_INTEREST_COLUMNS,
        ]

        cutoffs = publication_cutoffs(keys["snapshot_date"])
        by_ticker: dict[str, list[tuple]] = {}
        for t, s, si, dtc in settlements.itertuples(index=False):
            by_ticker.setdefault(t, []).append((s, si, dtc))

        got = {
            (r.ticker, r.snapshot_date): (r.short_interest_days_to_cover,
                                          r.short_interest_change_1m)
            for r in frame.itertuples(index=False)
        }
        n_dtc = n_chg = 0
        for t, d in zip(keys["ticker"], keys["snapshot_date"]):
            expected = short_interest_features_at(
                by_ticker.get(t, []), d, cutoff=cutoffs[d]
            )
            v_dtc, v_chg = got[(t, d)]
            for exp, val in ((expected[DTC], v_dtc), (expected[CHG], v_chg)):
                if exp is None:
                    assert _is_missing(val), (t, d, val)
                else:
                    assert val == exp, (t, d, val, exp)  # exact, no tolerance
            n_dtc += expected[DTC] is not None
            n_chg += expected[CHG] is not None
        # the scenario really exercises both paths
        assert n_dtc > 1000 and n_chg > 1000
        assert n_dtc < len(keys) and n_chg < len(keys)

    def test_no_lookahead_in_vectorised_frame(self):
        # a settlement dated after cutoff(d) must not leak into d
        s = pd.DataFrame(
            [("AAA", date(2026, 9, 30), 10, 1.0), ("AAA", date(2026, 10, 6), 99, 9.9)],
            columns=list(SETTLEMENT_COLUMNS),
        )
        days = [date(2026, 10, 13), date(2026, 10, 16), date(2026, 10, 19)]
        keys = pd.DataFrame({"ticker": ["AAA"] * 3, "snapshot_date": days})
        out = compute_short_interest_frame(s, keys).set_index("snapshot_date")
        assert out.loc[days[0], DTC] == 1.0  # cutoff 09-30
        assert out.loc[days[1], DTC] == 1.0  # cutoff 10-05: 10-06 not public yet
        assert out.loc[days[2], DTC] == 9.9  # cutoff 10-06

    def test_empty_inputs(self):
        empty = pd.DataFrame(columns=["ticker", "snapshot_date"])
        out = compute_short_interest_frame(
            pd.DataFrame(columns=list(SETTLEMENT_COLUMNS)), empty
        )
        assert out.empty and list(out.columns) == [
            "ticker", "snapshot_date", *SHORT_INTEREST_COLUMNS,
        ]
        # keys but no settlements at all -> all NaN
        keys = pd.DataFrame({"ticker": ["A"], "snapshot_date": [D]})
        out = compute_short_interest_frame(
            pd.DataFrame(columns=list(SETTLEMENT_COLUMNS)), keys
        )
        assert out[DTC].isna().all() and out[CHG].isna().all()

    def test_decimal_and_none_inputs(self):
        s = pd.DataFrame(
            [
                ("AAA", date(2026, 8, 31), Decimal("100"), Decimal("1.5")),
                ("AAA", date(2026, 9, 15), None, None),
                ("AAA", date(2026, 9, 30), Decimal("150"), Decimal("2.25")),
            ],
            columns=list(SETTLEMENT_COLUMNS),
        )
        keys = pd.DataFrame({"ticker": ["AAA"], "snapshot_date": [D]})
        row = compute_short_interest_frame(s, keys).iloc[0]
        assert row[DTC] == 2.25 and row[CHG] == 0.5


# ── Pipeline wiring (no DB) ──────────────────────────────────────────


def _pipeline_with_rows(rows: list[tuple]):
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    return FeaturePipeline(session), session


class TestPipelineWiring:
    def test_features_from_day_rows(self):
        rows = [
            ("AAA", s, si, dtc) for s, si, dtc in _quad()
        ] + [("BBB", date(2026, 9, 30), 5, 0.5)]
        pipeline, session = _pipeline_with_rows(rows)
        a = pipeline._short_interest_settlement_features("AAA", D)
        assert a[DTC] == 2.5 and a[CHG] == pytest.approx(0.1)
        b = pipeline._short_interest_settlement_features("BBB", D)
        assert b[DTC] == 0.5 and b[CHG] is None
        assert pipeline._short_interest_settlement_features("ZZZ", D) == {}
        # one query for the whole day, not one per ticker
        assert session.execute.call_count == 1

    def test_query_window_is_cutoff_based(self):
        pipeline, session = _pipeline_with_rows([])
        pipeline._short_interest_settlement_features("AAA", D)
        stmt = session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "settlement_date <= '2026-10-05'" in sql
        lower = CUTOFF - timedelta(days=SI_LOAD_WINDOW_DAYS)
        assert f"settlement_date >= '{lower}'" in sql

    def test_day_cache_is_per_run(self):
        pipeline, session = _pipeline_with_rows([])
        pipeline._short_interest_settlement_features("AAA", D)
        pipeline._reset_caches()
        pipeline._short_interest_settlement_features("AAA", D)
        assert session.execute.call_count == 2

    def test_combined_with_short_volume(self):
        pipeline, _ = _pipeline_with_rows([("AAA", *_quad()[-1])])
        volume = {"short_volume_ratio_5d": 0.4}
        with patch.object(pipeline, "_short_volume_features", return_value=volume):
            out = pipeline._short_interest_features("AAA", D)
        assert out["short_volume_ratio_5d"] == 0.4
        assert out[DTC] == 2.5

    def test_settlement_features_survive_missing_short_volume(self):
        pipeline, _ = _pipeline_with_rows([("AAA", *_quad()[-1])])
        with patch.object(pipeline, "_short_volume_features", return_value={}):
            assert pipeline._short_interest_features("AAA", D)[DTC] == 2.5

    def test_compute_ticker_includes_the_group(self):
        pipeline, _ = _pipeline_with_rows([])
        others = [
            n for n in dir(FeaturePipeline)
            if n.startswith("_") and n.endswith("_features")
            and n != "_short_interest_features"
        ]
        with ExitStack() as stack:
            for name in others:
                stack.enter_context(
                    patch.object(pipeline, name, MagicMock(return_value={}))
                )
            m = MagicMock(return_value={DTC: 1.0})
            stack.enter_context(patch.object(pipeline, "_short_interest_features", m))
            features = pipeline._compute_ticker("AAA", D)
        m.assert_called_once_with("AAA", D)
        assert features == {DTC: 1.0}


# ── Migration 036 ────────────────────────────────────────────────────


class TestMigration036:
    def test_revision_chain(self):
        assert mig036.revision == "036"
        assert mig036.down_revision == "035"

    def test_upgrade_adds_nullable_numeric_columns(self):
        op = MagicMock()
        with patch.object(mig036, "op", op):
            mig036.upgrade()
        added = [c.args for c in op.add_column.call_args_list]
        assert [a[0] for a in added] == ["feature_snapshots"] * 2
        cols = [a[1] for a in added]
        assert [c.name for c in cols] == list(SHORT_INTEREST_COLUMNS)
        for c in cols:
            assert c.nullable is not False
            assert (c.type.precision, c.type.scale) == (10, 4)
        for call in op.add_column.call_args_list:
            assert call.kwargs["schema"] == "signals"

    def test_downgrade_drops_both(self):
        op = MagicMock()
        with patch.object(mig036, "op", op):
            mig036.downgrade()
        dropped = {c.args[1] for c in op.drop_column.call_args_list}
        assert dropped == set(SHORT_INTEREST_COLUMNS)

    def test_migration_matches_orm(self):
        assert set(mig036.COLUMNS) == set(SHORT_INTEREST_COLUMNS)


# ── Backfill script helpers ──────────────────────────────────────────


class TestBackfillScript:
    def test_dry_run_is_default(self):
        args = backfill.parse_args([])
        assert args.apply is False and args.batch == 100
        assert backfill.parse_args(["--apply", "--batch", "7"]).apply is True

    def test_quarter_label(self):
        assert backfill.quarter_label(date(2021, 10, 1)) == "2021-Q4"
        assert backfill.quarter_label(date(2026, 3, 31)) == "2026-Q1"

    def test_differs_treats_nan_as_equal_and_ignores_rounding_noise(self):
        cur = pd.Series([1.0, float("nan"), 2.0, float("nan"), 0.5])
        new = pd.Series([1.00001, float("nan"), 2.1, 3.0, float("nan")])
        assert backfill.differs(cur, new).tolist() == [False, False, True, True, True]

    def _keys(self, dtc, chg):
        return pd.DataFrame(
            {
                "ticker": ["A", "A"],
                "snapshot_date": [date(2026, 10, 13), date(2026, 10, 16)],
                backfill.CUR_DTC: dtc,
                backfill.CUR_CHG: chg,
            }
        )

    def _settlements(self):
        rows = [("A", s, si, dtc) for s, si, dtc in _quad()]
        return pd.DataFrame(rows, columns=list(SETTLEMENT_COLUMNS))

    def test_plan_updates_is_idempotent(self):
        nan = float("nan")
        keys = self._keys([nan, nan], [nan, nan])
        merged, todo = backfill.plan_updates(keys, self._settlements())
        assert len(todo) == 2
        assert merged[DTC].tolist() == [2.5, 2.5]
        # feeding the computed values back in as "stored" -> nothing to do
        stored = self._keys(merged[DTC].tolist(), merged[CHG].tolist())
        _, todo2 = backfill.plan_updates(stored, self._settlements())
        assert todo2.empty

    def test_plan_updates_clears_stale_values(self):
        keys = self._keys([9.9, 9.9], [0.9, 0.9])
        empty = pd.DataFrame(columns=list(SETTLEMENT_COLUMNS))
        _, todo = backfill.plan_updates(keys, empty)
        assert len(todo) == 2
        rows = backfill.update_rows(todo)
        assert all(r[2] is None and r[3] is None for r in rows)

    def test_update_statement_touches_only_the_two_columns(self):
        from sqlalchemy.dialects import postgresql

        rows = [(date(2026, 10, 16), "A", 2.5, None)]
        stmt = backfill.build_update(rows)
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert sql.startswith("UPDATE signals.feature_snapshots SET")
        set_part = sql.split(" FROM ", 1)[0].split(" SET ", 1)[1]
        assert set(re.findall(r"(\w+)\s*=\s*CAST", set_part)) == set(
            SHORT_INTEREST_COLUMNS
        )
        assert "feature_version" not in sql and "VALUES" in sql

    def test_apply_updates_batches(self):
        todo = pd.DataFrame(
            {
                "snapshot_date": [D] * 5,
                "ticker": list("ABCDE"),
                DTC: [1.0, None, 3.0, 4.0, 5.0],
                CHG: [None] * 5,
            }
        )
        session = MagicMock()
        with patch.object(backfill, "UPDATE_CHUNK", 2):
            assert backfill.apply_updates(session, todo) == 5
        assert session.execute.call_count == 3
