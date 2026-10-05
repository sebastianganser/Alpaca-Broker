"""Dependency injection for API routes.

Provides database sessions, scheduler access and the write-access guard
(API key / CSRF header) to route handlers via FastAPI's Depends() system.
"""

import hmac
from collections.abc import Generator
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
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


# ── Write-access guard (auth contract) ──────────────────────────────────

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_api_key() -> str:
    """Configured API key (``settings.API_KEY``); empty string if unset."""
    try:
        from trading_signals.config import get_settings

        return get_settings().API_KEY or ""
    except Exception:  # settings unavailable → behave as "no key configured"
        return ""


def require_write_access(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_requested_with: str | None = Header(default=None, alias="X-Requested-With"),
    api_key: str = Depends(get_api_key),
) -> None:
    """Guard for mutating requests (POST/PUT/PATCH/DELETE).

    * ``API_KEY`` configured → header ``X-API-Key`` must match (constant-time
      comparison), otherwise **401**.
    * ``API_KEY`` empty → header ``X-Requested-With`` must be present (any
      value), otherwise **403**. A custom header forces a CORS preflight, so
      a malicious cross-site form/fetch cannot trigger destructive actions.

    Safe methods (GET/HEAD/OPTIONS) are always allowed.
    """
    if request.method.upper() in SAFE_METHODS:
        return
    if api_key:
        if not x_api_key or not hmac.compare_digest(
            x_api_key.encode("utf-8"), api_key.encode("utf-8")
        ):
            raise HTTPException(
                status_code=401, detail="Ungültiger oder fehlender API-Key"
            )
        return
    if not x_requested_with:
        raise HTTPException(
            status_code=403, detail="Header 'X-Requested-With' erforderlich"
        )
