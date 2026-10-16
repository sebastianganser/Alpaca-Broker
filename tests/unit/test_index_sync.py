"""Tests for IndexSyncer."""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.sql.dml import Update

from trading_signals.universe.corporate_actions import CorporateEvents, NameChange
from trading_signals.universe.index_sync import (
    NASDAQ100_CURRENT_URL,
    IndexSyncer,
    IndexSyncSanityError,
    SyncResult,
    check_index_sizes,
)
from trading_signals.universe.wikipedia_index_history import (
    SP500_CURRENT_URL,
    IndexChange,
    ParseResult,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def _no_network():
    """No Wikipedia / Alpaca access (and no .env) from any test in this file."""
    parser = MagicMock()
    parser.parse_sp500_changes.return_value = ParseResult(source="wikipedia_sp500")
    parser.parse_nasdaq100_changes.return_value = ParseResult(
        source="wikipedia_nasdaq100"
    )
    with (
        patch(
            "trading_signals.universe.index_sync.WikipediaIndexHistoryParser",
            return_value=parser,
        ),
        patch("trading_signals.universe.index_sync.AlpacaCorporateActions") as corp,
        patch(
            "trading_signals.universe.index_sync.fetch_html",
            side_effect=AssertionError("network access in a unit test"),
        ),
    ):
        corp.return_value.fetch.return_value = CorporateEvents()
        yield


class TestSyncResult:
    """Test the SyncResult dataclass."""

    def test_default_values(self):
        result = SyncResult()
        assert result.sp500_count == 0
        assert result.nasdaq100_count == 0
        assert result.newly_added == 0
        assert result.new_tickers == []

    def test_fields_independent(self):
        r1 = SyncResult()
        r2 = SyncResult()
        r1.new_tickers.append("TEST")
        assert r2.new_tickers == []


class TestIndexSyncer:
    """Test IndexSyncer with mocked API calls."""

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value={"AAPL", "MSFT"})
    @patch.object(IndexSyncer, "_fetch_sp500", return_value={"AAPL", "GOOG", "AMZN"})
    def test_sync_dry_run(self, mock_sp, mock_nq):
        """Dry run should not modify anything."""
        session = MagicMock()
        # Mock existing universe as empty
        session.execute.return_value.all.return_value = []

        syncer = IndexSyncer(session)

        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator:
            mock_instance = MagicMock()
            mock_instance.fetch_all_assets.return_value = {
                "AAPL": MagicMock(tradable=True, exchange="NASDAQ", name="Apple"),
                "MSFT": MagicMock(tradable=True, exchange="NASDAQ", name="Microsoft"),
                "GOOG": MagicMock(tradable=True, exchange="NASDAQ", name="Alphabet"),
                "AMZN": MagicMock(tradable=True, exchange="NASDAQ", name="Amazon"),
            }
            mock_validator.return_value = mock_instance

            result = syncer.sync(dry_run=True, sanity_check=False)

        assert result.sp500_count == 3
        assert result.nasdaq100_count == 2

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set())
    @patch.object(IndexSyncer, "_fetch_sp500", return_value={"NEW_TICKER"})
    def test_sync_adds_new_tickers(self, mock_sp, mock_nq):
        """New tickers should be added if tradeable on Alpaca."""
        session = MagicMock()
        # Empty universe
        session.execute.return_value.all.return_value = []

        syncer = IndexSyncer(session)

        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator:
            mock_instance = MagicMock()
            mock_instance.fetch_all_assets.return_value = {
                "NEW_TICKER": MagicMock(
                    tradable=True, exchange="NYSE", name="New Corp"
                ),
            }
            mock_validator.return_value = mock_instance

            with patch.object(syncer._manager, "add_ticker"):
                result = syncer.sync(dry_run=False, sanity_check=False)

        assert result.newly_added == 1
        assert "NEW_TICKER" in result.new_tickers

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set())
    @patch.object(IndexSyncer, "_fetch_sp500", return_value={"FAKE_TICKER"})
    def test_sync_skips_untradeable(self, mock_sp, mock_nq):
        """Untradeable tickers should not be added."""
        session = MagicMock()
        session.execute.return_value.all.return_value = []

        syncer = IndexSyncer(session)

        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator:
            mock_instance = MagicMock()
            mock_instance.fetch_all_assets.return_value = {}  # No assets match
            mock_validator.return_value = mock_instance

            result = syncer.sync(dry_run=False, sanity_check=False)

        assert result.newly_added == 0
        assert result.not_tradeable == 1


class TestIndexSyncSanity:
    """H2: implausible list sizes must abort before any DB update."""

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set())
    @patch.object(
        IndexSyncer, "_fetch_sp500", return_value={f"T{i}" for i in range(503)}
    )
    def test_empty_nasdaq_table_aborts_before_updates(self, mock_sp, mock_nq):
        session = MagicMock()
        syncer = IndexSyncer(session)
        with pytest.raises(IndexSyncSanityError):
            syncer.sync(dry_run=False)
        session.execute.assert_not_called()
        session.add.assert_not_called()

    def test_plausible_sizes_pass(self):
        check_index_sizes({
            "sp500": {f"S{i}" for i in range(503)},
            "nasdaq100": {f"N{i}" for i in range(101)},
        })

    @patch.object(
        IndexSyncer, "_fetch_nasdaq100", return_value={f"N{i}" for i in range(101)}
    )
    @patch.object(
        IndexSyncer, "_fetch_sp500", return_value={f"S{i}" for i in range(503)}
    )
    def test_leavers_membership_cleared(self, mock_sp, mock_nq):
        session = MagicMock()
        existing = [("S1", ["sp500"]), ("GONE", ["sp500"]), ("NEVER", None)]
        session.execute.return_value.all.side_effect = [existing, [], []]
        syncer = IndexSyncer(session)
        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator, patch.object(syncer._manager, "add_ticker"):
            mock_validator.return_value.fetch_all_assets.return_value = {}
            result = syncer.sync(dry_run=False)
        assert result.membership_cleared == 1
        cleared = [
            c[0][0] for c in session.execute.call_args_list
            if "index_membership" in str(c[0][0])
            and "GONE" in str(c[0][0].compile(compile_kwargs={"literal_binds": True}))
        ]
        assert cleared, "GONE must have its index_membership cleared"


# ── Interval maintenance (effective dates, renames) ─────────────────────


def _syncer(sp500_open=(), nasdaq_open=(), **kwargs):
    """IndexSyncer on a mock session; ``*_open`` = open (ticker, from, reason) rows."""
    session = MagicMock()
    session.execute.return_value.all.side_effect = [
        list(sp500_open),
        list(nasdaq_open),
    ]
    return IndexSyncer(session, **kwargs), session


def _updates(session):
    """Compiled parameters of every UPDATE index_membership statement."""
    out = []
    for call in session.execute.call_args_list:
        stmt = call.args[0]
        if isinstance(stmt, Update) and stmt.table.name == "index_membership":
            out.append(stmt.compile().params)
    return out


def _added(session):
    return [c.args[0] for c in session.add.call_args_list]


def _membership(sp500=(), nasdaq100=()):
    m: dict[str, set[str]] = {}
    for t in sp500:
        m.setdefault(t, set()).add("sp500")
    for t in nasdaq100:
        m.setdefault(t, set()).add("nasdaq100")
    return m


def _dates(**entries):
    """``_dates(sp500_TAP_removed=date)`` -> effective-date dict."""
    return {tuple(k.split("_")): v for k, v in entries.items()}


FROM = date(2021, 1, 1)


class TestEffectiveDates:
    """Intervals use the change date of the Wikipedia table, not the sync date."""

    def test_removal_closes_at_the_effective_date(self):
        syncer, session = _syncer(
            sp500_open=[("TAP", FROM, None), ("KEEP", FROM, None)]
        )
        result = SyncResult()
        found = _dates(sp500_TAP_removed=date(2026, 9, 21))
        with patch.object(syncer, "_effective_dates", return_value=found):
            syncer._update_membership_intervals(_membership(sp500=["KEEP"]), result)
        (upd,) = _updates(session)
        assert upd["valid_to"] == date(2026, 9, 21)
        assert upd["ticker_1"] == "TAP" and upd["index_name_1"] == "sp500"
        assert result.date_fallbacks == []

    def test_addition_opens_at_the_effective_date(self):
        syncer, session = _syncer()
        found = _dates(sp500_BE_added=date(2026, 9, 21))
        with patch.object(syncer, "_effective_dates", return_value=found):
            syncer._update_membership_intervals(_membership(sp500=["BE"]))
        (iv,) = _added(session)
        assert (iv.ticker, iv.index_name, iv.valid_from, iv.valid_to) == (
            "BE",
            "sp500",
            date(2026, 9, 21),
            None,
        )
        assert iv.source == "index_sync"

    def test_latest_event_of_a_re_added_ticker_is_used(self):
        """ILMN left in 2024 and came back 2026-09-21: the table holds both."""
        syncer, session = _syncer()
        parser = MagicMock()
        parser.parse_sp500_changes.return_value = ParseResult(
            source="wikipedia_sp500",
            changes=[
                IndexChange(date(2024, 6, 24), "ILMN", "sp500", "removed"),
                IndexChange(date(2026, 9, 21), "ILMN", "sp500", "added"),
            ],
        )
        parser.parse_nasdaq100_changes.return_value = ParseResult()
        syncer._history_parser = parser
        syncer._update_membership_intervals(_membership(sp500=["ILMN"]))
        assert _added(session)[0].valid_from == date(2026, 9, 21)

    def test_change_missing_in_the_table_uses_sync_date_and_warns(self, caplog):
        syncer, session = _syncer(sp500_open=[("GONE", FROM, None)])
        result = SyncResult()
        with patch.object(syncer, "_effective_dates", return_value={}):
            with caplog.at_level("WARNING"):
                syncer._update_membership_intervals(_membership(sp500=["NEW"]), result)
        (iv,) = _added(session)
        assert iv.valid_from == date.today()
        (upd,) = _updates(session)
        assert upd["valid_to"] == date.today()
        assert sorted(result.date_fallbacks) == [
            "sp500 GONE removed",
            "sp500 NEW added",
        ]
        warned = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("NEW" in m and "not in the changes table" in m for m in warned)
        assert any("GONE" in m and "not in the changes table" in m for m in warned)

    def test_removal_not_after_valid_from_falls_back(self):
        syncer, session = _syncer(sp500_open=[("OLD", date(2025, 6, 1), None)])
        result = SyncResult()
        found = _dates(sp500_OLD_removed=date(2024, 6, 24))
        with patch.object(syncer, "_effective_dates", return_value=found):
            syncer._update_membership_intervals(_membership(), result)
        assert _updates(session)[0]["valid_to"] == date.today()
        assert result.date_fallbacks == ["sp500 OLD removed"]

    def test_future_date_falls_back(self):
        syncer, session = _syncer()
        future = date.today() + timedelta(days=30)
        result = SyncResult()
        found = _dates(sp500_SOON_added=future)
        with patch.object(syncer, "_effective_dates", return_value=found):
            syncer._update_membership_intervals(_membership(sp500=["SOON"]), result)
        assert _added(session)[0].valid_from == date.today()
        assert result.date_fallbacks == ["sp500 SOON added"]

    def test_indexes_are_dated_independently(self):
        syncer, session = _syncer(nasdaq_open=[("EA", FROM, None)])
        found = _dates(
            sp500_EA_removed=date(2026, 8, 5),
            nasdaq100_EA_removed=date(2026, 8, 4),
        )
        with patch.object(syncer, "_effective_dates", return_value=found):
            syncer._update_membership_intervals(_membership())
        (upd,) = _updates(session)
        assert upd["index_name_1"] == "nasdaq100"
        assert upd["valid_to"] == date(2026, 8, 4)

    def test_nothing_to_date_means_no_wikipedia_fetch(self):
        syncer, session = _syncer(sp500_open=[("KEEP", FROM, None)])
        with patch.object(syncer, "_effective_dates") as dates:
            syncer._update_membership_intervals(_membership(sp500=["KEEP"]))
        dates.assert_not_called()
        session.add.assert_not_called()
        assert _updates(session) == []


class TestEffectiveDatesSource:
    @staticmethod
    def _parser(sp500=(), nasdaq=()):
        parser = MagicMock()
        parser.parse_sp500_changes.return_value = ParseResult(changes=list(sp500))
        parser.parse_nasdaq100_changes.return_value = ParseResult(changes=list(nasdaq))
        return parser

    def test_newest_event_per_index_ticker_action(self):
        parser = self._parser(
            sp500=[
                IndexChange(date(2024, 6, 24), "ILMN", "sp500", "removed"),
                IndexChange(date(2026, 9, 21), "ILMN", "sp500", "added"),
                IndexChange(date(2022, 1, 5), "ILMN", "sp500", "added"),
            ],
            nasdaq=[IndexChange(date(2026, 8, 4), "EA", "nasdaq100", "removed")],
        )
        dates = IndexSyncer(MagicMock(), history_parser=parser)._effective_dates()
        assert dates == {
            ("sp500", "ILMN", "removed"): date(2024, 6, 24),
            ("sp500", "ILMN", "added"): date(2026, 9, 21),
            ("nasdaq100", "EA", "removed"): date(2026, 8, 4),
        }

    def test_fetched_once_and_failure_is_not_fatal(self, caplog):
        parser = self._parser(
            nasdaq=[IndexChange(date(2026, 8, 4), "EA", "nasdaq100", "removed")]
        )
        parser.parse_sp500_changes.side_effect = RuntimeError("layout")
        syncer = IndexSyncer(MagicMock(), history_parser=parser)
        with caplog.at_level("WARNING"):
            first = syncer._effective_dates()
            second = syncer._effective_dates()
        assert first == second == {("nasdaq100", "EA", "removed"): date(2026, 8, 4)}
        assert parser.parse_nasdaq100_changes.call_count == 1
        assert "sp500 changes table unavailable" in caplog.text


class TestRenames:
    """A rename re-keys the open interval; it is not an exit."""

    def test_known_alias_rekeys_instead_of_close_and_open(self):
        syncer, session = _syncer(
            sp500_open=[("PSKY", date(2025, 8, 8), "Market capitalization changes.")]
        )
        result = SyncResult()
        with patch.object(syncer, "_effective_dates") as dates:
            syncer._update_membership_intervals(_membership(sp500=["SKYD"]), result)
        dates.assert_not_called()  # nothing left to date
        session.add.assert_not_called()
        (upd,) = _updates(session)
        assert upd["ticker"] == "SKYD"  # new value
        assert upd["ticker_1"] == "PSKY" and upd["valid_from_1"] == date(2025, 8, 8)
        assert "valid_to" not in upd  # still open
        assert upd["reason"].startswith("Market capitalization changes.; ")
        assert "ticker renamed PSKY->SKYD 2026-10-06" in upd["reason"]
        assert result.renamed == ["PSKY->SKYD"]

    def test_rename_alongside_ordinary_changes(self):
        syncer, session = _syncer(
            sp500_open=[("PSKY", date(2025, 8, 8), None), ("TAP", FROM, None)]
        )
        result = SyncResult()
        found = _dates(
            sp500_TAP_removed=date(2026, 9, 21), sp500_BE_added=date(2026, 9, 21)
        )
        with patch.object(syncer, "_effective_dates", return_value=found):
            syncer._update_membership_intervals(
                _membership(sp500=["SKYD", "BE"]), result
            )
        assert result.renamed == ["PSKY->SKYD"]
        rekey, close = sorted(_updates(session), key=lambda p: "valid_to" in p)
        assert rekey["ticker"] == "SKYD"
        assert close["ticker_1"] == "TAP" and close["valid_to"] == date(2026, 9, 21)
        assert [iv.ticker for iv in _added(session)] == ["BE"]

    def test_alpaca_name_change_rekeys_unknown_rename(self):
        corp = MagicMock()
        corp.fetch.return_value = CorporateEvents(
            name_changes=[NameChange("OLDX", "NEWX", date(2026, 10, 1))]
        )
        syncer, session = _syncer(
            sp500_open=[("OLDX", FROM, None)], corporate_actions=corp
        )
        result = SyncResult()
        syncer._update_membership_intervals(_membership(sp500=["NEWX"]), result)
        assert result.renamed == ["OLDX->NEWX"]
        session.add.assert_not_called()
        (upd,) = _updates(session)
        assert upd["ticker"] == "NEWX" and upd["ticker_1"] == "OLDX"
        assert "2026-10-01" in upd["reason"]
        args, kwargs = corp.fetch.call_args
        assert set(args[0]) == {"OLDX"}
        assert kwargs["include_mergers"] is False
        assert kwargs["end"] == date.today()

    def test_name_change_to_a_ticker_that_did_not_appear_is_ignored(self):
        corp = MagicMock()
        corp.fetch.return_value = CorporateEvents(
            name_changes=[NameChange("OLDX", "ELSEWHERE", date(2026, 10, 1))]
        )
        syncer, session = _syncer(
            sp500_open=[("OLDX", FROM, None)], corporate_actions=corp
        )
        with patch.object(syncer, "_effective_dates", return_value={}):
            syncer._update_membership_intervals(_membership(sp500=["NEWX"]))
        assert [iv.ticker for iv in _added(session)] == ["NEWX"]
        (upd,) = _updates(session)
        assert "valid_to" in upd and upd["ticker_1"] == "OLDX"

    def test_alpaca_failure_falls_back_to_close_and_open(self, caplog):
        corp = MagicMock()
        corp.fetch.side_effect = RuntimeError("alpaca down")
        syncer, session = _syncer(
            sp500_open=[("OLDX", FROM, None)], corporate_actions=corp
        )
        result = SyncResult()
        with patch.object(syncer, "_effective_dates", return_value={}):
            with caplog.at_level("WARNING"):
                syncer._update_membership_intervals(
                    _membership(sp500=["NEWX"]), result
                )
        assert result.renamed == []
        assert [iv.ticker for iv in _added(session)] == ["NEWX"]
        assert "name_change lookup failed" in caplog.text

    def test_alpaca_not_asked_when_the_alias_list_resolves_everything(self):
        corp = MagicMock()
        syncer, _ = _syncer(
            sp500_open=[("PSKY", date(2025, 8, 8), None)], corporate_actions=corp
        )
        syncer._update_membership_intervals(_membership(sp500=["SKYD"]))
        corp.fetch.assert_not_called()

    def test_alpaca_not_asked_without_additions(self):
        corp = MagicMock()
        syncer, _ = _syncer(sp500_open=[("OLDX", FROM, None)], corporate_actions=corp)
        with patch.object(syncer, "_effective_dates", return_value={}):
            syncer._update_membership_intervals(_membership())
        corp.fetch.assert_not_called()

    def test_full_sync_reports_the_rename(self):
        session = MagicMock()
        universe_rows = [("PSKY", ["sp500"]), ("AAA", ["sp500"])]
        session.execute.return_value.all.side_effect = [
            universe_rows,
            [("PSKY", date(2025, 8, 8), None), ("AAA", FROM, None)],
            [],
        ]
        syncer = IndexSyncer(session)
        with (
            patch.object(IndexSyncer, "_fetch_sp500", return_value={"SKYD", "AAA"}),
            patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set()),
            patch("trading_signals.universe.index_sync.AlpacaAssetValidator") as val,
            patch.object(syncer._manager, "add_ticker"),
        ):
            val.return_value.fetch_all_assets.return_value = {
                "SKYD": MagicMock(tradable=True, exchange="NASDAQ", name="Skydance")
            }
            result = syncer.sync(dry_run=False, sanity_check=False)
        assert result.renamed == ["PSKY->SKYD"]
        assert result.date_fallbacks == []
        assert result.membership_cleared == 1  # PSKY left the universe flags
        session.add.assert_not_called()


class TestWikipediaFetchers:
    def test_sp500_current_list(self):
        html = (FIXTURES / "wikipedia_sp500_current.html").read_text(encoding="utf-8")
        with patch(
            "trading_signals.universe.index_sync.fetch_html", return_value=html
        ) as fetch:
            tickers = IndexSyncer(MagicMock())._fetch_sp500()
        assert fetch.call_args.args[0] == SP500_CURRENT_URL
        assert {"SKYD", "BNY", "BRK.B", "P"} <= tickers

    def test_nasdaq100_current_list(self):
        html = pd.DataFrame({"Ticker": [f"T{i}" for i in range(101)]}).to_html()
        with patch(
            "trading_signals.universe.index_sync.fetch_html", return_value=html
        ) as fetch:
            tickers = IndexSyncer(MagicMock())._fetch_nasdaq100()
        assert fetch.call_args.args[0] == NASDAQ100_CURRENT_URL
        assert len(tickers) == 101
