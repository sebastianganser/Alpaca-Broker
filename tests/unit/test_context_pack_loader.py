"""Loader of the context pack: price targets, estimate details, holdings, price
bars and the tracked 13F funds (mock session, no DB).

Complements ``test_context_pack_pit.py`` (point-in-time rules and the shape of
the older queries).
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from trading_signals.derived.context_pack.loader import SqlPackLoader
from trading_signals.derived.context_pack.rows import RawSources

D = date(2026, 10, 16)  # Friday


def empty_session():
    session = MagicMock()
    result = session.execute.return_value
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    return session


def rows_session(*batches):
    """A mock session whose consecutive ``execute().all()`` return ``batches``."""
    session = MagicMock()
    results = []
    for rows in batches:
        result = MagicMock()
        result.all.return_value = rows
        results.append(result)
    session.execute.side_effect = results
    return session


def sql_of(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# ── analyst price targets ─────────────────────────────────────────────


def test_rating_targets_come_from_the_columns_else_from_raw_data():
    # (ticker, date, firm, action, old grade, new grade, old / new target columns,
    #  then the raw_data text of the old target, the new target and the action)
    rows = [
        (
            "AAA",
            D,
            "Col",
            "up",
            "Hold",
            "Buy",
            250.0,
            275.0,
            "200.0",
            "300.0",
            "Raises",
        ),
        ("AAA", D, "Raw", "main", "Buy", "Buy", None, None, "250.0", "275.0", "Raises"),
        ("AAA", D, "Half", "main", "Buy", "Buy", None, 90.0, "None", "95.0", "Lowers"),
        ("AAA", D, "Nan", "main", "Buy", "Buy", None, None, "nan", "0.0", "nan"),
        ("AAA", D, "Old", "main", "Buy", "Buy", None, None, None, None, None),
    ]
    raw = RawSources()
    SqlPackLoader(rows_session(rows))._ratings(D, ["AAA"], raw)
    by_firm = {r.firm: r for r in raw.by_ticker["AAA"].ratings}
    # the column wins over raw_data
    assert (by_firm["Col"].target_old, by_firm["Col"].target_new) == (250.0, 275.0)
    # a NULL column falls back to the text of raw_data
    assert (by_firm["Raw"].target_old, by_firm["Raw"].target_new) == (250.0, 275.0)
    assert by_firm["Raw"].target_action == "Raises"
    # per column: the new target is in the column, "None" (no old target) stays NULL
    assert (by_firm["Half"].target_old, by_firm["Half"].target_new) == (None, 90.0)
    # NaN / 0 / missing raw_data is NULL - never 0
    assert (by_firm["Nan"].target_old, by_firm["Nan"].target_new) == (None, None)
    assert by_firm["Nan"].target_action is None
    assert (by_firm["Old"].target_old, by_firm["Old"].target_new) == (None, None)


def test_rating_query_reads_the_target_keys_of_raw_data_and_stays_pit_bounded():
    session = empty_session()
    SqlPackLoader(session)._ratings(D, ["AAA"], MagicMock())
    statement = session.execute.call_args.args[0]
    sql = sql_of(statement)
    keys = {str(v) for v in statement.compile().params.values()}
    assert {"currentPriceTarget", "priorPriceTarget", "priceTargetAction"} <= keys
    assert "analyst_ratings.raw_data" in sql and "rating_date <=" in sql
    assert "price_target_new" in sql and "price_target_old" in sql


# ── estimates ─────────────────────────────────────────────────────────


def test_estimate_query_loads_ranges_year_ago_and_revenue_details():
    session = empty_session()
    SqlPackLoader(session)._estimates(D, ["AAA"], MagicMock())
    sql = sql_of(session.execute.call_args.args[0])
    for col in (
        "eps_low",
        "eps_high",
        "eps_year_ago",
        "eps_growth",
        "revenue_low",
        "revenue_high",
        "revenue_n_analysts",
        "revenue_year_ago",
        "revenue_growth",
    ):
        assert f"estimates_snapshot.{col}" in sql
    assert "as_of <=" in sql


def test_estimate_rows_keep_every_new_field():
    row = (
        "AAA", D, "0q", 1.5, 20, 1.5, 1.48, 1.4, 1.3, 3, 0, 6, 1, 2.5e10, 0.12,
        1.3, 1.7, 1.3, 0.15, 2.3e10, 2.7e10, 18, 2.2e10,
    )  # fmt: skip
    raw = RawSources()
    SqlPackLoader(rows_session([row]))._estimates(D, ["AAA"], raw)
    (e,) = raw.by_ticker["AAA"].estimates
    assert (e.eps_low, e.eps_high, e.eps_year_ago, e.eps_growth) == (
        1.3,
        1.7,
        1.3,
        0.15,
    )
    assert (e.revenue_low, e.revenue_high) == (2.3e10, 2.7e10)
    assert (e.revenue_n_analysts, e.revenue_year_ago, e.revenue_growth) == (
        18,
        2.2e10,
        0.12,
    )


# ── insider holdings ──────────────────────────────────────────────────


def test_insider_query_loads_the_holding_after_the_trade():
    session = empty_session()
    SqlPackLoader(session)._insider(D, ["AAA"], MagicMock())
    sql = sql_of(session.execute.call_args.args[0])
    assert "insider_trades.shares_owned_after" in sql
    row = ("AAA", "Jane", "CEO", "P", D, D, None, 1000, 10.0, 10000.0, 11000)
    raw = RawSources()
    SqlPackLoader(rows_session([row]))._insider(D, ["AAA"], raw)
    assert raw.by_ticker["AAA"].insider[0].shares_owned_after == 11000.0
    unknown = ("AAA", "Jane", "CEO", "P", D, D, None, 1000, 10.0, 10000.0, None)
    raw2 = RawSources()
    SqlPackLoader(rows_session([unknown]))._insider(D, ["AAA"], raw2)
    assert raw2.by_ticker["AAA"].insider[0].shares_owned_after is None  # NULL, not 0


# ── price bars ────────────────────────────────────────────────────────


def test_bar_query_is_real_bars_up_to_the_session_in_one_statement():
    session = empty_session()
    SqlPackLoader(session)._bars(D, ["AAA", "BBB"], MagicMock())
    assert session.execute.call_count == 1
    statement = session.execute.call_args.args[0]
    sql = sql_of(statement)
    assert "prices_daily.trade_date <=" in sql and "prices_daily.trade_date >=" in sql
    assert "is_extrapolated IS NOT true" in sql and "close IS NOT NULL" in sql
    assert (
        "ORDER BY signals.prices_daily.ticker, signals.prices_daily.trade_date" in sql
    )
    params = list(statement.compile().params.values())
    assert D in params  # the upper bound is the session itself
    lows = [v for v in params if isinstance(v, date) and v < D]
    assert lows and (D - min(lows)).days >= 800  # 8 quarters + the SMA200 warm-up


def test_bars_are_collected_per_ticker_in_date_order():
    rows = [
        ("AAA", D - timedelta(days=1), 1.0, 2.0, 0.5, 1.5, 100),
        ("AAA", D, 1.5, 2.5, 1.0, 2.0, None),
        ("BBB", D, 9.0, 9.5, 8.5, 9.2, 5),
    ]
    raw = RawSources()
    SqlPackLoader(rows_session(rows))._bars(D, ["AAA", "BBB"], raw)
    aaa = raw.by_ticker["AAA"].bars
    assert [b.trade_date for b in aaa] == [D - timedelta(days=1), D]
    assert aaa[1].volume is None  # a NULL volume stays NULL
    assert raw.by_ticker["BBB"].bars[0].close == 9.2


def test_the_bar_query_is_part_of_the_batch_and_does_not_grow_with_the_tickers():
    def count(n):
        session = empty_session()
        SqlPackLoader(session).raw_sources(D, [f"T{i:03d}" for i in range(n)])
        return session.execute.call_count

    assert count(2) == count(30)
    assert count(0) == 0


# ── 13F: tracked funds ────────────────────────────────────────────────


def test_13f_tracked_fund_counts_are_attached_to_the_periods():
    latest, previous = date(2026, 6, 30), date(2026, 3, 31)
    summaries = [  # ticker, period, filers, shares, newest filing
        ("AAA", latest, 15, 5e8, date(2026, 8, 14)),
        ("AAA", previous, 14, 4.9e8, date(2026, 5, 15)),
    ]
    tracked = [(latest, 19), (previous, 18)]
    top = [
        SimpleNamespace(
            ticker="AAA", period=latest, cik="1", name="Vanguard", shares=8e7,
            value=1.6e10, rn=1,
        )
    ]  # fmt: skip
    prev_shares = [("AAA", "1", 7.5e7)]
    session = rows_session(summaries, tracked, top, prev_shares)
    raw = RawSources()
    SqlPackLoader(session)._holders(D, ["AAA"], raw)
    h = raw.by_ticker["AAA"].holders
    assert (h.n_filers, h.n_tracked) == (15, 19)
    assert (h.prev_n_filers, h.prev_n_tracked) == (14, 18)
    assert h.top[0].prev_shares == 7.5e7
    # only filings that were public by the session count as tracked funds
    tracked_sql = sql_of(session.execute.call_args_list[1].args[0])
    assert "form13f_holdings.filing_date <=" in tracked_sql
    assert "form13f_holdings.put_call =" in tracked_sql
    assert "count(distinct(signals.form13f_holdings.filer_cik))" in tracked_sql
    assert "GROUP BY signals.form13f_holdings.report_period" in tracked_sql


def test_a_period_without_a_tracked_count_leaves_it_unknown():
    latest = date(2026, 6, 30)
    summaries = [("AAA", latest, 15, 5e8, date(2026, 8, 14))]
    session = rows_session(summaries, [], [], [])
    raw = RawSources()
    SqlPackLoader(session)._holders(D, ["AAA"], raw)
    h = raw.by_ticker["AAA"].holders
    assert h.n_tracked is None and h.prev_n_tracked is None and h.prev_period is None
