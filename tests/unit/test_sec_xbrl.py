"""Tests for collectors/sec_xbrl.py - companyfacts -> quarterly financials (no I/O)."""

import json
from datetime import date
from pathlib import Path

from trading_signals.collectors import sec_xbrl as xbrl

FIXTURES = Path(__file__).parent.parent / "fixtures"
WINDOW = date(2021, 7, 1)

# Calendar fiscal year 2025: (start, end, fp, form, filed, accn)
Q1 = ("2025-01-01", "2025-03-31", "Q1", "10-Q", "2025-05-02", "0000001-25-000001")
Q2 = ("2025-04-01", "2025-06-30", "Q2", "10-Q", "2025-08-01", "0000001-25-000002")
Q3 = ("2025-07-01", "2025-09-30", "Q3", "10-Q", "2025-10-31", "0000001-25-000003")
FY = ("2025-01-01", "2025-12-31", "FY", "10-K", "2026-02-14", "0000001-26-000004")
H1 = ("2025-01-01", "2025-06-30", "Q2", "10-Q", "2025-08-01", "0000001-25-000002")
M9 = ("2025-01-01", "2025-09-30", "Q3", "10-Q", "2025-10-31", "0000001-25-000003")
Q1_YTD = Q1


def fact(period, val, fy=2025):
    start, end, fp, form, filed, accn = period
    out = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if start:
        out["start"] = start
    return out


def instant(end, val, fp, form, filed, accn, fy=2025):
    return {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }


def company(gaap=None, dei=None):
    return {
        "cik": 1,
        "entityName": "Test Corp",
        "facts": {
            "us-gaap": {tag: {"units": units} for tag, units in (gaap or {}).items()},
            "dei": {tag: {"units": units} for tag, units in (dei or {}).items()},
        },
    }


def usd(*facts):
    return {"USD": list(facts)}


def rows_by_end(doc, window=WINDOW):
    return {r["period_end"]: r for r in xbrl.build_quarterly_rows(doc, window)}


class TestRealCompanyFacts:
    """Trimmed real Apple companyfacts (fiscal year ends in September)."""

    def _rows(self):
        doc = json.loads((FIXTURES / "companyfacts_aapl_trimmed.json").read_text())
        return rows_by_end(doc, date(2024, 7, 1))

    def test_published_numbers_of_the_third_fiscal_quarter_2026(self):
        r = self._rows()[date(2026, 6, 27)]
        assert (r["fiscal_year"], r["fiscal_period"], r["form"]) == (2026, "Q3", "10-Q")
        assert r["filed"] == date(2026, 7, 31)
        assert r["accession_number"] == "0000320193-26-000020"
        assert r["revenue"] == 109_417_000_000
        assert r["gross_profit"] == 54_770_000_000
        assert r["operating_income"] == 35_695_000_000
        assert r["net_income"] == 29_789_000_000
        assert r["eps_diluted"] == 2.02
        # cash flow is year-to-date in the 10-Q: Q3 = 9M - 6M
        assert r["operating_cash_flow"] == 34_369_000_000
        assert r["capex"] == 2_455_000_000
        assert r["free_cash_flow"] == 31_914_000_000
        assert r["cash_and_equivalents"] == 39_544_000_000
        assert r["shares_outstanding"] == 14_608_963_000
        # long-term debt + current portion + commercial paper
        assert r["total_debt"] == 84_344_000_000
        assert r["raw_tags"]["derived"] == [
            "capex",
            "free_cash_flow",
            "operating_cash_flow",
        ]
        assert r["raw_tags"]["tags"]["revenue"] == (
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        )

    def test_fourth_quarter_is_the_fiscal_year_minus_nine_months(self):
        r = self._rows()[date(2025, 9, 27)]
        assert (r["fiscal_year"], r["fiscal_period"], r["form"]) == (2025, "Q4", "10-K")
        assert r["filed"] == date(2025, 10, 31)
        assert r["revenue"] == 102_466_000_000  # FY 416,161 - 9M 313,695
        assert r["net_income"] == 27_466_000_000
        assert r["eps_diluted"] == 1.84
        assert {"revenue", "net_income", "eps_diluted"} <= set(r["raw_tags"]["derived"])

    def test_one_row_per_quarter_oldest_first(self):
        rows = xbrl.build_quarterly_rows(
            json.loads((FIXTURES / "companyfacts_aapl_trimmed.json").read_text()),
            date(2024, 7, 1),
        )
        ends = [r["period_end"] for r in rows]
        assert ends == sorted(set(ends))
        assert ends[0] == date(2024, 9, 28) and ends[-1] == date(2026, 6, 27)
        assert all(r["filed"] <= date(2026, 7, 31) for r in rows)


class TestQuarters:
    def test_income_statement_quarters_are_used_directly_and_q4_is_fy_minus_9m(self):
        doc = company(
            {
                "Revenues": usd(
                    fact(Q1, 100),
                    fact(Q2, 110),
                    fact(H1, 210),
                    fact(Q3, 120),
                    fact(M9, 330),
                    fact(FY, 480),
                ),
            }
        )
        rows = rows_by_end(doc)
        assert [r["revenue"] for r in rows.values()] == [100, 110, 120, 150]
        q4 = rows[date(2025, 12, 31)]
        assert (q4["fiscal_period"], q4["form"], q4["filed"]) == (
            "Q4",
            "10-K",
            date(2026, 2, 14),
        )
        assert q4["raw_tags"]["derived"] == ["revenue"]
        assert rows[date(2025, 6, 30)]["raw_tags"]["derived"] == []
        assert [r["fiscal_period"] for r in rows.values()] == ["Q1", "Q2", "Q3", "Q4"]

    def test_cash_flow_year_to_date_values_become_quarters(self):
        doc = company(
            {
                "NetCashProvidedByUsedInOperatingActivities": usd(
                    fact(Q1, 50),
                    fact(H1, 130),
                    fact(M9, 190),
                    fact(FY, 300),
                ),
                "Revenues": usd(fact(Q1, 1), fact(Q2, 1), fact(Q3, 1), fact(FY, 4)),
            }
        )
        rows = rows_by_end(doc)
        assert [r["operating_cash_flow"] for r in rows.values()] == [50, 80, 60, 110]
        assert all(
            "operating_cash_flow" in r["raw_tags"]["derived"]
            for k, r in rows.items()
            if k != date(2025, 3, 31)
        )
        assert rows[date(2025, 3, 31)]["raw_tags"]["derived"] == []

    def test_first_reported_value_wins_over_a_restatement(self):
        later = (
            "2025-01-01",
            "2025-03-31",
            "Q1",
            "10-Q",
            "2026-05-01",
            "0000009-26-000001",
        )
        doc = company({"Revenues": usd(fact(later, 999, fy=2026), fact(Q1, 100))})
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert row["revenue"] == 100
        assert row["filed"] == date(2025, 5, 2)  # as first reported, public since then

    def test_a_difference_must_span_one_quarter(self):
        # no 9-month fact: FY - 6M would be two quarters -> Q3 and Q4 stay empty
        doc = company(
            {
                "NetCashProvidedByUsedInOperatingActivities": usd(
                    fact(Q1, 50), fact(H1, 130), fact(FY, 300)
                )
            }
        )
        rows = rows_by_end(doc)
        assert list(rows) == [date(2025, 3, 31), date(2025, 6, 30)]

    def test_a_53_week_fourth_quarter_is_accepted(self):
        fy = (
            "2024-12-29",
            "2026-01-03",
            "FY",
            "10-K",
            "2026-02-20",
            "0000002-26-000001",
        )
        nine = (
            "2024-12-29",
            "2025-09-27",
            "Q3",
            "10-Q",
            "2025-10-30",
            "0000002-25-000003",
        )
        doc = company({"Revenues": usd(fact(nine, 300), fact(fy, 410))})
        rows = rows_by_end(doc)
        assert rows[date(2026, 1, 3)]["revenue"] == 110  # 98-day quarter

    def test_non_additive_series_are_never_derived(self):
        doc = company({"Revenues": usd(fact(Q1, 1), fact(FY, 4))})
        quarters = xbrl.flow_quarters(
            xbrl.load_facts({"units": usd(fact(Q1, 1), fact(H1, 3))}, "USD"),
            additive=False,
        )
        assert list(quarters) == [date(2025, 3, 31)]
        assert len(rows_by_end(doc)) == 1  # FY alone does not make a quarter

    def test_window_start_drops_older_quarters(self):
        doc = company({"Revenues": usd(fact(Q1, 100), fact(Q2, 110), fact(Q3, 120))})
        rows = rows_by_end(doc, window=date(2025, 7, 1))
        assert list(rows) == [date(2025, 9, 30)]

    def test_other_forms_units_and_garbage_are_ignored(self):
        bad = [
            {**fact(Q1, 100), "form": "8-K"},
            {**fact(Q2, float("nan"))},
            {**fact(Q3, 10**30)},
            {"start": "2025-01-01", "val": 5, "form": "10-Q", "filed": "2025-05-02"},
        ]
        doc = company({"Revenues": {"EUR": [fact(Q1, 100)], "USD": bad}})
        assert xbrl.build_quarterly_rows(doc, WINDOW) == []

    def test_empty_or_malformed_input_returns_nothing(self):
        assert xbrl.build_quarterly_rows({}, WINDOW) == []
        assert xbrl.build_quarterly_rows({"facts": None}, WINDOW) == []
        assert xbrl.build_quarterly_rows(None, WINDOW) == []
        assert xbrl.build_quarterly_rows(company({"Revenues": usd()}), WINDOW) == []


class TestFiguresAndPointInTime:
    def _income(self):
        return {
            "Revenues": usd(fact(Q1, 100)),
            "NetIncomeLoss": usd(fact(Q1, 10)),
        }

    def test_gross_profit_falls_back_to_revenue_minus_cost(self):
        doc = company({**self._income(), "CostOfRevenue": usd(fact(Q1, 60))})
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert row["gross_profit"] == 40
        assert "gross_profit" in row["raw_tags"]["derived"]
        assert row["raw_tags"]["tags"]["gross_profit"] == "Revenues-CostOfRevenue"

    def test_reported_gross_profit_wins_over_the_fallback(self):
        doc = company(
            {
                **self._income(),
                "CostOfRevenue": usd(fact(Q1, 60)),
                "GrossProfit": usd(fact(Q1, 38)),
            }
        )
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert row["gross_profit"] == 38 and row["raw_tags"]["derived"] == []

    def test_free_cash_flow_subtracts_the_absolute_capex(self):
        doc = company(
            {
                **self._income(),
                "NetCashProvidedByUsedInOperatingActivities": usd(fact(Q1, 50)),
                "PaymentsToAcquirePropertyPlantAndEquipment": usd(fact(Q1, 12)),
            }
        )
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert (row["capex"], row["free_cash_flow"]) == (12, 38)
        # a capex tag with a negative sign is treated the same way
        doc = company(
            {
                **self._income(),
                "NetCashProvidedByUsedInOperatingActivities": usd(fact(Q1, 50)),
                "PaymentsToAcquirePropertyPlantAndEquipment": usd(fact(Q1, -12)),
            }
        )
        assert rows_by_end(doc)[date(2025, 3, 31)]["free_cash_flow"] == 38

    def test_debt_is_the_sum_of_its_components(self):
        def bs(val):
            return usd(
                instant(
                    "2025-03-31", val, "Q1", "10-Q", "2025-05-02", "0000001-25-000001"
                )
            )

        doc = company(
            {
                **self._income(),
                "LongTermDebtNoncurrent": bs(100),
                "LongTermDebtCurrent": bs(10),
                "CommercialPaper": bs(5),
            }
        )
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert row["total_debt"] == 115
        assert row["raw_tags"]["tags"]["total_debt"] == (
            "LongTermDebtNoncurrent+LongTermDebtCurrent+CommercialPaper"
        )
        # DebtCurrent already contains the short-term borrowings
        doc = company(
            {
                **self._income(),
                "LongTermDebtNoncurrent": bs(100),
                "DebtCurrent": bs(20),
                "ShortTermBorrowings": bs(5),
            }
        )
        assert rows_by_end(doc)[date(2025, 3, 31)]["total_debt"] == 120
        # no components: the combined tag, then LongTermDebt
        doc = company({**self._income(), "LongTermDebt": bs(77)})
        assert rows_by_end(doc)[date(2025, 3, 31)]["total_debt"] == 77

    def test_balance_sheet_figures_first_reported_later_are_left_empty(self):
        late = instant(
            "2025-03-31", 100, "Q2", "10-Q", "2025-08-01", "0000001-25-000002"
        )
        doc = company(
            {
                **self._income(),
                "LongTermDebtNoncurrent": usd(late),
                "CashAndCashEquivalentsAtCarryingValue": usd(
                    instant(
                        "2025-03-31", 7, "Q1", "10-Q", "2025-05-02", "0000001-25-000001"
                    )
                ),
            }
        )
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert row["total_debt"] is None  # only public with the Q2 10-Q
        assert row["cash_and_equivalents"] == 7
        assert row["filed"] == date(2025, 5, 2)  # the row is not delayed

    def test_shares_fall_back_to_the_cover_page_of_the_same_filing(self):
        doc = company(
            self._income(),
            dei={
                "EntityCommonStockSharesOutstanding": {
                    "shares": [
                        {
                            "end": "2025-04-18",
                            "val": 5_000_000,
                            "accn": "0000001-25-000001",
                            "fy": 2025,
                            "fp": "Q1",
                            "form": "10-Q",
                            "filed": "2025-05-02",
                        },
                        {
                            "end": "2025-07-18",
                            "val": 6_000_000,
                            "accn": "0000001-25-000002",
                            "fy": 2025,
                            "fp": "Q2",
                            "form": "10-Q",
                            "filed": "2025-08-01",
                        },
                    ]
                }
            },
        )
        row = rows_by_end(doc)[date(2025, 3, 31)]
        assert row["shares_outstanding"] == 5_000_000
        assert row["raw_tags"]["tags"]["shares_outstanding"].startswith("dei:")

    def test_the_balance_sheet_share_count_wins_over_the_cover_page(self):
        doc = company(
            {
                **self._income(),
                "CommonStockSharesOutstanding": {
                    "shares": [
                        instant(
                            "2025-03-31",
                            4_900_000,
                            "Q1",
                            "10-Q",
                            "2025-05-02",
                            "0000001-25-000001",
                        )
                    ]
                },
            },
            dei={
                "EntityCommonStockSharesOutstanding": {
                    "shares": [
                        {
                            "end": "2025-04-18",
                            "val": 5_000_000,
                            "accn": "0000001-25-000001",
                            "fy": 2025,
                            "fp": "Q1",
                            "form": "10-Q",
                            "filed": "2025-05-02",
                        }
                    ]
                }
            },
        )
        assert rows_by_end(doc)[date(2025, 3, 31)]["shares_outstanding"] == 4_900_000

    def test_row_filed_is_the_latest_income_statement_or_cash_flow_figure(self):
        doc = company(
            {
                "Revenues": usd(
                    fact(Q1, 1), fact(Q2, 2), fact(Q3, 3), fact(M9, 6), fact(FY, 10)
                ),
            }
        )
        rows = rows_by_end(doc)
        assert rows[date(2025, 12, 31)]["filed"] == date(2026, 2, 14)
        assert rows[date(2025, 9, 30)]["filed"] == date(2025, 10, 31)

    def test_eps_uses_the_per_share_unit_only(self):
        doc = company(
            {
                **self._income(),
                "EarningsPerShareDiluted": {
                    "USD/shares": [fact(Q1, 0.5)],
                    "USD": [fact(Q1, 999)],
                },
            }
        )
        assert rows_by_end(doc)[date(2025, 3, 31)]["eps_diluted"] == 0.5


class TestMergeSeries:
    def _series(self, values):
        return {
            end: xbrl.QPoint(
                end=end,
                val=val,
                filed=date(2025, 5, 2),
                accn="a",
                form="10-Q",
                fy=2025,
                fp="Q1",
                derived=False,
            )
            for end, val in values.items()
        }

    D = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31)]
    PRIORITY = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")

    def test_a_taxonomy_hand_over_is_stitched(self):
        old = self._series({self.D[0]: 80, self.D[1]: 84, self.D[2]: 88})
        new = self._series({self.D[2]: 88.4, self.D[3]: 96})  # agrees within 1 %
        merged = xbrl.merge_series(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": old,
                "Revenues": new,
            },
            self.PRIORITY,
            date(2021, 7, 1),
        )
        assert {e: (t, p.val) for e, (t, p) in merged.items()} == {
            self.D[0]: ("RevenueFromContractWithCustomerExcludingAssessedTax", 80),
            self.D[1]: ("RevenueFromContractWithCustomerExcludingAssessedTax", 84),
            self.D[2]: ("Revenues", 88.4),  # the base (latest) tag wins on the overlap
            self.D[3]: ("Revenues", 96),
        }

    def test_different_measures_are_not_mixed(self):
        # same quarters, values differ by 3 %: another definition of "revenue"
        total = self._series({self.D[1]: 100, self.D[2]: 103})
        sales = self._series({self.D[0]: 90, self.D[1]: 97, self.D[2]: 100})
        merged = xbrl.merge_series(
            {
                "Revenues": total,
                "RevenueFromContractWithCustomerExcludingAssessedTax": sales,
            },
            self.PRIORITY,
            date(2021, 7, 1),
        )
        assert {t for t, _ in merged.values()} == {
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        }
        assert len(merged) == 3

    def test_a_sparse_recent_tag_cannot_become_the_base(self):
        full = self._series({d: 100 for d in self.D})
        sparse = self._series({date(2025, 3, 31): 100})
        merged = xbrl.merge_series(
            {
                "Revenues": full,
                "RevenueFromContractWithCustomerExcludingAssessedTax": sparse,
            },
            self.PRIORITY,
            date(2021, 7, 1),
        )
        # sparse has 25 % of the coverage -> not eligible; no overlap -> not stitched
        assert {t for t, _ in merged.values()} == {"Revenues"} and len(merged) == 4

    def test_priority_breaks_ties_and_empty_input_is_empty(self):
        a = self._series({self.D[0]: 1})
        b = self._series({self.D[0]: 1})
        merged = xbrl.merge_series(
            {"RevenueFromContractWithCustomerExcludingAssessedTax": b, "Revenues": a},
            self.PRIORITY,
            date(2021, 7, 1),
        )
        assert merged[self.D[0]][0] == "Revenues"
        assert xbrl.merge_series({}, self.PRIORITY, date(2021, 7, 1)) == {}
        assert (
            xbrl.merge_series({"Revenues": {}}, self.PRIORITY, date(2021, 7, 1)) == {}
        )
