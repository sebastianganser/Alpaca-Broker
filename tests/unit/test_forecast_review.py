"""Unit tests for analysis/forecast_review.py: persisted criterion state (no DB).

The decisive sample and its verdict are written ONCE (docs/FORECASTS.md
section 6); everything here runs on synthetic frames and an in-memory store.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis import forecast_eval as fe
from trading_signals.analysis import forecast_review as fr

NOW = datetime(2026, 10, 18, 4, 40, tzinfo=UTC)
MIN_DATES, MIN_SIDE = 120, 100
DAYS = pd.bdate_range("2023-01-02", periods=140)
LATEST = DAYS[-1].date()
FAR_LATER = date(2024, 6, 28)  # many NYSE sessions after the last frozen one


class MemoryStore:
    """In-memory CriterionStore with the same write-once rules as the SQL one."""

    def __init__(self) -> None:
        self.rows: dict[str, fr.SampleRecord] = {}
        self.add_calls = 0
        self.verdict_calls = 0

    def samples(self):
        return list(self.rows.values())

    def add_sample(self, record):
        self.add_calls += 1
        if record.sample_key in self.rows or any(
            (r.horizon, r.forecaster_version)
            == (record.horizon, record.forecaster_version)
            for r in self.rows.values()
        ):
            return False
        self.rows[record.sample_key] = record
        return True

    def set_verdict(self, sample_key, verdict, values, at):
        self.verdict_calls += 1
        row = self.rows[sample_key]
        if row.verdict is not None:
            return False
        row.verdict, row.verdict_values, row.verdict_at = verdict, values, at
        return True


def world(
    n_dates=140, n=60, k=24, skill=0.03, seed=5, version="skill/1", ret_noise=0.05
):
    """Gated universe (all rows gated), SPY, pack and DB-style forecast rows."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    uni_rows, fc_rows, spy_rows, pack_rows = [], [], [], []
    next_id = 1
    for d in dates:
        shock = rng.normal(scale=0.02)
        q = rng.normal(size=n)
        ret20 = shock + skill * q + rng.normal(scale=ret_noise, size=n)
        tickers = [f"T{i:03d}" for i in range(n)]
        uni_rows.append(
            pd.DataFrame(
                {
                    "snapshot_date": d,
                    "ticker": tickers,
                    "gate_ok": True,
                    "return_20d": ret20,
                }
            )
        )
        spy_rows.append({"snapshot_date": d, "return_20d": shock + 0.001})
        chosen = rng.choice(n, size=k, replace=False)
        for i in chosen:
            direction = "up" if q[i] > 0.4 else ("down" if q[i] < -0.4 else "neutral")
            fc_rows.append(
                {
                    "forecast_id": str(next_id),
                    "review_id": int(d.dayofyear),
                    "review_status": "ok",
                    "session_date": d,
                    "ticker": tickers[i],
                    "horizon_days": 20,
                    "direction": direction,
                    "confidence": float(np.clip(0.5 + 0.12 * abs(q[i]), 0.5, 0.9)),
                    "conviction": int(np.clip(1 + abs(q[i]) * 2, 1, 5)),
                    "forecaster_version": version,
                    "on_time": True,
                }
            )
            next_id += 1
        top = [tickers[i] for i in chosen[np.argsort(-q[chosen])][:8]]
        pack_rows += [{"session_date": d, "ticker": t, "bucket": "bull"} for t in top]
    return (
        pd.concat(uni_rows, ignore_index=True),
        pd.DataFrame(fc_rows),
        pd.DataFrame(spy_rows).set_index("snapshot_date"),
        pd.DataFrame(pack_rows),
    )


def run(store, uni, fc, spy, pack, latest=LATEST, **kw):
    eff = fr.effective_forecasts(fc)
    return fr.update_criterion(
        eff,
        fc,
        uni,
        spy,
        pack,
        store,
        latest,
        NOW,
        min_dates=MIN_DATES,
        min_per_side=MIN_SIDE,
        **kw,
    )


# ── effective forecasts ──────────────────────────────────────────────


def _rows(*specs):
    return pd.DataFrame(
        [
            {
                "forecast_id": str(i),
                "review_id": rid,
                "review_status": status,
                "session_date": pd.Timestamp(day),
                "ticker": t,
                "horizon_days": 20,
                "direction": "up",
                "on_time": on_time,
            }
            for i, (rid, status, day, t, on_time) in enumerate(specs, 1)
        ]
    )


def test_effective_forecasts_use_the_latest_review_per_session_and_flag():
    rows = _rows(
        (1, "ok", "2026-10-16", "A", True),
        (2, "ok", "2026-10-16", "A", True),  # supersedes review 1 (on-time)
        (2, "ok", "2026-10-16", "B", True),
        (3, "ok", "2026-10-16", "A", False),  # late edit: separate stream
        (4, "partial", "2026-10-19", "C", True),
        (5, "rejected", "2026-10-19", "D", True),  # never counts
    )
    eff = fr.effective_forecasts(rows)
    assert sorted(
        zip(eff["review_id"], eff["ticker"], eff["on_time"], strict=True)
    ) == [(2, "A", True), (2, "B", True), (3, "A", False), (4, "C", True)]
    assert fr.effective_forecasts(rows.iloc[0:0]).empty


# ── JSON helpers ─────────────────────────────────────────────────────


def test_to_jsonable_makes_numpy_pandas_and_nan_safe():
    obj = {
        "a": np.float64(0.5),
        "b": np.int64(3),
        "c": float("nan"),
        "d": date(2026, 1, 2),
        "e": pd.Timestamp("2026-01-02"),
        "f": np.bool_(True),
        "g": [np.nan, 1.5],
        "h": pd.NaT,
        "i": (1, 2),
        "j": {"k": float("inf")},
    }
    out = fr.to_jsonable(obj)
    assert out == {
        "a": 0.5,
        "b": 3,
        "c": None,
        "d": "2026-01-02",
        "e": "2026-01-02T00:00:00",
        "f": True,
        "g": [None, 1.5],
        "h": None,
        "i": [1, 2],
        "j": {"k": None},
    }
    import json

    json.dumps(out, allow_nan=False)
    with pytest.raises(TypeError):
        fr.to_jsonable(object())


def test_revive_verdict_restores_nan_bounds_only():
    stored = {
        "status": "passed",
        "decisive_mode": "system",
        "modes": {
            "system": {
                "status": "passed",
                "checks": {
                    "relative_up_minus_down": {
                        "estimate": 0.03,
                        "ci_low": None,
                        "ci_high": None,
                        "n_obs": 5,
                        "passed": False,
                    }
                },
            }
        },
        "checks": {},
    }
    chk = fr.revive_verdict(stored)["checks"]["relative_up_minus_down"]
    assert chk["estimate"] == 0.03 and np.isnan(chk["ci_low"]) and chk["n_obs"] == 5


# ── freeze: written once ─────────────────────────────────────────────


def test_nothing_is_frozen_before_the_sample_is_complete():
    uni, fc, spy, pack = world(n_dates=100)  # 120 sessions needed
    store = MemoryStore()
    update = run(store, uni, fc, spy, pack)
    assert store.rows == {} and update.frozen == [] and update.notes() == ""


def test_the_sample_freezes_once_and_is_never_overwritten():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    # windows of the last frozen sessions still open: frozen, but no verdict yet
    open_uni = uni.copy()
    open_uni.loc[open_uni["snapshot_date"].isin(DAYS[100:]), "return_20d"] = np.nan
    update = run(store, open_uni, fc, spy, pack, latest=DAYS[130].date())
    assert len(update.frozen) == 1 and update.verdicts == []
    rec = next(iter(store.rows.values()))
    assert (
        rec.verdict is None
        and rec.horizon == 20
        and rec.forecaster_version == "skill/1"
    )
    assert (rec.min_sessions, rec.min_per_side) == (MIN_DATES, MIN_SIDE)
    assert len(rec.sessions) == 120 and rec.sessions[0] == "2023-01-02"
    assert rec.n_up >= MIN_SIDE and rec.n_down >= MIN_SIDE
    assert all(isinstance(i, str) for i in rec.forecast_ids)
    assert fe.sample_key(fr.frozen_from_record(rec)) == rec.sample_key
    snapshot = copy.deepcopy(rec)

    # the next nights: more forecasts arrive, the frozen sample stays as it is
    later_fc = pd.concat(
        [
            fc,
            fc.assign(
                forecast_id=lambda d: "x" + d["forecast_id"],
                session_date=lambda d: d["session_date"] + pd.Timedelta(days=400),
                review_id=999,
            ),
        ]
    )
    again = run(store, open_uni, later_fc, spy, pack, latest=DAYS[131].date())
    assert again.frozen == [] and store.rows == {rec.sample_key: rec}
    assert rec == snapshot  # same ids, same sessions, same key


def test_a_writer_that_loses_the_race_is_reported_and_changes_nothing():
    class LostRace(MemoryStore):
        def add_sample(self, record):  # another run stored the sample first
            self.add_calls += 1
            return False

    uni, fc, spy, pack = world()
    store = LostRace()
    update = run(store, uni, fc, spy, pack)
    assert update.frozen == [] and store.rows == {}
    assert update.problems == ["sample 20d skill/1 already stored"]


def test_late_forecasts_never_enter_the_frozen_sample():
    uni, fc, spy, pack = world()
    late = fc.copy()
    late["forecast_id"] = "L" + late["forecast_id"]
    late["on_time"] = False
    late["review_id"] += 5000
    store = MemoryStore()
    run(store, uni, pd.concat([fc, late], ignore_index=True), spy, pack)
    rec = next(iter(store.rows.values()))
    assert rec.forecast_ids and not any(i.startswith("L") for i in rec.forecast_ids)


def test_a_new_forecaster_version_gets_its_own_sample():
    uni, fc, spy, pack = world(version="skill/1")
    fc2 = fc.assign(
        forecast_id=lambda d: "b" + d["forecast_id"],
        session_date=lambda d: d["session_date"] + pd.Timedelta(days=300),
        forecaster_version="skill/2",
        review_id=lambda d: d["review_id"] + 900,
    )
    uni2 = uni.assign(
        snapshot_date=lambda d: d["snapshot_date"] + pd.Timedelta(days=300)
    )
    spy2 = spy.set_axis(spy.index + pd.Timedelta(days=300))
    store = MemoryStore()
    run(store, uni, fc, spy, pack)
    assert [r.forecaster_version for r in store.samples()] == ["skill/1"]
    # the version changes: the finished sample keeps its verdict, a new one starts
    both_fc = pd.concat([fc, fc2], ignore_index=True)
    both_uni = pd.concat([uni, uni2], ignore_index=True)
    both_spy = pd.concat([spy, spy2])
    run(store, both_uni, both_fc, both_spy, pack, latest=date(2024, 12, 31))
    assert sorted(r.forecaster_version for r in store.samples()) == [
        "skill/1",
        "skill/2",
    ]
    first = next(r for r in store.samples() if r.forecaster_version == "skill/1")
    assert first.verdict is not None and first.verdict_at == NOW


def relabel(fc, blocks):
    """Give the sessions of ``fc`` the versions of ``blocks`` = [(version, n)]."""
    days = list(pd.bdate_range("2023-01-02", periods=sum(n for _, n in blocks)))
    label, i = {}, 0
    for version, n in blocks:
        for d in days[i : i + n]:
            label[d] = version
        i += n
    return fc.assign(forecaster_version=fc["session_date"].map(label))


def test_a_version_that_returns_after_another_one_is_never_frozen():
    # A 80 + B 20 + A 40: 120 A sessions in total (the old per-version rule
    # froze a gappy sample of them), but the first A run has only 80
    uni, fc, spy, pack = world()
    fc = relabel(fc, [("A", 80), ("B", 20), ("A", 40)])
    store = MemoryStore()
    update = run(store, uni, fc, spy, pack)
    assert store.rows == {} and update.frozen == []
    assert len(update.problems) == 1
    assert "A was used again" in update.problems[0]
    assert "choose a new version name" in update.problems[0]
    assert update.notes() == update.problems[0]


def test_a_changed_version_aborts_the_partial_sample_and_freezes_the_new_one():
    uni, fc, spy, pack = world(n_dates=200)
    fc = relabel(fc, [("A", 50), ("B", 150)])
    store = MemoryStore()
    update = run(store, uni, fc, spy, pack, latest=pd.bdate_range(
        "2023-01-02", periods=200)[-1].date())
    assert [r.forecaster_version for r in update.frozen] == ["B"]
    assert update.problems == []  # an aborted version is reported in the report only
    assert [r.forecaster_version for r in store.samples()] == ["B"]
    assert store.samples()[0].sessions[0] == "2023-03-13"  # B's first session


# ── verdict: computed once, from the persisted values ────────────────


def test_the_verdict_waits_for_the_returns_and_is_then_written_once():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    gap = uni.copy()
    gap.loc[gap["snapshot_date"].isin(DAYS[115:120]), "return_20d"] = np.nan
    waiting = run(store, gap, fc, spy, pack, latest=DAYS[125].date())
    assert (
        len(waiting.frozen) == 1 and waiting.verdicts == [] and store.verdict_calls == 0
    )
    rec = next(iter(store.rows.values()))
    assert rec.verdict is None and rec.verdict_values is None and rec.verdict_at is None

    done = run(store, uni, fc, spy, pack, latest=LATEST)
    assert done.frozen == [] and [s for _, s in done.verdicts] == ["passed"]
    assert rec.verdict["status"] == "passed" and rec.verdict_at == NOW
    assert rec.verdict["frozen"]["key"] == rec.sample_key
    assert rec.verdict["frozen"]["first_date"] == "2023-01-02"  # JSON-safe dates
    assert len(rec.verdict_values) == len(rec.forecast_ids)
    assert "values" not in rec.verdict  # stored separately, once
    assert store.verdict_calls == 1


def test_missing_returns_after_the_grace_period_are_reported_not_waited_for():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    gap = uni.copy()
    gap.loc[gap["snapshot_date"].isin(DAYS[115:120]), "return_20d"] = np.nan
    run(store, gap, fc, spy, pack, latest=FAR_LATER)
    rec = next(iter(store.rows.values()))
    assert rec.verdict["status"] == "passed" and rec.verdict["n_missing"] > 0
    assert len(rec.verdict_values) == len(rec.forecast_ids) - rec.verdict["n_missing"]


def test_a_stored_verdict_is_never_recomputed_from_live_returns():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    run(store, uni, fc, spy, pack)
    rec = next(iter(store.rows.values()))
    before = copy.deepcopy(rec)
    assert before.verdict["status"] == "passed"

    # a price correction reverses the early returns of the frozen window ...
    revised = uni.copy()
    early = revised["snapshot_date"].isin(DAYS[:60])
    revised.loc[early, "return_20d"] = -revised.loc[early, "return_20d"]
    live = fe.verdict_from_frozen(
        fr.frozen_from_record(rec), fe.attach_outcomes(fc, revised, spy), 20
    )
    assert live["checks"] != {
        k: v for k, v in rec.verdict["checks"].items()
    }  # live data really moved
    # ... the nightly step leaves the persisted verdict exactly as it was
    update = run(store, revised, fc, spy, pack)
    assert update.verdicts == [] and store.verdict_calls == 1
    assert rec == before


def test_the_stored_values_reproduce_the_stored_verdict():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    run(store, uni, fc, spy, pack)
    rec = next(iter(store.rows.values()))
    again = fr.recompute_verdict(rec)
    stored = fr.revive_verdict(rec.verdict)
    assert again["status"] == stored["status"] == "passed"
    for mode in fe.CRITERION_MODES:
        for name, chk in stored["modes"][mode]["checks"].items():
            new = again["modes"][mode]["checks"][name]
            assert new["estimate"] == pytest.approx(chk["estimate"])
            assert new["ci_low"] == pytest.approx(chk["ci_low"])
            assert new["passed"] == chk["passed"]
    with pytest.raises(ValueError):
        fr.recompute_verdict(
            fr.SampleRecord("k", 20, "v", "system", 1, 1, 1, 1, ["2023-01-02"], ["1"])
        )


def test_a_failed_verdict_is_stored_too():
    uni, fc, spy, pack = world(skill=0.0, seed=2)
    store = MemoryStore()
    update = run(store, uni, fc, spy, pack)
    assert [s for _, s in update.verdicts] == ["failed"]
    assert next(iter(store.rows.values())).verdict["status"] == "failed"


def test_a_tampered_sample_gets_no_verdict():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    open_uni = uni.copy()
    open_uni.loc[open_uni["snapshot_date"].isin(DAYS[100:]), "return_20d"] = np.nan
    run(store, open_uni, fc, spy, pack, latest=DAYS[125].date())
    rec = next(iter(store.rows.values()))
    rec.forecast_ids = rec.forecast_ids[:-1]  # no longer the sample of its key
    update = run(store, uni, fc, spy, pack)
    assert update.verdicts == [] and rec.verdict is None
    assert "does not match" in update.problems[0] and update.notes()


def test_a_superseded_review_row_of_the_sample_is_still_scored():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    open_uni = uni.copy()
    open_uni.loc[open_uni["snapshot_date"].isin(DAYS[100:]), "return_20d"] = np.nan
    run(store, open_uni, fc, spy, pack, latest=DAYS[125].date())
    rec = next(iter(store.rows.values()))
    # a later on-time review of the last frozen session replaces its rows
    last = pd.Timestamp(rec.sessions[-1])
    replaced = fc[fc["session_date"] == last]
    newer = replaced.assign(
        forecast_id=lambda d: "n" + d["forecast_id"],
        review_id=replaced["review_id"] + 7000,
    )
    all_rows = pd.concat([fc, newer], ignore_index=True)
    eff = fr.effective_forecasts(all_rows)
    assert not eff["forecast_id"].isin(replaced["forecast_id"]).any()
    update = fr.update_criterion(
        eff,
        all_rows,
        uni,
        spy,
        pack,
        store,
        LATEST,
        NOW,
        min_dates=MIN_DATES,
        min_per_side=MIN_SIDE,
    )
    assert [s for _, s in update.verdicts] == ["passed"]
    assert rec.verdict["n_missing"] == 0  # the old ids were found in all_forecasts


# ── what the evaluation and the report show ──────────────────────────


def test_criterion_state_hands_the_active_versions_sample_to_the_evaluation():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    run(store, uni, fc, spy, pack)
    eff = fr.effective_forecasts(fc)
    frozen, verdicts = fr.criterion_state(eff, store.samples())
    rec = next(iter(store.rows.values()))
    assert frozen[20]["key"] == rec.sample_key and verdicts[20]["status"] == "passed"
    # another version is active: its sample does not exist yet, nothing is handed over
    newer = eff.assign(
        forecaster_version="skill/2",
        forecast_id=lambda d: "z" + d["forecast_id"],
        session_date=lambda d: d["session_date"] + pd.Timedelta(days=400),
    )
    frozen2, verdicts2 = fr.criterion_state(
        pd.concat([eff, newer], ignore_index=True), store.samples()
    )
    assert frozen2 == {} and verdicts2 == {}
    assert fr.criterion_state(eff.iloc[0:0], store.samples()) == ({}, {})


def test_the_report_shows_the_persisted_verdict_and_sample():
    uni, fc, spy, pack = world()
    store = MemoryStore()
    run(store, uni, fc, spy, pack)
    eff = fr.effective_forecasts(fc)
    frozen, verdicts = fr.criterion_state(eff, store.samples())
    # a later correction of the live data must not change the reported verdict
    revised = uni.copy()
    revised["return_20d"] = -revised["return_20d"]
    result = fe.evaluate_forecasts(
        eff, revised, spy, pack, horizons=(20,), frozen=frozen, verdicts=verdicts
    )
    assert result["criterion"][20]["status"] == "passed"
    reviews = [
        {
            "session_date": date(2023, 7, 5),
            "forecaster_version": "skill/1",
            "status": "partial",
            "n_forecasts": 20,
            "n_rejected": 2,
            "on_time": True,
            "errors": ["forecasts[3] (MSFT 20d): Feld(er) stop nicht erlaubt"],
        }
    ]
    ingest = MagicMock(
        files=3,
        ingested=1,
        unchanged=2,
        pending=1,
        late_notes=["2026-10-15 (Datei vor der Eröffnung nicht versiegelt)"],
        forecasts=20,
        late=0,
        rejected_files=0,
        rejected_rows=2,
    )
    text = fr.build_report(result, store.samples(), reviews, ingest, date(2026, 10, 18))
    rec = next(iter(store.rows.values()))
    assert "**Urteil: bestanden**" in text and rec.sample_key[:12] in text
    assert "## Eingelesene Dateien" in text and "partial" in text
    assert "stop nicht erlaubt" in text
    assert "noch vor der Frist 1" in text
    assert "Verspätet eingelesen" in text and "nicht versiegelt" in text
    assert "## Gespeicherte Stichproben und Urteile (einmalig geschrieben)" in text
    assert "bestanden" in text.split("## Gespeicherte Stichproben")[1]
    assert text.rstrip().endswith("*Automatisch erzeugt · Keine Anlageberatung*")


def test_the_report_before_the_first_forecast_says_so():
    text = fr.build_report({"n_forecasts": 0}, [], [], None, date(2026, 10, 18))
    assert "Noch keine `forecasts.yaml` eingelesen." in text
    assert text.startswith("# Prognose-Auswertung – Stand 2026-10-18")
    ingest = MagicMock(
        files=0,
        ingested=0,
        unchanged=0,
        pending=0,
        late_notes=[],
        forecasts=0,
        late=0,
        rejected_files=0,
        rejected_rows=0,
    )
    assert "Dateien gefunden 0" in fr.build_report(
        {"n_forecasts": 0}, [], [], ingest, date(2026, 10, 18)
    )


def test_the_open_sample_is_listed_without_a_verdict():
    rec = fr.SampleRecord(
        "a" * 64,
        20,
        "skill/1",
        "system",
        300,
        100,
        150,
        140,
        ["2023-01-02", "2024-03-01"],
        ["1", "2"],
    )
    section = "\n".join(fr.render_samples_section([rec]))
    assert "Urteil ausstehend" in section and "aaaaaaaaaaaa" in section
    assert fr.render_samples_section([]) == []


# ── blinding: no running figures before the verdict ──────────────────

#: Rendered figure sections (the explanatory text of the blinded report names the
#: metrics in prose, so the markers are the headings / row labels).
FIGURE_MARKERS = (
    "## Horizont",
    "| Kennzahl |",
    "Auswahl-Trefferquote (querschnittlich",
    "Rang-IC (Konviktion",
    "- Kalibrierung:",
    "Brier",
    "### Absolut",
    "### Claude gegen Shortlist",
    "## Verspätete Prognosen",
    "Hoch-Prognosen Ø Rendite",
)


@pytest.fixture
def small_sample(monkeypatch):
    """Registered size 300 -> 120 sessions (per side stays 100) for the test world."""
    monkeypatch.setattr(fe, "CRITERION_MIN_SESSIONS", MIN_DATES)


def review(fc, uni, spy, pack, store, latest=LATEST, today=date(2026, 10, 18)):
    return fr.build_review(
        fc, [], uni, spy, pack, None, latest, store, None, today, NOW, lambda: None
    )


def no_figures(text):
    return [m for m in FIGURE_MARKERS if m in text]


def test_no_running_figures_before_the_sample_is_frozen(small_sample):
    uni, fc, spy, pack = world(n_dates=100)  # 120 sessions needed
    store = MemoryStore()
    res = review(fc, uni, spy, pack, store)
    assert res.update.frozen == [] and store.rows == {}
    text = res.report
    assert no_figures(text) == []
    assert "## Fortschritt je Prognose-Version (nur Zählungen)" in text
    assert "| `skill/1` | 20 Tage | 100 (2023-01-02 … 2023-05-19) |" in text
    assert "bleiben **verborgen**" in text
    assert "Noch kein Urteil: 100/120" in text.replace("**", "")


def test_no_running_figures_while_the_verdict_is_pending(small_sample):
    uni, fc, spy, pack = world()
    open_uni = uni.copy()
    open_uni.loc[open_uni["snapshot_date"].isin(DAYS[100:]), "return_20d"] = np.nan
    store = MemoryStore()
    res = review(fc, open_uni, spy, pack, store, latest=DAYS[125].date())
    assert len(res.update.frozen) == 1 and store.verdict_calls == 0
    assert no_figures(res.report) == []
    assert "Stichprobe eingefroren, Urteil ausstehend" in res.report
    # the frozen sample is listed with counts, not with a result
    assert "Urteil ausstehend (Fenster noch offen)" in res.report


def test_figures_appear_with_the_verdict_for_that_version(small_sample):
    uni, fc, spy, pack = world()
    store = MemoryStore()
    res = review(fc, uni, spy, pack, store)
    assert [s for _, s in res.update.verdicts] == ["passed"]
    text = res.report
    assert "**Urteil: bestanden**" in text
    assert "## Horizont 20 Handelstage – Version `skill/1`" in text
    assert "Rang-IC (Konviktion" in text and "- Kalibrierung:" in text
    assert "bleiben **verborgen**" not in text


def test_an_aborted_version_never_gets_figures_even_next_to_a_judged_one(small_sample):
    uni, fc, spy, pack = world(n_dates=200)
    fc = relabel(fc, [("A", 50), ("B", 150)])
    store = MemoryStore()
    latest = pd.bdate_range("2023-01-02", periods=200)[-1].date()
    res = review(fc, uni, spy, pack, store, latest=latest)
    assert [s for _, s in res.update.verdicts] == ["passed"]
    text = res.report
    # B is judged and shown; A is aborted: counts only, no figures of any kind
    assert "– Version `B`" in text and "– Version `A`" not in text
    assert "| `A` | 20 Tage | 50 (" in text and "| `B` | 20 Tage | 150 (" in text
    assert "abgebrochene Teilstichprobe" in text
    a_part = text.split("– Version `B`")[0]
    assert no_figures(a_part.split("## Kennzahlen")[0]) == []


def test_late_forecasts_of_an_unjudged_version_show_counts_only(small_sample):
    uni, fc, spy, pack = world(n_dates=100)
    late = fc.iloc[:30].assign(
        forecast_id=lambda d: "L" + d["forecast_id"], on_time=False, review_id=900
    )
    both = pd.concat([fc, late], ignore_index=True)
    res = review(both, uni, spy, pack, MemoryStore())
    assert no_figures(res.report) == []
    assert "| 30 |" in res.report  # the late count in the progress table


def _wired_run(fc, uni, spy, pack, store):
    """``evaluate_and_persist`` with the database replaced by in-memory pieces."""
    from contextlib import contextmanager
    from unittest.mock import patch

    session = MagicMock()

    @contextmanager
    def fake_session():
        yield session

    with (
        patch("trading_signals.db.session.get_session", fake_session),
        patch.object(fr, "load_forecast_rows", return_value=fc),
        patch.object(fr, "load_review_overview", return_value=[]),
        patch.object(fr, "SqlCriterionStore", return_value=store),
        patch.object(fr, "load_universe_returns", return_value=uni),
        patch.object(fr, "load_pack_candidates", return_value=pack),
        patch.object(fr, "load_long_run_shares", return_value=None),
        patch.object(fr, "load_latest_session", return_value=LATEST),
        patch(
            "trading_signals.analysis.benchmark.load_benchmark_returns",
            return_value=spy.reset_index(),
        ),
    ):
        return fr.evaluate_and_persist(None, date(2026, 10, 18), NOW), session


def test_the_nightly_step_is_wired_to_the_blinded_report(small_sample):
    uni, fc, spy, pack = world(n_dates=100)  # not frozen yet
    result, session = _wired_run(fc, uni, spy, pack, MemoryStore())
    assert no_figures(result.report) == [] and "bleiben **verborgen**" in result.report
    session.commit.assert_called_once()  # the freeze / verdict are committed first

    uni, fc, spy, pack = world()  # frozen and judged in one run
    store = MemoryStore()
    result, _ = _wired_run(fc, uni, spy, pack, store)
    assert [s for _, s in result.update.verdicts] == ["passed"]
    assert "## Horizont 20 Handelstage – Version `skill/1`" in result.report


def test_revealed_versions_are_exactly_those_with_a_stored_verdict():
    done = fr.SampleRecord("a" * 64, 20, "v1", "system", 300, 100, 1, 1, ["x"], ["1"])
    done.verdict, done.verdict_values = {"status": "passed"}, []
    open_ = fr.SampleRecord("b" * 64, 20, "v2", "system", 300, 100, 1, 1, ["x"], ["1"])
    assert fr.revealed_versions([done, open_]) == {"v1"}
    assert fr.revealed_versions([]) == set()


# ── the SQL store (statements only, no connection) ───────────────────


def test_sql_store_writes_are_guarded_against_overwriting():
    from sqlalchemy.dialects import postgresql

    session = MagicMock()
    session.execute.return_value.rowcount = 1
    store = fr.SqlCriterionStore(session)
    rec = fr.SampleRecord(
        "k" * 64, 20, "v", "system", 300, 100, 1, 1, ["2023-01-02"], ["1"]
    )
    assert store.add_sample(rec) is True
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT DO NOTHING" in sql  # never overwrites a stored sample
    session.execute.return_value.rowcount = 0
    assert store.add_sample(rec) is False

    assert store.set_verdict("k" * 64, {"status": "passed"}, [], NOW) is False
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert sql.startswith("UPDATE") and "verdict IS NULL" in sql  # write once


# ── the gate of the evaluation is the gate of the screen ─────────────


def test_gate_flags_equal_the_gate_of_the_screen():
    from tests.unit.screen_data import cross_section
    from trading_signals.analysis.screen import screen_cross_section

    df = cross_section("2024-03-01", n=150, seed=7)
    rng = np.random.default_rng(1)
    # thin liquidity, no history (NULL SMA), and tickers with most groups missing
    df.loc[df.index[:10], "dollar_volume_20d"] = 1e6
    df.loc[df.index[10:20], "dollar_volume_20d"] = np.nan
    df.loc[df.index[20:30], "price_vs_sma50"] = np.nan
    from trading_signals.analysis.screen import scored_features

    sparse = df.index[30:60]
    for col in rng.choice(scored_features(), size=35, replace=False):
        df.loc[sparse, col] = np.nan
    expected = screen_cross_section(df)["gate_ok"].to_numpy()
    flags = fr.gate_flags(df).to_numpy()
    assert flags.tolist() == expected.tolist()
    assert 0 < flags.sum() < len(df)  # the fixture exercises both outcomes
