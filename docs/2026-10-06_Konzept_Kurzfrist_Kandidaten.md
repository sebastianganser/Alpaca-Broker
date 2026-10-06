# Konzept – Kurzfrist-Kandidaten (1–14 Tage, Gewinnmitnahme 0,5–1 %)

**Stand:** 6. Oktober 2026
**Gegenstand:** Ausrichtung der täglichen Kandidatenauswahl auf das tatsächliche Handelsziel,
Validierung per Rückblick-Test (Barrier-Backtest), Regeln für „heute keine Kandidaten“
**Bezug:** [ROADMAP.md](ROADMAP.md) Sprint 9.5c Phase 2 (R1/F1), [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)

> **Hinweis:** Technisches Konzept, keine Anlageberatung. Aussagen zu Marktanomalien verweisen
> auf die Literatur und sind keine Renditeversprechen.

---

## 1. Zweck von Alpaca-Broker

- Pro Handelstag **0 bis 5 Long-Kandidaten** aus dem Alpaca-handelbaren Universe nennen.
- **Keine Kandidaten** ist ein gültiges Ergebnis, wenn die Datenlage keinen Vorteil zeigt.
- **Kurzfristig:** Haltedauer 1–14 Handelstage, Ziel nach Kosten **+0,5 bis +1 %** pro Trade.
- **Zweistufig:** Alpaca-Broker liefert die Vorauswahl (Context Pack). Die Tiefenprüfung mit
  Live-Daten übernimmt ein Claude-Skill („Wall-Street-Broker“).
- Nur Paper-Trading.

## 2. Ausgangslage (Befund 06.10.2026)

| Punkt | Stand |
|---|---|
| Auswahl | `compute_preliminary_scores` – 10 Features mit **handgesetzten, nicht validierten** Gewichten |
| Anzahl | Immer Top 5 – keine Mindestqualität, kein „keine Kandidaten“ |
| Feature-Analyse | 527k Snapshots: **kein Feature mit belastbarem, signifikantem IC**; bestes `atr_14_pct` (IC 0,036, p = 0,16) misst Volatilität, nicht Richtung |
| Zielgröße der Analyse | Rendite nach festen Horizonten (1/5/20/60 Tage) – passt **nicht** zum Trade (Gewinnziel vor Stop) |
| Datenhistorie | Preise/TA seit 2021 (ausgewertet); Estimates, Options-IV, Sentiment erst seit wenigen Monaten → ca. 1 unabhängige 20-Tage-Periode pro Monat, belastbar frühestens in ~1 Jahr |

Realistische Erwartung: Professionelle Faktoren erreichen IC 0,02–0,05. Das trägt für breit
gestreute Portfolios, nicht für eine treffsichere Auswahl von 1–5 Einzelwerten. Der größte
Hebel liegt daher in **passender Zielgröße, Filtern und Schwellen**, nicht in mehr Daten.

## 3. Trade-Definition (Barrier-Modell)

| Parameter | Wert / Testraster |
|---|---|
| Einstieg | Eröffnungskurs am Handelstag **nach** dem Signal (`open(d+1)`, kein Lookahead) |
| Gewinnziel *a* | 0,5 % / 0,75 % / 1,0 % |
| Stop *b* | 2× bzw. 3× Gewinnziel (also 1,0–3,0 %) |
| Zeitstopp | max. **14 Handelstage**, dann Verkauf zum Schlusskurs |
| Gleicher Tag Ziel + Stop berührt | zählt als **Stop** (konservativ, nur Tagesdaten vorhanden) |
| Kurslücke über Stop/Ziel | Ausführung zum Eröffnungskurs (realistische Slippage bei Gaps) |
| Kosten | konservativ 0,05 % pro Round-Trip (Spread + SEC/FINRA-Gebühren), konfigurierbar |

**Break-even-Logik:** Ohne Vorhersagekraft gilt näherungsweise

$$P(\text{Ziel vor Stop}) \approx \frac{b}{a+b}$$

– das ist zugleich die Trefferquote, bei der man genau bei null landet. Ein Verhältnis Stop =
2–3× Ziel ist legitim (häufige kleine Gewinne, seltene größere Verluste), erzeugt aber selbst
**keinen** Vorteil. Erst eine Trefferquote über dem Zufallswert (nach Kosten) bringt Gewinn.

| Ziel | Stop | Zufalls-Trefferquote | Ertrag/Trade bei +5 %-Pkt. Vorsprung |
|---|---|---|---|
| 0,5 % | 1,0 % | 66,7 % | ≈ +0,07 % |
| 0,5 % | 1,5 % | 75,0 % | ≈ +0,10 % |
| 1,0 % | 2,0 % | 66,7 % | ≈ +0,15 % |
| 1,0 % | 3,0 % | 75,0 % | ≈ +0,20 % |

Bei 0,5 % Ziel fressen Kosten 5–10 % des Gewinns, bei 1 % etwa die Hälfte davon.

### Unterschiede zu Krypto
1. **Overnight-Gaps:** Aktien handeln nicht 24/7 – ein Stop kann deutlich schlechter ausgeführt
   werden, besonders bei Quartalszahlen.
2. **Volatilität muss zum Stop passen:** Eine Aktie mit 4 % Tagesschwankung reißt einen
   1,5 %-Stop zufällig.

## 4. Bewertung im Rückblick-Test

Für jede Parameterkombination und jeden Signaltag:

- **Kandidaten** (Top 5 des jeweiligen Scores) vs. **alle Universe-Aktien** vs. **Zufallsbasis** b/(a+b)
- Kennzahlen: Trefferquote, Ø Nettoertrag pro Trade, Anteil Zeitstopps, Ø Haltedauer,
  Anzahl Trades, schlechtester Trade
- Aufteilung nach Jahr und Marktregime (SPY über/unter SMA200, VIX-Niveau)
- Unsicherheit: Bootstrap über **Datumsblöcke** (überlappende Trades sind nicht unabhängig)
- Mehrfachtests: 6 Kombinationen → Ergebnis nur als belastbar werten, wenn der Vorsprung
  über mehrere Kombinationen und Jahre stabil ist

## 5. Auswahlregeln (nach Validierung)

1. **Mindestschwelle:** Kandidat nur, wenn die erwartete Trefferquote die Zufallsbasis klar
   übersteigt → sonst „heute keine Kandidaten“.
2. **Marktfilter:** Kein Long-Vorschlag in ungünstigem Regime (SPY-Trend, VIX, Marktbreite –
   Daten vorhanden).
3. **Volatilitätsfilter:** Tagesschwankung (`atr_14_pct`) passend zu Ziel/Stop, z. B. 1–3 %.
4. **Earnings-Ausschluss:** keine Kandidaten mit Quartalszahlen innerhalb der Haltedauer.
5. **Liquidität:** Mindest-Dollarvolumen (bestehend), max. 2 pro Sektor (F1).

## 6. Zusätzliche frei verfügbare Daten (Backlog)

| Effekt (Literatur) | Quelle | Status |
|---|---|---|
| Post-Earnings-Announcement-Drift (PEAD) | Earnings + SUE | vorhanden, wenig genutzt |
| Kurzfrist-Reversal (1–5 Tage Verlierer ohne News) | Preise + News | vorhanden, als Feature ergänzen |
| Pre-Earnings-Drift | Earnings-Termine | vorhanden |
| Revisionen der Analystenschätzungen | Estimates | vorhanden, Historie kurz |
| Insider-Clusterkäufe | Form 4 | vorhanden |
| Aktienrückkauf-Ankündigungen (8-K) | SEC EDGAR (kostenlos) | **neu** |
| Index-Aufnahmen | S&P-Meldungen (kostenlos) | **neu** |
| Retail-Hype (Reddit) | z. B. ApeWisdom-API | **neu**, eher als Warnfilter |

Neue Quellen erst nach Phase 2, damit ihr Beitrag messbar ist.

## 7. Vorgehen

**Hauptkonfiguration (2026-10-06):** Ziel **+1 %**, Stop **−2 %**, max. **14 Handelstage**, Kosten 0,05 % Round-Trip, Mehrdeutigkeit nach OHLC-Regel (`MAIN_BARRIER` in `analysis/barrier.py`).

| Phase | Inhalt | Ergebnis / Kriterium |
|---|---|---|
| **P1** ✅ | Barrier-Backtest-Skript (`scripts/analysis/backtest_barrier.py`) für den **aktuellen** Score | Ergebnis: Ist-Score ≈ Zufall (siehe `LEARNINGS.md`), handgesetzte Gewichte tragen nicht |
| **A** (K2) ✅ | Barrier-Label (`return_barrier_14d`, `barrier_outcome`, `barrier_ambiguous`) in `feature_snapshots` (Migration 033), Zielgröße der Feature-Analyse | Ergebnis: kein robustes Signal in den vorhandenen 74 Kennzahlen (siehe `LEARNINGS.md`) |
| **A2** | Kurzfrist-Kennzahlen aus vorhandenen Tageskursen (§7.1), Migration 034, vektorisierter Backfill, Analyse neu | Bringt eine der neuen Kennzahlen ein belastbares Signal gegenüber dem Stand nach A? |
| **B** (Sprint 10) | ML-Modell (erst LASSO, dann Gradient Boosting) schätzt P(Treffer); Purged Walk-forward (Training 2022–2024, Validierung 2025–2026) | Kandidat nur, wenn P(Treffer) deutlich über Break-even → „keine Kandidaten“ ergibt sich automatisch |
| **C** | Hyperopt **nur** für wenige Trade-Parameter (Ziel/Stop, Schwelle, Filter), Bestätigung auf Holdout-Zeitraum | Stabile Parameter statt Überanpassung |
| **D** | Neue Quellen aus §6 (8-K-Rückkäufe, Index-Aufnahmen, Reddit), jeweils mit Vorher/Nachher-Messung | Nur behalten, was messbar hilft |

> Warum kein Hyperopt auf ~80 Gewichte: bei ~1000 Handelstagen und einem Signal nahe null würde die Optimierung vor allem Rauschen anpassen. Das ML-Modell lernt die Gewichtung mit Regularisierung und wird außerhalb der Trainingszeit geprüft.

## 7.1 Kurzfrist-Kennzahlen (Schritt A2)

Alle aus `prices_daily` (adjustiert, ohne extrapolierte Zeilen), Stand Schlusskurs von Tag d, Fenster in Handelstagen. Präfix `st_` → eigene Gruppe „Short-Term“.

| Spalte | Definition | Hintergrund (Literatur) |
|---|---|---|
| `st_return_1d` | close(d) / close(d−1) − 1 | Kurzfrist-Reversal |
| `st_return_5d` | close(d) / close(d−5) − 1 | Wochen-Reversal (Jegadeesh 1990, Lehmann 1990) |
| `st_gap` | open(d) / close(d−1) − 1 | Gap-Fortsetzung bzw. -Schließung |
| `st_close_location` | (close − low) / (high − low) von d | Schluss nahe Tageshoch/-tief |
| `st_dist_52w_high` | close(d) / max(high der letzten 252 Tage) − 1 | 52-Wochen-Hoch-Effekt (George/Hwang 2004) |
| `st_rsi_2` | RSI über 2 Tage (einfacher Mittelwert, ohne Rekursion) | Überverkauft-Signal kurzfristig |
| `st_bollinger_pctb` | Lage im Bollinger-Band (20 Tage, 2σ) | Mean Reversion |
| `st_signed_volume_shock` | ln(Volumen(d) / Ø Volumen der 20 Vortage) × Vorzeichen der Tagesrendite | Volumen bestätigt Bewegung (Gervais et al. 2001) |
| `st_earnings_reaction` | close(Tag nach Earnings) / close(Tag vor Earnings) − 1, gültig bis 60 Kalendertage nach dem Termin | Post-Earnings-Drift über die Kursreaktion |

Umsetzung: eine gemeinsame vektorisierte Funktion für Tageslauf und Backfill (identische Werte), Backfill nur der neuen Spalten (Minuten statt 7–12 h Komplett-Neuaufbau), Neuberechnung auch nach Split-/Dividenden-Readjustierung.

## 7.2 Schritt B – Trefferwahrscheinlichkeit per Modell (Walk-forward)

Ziel: prüfen, ob die kombinierten Kennzahlen **außerhalb der Trainingszeit** Kandidaten liefern, deren Trades nach Kosten Gewinn bringen. Festgelegt **vor** Sicht auf die Ergebnisse:

- **Label:** Treffer = `barrier_outcome = 1` (Ziel +1 % vor Stop −2 % binnen 14 Handelstagen, OHLC-Regel). Rendite = `return_barrier_14d` (netto). Break-even-Trefferquote ≈ (2 % + 0,05 %) / 3 % ≈ **68,3 %**.
- **Eingaben:** alle Kennzahlen; tickerbezogene als Tagesrang (0–1), marktweite (VIX, Breadth, Makro) und `atr_14_pct` zusätzlich absolut – damit das Modell auch „heute lieber nichts“ lernen kann.
- **Modelle mit fest gewählten Parametern** (kein Tuning): logistische Regression (L2) und Gradient Boosting (sklearn `HistGradientBoostingClassifier`, flache Bäume, starke Regularisierung).
- **Walk-forward:** quartalsweise neu trainiert, wachsendes Trainingsfenster ab 07/2022, Purge 16 Handelstage vor jedem Testquartal (Label-Überlappung).
  - **Entwicklung:** Testquartale 2023-Q3 … 2024-Q4 → Wahl von Modell, Schwelle P(Treffer) und max. Kandidaten (1–5).
  - **Holdout:** Testquartale 2025-Q1 … 2026-Q3 → einmalige Prüfung mit der in der Entwicklung gewählten Regel, keine Änderung danach.
- **Auswahlregel:** pro Tag die höchsten P(Treffer), höchstens k Stück, nur wenn P ≥ Schwelle → sonst **„keine Kandidaten“**.
- **Erfolgskriterium (Holdout):** Ø Netto/Trade > 0 **und** 95 %-KI (Datumsblock-Bootstrap) der Differenz zum Universum > 0. Zusätzlich berichtet: Trefferquote, konservative Variante (mehrdeutige Tage = Stop), Kalibrierung, Tage ohne Kandidaten, Ergebnis je Quartal.
- Code: `analysis/hit_model.py`, Skript `scripts/analysis/walkforward_hit_model.py` (nur lesend).

## 8. Risiken und Grenzen

- Nur Tagesdaten → Reihenfolge Ziel/Stop innerhalb eines Tages unbekannt (konservativ gelöst).
- Kurze Historie der Alternativdaten → frühe Ergebnisse dafür unsicher.
- Überanpassung an das Testraster; Ergebnisse auf späteren Daten bestätigen (Walk-forward).
- `universe.sector` ist nicht point-in-time (bekannte Grenze).
