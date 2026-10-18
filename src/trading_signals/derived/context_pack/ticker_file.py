"""Assemble one ticker file: YAML frontmatter plus the 13 sections."""

from __future__ import annotations

from trading_signals.derived.context_pack.constants import PACK_FORMAT
from trading_signals.derived.context_pack.fmt import clean_text
from trading_signals.derived.context_pack.model import TickerContext
from trading_signals.derived.context_pack.sections import SECTIONS
from trading_signals.derived.context_pack.sections.quality import source_freshness


def _yaml_text(value: str | None) -> str:
    """A quoted YAML scalar (``null`` for a missing value)."""
    if value is None:
        return "null"
    flat = clean_text(value, 120).replace("\\", "/").replace('"', "'")
    return f'"{flat}"'


def frontmatter(ctx: TickerContext) -> list[str]:
    """Frontmatter: format, ticker, session and the freshness of each source.

    No rank, bucket or score: the file must not reveal why it is in the pack.
    """
    lines = [
        "---",
        f"pack_format: {PACK_FORMAT}",
        f"ticker: {ctx.ticker}",
        f"session: {ctx.session.isoformat()}",
        f"sector: {_yaml_text(ctx.info.sector)}",
        "data_freshness:",
    ]
    for source, last in source_freshness(ctx).items():
        lines.append(f"  {source}: {last.isoformat() if last else 'null'}")
    lines.append("---")
    return lines


def render_ticker_file(ctx: TickerContext) -> str:
    """The full text of ``<TICKER>.md``."""
    title = ctx.info.company_name or ctx.ticker
    lines = [
        *frontmatter(ctx),
        "",
        f"# {ctx.ticker} — {clean_text(title, 80)} · "
        f"Kontext zum {ctx.session.isoformat()}",
        "",
        "Stand Börsenschluss des Pack-Tags, nur bis dahin öffentliche Informationen. "
        f"**P** = Perzentil des Rohwerts im Gate-Universum (n={ctx.xs.n_universe}), "
        "**S** = im Sektor; hoch = hoher *Wert*, keine Bewertung als gut oder "
        "schlecht. „keine Abdeckung“ = die Quelle liefert für diesen Ticker "
        "nichts (nicht 0).",
        "",
    ]
    for section in SECTIONS:
        lines += section(ctx)
    lines += [
        "---",
        f"*Generiert für die Sitzung {ctx.session.isoformat()} · Keine Anlageberatung*",
    ]
    return "\n".join(lines) + "\n"
