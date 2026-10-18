"""Point-in-time rules and SQL shape of the context pack (no DB).

The exact visibility rules live in ``derived/context_pack/pit.py`` and mirror
the feature pipeline (``derived/feature_pipeline.py``); the loader fetches a
superset with coarse SQL bounds. ``SqlPackLoader`` is exercised with a mock
session: the tests inspect the statements it builds and prove that the number
of queries does not grow with the number of tickers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

from trading_signals.db.models.features import FEATURE_COLUMNS, TARGET_COLUMNS
from trading_signals.derived.context_pack import pit
from trading_signals.derived.context_pack.loader import SqlPackLoader
from trading_signals.derived.context_pack.rows import (
    BarRow,
    EarningsRow,
    EstimateRow,
    HeadlineRow,
    Holders13F,
    InsiderTradeRow,
    MembershipRow,
    PoliticianRow,
    ShortInterestRow,
    SourceStat,
    TechnicalRow,
    TickerRaw,
)
from trading_signals.derived.short_interest_features import publication_cutoff

NY = ZoneInfo("America/New_York")
D = date(2026, 10, 16)  # Friday


def at(hour, minute=0, second=0, day=D):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=NY)


def trade(**kw):
    base = dict(
        ticker="AAA",
        name="X",
        title=None,
        transaction_type="P",
        transaction_date=D - timedelta(days=2),
        filing_date=D,
        acceptance=None,
        shares=1.0,
        price=1.0,
        value=1.0,
    )
    base.update(kw)
    return InsiderTradeRow(**base)


# ── Form 4 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "acceptance, expected",
    [
        (at(15, 59, 59), True),
        (at(16, 0, 0), False),  # strict: accepted AT the close is not yet public
        (at(16, 5), False),  # the case of the plan: 16:05 ET on d
        (at(9, 0, 0, day=D + timedelta(days=1)), False),
        (at(23, 0, 0, day=D - timedelta(days=1)), True),
        # the same instant written in UTC: 16:05 ET = 20:05 UTC
        (datetime(2026, 10, 16, 20, 5, tzinfo=UTC), False),
        (datetime(2026, 10, 16, 19, 55, tzinfo=UTC), True),
    ],
)
def test_form4_with_acceptance_time_public_only_before_the_close(acceptance, expected):
    assert (
        pit.insider_public_by(trade(acceptance=acceptance, filing_date=D), D)
        is expected
    )


def test_form4_acceptance_decides_even_if_the_filing_date_is_d():
    late = trade(acceptance=at(16, 5), filing_date=D)  # filed on d, after the bell
    assert not pit.insider_public_by(late, D)
    assert pit.insider_public_by(late, D + timedelta(days=1))  # public the next day


def test_form4_without_acceptance_uses_the_filing_date_then_the_fallback_lag():
    assert pit.insider_public_by(trade(filing_date=D), D)
    assert not pit.insider_public_by(trade(filing_date=D + timedelta(days=1)), D)
    # no dates at all: transaction date + 4 days (FALLBACK_FILING_LAG_DAYS)
    nofile = dict(filing_date=None, acceptance=None)
    assert pit.insider_public_by(
        trade(transaction_date=D - timedelta(days=4), **nofile), D
    )
    assert not pit.insider_public_by(
        trade(transaction_date=D - timedelta(days=3), **nofile), D
    )
    assert not pit.insider_public_by(trade(transaction_date=None, **nofile), D)


# ── news ───────────────────────────────────────────────────────────────


def test_news_must_be_published_before_1600_new_york():
    assert pit.news_public_by(at(15, 59, 59), D)
    assert not pit.news_public_by(at(16, 0, 0), D)
    assert not pit.news_public_by(at(16, 30), D)
    assert pit.news_public_by(at(16, 30, day=D - timedelta(days=1)), D)
    # a UTC timestamp across the date line: 21:00 UTC on d is after 16:00 ET
    assert not pit.news_public_by(datetime(2026, 10, 16, 21, 0, tzinfo=UTC), D)
    # winter time (EST): 16:00 ET = 21:00 UTC
    dec = date(2026, 12, 4)
    assert pit.news_public_by(datetime(2026, 12, 4, 20, 59, tzinfo=UTC), dec)
    assert not pit.news_public_by(datetime(2026, 12, 4, 21, 0, tzinfo=UTC), dec)


# ── politicians, earnings, 13F, short interest, memberships ───────────


def test_politicians_need_a_disclosure_by_d():
    def pol(**kw):
        base = dict(
            ticker="AAA",
            name="P",
            chamber="House",
            party="D",
            state="CA",
            transaction_type="Purchase",
            transaction_date=D - timedelta(days=30),
            disclosure_date=D,
            amount_range="$1",
            owner="Self",
        )
        base.update(kw)
        return PoliticianRow(**base)

    assert pit.politician_public_by(pol(disclosure_date=D), D)
    assert not pit.politician_public_by(pol(disclosure_date=D + timedelta(days=1)), D)
    assert not pit.politician_public_by(pol(disclosure_date=None), D)
    assert not pit.politician_public_by(pol(transaction_date=None), D)


def test_earnings_dates_must_have_been_known_by_d():
    def e(date_, first_seen=None, actual=None):
        return EarningsRow("AAA", date_, "AMC", 1.0, actual, None, first_seen)

    seen_before = datetime(2026, 9, 1)
    assert pit.earnings_visible(e(D + timedelta(days=30), seen_before), D)
    # announced date first seen after d: nobody knew it at d
    after = datetime(2026, 10, 18)
    assert not pit.earnings_visible(e(D + timedelta(days=30), after), D)
    # ... unless it was collected retroactively (first seen after its own date)
    assert pit.earnings_visible(e(D, datetime(2026, 10, 20)), D)
    assert pit.earnings_visible(e(D + timedelta(days=30), None), D)  # legacy row
    assert not pit.earnings_visible(
        e(D + timedelta(days=91), seen_before), D
    )  # > 90 days
    # reported quarters need an actual EPS
    assert pit.earnings_visible(e(D - timedelta(days=60), actual=1.2), D)
    assert not pit.earnings_visible(e(D - timedelta(days=60), actual=None), D)


def test_apply_pit_filters_every_source():
    cutoff = publication_cutoff(D)  # the 9th NYSE session before d
    assert cutoff == date(2026, 10, 5)
    raw = TickerRaw(
        insider=[
            trade(name="ok", acceptance=at(15, 0)),
            trade(name="late", acceptance=at(16, 5)),
        ],
        headlines=[
            HeadlineRow("AAA", at(10), "ok", None, None, None),
            HeadlineRow("AAA", at(17), "late", None, None, None),
        ],
        estimates=[
            EstimateRow("AAA", D, "0q"),
            EstimateRow("AAA", D + timedelta(days=1), "0q"),
        ],
        short_interest=[
            ShortInterestRow("AAA", cutoff, 1, 1.0),
            ShortInterestRow("AAA", cutoff + timedelta(days=1), 2, 2.0),
        ],
        memberships=[
            MembershipRow("AAA", "sp500", date(2000, 1, 1)),
            MembershipRow(
                "AAA", "nasdaq100", date(2000, 1, 1), valid_to=D
            ),  # left on d
            MembershipRow("AAA", "nasdaq100", D + timedelta(days=1)),  # joins later
        ],
        holders=Holders13F(date(2026, 6, 30), D + timedelta(days=1), 1, 1.0),
        bars=[
            BarRow(D, 1, 2, 0.5, 1.5, 10),
            BarRow(D + timedelta(days=3), 9, 99, 0.1, 50, 10),  # the next session
        ],
        technical=TechnicalRow("AAA", D + timedelta(days=1)),
        stats={
            "prices": SourceStat(date(2021, 1, 1), D, 10),
            "news": SourceStat(date(2026, 1, 1), D + timedelta(days=1), 10),
        },
    )
    out = pit.apply_pit(raw, D)
    assert [t.name for t in out.insider] == ["ok"]
    assert [h.headline for h in out.headlines] == ["ok"]
    assert [e.as_of for e in out.estimates] == [D]
    assert [s.settlement_date for s in out.short_interest] == [cutoff]
    assert [m.index_name for m in out.memberships] == ["sp500"]
    assert out.holders is None and out.technical is None
    assert [b.trade_date for b in out.bars] == [D]  # no bar after the session
    assert set(out.stats) == {"prices"}
    # the input is not mutated
    assert len(raw.insider) == 2 and raw.holders is not None and len(raw.bars) == 2


# ── loader: SQL shape (mock session) ──────────────────────────────────


def empty_session():
    session = MagicMock()
    result = session.execute.return_value
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    return session


def sql_of(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_snapshots_query_selects_features_only_never_targets():
    session = empty_session()
    SqlPackLoader(session).snapshots(D)
    sql = sql_of(session.execute.call_args.args[0])
    assert "feature_snapshots.snapshot_date =" in sql
    for target in TARGET_COLUMNS:  # (st_return_1d is a feature, return_1d a target)
        assert f"feature_snapshots.{target}" not in sql, (
            f"{target} would leak the future"
        )
    assert "barrier" not in sql
    for col in ("rsi_14", "dollar_volume_20d", "insider_cluster_active"):
        assert col in sql
    assert all(col in sql for col in FEATURE_COLUMNS)


def test_insider_query_prefilters_with_a_superset_of_the_pipeline_rule():
    session = empty_session()
    loader = SqlPackLoader(session)
    raw = MagicMock()
    loader._insider(D, ["AAA", "BBB"], raw)
    sql = sql_of(session.execute.call_args.args[0])
    assert "transaction_type IN" in sql and "is_derivative IS false" in sql
    assert "acceptance_datetime <" in sql and "filing_date <=" in sql
    assert "acceptance_datetime IS NULL" in sql


def test_headline_query_is_bounded_by_the_1600_et_cutoff():
    session = empty_session()
    SqlPackLoader(session)._headlines(D, ["AAA"], MagicMock())
    stmt = session.execute.call_args.args[0]
    sql = sql_of(stmt)
    assert "published_at <" in sql and "model_version =" in sql
    cutoff = [v for v in stmt.compile().params.values() if isinstance(v, datetime)]
    assert max(cutoff) == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)  # 16:00 EDT


def test_headline_timestamps_are_read_as_absolute_instants():
    # TZDateTime returns NAIVE Berlin wall clock; the 16:00 ET check needs the
    # timezone-aware instant, otherwise articles from 14:00-16:00 ET would be
    # judged two hours too late (found against a real PostgreSQL)
    from trading_signals.db.base import TZDateTime

    session = empty_session()
    SqlPackLoader(session)._headlines(D, ["AAA"], MagicMock())
    col_type = session.execute.call_args.args[0].selected_columns["published_at"].type
    assert not isinstance(col_type, TZDateTime) and col_type.timezone is True


def test_number_of_queries_does_not_depend_on_the_number_of_tickers():
    def count(n):
        session = empty_session()
        SqlPackLoader(session).raw_sources(D, [f"T{i:03d}" for i in range(n)])
        return session.execute.call_count

    few, many = count(3), count(25)
    assert few == many and few <= 20  # batch queries, never per ticker x section
    assert count(0) == 0


def test_cross_section_queries_use_one_statement_each():
    session = empty_session()
    loader = SqlPackLoader(session)
    loader.closes([D, D - timedelta(days=1)])
    assert session.execute.call_count == 1
    sql = sql_of(session.execute.call_args.args[0])
    assert "trade_date IN" in sql and "is_extrapolated IS NOT true" in sql
