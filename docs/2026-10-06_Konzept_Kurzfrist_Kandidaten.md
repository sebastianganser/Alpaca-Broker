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

| Phase | Inhalt | Ergebnis / Kriterium |
|---|---|---|
| **P1** | Barrier-Backtest-Skript (`scripts/analysis/`) für den **aktuellen** Score, Raster aus §3 | Hat der Ist-Score einen Vorsprung? Welche Kombination passt? |
| **P2** | Barrier-Ergebnis als Zielgröße in die Feature-Analyse (statt/zusätzlich zu festen Horizonten) | Welche Features erhöhen die Trefferquote? |
| **P3** | Filter + Mindestschwelle im Context Pack, „keine Kandidaten“-Ausgabe | 0–5 Kandidaten/Tag mit dokumentierter Erwartung |
| **P4** | Neue Quellen aus §6, jeweils mit Vorher/Nachher-Messung | Nur behalten, was messbar hilft |

## 8. Risiken und Grenzen

- Nur Tagesdaten → Reihenfolge Ziel/Stop innerhalb eines Tages unbekannt (konservativ gelöst).
- Kurze Historie der Alternativdaten → frühe Ergebnisse dafür unsicher.
- Überanpassung an das Testraster; Ergebnisse auf späteren Daten bestätigen (Walk-forward).
- `universe.sector` ist nicht point-in-time (bekannte Grenze).
