# Barrier-Backtest – aktueller Context-Pack-Score

Erstellt: 2026-10-17 17:27 · Signale 2022-07-28 – 2026-10-16 · 1060 Tage · Kosten 0.050 % Round-Trip

Einstieg Open d+1 · Gap → Ausführung zum Open · gewichtet je Signaltag gleich.
Tag mit Ziel **und** Stop berührt (mehrdeutig): *konservativ* = Stop zählt (Untergrenze); *OHLC-Pfad* = grüne Kerze O→L→H→C (Stop zuerst), rote Kerze O→H→L→C (Ziel zuerst).
Filter: ATR 1 %–3 %, keine Earnings in der Haltedauer · Regime: SPY > SMA200 und VIX < 25.

## TP 0.50% / SL 1.00% / 14d

Zufalls-Trefferquote (Break-even brutto): **66.7 %**

| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC | mehrdeutig | Zeitstopp | Ø Haltedauer |
|---|---|---|---|---|---|---|---|---|
| Universum | 557166 | 50.5 % | 66.3 % | -0.291 % | -0.055 % | 24.4 % | 0.0 % | 1.1 |
| Top 5 | 5230 | 45.0 % | 65.9 % | -0.376 % | -0.063 % | 33.1 % | 0.0 % | 1.0 |
| Bottom 5 | 5229 | 41.5 % | 64.2 % | -0.425 % | -0.083 % | 36.4 % | 0.0 % | 1.0 |
| Top 5 gefiltert | 5230 | 52.5 % | 67.2 % | -0.263 % | -0.042 % | 21.7 % | 0.0 % | 1.1 |
| Top 5 gefiltert + Regime | 4280 | 52.5 % | 67.1 % | -0.265 % | -0.045 % | 21.3 % | 0.0 % | 1.1 |

Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):

- Top 5: konservativ -0.085 % [-0.110 % … -0.059 %] ❌ · OHLC-Pfad -0.009 % [-0.032 % … 0.013 %] n.s.
- Bottom 5: konservativ -0.134 % [-0.161 % … -0.107 %] ❌ · OHLC-Pfad -0.029 % [-0.052 % … -0.008 %] ❌
- Top 5 gefiltert: konservativ 0.028 % [0.002 % … 0.048 %] ✅ · OHLC-Pfad 0.013 % [-0.007 % … 0.031 %] n.s.
- Top 5 gefiltert + Regime: konservativ 0.012 % [-0.014 % … 0.033 %] n.s. · OHLC-Pfad 0.010 % [-0.011 % … 0.025 %] n.s.

Pro Jahr (OHLC-Pfad, Ø Netto/Trade):

| Jahr | Universum | Top 5 | Bottom 5 | Top 5 gefiltert | Top 5 gefiltert + Regime |
|---|---|---|---|---|---|
| 2022 | -0.057 % | -0.041 % | -0.040 % | -0.047 % | 0.150 % |
| 2023 | -0.049 % | -0.028 % | -0.084 % | -0.003 % | -0.010 % |
| 2024 | -0.046 % | -0.080 % | -0.074 % | -0.061 % | -0.062 % |
| 2025 | -0.059 % | -0.055 % | -0.108 % | -0.031 % | -0.036 % |
| 2026 | -0.067 % | -0.111 % | -0.085 % | -0.080 % | -0.086 % |

## TP 0.50% / SL 1.50% / 14d

Zufalls-Trefferquote (Break-even brutto): **75.0 %**

| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC | mehrdeutig | Zeitstopp | Ø Haltedauer |
|---|---|---|---|---|---|---|---|---|
| Universum | 557166 | 64.4 % | 75.1 % | -0.260 % | -0.045 % | 14.4 % | 0.1 % | 1.2 |
| Top 5 | 5230 | 59.4 % | 75.3 % | -0.359 % | -0.041 % | 21.5 % | 0.0 % | 1.1 |
| Bottom 5 | 5229 | 56.0 % | 73.5 % | -0.425 % | -0.073 % | 24.6 % | 0.0 % | 1.1 |
| Top 5 gefiltert | 5230 | 67.7 % | 76.6 % | -0.197 % | -0.020 % | 10.9 % | 0.0 % | 1.2 |
| Top 5 gefiltert + Regime | 4280 | 67.3 % | 76.1 % | -0.206 % | -0.030 % | 10.8 % | 0.0 % | 1.2 |

Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):

- Top 5: konservativ -0.100 % [-0.132 % … -0.066 %] ❌ · OHLC-Pfad 0.005 % [-0.025 % … 0.034 %] n.s.
- Bottom 5: konservativ -0.165 % [-0.201 % … -0.124 %] ❌ · OHLC-Pfad -0.028 % [-0.055 % … -0.000 %] ❌
- Top 5 gefiltert: konservativ 0.063 % [0.031 % … 0.090 %] ✅ · OHLC-Pfad 0.026 % [0.004 % … 0.047 %] ✅
- Top 5 gefiltert + Regime: konservativ 0.039 % [0.006 % … 0.064 %] ✅ · OHLC-Pfad 0.017 % [-0.007 % … 0.038 %] n.s.

Pro Jahr (OHLC-Pfad, Ø Netto/Trade):

| Jahr | Universum | Top 5 | Bottom 5 | Top 5 gefiltert | Top 5 gefiltert + Regime |
|---|---|---|---|---|---|
| 2022 | -0.059 % | -0.007 % | -0.040 % | -0.008 % | 0.150 % |
| 2023 | -0.040 % | -0.029 % | -0.077 % | -0.006 % | -0.026 % |
| 2024 | -0.032 % | -0.037 % | -0.043 % | -0.015 % | -0.015 % |
| 2025 | -0.046 % | -0.025 % | -0.081 % | -0.014 % | -0.022 % |
| 2026 | -0.063 % | -0.103 % | -0.119 % | -0.059 % | -0.072 % |

## TP 0.75% / SL 1.50% / 14d

Zufalls-Trefferquote (Break-even brutto): **66.7 %**

| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC | mehrdeutig | Zeitstopp | Ø Haltedauer |
|---|---|---|---|---|---|---|---|---|
| Universum | 557166 | 60.1 % | 67.2 % | -0.192 % | -0.034 % | 10.4 % | 0.1 % | 1.3 |
| Top 5 | 5230 | 56.5 % | 67.9 % | -0.276 % | -0.021 % | 16.4 % | 0.0 % | 1.2 |
| Bottom 5 | 5229 | 53.1 % | 65.8 % | -0.351 % | -0.066 % | 19.2 % | 0.0 % | 1.2 |
| Top 5 gefiltert | 5230 | 63.3 % | 68.4 % | -0.127 % | -0.013 % | 6.6 % | 0.0 % | 1.3 |
| Top 5 gefiltert + Regime | 4280 | 62.8 % | 67.8 % | -0.140 % | -0.027 % | 6.6 % | 0.0 % | 1.3 |

Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):

- Top 5: konservativ -0.084 % [-0.122 % … -0.045 %] ❌ · OHLC-Pfad 0.013 % [-0.024 % … 0.048 %] n.s.
- Bottom 5: konservativ -0.159 % [-0.200 % … -0.113 %] ❌ · OHLC-Pfad -0.032 % [-0.069 % … 0.002 %] n.s.
- Top 5 gefiltert: konservativ 0.066 % [0.033 % … 0.093 %] ✅ · OHLC-Pfad 0.021 % [-0.005 % … 0.047 %] n.s.
- Top 5 gefiltert + Regime: konservativ 0.042 % [0.007 % … 0.072 %] ✅ · OHLC-Pfad 0.011 % [-0.018 % … 0.038 %] n.s.

Pro Jahr (OHLC-Pfad, Ø Netto/Trade):

| Jahr | Universum | Top 5 | Bottom 5 | Top 5 gefiltert | Top 5 gefiltert + Regime |
|---|---|---|---|---|---|
| 2022 | -0.061 % | -0.020 % | -0.011 % | -0.038 % | -0.087 % |
| 2023 | -0.029 % | -0.026 % | -0.097 % | -0.000 % | -0.031 % |
| 2024 | -0.013 % | -0.001 % | -0.050 % | 0.007 % | 0.006 % |
| 2025 | -0.034 % | -0.014 % | -0.060 % | 0.004 % | -0.015 % |
| 2026 | -0.052 % | -0.052 % | -0.084 % | -0.063 % | -0.082 % |

## TP 0.75% / SL 2.25% / 14d

Zufalls-Trefferquote (Break-even brutto): **75.0 %**

| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC | mehrdeutig | Zeitstopp | Ø Haltedauer |
|---|---|---|---|---|---|---|---|---|
| Universum | 557166 | 71.5 % | 75.5 % | -0.145 % | -0.024 % | 5.2 % | 0.1 % | 1.6 |
| Top 5 | 5230 | 68.8 % | 75.9 % | -0.231 % | -0.019 % | 9.2 % | 0.0 % | 1.4 |
| Bottom 5 | 5229 | 66.1 % | 74.5 % | -0.310 % | -0.059 % | 11.0 % | 0.0 % | 1.4 |
| Top 5 gefiltert | 5230 | 73.9 % | 76.0 % | -0.080 % | -0.014 % | 2.7 % | 0.0 % | 1.6 |
| Top 5 gefiltert + Regime | 4280 | 73.3 % | 75.4 % | -0.098 % | -0.035 % | 2.5 % | 0.0 % | 1.6 |

Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):

- Top 5: konservativ -0.087 % [-0.138 % … -0.041 %] ❌ · OHLC-Pfad 0.005 % [-0.039 % … 0.045 %] n.s.
- Bottom 5: konservativ -0.166 % [-0.219 % … -0.106 %] ❌ · OHLC-Pfad -0.035 % [-0.083 % … 0.011 %] n.s.
- Top 5 gefiltert: konservativ 0.065 % [0.027 % … 0.103 %] ✅ · OHLC-Pfad 0.010 % [-0.027 % … 0.044 %] n.s.
- Top 5 gefiltert + Regime: konservativ 0.043 % [0.003 % … 0.080 %] ✅ · OHLC-Pfad -0.004 % [-0.040 % … 0.030 %] n.s.

Pro Jahr (OHLC-Pfad, Ø Netto/Trade):

| Jahr | Universum | Top 5 | Bottom 5 | Top 5 gefiltert | Top 5 gefiltert + Regime |
|---|---|---|---|---|---|
| 2022 | -0.063 % | -0.040 % | -0.007 % | -0.016 % | 0.100 % |
| 2023 | -0.029 % | -0.031 % | -0.093 % | -0.000 % | -0.035 % |
| 2024 | 0.002 % | 0.028 % | -0.019 % | -0.018 % | -0.025 % |
| 2025 | -0.013 % | -0.023 % | -0.088 % | 0.041 % | 0.010 % |
| 2026 | -0.043 % | -0.050 % | -0.057 % | -0.101 % | -0.111 % |

## TP 1.00% / SL 2.00% / 14d

Zufalls-Trefferquote (Break-even brutto): **66.7 %**

| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC | mehrdeutig | Zeitstopp | Ø Haltedauer |
|---|---|---|---|---|---|---|---|---|
| Universum | 557166 | 64.0 % | 67.3 % | -0.120 % | -0.020 % | 4.9 % | 0.2 % | 1.8 |
| Top 5 | 5230 | 62.1 % | 68.2 % | -0.184 % | -0.001 % | 8.7 % | 0.0 % | 1.5 |
| Bottom 5 | 5229 | 59.1 % | 66.3 % | -0.274 % | -0.059 % | 10.3 % | 0.0 % | 1.4 |
| Top 5 gefiltert | 5230 | 66.5 % | 68.2 % | -0.052 % | -0.003 % | 2.2 % | 0.0 % | 1.8 |
| Top 5 gefiltert + Regime | 4280 | 65.8 % | 67.4 % | -0.076 % | -0.029 % | 2.1 % | 0.0 % | 1.8 |

Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):

- Top 5: konservativ -0.064 % [-0.120 % … -0.009 %] ❌ · OHLC-Pfad 0.019 % [-0.033 % … 0.070 %] n.s.
- Bottom 5: konservativ -0.154 % [-0.211 % … -0.090 %] ❌ · OHLC-Pfad -0.040 % [-0.095 % … 0.013 %] n.s.
- Top 5 gefiltert: konservativ 0.068 % [0.026 % … 0.111 %] ✅ · OHLC-Pfad 0.016 % [-0.023 % … 0.057 %] n.s.
- Top 5 gefiltert + Regime: konservativ 0.043 % [0.001 % … 0.089 %] ✅ · OHLC-Pfad -0.001 % [-0.039 % … 0.042 %] n.s.

Pro Jahr (OHLC-Pfad, Ø Netto/Trade):

| Jahr | Universum | Top 5 | Bottom 5 | Top 5 gefiltert | Top 5 gefiltert + Regime |
|---|---|---|---|---|---|
| 2022 | -0.063 % | 0.019 % | -0.013 % | 0.035 % | -0.100 % |
| 2023 | -0.019 % | -0.008 % | -0.060 % | -0.010 % | -0.058 % |
| 2024 | 0.009 % | 0.066 % | -0.058 % | 0.027 % | 0.017 % |
| 2025 | -0.012 % | -0.053 % | -0.082 % | 0.014 % | -0.013 % |
| 2026 | -0.043 % | -0.022 % | -0.056 % | -0.080 % | -0.073 % |

## TP 1.00% / SL 3.00% / 14d

Zufalls-Trefferquote (Break-even brutto): **75.0 %**

| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC | mehrdeutig | Zeitstopp | Ø Haltedauer |
|---|---|---|---|---|---|---|---|---|
| Universum | 557166 | 73.8 % | 75.6 % | -0.072 % | -0.002 % | 2.3 % | 0.4 % | 2.2 |
| Top 5 | 5230 | 73.0 % | 76.4 % | -0.128 % | 0.008 % | 4.0 % | 0.0 % | 1.8 |
| Bottom 5 | 5229 | 70.3 % | 74.9 % | -0.218 % | -0.032 % | 5.8 % | 0.1 % | 1.8 |
| Top 5 gefiltert | 5230 | 75.5 % | 76.2 % | -0.020 % | 0.006 % | 0.7 % | 0.2 % | 2.3 |
| Top 5 gefiltert + Regime | 4280 | 74.9 % | 75.5 % | -0.049 % | -0.026 % | 0.6 % | 0.3 % | 2.3 |

Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):

- Top 5: konservativ -0.056 % [-0.131 % … 0.007 %] n.s. · OHLC-Pfad 0.010 % [-0.057 % … 0.071 %] n.s.
- Bottom 5: konservativ -0.146 % [-0.223 % … -0.061 %] ❌ · OHLC-Pfad -0.030 % [-0.097 % … 0.040 %] n.s.
- Top 5 gefiltert: konservativ 0.051 % [-0.003 % … 0.110 %] n.s. · OHLC-Pfad 0.009 % [-0.044 % … 0.064 %] n.s.
- Top 5 gefiltert + Regime: konservativ 0.031 % [-0.024 % … 0.095 %] n.s. · OHLC-Pfad -0.010 % [-0.060 % … 0.049 %] n.s.

Pro Jahr (OHLC-Pfad, Ø Netto/Trade):

| Jahr | Universum | Top 5 | Bottom 5 | Top 5 gefiltert | Top 5 gefiltert + Regime |
|---|---|---|---|---|---|
| 2022 | -0.054 % | 0.065 % | 0.023 % | 0.073 % | -0.250 % |
| 2023 | -0.017 % | -0.034 % | -0.035 % | -0.058 % | -0.113 % |
| 2024 | 0.031 % | 0.098 % | -0.019 % | 0.056 % | 0.044 % |
| 2025 | 0.024 % | -0.065 % | -0.032 % | 0.046 % | 0.030 % |
| 2026 | -0.033 % | 0.007 % | -0.082 % | -0.067 % | -0.073 % |
