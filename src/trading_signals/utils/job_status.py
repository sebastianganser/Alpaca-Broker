"""Canonical job / collector run status values.

Every writer of ``collection_log.status`` and every reader (dashboard,
ticker health, alerting) MUST use these constants.
"""

from typing import Final

RUNNING: Final = "running"
SUCCESS: Final = "success"
PARTIAL: Final = "partial"  # finished, but a relevant share of items failed
FAILED: Final = "failed"
SKIPPED: Final = "skipped"  # intentionally not executed (e.g. non-trading day)

ALL_STATUSES: Final = (RUNNING, SUCCESS, PARTIAL, FAILED, SKIPPED)
#: Statuses that should trigger an alert.
ALERT_STATUSES: Final = (PARTIAL, FAILED)

#: Legacy values found in old collection_log rows, mapped to canonical ones.
LEGACY_MAP: Final = {"error": FAILED, "complete": SUCCESS, "completed": SUCCESS}


def normalize(status: str | None) -> str | None:
    """Map legacy status strings to canonical values."""
    if status is None:
        return None
    return LEGACY_MAP.get(status, status)
