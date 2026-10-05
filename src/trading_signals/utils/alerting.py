"""Push alerts for failed / partial job runs.

Configured via ``ALERT_WEBHOOK_URL`` (e.g. an ntfy topic URL such as
``https://ntfy.sh/my-alpaca-alerts`` or a self-hosted ntfy/Gotify/Discord-
compatible endpoint that accepts a plain-text POST body). If unset, alerts
are only logged. Alerting must never raise into the caller.
"""

from __future__ import annotations

import re

import requests

from trading_signals.utils import job_status
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

_SECRET_RE = re.compile(r"(api_?key=|apiKey=|token=)[^&\s]+", re.IGNORECASE)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", text)


def notify_run_status(job_name: str, status: str | None, details: str | None = None) -> None:
    """Send an alert if ``status`` is an alert status. Never raises."""
    status = job_status.normalize(status)
    if status not in job_status.ALERT_STATUSES:
        return
    message = _redact(f"[Alpaca-Broker] {job_name}: {status.upper()}" + (f"\n{details[:500]}" if details else ""))
    logger.warning(f"ALERT: {message}")
    try:
        from trading_signals.config import get_settings

        url = get_settings().ALERT_WEBHOOK_URL
    except Exception:  # settings unavailable (e.g. tests without .env)
        return
    if not url:
        return
    try:
        requests.post(
            url,
            data=message.encode("utf-8"),
            headers={"Title": f"Alpaca-Broker: {job_name} {status}", "Priority": "high" if status == job_status.FAILED else "default"},
            timeout=10,
        )
    except Exception as e:  # pragma: no cover - network failures must not propagate
        logger.warning(f"Alert delivery failed: {type(e).__name__}")
