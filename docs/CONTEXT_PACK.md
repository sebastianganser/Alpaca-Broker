# Context Pack v2 – format reference

Status: implemented 2026-10-18 (plan [2026-10-17_Plan_Strategieneutral.md](2026-10-17_Plan_Strategieneutral.md), step 3; code in [`derived/context_pack/`](../src/trading_signals/derived/context_pack/), deploy together with step 4 / migration 039). **Hard cut:** `pack_format: 2` replaces the trade-oriented pack of Sprint 9.5c F2 (stage-1 risk pre-filter, hand-weighted top 5, `decisions.yaml`); the skill prompt (`aktien-analyse`, outside the repository) has to switch to this format and to `forecasts.yaml` at the same time as the deploy.

Audience: whoever writes or maintains the analysis skill prompt. This document describes what the nightly job produces, what every part means, and how the result goes back ([FORECASTS.md](FORECASTS.md) is the format of the return file).

## 1. Purpose

Alpaca-Broker forecasts which tickers will rise (later: fall) over days to months; a separate, later bot trades. Claude reads the pack like a Wall-Street broker and writes `forecasts.yaml` (absolute direction `up` / `down` / `neutral` per ticker and horizon 20 / 60 sessions, 5 optional; confidence, conviction, drivers, reason). Nothing in the pack is an entry, exit, stop, target or position size, and nothing may come back in the forecast file ([FORECASTS.md](FORECASTS.md) §3).

The pack is **data, not a recommendation**: the screen that picks the full files is a neutral filter ([SCREEN.md](SCREEN.md)), not a forecast.

## 2. Files

Written to `CONTEXT_PACK_PATH` (`/mnt/user/Workfiles/AlpacaBroker/context_packs`, share `\\HOMENAS\Workfiles\AlpacaBroker\context_packs`) by the nightly chain step `context_pack_generator` (04:30 chain, after `forecast_review`), one folder per NYSE session, all files UTF-8 **with BOM** (read them as `utf-8-sig`, also `screen.csv`, otherwise the first CSV header becomes `﻿ticker`):

```
context_packs/
├── requested_tickers.txt        optional, written by Sebastian or the skill: tickers that always get a full file
├── forecast_auswertung.md       report of the nightly forecast_review step (German), rewritten every night
└── 2026-10-16/                  one folder per session (the session the pack describes, NOT the generation date)
    ├── 00_uebersicht.md         overview: regime, coverage, gate counts, file list, return channel (German)
    ├── screen.csv               every ticker that passed the quality gate (the percentile population)
    ├── AAPL.md  AMD.md  …       full files, ALPHABETICAL, no rank prefix, no marker why they are in the pack
    └── forecasts.yaml           written by the skill (forecasts/v1), read by forecast_review - never touched by the generator
```

**A pack that exists is not regenerated (2026-10-18).** If the session folder already contains a `forecasts.yaml` (Claude has assessed it) or `pack_candidates` rows of the session exist (the pack was published), `generate_daily` returns 0 and touches nothing - no file is written, replaced or deleted and the candidates are not replaced -, because a different selection would change the files and the shortlist the forecasts are judged against. The reason is logged (`ContextPackGenerator.skip_reason`), the chain step ends `success` with the note `pack kept (…) - not regenerated`, and the chain notes repeat it. This also covers a `partial` chain re-run for the same session on a later day (weekend, holiday): it cannot rewrite the pack. Only an operator can force a regeneration (`generate_daily(d, force=True)`, for a repair). A re-run of a session that has no such trace (e.g. an earlier run failed before it committed) replaces the ticker files, the overview and `screen.csv`; before that it deletes only the ticker files **this generator wrote** (name `TICKER.md` with the generator's frontmatter marker `pack_format:` + `ticker: <name>`, or the legacy `NN_TICKER.md` of pack v1 with `rank:`) - `forecasts.yaml`, `requested_tickers.txt`, a hand-made `README.md` and any other file are never deleted. The overview is written last, so a folder that contains `00_uebersicht.md` is complete.

## 3. Which tickers get a full file

1. **Quality gate** ([SCREEN.md](SCREEN.md) §3, confirmed 2026-10-18): active member of the point-in-time universe, 20-day mean dollar volume ≥ USD 5 m, ≥ 60 real price sessions, at least half of the 12 evidence groups with data. The pack uses *exactly* the gate that `forecast_review` rebuilds for the evaluation (`forecast_review.gate_flags`, price-history proxy `price_vs_sma50` not NULL), so `screen.csv` and the evaluation universe are the same set (pinned by a unit test).
2. **Screen** `screen/v1` on the session's `feature_snapshots` cross-section → buckets: ≤ 10 bullish candidates (fewer on weak days, never topped up), 10 bearish (**switched off** by configuration, `BEARISH_ENABLED = False`), 5 **random controls** (deterministic per date: `RANDOM_SEED` and the date ordinal), and the **requested tickers**.
3. **Requested tickers:** `context_packs/requested_tickers.txt`, one ticker per line (`#` starts a comment, case-insensitive, duplicates and invalid lines are ignored, at most 25 are used – the rest is listed in the overview). They stay in every pack until removed from the file. The file may be UTF-8 (with or without BOM), UTF-16 with a BOM (PowerShell, Notepad "Unicode") or Windows-1252 (Notepad "ANSI", e.g. an umlaut in a comment); a file that cannot be decoded or read is logged and ignored - the pack is then generated without requested tickers, the nightly step does not fail. A requested ticker that fails the gate still gets a file, with a note at the top ("Qualitäts-Gate nicht bestanden: …"); it is not in `screen.csv`. A requested ticker without a feature snapshot on the session day cannot get a file and is named in the overview.
4. **No ranking anchor:** the files are alphabetical; neither the files, the overview nor `screen.csv` say which ticker came from which bucket, and the ticker files contain no screen result (no breadth, composite, rank, bucket). The buckets live only in `signals.pack_candidates` (`bucket`, `bucket_rank`, group scores and flags), written by the generator in the same transaction; the evaluation uses them to separate the screen effect from Claude's effect ([FORECASTS.md](FORECASTS.md) §6, `claude_value_add`). `screen.csv` shows the evidence of *all* gate tickers (the same columns for everybody), so anyone can reproduce the shortlist – but the pack does not point at it. **The random controls are therefore not blind** (Claude can infer which files are controls from `screen.csv`); Sebastian confirmed on 2026-10-18 that this is acceptable: the controls measure the screen's own effect on the forecasts, they do not have to be hidden from the forecaster ([FORECASTS.md](FORECASTS.md) §6).

## 4. `00_uebersicht.md`

German. Sections: (1) task, (2) market regime (VIX and regime, 10Y-2Y spread, HY spread, dollar index, inflation expectation, breadth, market news sentiment; SPY returns), (3) coverage and gate (snapshot count, gate count, exclusion reasons – a ticker can fail several –, share of gate tickers with data per evidence group, note on young sources), (4) the file list (ticker, company, sector – alphabetical, nothing else), (5) a short explanation of the selection (neutral screen + random controls + requested tickers, deliberately not saying which is which) and of the percentiles, (6) the **return channel**: the `forecasts.yaml` template (the exact block of [`template.py`](../src/trading_signals/derived/context_pack/template.py), validated against the real parser by a unit test), the **deadline**, the rules and the pointer to `context_packs/forecast_auswertung.md`.

**Deadline:** the forecast file has to be written (file last-write *and* `decided_at`) **before 09:30 New York of the next trading day** – the open from which the evaluated return starts (`open(d+1)`). The overview names it with both clocks, e.g. `2026-10-19 09:30 New York (19.10. 15:30 Uhr deutscher Zeit)` for a Friday pack; it is 14:30 Berlin time in the few weeks where the US and Europe are on different clock-change schedules. **The file has to be in place before 09:25 New York** (15:25 Berlin time; 14:25 in the weeks of the clock change): the scheduler job `forecast_seal` hashes it at 09:00, 09:15 and 09:25 New York, and only a content that was sealed in time counts as on time - `decided_at` and the file's modification time prove nothing. Every edit after the last run changes the hash and is late. Later or edited files are stored, evaluated separately, and never count for the pre-registered criterion ([FORECASTS.md](FORECASTS.md) §2).

## 5. `screen.csv`

One row per gate ticker, sorted by ticker, comma separated, empty cell = no value (NULL, **not** 0), `\n` line ends, no bucket and no rank column.

| Column | Meaning |
|---|---|
| `ticker`, `company`, `sector` | identification (sector is the current `universe.sector`, not point-in-time) |
| `close` | close of the session (real bar) |
| `ret_20d`, `ret_60d` | trailing return over 20 / 60 sessions (fraction) |
| `dollar_volume_musd` | 20-session mean of close × volume, USD million (liquidity) |
| `earnings_days_until` | calendar days to the next known earnings date (information, never a filter) |
| `n_groups` | evidence groups with a score (of 12) |
| `evidence_breadth` | bullish groups minus bearish groups (group percentile ≥ 80 / ≤ 20); empty with fewer than 3 groups |
| `composite`, `composite_pct` | mean group score and its percentile (0-100) |
| `n_bull_groups`, `n_bear_groups` | the two counts behind the breadth |
| `p_<group>` (12) | percentile (0-100) of the group score among the gate tickers; **oriented**: high = more bullish evidence of that group (a high `p_value` = cheap, a high `p_short_interest` = little short interest). Empty = the group has too little data for this ticker |
| `events_bull`, `events_bear` | `;`-separated event flags ([SCREEN.md](SCREEN.md) §5): `insider_cluster`, `ark_increase`, `analyst_upgrades`, `positive_revisions`, `new_13f_positions` / `insider_net_selling`, `ark_reduction`, `analyst_downgrades`, `negative_revisions`, `exited_13f_positions` |

The 12 groups: `trend`, `reversal`, `value`, `quality`, `revisions`, `surprise`, `analyst`, `insider`, `institutional`, `short_interest`, `short_volume`, `sentiment` – the same names the forecast file accepts as `drivers` tags (plus `news`, `earnings`, `sector`, `macro`, `other`).

## 6. Ticker file (`TICKER.md`)

German, about 8-10 KB. Frontmatter (valid YAML):

```yaml
---
pack_format: 2
ticker: ACME
session: 2026-10-16
sector: "Technology"
data_freshness:            # date of the newest observation per source, null = no coverage
  prices: 2026-10-16
  fundamentals: 2026-10-14
  estimates: 2026-10-16
  analyst: 2026-10-13
  earnings: 2026-08-17      # last reported quarter
  insider: 2026-10-09
  form13f: 2026-08-14       # latest filing of the report period shown
  ark: 2026-10-16
  politicians: 2026-10-12
  short_interest: 2026-09-30   # settlement date
  short_volume: 2026-10-16
  options_iv: 2026-10-16
  news: 2026-10-16
---
```

**How to read a value.** Every figure is the raw value, plus – in parentheses – `P` = percentile of that *raw value* among the gate tickers (n is printed in the file) and, where meaningful and the sector has at least 10 gate tickers, `S` = percentile within the sector: `(P87 · S62)`. The percentile is the share of gate tickers with a smaller value (ties count half). **A high percentile means a high value, not "bullish"** – a high P/E percentile is expensive. Counts that are mostly 0 (upgrades, new positions) have heavily tied percentiles; read the raw value. `keine Abdeckung` means the source delivers nothing for this ticker (NULL); a plain `0` means the source covers the ticker and nothing happened. Source age is given in the section's source line (`Stand 2026-10-14 (2 T alt)`) and in section 12.

| # | Section | Content |
|---|---|---|
| 1 | Kopf | price (close), sector / industry, market cap, 20-day dollar volume, index membership as a plain fact (`S&P 500 (seit 2010-03-01)`; seeded rows say "Aufnahmedatum nicht bekannt"), data coverage per evidence group (`trend 3/3 · value 2/3 …`, `(zu wenig)` below the screen's threshold), a note if the quality gate failed |
| 2 | Trend und Momentum | returns 1 / 5 / 20 / 60 / 120 / 252 sessions with SPY next to them and P / S, distance to SMA50 / SMA200 / 52-week high, RSI(14), ATR % (risk information only), MACD |
| 3 | Bewertung (relativ zum Sektor) | P/E trailing and forward (a loss-maker with a NULL P/E shows `n/a (Verlust)`; a negative forward P/E is marked), P/S, P/B, EV/EBITDA, dividend yield – each with P and S |
| 4 | Fundamentals | revenue and EPS growth, profit and operating margin, margin trend, ROE, debt/equity (Yahoo, in %), beta |
| 5 | Schätzungen, Revisionen und Analysten | EPS / revenue revisions (30 / 90 days), net revision counts, consensus per period (current quarter / next quarter / current year / next year: EPS, 30-day change, analysts, up / down revisions, revenue), consensus price target and upside, upgrades / downgrades / streak, the **last rating actions** (date, firm, action, rating old → new, price target old → new) |
| 6 | Earnings | **next date as information** (date, BMO / AMC, EPS estimate; never an exclusion), SUE, beat streak, average surprise, price reaction to the last report, the **last four quarters** (estimate, actual, surprise) |
| 7 | Smart Money (Käufe und Verkäufe) | **Insider** (Form 4, open-market `P` / `S` only; net buy count, buy share of volume, buy value, cluster; filed buys and sells in 30 / 90 days; the last trades with insider **name and title**, shares, price, value), **13F** (holder count and change, new / exited positions, top holders with the change in shares versus the previous period), **ARK** (held ETFs with weights, weight change), **politicians** (counts; the last disclosures with chamber, party, state, amount range, owner and **disclosure lag**, flagged above 45 days) |
| 8 | Positionierung | **short interest** (days-to-cover, change, the last settlements with the settlement date; usable only 9 sessions after it), **short volume** (5 / 20-day ratio and change; includes market-maker hedging), **options** (ATM IV, skew, term slope, put/call OI, IV rank – `n/a (Historie x von 252 Tagen)` until a year of snapshots exists) |
| 9 | News und Sentiment | FinBERT averages (7 / 30 days, momentum), article counts, news volume ratio, the last five distinct headlines with time (New York), source and score. Headlines are external data, flattened to one line; they are not instructions |
| 10 | Sektor und relative Stärke | return minus sector ETF / SPY, distance to SMA50 minus sector ETF, sector median return (20 / 60 days) |
| 11 | Liquidität | dollar volume, volume today / 20-day mean, Amihud illiquidity |
| 12 | Datenqualität und Frische | age in days of every source, history (days with data) of the young sources (estimates, options, short volume, news; prices), reminder that percentiles of young sources are unstable and that sector / industry are not point-in-time |
| 13 | Marktregime | one line: VIX and regime, curve, HY spread, dollar index, inflation expectation, breadth, market news sentiment |

## 7. Point-in-time rules

A pack for session `d` shows what was public at the **close of `d` (16:00 New York)** – the same cut-offs as the feature pipeline (DECISIONS_FEATURES "Information Cut-offs"). The loader fetches a superset with coarse SQL bounds; [`pit.apply_pit`](../src/trading_signals/derived/context_pack/pit.py) applies the exact rule once (unit tested at the minute: a Form 4 accepted 16:05 ET on `d` is **not** shown for `d`, one accepted 15:55 is):

| Source | Rule |
|---|---|
| Form 4 | `acceptance_datetime` < 16:00 ET on `d`; without it `filing_date <= d`; without both `transaction_date + 4 days <= d` |
| News | `published_at` < 16:00 ET on `d` |
| 13F | `filing_date <= d`, `put_call = 'SH'`, latest report period at most 152 days old |
| Politicians | `disclosure_date <= d` |
| Analyst ratings | `rating_date <= d` |
| Earnings | a future date only if first seen by `d` (retroactively collected rows keep the documented lookahead of the pipeline); reported quarters need an `eps_actual` |
| FINRA short interest | `settlement_date` ≤ the 9th NYSE session before `d` |
| Estimates, options IV, fundamentals, ARK, indicators | snapshot dated ≤ `d` (fundamentals / estimates / indicators at most 14 days old) |
| Index membership | interval `[valid_from, valid_to)` contains `d` |
| Snapshots | the feature columns only – the `return_*` target columns are never loaded |

The skill may of course look at live data until it writes `forecasts.yaml`; that is why the on-time rule exists ([FORECASTS.md](FORECASTS.md) §2, §7).

## 8. Writing `forecasts.yaml`

The overview carries the template; the complete rules are in [FORECASTS.md](FORECASTS.md) §3. In short:

- header: `schema: forecasts/v1`, `session` = the folder date, `decided_at` with a time zone offset (e.g. `2026-10-17T09:40:00+02:00`; a value without an offset rejects the file), `forecaster_version` (skill / prompt + model + `pack_format`, ≤ 64 characters – **change it only when the forecaster really changes**: every change before the verdict starts a new frozen sample);
- one row per `(ticker, horizon_days)`, horizon 20 or 60 (5 only descriptive); `direction` `up` / `down` / `neutral`; `confidence` 0.50-0.95 and `conviction` 1-5 for `up` / `down` only; optional `expected_pct`, `drivers` (≤ 5 tags), `reason`, `risks`;
- forbidden: any entry, exit, stop, target, size or order field (the whole row is rejected with a message); unknown fields are rejected too;
- any ticker of `screen.csv` may be forecast, with or without a file; a ticker without a feature snapshot on the session day is rejected;
- the file goes **into the folder of the pack it reviewed**, before the deadline of the overview - to be safe before 09:25 New York, the last seal run (§4); do not edit it afterwards.

**Where results appear:** the next nightly run (04:30) ingests the file; `context_packs/forecast_auswertung.md` shows files read / rejected, per-forecast checks, the metrics and the state of the pre-registered criterion (300 sessions, ≈ 14 months; until then every figure is "descriptive only"). Rejected files or rows make the `forecast_review` run `partial` and are listed with the reason – fix the skill output, not the file.

## 9. Operations

- **Generation:** `ContextPackGenerator(session).generate_daily(session_date)` (also used by `scripts/repair/fix_market_date_collision.py`); one batch query per source for all selected tickers (about 25 queries, independent of the number of tickers), the cross-section work is pandas / numpy. Synthetic 650-ticker session with 25 files: about 0.2-0.5 s of computation, plus the database round trips (a few seconds at most).
- **Transaction:** `pack_candidates` rows of the session are replaced (flushed) before the files are written and committed by the caller (`get_session()`); a failing file write raises and rolls them back. A session that already has candidates or a `forecasts.yaml` is not replaced at all (§2).
- **Size:** about 8-10 KB per ticker file with all sources present (every NULL section is shorter), `screen.csv` about 95-110 KB for 650 gate tickers (≈ 30k tokens if read completely – filter it first). Row limits (insider 6, ratings 5, politicians 4, headlines 5, holders 5, earnings 4) are constants in [`constants.py`](../src/trading_signals/derived/context_pack/constants.py).
- **Container user:** files are written as `99:100`; never run the generator with `docker exec -u root` (root-owned files cannot be overwritten, LEARNINGS 2026-10-15).
- **Tests:** `tests/unit/test_context_pack*.py` (generator on a fake loader, point-in-time rules, renderers, template parsed by the real forecast parser), no database.

## 10. Known limits

- The screen is neutral and only a filter: its historical validation shows no secured excess return ([reports/2026-10-17_screen_backtest.md](reports/2026-10-17_screen_backtest.md)); the random controls exist to measure that live.
- Young sources (estimates, options IV, sentiment, short volume have only months of history) have unstable percentiles; the IV rank needs 252 days.
- `universe.sector` / `industry` are the current values, so sector percentiles of historical packs are not point-in-time.
- 13F data of the newest period is incomplete during the filing season; politician disclosures come with long lags; insider sales are often pre-planned.
- Percentiles are over the gate universe of the day (n ≈ 550-650), not over the whole market.
- No delisting returns in the targets (LEARNINGS 2026-10-17): the worst outcomes are under-represented in the evaluation.

## 11. Excerpt (synthetic data, invented company)

```
## 3. Bewertung (relativ zum Sektor)

Quelle: Fundamentals-Snapshot (Yahoo), Stand 2026-10-14 (2 T alt). Hohes Perzentil = teurer.

- KGV (trailing): 41.2 (P77 · S77)
- KGV (Forward): 29.7 (P73 · S74)
- KUV: 21.8 (P100 · S99)
…
## 7. Smart Money (Käufe und Verkäufe)
### Insider (Form 4, offene Marktgeschäfte: Käufe und Verkäufe)

- Netto-Käufe 30 T (Käufe minus Verkäufe): -3 (P11) · Kaufanteil am Volumen 30 T: 0.0 % (P13) · 90 T: 2.0 % (P25)
- Gemeldet 30 T: 0 Käufe (0.00 USD) / 3 Verkäufe (51.38 Mio. USD); 90 T: 1 Käufe (162.5 Tsd. USD) / 3 Verkäufe (51.38 Mio. USD). Verkäufe sind oft vorab geplant (10b5-1) und sagen weniger als Käufe.

| Gemeldet | Insider (Titel) | Art | Aktien | Preis | Wert |
|---|---|---|---|---|---|
| 2026-10-09 | Alan Founder (CEO) | Verkauf | 120.0 Tsd. | 188.40 | 22.61 Mio. USD |
| 2026-10-02 | Maria Finance (CFO) | Verkauf | 35.0 Tsd. | 181.20 | 6.34 Mio. USD |
| 2026-09-08 | Lee Director (Director) | Kauf | 1.0 Tsd. | 162.50 | 162.5 Tsd. USD |
```
