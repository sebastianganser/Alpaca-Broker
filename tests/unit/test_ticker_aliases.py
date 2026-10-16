"""Tests for the known-rename list and its helpers."""

from datetime import date

import pytest

from trading_signals.universe.ticker_aliases import (
    DEFAULT_ALIASES,
    KNOWN_RENAMES,
    SYMBOL_REUSE,
    AliasMap,
    TickerAlias,
    aliases_of,
    current_symbol,
    symbol_history,
)

#: The 21 ``rename`` rows of docs/reports/2026-10-16_sp500_removals.csv.
CSV_RENAME_ROWS = [
    ("LB", "BBWI", date(2021, 8, 3)),
    ("COG", "CTRA", date(2021, 10, 4)),
    ("WLTW", "WTW", date(2022, 1, 10)),
    ("VIAC", "PARA", date(2022, 2, 17)),
    ("BLL", "BALL", date(2022, 5, 10)),
    ("FB", "META", date(2022, 6, 9)),
    ("ANTM", "ELV", date(2022, 6, 28)),
    ("NLOK", "GEN", date(2022, 11, 8)),
    ("PKI", "RVTY", date(2023, 5, 16)),
    ("FISV", "FI", date(2023, 6, 7)),
    ("RE", "EG", date(2023, 7, 10)),
    ("ABC", "COR", date(2023, 8, 30)),
    ("CDAY", "DAY", date(2024, 2, 1)),
    ("PEAK", "DOC", date(2024, 3, 4)),
    ("FLT", "CPAY", date(2024, 3, 25)),
    ("WRK", "SW", date(2024, 7, 8)),
    ("FI", "FISV", date(2025, 11, 11)),
    ("MMC", "MRSH", date(2026, 1, 14)),
    ("BK", "BNY", date(2026, 5, 21)),
    ("SATS", "ECHO", date(2026, 6, 24)),
    ("EQR", "VMRK", date(2026, 8, 18)),
]


class TestKnownRenames:
    def test_list_is_a_valid_chain_set(self):
        AliasMap(KNOWN_RENAMES)  # raises ValueError if a company's renames branch

    def test_contains_every_inventory_rename_row(self):
        have = {(a.old, a.new, a.effective_date) for a in KNOWN_RENAMES}
        for row in CSV_RENAME_ROWS:
            assert row in have, row

    def test_contains_the_documented_extras(self):
        have = {(a.old, a.new) for a in KNOWN_RENAMES}
        assert {("FBHS", "FBIN"), ("PSTG", "P"), ("PSKY", "SKYD")} <= have

    def test_entries_are_documented(self):
        for a in KNOWN_RENAMES:
            assert a.note.strip(), a
            assert a.old != a.new and a.old == a.old.upper() and a.new == a.new.upper()

    def test_mergers_and_collisions_are_not_aliases(self):
        pairs = {(a.old, a.new) for a in KNOWN_RENAMES}
        assert ("PARA", "PSKY") not in pairs  # merger, real exit
        assert ("DISCA", "WBD") not in pairs and ("DISCK", "WBD") not in pairs
        assert ("BNZI", "PARA") not in pairs  # Banzai re-used the symbol
        assert ("GPS", "GAP") not in pairs  # renamed after the exit
        assert set(SYMBOL_REUSE) >= {"FB", "LB", "PARA", "INFO"}


class TestCurrentSymbol:
    @pytest.mark.parametrize(
        "old,new",
        [
            ("FB", "META"),
            ("BK", "BNY"),
            ("SATS", "ECHO"),
            ("EQR", "VMRK"),
            ("CDAY", "DAY"),
            ("FBHS", "FBIN"),
            ("WRK", "SW"),
            ("PSTG", "P"),
            ("PSKY", "SKYD"),
            ("VIAC", "PARA"),
        ],
    )
    def test_old_symbol_maps_to_current(self, old, new):
        assert current_symbol(old) == new

    def test_current_and_unknown_symbols_are_unchanged(self):
        assert current_symbol("META") == "META"
        assert current_symbol("AAPL") == "AAPL"
        assert current_symbol("PARA") == "PARA"  # never mapped to PSKY (merger)

    def test_case_and_whitespace_insensitive(self):
        assert current_symbol(" fb ") == "META"

    def test_fiserv_round_trip_ends_on_fisv(self):
        assert current_symbol("FISV") == "FISV"
        assert current_symbol("FI") == "FISV"

    def test_as_of_works_in_both_directions(self):
        assert current_symbol("DAY", date(2022, 1, 1)) == "CDAY"
        assert current_symbol("CDAY", date(2022, 1, 1)) == "CDAY"
        assert current_symbol("CDAY", date(2025, 1, 1)) == "DAY"
        assert current_symbol("META", date(2021, 1, 1)) == "FB"

    def test_as_of_boundary_is_the_effective_date(self):
        assert current_symbol("FB", date(2022, 6, 8)) == "FB"
        assert current_symbol("FB", date(2022, 6, 9)) == "META"

    def test_fiserv_changes_back_and_forth(self):
        assert current_symbol("FISV", date(2022, 1, 1)) == "FISV"
        assert current_symbol("FISV", date(2024, 1, 1)) == "FI"
        assert current_symbol("FI", date(2024, 1, 1)) == "FI"
        assert current_symbol("FI", date(2026, 1, 1)) == "FISV"

    def test_unknown_ticker_with_date_is_unchanged(self):
        assert current_symbol("AAPL", date(2020, 1, 1)) == "AAPL"


class TestAliasesOf:
    def test_single_rename(self):
        assert aliases_of("DAY") == ["CDAY"]
        assert aliases_of("CDAY") == ["DAY"]
        assert aliases_of("META") == ["FB"]
        assert aliases_of("fb") == ["META"]

    def test_round_trip_lists_the_other_symbol_once(self):
        assert aliases_of("FISV") == ["FI"]
        assert aliases_of("FI") == ["FISV"]

    def test_viacom_paramount_pair_only(self):
        assert aliases_of("PARA") == ["VIAC"]

    def test_unknown_has_no_aliases(self):
        assert aliases_of("AAPL") == []


class TestSymbolHistory:
    def test_fiserv_periods(self):
        assert symbol_history("FI") == [
            ("FISV", None, date(2023, 6, 7)),
            ("FI", date(2023, 6, 7), date(2025, 11, 11)),
            ("FISV", date(2025, 11, 11), None),
        ]

    def test_single_rename_periods(self):
        assert symbol_history("DAY") == [
            ("CDAY", None, date(2024, 2, 1)),
            ("DAY", date(2024, 2, 1), None),
        ]

    def test_unknown_is_one_open_period(self):
        assert symbol_history("aapl") == [("AAPL", None, None)]


class TestAliasMap:
    def test_non_chain_is_rejected(self):
        with pytest.raises(ValueError, match="chain"):
            AliasMap([
                TickerAlias("A", "B", date(2020, 1, 1)),
                TickerAlias("A", "C", date(2021, 1, 1)),
            ])

    def test_self_alias_is_rejected(self):
        with pytest.raises(ValueError):
            AliasMap([TickerAlias("A", "A", date(2020, 1, 1))])

    def test_longer_chain(self):
        m = AliasMap([
            TickerAlias("B", "C", date(2021, 1, 1)),
            TickerAlias("A", "B", date(2020, 1, 1)),
        ])
        assert m.current_symbol("A") == "C"
        assert m.aliases_of("B") == ["A", "C"]

    def test_with_extra_adds_and_ignores_known_pairs(self):
        extra = [
            TickerAlias("CDAY", "DAY", date(2024, 2, 2), "duplicate"),  # known pair
            TickerAlias("OLDX", "NEWX", date(2026, 1, 1), "new"),
        ]
        m = DEFAULT_ALIASES.with_extra(extra)
        assert m.current_symbol("OLDX") == "NEWX"
        # the curated date wins over the duplicate
        cday = next(a for a in m.aliases if a.old == "CDAY")
        assert cday.effective_date == date(2024, 2, 1)
        assert len(m.aliases) == len(KNOWN_RENAMES) + 1
        assert DEFAULT_ALIASES.current_symbol("OLDX") == "OLDX"  # original untouched

    def test_with_extra_skips_conflicting_rename(self, caplog):
        conflict = TickerAlias("FB", "XYZ", date(2023, 1, 1), "branches FB")
        with caplog.at_level("WARNING"):
            m = DEFAULT_ALIASES.with_extra([conflict])
        assert m.current_symbol("FB") == "META"
        assert "ignoring rename FB->XYZ" in caplog.text


class TestCanonicalSymbol:
    """Event keys: re-used symbols must not be re-keyed."""

    m = DEFAULT_ALIASES.with_extra([
        TickerAlias("Q", "IQV", date(2017, 11, 15), "QuintilesIMS became IQVIA"),
    ])

    def test_old_symbol_before_and_on_the_rename(self):
        assert self.m.canonical_symbol("FB", date(2022, 1, 1)) == "META"
        assert self.m.canonical_symbol("CDAY", date(2024, 2, 1)) == "DAY"

    def test_tolerance_covers_snapshot_dates_a_few_days_late(self):
        assert self.m.canonical_symbol("FB", date(2022, 6, 12)) == "META"
        assert self.m.canonical_symbol("FB", date(2022, 6, 30)) == "FB"

    def test_reused_symbol_stays_untouched(self):
        assert self.m.canonical_symbol("Q", date(2017, 11, 10)) == "IQV"
        # Qnity was added to the S&P 500 in 2025 under the old ticker Q
        assert self.m.canonical_symbol("Q", date(2025, 11, 3)) == "Q"
        assert self.m.canonical_symbol("FI", date(2021, 6, 1)) == "FI"  # Frank's

    def test_unknown_and_current_symbols(self):
        assert self.m.canonical_symbol("AAPL", date(2025, 1, 1)) == "AAPL"
        assert self.m.canonical_symbol("META", date(2025, 1, 1)) == "META"
