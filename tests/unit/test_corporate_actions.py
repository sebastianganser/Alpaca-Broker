"""Tests for the Alpaca corporate-actions client (HTTP mocked, no .env)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.universe.corporate_actions import (
    CORPORATE_ACTIONS_URL,
    SYMBOL_BATCH_SIZE,
    AlpacaCorporateActions,
    CorporateEvents,
    Merger,
    NameChange,
)

START, END = date(2025, 10, 16), date(2026, 10, 16)


@pytest.fixture
def client():
    settings = MagicMock(ALPACA_API_KEY="key-id", ALPACA_SECRET_KEY="secret")
    with patch(
        "trading_signals.universe.corporate_actions.get_settings",
        return_value=settings,
    ):
        yield AlpacaCorporateActions()


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _get(payloads):
    """Patch ``requests.get`` to return the given payloads in order."""
    return patch(
        "trading_signals.universe.corporate_actions.requests.get",
        side_effect=[_response(p) for p in payloads],
    )


class TestFetch:
    def test_request_shape_and_headers(self, client):
        with _get([{"corporate_actions": {}}]) as get:
            client.fetch(["psky", "WBD", "PSKY"], START, END)
        (url,), kwargs = get.call_args
        assert url == CORPORATE_ACTIONS_URL
        assert url == "https://data.alpaca.markets/v1/corporate-actions"
        assert kwargs["headers"] == {
            "APCA-API-KEY-ID": "key-id",
            "APCA-API-SECRET-KEY": "secret",
        }
        params = kwargs["params"]
        assert params["symbols"] == "PSKY,WBD"  # upper-cased, de-duplicated, sorted
        assert params["types"] == (
            "name_change,cash_merger,stock_merger,stock_and_cash_merger"
        )
        assert (params["start"], params["end"]) == ("2025-10-16", "2026-10-16")
        assert "page_token" not in params

    def test_name_changes_only(self, client):
        with _get([{"corporate_actions": {}}]) as get:
            client.fetch(["OLDX"], START, END, include_mergers=False)
        assert get.call_args.kwargs["params"]["types"] == "name_change"

    def test_parses_name_changes_and_mergers(self, client):
        payload = {
            "corporate_actions": {
                "name_changes": [
                    {
                        "old_symbol": "PSKY",
                        "new_symbol": "SKYD",
                        "process_date": "2026-10-06",
                        "old_cusip": "x",
                        "new_cusip": "x",
                    },
                    {"old_symbol": "BROKEN"},  # incomplete rows are skipped
                ],
                "cash_mergers": [
                    {"acquiree_symbol": "EA", "effective_date": "2026-08-04"},
                    {
                        "acquiree_symbol": "DAY",
                        "acquirer_symbol": "",
                        "effective_date": "2026-02-04",
                    },
                ],
                "stock_mergers": [
                    {
                        "acquiree_symbol": "AVB",
                        "acquirer_symbol": "EQR",
                        "effective_date": "2026-08-17",
                    }
                ],
                "stock_and_cash_mergers": [
                    {
                        "acquiree_symbol": "HOLX",
                        "acquirer_symbol": "436CVR021",
                        "process_date": "2026-04-07",
                    }
                ],
                "spin_offs": [{"source_symbol": "IGNORED"}],
            }
        }
        with _get([payload]):
            events = client.fetch(["PSKY"], START, END)
        assert events.name_changes == [NameChange("PSKY", "SKYD", date(2026, 10, 6))]
        assert events.mergers == [
            Merger("EA", None, date(2026, 8, 4), "cash_merger"),
            Merger("DAY", None, date(2026, 2, 4), "cash_merger"),
            Merger("AVB", "EQR", date(2026, 8, 17), "stock_merger"),
            Merger("HOLX", "436CVR021", date(2026, 4, 7), "stock_and_cash_merger"),
        ]

    def test_follows_pagination(self, client):
        pages = [
            {
                "corporate_actions": {
                    "name_changes": [
                        {
                            "old_symbol": "A",
                            "new_symbol": "B",
                            "process_date": "2026-01-02",
                        }
                    ]
                },
                "next_page_token": "tok1",
            },
            {
                "corporate_actions": {
                    "name_changes": [
                        {
                            "old_symbol": "C",
                            "new_symbol": "D",
                            "process_date": "2026-01-03",
                        }
                    ]
                },
                "next_page_token": None,
            },
        ]
        with _get(pages) as get:
            events = client.fetch(["A", "C"], START, END)
        assert [c.old_symbol for c in events.name_changes] == ["A", "C"]
        assert get.call_count == 2
        assert "page_token" not in get.call_args_list[0].kwargs["params"]
        assert get.call_args_list[1].kwargs["params"]["page_token"] == "tok1"

    def test_symbols_are_sent_in_batches(self, client):
        symbols = [f"T{i:03d}" for i in range(SYMBOL_BATCH_SIZE * 2 + 1)]
        with _get([{"corporate_actions": {}}] * 3) as get:
            client.fetch(symbols, START, END)
        sent = [c.kwargs["params"]["symbols"].split(",") for c in get.call_args_list]
        assert [len(s) for s in sent] == [SYMBOL_BATCH_SIZE, SYMBOL_BATCH_SIZE, 1]
        assert sorted(s for batch in sent for s in batch) == sorted(symbols)

    def test_no_symbols_no_request(self, client):
        with _get([]) as get:
            events = client.fetch([], START, END)
        get.assert_not_called()
        assert events == CorporateEvents()


class TestCorporateEvents:
    def test_filters_by_old_and_acquiree_symbol(self):
        events = CorporateEvents(
            name_changes=[
                NameChange("PSKY", "SKYD", date(2026, 10, 6)),
                NameChange("BNZI", "PARA", date(2026, 8, 7)),
            ],
            mergers=[Merger("WBD", None, date(2026, 10, 6), "cash_merger")],
        )
        assert [c.new_symbol for c in events.renames_of("psky")] == ["SKYD"]
        assert events.renames_of("SKYD") == []  # only the old symbol counts
        assert [m.kind for m in events.mergers_of("WBD")] == ["cash_merger"]
        assert events.mergers_of("PSKY") == []
