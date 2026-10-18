"""Sections of a ticker file, in the order of ``docs/CONTEXT_PACK.md``.

Every renderer is a pure function ``TickerContext -> list[str]`` (Markdown
lines, starting with its ``## N. Title`` heading and ending with a blank line).
"""

from __future__ import annotations

from collections.abc import Callable

from trading_signals.derived.context_pack.model import TickerContext
from trading_signals.derived.context_pack.sections.estimates import (
    render_earnings,
    render_estimates,
)
from trading_signals.derived.context_pack.sections.filings import (
    render_sec_filings,
)
from trading_signals.derived.context_pack.sections.flows import (
    render_positioning,
    render_smart_money,
)
from trading_signals.derived.context_pack.sections.fundamentals import (
    render_fundamentals,
    render_valuation,
)
from trading_signals.derived.context_pack.sections.head import render_head
from trading_signals.derived.context_pack.sections.news import render_news
from trading_signals.derived.context_pack.sections.peers import render_peers
from trading_signals.derived.context_pack.sections.price import (
    render_liquidity,
    render_sector,
    render_trend,
)
from trading_signals.derived.context_pack.sections.quality import (
    render_quality,
    render_regime,
)

Section = Callable[[TickerContext], list[str]]

#: Order = section numbers 1 ... 15 (the headings carry the numbers). The peer
#: section (14) and the SEC filings (15) were added last (additive changes of
#: pack format 2): sections 1-13 keep their numbers.
SECTIONS: tuple[Section, ...] = (
    render_head,
    render_trend,
    render_valuation,
    render_fundamentals,
    render_estimates,
    render_earnings,
    render_smart_money,
    render_positioning,
    render_news,
    render_sector,
    render_liquidity,
    render_quality,
    render_regime,
    render_peers,
    render_sec_filings,
)

__all__ = ["SECTIONS", "Section"]
