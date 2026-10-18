"""The ``forecasts.yaml`` template of the overview (``docs/FORECASTS.md``).

The template is the schema ``forecasts/v1`` with placeholders for the two
values the writer has to fill in; :func:`render_example` replaces them and a
unit test feeds the result to the real parser (``derived/forecasts.py``), so
the template cannot drift away from the format.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trading_signals.db.base import APP_TIMEZONE
from trading_signals.derived.forecasts import (
    DRIVER_TAGS,
    HORIZONS,
    SCHEMA_ID,
    next_session_open,
)
from trading_signals.utils.market_calendar import NY_TZ

PLACEHOLDER_DECIDED_AT = "JJJJ-MM-TTTHH:MM:SS+HH:MM"
PLACEHOLDER_VERSION = "SKILL-VERSION+MODELL"
#: The example ticker is no real one: if the template is copied unchanged the
#: rows are rejected by the ingest (no feature snapshot), not evaluated.
EXAMPLE_TICKER = "TICKER"


def forecasts_template(session_date: date) -> list[str]:
    """YAML lines of the template for the pack of ``session_date``."""
    return [
        f"schema: {SCHEMA_ID}",
        f"session: {session_date.isoformat()}",
        f"decided_at: {PLACEHOLDER_DECIDED_AT}   # Zeitpunkt der Entscheidung, "
        "ISO 8601 MIT Zeitzonen-Offset (Pflicht)",
        f"forecaster_version: {PLACEHOLDER_VERSION}   # Skill/Prompt + Modell + "
        "pack_format 2, höchstens 64 Zeichen",
        "forecasts:",
        f"  - ticker: {EXAMPLE_TICKER}",
        f"    horizon_days: 20          # {' | '.join(map(str, HORIZONS[1:]))} "
        "(5 nur beschreibend)",
        "    direction: up             # up | down | neutral",
        "    confidence: 0.60          # P(Richtung stimmt), 0.50 ... 0.95 "
        "(nicht bei neutral)",
        "    conviction: 3             # 1 ... 5, Stärke der Evidenz "
        "(nicht bei neutral)",
        "    expected_pct: 4.0         # optional: erwartete Rohrendite in %, "
        "up > 0, down < 0",
        "    drivers: [revisions, insider]   # optional, höchstens 5 Tags",
        '    reason: "Begründung in einem bis zwei Sätzen"',
        '    risks: "Hauptrisiken"',
        f"  - ticker: {EXAMPLE_TICKER}",
        "    horizon_days: 60",
        "    direction: neutral        # neutral: ohne confidence/conviction/"
        "expected_pct",
        '    reason: "Kein Vorteil in einer der beiden Richtungen"',
    ]


def render_example(
    session_date: date,
    decided_at: str = "2026-01-01T00:00:00+00:00",
    version: str = "test-skill/1+test-model",
    ticker: str = "AAA",
) -> str:
    """The template with placeholders and the example ticker filled in."""
    text = "\n".join(forecasts_template(session_date))
    return (
        text.replace(PLACEHOLDER_DECIDED_AT, decided_at)
        .replace(PLACEHOLDER_VERSION, version)
        .replace(EXAMPLE_TICKER, ticker)
    )


def deadline(session_date: date) -> tuple[datetime, datetime]:
    """Deadline of the pack: the open after the pack day, in New York and Berlin."""
    opening = next_session_open(session_date)
    return opening.astimezone(NY_TZ), opening.astimezone(APP_TIMEZONE)


def deadline_text(session_date: date) -> str:
    """``2026-10-19 09:30 New York (19.10. 15:30 Uhr deutscher Zeit)``."""
    ny, berlin = deadline(session_date)
    return f"{ny:%Y-%m-%d %H:%M} New York ({berlin:%d.%m. %H:%M} Uhr deutscher Zeit)"


#: Minutes between the last seal run (``forecast_seal``, 09:25 New York) and the open.
SEAL_LEAD = timedelta(minutes=5)


def last_seal_text(session_date: date) -> str:
    """``09:25 New York (15:25 Uhr deutscher Zeit)``: the last seal run."""
    ny, berlin = deadline(session_date)
    ny, berlin = ny - SEAL_LEAD, berlin - SEAL_LEAD
    return f"{ny:%H:%M} New York ({berlin:%H:%M} Uhr deutscher Zeit)"


DRIVER_LIST = ", ".join(DRIVER_TAGS)

__all__ = [
    "DRIVER_LIST",
    "EXAMPLE_TICKER",
    "PLACEHOLDER_DECIDED_AT",
    "PLACEHOLDER_VERSION",
    "deadline",
    "deadline_text",
    "forecasts_template",
    "last_seal_text",
    "render_example",
]
