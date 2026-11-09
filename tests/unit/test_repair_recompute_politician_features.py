"""Tests for scripts/repair/recompute_politician_features.py (pure parts, no DB)."""

import importlib.util
import random
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rep = _load(
    "scripts/repair/recompute_politician_features.py", "recompute_politician_features"
)
COLS = rep.COLS
D1, D2, D3 = date(2025, 1, 2), date(2026, 1, 5), date(2026, 1, 6)


def frame(rows: dict[date, list]) -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.array([r[i] for r in rows.values()], dtype="Int64")
         for i, c in enumerate(COLS)},
        index=list(rows),
    )


def berlin(h, m, tz_name="Europe/Berlin"):
    from zoneinfo import ZoneInfo

    return datetime(2026, 11, 10, h, m, tzinfo=ZoneInfo(tz_name))


# ── time guard ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "h,m,blocked",
    [(4, 14, False), (4, 15, True), (4, 30, True), (5, 14, True), (5, 15, False),
     (12, 0, False), (0, 0, False)],
)
def test_time_guard_berlin(h, m, blocked):
    assert rep.in_blocked_window(berlin(h, m)) is blocked


def test_time_guard_converts_other_timezones():
    # 03:30 UTC in November = 04:30 Berlin (CET)
    assert rep.in_blocked_window(datetime(2026, 11, 10, 3, 30, tzinfo=UTC))
    assert not rep.in_blocked_window(datetime(2026, 11, 10, 4, 30, tzinfo=UTC))


def test_time_guard_rejects_naive_datetime():
    with pytest.raises(ValueError):
        rep.in_blocked_window(datetime(2026, 11, 10, 4, 30))


# ── NULL-aware diff ──────────────────────────────────────────────────


def test_changed_mask_null_aware():
    cur = frame({D1: [None, 0, 1, None], D2: [1, 1, 1, 1], D3: [None, None, 2, 0]})
    new = frame({D1: [None, 0, 1, None], D2: [2, 1, None, 1], D3: [0, None, 2, None]})
    m = rep.changed_mask(cur, new)
    assert not m.loc[D1].any()  # NA == NA and equal numbers are unchanged
    assert m.loc[D2].tolist() == [True, False, True, False]
    assert m.loc[D3].tolist() == [True, False, False, True]


def test_zero_is_not_null():
    cur = frame({D1: [0, 0, 0, 0]})
    new = frame({D1: [None, None, None, None]})
    assert rep.changed_mask(cur, new).loc[D1].all()
    assert rep.changed_mask(new, cur).loc[D1].all()


def test_stats_counts_and_summary():
    cur = frame({D1: [None] * 4, D2: [1, 1, 1, 1], D3: [3, 3, 3, 3]})
    new = frame({D1: [0, 0, 0, 0], D2: [1, 1, None, 1], D3: [3, 3, 3, 3]})
    s = rep.Stats()
    mask = s.add("AAA", cur, new)
    assert mask.any(axis=1).tolist() == [True, True, False]
    assert s.scanned == 3 and s.changed_rows == 2
    assert s.null_to_value[COLS[0]] == 1
    assert s.value_to_null[COLS[2]] == 1
    assert s.changed[COLS[1]] == 1 and s.changed[COLS[2]] == 2
    assert s.per_year == {2025: 1, 2026: 1}
    text = "\n".join(s.lines())
    assert "rows scanned: 3" in text and "AAA 2025-01-02" in text
    assert "NULL -> 0" in text


def test_stats_example_limit():
    idx = [date(2025, 1, 1 + i) for i in range(20)]
    cur = frame({d: [None] * 4 for d in idx})
    new = frame({d: [1] * 4 for d in idx})
    s = rep.Stats()
    s.add("X", cur, new)
    assert len(s.examples) == rep.MAX_EXAMPLES and s.changed_rows == 20


# ── verification sample ──────────────────────────────────────────────


def test_pick_keys_is_seeded_and_balanced():
    idx = [date(2025, 1, 1 + i) for i in range(30)]
    cov = frame({d: [1, 1, 1, 1] for d in idx})
    unc = frame({d: [None] * 4 for d in idx})
    a = rep.pick_verification_keys({"C": cov, "U": unc}, 10, random.Random(1))
    b = rep.pick_verification_keys({"C": cov, "U": unc}, 10, random.Random(1))
    assert a == b and len(a) == 10
    assert sum(t == "C" for t, _ in a) == 5


def test_pick_keys_fills_from_other_class_and_caps():
    idx = [date(2025, 1, 1 + i) for i in range(4)]
    cov = frame({d: [0, 0, 0, 0] for d in idx})
    keys = rep.pick_verification_keys({"C": cov}, 10, random.Random(1))
    assert len(keys) == 4  # only 4 exist
    unc = frame({d: [None] * 4 for d in idx})
    keys = rep.pick_verification_keys({"C": cov, "U": unc}, 6, random.Random(1))
    assert len(keys) == 6


def test_expected_row_empty_dict_is_all_none():
    assert rep.expected_row({}) == dict.fromkeys(COLS)
    assert rep.expected_row({COLS[0]: 2, COLS[1]: 0})[COLS[1]] == 0


# ── arguments ────────────────────────────────────────────────────────


def test_argument_defaults():
    a = rep.parse_args([])
    assert a.start is None and a.end is None and a.tickers is None
    assert a.verify == 200 and a.batch == rep.DEFAULT_BATCH and a.apply is False


def test_arguments_parsed():
    a = rep.parse_args(
        ["--start", "2024-01-02", "--end", "2024-02-01", "--tickers", "aapl,msft",
         "nvda", "--verify", "5", "--batch", "3", "--apply"]
    )
    assert a.start == date(2024, 1, 2) and a.end == date(2024, 2, 1)
    assert a.tickers == ["AAPL", "MSFT", "NVDA"]
    assert (a.verify, a.batch, a.apply) == (5, 3, True)


def test_batch_must_be_positive():
    with pytest.raises(SystemExit):
        rep.parse_args(["--batch", "0"])


# ── main (everything DB-related patched) ─────────────────────────────


@contextmanager
def fake_session(read_only):
    s = MagicMock()
    s.read_only = read_only
    yield s


def _patched(now, mismatches, snaps=None):
    snaps = snaps if snaps is not None else {
        "AAA": frame({D1: [None] * 4, D2: [1, 1, 1, 1]})
    }
    new = {"AAA": frame({D1: [0, 0, 0, 0], D2: [1, 1, 1, 1]})}
    fake_dt = MagicMock()
    fake_dt.now.return_value = now
    return [
        patch.object(rep, "announce_db_target"),
        patch.object(rep, "setup_logging"),
        patch.object(rep, "datetime", fake_dt),
        patch.object(rep, "repair_session", fake_session),
        patch.object(rep, "last_snapshot_date", return_value=D3),
        patch.object(rep, "snapshot_tickers", return_value=["AAA"]),
        patch.object(rep, "trade_tickers", return_value={"AAA"}),
        patch.object(rep, "verify", return_value=mismatches),
        patch.object(rep, "load_snapshots", return_value=snaps),
        patch.object(rep, "load_trades", return_value={}),
        patch.object(rep, "compute_for", return_value=new),
        patch.object(rep, "write_changes", return_value=1),
    ]


def _run(argv, now, mismatches=()):
    ctx = _patched(now, list(mismatches))
    for p in ctx:
        p.start()
    try:
        return rep.main(argv), ctx
    finally:
        patch.stopall()


def test_main_refuses_inside_time_window(capsys):
    code, _ = _run([], berlin(4, 30))
    assert code == 1
    assert "REFUSED" in capsys.readouterr().out


def test_main_dry_run_writes_nothing(capsys):
    with patch.object(rep, "write_changes") as w:
        code, _ = _run([], berlin(12, 0))
        w.assert_not_called()
    out = capsys.readouterr().out
    assert code == 0 and "dry run done" in out and "rows that would change: 1" in out


def test_main_dry_run_mismatch_exits_nonzero(capsys):
    code, _ = _run([], berlin(12, 0), ["MISMATCH AAA 2025-01-02"])
    assert code == 1
    assert "MISMATCH AAA" in capsys.readouterr().out


def test_main_apply_refuses_after_failed_verification(capsys):
    ctx = _patched(berlin(12, 0), ["MISMATCH AAA 2025-01-02"])
    wc = ctx[-1]
    for p in ctx[:-1]:
        p.start()
    mock_write = wc.start()
    try:
        code = rep.main(["--apply"])
    finally:
        patch.stopall()
    assert code == 1
    mock_write.assert_not_called()
    assert "REFUSED: verification failed" in capsys.readouterr().out


def test_main_apply_writes_changed_rows(capsys):
    ctx = _patched(berlin(12, 0), [])
    for p in ctx[:-1]:
        p.start()
    mock_write = ctx[-1].start()
    try:
        code = rep.main(["--apply"])
    finally:
        patch.stopall()
    assert code == 0
    mock_write.assert_called_once()
    assert "applied: 1 rows updated" in capsys.readouterr().out
