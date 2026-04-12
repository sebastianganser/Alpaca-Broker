"""Alembic environment configuration.

Connects Alembic to our SQLAlchemy models and database configuration.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from trading_signals.config import get_settings
from trading_signals.db.base import Base

# Import all models so Alembic can see them for autogeneration
from trading_signals.db.models import Universe  # noqa: F401

# Alembic Config object
config = context.config

# Set up loggers from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Load settings (DB URL comes from .env, not alembic.ini)
settings = get_settings()

# Target metadata for autogenerate support
target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Only include objects in the 'signals' schema."""
    if type_ == "table":
        return object.schema == "signals"
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without DB connection)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        version_table_schema="signals",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with DB connection).

    Creates the engine directly from Settings.database_url to avoid
    configparser's %-interpolation issues with URL-encoded passwords.
    """
    connectable = create_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure the signals schema exists before running migrations
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS signals"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            version_table_schema="signals",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

