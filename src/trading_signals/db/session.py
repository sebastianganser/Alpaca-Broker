"""Database engine and session management.

Provides a configured SQLAlchemy engine and session factory.
Use get_session() as a context manager for transactional work.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from trading_signals.config import get_settings

# Module-level engine and session factory (lazy-initialized)
_engine = None
_SessionFactory = None


def get_engine():
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Verify connections before use
            echo=False,  # Set True for SQL debugging
        )
    return _engine


def get_session_factory() -> sessionmaker:
    """Get or create the session factory."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional database session.

    Usage:
        with get_session() as session:
            session.add(my_model)
            # auto-commits on success, auto-rollbacks on exception
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
