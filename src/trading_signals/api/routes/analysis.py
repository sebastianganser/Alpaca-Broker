"""API routes for feature analysis reports.

Provides endpoints to view analysis reports, trigger manual analysis runs,
and list historical reports.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    AnalysisReportItem,
    AnalysisReportListResponse,
    TriggerResponse,
)
from trading_signals.db.models.analysis import AnalysisReport

router = APIRouter(prefix="/analysis")


@router.get("/latest")
def get_latest_report(db: Session = Depends(get_db)) -> dict:
    """Get the most recent analysis report."""
    stmt = (
        select(AnalysisReport)
        .order_by(AnalysisReport.report_date.desc())
        .limit(1)
    )
    report = db.execute(stmt).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No analysis reports found")
    return _report_to_dict(report)


@router.get("/latest/html")
def get_latest_report_html(db: Session = Depends(get_db)):
    """Get the HTML report of the latest analysis."""
    from fastapi.responses import HTMLResponse

    stmt = (
        select(AnalysisReport)
        .order_by(AnalysisReport.report_date.desc())
        .limit(1)
    )
    report = db.execute(stmt).scalar_one_or_none()
    if not report or not report.html_report:
        raise HTTPException(status_code=404, detail="No HTML report available")
    return HTMLResponse(content=report.html_report)


@router.get("/list", response_model=AnalysisReportListResponse)
def list_reports(
    limit: int = 12,
    db: Session = Depends(get_db),
) -> AnalysisReportListResponse:
    """List all analysis reports (most recent first)."""
    stmt = (
        select(AnalysisReport)
        .order_by(AnalysisReport.report_date.desc())
        .limit(limit)
    )
    reports = db.execute(stmt).scalars().all()
    return AnalysisReportListResponse(
        reports=[
            AnalysisReportItem(
                report_date=r.report_date,
                snapshot_count=r.snapshot_count,
                ticker_count=r.ticker_count,
                date_range_start=r.date_range_start,
                date_range_end=r.date_range_end,
                computation_time_seconds=r.computation_time_seconds,
                computed_at=r.computed_at,
                has_html=r.html_report is not None,
            )
            for r in reports
        ],
        total=len(reports),
    )


@router.get("/{report_date}")
def get_report_by_date(
    report_date: date,
    db: Session = Depends(get_db),
) -> dict:
    """Get a specific analysis report by date."""
    stmt = select(AnalysisReport).where(
        AnalysisReport.report_date == report_date
    )
    report = db.execute(stmt).scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"No report found for {report_date}",
        )
    return _report_to_dict(report)


@router.get("/{report_date}/html")
def get_report_html_by_date(
    report_date: date,
    db: Session = Depends(get_db),
):
    """Get the HTML report for a specific date."""
    from fastapi.responses import HTMLResponse

    stmt = select(AnalysisReport).where(
        AnalysisReport.report_date == report_date
    )
    report = db.execute(stmt).scalar_one_or_none()
    if not report or not report.html_report:
        raise HTTPException(
            status_code=404,
            detail=f"No HTML report found for {report_date}",
        )
    return HTMLResponse(content=report.html_report)


@router.post("/trigger", response_model=TriggerResponse)
def trigger_analysis(db: Session = Depends(get_db)) -> TriggerResponse:
    """Manually trigger a feature analysis run."""
    try:
        from trading_signals.analysis.feature_report import (
            FeatureAnalysisEngine,
        )

        engine = FeatureAnalysisEngine(db)
        report = engine.run()
        return TriggerResponse(
            success=True,
            message=(
                f"Analysis completed: {report.snapshot_count} snapshots, "
                f"{report.ticker_count} tickers, "
                f"{report.computation_time_seconds:.0f}s"
            ),
        )
    except Exception as e:
        return TriggerResponse(success=False, message=f"Analysis failed: {e}")


def _report_to_dict(report: AnalysisReport) -> dict:
    """Convert an AnalysisReport to a JSON-serializable dict."""
    return {
        "report_date": report.report_date.isoformat(),
        "snapshot_count": report.snapshot_count,
        "ticker_count": report.ticker_count,
        "date_range_start": report.date_range_start.isoformat(),
        "date_range_end": report.date_range_end.isoformat(),
        "feature_correlations": report.feature_correlations,
        "feature_importance_rf": report.feature_importance_rf,
        "feature_importance_lasso": report.feature_importance_lasso,
        "hypothesis_results": report.hypothesis_results,
        "consensus_features": report.consensus_features,
        "computation_time_seconds": report.computation_time_seconds,
        "computed_at": report.computed_at.isoformat() if report.computed_at else None,
    }
