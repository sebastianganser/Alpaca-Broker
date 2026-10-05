"""Rolling data-retention window (single source of truth).

Decision 2026-10-05 (Sebastian): the system keeps exactly the last
``DATA_RETENTION_QUARTERS`` calendar quarters. Older data is deleted by the
weekly ``data_retention`` job and MUST NOT be (re-)collected by backfills.

Use :func:`data_start_date` everywhere instead of hard-coded start dates.
Never cache the result at import time – the window moves every quarter.
"""

from __future__ import annotations

from datetime import date, timedelta

DATA_RETENTION_QUARTERS = 20  # 20 quarters = 5 years

#: Calendar days of price history needed before the first usable
#: indicator/feature value (SMA200 ≈ 200 sessions ≈ 290 calendar days).
INDICATOR_WARMUP_DAYS = 300


def quarter_cutoff(retention_quarters: int = DATA_RETENTION_QUARTERS, today: date | None = None) -> date:
    """Start of the quarter that lies ``retention_quarters`` quarters back.

    E.g. today=2026-09-22 (Q3 2026), retention=20 → 2021-07-01.
    Data strictly older than this date is deleted.
    """
    today = today or date.today()
    current_q = (today.month - 1) // 3  # 0-based quarter index
    total_q = today.year * 4 + current_q - retention_quarters
    return date(total_q // 4, (total_q % 4) * 3 + 1, 1)


def data_start_date(today: date | None = None) -> date:
    """Earliest date any collector/backfill may store (= retention cutoff)."""
    return quarter_cutoff(DATA_RETENTION_QUARTERS, today)


def ml_start_date(today: date | None = None) -> date:
    """Earliest snapshot date with fully warmed-up indicators (for ML/analysis)."""
    return data_start_date(today) + timedelta(days=INDICATOR_WARMUP_DAYS)
