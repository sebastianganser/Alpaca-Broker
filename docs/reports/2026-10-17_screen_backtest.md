# Screen-Backtest – neutraler Kandidaten-Screen

Erstellt 2026-10-17 · Screen `screen/v1` · 1060 Handelstage (2022-07-28 … 2026-10-16) · 568079 Snapshot-Zeilen

Zielgrößen: `return_h = close(d+h) / open(d+1) − 1` (h = 20, 60). Jeder Handelstag zählt einmal (datumsgewichtet). *Absolut* = Rohrendite, *relativ* = Überschuss gegenüber dem Mittel des Gate-Universums am selben Tag bzw. gegenüber SPY. KI = Batch-Jackknife (Batches von 2 × Horizont Tagen, t-Verteilung), t = Newey-West (Lag h−1); Signifikanz-Marker erst ab 10 × Horizont Tagen mit Daten. Die Gruppen-Vorzeichen stammen aus der Literatur und werden anhand dieses Berichts **nicht** verändert (`docs/SCREEN.md`).

Konfiguration: K bullisch 10, K bärisch 10 (im Backtest immer ausgewertet), Zufallskontrollen 5, Perzentile ≥ 80% / ≤ 20%, Breite ≥ 1 bzw. ≤ -1, Liquidität ≥ 5 Mio. USD, ≥ 60 Kurstage (Proxy: SMA50 vorhanden), ≥ 50% der Gruppen mit Daten.

## Horizont 20 Handelstage

851 Handelstage mit Rendite ≈ 42.5 unabhängige 20-Tage-Perioden (Batch 40 Tage). Belastbar erst ab ≈ 10 unabhängigen Perioden; Signifikanz-Marker erst ab 200 Tagen mit Daten je Kennzahl.

### Absolut und relativ

| Gruppe | Namen/Tag | Ø Rendite | Ø Universum | Überschuss vs. Universum (95 %-KI) | t (NW) | Überschuss vs. SPY (95 %-KI) |
|---|---|---|---|---|---|---|
| Bullisch (Top) | 10.0 | +1.83 % | +1.23 % | +0.60 % [-0.09 % … +1.30 %] n. s. | 2.26 | +0.25 % [-0.69 % … +1.18 %] n. s. |
| Bärisch (Bottom) | 10.0 | +0.78 % | +1.23 % | -0.45 % [-1.71 % … +0.80 %] n. s. | -0.97 | -0.81 % [-2.14 % … +0.52 %] n. s. |
| Zufalls-Kontrollen | 5.0 | +1.10 % | +1.23 % | -0.13 % [-0.47 % … +0.22 %] n. s. | -0.92 | -0.49 % [-1.06 % … +0.08 %] n. s. |
| Universum (Gate) | 366.2 | +1.23 % | +1.23 % | +0.00 % [+0.00 % … +0.00 %] n. s. | – | -0.36 % [-0.88 % … +0.16 %] n. s. |

### Trefferquote gegen die Basisrate

Bullisch/Zufall/Universum: Anteil Rendite > 0; bärisch: Anteil Rendite < 0. Basisrate = derselbe Anteil im Gate-Universum am selben Tag (querschnittlich: das Markt-Timing zählt 0).

| Gruppe | Trefferquote | Basisrate | Differenz (95 %-KI) |
|---|---|---|---|
| Bullisch (Top) | 57.3 % | 54.9 % | +2.44 % [-0.15 % … +5.04 %] n. s. |
| Bärisch (Bottom) | 49.4 % | 45.1 % | +4.33 % [+0.92 % … +7.74 %] ✅ |
| Zufalls-Kontrollen | 54.9 % | 54.8 % | +0.10 % [-1.30 % … +1.51 %] n. s. |
| Universum (Gate) | 54.8 % | 54.8 % | +0.00 % [+0.00 % … +0.00 %] n. s. |

### Spreads (je Tag, Differenz der Gruppenmittel)

| Spread | Tage | Differenz (95 %-KI) | t (NW) |
|---|---|---|---|
| Bullisch minus Bärisch | 850 | +1.06 % [-0.59 % … +2.71 %] n. s. | 1.71 |
| Bullisch minus Zufall | 850 | +0.73 % [+0.01 % … +1.46 %] ✅ | 2.50 |
| Zufall minus Universum | 851 | -0.13 % [-0.47 % … +0.22 %] n. s. | -0.92 |

### Je Jahr (Überschuss vs. Universum; Spread)

| Jahr | Tage | Bullisch (Top) | Bärisch (Bottom) | Zufalls-Kontrollen | Bull minus Bear |
|---|---|---|---|---|---|
| 2023 | 170 | +0.27 % | +0.52 % | +0.02 % | -0.25 % |
| 2024 | 252 | +0.41 % | -1.11 % | -0.36 % | +1.52 % |
| 2025 | 250 | +0.63 % | -1.62 % | +0.05 % | +2.25 % |
| 2026 | 179 | +1.16 % | +1.19 % | -0.20 % | -0.04 % |

### Gruppen-Diagnose (nur beschreibend – Vorzeichen werden nicht angepasst)

Rang-IC des orientierten Scores und Spread oberes minus unteres Extrem-Perzentil. Positiv = das Literatur-Vorzeichen hat gehalten. 12 Gruppen × 2 Horizonte: ohne Mehrfachtest-Korrektur nur Hinweise.

| Gruppe | Abdeckung | Tage | Ø IC | t (NW) | Spread | t (NW) |
|---|---|---|---|---|---|---|
| trend | 100 % | 850 | 0.0124 | 0.60 | +0.24 % | 0.43 |
| reversal | 100 % | 850 | -0.0012 | -0.09 | -0.14 % | -0.38 |
| value | 21 % | 108 | 0.0071 | 0.13 | -1.51 % | -0.69 |
| quality | 21 % | 108 | -0.0128 | -0.65 | -0.94 % | -1.60 |
| revisions | 0 % | 0 | – | – | – | – |
| surprise | 100 % | 850 | 0.0174 | 1.43 | +0.43 % | 1.60 |
| analyst | 22 % | 129 | 0.0243 | 1.86 | +0.78 % | 1.44 |
| insider † | 95 % | 850 | -0.0055 | -0.46 | +0.04 % | 0.13 |
| institutional | 98 % | 850 | 0.0074 | 0.96 | +0.21 % | 0.97 |
| short_interest † | 100 % | 850 | 0.0237 | 2.48 | +0.60 % | 2.43 |
| short_volume | 3 % | 14 | 0.0971 | 10.07 | +2.71 % | 19.25 |
| sentiment | 12 % | 90 | -0.0045 | -0.14 | +0.22 % | 0.20 |
| composite | 100 % | 850 | 0.0287 | 2.22 | +0.55 % | 1.74 |
| evidence_breadth | 100 % | 850 | 0.0279 | 3.07 | – | – |

† in-sample bezüglich der Feature-Analyse vom 2026-10-17: Insider-Käufe und Days-to-Cover waren dort auf denselben Daten bereits aufgefallen; ein gutes Ergebnis ist hier keine unabhängige Bestätigung.

## Horizont 60 Handelstage

811 Handelstage mit Rendite ≈ 13.5 unabhängige 60-Tage-Perioden (Batch 120 Tage). Belastbar erst ab ≈ 10 unabhängigen Perioden; Signifikanz-Marker erst ab 600 Tagen mit Daten je Kennzahl.

### Absolut und relativ

| Gruppe | Namen/Tag | Ø Rendite | Ø Universum | Überschuss vs. Universum (95 %-KI) | t (NW) | Überschuss vs. SPY (95 %-KI) |
|---|---|---|---|---|---|---|
| Bullisch (Top) | 10.0 | +5.41 % | +3.77 % | +1.65 % [+0.03 % … +3.26 %] ✅ | 2.25 | +0.63 % [-1.39 % … +2.66 %] n. s. |
| Bärisch (Bottom) | 10.0 | +1.29 % | +3.77 % | -2.48 % [-5.36 % … +0.40 %] n. s. | -2.62 | -3.49 % [-5.96 % … -1.03 %] ❌ |
| Zufalls-Kontrollen | 5.0 | +3.25 % | +3.76 % | -0.52 % [-1.68 % … +0.64 %] n. s. | -1.57 | -1.54 % [-2.97 % … -0.11 %] ❌ |
| Universum (Gate) | 353.6 | +3.76 % | +3.76 % | +0.00 % [+0.00 % … +0.00 %] n. s. | – | -1.02 % [-1.94 % … -0.11 %] ❌ |

### Trefferquote gegen die Basisrate

Bullisch/Zufall/Universum: Anteil Rendite > 0; bärisch: Anteil Rendite < 0. Basisrate = derselbe Anteil im Gate-Universum am selben Tag (querschnittlich: das Markt-Timing zählt 0).

| Gruppe | Trefferquote | Basisrate | Differenz (95 %-KI) |
|---|---|---|---|
| Bullisch (Top) | 62.0 % | 58.3 % | +3.71 % [-0.34 % … +7.75 %] n. s. |
| Bärisch (Bottom) | 48.4 % | 41.7 % | +6.65 % [-1.08 % … +14.39 %] n. s. |
| Zufalls-Kontrollen | 56.7 % | 58.2 % | -1.54 % [-4.65 % … +1.57 %] n. s. |
| Universum (Gate) | 58.2 % | 58.2 % | +0.00 % [+0.00 % … +0.00 %] n. s. |

### Spreads (je Tag, Differenz der Gruppenmittel)

| Spread | Tage | Differenz (95 %-KI) | t (NW) |
|---|---|---|---|
| Bullisch minus Bärisch | 810 | +4.13 % [+0.14 % … +8.12 %] ✅ | 2.78 |
| Bullisch minus Zufall | 810 | +2.17 % [-0.45 % … +4.79 %] n. s. | 2.58 |
| Zufall minus Universum | 811 | -0.52 % [-1.68 % … +0.64 %] n. s. | -1.57 |

### Je Jahr (Überschuss vs. Universum; Spread)

| Jahr | Tage | Bullisch (Top) | Bärisch (Bottom) | Zufalls-Kontrollen | Bull minus Bear |
|---|---|---|---|---|---|
| 2023 | 170 | +1.94 % | -0.17 % | -0.05 % | +2.11 % |
| 2024 | 252 | +0.95 % | -1.04 % | -0.68 % | +1.99 % |
| 2025 | 250 | +1.16 % | -4.70 % | +0.35 % | +5.86 % |
| 2026 | 139 | +3.44 % | -3.90 % | -2.35 % | +7.35 % |

### Gruppen-Diagnose (nur beschreibend – Vorzeichen werden nicht angepasst)

Rang-IC des orientierten Scores und Spread oberes minus unteres Extrem-Perzentil. Positiv = das Literatur-Vorzeichen hat gehalten. 12 Gruppen × 2 Horizonte: ohne Mehrfachtest-Korrektur nur Hinweise.

| Gruppe | Abdeckung | Tage | Ø IC | t (NW) | Spread | t (NW) |
|---|---|---|---|---|---|---|
| trend | 100 % | 810 | -0.0014 | -0.05 | +0.33 % | 0.25 |
| reversal | 100 % | 810 | 0.0045 | 0.25 | -0.27 % | -0.34 |
| value | 14 % | 68 | 0.1151 | 4.93 | +2.05 % | 1.85 |
| quality | 14 % | 68 | -0.0213 | -1.10 | -3.53 % | -4.02 |
| revisions | 0 % | 0 | – | – | – | – |
| surprise | 100 % | 810 | 0.0084 | 0.55 | +1.07 % | 1.50 |
| analyst | 16 % | 89 | 0.0121 | 1.46 | +1.72 % | 3.05 |
| insider † | 96 % | 810 | 0.0074 | 0.51 | +0.37 % | 0.56 |
| institutional | 99 % | 810 | 0.0061 | 0.47 | +0.09 % | 0.15 |
| short_interest † | 100 % | 810 | 0.0363 | 2.70 | +1.69 % | 2.79 |
| short_volume | 0 % | 0 | – | – | – | – |
| sentiment | 7 % | 50 | -0.0369 | -1.54 | +0.18 % | 0.10 |
| composite | 100 % | 810 | 0.0287 | 2.08 | +1.34 % | 1.94 |
| evidence_breadth | 100 % | 810 | 0.0287 | 2.30 | – | – |

† in-sample bezüglich der Feature-Analyse vom 2026-10-17: Insider-Käufe und Days-to-Cover waren dort auf denselben Daten bereits aufgefallen; ein gutes Ergebnis ist hier keine unabhängige Bestätigung.

## Abdeckung

Pro Jahr (Mittel je Handelstag):

| Jahr | Tage | Zeilen | Gate ok | Ø bullisch | Ø bärisch | Tage mit voller Bullisch-Liste |
|---|---|---|---|---|---|---|
| 2022 | 109 | 527 | 0 | 0.0 | 0.0 | 0 % |
| 2023 | 250 | 522 | 191 | 6.8 | 6.8 | 67 % |
| 2024 | 252 | 519 | 335 | 10.0 | 10.0 | 100 % |
| 2025 | 250 | 517 | 344 | 10.0 | 10.0 | 100 % |
| 2026 | 199 | 602 | 533 | 10.0 | 10.0 | 100 % |

Anteil der Gate-Zeilen mit Gruppen-Score je Jahr:

| Jahr | trend | reversal | value | quality | revisions | surprise | analyst | insider | institutional | short_interest | short_volume | sentiment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | 100 % | 100 % | 0 % | 0 % | 0 % | 100 % | 0 % | 100 % | 100 % | 100 % | 0 % | 0 % |
| 2024 | 100 % | 100 % | 0 % | 0 % | 0 % | 100 % | 0 % | 100 % | 100 % | 100 % | 0 % | 0 % |
| 2025 | 100 % | 100 % | 0 % | 0 % | 0 % | 100 % | 0 % | 100 % | 100 % | 100 % | 0 % | 0 % |
| 2026 | 100 % | 100 % | 74 % | 74 % | 11 % | 100 % | 77 % | 81 % | 94 % | 100 % | 20 % | 42 % |

Gate-Ausschlüsse (Zeilen, Mehrfachgründe möglich): few_groups 241368, low_liquidity 4626, short_history 1545


---
*Rückblick auf Altdaten: junge Quellen (Schätzungen, Optionen-IV, Sentiment, Short Volume) haben nur wenige Monate Historie und fehlen früher; die Gruppen-Abdeckung oben zeigt, was in welchem Jahr tatsächlich in den Screen einging. Keine Anlageberatung.*
