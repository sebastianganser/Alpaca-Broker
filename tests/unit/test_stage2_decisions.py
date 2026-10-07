"""Unit tests for derived/stage2_decisions.py (concept §7.7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_signals.derived.stage2_decisions import (
    DecisionFileError,
    is_on_time,
    next_session_open,
    pack_ranks,
    parse_decisions,
)

D = date(2026, 10, 6)

VALID = """
schema: stage2-decisions/v1
session: 2026-10-06
decided_at: 2026-10-07T09:40:00+02:00
decisions:
  - ticker: nvda
    action: buy
    reason: "Guidance bestätigt"
    limit: 182.5
    target_pct: 1.0
    stop_pct: -2.0
  - ticker: MSFT
    action: NO_ENTRY
"""


def test_parse_valid_file():
    r = parse_decisions(VALID, D)
    assert r.session_date == D
    assert r.decided_at.utcoffset() == timedelta(hours=2)
    assert [(d.ticker, d.action) for d in r.decisions] == [("NVDA", "buy"), ("MSFT", "no_entry")]
    assert r.decisions[0].limit_price == 182.5
    assert r.decisions[0].reason == "Guidance bestätigt"
    assert r.decisions[1].reason is None


def test_empty_decisions_means_reviewed_without_entry():
    text = "schema: stage2-decisions/v1\nsession: 2026-10-06\n" \
           "decided_at: 2026-10-07T09:40:00+02:00\ndecisions: []\n"
    assert parse_decisions(text, D).decisions == []


def test_naive_decided_at_is_berlin_time():
    text = VALID.replace("2026-10-07T09:40:00+02:00", "2026-10-07T09:40:00")
    r = parse_decisions(text, D)
    assert r.decided_at.utcoffset() == timedelta(hours=2)  # CEST in October


@pytest.mark.parametrize("old,new,msg", [
    ("schema: stage2-decisions/v1", "schema: v0", "schema"),
    ("session: 2026-10-06", "session: 2026-10-05", "passt nicht zum Ordner"),
    ("decided_at: 2026-10-07T09:40:00+02:00",
     "decided_at: JJJJ-MM-TTTHH:MM:SS+HH:MM", "decided_at"),
    ("action: buy", "action: kaufen", "action"),
    ("ticker: MSFT", "ticker: nvda", "doppelt"),
    ("ticker: MSFT", "ticker: 'MS FT'", "ungültiger Ticker"),
    ("limit: 182.5", "limit: abc", "keine Zahl"),
])
def test_invalid_files_raise(old, new, msg):
    with pytest.raises(DecisionFileError, match=msg):
        parse_decisions(VALID.replace(old, new, 1), D)


def test_template_placeholder_is_rejected():
    from trading_signals.derived.context_pack_generator import decisions_template

    text = "\n".join(decisions_template(D, ["NVDA"]))
    with pytest.raises(DecisionFileError, match="decided_at"):
        parse_decisions(text, D)


def test_no_yaml_object_raises():
    with pytest.raises(DecisionFileError):
        parse_decisions("- a\n- b\n", D)
    with pytest.raises(DecisionFileError, match="YAML"):
        parse_decisions("schema: [unclosed", D)


def test_next_session_open_skips_weekend():
    # Friday 2026-10-09 → Monday 2026-10-12 09:30 New York (= 13:30 UTC, EDT)
    o = next_session_open(date(2026, 10, 9))
    assert o.astimezone(UTC) == datetime(2026, 10, 12, 13, 30, tzinfo=UTC)


def test_is_on_time_requires_decision_and_file_before_open():
    open_ = datetime(2026, 10, 7, 13, 30, tzinfo=UTC)
    early = datetime(2026, 10, 7, 9, 0, tzinfo=timezone(timedelta(hours=2)))
    late = datetime(2026, 10, 7, 16, 0, tzinfo=UTC)
    assert is_on_time(early, early, open_)
    assert not is_on_time(late, early, open_)
    assert not is_on_time(early, late, open_)  # file changed after the open


def test_pack_ranks_reads_candidate_files(tmp_path: Path):
    for name in ("00_uebersicht.md", "01_NVDA.md", "02_BRK.B.md", "decisions.yaml"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert pack_ranks(tmp_path) == {"NVDA": 1, "BRK.B": 2}


# ── ingest_decisions with a mocked session ──

def _pack(root: Path, day: str, content: str | None, ranks=("NVDA",)) -> Path:
    d = root / day
    d.mkdir()
    for i, t in enumerate(ranks, 1):
        (d / f"{i:02d}_{t}.md").write_text("x", encoding="utf-8")
    if content is not None:
        (d / "decisions.yaml").write_text(content, encoding="utf-8")
    return d


def _ingest(root: Path, session):
    from trading_signals.derived.stage2_decisions import ingest_decisions

    # entry open far in the future → files written now are on time
    far = datetime(2100, 1, 1, tzinfo=UTC)
    return ingest_decisions(session, root, today=date(2026, 10, 7), open_fn=lambda d: far)


def test_ingest_new_invalid_and_unchanged(tmp_path: Path):
    from unittest.mock import MagicMock

    from trading_signals.db.models.stage2 import Stage2Decision, Stage2Review

    _pack(tmp_path, "2026-10-06", VALID, ranks=("NVDA", "AAPL"))
    _pack(tmp_path, "2026-10-05", VALID)  # session 2026-10-06 ≠ folder → error
    _pack(tmp_path, "2026-10-02", None)  # no decisions.yaml → not counted
    _pack(tmp_path, "2026-07-01", VALID)  # outside the 45-day window
    (tmp_path / "stage2_auswertung.md").write_text("x", encoding="utf-8")

    session = MagicMock()
    session.get.return_value = None
    res = _ingest(tmp_path, session)
    assert (res.files, res.ingested, res.unchanged) == (2, 1, 0)
    assert len(res.errors) == 1 and res.errors[0].startswith("2026-10-05")

    added = [c.args[0] for c in session.add.call_args_list]
    review = next(a for a in added if isinstance(a, Stage2Review))
    assert review.session_date == D and review.on_time
    assert (review.n_buy, review.n_no_entry) == (1, 1)
    decs = {a.ticker: a for a in added if isinstance(a, Stage2Decision)}
    assert decs["NVDA"].pack_rank == 1 and decs["NVDA"].action == "buy"
    assert decs["MSFT"].pack_rank is None  # not in the pack

    # same content again → unchanged, nothing written
    session2 = MagicMock()
    session2.get.side_effect = lambda model, key: (
        review if key == D else None
    )
    res2 = _ingest(tmp_path, session2)
    assert res2.unchanged == 1 and res2.ingested == 0
    assert not any(isinstance(c.args[0], Stage2Review) for c in session2.add.call_args_list)


def test_ingest_changed_file_replaces_day(tmp_path: Path):
    from unittest.mock import MagicMock

    d = _pack(tmp_path, "2026-10-06", VALID)
    old = MagicMock(content_sha256="old")
    session = MagicMock()
    session.get.return_value = old
    (d / "decisions.yaml").write_text(VALID.replace("action: buy", "action: no_entry"),
                                      encoding="utf-8")
    res = _ingest(tmp_path, session)
    assert res.ingested == 1
    session.delete.assert_called_once_with(old)
    assert session.execute.called  # old decisions deleted
