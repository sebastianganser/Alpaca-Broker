"""The dashboard table registry (``table_stats`` of /api/v1/dashboard/summary)."""

from datetime import date, datetime
from unittest.mock import MagicMock

import trading_signals.db.models  # noqa: F401  (registers every model)
import trading_signals.db.models.short_interest  # noqa: F401  (not in __init__)
from trading_signals.api.routes import dashboard
from trading_signals.db.base import Base

#: Tables that are deliberately not listed (reference/lookup data).
UNLISTED = {"cusip_map"}


def _names() -> list[str]:
    return [entry[0] for entry in dashboard._TABLE_MODELS]


def test_registry_has_no_duplicates_and_every_name_is_the_table_of_its_model():
    assert len(_names()) == len(set(_names()))
    for name, model, _, _ in dashboard._TABLE_MODELS:
        assert model.__tablename__ == name


def test_new_sec_tables_and_the_forecast_tables_are_listed():
    names = set(_names())
    assert {"sec_filings", "sec_filing_texts", "financials_quarterly"} <= names
    assert {
        "forecasts",
        "forecast_reviews",
        "forecast_criterion_samples",
        "forecast_file_seals",
        "pack_candidates",
    } <= names


def test_every_orm_table_is_listed_or_deliberately_not():
    tables = {t.name for t in Base.metadata.tables.values() if t.schema == "signals"}
    assert tables - set(_names()) == UNLISTED


def test_date_range_columns_exist_on_the_models():
    for name, model, _, date_col in dashboard._TABLE_MODELS:
        if date_col is not None:
            assert hasattr(model, date_col), (name, date_col)


def test_table_stats_report_the_new_tables_with_their_date_range():
    db = MagicMock()
    db.begin_nested.return_value.__enter__.return_value = None
    estimates = {"sec_filings": 1234, "forecasts": 56}
    query = db.query.return_value
    query.first.return_value = (datetime(2026, 7, 30, 20, 30), date(2026, 10, 16))
    query.select_from.return_value.filter.return_value.scalar.return_value = 760
    from unittest.mock import patch

    with patch.object(dashboard, "_estimated_row_counts", return_value=estimates):
        stats = {s.table: s for s in dashboard._compute_table_stats(db)}
    assert stats["sec_filings"].row_count == 1234
    assert stats["forecasts"].row_count == 56
    assert stats["financials_quarterly"].row_count == 0  # table has no rows yet
    # a datetime column is reported as a date (the schema field is a `date`)
    assert stats["sec_filing_texts"].min_date == date(2026, 7, 30)
    assert all(s.row_count != -1 for s in stats.values())
