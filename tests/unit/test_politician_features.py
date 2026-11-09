"""Vectorised politician features vs. a literal transcription of the SQL (no DB)."""

import random
from datetime import date, timedelta

import pandas as pd
import pytest

from trading_signals.derived.politician_features import (
    BUY_COUNT_DISCLOSURE,
    BUY_COUNT_TRANSACTION,
    DISTINCT_DISCLOSURE,
    DISTINCT_TRANSACTION,
    POLITICIAN_FEATURE_COLUMNS,
    compute_politician_features,
)

D = date(2025, 6, 30)


def reference(trades: list[dict], d: date) -> dict | None:
    """Literal transcription of FeaturePipeline._politician_features (None = {})."""
    elig = [
        t
        for t in trades
        if t["transaction_date"] is not None
        and t["disclosure_date"] is not None
        and t["disclosure_date"] <= d
        and t["transaction_date"] >= t["disclosure_date"] - timedelta(days=45)
    ]

    def count(col, since):
        return sum(
            1
            for t in elig
            if t["transaction_type"] == "Purchase" and since <= t[col] <= d
        )

    def distinct(col, since):
        return len(
            {
                t["politician_name"]
                for t in elig
                if since <= t[col] <= d and t["politician_name"] is not None
            }
        )

    s60, s90 = d - timedelta(days=60), d - timedelta(days=90)
    vals = {
        BUY_COUNT_DISCLOSURE: count("disclosure_date", s60),
        DISTINCT_DISCLOSURE: distinct("disclosure_date", s90),
        BUY_COUNT_TRANSACTION: count("transaction_date", s60),
        DISTINCT_TRANSACTION: distinct("transaction_date", s90),
    }
    if not any(vals.values()) and not elig:
        return None
    return vals


def trade(txn, disc, typ="Purchase", name="A"):
    return {
        "transaction_date": txn,
        "disclosure_date": disc,
        "transaction_type": typ,
        "politician_name": name,
    }


def run(trades: list[dict], sessions: list[date]) -> pd.DataFrame:
    df = pd.DataFrame(
        trades,
        columns=[
            "transaction_date",
            "disclosure_date",
            "transaction_type",
            "politician_name",
        ],
    )
    return compute_politician_features(df, sessions)


def row(res: pd.DataFrame, d: date) -> dict | None:
    r = res.loc[d]
    if r.isna().all():
        return None
    assert not r.isna().any()
    return {c: int(r[c]) for c in POLITICIAN_FEATURE_COLUMNS}


def assert_matches(trades, sessions):
    res = run(trades, sessions)
    assert list(res.columns) == list(POLITICIAN_FEATURE_COLUMNS)
    assert all(str(res[c].dtype) == "Int64" for c in res.columns)
    for d in sessions:
        assert row(res, d) == reference(trades, d), d


def test_random_equivalence():
    rng = random.Random(1234)
    names = [None, "A", "B", "C", "D", "E"]
    base = date(2024, 1, 1)
    sessions = [base + timedelta(days=i) for i in range(0, 400, 3)]
    for _ in range(60):
        trades = []
        for _ in range(rng.randint(0, 40)):
            disc = base + timedelta(days=rng.randint(-20, 420))
            lag = rng.randint(-10, 60)  # includes lags above 45 and txn after disc
            txn = disc - timedelta(days=lag)
            trades.append(
                trade(
                    None if rng.random() < 0.05 else txn,
                    None if rng.random() < 0.05 else disc,
                    rng.choice(["Purchase", "Sale", "Sale (Partial)", None]),
                    rng.choice(names),
                )
            )
        assert_matches(trades, sessions)


def test_lag_45_included_46_excluded():
    res = run(
        [
            trade(D - timedelta(days=45), D),  # lag 45: eligible
            trade(D - timedelta(days=46), D, name="B"),  # lag 46: dropped
        ],
        [D],
    )
    assert row(res, D) == {
        BUY_COUNT_DISCLOSURE: 1,
        DISTINCT_DISCLOSURE: 1,
        BUY_COUNT_TRANSACTION: 1,
        DISTINCT_TRANSACTION: 1,
    }


def test_disclosure_on_d_included_next_day_excluded():
    t = [trade(D, D)]
    assert row(run(t, [D]), D)[BUY_COUNT_DISCLOSURE] == 1
    assert row(run(t, [D - timedelta(days=1)]), D - timedelta(days=1)) is None


def test_window_bounds_inclusive():
    # disclosure exactly 60 / 61 days before d, transaction the same day
    in60 = trade(D - timedelta(days=60), D - timedelta(days=60))
    out60 = trade(D - timedelta(days=61), D - timedelta(days=61), name="B")
    r = row(run([in60, out60], [D]), D)
    assert r[BUY_COUNT_DISCLOSURE] == 1 and r[BUY_COUNT_TRANSACTION] == 1
    # 90 / 91 days for the distinct windows
    in90 = trade(D - timedelta(days=90), D - timedelta(days=90), "Sale", "A")
    out90 = trade(D - timedelta(days=91), D - timedelta(days=91), "Sale", "B")
    r = row(run([in90, out90], [D]), D)
    assert r[DISTINCT_DISCLOSURE] == 1 and r[DISTINCT_TRANSACTION] == 1
    assert r[BUY_COUNT_DISCLOSURE] == 0  # covered, so zeros not NA


def test_null_names_not_counted_in_distinct_but_in_buy_count():
    r = row(run([trade(D, D, name=None), trade(D, D, name=None)], [D]), D)
    assert r[BUY_COUNT_DISCLOSURE] == 2
    assert r[DISTINCT_DISCLOSURE] == 0


def test_non_purchase_only_in_distinct():
    r = row(run([trade(D, D, "Sale", "A"), trade(D, D, None, "B")], [D]), D)
    assert r[BUY_COUNT_DISCLOSURE] == 0 and r[BUY_COUNT_TRANSACTION] == 0
    assert r[DISTINCT_DISCLOSURE] == 2 and r[DISTINCT_TRANSACTION] == 2


def test_distinct_counts_a_politician_once():
    r = row(run([trade(D, D), trade(D, D, "Sale", "A")], [D]), D)
    assert r[DISTINCT_DISCLOSURE] == 1 and r[BUY_COUNT_DISCLOSURE] == 1


def test_transaction_after_session_excluded_from_transaction_window():
    # disclosed on D but transaction dated after D (data quirk): not in txn window
    t = trade(D + timedelta(days=3), D)
    r = row(run([t], [D]), D)
    assert r[BUY_COUNT_DISCLOSURE] == 1 and r[BUY_COUNT_TRANSACTION] == 0


def test_never_covered_ticker_all_na():
    t = [trade(None, D), trade(D, None), trade(D - timedelta(days=100), D)]  # lag 100
    res = run(t, [D, D + timedelta(days=5)])
    assert res.isna().all().all()


def test_covered_by_old_trade_gives_zeros():
    old = trade(D - timedelta(days=400), D - timedelta(days=400))
    r = row(run([old], [D]), D)
    assert r == dict.fromkeys(POLITICIAN_FEATURE_COLUMNS, 0)


def test_na_before_first_public_then_values():
    first = D - timedelta(days=10)
    res = run([trade(first, first)], [first - timedelta(days=1), first])
    assert row(res, first - timedelta(days=1)) is None
    assert row(res, first)[BUY_COUNT_DISCLOSURE] == 1


def test_empty_trades_and_empty_sessions():
    res = run([], [D, D + timedelta(days=1)])
    assert len(res) == 2 and res.isna().all().all()
    assert len(run([trade(D, D)], [])) == 0


def test_session_order_and_index_preserved():
    s = [D + timedelta(days=5), D, D + timedelta(days=2)]
    res = run([trade(D, D)], s)
    assert list(res.index) == s
    assert row(res, D) is not None


def test_accepts_timestamp_and_datetime_columns():
    df = pd.DataFrame(
        {
            "transaction_date": pd.to_datetime([D]),
            "disclosure_date": pd.to_datetime([D]),
            "transaction_type": ["Purchase"],
            "politician_name": ["A"],
        }
    )
    res = compute_politician_features(df, [D])
    assert int(res.loc[D, BUY_COUNT_DISCLOSURE]) == 1


def test_worst_case_runs_fast():
    import time

    rng = random.Random(7)
    base = date(2021, 7, 1)
    trades = []
    for _ in range(3000):
        disc = base + timedelta(days=rng.randint(0, 1900))
        trades.append(
            trade(
                disc - timedelta(days=rng.randint(0, 45)),
                disc,
                rng.choice(["Purchase", "Sale"]),
                f"P{rng.randint(0, 250)}",
            )
        )
    sessions = [base + timedelta(days=i) for i in range(1300)]
    t0 = time.perf_counter()
    res = run(trades, sessions)
    assert time.perf_counter() - t0 < 30
    assert len(res) == 1300
    for d in rng.sample(sessions, 3):
        assert row(res, d) == reference(trades, d)


@pytest.mark.parametrize("n", [1, 2])
def test_single_trade_frames(n):
    res = run([trade(D, D)] * n, [D])
    assert row(res, D)[BUY_COUNT_DISCLOSURE] == n
