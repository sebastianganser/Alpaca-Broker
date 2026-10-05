"""Tests for the shared collector parsing helpers."""

from datetime import date, datetime

import pandas as pd

from trading_signals.collectors._parsing import parse_date, safe_float, safe_int


class TestSafeFloat:
    def test_numbers_and_strings(self):
        assert safe_float(1) == 1.0
        assert safe_float("1,234.5") == 1234.5
        assert safe_float("$12") == 12.0

    def test_invalid(self):
        assert safe_float(None) is None
        assert safe_float("") is None
        assert safe_float("abc") is None
        assert safe_float(float("nan")) is None
        assert safe_float(float("inf")) is None


class TestSafeInt:
    def test_truncates(self):
        assert safe_int("1,000.9") == 1000

    def test_invalid(self):
        assert safe_int(None) is None
        assert safe_int("x") is None


class TestParseDate:
    def test_types(self):
        assert parse_date(date(2024, 5, 1)) == date(2024, 5, 1)
        assert parse_date(datetime(2024, 5, 1, 13, 0)) == date(2024, 5, 1)
        assert parse_date(pd.Timestamp("2024-05-01")) == date(2024, 5, 1)

    def test_strings(self):
        assert parse_date("2024-05-01T12:00:00Z") == date(2024, 5, 1)
        assert parse_date("05/01/2024") == date(2024, 5, 1)
        assert parse_date("20240501") == date(2024, 5, 1)

    def test_invalid(self):
        assert parse_date(None) is None
        assert parse_date("") is None
        assert parse_date("garbage") is None
        assert parse_date(pd.NaT) is None
