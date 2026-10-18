"""Peer selection of the context pack (pure, deterministic, no DB)."""

from __future__ import annotations

import random

from trading_signals.derived.context_pack.model import UniverseInfo
from trading_signals.derived.context_pack.peers import select_peers


def universe(spec: dict[str, tuple[str, str]]) -> dict[str, UniverseInfo]:
    """``{ticker: (sector, industry)}`` as universe info."""
    return {t: UniverseInfo(f"Co {t}", s, i) for t, (s, i) in spec.items()}


SEMI = ("Technology", "Semiconductors")
SOFT = ("Technology", "Software")
BANK = ("Financial Services", "Banks")


def test_peers_come_from_the_same_industry_closest_in_market_cap():
    info = universe(
        {
            "ME": SEMI,
            "A": SEMI,
            "B": SEMI,
            "C": SEMI,
            "D": SEMI,
            "E": SEMI,
            "F": SEMI,
            "SOFT": SOFT,
            "BANK": BANK,
        }
    )
    caps = {
        "ME": 100.0,
        "A": 95.0,  # ln(100/95) = 0.051
        "B": 112.0,  # 0.113
        "C": 60.0,  # 0.511
        "D": 400.0,  # 1.386
        "E": 105.0,  # 0.049 -> closest
        "F": 20.0,
        "SOFT": 100.0,
        "BANK": 100.0,
    }
    sel = select_peers("ME", info, set(info), caps)
    assert sel.industry == "Semiconductors" and sel.sector == "Technology"
    assert sel.industry_peers == ("E", "A", "B", "C")
    assert sel.sector_peers == () and sel.n_industry_pool == 6
    assert sel.tickers == ("E", "A", "B", "C")
    assert "ME" not in sel.tickers  # never itself


def test_only_gate_tickers_are_peers():
    info = universe({"ME": SEMI, "A": SEMI, "B": SEMI})
    caps = {"ME": 10.0, "A": 10.0, "B": 10.0}
    sel = select_peers("ME", info, {"ME", "B"}, caps)  # A failed the quality gate
    assert sel.industry_peers == ("B",) and sel.n_industry_pool == 1


def test_a_small_industry_is_topped_up_from_the_sector():
    info = universe(
        {
            "ME": SEMI,
            "A": SEMI,
            "S1": SOFT,
            "S2": SOFT,
            "S3": SOFT,
            "S4": SOFT,
            "BANK": BANK,
        }
    )
    caps = {"ME": 100.0, "A": 50.0, "S1": 101.0, "S2": 90.0, "S3": 150.0, "S4": 5.0}
    sel = select_peers("ME", info, set(info), caps)
    assert sel.industry_peers == ("A",)
    assert sel.sector_peers == ("S1", "S2", "S3")  # closest first, 4 in total
    assert "BANK" not in sel.tickers and sel.n_industry_pool == 1


def test_without_an_industry_the_sector_is_the_pool():
    info = {
        "ME": UniverseInfo("Me", "Technology", None),
        "A": UniverseInfo("A", "Technology", "X"),
        "B": UniverseInfo("B", "Technology", None),
        "OTHER": UniverseInfo("O", "Energy", None),
    }
    sel = select_peers("ME", info, set(info), {"ME": 1.0, "A": 1.0, "B": 1.0})
    assert sel.industry is None and sel.n_industry_pool == 0
    assert sel.sector_peers == ("A", "B")
    unknown = select_peers("NOPE", info, set(info), {})
    assert unknown.tickers == () and unknown.industry is None


def test_missing_market_caps_rank_last_and_an_unknown_own_cap_prefers_large_peers():
    info = universe({"ME": SEMI, "A": SEMI, "B": SEMI, "C": SEMI})
    caps = {"ME": 100.0, "A": None, "B": float("nan"), "C": 90.0}
    sel = select_peers("ME", info, set(info), caps)
    assert sel.industry_peers == ("C", "A", "B")  # unknown caps last, by ticker
    no_own = select_peers("ME", info, set(info), {"A": 5.0, "B": 50.0, "C": 20.0})
    assert no_own.industry_peers == ("B", "C", "A")  # the largest first
    zero = select_peers("ME", info, set(info), {"ME": 0.0, "A": 5.0, "B": 50.0})
    assert zero.industry_peers[:2] == ("B", "A")  # a zero cap is no cap


def test_ties_break_by_ticker_and_the_input_order_does_not_matter():
    spec = {f"T{i:02d}": SEMI for i in range(12)}
    caps = {t: 100.0 for t in spec}  # all equally close
    reference = select_peers("T05", universe(spec), set(spec), caps)
    assert reference.industry_peers == ("T00", "T01", "T02", "T03")
    items = list(spec.items())
    for seed in range(5):
        random.Random(seed).shuffle(items)
        shuffled = universe(dict(items))
        gate = {t for t, _ in items}
        assert select_peers("T05", shuffled, gate, caps) == reference


def test_the_limit_is_respected():
    info = universe({f"T{i}": SEMI for i in range(10)})
    caps = {t: float(i + 1) for i, t in enumerate(info)}
    assert len(select_peers("T0", info, set(info), caps).tickers) == 4
    assert len(select_peers("T0", info, set(info), caps, limit=2).tickers) == 2
