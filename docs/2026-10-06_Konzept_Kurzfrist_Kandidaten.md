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
| **A2** ✅ | Kurzfrist-Kennzahlen aus vorhandenen Tageskursen (§7.1), Migration 034, vektorisierter Backfill, Analyse neu | Ergebnis: Richtung wie Literatur (`st_close_location`, `st_earnings_reaction`), Effekt zu klein, nicht signifikant |
| **B** ✅ ❌ | ML-Modell (logistische Regression, Gradient Boosting) schätzt P(Treffer); Purged Walk-forward (§7.2) | Ergebnis: **Kriterium nicht erfüllt** – Tages-AUC ≈ 0,50, Holdout +0,06 %/Trade n. s. (§7.3) |
| **C** ⏸ | Hyperopt **nur** für wenige Trade-Parameter (Ziel/Stop, Schwelle, Filter), Bestätigung auf Holdout-Zeitraum | Zurückgestellt: ohne Ranking-Signal würde Hyperopt nur Rauschen anpassen |
| **D** ✅ ❌ | Ereignisse als Signal (§7.4): D1 8-K-Rückkäufe, D2 Index-Aufnahmen; dazu Stufe 1 als Risiko-Vorfilter (§7.5) | Ergebnis: **D1 nicht bestanden** (Ø −0,06 %/Trade, n. s.); D2 nach Wirksamkeit schwächer als das Universum → nur Hinweis für Warnfilter (§7.6) |

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

## 7.3 Ergebnis Schritt B (2026-10-06)

Vollständiger Bericht: [reports/2026-10-06_hit_model_walkforward.md](reports/2026-10-06_hit_model_walkforward.md). Daten: 515k Snapshots, 93 Eingaben, Labels bis 2026-09-15.

| | Entwicklung (2023-Q3 … 2024-Q4) | Holdout (2025-Q1 … 2026-Q3) |
|---|---|---|
| Tages-AUC Logit / GBM | 0,497 / 0,495 | 0,503 / 0,502 |
| Gewählte Regel | GBM, max. 1/Tag, P ≥ 0,65 | (unverändert angewendet) |
| Ø Netto/Trade Auswahl | +0,144 % | +0,062 % (konservativ −0,039 %) |
| Ø Netto/Trade Universum | +0,016 % | −0,012 % |
| Differenz (95 %-KI) | – | +0,075 % [−0,047 … +0,175] n. s. |
| Trefferquote | – | 69,9 % (Break-even 68,3 %) |

- **Urteil: nicht bestanden.** Die Modelle können Aktien am selben Tag nicht besser als Zufall ordnen (AUC ≈ 0,5); die Kalibrierung ist flach (Trefferquote 65–70 % in allen P-Dezilen). Quartale gemischt (2025-Q3 −0,37 %, 2026-Q1 −0,14 %).
- **Transparenz:** Ein früherer Testlauf (nur Logit, verkleinerte Trainingsmenge) hat den Holdout bereits gesehen und war ebenfalls negativ; die Auswahlregel wurde danach nicht verändert.
- **Folge:** Schritt C (Hyperopt) zurückgestellt. Das Walk-forward-Skript bleibt als **Referenzmessung** für jede neue Datenquelle (Schritt D) und wird erneut ausgeführt, wenn die kurzen Alternativdaten-Historien (Optionen-IV, Fundamentaldaten, ARK) gewachsen sind. Ein neuer Holdout ist dann ab 2026-Q4 zu verwenden.

## 7.4 Schritt D – Ereignisse als Signal (Event-Studien)

Statt weiterer Querschnitts-Kennzahlen werden **seltene, datierbare Ereignisse** geprüft. Ein Ereignis ist nur an wenigen Tagen für wenige Aktien aktiv – genau passend zu „0–5 Kandidaten, oft keine“. Festgelegt **vor** Sicht auf die Ergebnisse (2026-10-06):

**Gemeinsame Messung** (Skript `scripts/analysis/event_study.py`, nur lesend):
- Signaltag *d* = erster Handelstag **≥** Ereignisdatum. Trade = vorhandenes Barrier-Label des Snapshots (Ticker, *d*): Einstieg `open(d+1)`, +1 % / −2 % / 14 Handelstage, 0,05 % Kosten.
- Vergleich mit dem Universum **am selben Tag** (Differenz je Ereignis = Ereignis-Trade − Ø Universum an *d*), Datumsblock-Bootstrap (95 %-KI), Trefferquote gegen 68,3 % Break-even, OHLC-Regel und konservative Regel.
- Zusätzlich berichtet (nicht entscheidend): Aufteilung nach Jahr, verspäteter Einstieg (*d*+1 … *d*+5, d. h. wie lange ein Ereignis als Signal gilt).

**D1 – Aktienrückkauf-Ankündigungen (8-K)** – Hauptprüfung
- **Quelle:** SEC EDGAR Volltextsuche (kostenlos, ab 2001), Formular 8-K, Suchphrasen „share repurchase program“, „stock repurchase program“, „share buyback program“, „repurchase authorization“; Treffer per CIK auf Universe-Ticker abgebildet.
- **Klassifizierung:** Dokument laden; Ereignis nur, wenn ein Satz eine **neue oder erhöhte Genehmigung** beschreibt (Vorstand *authorized/approved* + *repurchase/buyback* + neu/zusätzlich/Betrag). Reine Vollzugsmeldungen („repurchased 1.2 million shares during the quarter“) zählen nicht. Betrag in USD, falls lesbar.
- **Zeitpunkt:** Ereignisdatum = Einreichungsdatum des 8-K (konservativ – die Pressemitteilung kommt oft früher).
- **Speicherung:** Für die Studie nur als CSV (`scripts/analysis/collect_buyback_events.py`, liest das Universe, schreibt nichts in die DB; Stichprobe zur manuellen Prüfung der Klassifizierung). Tabelle `signals.corporate_events` (Migration 035) und täglicher Collector erst, **wenn bestanden**.
- **Erfolgskriterium:** mindestens 150 Ereignisse, Ø Netto/Trade > 0 **und** 95 %-KI der Differenz zum Universum > 0 (OHLC-Regel).
- **Getrennt berichtet:** mit/ohne gleichzeitige Quartalszahlen (Item 2.02 im selben 8-K).
- **Wenn bestanden:** Kennzahlen `ev_buyback_*` in `feature_snapshots`, Hinweis im Context Pack, Bestätigung auf neuen Daten ab 2026-Q4.

**D2 – Index-Aufnahmen** – explorativ, vorhandene Daten
- **Quelle:** `index_membership` (Wikipedia-Änderungen, S&P 500 + Nasdaq 100), Aufnahmen seit 07/2022: 73 + 44. Nur **Wirksamkeitsdatum** vorhanden, das Ankündigungsdatum fehlt.
- **Test zweiseitig:** Laut Literatur ist der Ankündigungseffekt weitgehend verschwunden und nach Wirksamkeit droht Rückgang → Ergebnis kann auch ein **Warnfilter** sein.
- Wegen geringer Fallzahl nur Hinweis, kein Erfolgskriterium.

**D3 – Reddit-Hype** – zurückgestellt: Die freie Quelle (ApeWisdom) liefert nur den aktuellen Stand, keine Historie → rückwirkend nicht prüfbar.

## 7.5 Stufe 1 als Risiko-Vorfilter (ab 2026-10-06)

Solange kein Modell den Walk-forward besteht, liefert Alpaca-Broker **handelbare** statt „vorhergesagter“ Kandidaten. Die eigentliche Auswahl trifft der Claude-Broker in Stufe 2.

| Regel | Wert | Grund |
|---|---|---|
| Liquidität | `dollar_volume_20d` > 0 (wie bisher) | Universe besteht aus Large Caps |
| Schwankung passt zu Ziel/Stop | `atr_14_pct` zwischen 1 % und 3 % | Unter 1 %: +1 % kaum in 14 Tagen erreichbar; über 3 %: −2 %-Stop wird zufällig gerissen |
| Keine Quartalszahlen im Haltefenster | `earnings_days_until` unbekannt, < 0 oder > 21 Kalendertage | Kurslücken über den Stop |

- **Reihenfolge** der geeigneten Aktien weiter nach dem vorläufigen Score – im Context Pack deutlich als **nicht validiert** gekennzeichnet; zusätzlich Anzahl geeigneter Aktien.
- **„Keine Kandidaten“**, wenn keine Aktie die Regeln erfüllt.
- Kein Marktfilter (SPY/VIX): brachte im Backtest K1 keine Verbesserung.
- Gleiche Filterdefinition wie im Backtest K1 (`backtest_barrier.py`), dort: konservativ +0,05 % signifikant, nach OHLC-Regel n. s. → Wirkung vor allem weniger Stop-Lücken, kein belegter Vorsprung.

## 7.6 Ergebnis Schritt D (2026-10-06)

**D1 – 8-K-Rückkauf-Genehmigungen:** [Bericht](reports/2026-10-06_event_study_buyback.md), [Ereignisliste](reports/2026-10-06_buyback_events.csv), [Audit-Stichprobe](reports/2026-10-06_buyback_audit.md)

- **Datenbasis:** EDGAR-Volltextsuche 06/2022–10/2026: 21.860 Dokument-Treffer, davon 5.145 von Universe-Firmen (4.456 Filings, 758 von 838 Tickern per CIK abgebildet) → 815 Ereignisse, nach Entdoppelung (5 Kalendertage) 798, mit Trade-Label 648 an 404 Tagen.
- **Klassifizierung:** Stichprobe von 40 Positiven manuell geprüft, **≈ 90 % korrekt** (36/40; Fehler: wiederholte ältere Genehmigungen in Quartalsmeldungen). Recall nicht gemessen.
- **Transparenz:** Die Regeln des Klassifizierers wurden an Text-Stichproben (Feb 2024, Apr/Mai 2025, erster Volllauf) nachgeschärft – **nur am Text, nie an Renditen**. Stand des Klassifizierers vor der Rendite-Auswertung eingecheckt (Commit `5c10690`).

| | Ereignisse | Trefferquote | Ø Netto | Ø Universum | Differenz (95 %-KI) | Differenz konservativ |
|---|---|---|---|---|---|---|
| **Alle** | 648 | 66,2 % | −0,063 % | +0,005 % | −0,069 % [−0,210 … +0,048] n. s. | −0,203 % [−0,360 … −0,059] |
| mit Quartalszahlen | 431 | 65,5 % | −0,085 % | +0,010 % | −0,094 % [−0,249 … +0,020] n. s. | −0,288 % (signifikant negativ) |
| ohne Quartalszahlen | 217 | 68,1 % | −0,006 % | +0,009 % | −0,015 % [−0,296 … +0,195] n. s. | −0,072 % n. s. |

- **Urteil: nicht bestanden** (Ø Netto < 0, KI der Differenz schließt 0 ein). Kein Jahr klar positiv (2022 +0,14 %, 2023–2026 negativ); verspäteter Einstieg (*d*+1 … *d*+5) ebenfalls n. s.
- **Deutung:** Die Rückkauf-Drift der Literatur wirkt über Monate, nicht über 14 Tage mit +1 %-Ziel. Mit Quartalszahlen fällt der Einstieg in eine Phase hoher Schwankung → mehr Stop-Treffer (konservativ signifikant schlechter).
- **Folge:** Keine Tabelle `corporate_events`, kein täglicher Collector, keine `ev_buyback_*`-Kennzahlen. Werkzeuge (`collectors/buyback_events.py`, `analysis/event_study.py`) bleiben für spätere Prüfungen.

**D2 – Index-Aufnahmen (explorativ):** [Bericht](reports/2026-10-06_event_study_index_add.md)

- 117 Aufnahmen, 108 mit Label an 53 Tagen. Einstieg nach dem Wirksamkeitsdatum: Ø Netto −0,27 % vs. Universum +0,05 %, Differenz −0,32 % (KI [−0,48 … −0,26] mit Block 20; [−0,63 … −0,10] mit Block 3). Nasdaq-100 −0,56 %, S&P 500 −0,12 % (n. s. mit Block 3); ab *d*+1 n. s.
- **Deutung:** passt zur Literatur (Schwäche nach Aufnahme). Kleine Fallzahl, Ergebnis nach Sicht festgestellt → **nur Hinweis**: ein Warnhinweis „frisch in Index aufgenommen“ für Stufe 2 ist plausibel, aber nicht belegt.

## 7.7 Vorwärtstest Stufe 2 + Index-Warnhinweis (ab 2026-10-07)

Bisher hat kein Baustein von Stufe 1 einen Vorsprung gezeigt (§7.3, §7.6). Ungetestet ist nur noch **Stufe 2** – der Claude-Broker mit Live-Daten. Sie wird ab jetzt auf neuen Daten gemessen (echter Vorwärtstest, kein Rückblick möglich). Festgelegt **vor** der ersten Entscheidung (2026-10-07):

**Rückkanal:** Der Skill schreibt pro Pack-Tag eine Datei `decisions.yaml` in den Pack-Ordner (`context_packs/<Datum>/`). Format und Anleitung: [STAGE2_DECISIONS.md](STAGE2_DECISIONS.md).
- Je geprüftem Ticker eine Zeile mit `action: buy` oder `action: no_entry` (+ kurzer Grund). Leere Liste = geprüft, kein Einstieg.
- Keine Datei = Tag nicht bewertet (zählt nicht als „keine Kandidaten“).

**Einlesen:** Nachtlauf-Schritt `stage2_review` (vor dem Context Pack) liest die Dateien der letzten 45 Tage, prüft das Format und speichert sie in `signals.stage2_reviews` / `signals.stage2_decisions` (Migration 035). Geänderte Dateien ersetzen den Stand des Tages; fehlerhafte Dateien werden gemeldet, nicht übernommen.

**Bewertung** (gleiche Trade-Regel wie §3, keine neuen Parameter):
- Trade = vorhandenes Barrier-Label des Snapshots (Ticker, Pack-Tag *d*): Einstieg `open(d+1)`, +1 % / −2 % / 14 Handelstage, 0,05 % Kosten. Eigene Ziel-/Stop-Angaben des Skills werden nur gespeichert, nicht bewertet.
- **Rechtzeitig** = Entscheidung (`decided_at` und Dateizeit) vor der Eröffnung von *d*+1 (09:30 New York). Verspätete Tage werden getrennt berichtet und zählen nicht zum Kriterium (sonst Rückschau-Vorteil).
- Vergleich am selben Tag mit (a) allen nach dem Vorfilter geeigneten Aktien (§7.5) und (b) dem Universum; zusätzlich `buy` gegen `no_entry` der geprüften Kandidaten.
- **Erfolgskriterium (Hauptprüfung):** frühestens bei **≥ 100 abgeschlossenen, rechtzeitigen `buy`-Trades**: Ø Netto/Trade > 0 **und** 95 %-KI (Datumsblock-Bootstrap) der Differenz zu (a) > 0 (OHLC-Regel). Vorher nur laufende Zahlen, ausdrücklich **vorläufig**, ohne Urteil.
- Bei ~1 Kauf pro Tag ist das Kriterium nach etwa 5–6 Monaten prüfbar.

**Ausgabe:** Der Nachtlauf schreibt `context_packs/stage2_auswertung.md` (laufender Stand) – lesbar für dich und für den Skill.

**Index-Warnhinweis (Hinweis aus D2, nicht belegt):** Context Pack markiert Aktien, deren Aufnahme in S&P 500 oder Nasdaq-100 (`index_membership`, ohne Startbestand) **höchstens 7 Kalendertage zurückliegt oder bevorsteht**: Frontmatter `index_added`, Warnzeile im Kandidaten-Dokument, Liste in der Übersicht. Kein Ausschluss, nur Information für Stufe 2. Einträge aus dem monatlichen `index_sync` tragen das Abgleichsdatum statt des Wirksamkeitsdatums und werden so gekennzeichnet („erkannt …, genaues Datum unbekannt“). D2 ist davon nicht betroffen (die 3 `index_sync`-Einträge vom 2026-10-01 hatten noch kein Label).

## 8. Risiken und Grenzen

- Nur Tagesdaten → Reihenfolge Ziel/Stop innerhalb eines Tages unbekannt (konservativ gelöst).
- Kurze Historie der Alternativdaten → frühe Ergebnisse dafür unsicher.
- Überanpassung an das Testraster; Ergebnisse auf späteren Daten bestätigen (Walk-forward).
- `universe.sector` ist nicht point-in-time (bekannte Grenze).
