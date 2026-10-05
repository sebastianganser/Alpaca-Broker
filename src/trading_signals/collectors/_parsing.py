"""Small, dependency-free parsing helpers shared by all collectors.

Replaces the many copy-pasted ``_safe_float`` / ``_safe_int`` /
``_parse_date`` helpers that used to live in the individual collector
modules. All helpers are total: they never raise and return ``None`` for
anything that cannot be interpreted.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

_DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%m/%d/%y", "%d-%b-%Y")


def safe_float(value: Any) -> float | None:
    """Convert ``value`` to float. ``None``/''/NaN/inf/garbage → ``None``.

    Strings may contain thousands separators (``"1,234.5"``) and a leading
    ``$``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("$", "")
        if not value:
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def safe_int(value: Any) -> int | None:
    """Convert ``value`` to int (via float, truncating). Invalid → ``None``."""
    f = safe_float(value)
    if f is None:
        return None
    try:
        return int(f)
    except (OverflowError, ValueError):  # pragma: no cover - inf filtered above
        return None


def parse_date(
    value: Any, formats: tuple[str, ...] = _DEFAULT_DATE_FORMATS
) -> date | None:
    """Parse a date from ``date``/``datetime``/``pd.Timestamp``/str.

    ISO strings with a time part (``2024-05-01T12:00:00Z``) are accepted –
    only the date part is used.
    """
    if value is None:
        return None
    try:
        if value != value:  # NaN / NaT
            return None
    except Exception:  # pragma: no cover - exotic types
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # pandas Timestamp / numpy datetime64 without importing pandas
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            dt = to_pydatetime()
            if dt != dt:  # NaT
                return None
            return dt.date()
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Fast path: ISO date prefix
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
