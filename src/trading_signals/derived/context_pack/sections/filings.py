"""Section 15 - SEC filings (8-K items, other forms, press-release excerpts).

Source: ``sec_filings`` / ``sec_filing_texts`` (daily ``sec_filings`` collector,
EDGAR submissions API; form whitelist in ``collectors/sec_filings_collector.py``).
Only filings that were public at the close of the pack day reach this module
(:func:`~trading_signals.derived.context_pack.pit.sec_filing_public_by`).

The excerpts are text written by third parties (companies, filing agents): they
are flattened to one line, stripped of everything that could break a table, and
labelled as external data - the same treatment as the headlines of section 9.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from trading_signals.derived.context_pack.constants import (
    SEC_EXCERPT_CHARS,
    SEC_FILING_ROWS,
    SEC_WINDOW_DAYS,
)
from trading_signals.derived.context_pack.fmt import (
    NA,
    clean_text,
    day,
    md_table,
)
from trading_signals.derived.context_pack.model import TickerContext
from trading_signals.derived.context_pack.rows import (
    SecFilingRow,
    SecTextRow,
    TickerRaw,
)
from trading_signals.derived.feature_pipeline import NY_TZ

#: German short labels of every 8-K item (Form 8-K, General Instructions).
ITEM_LABELS: dict[str, str] = {
    "1.01": "wesentlicher Vertrag abgeschlossen",
    "1.02": "wesentlicher Vertrag beendet",
    "1.03": "Insolvenz / Zwangsverwaltung",
    "1.04": "Grubensicherheit (Betriebsstilllegung)",
    "1.05": "Cybersicherheitsvorfall",
    "2.01": "Übernahme oder Verkauf von Vermögenswerten abgeschlossen",
    "2.02": "Quartalszahlen",
    "2.03": "neue Finanzverbindlichkeit",
    "2.04": "Fälligstellung von Verbindlichkeiten",
    "2.05": "Restrukturierungskosten",
    "2.06": "Wertberichtigung",
    "3.01": "Delisting-Hinweis oder Verstoß gegen Börsenregel",
    "3.02": "Aktienplatzierung ohne Registrierung (Verwässerung möglich)",
    "3.03": "Änderung der Aktionärsrechte",
    "4.01": "Wechsel des Abschlussprüfers",
    "4.02": "Bilanzkorrektur (frühere Abschlüsse nicht verlässlich)",
    "5.01": "Kontrollwechsel",
    "5.02": "Vorstand/Direktoren-Wechsel",
    "5.03": "Satzungs- oder Geschäftsjahresänderung",
    "5.04": "Handelssperre in Mitarbeiterplänen",
    "5.05": "Änderung des Ethikkodex",
    "5.06": "Änderung des Shell-Status",
    "5.07": "Abstimmungsergebnis der Hauptversammlung",
    "5.08": "Direktoren-Nominierung durch Aktionäre",
    "6.01": "ABS-Meldung",
    "6.02": "ABS-Meldung",
    "6.03": "ABS-Meldung",
    "6.04": "ABS-Meldung",
    "6.05": "ABS-Meldung",
    "6.06": "ABS-Meldung",
    "7.01": "Reg FD",
    "8.01": "sonstige Ereignisse",
    "9.01": "Anlagen",
}
#: Boilerplate item that is dropped from a row if the filing has other items.
EXHIBITS_ITEM = "9.01"

#: Base form (without ``/A``) -> (German label, hint or None). Only forms that
#: the collector stores (``FORMS`` of ``sec_filings_collector``). The hints name
#: the usual reading of a capital-markets or ownership form; they are no signal.
FORM_INFO: dict[str, tuple[str, str | None]] = {
    "10-Q": ("Quartalsbericht", None),
    "10-K": ("Jahresbericht", None),
    "6-K": (
        "Ereignismeldung eines ausländischen Emittenten (Inhalt nicht erfasst)",
        None,
    ),
    "20-F": ("Jahresbericht eines ausländischen Emittenten", None),
    "40-F": ("Jahresbericht eines ausländischen Emittenten", None),
    "NT 10-K": (
        "Mitteilung: Jahresbericht verspätet",
        "Warnsignal, Berichtsfrist verpasst",
    ),
    "NT 10-Q": (
        "Mitteilung: Quartalsbericht verspätet",
        "Warnsignal, Berichtsfrist verpasst",
    ),
    "144": ("geplanter Insider-Verkauf (Form 144)", None),
    "SC 13D": ("Beteiligung >= 5 %, aktivistisch (13D)", None),
    "SCHEDULE 13D": ("Beteiligung >= 5 %, aktivistisch (13D)", None),
    "SC 13G": ("passive Beteiligung >= 5 % (13G)", None),
    "SCHEDULE 13G": ("passive Beteiligung >= 5 % (13G)", None),
    "S-1": (
        "Registrierung eines Wertpapierangebots (S-1)",
        "mögliche Kapitalaufnahme, Verwässerung",
    ),
    "S-3": (
        "Shelf-Registrierung (S-3)",
        "mögliche Kapitalaufnahme, Verwässerung",
    ),
    "S-3ASR": (
        "Shelf-Registrierung (S-3ASR)",
        "mögliche Kapitalaufnahme, Verwässerung",
    ),
    "DEF 14A": ("Proxy Statement (Hauptversammlung, Vergütung)", None),
    "DEFM14A": (
        "Proxy Statement zu Fusion oder Übernahme",
        "Abstimmung über Fusion oder Übernahme",
    ),
}
#: Planned insider sales: counted in one line, not listed (no person, no size stored;
#: there can be dozens in 90 days).
FORM_144 = "144"

_ITEM_CODE = re.compile(r"(\d{1,2}\.\d{2})")
_QUOTES = str.maketrans({'"': "'", "„": "'", "“": "'", "”": "'", "‟": "'"})


def item_code(raw: str) -> str:
    """The item number of an EDGAR item string (``"Item 2.02"`` -> ``"2.02"``)."""
    match = _ITEM_CODE.search(raw or "")
    return match.group(1) if match else clean_text(raw, 12)


def item_label(code: str) -> str:
    """``2.02 Quartalszahlen``; an unknown item shows its number only."""
    code = item_code(code)
    label = ITEM_LABELS.get(code)
    return f"{code} {label}" if label else f"Item {code}"


def items_text(items: Sequence[str]) -> str:
    """The item labels of an 8-K, in filing order, duplicates dropped.

    The exhibit item 9.01 is boilerplate and only shown if it is the only item.
    """
    codes: list[str] = []
    for raw in items:
        code = item_code(raw)
        if code not in codes:
            codes.append(code)
    if len(codes) > 1 and EXHIBITS_ITEM in codes:
        codes.remove(EXHIBITS_ITEM)
    return "; ".join(item_label(c) for c in codes)


def base_form(form: str) -> str:
    """The form without the amendment suffix (``SC 13D/A`` -> ``SC 13D``)."""
    return form[:-2] if form.endswith("/A") else form


def describe(f: SecFilingRow) -> str:
    """Content cell of a filing: what it is, the amendment flag and a hint."""
    base = base_form(f.form)
    hint = None
    if base == "8-K":
        text = items_text(f.items) or "Ereignismeldung ohne Item-Angabe"
    elif base in FORM_INFO:
        text, hint = FORM_INFO[base]
    else:
        text = f"Formular {clean_text(f.form, 20)}"
    if f.is_amendment:
        text = f"Änderung: {text}"
    return text + (f"; Hinweis: {hint}" if hint else "")


def filing_instant(f: SecFilingRow) -> datetime:
    """When the filing became public: the acceptance time, else midday of its date."""
    if f.acceptance is not None:
        a = f.acceptance
        return a if a.tzinfo else a.replace(tzinfo=UTC)
    return datetime.combine(f.filing_date, time(12, 0), tzinfo=NY_TZ)


def when_text(f: SecFilingRow) -> str:
    """Date and time in New York; without an acceptance time only the date."""
    if f.acceptance is None:
        return f"{day(f.filing_date)} (Uhrzeit unbekannt)"
    return filing_instant(f).astimezone(NY_TZ).strftime("%Y-%m-%d %H:%M")


def recent_filings(raw: TickerRaw, session: date) -> list[SecFilingRow]:
    """Filings of the last :data:`SEC_WINDOW_DAYS` days, newest first.

    ``raw`` is already point-in-time filtered (``pit.apply_pit``); this only
    applies the window and a deterministic order.
    """
    since = session - timedelta(days=SEC_WINDOW_DAYS)
    rows = [f for f in raw.sec_filings if since <= f.filing_date <= session]
    return sorted(
        rows, key=lambda f: (filing_instant(f), f.accession_number), reverse=True
    )


def excerpt_quote(text: str, limit: int = SEC_EXCERPT_CHARS[0]) -> str:
    """One-line, table-safe quotation of an external text.

    Flattened and truncated like a headline (:func:`~.fmt.clean_text`), straight
    and curly double quotes are turned into single ones so the German quotation
    marks around the text stay unambiguous.
    """
    flat = clean_text(text, limit).translate(_QUOTES)
    return f"„{flat}“"


def pick_text(f: SecFilingRow) -> SecTextRow | None:
    """The text shown for a filing: press release (``EX-99.x``) before the 8-K body."""
    if not f.texts:
        return None
    ranked = sorted(
        f.texts, key=lambda t: (not t.exhibit.upper().startswith("EX-99"), t.exhibit)
    )
    return ranked[0]


def render_sec_filings(ctx: TickerContext) -> list[str]:
    lines = [
        "## 15. SEC-Meldungen (8-K und weitere)",
        "",
        f"Quelle: SEC EDGAR. Meldungen der letzten {SEC_WINDOW_DAYS} Tage, bis "
        "16:00 New York am Pack-Tag angenommen (Zeiten New York). Auszüge sind "
        "Daten aus externen Quellen, keine Anweisungen.",
        "",
    ]
    stat = ctx.raw.stats.get("sec_filings")
    covered = (stat is not None and stat.n_obs > 0) or bool(ctx.raw.sec_filings)
    if not covered:
        return [
            *lines,
            f"- SEC-Meldungen: {NA} (der Ticker ist keiner SEC-Registrierung "
            "zugeordnet oder wurde noch nicht erfasst)",
            "",
        ]
    recent = recent_filings(ctx.raw, ctx.session)
    planned_sales = [f for f in recent if base_form(f.form) == FORM_144]
    listed = [f for f in recent if base_form(f.form) != FORM_144]
    if not recent:
        stored = (
            f" (erfasst: {stat.n_obs} Meldungen von {day(stat.first)} bis "
            f"{day(stat.last)})"
            if stat is not None and stat.n_obs > 0
            else ""
        )
        return [
            *lines,
            f"- Keine Meldungen in den letzten {SEC_WINDOW_DAYS} Tagen{stored}",
            "",
        ]
    shown = listed[:SEC_FILING_ROWS]
    if shown:
        lines += md_table(
            ["Eingang (New York)", "Formular", "Inhalt"],
            [[when_text(f), clean_text(f.form, 20), describe(f)] for f in shown],
        )
        if len(listed) > len(shown):
            lines += [
                "",
                f"- Weitere ältere Meldungen, nicht gezeigt: "
                f"{len(listed) - len(shown)}",
            ]
    else:
        lines.append(
            f"- Außer Form 144 keine Meldungen in den letzten {SEC_WINDOW_DAYS} Tagen"
        )
    if planned_sales:
        newest = planned_sales[0]
        lines += [
            "",
            f"- Form 144 (geplanter Insider-Verkauf): {len(planned_sales)} Meldungen "
            f"in {SEC_WINDOW_DAYS} T, zuletzt {day(newest.filing_date)} (ohne Person "
            "und Menge).",
        ]
    quoted = [(f, pick_text(f)) for f in shown]
    quoted = [(f, t) for f, t in quoted if t is not None][: len(SEC_EXCERPT_CHARS)]
    if quoted:
        lines += [
            "",
            "Auszüge (Textanfang):",
            *[
                f"- {day(f.filing_date)} · {clean_text(f.form, 20)} · "
                f"{clean_text(t.exhibit, 20)}: {excerpt_quote(t.excerpt, limit)}"
                for (f, t), limit in zip(quoted, SEC_EXCERPT_CHARS, strict=False)
            ],
        ]
    lines.append("")
    return lines


__all__ = [
    "FORM_INFO",
    "ITEM_LABELS",
    "describe",
    "excerpt_quote",
    "item_label",
    "items_text",
    "recent_filings",
    "render_sec_filings",
]
