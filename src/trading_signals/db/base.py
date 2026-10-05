"""SQLAlchemy declarative base for all ORM models.

All tables live in the 'signals' schema within the 'broker_data' database.
This keeps our data logically separated and allows future schema additions
(e.g. 'trading', 'analysis') without conflicts.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# All tables use the 'signals' schema
SCHEMA_NAME = "signals"

#: Wall-clock timezone of the application (container runs with TZ=Europe/Berlin).
APP_TIMEZONE = ZoneInfo("Europe/Berlin")

# Convention for constraint naming – makes Alembic auto-migrations cleaner
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all ORM models in the signals schema."""

    metadata = MetaData(schema=SCHEMA_NAME, naming_convention=convention)


def now_local() -> datetime:
    """Timezone-aware 'now' in the application timezone (Europe/Berlin)."""
    return datetime.now(APP_TIMEZONE)


class TZDateTime(TypeDecorator):
    """``TIMESTAMP WITH TIME ZONE`` column with a backwards-compatible Python API.

    * Storage: ``timestamptz`` – an absolute point in time (migration 031).
    * Writing: aware datetimes are stored as-is. *Naive* datetimes (legacy
      writers using ``datetime.now()``) are interpreted as Europe/Berlin wall
      clock, which is what the container produced historically. Plain
      ``date`` values (e.g. retention cutoffs) mean Berlin midnight.
    * Reading: values are returned as **naive Europe/Berlin wall clock** so
      that existing code doing ``datetime.now() - log.started_at`` keeps
      working. Callers that need aware values can use
      ``value.replace(tzinfo=APP_TIMEZONE)``.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: D401 - SQLAlchemy hook
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=APP_TIMEZONE)
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=APP_TIMEZONE)
        return value

    def process_result_value(self, value, dialect):  # noqa: D401 - SQLAlchemy hook
        if value is None or not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            return value
        return value.astimezone(APP_TIMEZONE).replace(tzinfo=None)
