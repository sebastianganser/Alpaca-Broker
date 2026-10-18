"""Formatting helpers of the context pack (pure).

Rules (``docs/CONTEXT_PACK.md``):

* a missing value (NULL) is rendered :data:`NA` - never ``0``; ``0`` means the
  source covers the ticker and nothing happened;
* percentages are written with a unit (``12.3 %``), money in abbreviated
  German units (``4.58 Bio. USD``), decimals with a dot;
* free text from outside sources (headlines, names) is flattened to one line
  and stripped of characters that would break a Markdown table.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

#: Rendering of "the source gives nothing for this ticker".
NA = "keine Abdeckung"
#: Rendering of a value that does not apply (not a coverage gap).
DASH = "–"


def to_float(x: Any) -> float | None:
    """``float(x)`` or None for None / NaN / infinity / non-numbers."""
    if x is None or isinstance(x, str):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def num(x: Any, digits: int = 2, na: str = NA) -> str:
    """Plain number, e.g. ``12.35``."""
    v = to_float(x)
    return na if v is None else f"{v:.{digits}f}"


def integer(x: Any, na: str = NA) -> str:
    v = to_float(x)
    return na if v is None else f"{int(round(v)):d}"


def signed_int(x: Any, na: str = NA) -> str:
    """Signed whole number (``+3``, ``0``, ``-2``; never ``-0``)."""
    v = to_float(x)
    if v is None:
        return na
    n = int(round(v))
    return f"{n:+d}" if n else "0"


def signed(x: Any, digits: int = 2, na: str = NA) -> str:
    v = to_float(x)
    return na if v is None else f"{v:+.{digits}f}"


def percent(x: Any, digits: int = 1, na: str = NA) -> str:
    """A fraction as percent: ``0.123`` -> ``12.3 %``."""
    v = to_float(x)
    return na if v is None else f"{v * 100:.{digits}f} %"


def signed_percent(x: Any, digits: int = 1, na: str = NA) -> str:
    """A fraction as signed percent: ``0.123`` -> ``+12.3 %``."""
    v = to_float(x)
    return na if v is None else f"{v * 100:+.{digits}f} %"


def money(x: Any, unit: str = " USD", na: str = NA) -> str:
    """Abbreviated amount: ``4.58 Bio. USD``, ``312 Mio. USD``."""
    v = to_float(x)
    if v is None:
        return na
    a = abs(v)
    for limit, name in ((1e12, "Bio."), (1e9, "Mrd."), (1e6, "Mio.")):
        if a >= limit:
            return f"{v / limit:.2f} {name}{unit}"
    if a >= 1e3:
        return f"{v / 1e3:.1f} Tsd.{unit}"
    return f"{v:.2f}{unit}"


def shares(x: Any, na: str = NA) -> str:
    """Share count, abbreviated (``12.3 Mio.``)."""
    v = to_float(x)
    if v is None:
        return na
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f} Mrd."
    if a >= 1e6:
        return f"{v / 1e6:.2f} Mio."
    if a >= 1e3:
        return f"{v / 1e3:.1f} Tsd."
    return f"{v:.0f}"


def day(d: date | datetime | None, na: str = NA) -> str:
    """ISO date."""
    if d is None:
        return na
    if isinstance(d, datetime):
        d = d.date()
    return d.isoformat()


def age_days(d: date | datetime | None, session: date) -> int | None:
    """Calendar days between ``d`` and the session (None if ``d`` is None)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return (session - d).days


def age_text(d: date | datetime | None, session: date) -> str:
    """``2026-10-12 (4 T alt)`` or :data:`NA`."""
    if d is None:
        return NA
    a = age_days(d, session)
    return f"{day(d)} ({a} T alt)"


def pct_cell(universe: float | None, sector: float | None = None) -> str:
    """Percentile cell: ``P87 · S62`` (universe / sector), ``–`` if none."""
    parts = []
    if universe is not None:
        parts.append(f"P{universe:.0f}")
    if sector is not None:
        parts.append(f"S{sector:.0f}")
    return " · ".join(parts) if parts else DASH


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE = re.compile(r"\s+")


def clean_text(text: Any, maxlen: int = 120) -> str:
    """One-line, table-safe text, truncated with an ellipsis."""
    if text is None:
        return DASH
    s = unicodedata.normalize("NFKC", str(text))
    s = _CONTROL.sub(" ", s)
    s = s.replace("|", "/").replace("`", "'")
    s = _SPACE.sub(" ", s).strip()
    if not s:
        return DASH
    return s if len(s) <= maxlen else s[: maxlen - 1].rstrip() + "…"


def md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    """Markdown table lines (no blank line before or after)."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return lines


def bullets(items: Iterable[tuple[str, str]]) -> list[str]:
    """``- **label:** value`` lines."""
    return [f"- **{label}:** {value}" for label, value in items]
