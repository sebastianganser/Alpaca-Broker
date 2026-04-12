"""SQLAlchemy declarative base for all ORM models.

All tables live in the 'signals' schema within the 'broker_data' database.
This keeps our data logically separated and allows future schema additions
(e.g. 'trading', 'analysis') without conflicts.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# All tables use the 'signals' schema
SCHEMA_NAME = "signals"

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
