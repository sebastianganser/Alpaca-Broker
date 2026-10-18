# Plan: Alpaca-Broker strategieneutral machen

**Stand:** 17. Oktober 2026 · **Status:** freigegeben am 17.10.2026 (Entscheidungen siehe Abschnitt 6) – Umsetzung läuft
**Anlass:** Klarstellung von Sebastian am 17.10.: Alpaca-Broker sammelt Daten und soll Ticker identifizieren, deren Kurse in den nächsten Tagen bis Monaten voraussichtlich **steigen** (später auch **fallen**). Die Daten bekommt Claude über den Context Pack und bewertet die Ticker wie ein Wall-Street-Broker. Strategie, Ein-/Ausstiegsregeln und das eigentliche Trading übernimmt später ein **eigener Bot**, nicht Alpaca-Broker.

Voraussetzung: Migration 038 (Rückbau des Trailing-Labels) ist deployt. Neue Migrationen dieses Plans heißen daher 039 und 040.

---

## 1. Inventar: wo die feste Trade-Regel (+1 % / −2 % / 14 Tage) heute steckt

API und Frontend sind nicht betroffen (das Frontend nutzt nur `return_1d..60d`).

| Ort | Was | Konsumenten |
|---|---|---|
| DB `feature_snapshots` (Mig. 033) | `return_barrier_14d`, `barrier_outcome`, `barrier_ambiguous` | `target_backfill`, `feature_report`, `stage2_eval`, Analyse-Skripte |
| DB `stage2_reviews` / `stage2_decisions` (Mig. 035) | Aktionen `buy`/`no_entry`, `limit_price`/`target_pct`/`stop_pct` – **beide Tabellen leer** (keine `decisions.yaml` eingelesen) | `derived/stage2_decisions.py`, `analysis/stage2_eval.py` |
| `analysis/barrier.py` | `MAIN_BARRIER`, Simulation; enthält auch das generische `block_bootstrap_diff` | `target_backfill`, `hit_model`, Backtest |
| `derived/target_backfill.py` | Barrier-Label-Berechnung | Nightly Chain |
| `derived/context_pack_generator.py` | ATR-Filter 1–3 %, Earnings-Sperre 21 Tage, „Risiko-Vorfilter“, Texte „+1 % / −2 % / 14 Handelstage“, `decisions_template` (`buy`/`no_entry`), Top-5 nach handgesetzten `PRELIMINARY_WEIGHTS`. Am 16.10. blieben von 647 Tickern nur 63 übrig (488 wegen Earnings, 276 wegen ATR > 3 %) | Claude-Skill über die Share |
| `derived/stage2_decisions.py`, `analysis/stage2_eval.py` | Parser, Barrier-Auswertung, `stage2_auswertung.md` | Chain-Schritt `stage2_review` |
| `analysis/feature_report.py` | `BARRIER_TARGET`, `H14_SECONDARY_TARGET='return_barrier_14d'` | Monatsanalyse |
| `analysis/hit_model.py`, `analysis/event_study.py`, Skripte `backtest_barrier.py`, `walkforward_hit_model.py`, `event_study.py` | Barrier-Konstanten und -Labels | nur manuell |
| Docs/Agents | Konzept 2026-10-06, `STAGE2_DECISIONS.md`, DECISIONS_*, DATABASE, ARCHITECTURE, SCHEDULER, ROADMAP (K5, F1, Sprint 10–12), INDEX, README, CLAUDE.md, log-checker | – |

Unberührt bleiben die neutralen Features (`st_*`, `atr_14_pct`, `earnings_days_until` …), `FEATURE_VERSION` (sonst kippt die H14-Registrierung) und die Datensammlung.

## 2. Zieldesign Context Pack v2

**Ticker-Datei** (ca. 6 KB, `pack_format: 2`): jeder Wert als Rohwert + Universums-Perzentil (wo sinnvoll Sektor-Perzentil) + Alter der Quelle.

1. Kopf: Kurs, Sektor, Market Cap, ADV, Index-Mitgliedschaft (als Fakt), Datenabdeckung je Gruppe
2. Trend/Momentum: Returns 1–252 Tage, Abstand SMA50/200 und 52-Wochen-Hoch, RSI, MACD, ATR % (nur Risikoinfo), relative Stärke zu SPY und Sektor
3. Bewertung: KGV, Forward-KGV, KUV, KBV, EV/EBITDA, Dividende – relativ zum Sektor
4. Fundamentals: Wachstum, Margen, ROE, Verschuldung, Beta
5. Schätzungen/Revisionen: Konsens, Revisionen 7–90 Tage, letzte Rating-Aktionen mit Kursziel
6. Earnings: nächster Termin **als Information statt Ausschluss**, letzte 4 Quartale, SUE
7. Smart Money (Käufe **und** Verkäufe): Insider (letzte Trades mit Name/Titel), 13F, ARK, Politiker
8. Positionierung: Short Interest (Days-to-Cover), Short Volume, Optionen-IV
9. News/Sentiment: FinBERT, letzte Schlagzeilen
10. Sektor/Relativ, 11. Liquidität, 12. Datenqualität/Frische, 13. Marktregime (im Überblick)

**Überblick:** `00_uebersicht.md` (Regime, Abdeckung, Dateiliste) + `screen.csv` mit allen Tickern, die das Qualitäts-Gate bestehen, damit Claude selbst filtern kann.

**Kandidatenauswahl ohne Trade-Regel** – ein Qualitäts-Gate (Liquidität, ≥ 60 reale Kurstage, aktiv, ≥ 50 % der Datengruppen befüllt) ersetzt den ATR/Earnings-Filter:

| Option | Pro | Contra |
|---|---|---|
| A Alle Gate-Ticker als Datei (~550–600) | kein Auswahl-Bias | ~4 MB/Tag, für Claude nicht lesbar |
| B Neutraler Screen (Shortlist nach Evidenzbreite, beide Richtungen) | lesbar, symmetrisch | Schwellen ungeprüft |
| C Score-Top-K (heute) | existiert | Walk-forward ≈ Zufall, nur Long |
| **D Hybrid (Empfehlung)** | `screen.csv` für alle; Volldateien für 10 bullische + 10 bärische (anfangs aus) + 5 Zufalls-Kontrollen + Wunsch-Ticker; Dateien alphabetisch, Gruppe nur in der DB → kein Ranking-Anchoring, Screen-Effekt und Claude-Effekt trennbar | etwas mehr Code |

**Screen:** je Datengruppe der Mittelwert der Querschnitts-Perzentile mit Literatur-Vorzeichen (bullisch ≥ 80., bärisch ≤ 20. Perzentil), dazu Ereignisse (Insider-Cluster, ARK-Zukauf, Upgrades, positive Revisionen, neue 13F-Positionen); `evidence_breadth` = bullische minus bärische Gruppen. Vor dem Go-live wird der Screen rückwirkend auf ~1.060 Handelstagen gegen die 20-/60-Tage-Rendite geprüft (`screen_backtest.py`); die handgesetzten Gewichte entfallen.

**Was Claude ausgibt – `forecasts.yaml` (Schema `forecasts/v1`) statt `decisions.yaml`:**

```yaml
schema: forecasts/v1
session: 2026-10-16
decided_at: 2026-10-17T09:40:00+02:00
forecasts:
  - ticker: NVDA
    horizon_days: 60          # 20 | 60 (5 optional)
    direction: up             # up | down | neutral
    confidence: 0.62          # Wahrscheinlichkeit, dass die Richtung stimmt
    conviction: 4             # 1–5
    expected_pct: 4.5         # optional
    drivers: [estimates, insider]
    reason: "..."
    risks: "..."
```

Felder wie Limit, Kursziel, Stop oder Positionsgröße lehnt der Parser ab – das ist Sache des Bots.

**Bewertung** (`analysis/forecast_eval.py`): realisierte 20-/60-Tage-Rendite – absolut, gegenüber dem Universums-Mittel und gegenüber SPY; Up-minus-Down-Differenz mit Batch-Jackknife-Intervall (der Datumsblock-Bootstrap war in der Simulation zu eng, siehe FORECASTS.md §6), Auswahl-Trefferquote gegen die Basisrate, Rang-IC (Konviktion × Richtung), Kalibrierung, Aufschlüsselung nach Treibern, Claude vs. Shortlist vs. Zufalls-Kontrolle. Das Urteil wird vorab registriert (wie H14; am 18.10. mit **300 Sitzungen** für das entscheidende 20-Tage-Kriterium, siehe Abschnitt 6). Beschreibend belastbar: ~200 Handelstage für 20 Tage (≈ 10 Monate), ~600 für 60 Tage (≈ 2,4 Jahre); davor nur beschreibend. Der Screen selbst ist sofort rückwirkend prüfbar.

## 3. Barrier-Label und Stage-2-Tabellen

**Empfehlung: entfernen statt stilllegen** (gleiches Vorgehen wie beim Trailing-Label):
- **039 (additiv):** neue Tabellen `forecast_reviews`, `forecasts`, `pack_candidates`; die leeren `stage2_*`-Tabellen werden gelöscht.
- **040 (destruktiv):** Barrier-Spalten löschen. Die Werte sind aus `prices_daily` jederzeit neu ableitbar (Auswertung, keine Rohdaten); es gibt aber keine Backups. Vorher Git-Tag `pre-neutral`.
- Angewendete Migrationen werden nicht editiert; Test auf frischer DB inkl. `alembic check`.

## 4. Analysen und Skripte

| Datei | Entscheidung |
|---|---|
| `backtest_barrier.py`, `walkforward_hit_model.py`, `analysis/hit_model.py` | ins Archiv; Berichte in `docs/reports/` bleiben; Ersatz: `screen_backtest.py` und die Monatsanalyse |
| Event-Study | umbauen auf 20/60 Tage Überschussrendite – erlaubt den Neutest von Rückkäufen (D1) und Index-Aufnahmen (D2) auf passendem Horizont |
| `block_bootstrap_diff` | nach `analysis/stats.py`; danach `analysis/barrier.py` löschen |
| `feature_report.py` | Ziel-Kette 60 → 20 Tage (kommt schon mit 038); `H14_SECONDARY_TARGET` auf `return_60d` (nur Info) – berührt die Vorab-Registrierung, daher Freigabe nötig |
| `sprint10_readiness.py` | neutral benennen („Forecast Readiness“), Logik bleibt |

## 5. Reihenfolge und Aufwand

| # | Schritt | Aufwand |
|---|---|---|
| 0 | Migration 038 (Trailing-Rückbau) committen und deployen | läuft |
| 1 | Entscheidung dokumentieren, `docs/FORECASTS.md` (Schema + vorab registrierte Bewertung) ersetzt `STAGE2_DECISIONS.md`, Konzept 2026-10-06 als historisch markieren | 0,5 d |
| 2 | `forecast_eval.py`, `screen_backtest.py`, Event-Study-Umbau, Tests | 2 d |
| 3 | Context Pack v2: Gate, Screen, Abschnitte, `screen.csv`, `pack_candidates`, Tests; optional Pack-Aufbewahrung begrenzen | 3–4 d |
| 4 | Forecast-Parser/Ingest, Chain-Schritt `stage2_review` → `forecast_review` (Jobzahl bleibt 26), Migration 039 | 2 d |
| 5 | Deploy 1; Skill-Prompt `aktien-analyse` anpassen (liegt bei Sebastian, außerhalb des Repos); Probelauf mit einem Tag | 0,5 d |
| 6 | Barrier/Hit-Model/Backtest-Rückbau, Migration 040, Test auf frischer DB | 1,5 d |
| 7 | Deploy 2, Doku-Durchgang (Mission, ROADMAP: Sprint 11/12 wandern zum Bot) | 0,5 d |

Gesamt ca. 10–12 Agenten-Tage.

**Risiken:** Pack und Skill müssen gleichzeitig umgestellt werden (harter Schnitt `pack_format: 2`); Claude-Kontextbudget (~25 Dateien × 2k Token); die bärische Seite hat weniger Daten (keine Leihgebühren, Insider-Verkäufe nur in den Rohtabellen); junge Quellen (Schätzungen, IV, Sentiment seit Aug. 2026) haben instabile Perzentile.

**Schnittstelle für den späteren Bot** (Tabellen jetzt, API nach ~1 Monat Forecasts): append-only `forecasts` (Sitzung, Zeitpunkt, Horizont, Richtung, Wahrscheinlichkeit, Konviktion, Treiber), View `forecast_outcomes` (Forecast + realisierte Renditen), `GET /api/v1/forecasts`, Risikokontext je Ticker (Kurs, ATR, ADV, Earnings-Termin, Sektor), Kalibrierungskennzahlen, Feature-Export. Stops, Ziele, Trailing, Positionsgröße und Orders bleiben im Bot.

## 6. Freigaben durch Sebastian

**Entschieden am 17.10.2026 (Sebastian):**

- **Prognose-Richtung:** Claude prognostiziert die **absolute** Richtung (Kurs steigt / fällt / neutral); ausgewertet wird **absolut und relativ** (gegenüber Universums-Mittel und SPY).
- **Auswahl:** Variante **D** (Hybrid: `screen.csv` für alle Gate-Ticker, Volldateien für 10 bullische + 10 bärische (anfangs per Config aus) + 5 Zufalls-Kontrollen + Wunsch-Ticker).
- **Barrier-Label:** wird **gelöscht** (Migration 040), ebenso die leeren Stage-2-Tabellen.
- **H14-Sekundärziel:** keine Antwort → H14 bleibt unverändert; nach dem Löschen der Barrier-Spalte entfällt die Info-Zeile (vom Code abgefangen), die Vorab-Registrierung bleibt unberührt.
- **Entscheidender Test (17.10.):** das **System** (Claudes Prognosen gegen das Gate-Universum, `decisive_mode = system`); der Mehrwert gegenüber der Shortlist wird zusätzlich berichtet; die rohe absolute Trefferquote wird nur berichtet; jede Änderung von `forecaster_version` startet eine neue eingefrorene Stichprobe.

**Entschieden am 18.10.2026 (Sebastian):**

- **Entscheidungskriterium vorab registriert mit 300 Sitzungen** (statt der vorgeschlagenen 200): Horizont 20, Modus `system`, ≥ 300 Sitzungen mit ≥ 100 rechtzeitigen `up`- UND ≥ 100 `down`-Prognosen **einer** `forecaster_version`, Einfrieren allein aus den Prognosen, beide Untergrenzen (relativ R und Auswahl-Trefferquote S) > 0, Batch-Jackknife mit Batches von 2h = 40 Sitzungen und t(g−1) (7 Batches, 6 Freiheitsgrade, 2,447), Karenz 10 Sitzungen, absolute Trefferquote nur beschreibend, Versionswechsel startet die Stichprobe neu. Simulierte Fehlbestehensrate bei 300 Sitzungen 1,2 %, Power 38 % (+1,7 % relativer Vorsprung) bzw. 85 % (+2,9 %). Die Konstanten sind wie bei H14 fix und dürfen nicht mehr geändert werden ([FORECASTS.md §6](FORECASTS.md), Tripwire-Test `test_registered_criterion_constants`); die schwächere Schwelle der beschreibenden Belastbarkeits-Kennzeichnungen (10 × Horizont = 200 Sitzungen bei 20 Tagen) bleibt unverändert und ist nicht die Stichprobengröße des Kriteriums.
- **Screen-Gate Liquidität:** bleibt bei **20-Tage-Mittel des Handelsvolumens ≥ 5 Mio. USD** (bestätigt, [SCREEN.md §3](SCREEN.md)).
- **Kürzere Bull-Liste:** an Tagen mit weniger qualifizierten bullischen Tickern bleibt die Liste kürzer – **kein Auffüllen auf 10** (`MIN_BREADTH_BULL` = 1 / `MAX_BREADTH_BEAR` = −1, bestätigt; [SCREEN.md §6](SCREEN.md)). Beide Konstanten sind durch den Tripwire-Test `test_confirmed_gate_liquidity_and_breadth_constants` festgehalten, eine Änderung braucht eine neue `SCREEN_VERSION`. Noch offen: die übrigen Gate-Schwellen (≥ 60 Kurstage, ≥ 50 % der Gruppen mit Daten).

Ursprüngliche Liste der offenen Freigaben:


1. Forecast-Schema und vorab registrierte Bewertungskriterien – insbesondere: soll Claude die **absolute** Richtung prognostizieren (Kurs steigt/fällt) oder die **relative** (besser/schlechter als der Markt)? Empfehlung: absolute Richtung prognostizieren, aber beides auswerten, damit Marktbewegung und echte Auswahlqualität getrennt sichtbar sind.
2. Auswahl-Option D, Anzahl der Volldateien und Zufalls-Kontrollen
3. Migration 040 (löscht abgeleitete Barrier-Werte ohne Backup)
4. Sekundärziel in H14 (`return_barrier_14d` → `return_60d`, nur Information)
5. Anpassung des Skill-Prompts `aktien-analyse`
6. Datenändernde Produktionsläufe wie gewohnt einzeln
