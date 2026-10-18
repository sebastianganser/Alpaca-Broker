"""The nightly step ``forecast_review`` (scheduler/jobs.py, no DB)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from trading_signals.analysis.forecast_review import (
    CriterionUpdate,
    ReviewResult,
    SampleRecord,
)
from trading_signals.derived.forecasts import IngestResult
from trading_signals.scheduler import jobs
from trading_signals.utils import job_status

REPORT = "# Prognose-Auswertung\n"
BOM = b"\xef\xbb\xbf"


def _run(
    tmp_path,
    ingest: IngestResult,
    update: CriterionUpdate | None = None,
    root_exists: bool = True,
):
    root = tmp_path if root_exists else tmp_path / "missing"
    review = ReviewResult(
        report=REPORT, update=update or CriterionUpdate(), n_forecasts=3
    )
    settings = SimpleNamespace(CONTEXT_PACK_PATH=str(root))
    with (
        patch("trading_signals.config.get_settings", return_value=settings),
        patch(
            "trading_signals.analysis.forecast_review.ingest_new_files",
            return_value=ingest,
        ) as ing,
        patch(
            "trading_signals.analysis.forecast_review.evaluate_and_persist",
            return_value=review,
        ) as ev,
    ):
        out = jobs._forecast_review_body()
    return out, ing, ev


def test_happy_path_writes_the_report_with_bom_and_reports_success(tmp_path):
    ingest = IngestResult(files=2, ingested=1, unchanged=1, forecasts=3)
    out, ing, ev = _run(tmp_path, ingest)
    assert out.status == job_status.SUCCESS
    assert (out.records_written, out.records_fetched) == (3, 2)
    assert out.notes == ingest.notes() and "criterion" not in out.notes
    written = tmp_path / "forecast_auswertung.md"
    assert written.read_bytes().startswith(BOM)  # UTF-8 with BOM like the packs
    assert written.read_text(encoding="utf-8-sig").splitlines() == REPORT.splitlines()
    ing.assert_called_once_with(tmp_path)
    # phase 2 gets the ingest result (for the file overview)
    assert ev.call_args.args[0] is ingest


def test_no_files_is_a_success_and_still_writes_the_report(tmp_path):
    out, _, _ = _run(tmp_path, IngestResult())
    assert out.status == job_status.SUCCESS and out.records_written == 0
    assert (tmp_path / "forecast_auswertung.md").is_file()


def test_rejected_files_or_rows_make_the_run_partial(tmp_path):
    ingest = IngestResult(
        files=1,
        ingested=1,
        rejected_rows=1,
        errors=["2026-10-16: forecasts[2] (MSFT 20d): doppelt"],
    )
    out, _, _ = _run(tmp_path, ingest)
    assert out.status == job_status.PARTIAL and "doppelt" in out.notes


def test_a_new_freeze_and_verdict_are_noted(tmp_path):
    rec = SampleRecord(
        "ab" * 32, 20, "skill/1", "system", 300, 100, 150, 140, ["2023-01-02"], ["1"]
    )
    update = CriterionUpdate(frozen=[rec], verdicts=[(rec, "passed")])
    out, _, _ = _run(tmp_path, IngestResult(), update)
    assert out.status == job_status.SUCCESS
    assert (
        "criterion: frozen 20d skill/1 (abababab); verdict 20d skill/1: passed"
        in out.notes
    )


def test_a_criterion_problem_makes_the_run_partial(tmp_path):
    update = CriterionUpdate(problems=["sample 20d v1 already stored"])
    out, _, _ = _run(tmp_path, IngestResult(), update)
    assert out.status == job_status.PARTIAL and "already stored" in out.notes


def test_missing_pack_folder_writes_no_report_and_does_not_crash(tmp_path):
    out, _, _ = _run(tmp_path, IngestResult(), root_exists=False)
    assert out.status == job_status.SUCCESS
    assert not (tmp_path / "missing").exists()


def test_run_forecast_review_is_logged_under_its_own_name():
    with patch.object(jobs, "run_logged_job") as logged:
        jobs.run_forecast_review()
    assert logged.call_args.args == (
        "forecast_review",
        "forecast_review",
        jobs._forecast_review_body,
    )
