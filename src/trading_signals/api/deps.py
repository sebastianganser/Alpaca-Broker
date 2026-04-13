"""Dependency injection for API routes.

Provides database sessions and scheduler access to route handlers
via FastAPI's Depends() system.
"""

from collections.abc import Generator
from typing import Any

from sqlalchemy.orm import Session

from trading_signals.db.session import get_session_factory


def get_db() -> Generator[Session, None, None]:
    """Provide a database session for API requests.

    Yields a session that auto-commits on success and
    auto-rollbacks on exception. Used with FastAPI's Depends().
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Scheduler reference – set at startup, accessed via dependency
_scheduler_instance: Any = None


def set_scheduler(scheduler: Any) -> None:
    """Store the scheduler reference for API access."""
    global _scheduler_instance
    _scheduler_instance = scheduler


def get_scheduler() -> Any:
    """Provide the APScheduler instance to route handlers."""
    return _scheduler_instance
