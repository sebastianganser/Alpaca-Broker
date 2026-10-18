"""Unit tests for the ``forecasts.yaml`` parser (derived/forecasts.py, no DB).

Format: docs/FORECASTS.md section 3 (``forecasts/v1``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
import yaml

from trading_signals.derived.forecasts import (
    DRIVER_TAGS,
    FORBIDDEN_FIELDS,
    ForecastFileError,
    is_on_time,
    next_session_open,
    parse_forecasts,
)

D = date(2026, 10, 16)  # Friday

HEADER = {
    "schema": "forecasts/v1",
    "session": "2026-10-16",
    "decided_at": "2026-10-17T09:40:00+02:00",
    "forecaster_version": "aktien-analyse/2026-10-17+claude-opus-4",
}

VALID = """
schema: forecasts/v1
session: 2026-10-16
decided_at: 2026-10-17T09:40:00+02:00
forecaster_version: aktien-analyse/2026-10-17+claude-opus-4
forecasts:
  - ticker: nvda
    horizon_days: 60
    direction: up
    confidence: 0.62
    conviction: 4
    expected_pct: 4.5
    drivers: [revisions, insider]
    reason: "Estimates revised up twice, CEO purchase."
    risks: "Export restrictions."
  - ticker: NVDA
    horizon_days: 20
    direction: Neutral
    reason: "Earnings in 6 sessions, no edge."
  - ticker: BRK.B
    horizon_days: 20
    direction: down
    confidence: 0.55
    conviction: 2
    expected_pct: -2
"""


def _file(*rows: dict, **header) -> str:
    return yaml.safe_dump(
        {**HEADER, **header, "forecasts": list(rows)}, sort_keys=False
    )


def _row(**kw) -> dict:
    base = {
        "ticker": "AAPL",
        "horizon_days": 20,
        "direction": "up",
        "confidence": 0.6,
        "conviction": 3,
    }
    return {**base, **kw}


def test_parse_valid_file():
    r = parse_forecasts(VALID, D)
    assert r.session_date == D and r.status == "ok" and not r.rejected
    assert r.forecaster_version == "aktien-analyse/2026-10-17+claude-opus-4"
    assert r.decided_at.utcoffset() == timedelta(hours=2)
    nv60, nv20, brk = r.forecasts
    assert (nv60.ticker, nv60.horizon_days, nv60.direction) == ("NVDA", 60, "up")
    assert (nv60.confidence, nv60.conviction, nv60.expected_pct) == (0.62, 4, 4.5)
    assert nv60.drivers == ("revisions", "insider") and nv60.index == 1
    assert nv60.reason.startswith("Estimates") and nv60.risks == "Export restrictions."
    # one ticker, two horizons; neutral carries no probability / strength
    assert (nv20.direction, nv20.confidence, nv20.conviction) == ("neutral", None, None)
    assert brk.ticker == "BRK.B" and brk.expected_pct == -2.0 and brk.index == 3


def test_empty_forecasts_means_reviewed_without_calls():
    r = parse_forecasts(_file(), D)
    assert r.forecasts == [] and r.status == "ok"


def test_naive_decided_at_is_berlin_time():
    r = parse_forecasts(_file(_row(), decided_at="2026-10-17T09:40:00"), D)
    assert r.decided_at.utcoffset() == timedelta(hours=2)  # CEST in October


def test_long_text_is_truncated_not_rejected():
    r = parse_forecasts(_file(_row(reason="x" * 2500)), D)
    assert len(r.forecasts[0].reason) == 1000


# ── header violations reject the file ────────────────────────────────


@pytest.mark.parametrize(
    "old,new,msg",
    [
        ("schema: forecasts/v1", "schema: stage2-decisions/v1", "schema"),
        ("session: 2026-10-16", "session: 2026-10-15", "passt nicht zum Ordner"),
        ("session: 2026-10-16", "session: gestern", "kein Datum"),
        (
            "decided_at: 2026-10-17T09:40:00+02:00",
            "decided_at: JJJJ-MM-TT",
            "decided_at",
        ),
        (
            "forecaster_version: aktien-analyse/2026-10-17+claude-opus-4",
            "forecaster_version: ''",
            "forecaster_version",
        ),
        (
            "forecaster_version: aktien-analyse/2026-10-17+claude-opus-4",
            "forecaster_version: " + "v" * 65,
            "höchstens 64",
        ),
    ],
)
def test_header_violations_reject_the_file(old, new, msg):
    with pytest.raises(ForecastFileError, match=msg):
        parse_forecasts(VALID.replace(old, new, 1), D)


@pytest.mark.parametrize(
    "line",
    [
        "session: 2026-10-16\n",
        "decided_at: 2026-10-17T09:40:00+02:00\n",
        "forecaster_version: aktien-analyse/2026-10-17+claude-opus-4\n",
    ],
)
def test_missing_header_field_rejects_the_file(line):
    with pytest.raises(ForecastFileError, match="fehlt"):
        parse_forecasts(VALID.replace(line, "", 1), D)


def test_not_a_yaml_object_or_list_rejects_the_file():
    with pytest.raises(ForecastFileError, match="YAML-Objekt"):
        parse_forecasts("- a\n- b\n", D)
    with pytest.raises(ForecastFileError, match="YAML"):
        parse_forecasts("schema: [unclosed", D)
    with pytest.raises(ForecastFileError, match="Liste"):
        parse_forecasts(VALID.split("forecasts:")[0] + "forecasts: viele\n", D)
    with pytest.raises(ForecastFileError, match="Liste"):  # key present but empty
        parse_forecasts(VALID.split("forecasts:")[0] + "forecasts:\n", D)


# ── row violations reject the row only ───────────────────────────────


@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
def test_every_forbidden_field_rejects_the_row(field):
    r = parse_forecasts(_file(_row(), _row(ticker="MSFT", **{field: 1})), D)
    assert [f.ticker for f in r.forecasts] == ["AAPL"]
    assert len(r.rejected) == 1 and r.status == "partial"
    assert r.rejected[0].index == 2 and r.rejected[0].ticker == "MSFT"
    assert field in r.rejected[0].reason and "Bots" in r.rejected[0].reason


def test_forbidden_field_names_are_matched_case_insensitively():
    r = parse_forecasts(_file(_row(**{"Stop_Loss": 5})), D)
    assert r.forecasts == [] and "stop_loss" in r.rejected[0].reason


def test_unknown_field_is_rejected_loudly():
    r = parse_forecasts(_file(_row(confidense=0.7)), D)
    assert r.forecasts == [] and "unbekannt" in r.rejected[0].reason
    assert "confidense" in r.rejected[0].reason


@pytest.mark.parametrize(
    "change,msg",
    [
        ({"ticker": "MS FT"}, "ungültiger Ticker"),
        ({"ticker": ""}, "ungültiger Ticker"),
        ({"horizon_days": 10}, "horizon_days muss"),
        ({"horizon_days": "zwanzig"}, "keine Zahl"),
        ({"horizon_days": 20.5}, "keine ganze Zahl"),
        ({"direction": "buy"}, "direction muss"),
        ({"direction": None}, "direction muss"),
        ({"confidence": 0.49}, "confidence muss"),
        ({"confidence": 0.96}, "confidence muss"),
        ({"confidence": 1.2}, "confidence muss"),
        ({"confidence": "hoch"}, "keine Zahl"),
        ({"confidence": None}, "confidence fehlt"),
        ({"conviction": 0}, "conviction muss"),
        ({"conviction": 6}, "conviction muss"),
        ({"conviction": 2.5}, "keine ganze Zahl"),
        ({"conviction": None}, "conviction fehlt"),
        ({"expected_pct": -3.0}, "passt nicht zu direction up"),
        ({"expected_pct": 0.0}, "passt nicht zu direction up"),
        ({"expected_pct": 5000}, "außerhalb"),
        ({"drivers": "trend"}, "Liste"),
        ({"drivers": ["trend", "astrologie"]}, "unbekannter Treiber"),
        (
            {"drivers": ["trend", "value", "news", "macro", "other", "sector"]},
            "höchstens 5",
        ),
        ({"reason": ["a"]}, "kein Text"),
    ],
)
def test_invalid_row_values(change, msg):
    r = parse_forecasts(_file(_row(**change), _row(ticker="MSFT")), D)
    assert [f.ticker for f in r.forecasts] == ["MSFT"]  # the valid row survives
    assert len(r.rejected) == 1 and msg in r.rejected[0].reason


def test_expected_pct_sign_must_match_a_down_call():
    ok = parse_forecasts(_file(_row(direction="down", expected_pct=-4.0)), D)
    assert ok.forecasts[0].expected_pct == -4.0
    bad = parse_forecasts(_file(_row(direction="down", expected_pct=4.0)), D)
    assert bad.forecasts == [] and "direction down" in bad.rejected[0].reason


def test_neutral_row_takes_no_probability_or_strength():
    r = parse_forecasts(
        _file(_row(direction="neutral", confidence=0.5, conviction=None)), D
    )
    assert r.forecasts == [] and "confidence" in r.rejected[0].reason
    ok = parse_forecasts(
        _file(_row(direction="neutral", confidence=None, conviction=None)), D
    )
    assert ok.forecasts[0].direction == "neutral"


def test_driver_vocabulary_is_the_screen_groups_plus_context_tags():
    from trading_signals.analysis.screen import GROUP_NAMES

    assert set(GROUP_NAMES) < set(DRIVER_TAGS)
    assert set(DRIVER_TAGS) - set(GROUP_NAMES) == {
        "news",
        "earnings",
        "sector",
        "macro",
        "other",
    }
    r = parse_forecasts(_file(_row(drivers=["TREND", "trend", "short_interest"])), D)
    assert r.forecasts[0].drivers == ("trend", "short_interest")  # case, duplicates


def test_duplicate_ticker_horizon_rejects_every_row_of_the_key():
    r = parse_forecasts(
        _file(
            _row(),
            _row(direction="down", confidence=0.7),
            _row(horizon_days=60),
            _row(ticker="MSFT"),
        ),
        D,
    )
    assert [(f.ticker, f.horizon_days) for f in r.forecasts] == [
        ("AAPL", 60),
        ("MSFT", 20),
    ]
    assert [r_.index for r_ in r.rejected] == [1, 2]
    assert all("doppelt" in r_.reason for r_ in r.rejected)
    assert "forecasts[1] (AAPL 20d)" in r.rejected[0].message()


def test_all_rows_rejected_makes_the_file_rejected_status():
    r = parse_forecasts(_file(_row(direction="buy")), D)
    assert r.forecasts == [] and r.status == "rejected"


def test_non_object_row_is_rejected():
    r = parse_forecasts(
        yaml.safe_dump({**HEADER, "forecasts": ["NVDA", _row()]}, sort_keys=False), D
    )
    assert len(r.forecasts) == 1 and "Objekt" in r.rejected[0].reason


# ── on-time rule ─────────────────────────────────────────────────────


def test_next_session_open_skips_the_weekend():
    # Friday 2026-10-16 -> Monday 2026-10-19 09:30 New York (EDT = 13:30 UTC)
    o = next_session_open(date(2026, 10, 16))
    assert o.astimezone(UTC) == datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


def test_next_session_open_skips_a_holiday_and_follows_the_clock_change():
    # Wednesday before Thanksgiving: the session after is Friday 2026-11-27,
    # 09:30 EST = 14:30 UTC (winter time, the open is one hour later in UTC)
    o = next_session_open(date(2026, 11, 25))
    assert o.astimezone(UTC) == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    # Thursday 2026-11-26 itself is a holiday, the next session is also the Friday
    assert next_session_open(date(2026, 11, 26)).astimezone(UTC) == o.astimezone(UTC)
    # ordinary day: Tuesday -> Wednesday, DST-independent 09:30 New York
    tue = next_session_open(date(2026, 10, 13))
    assert tue.astimezone(UTC) == datetime(2026, 10, 14, 13, 30, tzinfo=UTC)


def test_is_on_time_requires_decision_and_file_before_the_open():
    open_ = next_session_open(date(2026, 10, 16))  # Monday 2026-10-19 15:30 Berlin
    cest = timezone(timedelta(hours=2))
    saturday = datetime(2026, 10, 17, 9, 40, tzinfo=cest)  # the doc example
    monday_late = datetime(2026, 10, 19, 16, 0, tzinfo=cest)
    monday_early = datetime(2026, 10, 19, 15, 29, tzinfo=cest)
    assert is_on_time(saturday, saturday, open_)
    assert is_on_time(monday_early, monday_early, open_)  # one minute before the open
    assert not is_on_time(monday_late, monday_late, open_)
    assert not is_on_time(saturday, monday_late, open_)  # file changed after the open
    assert not is_on_time(monday_late, saturday, open_)  # decision made after the open
