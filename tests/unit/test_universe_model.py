"""Tests for the Universe ORM model."""

from datetime import date


class TestUniverseModel:
    """Test the Universe SQLAlchemy model."""

    def test_universe_instantiation(self):
        """Universe model should instantiate with required fields."""
        from trading_signals.db.models.universe import Universe

        ticker = Universe(
            ticker="AAPL",
            company_name="Apple Inc.",
            added_date=date(2026, 4, 12),
            added_by="manual",
            is_active=True,
        )

        assert ticker.ticker == "AAPL"
        assert ticker.company_name == "Apple Inc."
        assert ticker.added_date == date(2026, 4, 12)
        assert ticker.is_active is True

    def test_universe_optional_fields(self):
        """Universe model should allow optional fields to be None."""
        from trading_signals.db.models.universe import Universe

        ticker = Universe(
            ticker="TEST",
            added_date=date.today(),
        )

        assert ticker.cusip is None
        assert ticker.isin is None
        assert ticker.exchange is None
        assert ticker.sector is None
        assert ticker.industry is None
        assert ticker.metadata_json is None

    def test_universe_repr(self):
        """Universe __repr__ should be readable."""
        from trading_signals.db.models.universe import Universe

        ticker = Universe(
            ticker="MSFT",
            company_name="Microsoft Corp.",
            added_date=date.today(),
            is_active=True,
        )

        repr_str = repr(ticker)
        assert "MSFT" in repr_str
        assert "Microsoft" in repr_str

    def test_universe_table_name(self):
        """Universe model should use 'universe' as table name."""
        from trading_signals.db.models.universe import Universe

        assert Universe.__tablename__ == "universe"

    def test_universe_schema(self):
        """Universe model should be in the 'signals' schema."""
        from trading_signals.db.models.universe import Universe

        assert Universe.__table__.schema == "signals"
