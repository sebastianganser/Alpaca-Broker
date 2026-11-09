"""Tests for scripts/repair/reingest_forecast_file.py (no DB, fakes only)."""

import hashlib
import importlib.util
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rep = _load("scripts/repair/reingest_forecast_file.py", "reingest_forecast_file")

D = date(2026, 11, 2)
OPEN = datetime(2026, 11, 3, 14, 30, tzinfo=UTC)  # 09:30 New York
SEALED = datetime(2026, 11, 3, 14, 25, tzinfo=UTC)
KNOWN = {"NVDA", "MSFT", "ON"}

TEXT = """\
schema: forecasts/v1
session: 2026-11-02
decided_at: 2026-11-03T08:00:00+00:00
forecaster_version: skill/1+opus
forecasts:
  - ticker: NVDA
    horizon_days: 20
    direction: up
    confidence: 0.6
    conviction: 3
  - ticker: ON
    horizon_days: 20
    direction: up
    confidence: 0.6
    conviction: 3
  - ticker: ON
    horizon_days: 60
    direction: neutral
  - ticker: ZZZZ
    horizon_days: 20
    direction: neutral
"""
RAW = TEXT.encode("utf-8")
SHA = hashlib.sha256(RAW).hexdigest()


def _review(**kw) -> "rep.StoredReview":
    base = dict(
        id=7, session_date=D, file_sha256=SHA, on_time=True, status="partial",
        n_forecasts=1, n_rejected=3,
    )
    return rep.StoredReview(**{**base, **kw})


def _plan(raw=RAW, sealed=None, review="default", stored=None, known=KNOWN):
    return rep.build_plan(
        raw,
        D,
        {SHA: SEALED} if sealed is None else sealed,
        _review() if review == "default" else review,
        {("NVDA", 20)} if stored is None else stored,
        known,
        open_fn=lambda d: OPEN,
    )


def test_hash_not_sealed_refuses():
    with pytest.raises(rep.RepairRefusedError, match="not a stored seal"):
        _plan(sealed={"0" * 64: SEALED})
    with pytest.raises(rep.RepairRefusedError, match="not a stored seal"):
        _plan(raw=RAW + b"\n# edited after the seal\n")


def test_unreviewed_file_refuses():
    with pytest.raises(rep.RepairRefusedError, match="no stored review"):
        _plan(review=None)
    with pytest.raises(rep.RepairRefusedError, match="no stored review"):
        _plan(review=_review(file_sha256="f" * 64))


def test_only_the_formerly_rejected_rows_are_added():
    plan = _plan()
    assert [(f.ticker, f.horizon_days) for f in plan.to_add] == [("ON", 20), ("ON", 60)]
    assert plan.already_stored == [("NVDA", 20)]
    # ZZZZ has no snapshot: still rejected, for its own reason
    assert [r.ticker for r in plan.still_rejected] == ["ZZZZ"]
    assert (plan.n_forecasts, plan.n_rejected, plan.status) == (3, 1, "partial")
    assert len(plan.errors) == 1 and "ZZZZ" in plan.errors[0]


def test_added_rows_carry_the_regular_metadata():
    on = _plan().to_add[0]
    assert on.on_time is True and on.valid_from == OPEN
    assert on.session_date == D and on.forecaster_version == "skill/1+opus"
    assert on.decided_at == datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    assert (on.direction, on.confidence, on.conviction) == ("up", 0.6, 3)


def test_rerun_after_repair_adds_nothing():
    stored = {("NVDA", 20), ("ON", 20), ("ON", 60)}
    plan = _plan(stored=stored)
    assert plan.to_add == [] and len(plan.already_stored) == 3


def test_late_seal_keeps_the_rows_late_and_must_match_the_review():
    late = {SHA: datetime(2026, 11, 3, 14, 31, tzinfo=UTC)}
    with pytest.raises(rep.RepairRefusedError, match="on_time"):
        _plan(sealed=late)  # review says on time
    plan = _plan(sealed=late, review=_review(on_time=False))
    assert all(f.on_time is False for f in plan.to_add)


def test_no_snapshot_on_the_day_refuses():
    with pytest.raises(rep.RepairRefusedError, match="no feature snapshot"):
        _plan(known=set())


def test_clean_file_becomes_ok():
    plan = _plan(known=KNOWN | {"ZZZZ"})
    assert plan.status == "ok" and plan.n_rejected == 0 and plan.errors == []


def test_summary_lists_each_added_row():
    lines = rep.summarize(_plan(), D)
    assert any("+ ON 20d up" in line for line in lines)
    assert any("+ ON 60d neutral" in line for line in lines)


def test_apply_plan_inserts_and_updates_review():
    session = MagicMock()
    added = rep.apply_plan(session, _plan())
    assert added == 2
    assert session.add.call_count == 2
    assert {c.args[0].ticker for c in session.add.call_args_list} == {"ON"}
    assert {c.args[0].review_id for c in session.add.call_args_list} == {7}
    session.execute.assert_called_once()  # the review update


def test_dry_run_writes_nothing(tmp_path: Path, capsys):
    folder = tmp_path / D.isoformat()
    folder.mkdir()
    (folder / "forecasts.yaml").write_bytes(RAW)
    session = MagicMock()

    @contextmanager
    def fake_session(read_only):
        assert read_only is True
        yield session

    with (
        patch.object(rep, "announce_db_target"),
        patch.object(rep, "repair_session", fake_session),
        patch.object(
            rep, "load_inputs",
            return_value=({SHA: SEALED}, _review(), {("NVDA", 20)}, KNOWN),
        ),
        patch.object(rep, "apply_plan") as apply,
    ):
        code = rep.main(["--session", D.isoformat(), "--root", str(tmp_path)])
    assert code == 0
    apply.assert_not_called()
    session.commit.assert_not_called()
    assert "dry run done" in capsys.readouterr().out
