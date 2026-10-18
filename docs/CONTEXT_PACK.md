# Context Pack v2 – format reference

Status: implemented 2026-10-18 (plan [2026-10-17_Plan_Strategieneutral.md](2026-10-17_Plan_Strategieneutral.md), step 3; code in [`derived/context_pack/`](../src/trading_signals/derived/context_pack/), deploy together with step 4 / migration 039). **Hard cut:** `pack_format: 2` replaces the trade-oriented pack of Sprint 9.5c F2 (stage-1 risk pre-filter, hand-weighted top 5, `decisions.yaml`); the skill prompt (`aktien-analyse`, outside the repository) has to switch to this format and to `forecasts.yaml` at the same time as the deploy.

**Enriched 2026-10-18 (additive, `pack_format` stays 2):** price targets of the rating houses, estimate ranges, eight earnings quarters with the price reaction, revenue / EPS / current ratio, 52-week range, realised volatility, holding change of insiders, tracked 13F funds, a peer section (14) and a **chart image per ticker file** with support / resistance levels - all from data already in the database. Sections 1-13 keep their numbers and headings; the new content is only additional lines, tables and the new section 14 (details in §12).

**Enriched 2026-10-18 (SEC, additive, `pack_format` stays 2):** the new SEC data of migration 040 - quarterly financials from XBRL, the filings feed with 8-K items and press-release excerpts, and twelve more Yahoo fundamentals fields - is wired in: extra lines and a quarterly table in sections 4, 5 and 8, the new **last section 15** ("SEC-Meldungen") and two more keys in the frontmatter's `data_freshness` (details in §13).

Audience: whoever writes or maintains the analysis skill prompt. This document describes what the nightly job produces, what every part means, and how the result goes back ([FORECASTS.md](FORECASTS.md) is the format of the return file).

## 1. Purpose

Alpaca-Broker forecasts which tickers will rise (later: fall) over days to months; a separate, later bot trades. Claude reads the pack like a Wall-Street broker and writes `forecasts.yaml` (absolute direction `up` / `down` / `neutral` per ticker and horizon 20 / 60 sessions, 5 optional; confidence, conviction, drivers, reason). Nothing in the pack is an entry, exit, stop, target or position size, and nothing may come back in the forecast file ([FORECASTS.md](FORECASTS.md) §3).

The pack is **data, not a recommendation**: the screen that picks the full files is a neutral filter ([SCREEN.md](SCREEN.md)), not a forecast.

## 2. Files

Written to `CONTEXT_PACK_PATH` (`/mnt/user/Workfiles/AlpacaBroker/context_packs`, share `\\HOMENAS\Workfiles\AlpacaBroker\context_packs`) by the nightly chain step `context_pack_generator` (04:30 chain, after `forecast_review`), one folder per NYSE session, all text files UTF-8 **with BOM** (read them as `utf-8-sig`, also `screen.csv`, otherwise the first CSV header becomes `﻿ticker`; the chart images are plain PNG):

```
context_packs/
├── requested_tickers.txt        optional, written by Sebastian or the skill: tickers that always get a full file
├── forecast_auswertung.md       report of the nightly forecast_review step (German), rewritten every night
└── 2026-10-16/                  one folder per session (the session the pack describes, NOT the generation date)
    ├── 00_uebersicht.md         overview: regime, coverage, gate counts, file list, return channel (German)
    ├── screen.csv               every ticker that passed the quality gate (the percentile population)
    ├── AAPL.md  AMD.md  …       full files, ALPHABETICAL, no rank prefix, no marker why they are in the pack
    ├── AAPL.png AMD.png …       one chart image per full file (§6.1), same name, same folder
    └── forecasts.yaml           written by the skill (forecasts/v1), read by forecast_review - never touched by the generator
```

**A pack that exists is not regenerated (2026-10-18).** If the session folder already contains a `forecasts.yaml` (Claude has assessed it) or `pack_candidates` rows of the session exist (the pack was published), `generate_daily` returns 0 and touches nothing - no file is written, replaced or deleted and the candidates are not replaced -, because a different selection would change the files and the shortlist the forecasts are judged against. The reason is logged (`ContextPackGenerator.skip_reason`), the chain step ends `success` with the note `pack kept (…) - not regenerated`, and the chain notes repeat it. This also covers a `partial` chain re-run for the same session on a later day (weekend, holiday): it cannot rewrite the pack. Only an operator can force a regeneration (`generate_daily(d, force=True)`, for a repair). A re-run of a session that has no such trace (e.g. an earlier run failed before it committed) replaces the ticker files, the charts, the overview and `screen.csv`; before that it deletes only the files **this generator wrote**: ticker files (name `TICKER.md` with the generator's frontmatter marker `pack_format:` + `ticker: <name>`, or the legacy `NN_TICKER.md` of pack v1 with `rank:`) and charts (name `TICKER.png` **and** the ownership marker inside the PNG: a `Description` text chunk `alpaca-broker context_pack chart v1 ticker=<TICKER> session=<date>`). `forecasts.yaml`, `requested_tickers.txt`, a hand-made `README.md`, any hand-made image - even one called `AAPL.png` - and every other file are never deleted (a file that has exactly the name of one of today's ticker files or charts is replaced by the new one, as it always was for `TICKER.md`). A chart that cannot be drawn costs only the image: it is logged, the ticker file then has no chart reference, and a stale `TICKER.png` of an earlier run is removed. The overview is written last, so a folder that contains `00_uebersicht.md` is complete.

## 3. Which tickers get a full file

1. **Quality gate** ([SCREEN.md](SCREEN.md) §3, confirmed 2026-10-18): active member of the point-in-time universe, 20-day mean dollar volume ≥ USD 5 m, ≥ 60 real price sessions, at least half of the 12 evidence groups with data. The pack uses *exactly* the gate that `forecast_review` rebuilds for the evaluation (`forecast_review.gate_flags`, price-history proxy `price_vs_sma50` not NULL), so `screen.csv` and the evaluation universe are the same set (pinned by a unit test).
2. **Screen** `screen/v1` on the session's `feature_snapshots` cross-section → buckets: ≤ 10 bullish candidates (fewer on weak days, never topped up), 10 bearish (**switched off** by configuration, `BEARISH_ENABLED = False`), 5 **random controls** (deterministic per date: `RANDOM_SEED` and the date ordinal), and the **requested tickers**.
3. **Requested tickers:** `context_packs/requested_tickers.txt`, one ticker per line (`#` starts a comment, case-insensitive, duplicates and invalid lines are ignored, at most 25 are used – the rest is listed in the overview). They stay in every pack until removed from the file. The file may be UTF-8 (with or without BOM), UTF-16 with a BOM (PowerShell, Notepad "Unicode") or Windows-1252 (Notepad "ANSI", e.g. an umlaut in a comment); a file that cannot be decoded or read is logged and ignored - the pack is then generated without requested tickers, the nightly step does not fail. A requested ticker that fails the gate still gets a file, with a note at the top ("Qualitäts-Gate nicht bestanden: …"); it is not in `screen.csv`. A requested ticker without a feature snapshot on the session day cannot get a file and is named in the overview.
4. **No ranking anchor:** the files are alphabetical; neither the files, the overview nor `screen.csv` say which ticker came from which bucket, and the ticker files contain no screen result (no breadth, composite, rank, bucket). The buckets live only in `signals.pack_candidates` (`bucket`, `bucket_rank`, group scores and flags), written by the generator in the same transaction; the evaluation uses them to separate the screen effect from Claude's effect ([FORECASTS.md](FORECASTS.md) §6, `claude_value_add`). `screen.csv` shows the evidence of *all* gate tickers (the same columns for everybody), so anyone can reproduce the shortlist – but the pack does not point at it. **The random controls are therefore not blind** (Claude can infer which files are controls from `screen.csv`); Sebastian confirmed on 2026-10-18 that this is acceptable: the controls measure the screen's own effect on the forecasts, they do not have to be hidden from the forecaster ([FORECASTS.md](FORECASTS.md) §6).

## 4. `00_uebersicht.md`

German. Sections: (1) task, (2) market regime (VIX and regime, 10Y-2Y spread, HY spread, dollar index, inflation expectation, breadth, market news sentiment; SPY returns), (3) coverage and gate (snapshot count, gate count, exclusion reasons – a ticker can fail several –, share of gate tickers with data per evidence group, note on young sources), (4) the file list (ticker, company, sector – alphabetical, nothing else) and, if charts were drawn, a note that every ticker file has its `TICKER.png` next to it, (5) a short explanation of the selection (neutral screen + random controls + requested tickers, deliberately not saying which is which) and of the percentiles, (6) the **return channel**: the `forecasts.yaml` template (the exact block of [`template.py`](../src/trading_signals/derived/context_pack/template.py), validated against the real parser by a unit test), the **deadline**, the rules and the pointer to `context_packs/forecast_auswertung.md`.

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

German, about 11-17.5 KB (§9). Frontmatter (valid YAML):

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
  sec_filings: 2026-10-14      # newest public filing of the last 90 days (section 15)
  financials: 2026-08-04       # `filed` date of the newest public quarterly row (section 4)
---
```

**How to read a value.** Every figure is the raw value, plus – in parentheses – `P` = percentile of that *raw value* among the gate tickers (n is printed in the file) and, where meaningful and the sector has at least 10 gate tickers, `S` = percentile within the sector: `(P87 · S62)`. The percentile is the share of gate tickers with a smaller value (ties count half). **A high percentile means a high value, not "bullish"** – a high P/E percentile is expensive. Counts that are mostly 0 (upgrades, new positions) have heavily tied percentiles; read the raw value. `keine Abdeckung` means the source delivers nothing for this ticker (NULL); a plain `0` means the source covers the ticker and nothing happened. Source age is given in the section's source line (`Stand 2026-10-14 (2 T alt)`) and in section 12.

| # | Section | Content |
|---|---|---|
| 1 | Kopf | price (close), sector / industry, market cap, 20-day dollar volume, index membership as a plain fact (`S&P 500 (seit 2010-03-01)`; seeded rows say "Aufnahmedatum nicht bekannt"), data coverage per evidence group (`trend 3/3 · value 2/3 …`, `(zu wenig)` below the screen's threshold), a note if the quality gate failed |
| 2 | Trend und Momentum | returns 1 / 5 / 20 / 60 / 120 / 252 sessions with SPY next to them and P / S, distance to SMA50 / SMA200 / 52-week high, RSI(14), ATR % (risk information only), MACD, the **52-week range** (low and high with their dates, distance, position in the range 0-100 %), **support / resistance levels** (§6.1) and the reference to the **chart image** |
| 3 | Bewertung (relativ zum Sektor) | P/E trailing and forward (a loss-maker with a NULL P/E shows `n/a (Verlust)`; a negative forward P/E is marked), P/S, P/B, EV/EBITDA, dividend yield – each with P and S |
| 4 | Fundamentals | revenue TTM, EPS TTM, current ratio, revenue and EPS growth, profit and operating margin, margin trend, ROE, debt/equity (Yahoo, in %), beta; **Yahoo snapshot** (free cash flow TTM, cash, total debt, gross margin with P / S, shares outstanding, float); the **quarterly table (SEC XBRL)** of the last up to 8 quarters, newest first (period end, fiscal quarter, form, filed date, revenue, revenue growth versus the same quarter a year earlier, gross and operating margin, net income, diluted EPS, free cash flow, cash, total debt; USD millions) and a line with the **change of the share count over about a year** (§6.4) |
| 5 | Schätzungen, Revisionen und Analysten | EPS / revenue revisions (30 / 90 days), net revision counts, **two consensus tables per period** (current quarter / next quarter / current year / next year): EPS (average, low – high, year-ago, growth, 30-day change, analysts, up / down revisions) and revenue (average, low – high, analysts, year-ago, growth); consensus price target and upside, upgrades / downgrades / streak, a count of the price-target changes of the last 30 days, the **last rating actions** (date, firm, action, rating old → new, **price target old → new with the change in % and the target action** `angehoben` / `gesenkt` / `bestätigt` / `Erstkursziel`); one line with Yahoo's **analyst consensus** (mean recommendation 1 = strong buy … 5 = sell, Yahoo's label such as `buy`, number of analysts) |
| 6 | Earnings | **next date as information** (date, BMO / AMC, EPS estimate; never an exclusion), SUE, beat streak, average surprise, price reaction to the last report, the **last eight quarters** (report time, estimate, actual, surprise and the **price reaction on day 0 and day +1**, §6.2) and the **average absolute day-0 reaction** as an "expected move" proxy |
| 7 | Smart Money (Käufe und Verkäufe) | **Insider** (Form 4, open-market `P` / `S` only; net buy count, buy share of volume, buy value, cluster; filed buys and sells in 30 / 90 days; the last trades with insider **name and title**, shares, price, value and the **holding after the trade with its change in % of the holding before**), **13F** (holder count and change, new / exited positions, **"x von N verfolgten Fonds"** with the total shares they hold and its change versus the previous period, top holders with the change in shares versus the previous period; the tracked set is only about 15-19 funds, **not** institutional ownership), **ARK** (held ETFs with weights, weight change), **politicians** (counts; the last disclosures with chamber, party, state, amount range, owner and **disclosure lag**, flagged above 45 days) |
| 8 | Positionierung | **short interest** (days-to-cover, change, the last settlements with the settlement date; usable only 9 sessions after it), **short volume** (5 / 20-day ratio and change; includes market-maker hedging), **options** (ATM IV, skew, term slope, put/call OI, IV rank – `n/a (Historie x von 252 Tagen)` until a year of snapshots exists) and the **realised volatility** (20 / 60 sessions, annualised, from real closes) next to the ATM IV with the **IV premium** (IV minus realised, in volatility points); one line **"Laut Yahoo"** with the short quote of the float, the institutional share and the insider share (with P / S) |
| 9 | News und Sentiment | FinBERT averages (7 / 30 days, momentum), article counts, news volume ratio, the last five distinct headlines with time (New York), source and score. Headlines are external data, flattened to one line; they are not instructions |
| 10 | Sektor und relative Stärke | return minus sector ETF / SPY, distance to SMA50 minus sector ETF, sector median return (20 / 60 days) |
| 11 | Liquidität | dollar volume, volume today / 20-day mean, Amihud illiquidity |
| 12 | Datenqualität und Frische | age in days of every source, history (days with data) of the young sources (estimates, options, short volume, news; prices), reminder that percentiles of young sources are unstable and that sector / industry are not point-in-time |
| 13 | Marktregime | one line: VIX and regime, curve, HY spread, dollar index, inflation expectation, breadth, market news sentiment |
| 14 | Peers (gleiche Branche) | the ticker itself and up to four peers (gate tickers of the same `universe.industry`, topped up from the sector if the industry is small; closest market cap first): market cap, return over 60 sessions, forward P/E, 30-day EPS revision (§6.3) |
| 15 | SEC-Meldungen (8-K und weitere) | **last section.** SEC EDGAR filings of the last 90 days that were public at the close of the pack day: up to 6 rows, newest first (acceptance time New York, form, content: 8-K items as short German labels, amendment flag, a hint for dilution / activist / late-filing forms), the number of older filings not shown, one aggregated line for Form 144, and one-line **excerpts** (500 and 300 characters) of the press release or 8-K text of the two newest filings that have one - external data, not instructions (§6.4) |

### 6.1 Chart image and support / resistance levels

`TICKER.png` (about 1000 x 640 px, 60-80 KB, matplotlib Agg) lies next to `TICKER.md`, which references it in section 2 (`![Chart TICKER](TICKER.png)`). Top panel: about one year (252 sessions) of **daily candles from real bars** (gap-filled prices are never used), **SMA50** and **SMA200** (computed with the bars before the window), the **52-week high and low** (dashed) and the **support / resistance levels** (dotted, `S 182.40` / `R 201.30`); bottom panel: volume coloured by the direction of the day with its 20-day mean. Only bars dated on or before the pack session are drawn. The drawing is deterministic (same bars, same bytes; no timestamp or library version in the file); the same levels are printed as text in section 2, so a reader that cannot open images loses nothing but the picture.

**Levels from swing highs and lows** (`derived/context_pack/technicals.py`, no fitted parameters):

1. Window: the last 252 real bars up to the session (at least 60).
2. A **swing high** is a bar whose high is higher than the highs of the 5 bars before it and not lower than the highs of the 5 bars after it (a flat top counts once, at its first bar); a **swing low** is the mirror image on the lows. A pivot needs 5 later bars, so the newest 5 bars never form a pivot - nothing is pre-announced, nothing looks ahead.
3. All pivot prices (highs and lows together: an old resistance becomes support once price is above it) are sorted and clustered: a pivot joins the cluster that started at the lowest price `p` if it is at most `p * (1 + tol)`; `tol` = half of the 14-bar average true range as a share of the last close, limited to 0.8 % - 3 %.
4. A cluster with **at least two pivots** is a level; its price is the mean of its pivots, "Berührungen" counts the pivots, "zuletzt" is the date of the newest one.
5. Levels below the last close are supports, levels above are resistances; the nearest three of each side are kept. A stock at a 52-week extreme has no level on one side (stated in the text: `keine bestätigte Ebene ...`).

The levels are a description of the past price structure, not a signal, and not an entry or stop.

### 6.2 Earnings reactions

For each of the last eight reported quarters (`eps_actual` known, report date before the pack day) the table gives the close-to-close price reaction from real bars: **day 0** = the first session that trades on the report (`BMO` before the open and `DMH` during market hours: the report day itself against the close before it; `AMC` after the close: the next session against the report day's close), **day +1** = the following session against day 0. A report without a known time (`time_of_day` NULL) shows `Zeit unbekannt` - the reaction day is never guessed -, a missing real bar shows `keine Abdeckung`, and a session that lies after the pack day shows `offen`. The **expected-move proxy** is the mean of the absolute day-0 reactions of the quarters that have one (the count is printed); it is a rough look at how much the stock typically moves on numbers, **not** an option-implied move.

### 6.3 Peers

Peers are other **gate** tickers (the population of the percentiles and `screen.csv`) of the same `universe.industry`; if the industry has fewer than four other gate tickers, the list is topped up from the same `universe.sector` (the text says how many came from where). Within a group the **closest market cap** comes first (smallest `|ln(cap / own cap)|`, ties by ticker; without an own market cap the largest first; peers without a market cap last). The result is deterministic: the same data always gives the same peers. Industry and sector are the current values, not point-in-time.

### 6.4 Quarterly financials and SEC filings

**Quarterly table (section 4).** Source: `financials_quarterly` (SEC XBRL `companyfacts`, weekly job `sec_financials`, `collectors/sec_xbrl.py`). One row is one standalone fiscal quarter in USD **as first reported** (later restatements are not in it); Q4 is the fiscal year minus nine months. Amounts are printed in **USD millions** without a unit in the cells (`12400`, `85.3`; the heading names the unit); a figure that is not reported or cannot be derived is `keine Abdeckung`, never `0`. **Revenue growth** is the revenue against the quarter whose end lies 365 ± 20 days earlier (52/53-week calendars move it by a few days) and is empty if that quarter, one of the two revenues or a positive base is missing. **Margins** are gross profit and operating income over revenue (revenue must be positive). **Free cash flow** is the stored value; if it is missing the same formula the collector uses is applied (operating cash flow minus |capex|) and only if both parts exist. The line below the table gives the share count of the newest quarter that has one against the quarter about a year before, and the reading: at least 1 % fewer shares = `netto Aktienrückkäufe`, at least 1 % more = `netto Verwässerung`, otherwise `kaum verändert` (the band absorbs the small differences between balance-sheet and cover-page counts). IFRS filers (20-F / 40-F) and companies without a published US-GAAP quarter have no rows: `keine Abdeckung`.

**Yahoo fields (sections 4, 5, 8).** Twelve columns of `fundamentals_snapshot` (migration 040) come from the same snapshot row as the older fundamentals (at most 14 days old, the source line of section 3 gives its age): free cash flow TTM, cash, total debt, gross margin, shares outstanding, float (section 4; amounts in the listing currency, USD for US companies), the analyst consensus (section 5: `recommendation_mean`, Yahoo's `recommendation_key` as plain text, `number_of_analyst_opinions`) and short quote of the float, institutional and insider share (section 8, introduced with "Laut Yahoo"; the institutional share can exceed 100 %). Ratios carry the percentile among the gate tickers (P) and in the sector (S) like the other ratios.

**SEC filings (section 15).** Source: `sec_filings` and `sec_filing_texts` (daily job `sec_filings` at 04:00 Berlin = 22:00 ET, form whitelist in `collectors/sec_filings_collector.py`; share classes of one CIK, e.g. GOOG / GOOGL, share one row and each gets it). Window: filings with a filing date in the 90 calendar days up to the pack day, public at the close (§7), newest first by acceptance time. The table has three columns: acceptance time in New York (`2026-10-13 17:10`; `(Uhrzeit unbekannt)` and only the date if EDGAR gave no time), the form, and the content. 8-K content is the list of its item numbers with short German labels (all items of Form 8-K are mapped, e.g. `2.02 Quartalszahlen`, `5.02 Vorstand/Direktoren-Wechsel`, `7.01 Reg FD`, `8.01 sonstige Ereignisse`; the boilerplate item 9.01 is dropped unless it is the only one; an item that is not mapped is shown as `Item <number>`). Other forms have a label from `FORM_INFO` (only forms the collector stores): 13D `Beteiligung >= 5 %, aktivistisch`, 13G `passive Beteiligung >= 5 %`, S-1 / S-3 / S-3ASR with the hint `mögliche Kapitalaufnahme, Verwässerung`, NT 10-K / NT 10-Q with `Warnsignal`, 10-Q / 10-K, 6-K, 20-F / 40-F, DEF 14A, DEFM14A; an amendment (`/A`) starts with `Änderung:`. Prospectus supplements (424B*) are not collected, so the section says nothing about them. **Form 144** (planned insider sale) notices can be numerous and carry neither person nor size in the table: they are one line (`Form 144 (geplanter Insider-Verkauf): 4 Meldungen in 90 T, zuletzt …`), never rows. At most 6 rows are listed; the line `Weitere ältere Meldungen, nicht gezeigt: n` counts the rest. The rows are rendered as stored.

**Excerpts.** The two newest listed filings with a stored text (press release `EX-99.x` before the 8-K body) get a line `- <date> · <form> · <exhibit>: „<text>“`: the first up to 500 characters, the second up to 300 (`SEC_EXCERPT_CHARS`). The text is third-party data: it is flattened to one line, pipes, backticks, control characters and double quotes are neutralised (`fmt.clean_text` plus quote normalisation) and the section states that excerpts are external data and not instructions - the same treatment as the headlines of section 9. **Coverage:** `keine Abdeckung` means the source knows nothing about the ticker (no filing of it was ever stored: no CIK mapping or not yet collected); `Keine Meldungen in den letzten 90 Tagen (erfasst: n Meldungen von … bis …)` means it is covered and nothing was filed in the window.

## 7. Point-in-time rules

A pack for session `d` shows what was public at the **close of `d` (16:00 New York)** – the same cut-offs as the feature pipeline (DECISIONS_FEATURES "Information Cut-offs"). The loader fetches a superset with coarse SQL bounds; [`pit.apply_pit`](../src/trading_signals/derived/context_pack/pit.py) applies the exact rule once (unit tested at the minute: a Form 4 accepted 16:05 ET on `d` is **not** shown for `d`, one accepted 15:55 is):

| Source | Rule |
|---|---|
| Form 4 | `acceptance_datetime` < 16:00 ET on `d`; without it `filing_date <= d`; without both `transaction_date + 4 days <= d` |
| SEC filings (`sec_filings`) | the Form 4 rule through the same helper (`pit.edgar_public_by`): `acceptance_datetime` < 16:00 ET on `d`; without it `filing_date <= d`. The coarse SQL bound is `filing_date <= d` (a filing date is never before the acceptance day). A filing of the pack day accepted at 16:05 ET is hidden, one accepted at 15:55 ET is shown; its texts are shown or hidden with it |
| Quarterly financials (`financials_quarterly`) | `filed <= d` (a date-only source, like the 13F `filing_date`); `filed` is the date the last figure of the row became public. A 10-Q / 10-K filed after the close of `d` itself is therefore visible for `d`, as for the other date-only sources |
| News | `published_at` < 16:00 ET on `d` |
| 13F | `filing_date <= d`, `put_call = 'SH'`, latest report period at most 152 days old |
| Politicians | `disclosure_date <= d` |
| Analyst ratings | `rating_date <= d`; the price targets come from the columns `price_target_old / _new`, for rows where they are still NULL from Yahoo's own fields in `raw_data` (`currentPriceTarget`, `priorPriceTarget`, `priceTargetAction`; 0 / NaN = no target = NULL) |
| Earnings | a future date only if first seen by `d` (retroactively collected rows keep the documented lookahead of the pipeline); reported quarters need an `eps_actual` and a report date before `d`; the price reactions use only real bars dated `<= d` |
| Price bars (chart, 52-week range, volatility, levels) | real (not gap-filled) bars with `trade_date <= d`; a bar after `d` in the fetched superset is cut by `apply_pit` |
| FINRA short interest | `settlement_date` ≤ the 9th NYSE session before `d` |
| Estimates, options IV, fundamentals (incl. the Yahoo fields of migration 040), ARK, indicators | snapshot dated ≤ `d` (fundamentals / estimates / indicators at most 14 days old) |
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

- **Generation:** `ContextPackGenerator(session).generate_daily(session_date)` (also used by `scripts/repair/fix_market_date_collision.py`); one batch query per source for all selected tickers (about 24 queries, independent of the number of tickers; the SEC filings add three - filings with their texts, coverage, quarterly financials), the cross-section work is pandas / numpy. Synthetic 650-ticker session with 25 files: about 0.2-0.5 s of computation, plus the database round trips (a few seconds at most).
- **Transaction:** `pack_candidates` rows of the session are replaced (flushed) before the files are written and committed by the caller (`get_session()`); a failing file write raises and rolls them back. A session that already has candidates or a `forecasts.yaml` is not replaced at all (§2).
- **Size:** about 11-17.5 KB per ticker file with all sources present (every NULL section is shorter; the densest synthetic file - every source filled, every table at its row limit, eight quarters, six filings and two long excerpts - measures 17.4 KB; it was 13.7 KB before the SEC enrichment of 2026-10-18, and the limit in `test_context_pack.py` was raised from 13.8 KB to 17.5 KB with it), `screen.csv` about 95-110 KB for 650 gate tickers (≈ 30k tokens if read completely – filter it first), **`TICKER.png` 60-80 KB** (limit in the tests: 120 KB; about 2 MB of images per pack folder with 25-30 files). Row limits (insider 4, ratings 4, politicians 3, headlines 4, holders 4, **earnings 8**, peers 4, levels 3 per side, **quarters 8, SEC filings 6, excerpts 2**) are constants in [`constants.py`](../src/trading_signals/derived/context_pack/constants.py) and `technicals.py`; insider, rating, politician, headline and holder rows were reduced by one or two (2026-10-18) to make room for the new content within the size budget.
- **Chart cost:** about 0.3 s per image (25-35 ticker files: roughly 8-10 s at the 04:30 chain), drawn with the object-oriented matplotlib API (no `pyplot`, safe in the scheduler's threads). Matplotlib's font cache is built on the first run of a fresh container; set `MPLCONFIGDIR` to a writable folder to keep it across restarts (the monthly feature report uses matplotlib too). `bars` are loaded with one query for all selected tickers (about 840 calendar days: the 252-session chart plus the SMA200 warm-up and eight earnings quarters).
- **Container user:** files are written as `99:100`; never run the generator with `docker exec -u root` (root-owned files cannot be overwritten, LEARNINGS 2026-10-15).
- **Tests:** `tests/unit/test_context_pack*.py` (generator on a fake loader, point-in-time rules, renderers, template parsed by the real forecast parser; for the 2026-10-18 enrichment also `test_context_pack_technicals.py`, `_peers`, `_chart`, `_enrich` and `_loader`: levels, earnings reactions, peer determinism, chart bytes stable, deletion rules for `TICKER.png`, price-target fallback, SQL shape; `test_context_pack_sec.py`: filing and quarter point-in-time rules, 8-K item mapping, excerpt sanitising, coverage wording, year-on-year growth, free-cash-flow fallback, share-count change, the Yahoo lines, the new loader queries, frontmatter and section order), no database. Most generator tests replace the chart renderer with a tiny stand-in; the real one is exercised by `test_context_pack_chart.py`.
- **Backfill of the price targets:** `scripts/repair/backfill_analyst_price_targets.py` (dry run by default, `--apply` writes) fills `analyst_ratings.price_target_new / _old` of the historical rows from `raw_data`; only NULL columns, never a stored value, idempotent. Until it has run, the pack reads the same numbers from `raw_data` (the loader falls back), so nothing depends on the order of backfill and deploy.

## 10. Known limits

- The screen is neutral and only a filter: its historical validation shows no secured excess return ([reports/2026-10-17_screen_backtest.md](reports/2026-10-17_screen_backtest.md)); the random controls exist to measure that live.
- Young sources (estimates, options IV, sentiment, short volume have only months of history) have unstable percentiles; the IV rank needs 252 days.
- `universe.sector` / `industry` are the current values, so sector percentiles of historical packs are not point-in-time.
- 13F data of the newest period is incomplete during the filing season; politician disclosures come with long lags; insider sales are often pre-planned.
- Percentiles are over the gate universe of the day (n ≈ 550-650), not over the whole market.
- No delisting returns in the targets (LEARNINGS 2026-10-17): the worst outcomes are under-represented in the evaluation.
- Price bars are the split- and dividend-adjusted series of `prices_daily` (the same as every other price figure of the pack); chart, 52-week range and levels use it, so a high or low can differ slightly from the historically quoted price.
- Support / resistance levels are a mechanical description of past swing points (§6.1), not a forecast; with a short price history (< 60 bars) there are none, and a stock at a 52-week extreme has none on one side.
- The 13F "x von N verfolgten Fonds" counts only the funds the collector tracks (about 15-19), and the filer set can differ between two periods in the filing season; the total change is therefore indicative. `shares_owned_after` of a Form 4 is the holding of the reported share class after the trade, indirect holdings are not netted - the holding change can be missing or off for such filings.
- The earnings reaction needs the report time (`BMO` / `AMC` / `DMH`); quarters without it are shown as `Zeit unbekannt`. Yahoo's report dates can be a day off, which shifts day 0 by one session.
- Peers use the current industry / sector of the universe table and the market cap of the latest fundamentals snapshot (at most 14 days old).
- The SEC filings reach back only as far as the collector has run (daily job, plus the one-off backfill of 120 days): until the backfill has run, the 90-day window can be incomplete although the ticker counts as covered. The press-release excerpts are the first 3,000 characters of the exhibit cut to 500 / 300; tables and numbers inside a release are flattened text. Filings are rendered as stored (the filer / subject attribution of multi-party forms such as 144 and 13D / 13G is the collector's, not the pack's).
- The quarterly financials are as first reported, USD only, and exist only for companies with US-GAAP XBRL quarters; share counts mix balance-sheet and cover-page values (hence the 1 % band), and a company with several share classes may report one class only. A 10-Q filed after the close on its filing day is already visible that day (date-only point-in-time).
- Yahoo's analyst consensus, short quote and holder shares are the values of the latest snapshot; the consensus is not point-in-time history and not independent of the rating actions above.

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

Further excerpt of the enrichment (same synthetic ticker):

```
## 2. Trend und Momentum
…
- 52-Wochen-Spanne: Tief 69.35 (2026-06-16) – Hoch 93.26 (2025-10-29); Abstand zum Tief +6.6 %, zum Hoch -20.7 %; Position 19 % (0 % = Tief, 100 % = Hoch)
- Unterstützung (252 Sitzungen; Preis, Abstand, Berührungen = Swing-Punkte im selben Preisband, letzte Berührung): 72.88 (-1.4 %, 4×, zuletzt 2026-09-23) · 71.83 (-2.8 %, 3×, zuletzt 2026-08-05)
- Widerstand (über dem Kurs): 75.77 (+2.5 %, 2×, zuletzt 2026-09-30) · 79.75 (+7.9 %, 2×, zuletzt 2026-08-17)

![Chart T003](T003.png)

## 5. Schätzungen, Revisionen und Analysten
…
| Periode | EPS Ø | Spanne (tief – hoch) | Vorjahr | Wachstum | Δ 30 T | Analysten | Revis. 7 T | Revis. 30 T |
|---|---|---|---|---|---|---|---|---|
| lfd. Quartal | 1.50 | 1.30 – 1.70 | 1.30 | +15.0 % | +7.1 % | 20 | 3↑/0↓ | 6↑/1↓ |
…
| Datum | Haus | Aktion | Rating (alt → neu) | Kursziel (alt → neu, Änderung) |
|---|---|---|---|---|
| 2026-10-12 | Big Bank | Upgrade | Hold → Buy | 90 → 110 (+22.2 %, angehoben) |
| 2026-10-04 | No Target Bank | bestätigt | Hold → Hold | – |

## 6. Earnings
…
| Datum | Zeit | EPS Schätzung | EPS tatsächlich | Überraschung | Kurs Tag 0 | Kurs Tag +1 |
|---|---|---|---|---|---|---|
| 2026-08-17 | AMC | 1.20 | 1.30 | +8.3 % | -2.0 % | +0.4 % |
| 2026-05-19 | BMO | 1.00 | 1.10 | +10.0 % | +2.8 % | +4.0 % |
| 2025-05-20 | – | 1.00 | 1.20 | +20.0 % | Zeit unbekannt | Zeit unbekannt |

- **Typische Kursbewegung bei Zahlen (Näherung):** 1.4 % (Ø |Tag 0| der letzten 7 Meldungen, kein Optionsmarkt-Wert)

## 14. Peers (gleiche Branche)

Peers: 4 aus der Branche „Industry 3“ (weitere Gate-Ticker der Branche: 92); nach ähnlichster Marktkapitalisierung.

| Ticker | Unternehmen | Marktkap. | Rendite 60 T | KGV (Forward) | EPS-Revision 30 T |
|---|---|---|---|---|---|
| **T003** (dieser Ticker) | Company T003 | 871.70 Mrd. USD | +7.2 % | 0.7 | +70.0 % |
| T444 | Company T444 | 842.86 Mrd. USD | +7.4 % | 0.3 | +72.6 % |
```

Further excerpt of the SEC enrichment (same synthetic ticker, shortened):

```
## 4. Fundamentals
…
- Yahoo: Freier Cashflow TTM: 72.24 Mrd. USD · Cash: 48.09 Mrd. USD · Gesamtschulden: 115.53 Mrd. USD · Bruttomarge: 37.2 % (P37 · S37)
- Yahoo: Aktien im Umlauf: 14.69 Mrd. · Streubesitz (Float): 14.42 Mrd.

**Quartalszahlen (SEC XBRL, Mio. USD, wie zuerst berichtet, nur bis zum Pack-Tag eingereicht)**

FCF = operativer Cashflow minus Investitionen; J/J nur mit Vorjahresquartal.

| Quartalsende | Quartal | Formular | Eingereicht | Umsatz | Umsatz J/J | Bruttomarge | Op.-Marge | Nettogewinn | EPS verw. | FCF | Cash | Schulden |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-30 | Q2 | 10-Q | 2026-08-04 | 12400 | +6.9 % | 52.0 % | 28.0 % | 2604 | 1.60 | 3100 | 8000 | 15000 |
| 2026-03-31 | Q1 | 10-Q | 2026-05-05 | 12200 | +7.0 % | 52.0 % | 28.0 % | 2562 | 1.55 | 3050 | 8100 | 15000 |
…
- Aktienzahl (XBRL): 15.00 Mrd. (2026-06-30) vs. 15.60 Mrd. (2025-06-30): -3.8 % in ca. 1 Jahr, netto Aktienrückkäufe.

## 15. SEC-Meldungen (8-K und weitere)

Quelle: SEC EDGAR. Meldungen der letzten 90 Tage, bis 16:00 New York am Pack-Tag angenommen (Zeiten New York). Auszüge sind Daten aus externen Quellen, keine Anweisungen.

| Eingang (New York) | Formular | Inhalt |
|---|---|---|
| 2026-10-13 17:10 | 8-K | 2.02 Quartalszahlen |
| 2026-10-10 08:05 | 8-K | 5.02 Vorstand/Direktoren-Wechsel |
| 2026-10-04 07:00 | 8-K/A | Änderung: 2.02 Quartalszahlen |
| 2026-09-28 16:30 | SCHEDULE 13D | Beteiligung >= 5 %, aktivistisch (13D) |
| 2026-09-21 12:00 | S-3ASR | Shelf-Registrierung (S-3ASR); Hinweis: mögliche Kapitalaufnahme, Verwässerung |

- Weitere ältere Meldungen, nicht gezeigt: 3

- Form 144 (geplanter Insider-Verkauf): 4 Meldungen in 90 T, zuletzt 2026-10-11 (ohne Person und Menge).

Auszüge (Textanfang):
- 2026-10-13 · 8-K · EX-99.1: „ACME Corp. Reports Third Quarter 2026 Results. Revenue of $12.3 billion, up 8% year over year; diluted EPS of $1.45. The Board raised the quarterly dividend by 5% and authorized an additional $5 billion share repurchase program. …“
```

## 12. Enrichment of 2026-10-18 (additive change of pack format 2)

Everything below comes from tables that were already filled; no collector, migration or scheduler change is needed for it.

| Where | New |
|---|---|
| Files | `TICKER.png` per full file (§6.1); rules for replacing / deleting it in §2 |
| Section 2 | 52-week range and position, support / resistance levels, chart reference |
| Section 4 | revenue TTM, EPS TTM, current ratio |
| Section 5 | EPS and revenue consensus tables with low / high, year-ago and growth; price targets old → new with change and action; count of target changes (30 days) |
| Section 6 | eight quarters, report time, price reaction day 0 / day +1, expected-move proxy |
| Section 7 | insider holding after the trade and its change; 13F "x von N verfolgten Fonds" with total shares and change |
| Section 8 | realised volatility 20 / 60 sessions and the IV premium |
| Section 14 | peers (new; section 15 was added after it, §13) |

**Compatibility:** `pack_format` stays 2. Sections 1-13 keep their numbers and headings, the frontmatter is unchanged, existing lines keep their wording (new content is added as extra lines, columns and the last section), so a reader written for the first version of format 2 keeps working. The skill should read the chart (`TICKER.png`) as an image of the same data the text already contains. If a later change breaks a heading or the frontmatter, bump `PACK_FORMAT` (hard cut, as for v1 → v2).

## 13. Enrichment of 2026-10-18 (SEC)

Everything below comes from tables that migration 040 filled (daily `sec_filings`, weekly `sec_financials`, the Yahoo snapshot); no collector or migration change is needed for it. Only the scheduler registry changed: `sec_filings` is a **non-critical** nightly upstream (`NIGHTLY_UPSTREAM["sec_filings"] = False`): the 04:30 chain waits for a still running 04:00 filings job, and a failed or stale filings night only makes the chain `partial`, it never skips features or the pack. The weekly `sec_financials` is not an upstream.

| Where | New |
|---|---|
| Frontmatter | `data_freshness.sec_filings` (newest public filing of the 90-day window) and `data_freshness.financials` (`filed` date of the newest public quarter), appended after `news`; `null` = nothing in the window / no coverage (section 15 and the quarterly table say which) |
| Section 4 | Yahoo free cash flow TTM, cash, total debt, gross margin, shares outstanding, float; quarterly table (SEC XBRL) of up to 8 quarters; share-count change over about a year |
| Section 5 | Yahoo analyst consensus (mean, label, number of analysts) |
| Section 8 | "Laut Yahoo": short quote of the float, institutional and insider share |
| Section 15 | new, last: SEC filings of 90 days, excerpts of press releases and 8-K texts (external data) |

**Compatibility:** `pack_format` stays 2. Sections 1-14 keep their numbers and headings, existing lines keep their wording, the only change to an existing line group is that the new lines follow the old ones inside sections 4, 5 and 8, and `data_freshness` only gained keys. The screen (`screen.csv`, buckets, percentile groups) is untouched: none of the new data is a screen input.
