"""Logs API routes.

Provides access to collection_log entries for monitoring
job executions, errors, and data quality issues.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import CollectionLogItem, LogsResponse
from trading_signals.db.models.collection_log import CollectionLog

router = APIRouter(prefix="/logs")


@router.get("", response_model=LogsResponse)
def get_logs(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    collector: str | None = Query(None, description="Filter by collector name"),
    status: str | None = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    """Get paginated collection logs, newest first.

    Supports filtering by collector_name and status.
    """
    query = db.query(CollectionLog)

    if collector:
        query = query.filter(CollectionLog.collector_name == collector)
    if status:
        query = query.filter(CollectionLog.status == status)

    total = query.count()
    logs = (
        query
        .order_by(CollectionLog.started_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    items = []
    for log in logs:
        duration = None
        if log.started_at and log.finished_at:
            duration = (log.finished_at - log.started_at).total_seconds()

        items.append(
            CollectionLogItem(
                id=log.id,
                collector_name=log.collector_name,
                started_at=log.started_at,
                finished_at=log.finished_at,
                status=log.status,
                records_fetched=log.records_fetched,
                records_written=log.records_written,
                gaps_detected=log.gaps_detected,
                gaps_repaired=log.gaps_repaired,
                gaps_extrapolated=log.gaps_extrapolated,
                errors=log.errors,
                notes=log.notes,
                log_lines=log.log_lines,
                duration_seconds=duration,
            )
        )

    return LogsResponse(
        logs=items,
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/collectors", response_model=list[str])
def get_collector_names(db: Session = Depends(get_db)):
    """Get list of unique collector names for filtering."""
    result = (
        db.query(CollectionLog.collector_name)
        .distinct()
        .order_by(CollectionLog.collector_name)
        .all()
    )
    return [row[0] for row in result if row[0] is not None]
