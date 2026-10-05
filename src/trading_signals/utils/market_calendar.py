"""Shared NYSE trading-calendar helpers.

All collectors / derived jobs that need a "market date" MUST use these
helpers instead of ``date.today()``. The container runs in Europe/Berlin,
so after midnight CET ``date.today()`` is already the *next* calendar day
while the data still belongs to the previous US session.

Uses ``pandas_market_calendars`` (XNYS). Results are cached per process.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

NY_TZ = ZoneInfo("America/New_York")

# Minutes after the official close before a session's daily bar is
# considered final (Alpaca SIP requires end >= now - 15min on free tier).
_CLOSE_BUFFER_MIN = 20


@lru_cache(maxsize=1)
def _calendar():
    import pandas_market_calendars as mcal

    return mcal.get_calendar("XNYS")


@lru_cache(maxsize=64)
def _schedule(start: date, end: date) -> pd.DataFrame:
    return _calendar().schedule(start_date=start, end_date=end)


def trading_days(start: date, end: date) -> list[date]:
    """All NYSE sessions in [start, end] (inclusive)."""
    if end < start:
        return []
    sched = _schedule(start, end)
    return [ts.date() for ts in sched.index]


def is_trading_day(d: date) -> bool:
    """True if ``d`` is an NYSE trading session."""
    return len(_schedule(d, d)) > 0


def previous_trading_day(d: date) -> date:
    """Last NYSE session strictly before ``d``."""
    days = trading_days(d - timedelta(days=14), d - timedelta(days=1))
    if not days:  # pragma: no cover - 14 days without a session never happens
        raise RuntimeError(f"No NYSE session found in the 14 days before {d}")
    return days[-1]


def next_trading_day(d: date) -> date:
    """First NYSE session strictly after ``d``."""
    days = trading_days(d + timedelta(days=1), d + timedelta(days=14))
    if not days:  # pragma: no cover
        raise RuntimeError(f"No NYSE session found in the 14 days after {d}")
    return days[0]


def last_completed_session(now: datetime | None = None) -> date:
    """Most recent NYSE session whose close (+buffer) lies in the past.

    Works regardless of the local timezone of the container. Example:
    a job running Tuesday 04:30 Berlin returns Monday's session; a job
    running Saturday 02:00 Berlin returns Friday's session.
    """
    now_ny = (now or datetime.now(tz=NY_TZ)).astimezone(NY_TZ)
    today_ny = now_ny.date()
    sched = _schedule(today_ny - timedelta(days=14), today_ny)
    for ts, row in reversed(list(sched.iterrows())):
        close_ny = row["market_close"].tz_convert(NY_TZ).to_pydatetime()
        if close_ny + timedelta(minutes=_CLOSE_BUFFER_MIN) <= now_ny:
            return ts.date()
    raise RuntimeError(f"No completed NYSE session found before {now_ny}")  # pragma: no cover
