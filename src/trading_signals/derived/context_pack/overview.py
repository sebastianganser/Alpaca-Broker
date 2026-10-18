"""``00_uebersicht.md`` - market regime, coverage, file list, return channel."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from trading_signals.analysis.forecast_eval import PRIMARY_HORIZON
from trading_signals.analysis.screen import (
    BULL_PERCENTILE,
    GROUP_NAMES,
    MIN_DOLLAR_VOLUME_20D,
    SCREEN_VERSION,
)
from trading_signals.derived.context_pack.constants import (
    FORECASTS_FILE,
    MAX_REQUESTED,
    PACK_FORMAT,
    REPORT_FILE,
    REQUESTED_FILE,
    SCREEN_FILE,
)
from trading_signals.derived.context_pack.fmt import clean_text, md_table
from trading_signals.derived.context_pack.sections.head import GATE_REASON_TEXT
from trading_signals.derived.context_pack.sections.quality import regime_line
from trading_signals.derived.context_pack.template import (
    DRIVER_LIST,
    deadline_text,
    forecasts_template,
    last_seal_text,
)
from trading_signals.derived.forecasts import FORBIDDEN_FIELDS


@dataclass
class FileEntry:
    """One ticker file of the pack (alphabetical list, no selection reason)."""

    ticker: str
    company: str | None
    sector: str | None


@dataclass
class OverviewData:
    """Everything the overview is rendered from."""

    session: date
    n_snapshots: int
    n_gated: int
    #: Gate failures per reason code (a ticker can fail several checks).
    gate_failures: Mapping[str, int]
    #: Share (0-1) of the gate tickers with data, per evidence group.
    group_coverage: Mapping[str, float]
    market: Mapping[str, float | None]
    spy_returns: Mapping[int, float | None]
    files: Sequence[FileEntry]
    #: Requested tickers without a snapshot on the session (no file possible).
    missing_requested: Sequence[str] = ()
    #: Requested tickers beyond the cap (ignored).
    ignored_requested: Sequence[str] = ()
    #: Chart images written next to the ticker files (``TICKER.png``).
    n_charts: int = 0


def _spy_line(spy: Mapping[int, float | None]) -> str:
    parts = []
    for h in (1, 5, 20, 60):
        v = spy.get(h)
        parts.append(f"{h} T {'keine Abdeckung' if v is None else f'{v * 100:+.1f} %'}")
    return "SPY: " + " · ".join(parts)


def render_overview(data: OverviewData) -> str:
    d = data.session
    lines: list[str] = [
        f"# Tagesübersicht — {d.isoformat()}",
        "",
        f"Context Pack v2 (`pack_format: {PACK_FORMAT}`) für die Handelssitzung "
        f"**{d.isoformat()}** (Stand: Börsenschluss; nur Informationen, die bis "
        "dahin öffentlich waren). Keine Anlageberatung.",
        "",
        "## 1. Aufgabe",
        "",
        "Beurteile die Aktien dieses Packs wie ein Wall-Street-Broker und "
        "**prognostiziere die absolute Richtung** (steigt / fällt / neutral) je "
        "Ticker und Horizont: 20 und 60 Handelstage, 5 optional. Es geht nur um "
        "die Prognose - **keine** Einstiegs-, Ausstiegs-, Stop-, Ziel- oder "
        "Größenangaben; Strategie und Handel übernimmt später ein eigener Bot.",
        "",
        f"- `{SCREEN_FILE}`: alle {data.n_gated} Ticker, die das Qualitäts-Gate "
        "bestehen. Du kannst dort selbst filtern und **jeden** dieser Ticker "
        "prognostizieren, auch ohne eigene Datei.",
        "- `TICKER.md`: die ausführlichen Dateien (unten, alphabetisch).",
        f"- `{FORECASTS_FILE}`: dein Ergebnis, Rückkanal siehe Abschnitt 6.",
        "",
        "## 2. Marktregime",
        "",
        regime_line(data.market),
        "",
        _spy_line(data.spy_returns),
        "",
        "## 3. Abdeckung und Qualitäts-Gate",
        "",
        f"- Feature-Snapshots der Sitzung: **{data.n_snapshots}**; Gate bestanden: "
        f"**{data.n_gated}** (= Zeilen in `{SCREEN_FILE}`, Population aller "
        "Perzentile).",
        "- Gate: aktives Universumsmitglied, Handelsvolumen Ø 20 T ≥ "
        f"{MIN_DOLLAR_VOLUME_20D / 1e6:.0f} Mio. USD, ≥ 60 reale Kurstage, ≥ 50 % der "
        "12 Evidenzgruppen mit Daten.",
    ]
    failed = {k: v for k, v in data.gate_failures.items() if v}
    if failed:
        lines += ["", "Ausgeschlossen (ein Ticker kann mehrere Gründe haben):", ""]
        lines += md_table(
            ["Grund", "Ticker"],
            [
                [GATE_REASON_TEXT.get(k, k), v]
                for k, v in sorted(failed.items(), key=lambda kv: -kv[1])
            ],
        )
    lines += ["", "Anteil der Gate-Ticker mit Daten je Evidenzgruppe:", ""]
    cov = [f"{g} {data.group_coverage.get(g, 0.0) * 100:.0f} %" for g in GROUP_NAMES]
    lines += [" · ".join(cov), ""]
    lines += [
        "Junge Quellen (Konsens-Schätzungen, Optionen, News/Sentiment, Short "
        "Volume) haben nur Monate an Historie: ihre Werte und Perzentile sind "
        "weniger verlässlich. In jeder Datei zeigt Abschnitt 12 Alter und "
        "Historie je Quelle.",
        "",
        f"## 4. Ticker-Dateien ({len(data.files)}, alphabetisch)",
        "",
    ]
    if data.files:
        lines += md_table(
            ["Ticker", "Unternehmen", "Sektor"],
            [
                [f.ticker, clean_text(f.company, 40), clean_text(f.sector, 24)]
                for f in sorted(data.files, key=lambda f: f.ticker)
            ],
        )
    else:
        lines.append(
            "Heute keine Ticker-Dateien (zu wenige Gate-Ticker für den Screen)."
        )
    if data.n_charts:
        lines += [
            "",
            "Zu den Ticker-Dateien gehört je ein Chart `TICKER.png` im selben "
            "Ordner (Tageskerzen eines Jahres, SMA50 / SMA200, 52-Wochen-Spanne, "
            "Unterstützung / Widerstand, Volumen); der Text der Datei enthält "
            "dieselben Ebenen.",
        ]
    if data.missing_requested:
        lines += [
            "",
            "Wunsch-Ticker ohne Daten an diesem Tag (keine Datei): "
            + ", ".join(data.missing_requested),
        ]
    if data.ignored_requested:
        lines += [
            "",
            f"Wunsch-Ticker über dem Limit von {MAX_REQUESTED} (ignoriert): "
            + ", ".join(data.ignored_requested),
        ]
    lines += [
        "",
        "## 5. Auswahl der Dateien und Screen",
        "",
        f"Die Dateien stammen aus einem neutralen Screen (`{SCREEN_VERSION}`) "
        "über alle Gate-Ticker, ergänzt um Zufalls-Kontrollen und Wunsch-Ticker "
        f"(`{REQUESTED_FILE}`). **Welche Datei aus welchem Grund dabei ist, wird "
        "bewusst nicht angegeben**, und die Reihenfolge ist alphabetisch: Ein "
        "Rang würde dich ankern. Beurteile jeden Ticker für sich.",
        "",
        "Der Screen zählt, in wie vielen der 12 Evidenzgruppen ("
        + ", ".join(GROUP_NAMES)
        + f") ein Ticker im Vergleich zu den anderen Gate-Tickern im obersten "
        f"(≥ P{BULL_PERCENTILE * 100:.0f}) bzw. untersten Fünftel liegt "
        "(`evidence_breadth`). Die Vorzeichen stammen aus der Finanzliteratur, "
        "nicht aus eigenen Daten. Er ist ein Filter für die Lesbarkeit, keine "
        "Prognose: rückblickend zeigte er keine gesicherte Überrendite, und "
        "Evidenzgruppen sind teils korreliert (z. B. Trend und Reversal).",
        "",
        "Perzentile in `screen.csv` (`p_<gruppe>`, 0-100): Rang des Gruppen-Scores "
        "unter den Gate-Tickern, hoch = mehr bullische Evidenz der Gruppe. "
        "Leer = Gruppe ohne ausreichende Daten (nicht 0). In den Ticker-Dateien "
        "sind P/S dagegen Perzentile des *Rohwerts* (hoch = hoher Wert).",
        "",
        f"## 6. Rückkanal: `{FORECASTS_FILE}`",
        "",
        f"Schreibe das Ergebnis als `{FORECASTS_FILE}` **in diesen Ordner**. "
        f"**Frist: vor der Eröffnung am {deadline_text(d)}**, der Eröffnung der "
        "nächsten Handelssitzung. Ein Zeitgeber versiegelt die Datei (SHA-256) "
        f"um 09:00, 09:15 und 09:25 Uhr New York: Sie muss **spätestens "
        f"{last_seal_text(d)} fertig im Ordner liegen** - nur ein Inhalt, der "
        "vor der Eröffnung versiegelt wurde, zählt als rechtzeitig (`decided_at` "
        "und Dateizeit beweisen nichts). Später geschriebene oder geänderte "
        "Dateien werden gespeichert und getrennt ausgewertet, zählen aber nicht "
        "für das vorab registrierte Kriterium. Danach nichts mehr ändern.",
        "",
        "```yaml",
        *forecasts_template(d),
        "```",
        "",
        "Regeln (Format `forecasts/v1`, Details in `docs/FORECASTS.md`):",
        "",
        "- `session` ist das Datum dieses Packs (Ordnername), nicht das heutige. "
        "`decided_at` mit Zeitzonen-Offset (z. B. `+02:00`; ohne Offset wird die "
        "Datei abgelehnt). `forecaster_version` identifiziert Skill/Prompt "
        "und Modell; nur ändern, wenn sich der Prognostiker wirklich ändert "
        "(eine Änderung startet die vorab registrierte Stichprobe neu, und ein "
        "früherer Name darf nach einer Unterbrechung **nicht** wieder verwendet "
        "werden).",
        "- Eine Prognose je Ticker und Horizont. `up`/`down` = die Rohrendite "
        "`close(d+h) / open(d+1) − 1` ist positiv/negativ; `neutral` = keine "
        "Meinung (ohne `confidence`, `conviction`, `expected_pct`).",
        "- `confidence` = kalibrierte Wahrscheinlichkeit, dass die Richtung stimmt "
        "(0.50 … 0.95); `conviction` 1 … 5 = Stärke der Evidenz, unabhängig davon.",
        f"- `drivers`: bis zu 5 Tags aus {DRIVER_LIST}.",
        "- Verboten (Zeile wird abgelehnt): "
        + ", ".join(f"`{f}`" for f in sorted(FORBIDDEN_FIELDS))
        + ". Unbekannte Felder werden ebenfalls abgelehnt.",
        "- Jeder Ticker des Universums ist erlaubt, auch ohne Datei; ein Ticker "
        "ohne Snapshot an diesem Tag wird abgelehnt.",
        "",
        "Bewertet wird automatisch: realisierte Rendite ab Eröffnung des "
        f"Folgetags nach {PRIMARY_HORIZON} und 60 Handelstagen, absolut und "
        "relativ zum Gate-Universum und zu SPY; dazu Treffer, Kalibrierung und "
        f"Rang-IC. Laufender Stand: `context_packs/{REPORT_FILE}` (eine Ebene "
        "über diesem Ordner).",
        "",
        "---",
        f"*Generiert für die Sitzung {d.isoformat()} · Keine Anlageberatung*",
    ]
    return "\n".join(lines) + "\n"
