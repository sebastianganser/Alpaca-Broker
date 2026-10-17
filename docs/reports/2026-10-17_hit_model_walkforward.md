# Walk-forward-Test Trefferwahrscheinlichkeit (Schritt B)

Erstellt: 2026-10-17 17:26 · Daten 2022-07-28 – 2026-09-28 · 557275 Snapshots · 96 Eingaben

Trade: Einstieg Open d+1, Ziel +1 %, Stop −2 %, max. 14 Handelstage, 0,05 % Kosten, mehrdeutige Tage nach OHLC-Regel (*kons.* = als Stop gezählt). Break-even-Trefferquote **68.3 %**. Purge 16 Handelstage, quartalsweise neu trainiert.

Entwicklung: Testquartale ab 2023-07-01 bis vor 2025-01-01 · Holdout: ab 2025-01-01 (einmalige Prüfung der in der Entwicklung gewählten Regel).

## Modellgüte je Quartal (AUC, 0,5 = Zufall)

*AUC gesamt* mischt Tages- und Aktienauswahl; *Tages-AUC* misst nur die Rangfolge der Aktien innerhalb eines Tages.

| Quartal | AUC logit | Tages-AUC logit | AUC gbm | Tages-AUC gbm | Trefferquote Universum |
|---|---|---|---|---|---|
| 2023Q3 | 0.466 | 0.508 | 0.501 | 0.489 | 62.5 % |
| 2023Q4 | 0.468 | 0.510 | 0.540 | 0.499 | 72.4 % |
| 2024Q1 | 0.481 | 0.500 | 0.497 | 0.480 | 71.4 % |
| 2024Q2 | 0.512 | 0.504 | 0.499 | 0.496 | 64.6 % |
| 2024Q3 | 0.517 | 0.499 | 0.465 | 0.500 | 72.3 % |
| 2024Q4 | 0.408 | 0.489 | 0.397 | 0.494 | 64.3 % |
| 2025Q1 | 0.522 | 0.519 | 0.513 | 0.500 | 68.9 % |
| 2025Q2 | 0.492 | 0.503 | 0.484 | 0.498 | 68.9 % |
| 2025Q3 | 0.496 | 0.502 | 0.504 | 0.491 | 66.5 % |
| 2025Q4 | 0.514 | 0.497 | 0.556 | 0.510 | 66.0 % |
| 2026Q1 | 0.502 | 0.506 | 0.468 | 0.507 | 67.5 % |
| 2026Q2 | 0.527 | 0.489 | 0.475 | 0.506 | 67.6 % |
| 2026Q3 | 0.521 | 0.513 | 0.491 | 0.503 | 64.6 % |
| **Ø Entwicklung** | 0.475 | 0.502 | 0.483 | 0.493 | |
| **Ø Holdout** | 0.511 | 0.504 | 0.499 | 0.502 | |

## Regelwahl auf der Entwicklungsphase

| Regel | Trades | Tage mit Kandidat | Trefferquote | Ø Netto | Ø Netto Universum |
|---|---|---|---|---|---|
| gbm, max. 3/Tag, P ≥ 0.683 | 663 | 224 | 70.3 % | 0.086 % | 0.008 % |
| gbm, max. 1/Tag, P ≥ 0.600 | 352 | 352 | 69.0 % | 0.076 % | 0.003 % |
| logit, max. 1/Tag, P ≥ 0.683 | 333 | 333 | 70.9 % | 0.073 % | -0.010 % |
| gbm, max. 3/Tag, P ≥ 0.700 | 570 | 192 | 70.1 % | 0.070 % | -0.026 % |
| gbm, max. 1/Tag, ohne Schwelle | 378 | 378 | 68.5 % | 0.067 % | -0.000 % |
| gbm, max. 1/Tag, P ≥ 0.683 | 224 | 224 | 69.2 % | 0.066 % | 0.008 % |
| logit, max. 5/Tag, P ≥ 0.750 | 917 | 236 | 69.8 % | 0.053 % | -0.032 % |
| logit, max. 3/Tag, P ≥ 0.683 | 942 | 333 | 70.1 % | 0.051 % | -0.010 % |
| logit, max. 5/Tag, P ≥ 0.683 | 1539 | 333 | 70.0 % | 0.050 % | -0.010 % |
| gbm, max. 1/Tag, P ≥ 0.700 | 192 | 192 | 68.8 % | 0.048 % | -0.026 % |
| gbm, max. 3/Tag, P ≥ 0.650 | 893 | 298 | 68.9 % | 0.048 % | 0.004 % |
| gbm, max. 3/Tag, P ≥ 0.600 | 1055 | 352 | 68.8 % | 0.046 % | 0.003 % |

Gewählte Regel: **gbm, max. 3/Tag, P ≥ 0.683**

## Ergebnis Holdout (entscheidend)

| Regel | Trades | Tage mit Kandidat | Tage ohne | Trefferquote | Ø Netto | Ø Netto kons. | Ø Netto Universum | Differenz (95 %-KI) |
|---|---|---|---|---|---|---|---|---|
| Entwicklung (In-Sample-Wahl) | 663 | 224 | 41 % | 70.3 % | 0.086 % | 0.051 % | 0.008 % | 0.078 % [-0.002 % … 0.184 %] n.s. |
| **Holdout** | 954 | 318 | 27 % | 67.8 % | -0.006 % | -0.169 % | -0.020 % | 0.014 % [-0.126 % … 0.158 %] n.s. |

Konservativ (mehrdeutig = Stop), Differenz zum Universum: -0.013 % [-0.144 % … 0.119 %] n.s.

**Erfolgskriterium (Ø Netto > 0 und KI der Differenz > 0): ❌ nicht bestanden**

Holdout je Quartal:

| Quartal | Trades | Tage mit Kandidat | Trefferquote | Ø Netto | Ø Netto Universum |
|---|---|---|---|---|---|
| 2025Q1 | 180 | 60 | 71.1 % | 0.028 % | 0.017 % |
| 2025Q2 | 186 | 62 | 72.6 % | 0.213 % | 0.040 % |
| 2025Q3 | 87 | 29 | 62.1 % | -0.200 % | -0.044 % |
| 2025Q4 | 180 | 60 | 55.6 % | -0.375 % | -0.019 % |
| 2026Q1 | 111 | 37 | 68.5 % | 0.009 % | -0.057 % |
| 2026Q2 | 102 | 34 | 74.5 % | 0.218 % | -0.001 % |
| 2026Q3 | 108 | 36 | 72.2 % | 0.106 % | -0.149 % |

### Kalibrierung Holdout (gbm)

| Ø P(Treffer) | Trefferquote | Ø Netto | Trades |
|---|---|---|---|
| 60.0 % | 67.4 % | 0.007 % | 23925 |
| 64.3 % | 67.2 % | -0.020 % | 23934 |
| 66.1 % | 67.4 % | -0.019 % | 23971 |
| 67.4 % | 65.2 % | -0.093 % | 23873 |
| 68.7 % | 64.4 % | -0.117 % | 23940 |
| 70.1 % | 67.2 % | -0.033 % | 23899 |
| 71.8 % | 67.0 % | -0.032 % | 23918 |
| 73.9 % | 67.6 % | -0.009 % | 23992 |
| 76.2 % | 66.9 % | -0.019 % | 23848 |
| 79.9 % | 70.2 % | 0.056 % | 23913 |

## Nur zur Information: Holdout Top-k ohne Schwelle

| Regel | Trades | Tage mit Kandidat | Tage ohne | Trefferquote | Ø Netto | Ø Netto kons. | Ø Netto Universum | Differenz (95 %-KI) |
|---|---|---|---|---|---|---|---|---|
| logit, max. 1/Tag, ohne Schwelle | 435 | 435 | 0 % | 64.8 % | -0.118 % | -0.353 % | -0.025 % | -0.093 % [-0.247 % … 0.090 %] n.s. |
| logit, max. 5/Tag, ohne Schwelle | 2175 | 435 | 0 % | 68.5 % | 0.006 % | -0.220 % | -0.025 % | 0.031 % [-0.041 % … 0.117 %] n.s. |
| gbm, max. 1/Tag, ohne Schwelle | 435 | 435 | 0 % | 65.3 % | -0.094 % | -0.252 % | -0.025 % | -0.068 % [-0.257 % … 0.090 %] n.s. |
| gbm, max. 5/Tag, ohne Schwelle | 2175 | 435 | 0 % | 68.5 % | 0.010 % | -0.113 % | -0.025 % | 0.035 % [-0.040 % … 0.114 %] n.s. |

## Logit-Koeffizienten (Training vor dem Holdout, standardisiert)

| Eingabe | Koeffizient |
|---|---|
| macro_vix_regime__raw | -0.1612 |
| macro_vix__raw | +0.1346 |
| insider_buy_value_30d | +0.1198 |
| insider_buy_ratio_30d | -0.1152 |
| macro_inflation_expectation__raw | -0.1094 |
| price_vs_sma50 | +0.0749 |
| atr_14_pct | -0.0587 |
| dollar_volume_20d | +0.0463 |
| atr_14_pct__raw | +0.0458 |
| macro_hy_spread__raw | +0.0405 |
| relative_strength_spy | -0.0401 |
| amihud_illiquidity_20d | +0.0393 |
| macro_yield_spread__raw | +0.0334 |
| form13f_holder_delta_qoq | +0.0290 |
| st_close_location | +0.0249 |

Gesamturteil: **❌ nicht bestanden**
