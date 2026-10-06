# Walk-forward-Test Trefferwahrscheinlichkeit (Schritt B)

Erstellt: 2026-10-06 12:51 · Daten 2022-07-28 – 2026-09-15 · 515412 Snapshots · 93 Eingaben

Trade: Einstieg Open d+1, Ziel +1 %, Stop −2 %, max. 14 Handelstage, 0,05 % Kosten, mehrdeutige Tage nach OHLC-Regel (*kons.* = als Stop gezählt). Break-even-Trefferquote **68.3 %**. Purge 16 Handelstage, quartalsweise neu trainiert.

Entwicklung: Testquartale ab 2023-07-01 bis vor 2025-01-01 · Holdout: ab 2025-01-01 (einmalige Prüfung der in der Entwicklung gewählten Regel).

## Modellgüte je Quartal (AUC, 0,5 = Zufall)

*AUC gesamt* mischt Tages- und Aktienauswahl; *Tages-AUC* misst nur die Rangfolge der Aktien innerhalb eines Tages.

| Quartal | AUC logit | Tages-AUC logit | AUC gbm | Tages-AUC gbm | Trefferquote Universum |
|---|---|---|---|---|---|
| 2023Q3 | 0.464 | 0.500 | 0.511 | 0.495 | 62.4 % |
| 2023Q4 | 0.469 | 0.505 | 0.532 | 0.489 | 72.8 % |
| 2024Q1 | 0.485 | 0.496 | 0.490 | 0.489 | 71.6 % |
| 2024Q2 | 0.514 | 0.500 | 0.500 | 0.495 | 64.9 % |
| 2024Q3 | 0.520 | 0.494 | 0.492 | 0.506 | 72.6 % |
| 2024Q4 | 0.398 | 0.487 | 0.446 | 0.498 | 64.4 % |
| 2025Q1 | 0.520 | 0.516 | 0.525 | 0.509 | 69.1 % |
| 2025Q2 | 0.491 | 0.499 | 0.511 | 0.492 | 69.2 % |
| 2025Q3 | 0.494 | 0.501 | 0.498 | 0.491 | 66.7 % |
| 2025Q4 | 0.504 | 0.488 | 0.532 | 0.504 | 66.2 % |
| 2026Q1 | 0.498 | 0.500 | 0.503 | 0.504 | 67.6 % |
| 2026Q2 | 0.531 | 0.492 | 0.496 | 0.507 | 67.6 % |
| 2026Q3 | 0.529 | 0.520 | 0.499 | 0.509 | 65.8 % |
| **Ø Entwicklung** | 0.475 | 0.497 | 0.495 | 0.495 | |
| **Ø Holdout** | 0.510 | 0.503 | 0.509 | 0.502 | |

## Regelwahl auf der Entwicklungsphase

| Regel | Trades | Tage mit Kandidat | Trefferquote | Ø Netto | Ø Netto Universum |
|---|---|---|---|---|---|
| gbm, max. 1/Tag, P ≥ 0.650 | 320 | 320 | 71.9 % | 0.144 % | 0.016 % |
| gbm, max. 1/Tag, P ≥ 0.600 | 362 | 362 | 71.3 % | 0.119 % | 0.010 % |
| gbm, max. 1/Tag, ohne Schwelle | 378 | 378 | 71.2 % | 0.114 % | 0.006 % |
| gbm, max. 1/Tag, P ≥ 0.683 | 285 | 285 | 71.2 % | 0.097 % | 0.010 % |
| gbm, max. 1/Tag, P ≥ 0.700 | 256 | 256 | 69.9 % | 0.071 % | 0.012 % |
| gbm, max. 1/Tag, P ≥ 0.720 | 225 | 225 | 69.3 % | 0.055 % | 0.007 % |
| gbm, max. 3/Tag, P ≥ 0.650 | 953 | 320 | 69.0 % | 0.036 % | 0.016 % |
| gbm, max. 3/Tag, P ≥ 0.600 | 1086 | 362 | 69.0 % | 0.034 % | 0.010 % |
| gbm, max. 5/Tag, P ≥ 0.683 | 1384 | 285 | 69.1 % | 0.032 % | 0.010 % |
| gbm, max. 3/Tag, P ≥ 0.683 | 843 | 285 | 69.1 % | 0.028 % | 0.010 % |
| gbm, max. 3/Tag, ohne Schwelle | 1134 | 378 | 68.6 % | 0.023 % | 0.006 % |
| gbm, max. 5/Tag, P ≥ 0.650 | 1576 | 320 | 68.5 % | 0.022 % | 0.016 % |

Gewählte Regel: **gbm, max. 1/Tag, P ≥ 0.650**

## Ergebnis Holdout (entscheidend)

| Regel | Trades | Tage mit Kandidat | Tage ohne | Trefferquote | Ø Netto | Ø Netto kons. | Ø Netto Universum | Differenz (95 %-KI) |
|---|---|---|---|---|---|---|---|---|
| Entwicklung (In-Sample-Wahl) | 320 | 320 | 15 % | 71.9 % | 0.144 % | 0.097 % | 0.016 % | 0.127 % [-0.013 % … 0.291 %] n.s. |
| **Holdout** | 386 | 386 | 9 % | 69.9 % | 0.062 % | -0.039 % | -0.012 % | 0.075 % [-0.047 % … 0.175 %] n.s. |

Konservativ (mehrdeutig = Stop), Differenz zum Universum: 0.122 % [-0.008 % … 0.230 %] n.s.

**Erfolgskriterium (Ø Netto > 0 und KI der Differenz > 0): ❌ nicht bestanden**

Holdout je Quartal:

| Quartal | Trades | Tage mit Kandidat | Trefferquote | Ø Netto | Ø Netto Universum |
|---|---|---|---|---|---|
| 2025Q1 | 60 | 60 | 70.0 % | 0.016 % | 0.021 % |
| 2025Q2 | 62 | 62 | 75.8 % | 0.246 % | 0.046 % |
| 2025Q3 | 28 | 28 | 57.1 % | -0.369 % | -0.046 % |
| 2025Q4 | 64 | 64 | 71.9 % | 0.148 % | -0.056 % |
| 2026Q1 | 61 | 61 | 63.9 % | -0.135 % | -0.023 % |
| 2026Q2 | 58 | 58 | 72.4 % | 0.198 % | 0.016 % |
| 2026Q3 | 53 | 53 | 71.7 % | 0.101 % | -0.068 % |

### Kalibrierung Holdout (gbm)

| Ø P(Treffer) | Trefferquote | Ø Netto | Trades |
|---|---|---|---|
| 59.6 % | 67.4 % | -0.029 % | 22654 |
| 63.6 % | 66.6 % | -0.040 % | 22741 |
| 65.6 % | 65.1 % | -0.090 % | 22568 |
| 67.1 % | 66.8 % | -0.039 % | 22659 |
| 68.3 % | 66.4 % | -0.054 % | 22649 |
| 69.9 % | 68.8 % | 0.014 % | 22951 |
| 71.8 % | 67.5 % | -0.023 % | 22359 |
| 74.1 % | 67.1 % | -0.028 % | 22671 |
| 76.7 % | 68.1 % | 0.023 % | 22630 |
| 81.0 % | 70.5 % | 0.068 % | 22654 |

## Nur zur Information: Holdout Top-k ohne Schwelle

| Regel | Trades | Tage mit Kandidat | Tage ohne | Trefferquote | Ø Netto | Ø Netto kons. | Ø Netto Universum | Differenz (95 %-KI) |
|---|---|---|---|---|---|---|---|---|
| logit, max. 1/Tag, ohne Schwelle | 426 | 426 | 0 % | 66.9 % | -0.050 % | -0.339 % | -0.017 % | -0.033 % [-0.206 % … 0.104 %] n.s. |
| logit, max. 5/Tag, ohne Schwelle | 2130 | 426 | 0 % | 67.3 % | -0.030 % | -0.292 % | -0.017 % | -0.013 % [-0.100 % … 0.068 %] n.s. |
| gbm, max. 1/Tag, ohne Schwelle | 426 | 426 | 0 % | 69.2 % | 0.042 % | -0.057 % | -0.017 % | 0.059 % [-0.067 % … 0.156 %] n.s. |
| gbm, max. 5/Tag, ohne Schwelle | 2130 | 426 | 0 % | 68.4 % | 0.007 % | -0.099 % | -0.017 % | 0.024 % [-0.052 % … 0.081 %] n.s. |

## Logit-Koeffizienten (Training vor dem Holdout, standardisiert)

| Eingabe | Koeffizient |
|---|---|
| macro_vix_regime__raw | -0.1567 |
| insider_buy_value_30d | +0.1289 |
| insider_buy_ratio_30d | -0.1244 |
| macro_vix__raw | +0.1205 |
| macro_inflation_expectation__raw | -0.1104 |
| price_vs_sma50 | +0.0738 |
| atr_14_pct | -0.0703 |
| atr_14_pct__raw | +0.0671 |
| macro_hy_spread__raw | +0.0449 |
| relative_strength_spy | -0.0422 |
| macro_yield_spread__raw | +0.0386 |
| st_close_location | +0.0271 |
| sector_relative_return_20d | +0.0267 |
| sector_relative_momentum | -0.0230 |
| form13f_holder_delta_qoq | +0.0212 |

Gesamturteil: **❌ nicht bestanden**
