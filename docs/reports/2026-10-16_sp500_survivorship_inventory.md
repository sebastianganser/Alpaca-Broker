# S&P 500 survivorship inventory (2026-10-16)

Read-only inventory; nothing in the database, code or configuration was changed. DB access only through `SET TRANSACTION READ ONLY`
connections; Alpaca access only GET (assets, daily bars with the collector's parameters `feed=sip`, `adjustment=all`, corporate actions).
Companion file: `docs/reports/2026-10-16_sp500_removals.csv` (one row per removal/rename, all collected fields).
Data window start = `data_start_date()` = 2021-10-01. "Real removal" = the company left the index; a ticker rename is not an exit.

## 1. Summary numbers

| Item | Value |
|---|---|
| S&P 500 removal events 2021-01-01 .. 2026-10-16 (union Wikipedia 108 + GitHub 122, de-duplicated) | **128** |
| of which real exits / merger successors / ticker renames | 104 / 3 (DISCA, DISCK, PARA) / 21 (19 only in GitHub; WLTW->WTW and CDAY->DAY also in Wikipedia) |
| Removals before the window (2021-01-01 .. 2021-09-30) | 16 (15 real, 1 rename LB->BBWI); cannot affect stored data |
| **Real removals inside the window (>= 2021-10-01)** | **92** (89 real exits + 3 merger successors) |
| ... with any `sp500` interval in `index_membership` | 6 (TAP, TTD, BLDR closed 10 days late; CTVA, WBD still open; ILMN only the 2026 re-entry) |
| ... with **no** `sp500` interval at all | 87 |
| ... present in `signals.universe` | 23 (22 with complete prices; PARA holds the wrong company, see 4.3) |
| ... absent from `signals.universe` | 69 (FBHS is covered by its successor symbol FBIN, so 68 inserts) |
| ... with Alpaca daily bars for the whole membership period | 92 of 92 (4 need a non-default call: DAY, PARA, INFO, FBHS) |
| ... with no obtainable price data | 0 |
| PIT S&P 500 set in DB vs truth (S&P only, entity level) | differs on 1,242 of 1,265 trading days |
| ... example counts | 2022-01-03: DB 426 vs truth 505; 2024-01-03: 456 vs 503; 2026-01-03: 490 vs 503; gap down to 1-2 members from 2026-07 |
| Missing (ticker, day) pairs in the PIT universe | 55,275 (50,532 without an existing `feature_snapshots` row) |
| Wrong `valid_from` among the 416 seeded members | 1 (PSKY); 13 are rename successors and correct by entity; plus SW (too late) |

## 2. Root cause

1. **The changes table is no longer on the fetched page.** `wikipedia_index_history.py:64` fetches `List_of_S%26P_500_companies`;
   `pd.read_html` (line 90) now returns only 2 tables (constituents, navbox). The "Selected changes" table moved to
   `Historical_components_of_the_S%26P_500` (confirmed on 2026-10-16; commit `8b7709e` of 2026-08-28 already noted that it was gone).
2. **Silent fallback to additions only.** `_find_changes_table` (lines 219-261) returns `None`, so `parse_sp500_changes` takes
   Strategy 2 (lines 107-127, `_parse_sp500_date_added` lines 129-179). That path reads the "Date added" column of the
   current-members table: it yields additions of current members only (80 changes since 2021-10-01, no removals), and the logger
   even says "no removal data available" at INFO level. Anything that left the index is never seen.
3. **A second latent bug would hit even with the right URL.** Lines 233-237 flatten a MultiIndex with `get_level_values(-1)`, which keeps
   only the sub-headers (`Ticker`, `Security`, ...) and drops the group names `Added`/`Removed`; `has_added`/`has_removed` (lines 245-252)
   are then always false. Verified: `_find_changes_table` returns `None` on the new page as well. `_parse_sp500_changes_df`
   itself works on the new table (184 changes since 2021-10-01, 93 removals).
4. **The GitHub backup cannot rescue it.** `GITHUB_SP500_CSV_URL` (lines 33-36) points to the original file, whose last row is 2019-01-11, so
   `GitHubSP500Backup.fetch_changes` returns 0 changes since the window start (verified). The maintained file is
   `S&P 500 Historical Components & Changes (Updated).csv` (last row 2026-08-18; with it the project parser gives 210 changes, 106 removals,
   renames included as exits). The seed route `operations.py:341` only uses GitHub if Wikipedia returns < 20 changes; the 80-90
   additions from the fallback are above that, so GitHub was never consulted.
5. **Seeding then fills the gap with `initial_seed`.** `generate_intervals_from_changes` (lines 638-657) gives every current member without an
   event an open interval from `data_start` (2021-01-01 at seeding time): 416 `initial_seed` rows, 90 dated additions, 0 removals.
6. **Why `IndexSyncer` closed only 3 intervals.** `_update_membership_intervals` (`index_sync.py:214-285`) can only close intervals that are
   *open in the DB* (line 261: `previously_active - current_for_index`). The DB contains only members that were current at the seed
   (run after 2026-08-18, the latest dated addition), so the sync can only see changes after the seed; the first monthly run after the 2026-09-21 rebalance (2026-10-01) closed
   TAP, TTD, BLDR (and opened BE, P, ILMN). `_fetch_sp500` (line 296) reads `tables[0]` only, i.e. the current list, no history. It
   stamps `valid_to`/`valid_from` with `date.today()` (lines 228, 254, 274) instead of the effective date (10 days late here), and
   it treats a rename as removal + addition (next case: PSKY->SKYD, see 7).

## 3. Ground truth and reconciliation

Sources: Wikipedia "Historical components of the S&P 500" (121 rows since 2021-01-01 = 106 additions, 108 removals) and the diffs of the
fja05680 `(Updated)` CSV (snapshots 2020-12-21 .. 2026-08-18; 120 additions, 122 removals). Check: GitHub snapshot + Wikipedia events after
2026-08-18 reproduce the current Wikipedia constituents (503) exactly, except PSKY->SKYD (rename of 2026-10-06, Alpaca `name_change`, in neither source).

| Reconciliation result | Count | Detail |
|---|---|---|
| Events identical (date, ticker, action) | 196 | |
| Same event, date differs | 6 | Wikipedia later than GitHub: XRAY +1 d, VFC +2 d, DXC +1 d, VNO +1 d, GPS +1 d; OGN addition 1 d earlier. Wikipedia (effective date) used. |
| Wikipedia only | 12 | MBC add 2022-12-15 / remove 2022-12-19 (4-day member, between two GitHub snapshots); 10 events after GitHub's last snapshot: 2026-09-21 BE, P, ILMN added / TAP, TTD, BLDR removed; 2026-10-01 VYLR added; 2026-10-06 TWLO added / WBD, CTVA removed |
| GitHub only | 40 (= 20 pairs) | 19 ticker renames (LB, COG, VIAC, BLL, FB, ANTM, NLOK, PKI, FISV, RE, ABC, PEAK, FLT, WRK, FI, MMC, BK, SATS, EQR) plus PARA->PSKY (merger) |
| In neither | 1 | PSKY->SKYD (2026-10-06) |

Rename evidence: Alpaca `name_change` corporate actions (same CUSIP except FLT->CPAY) and the Wikipedia reason text "changed its ticker symbol".
FBHS is a special case: renamed FBIN on 2022-12-15 (Alpaca), but the company left the index on 2022-12-19, so it is a real exit.
Reasons among the 92 real removals: market-cap change 58, M&A 28, merger successor 3, bank failure 3 (SIVB, SBNY, FRC).

## 4. Our state per category

### 4.1 Real removals in the window, ticker present in `universe` (23)

All 23 have complete non-extrapolated price rows for the membership period (DB bars = Alpaca bars). `uni active` is the `universe.is_active` flag.
Only rows with a `sp500` interval need an UPDATE; for the others an INSERT is enough.
PARA is flagged: the stored series is the wrong company (4.3).

| Removal | Ticker | Company | Why | uni active | DB bars (period) | Alpaca bars (period) | sp500 rows in DB |
|---|---|---|---|---|---|---|---|
| 2022-02-15 | XLNX | Xilinx | M&A | n | 93 | 93 | - |
| 2022-06-08 | CERN | Cerner | M&A | n | 172 | 172 | - |
| 2023-10-18 | OGN | Organon & Co. | mkt cap | y | 514 | 514 | - |
| 2023-10-18 | ATVI | Activision Blizzard | M&A | n | 512 | 512 | - |
| 2024-06-24 | ILMN | Illumina | mkt cap | y | 684 | 684 | 2026-10-01..open(index_sync) |
| 2025-07-18 | ANSS | Ansys | M&A | n | 950 | 950 | - |
| 2025-08-08 | PARA | Paramount Global (Class B) | merger (successor) | y | 966 | 966 | - |
| 2025-08-28 | WBA | Walgreens Boots Alliance | M&A | n | 980 | 980 | - |
| 2025-09-22 | ENPH | Enphase Energy | mkt cap | n | 996 | 996 | - |
| 2025-12-22 | SOLS | Solstice Advanced Materials | mkt cap | y | 36 | 36 | - |
| 2026-03-23 | MTCH | Match Group | mkt cap | n | 1121 | 1121 | - |
| 2026-05-07 | CTRA | Coterra Energy | M&A | n | 1153 | 1153 | - |
| 2026-06-02 | EPAM | EPAM Systems | mkt cap | y | 1119 | 1119 | - |
| 2026-06-22 | POOL | Pool | mkt cap | y | 1183 | 1183 | - |
| 2026-06-22 | CPB | Campbell's | mkt cap | y | 1183 | 1183 | - |
| 2026-06-30 | CAG | Conagra Brands | mkt cap | y | 1189 | 1189 | - |
| 2026-08-05 | EA | Electronic Arts | M&A | n | 1214 | 1214 | - |
| 2026-08-18 | AVB | AvalonBay Communities | M&A | n | 1222 | 1222 | - |
| 2026-09-21 | TTD | The Trade Desk | mkt cap | y | 295 | 295 | 2025-07-18..2026-10-01(wikipedia) |
| 2026-09-21 | TAP | Molson Coors | mkt cap | y | 1246 | 1246 | 2021-01-01..2026-10-01(initial_seed) |
| 2026-09-21 | BLDR | Builders FirstSource | mkt cap | y | 690 | 690 | 2023-12-18..2026-10-01(wikipedia) |
| 2026-10-06 | WBD | Warner Bros. Discovery | M&A | n | 1125 | 1125 | 2022-04-11..open(wikipedia) |
| 2026-10-06 | CTVA | Corteva | mkt cap | y | 1257 | 1257 | 2021-01-01..open(initial_seed) |

### 4.2 Real removals in the window, ticker absent from `universe` (69)

Alpaca bars are for the membership period, plain call as the collector does it (`feed=sip`, `adjustment=all`, no `asof`).
Special cases: DAY (plain call returns only 2024-02-01 onwards; needs CDAY for 2021-09-20 .. 2024-01-31, 586 + 503 = 1,089 bars), INFO (2 extra bars of an
unrelated ETF that reused the ticker; use `asof`, 102 bars), FBHS (renamed FBIN before removal, no Alpaca asset record; FBIN is already in the DB with
complete prices, so no backfill), GPS (asset 404 but bars exist; Alpaca symbol today: GAP). Alpaca asset 404 also for SIVB, FRC (bars exist).
Alpaca asset status over all 92: active 54, inactive 34, not found 4.

| Removal | Ticker | Company | Why | Alpaca asset | Alpaca bars (first..last) | Backfill bars |
|---|---|---|---|---|---|---|
| 2021-12-14 | KSU | Kansas City Southern | M&A | inactive | 51 (2021-10-01..2021-12-13) | 51 |
| 2021-12-20 | HBI | Hanesbrands | mkt cap | inactive | 55 (2021-10-01..2021-12-17) | 55 |
| 2021-12-20 | WU | Western Union | mkt cap | active | 55 (2021-10-01..2021-12-17) | 55 |
| 2021-12-20 | LEG | Leggett & Platt | mkt cap | inactive | 55 (2021-10-01..2021-12-17) | 55 |
| 2022-02-03 | GPS | Gap | mkt cap | HTTP404 | 86 (2021-10-01..2022-02-02) | 86 |
| 2022-03-02 | INFO | IHS Markit | M&A | active | 104 (2021-10-01..2022-03-01) | 102 |
| 2022-04-04 | PBCT | People's United Financial | M&A | inactive | 127 (2021-10-01..2022-04-01) | 127 |
| 2022-04-11 | DISCK | Discovery | merger (successor) | inactive | 132 (2021-10-01..2022-04-08) | 132 |
| 2022-04-11 | DISCA | Discovery | merger (successor) | inactive | 132 (2021-10-01..2022-04-08) | 132 |
| 2022-06-21 | UAA | Under Armour (Class A) | mkt cap | active | 180 (2021-10-01..2022-06-17) | 180 |
| 2022-06-21 | UA | Under Armour (Class C) | mkt cap | active | 180 (2021-10-01..2022-06-17) | 180 |
| 2022-06-21 | IPGP | IPG Photonics | mkt cap | active | 180 (2021-10-01..2022-06-17) | 180 |
| 2022-09-19 | PVH | PVH | mkt cap | active | 242 (2021-10-01..2022-09-16) | 242 |
| 2022-09-19 | PENN | Penn Entertainment | mkt cap | active | 242 (2021-10-01..2022-09-16) | 242 |
| 2022-10-03 | DRE | Duke Realty | M&A | inactive | 252 (2021-10-01..2022-09-30) | 252 |
| 2022-10-03 | CTXS | Citrix Systems | M&A | inactive | 251 (2021-10-01..2022-09-29) | 251 |
| 2022-10-12 | NLSN | Nielsen Holdings | M&A | inactive | 259 (2021-10-01..2022-10-11) | 259 |
| 2022-11-01 | TWTR | Twitter | M&A | inactive | 271 (2021-10-01..2022-10-27) | 271 |
| 2022-12-19 | MBC | MasterBrand | mkt cap | active | 2 (2022-12-15..2022-12-16) | 2 |
| 2022-12-19 | FBHS | Fortune Brands Home & Security | mkt cap | HTTP404 | 304 (2021-10-01..2022-12-14) | 0 |
| 2022-12-22 | ABMD | Abiomed | M&A | inactive | 309 (2021-10-01..2022-12-21) | 309 |
| 2023-01-05 | VNO | Vornado Realty Trust | mkt cap | active | 317 (2021-10-01..2023-01-04) | 317 |
| 2023-03-15 | SIVB | SVB Financial Group | bank failure | HTTP404 | 361 (2021-10-01..2023-03-09) | 361 |
| 2023-03-15 | SBNY | Signature Bank | bank failure | active | 309 (2021-12-20..2023-03-14) | 309 |
| 2023-03-20 | LUMN | Lumen Technologies | mkt cap | active | 367 (2021-10-01..2023-03-17) | 367 |
| 2023-05-04 | FRC | First Republic Bank | bank failure | HTTP404 | 396 (2021-10-01..2023-04-28) | 396 |
| 2023-06-20 | DISH | Dish Network | mkt cap | inactive | 430 (2021-10-01..2023-06-16) | 430 |
| 2023-08-25 | AAP | Advance Auto Parts | M&A | active | 477 (2021-10-01..2023-08-24) | 477 |
| 2023-09-18 | NWL | Newell Brands | mkt cap | active | 492 (2021-10-01..2023-09-15) | 492 |
| 2023-09-18 | LNC | Lincoln National | mkt cap | active | 492 (2021-10-01..2023-09-15) | 492 |
| 2023-10-03 | DXC | DXC Technology | mkt cap | active | 503 (2021-10-01..2023-10-02) | 503 |
| 2023-12-18 | ALK | Alaska Air Group | mkt cap | active | 556 (2021-10-01..2023-12-15) | 556 |
| 2023-12-18 | SEE | Sealed Air | mkt cap | inactive | 556 (2021-10-01..2023-12-15) | 556 |
| 2023-12-18 | SEDG | SolarEdge | mkt cap | active | 501 (2021-12-20..2023-12-15) | 501 |
| 2024-03-18 | ZION | Zions Bancorporation | mkt cap | active | 617 (2021-10-01..2024-03-15) | 617 |
| 2024-03-18 | WHR | Whirlpool | mkt cap | active | 617 (2021-10-01..2024-03-15) | 617 |
| 2024-04-03 | XRAY | Dentsply Sirona | mkt cap | active | 628 (2021-10-01..2024-04-02) | 628 |
| 2024-04-03 | VFC | VF | mkt cap | active | 628 (2021-10-01..2024-04-02) | 628 |
| 2024-05-08 | PXD | Pioneer Natural Resources | M&A | inactive | 650 (2021-10-01..2024-05-02) | 650 |
| 2024-06-24 | RHI | Robert Half | mkt cap | active | 684 (2021-10-01..2024-06-21) | 684 |
| 2024-06-24 | CMA | Comerica | mkt cap | inactive | 684 (2021-10-01..2024-06-21) | 684 |
| 2024-09-23 | ETSY | Etsy | mkt cap | active | 747 (2021-10-01..2024-09-20) | 747 |
| 2024-09-23 | BIO | Bio-Rad Laboratories | mkt cap | active | 747 (2021-10-01..2024-09-20) | 747 |
| 2024-09-23 | AAL | American Airlines Group | mkt cap | active | 747 (2021-10-01..2024-09-20) | 747 |
| 2024-10-01 | BBWI | Bath & Body Works | mkt cap | active | 753 (2021-10-01..2024-09-30) | 753 |
| 2024-11-26 | MRO | Marathon Oil | M&A | inactive | 791 (2021-10-01..2024-11-21) | 791 |
| 2024-12-23 | QRVO | Qorvo | mkt cap | inactive | 811 (2021-10-01..2024-12-20) | 811 |
| 2024-12-23 | CTLT | Catalent | M&A | inactive | 808 (2021-10-01..2024-12-17) | 808 |
| 2024-12-23 | AMTM | Amentum | mkt cap | active | 59 (2024-09-30..2024-12-20) | 59 |
| 2025-03-24 | CE | Celanese | mkt cap | active | 871 (2021-10-01..2025-03-21) | 871 |
| 2025-03-24 | BWA | BorgWarner | mkt cap | active | 871 (2021-10-01..2025-03-21) | 871 |
| 2025-03-24 | TFX | Teleflex | mkt cap | active | 871 (2021-10-01..2025-03-21) | 871 |
| 2025-03-24 | FMC | FMC | mkt cap | active | 871 (2021-10-01..2025-03-21) | 871 |
| 2025-05-19 | DFS | Discover Financial | M&A | inactive | 910 (2021-10-01..2025-05-16) | 910 |
| 2025-07-09 | JNPR | Juniper Networks | M&A | inactive | 940 (2021-10-01..2025-07-01) | 940 |
| 2025-07-23 | HES | Hess | M&A | inactive | 951 (2021-10-01..2025-07-17) | 951 |
| 2025-09-22 | MKTX | MarketAxess | mkt cap | active | 996 (2021-10-01..2025-09-19) | 996 |
| 2025-09-22 | CZR | Caesars Entertainment | mkt cap | active | 996 (2021-10-01..2025-09-19) | 996 |
| 2025-10-31 | KMX | CarMax | mkt cap | active | 1025 (2021-10-01..2025-10-30) | 1025 |
| 2025-11-04 | EMN | Eastman Chemical Co. | mkt cap | active | 1027 (2021-10-01..2025-11-03) | 1027 |
| 2025-11-28 | IPG | Interpublic Group | M&A | inactive | 1044 (2021-10-01..2025-11-26) | 1044 |
| 2025-12-11 | K | Kellanova | M&A | inactive | 1053 (2021-10-01..2025-12-10) | 1053 |
| 2025-12-22 | MHK | Mohawk Industries | mkt cap | active | 1060 (2021-10-01..2025-12-19) | 1060 |
| 2025-12-22 | LKQ | LKQ | mkt cap | active | 1060 (2021-10-01..2025-12-19) | 1060 |
| 2026-02-09 | DAY | Dayforce | M&A | inactive | 503 (2024-02-01..2026-02-03) | 1089 |
| 2026-03-23 | PAYC | Paycom | mkt cap | active | 1121 (2021-10-01..2026-03-20) | 1121 |
| 2026-03-23 | MOH | Molina Healthcare | mkt cap | active | 1017 (2022-03-02..2026-03-20) | 1017 |
| 2026-03-23 | LW | Lamb Weston | mkt cap | active | 1121 (2021-10-01..2026-03-20) | 1121 |
| 2026-04-09 | HOLX | Hologic | M&A | inactive | 1131 (2021-10-01..2026-04-06) | 1131 |

### 4.3 Wrong company stored under PARA

`universe.PARA` is Banzai International (added 2026-08-09 by `politician_trades`, active). The plain Alpaca call `PARA` returns Banzai's history
(e.g. close 89,043.51 on 2021-10-01; 735 stored `PARA` rows have close > 1,000). Paramount Global is only reachable with `symbol=VIAC`
(2021-10-01 .. 2022-02-16, 96 bars) plus `symbol=PARA&asof=2022-03-01` (2022-02-17 .. 2025-08-06, 869 bars). A `PARA` membership interval would therefore pull
Banzai prices into features; an alias key is required. PSKY itself starts 2025-08-07 (292 rows).

### 4.4 Ticker renames (21): not exits

| Date | Old -> new | Evidence | In Wikipedia table | DB state of the new ticker (sp500) |
|---|---|---|---|---|
| 2021-08-03 | LB -> BBWI | manual: L Brands renamed Bath & Body Works (Alpaca name_change BBWI.WI->BBWI 2021-08-03) | no (GitHub only) | none |
| 2021-10-04 | COG -> CTRA | Alpaca name_change COG->CTRA 2021-10-04 | no (GitHub only) | none |
| 2022-01-10 | WLTW -> WTW | wikipedia reason: ticker symbol change | yes | 2021-01-01..open |
| 2022-02-17 | VIAC -> PARA | Alpaca name_change VIAC->PARA 2022-02-17 | no (GitHub only) | none |
| 2022-05-10 | BLL -> BALL | Alpaca name_change BLL->BALL 2022-05-10 | no (GitHub only) | 2021-01-01..open |
| 2022-06-09 | FB -> META | Alpaca name_change FB->META 2022-06-09 | no (GitHub only) | 2021-01-01..open |
| 2022-06-28 | ANTM -> ELV | Alpaca name_change ANTM->ELV 2022-06-28 | no (GitHub only) | 2021-01-01..open |
| 2022-11-08 | NLOK -> GEN | Alpaca name_change NLOK->GEN 2022-11-08 | no (GitHub only) | 2021-01-01..open |
| 2023-05-16 | PKI -> RVTY | Alpaca name_change PKI->RVTY 2023-05-16 | no (GitHub only) | 2021-01-01..open |
| 2023-06-07 | FISV -> FI | Alpaca name_change FISV->FI 2023-06-07 | no (GitHub only) | none |
| 2023-07-10 | RE -> EG | Alpaca name_change RE->EG 2023-07-10 | no (GitHub only) | 2021-01-01..open |
| 2023-08-30 | ABC -> COR | Alpaca name_change ABC->COR 2023-08-30 | no (GitHub only) | 2021-01-01..open |
| 2024-02-01 | CDAY -> DAY | wikipedia reason: ticker symbol change | yes | none |
| 2024-03-04 | PEAK -> DOC | Alpaca name_change PEAK->DOC 2024-03-04 | no (GitHub only) | 2021-01-01..open |
| 2024-03-25 | FLT -> CPAY | Alpaca name_change FLT->CPAY 2024-03-25 (cusip changed) | no (GitHub only) | 2021-01-01..open |
| 2024-07-08 | WRK -> SW | Alpaca name_change WRK->SW 2024-07-08 | no (GitHub only) | 2024-07-08..open |
| 2025-11-11 | FI -> FISV | Alpaca name_change FI->FISV 2025-11-11 | no (GitHub only) | 2021-01-01..open |
| 2026-01-14 | MMC -> MRSH | Alpaca name_change MMC->MRSH 2026-01-14 | no (GitHub only) | 2021-01-01..open |
| 2026-05-21 | BK -> BNY | Alpaca name_change BK->BNY 2026-05-21 | no (GitHub only) | 2021-01-01..open |
| 2026-06-24 | SATS -> ECHO | Alpaca name_change SATS->ECHO 2026-06-24 | no (GitHub only) | 2026-03-23..open |
| 2026-08-18 | EQR -> VMRK | Alpaca name_change EQR->VMRK 2026-08-18 | no (GitHub only) | 2021-01-01..open |

The seeded successor tickers (BALL, BNY, COR, CPAY, DOC, EG, ELV, GEN, META, MRSH, RVTY, VMRK, WTW, FISV) are correct by entity (Alpaca returns the
full history under the new symbol), so old symbols (FB, BK, ...) must **not** be added; several of them are reused by unrelated securities today
(FB = ProShares ETF, LB = LandBridge, PARA = Banzai). Exceptions: SW (WRK->SW) starts 2024-07-08 in the DB but WestRock was a member since before 2021
(correct start 2021-01-01, if WRK->SW is treated as a rename; see open questions). `universe` already holds inactive duplicates of old symbols (BK, EQR, SATS, FI) from earlier runs.

### 4.5 Removals before the window (16, listed for completeness)

| Removal | Ticker | Company | Why |
|---|---|---|---|
| 2021-01-07 | TIF | Tiffany & Co | M&A |
| 2021-01-21 | CXO | Concho Resources | M&A |
| 2021-02-12 | FTI | TechnipFMC | spin-off |
| 2021-03-22 | FLS | Flowserve | mkt cap |
| 2021-03-22 | SLG | SL Green Realty | mkt cap |
| 2021-03-22 | VNT | Vontier | mkt cap |
| 2021-03-22 | XRX | Xerox | mkt cap |
| 2021-04-20 | VAR | Varian Medical Systems | M&A |
| 2021-05-14 | FLIR | FLIR Systems | M&A |
| 2021-06-04 | HFC | HollyFrontier | mkt cap |
| 2021-07-21 | ALXN | Alexion Pharmaceuticals | M&A |
| 2021-08-03 | LB | L Brands (now Bath & Body Works) | rename -> BBWI |
| 2021-08-30 | MXIM | Maxim Integrated | M&A |
| 2021-09-20 | NOV | Nov | mkt cap |
| 2021-09-20 | PRGO | Perrigo | mkt cap |
| 2021-09-20 | UNM | Unum | mkt cap |

## 5. Additions side (416 members with `valid_from = 2021-01-01`)

* Of the 416 `initial_seed` rows (415 open + TAP), 402 were members on 2020-12-21 (GitHub snapshot) and are correct.
* 14 were not in that snapshot: 13 are rename successors of members (BALL, BNY, COR, CPAY, DOC, EG, ELV, GEN, META, MRSH, RVTY, VMRK, WTW) and are correct by entity;
  **1 is genuinely wrong: PSKY** (`valid_from` 2021-01-01, truth 2025-08-08; PSKY has prices only from 2025-08-07, so it adds 966 phantom member days; the real member is PARA).
* The 87 `wikipedia` rows (85 open + TTD, BLDR closed) match the ground-truth dates (84 of 85 open ones); **SW** is wrong in the other direction
  (`valid_from` 2024-07-08, truth 2021-01-01 as WRK).
* The 3 `index_sync` additions BE, P, ILMN start 2026-10-01; truth 2026-09-21 (10 days late; same lag as the TAP/TTD/BLDR closures).
* So lookahead on the addition side is small: 1 wrong start (PSKY, 966 days), 1 too late (SW, about 700 trading days inside the window), 3 x 8-10 days late. The bias is almost entirely on the removal side.

## 6. Effort estimate for the repair

| Step | Scope |
|---|---|
| `index_membership` INSERTs (`sp500`) | **88**: 87 for the 92 minus 5 that already have a row (TAP, TTD, BLDR, CTVA, WBD), plus DAY split into CDAY + DAY |
| `index_membership` UPDATEs | **10**: valid_to for TAP, TTD, BLDR (2026-10-01 -> 2026-09-21) and CTVA, WBD (open -> 2026-10-06); valid_from for BE, P, ILMN (-> 2026-09-21), SW (-> 2021-01-01), PSKY (-> 2025-08-08) |
| Pending monthly-sync changes (2026-11-01 run) | TWLO and VYLR additions, SKYD (rename of PSKY) would be dated with the sync date; PSKY would be closed as an exit |
| `universe` INSERTs (inactive) | 68 absent tickers + CDAY + VIAC = **70** (plus an alias key for Paramount's PARA); FBHS uses FBIN |
| Price backfill | **69 tickers, 39,881 bars** (about +4 % on 954,558 rows in `prices_daily`): 68 absent tickers (38,916 bars incl. DAY 1,089 and INFO 102) and the PARA/VIAC replacement series (965 bars). Roughly 10-20 Alpaca requests (10,000 bars per page, `feed=sip`, `adjustment=all`), a few minutes |
| Tickers without obtainable price data | **0** of 92 (coverage >= 98.9 % of expected sessions, XLNX being the lowest at 93 of 94; DAY only with the CDAY detour) |
| Derived data for new tickers | `technical_indicators` full backfill for about 70 tickers; targets recompute for them; the backfilled delisted tickers will have 12 of 108 feature columns empty (13F, earnings, sector-relative, `st_earnings_reaction`), as ATVI does today |
| Feature recompute | `feature_snapshots` dates 2021-10-01 .. 2026-09-30: **1,242 of 1,265 dates differ**, i.e. a full rebuild. +50,532 new ticker-day rows (+8 % on 626,765), so about 4.3 h at the last rebuild's pace (the `features_rebuild.py` docstring says 7-12 h). The per-ticker features of existing rows do not change; only `breadth_advance_decline` and `breadth_pct_above_sma50` depend on the universe, so a targeted path (compute the 50.5k new rows + recompute breadth per date) would be much shorter but does not exist as code |
| Parser/syncer fixes (outside the data repair) | Point the parser at `Historical_components_of_the_S%26P_500`, fix the MultiIndex flattening, use Wikipedia effective dates and Alpaca `name_change` in `IndexSyncer`, update the GitHub URL to the `(Updated)` file, fail loudly (not fall back) when the changes table is missing |

## 7. Open questions and uncertainties

1. **Entity vs ticker key.** The PIT table is keyed by ticker, prices too. Decisions needed for DAY/CDAY (two intervals vs one), PARA/VIAC (alias for Paramount because PARA now means Banzai),
   FBHS (use FBIN), and the coming PSKY->SKYD rename. Which key do you want for the CSV `successor_ticker` / alias logic?
2. **WRK->SW and DISCA/DISCK/PARA classification.** Alpaca lists WRK->SW as a `name_change` (same CUSIP), I treated it as a rename (SW starts 2021-01-01). If you see it as a merger (new entity), SW's
   2024-07-08 start stands and WRK becomes a 93rd real exit (about 700 bars to backfill). DISCA/DISCK/PARA are counted as exits (they have their own price history) with successor WBD/PSKY.
3. **Dates.** GitHub dates are snapshot dates and differ by 1-2 days from Wikipedia effective dates in 6 cases; I used Wikipedia. Intervals follow the DB convention `[valid_from, valid_to)`, so the last member day is the day before the effective date.
4. **Post-2026-08-18 events** (10 events) rest on Wikipedia alone; Alpaca bars/corporate actions are consistent (TAP/TTD/BLDR trade until 2026-09-18, WBD cash merger effective 2026-10-06), but there is no second index source.
5. **Existing price quality of delisted backfills.** `alpaca_delisted_backfill` rows (e.g. ANSS, ATVI, CERN) were fetched with `feed=iex` (`scripts/archive/backfill_delisted_prices.py:149`), while the collector uses SIP.
   For consistency the new backfill should use SIP; I did not compare IEX and SIP series.
6. **Adjusted prices of renamed symbols.** For FB/META the same date returned different adjusted closes depending on the symbol and `asof` (339.76 / 335.33 / 332.15 on 2021-10-01); I did not investigate whether `prices_daily` has such cross-symbol drift.
7. **Non-price source tables** (Form 4, 13F, news, earnings, ARK) were not examined for the 70 tickers; only prices and derived feature columns were assessed.
8. **Retention.** The 4 exits of Q4 2021 (KSU, HBI, WU, LEG) drop out of the window on 2027-01-01, so a repair after that date is smaller.

## 8. Repair runbook

Code written 2026-10-16; steps 2a, 2b, 3 and 8 ran against production on 2026-10-17 (see [Execution log 2026-10-17](#execution-log-2026-10-17) at the end of this section) - the repair is complete. Tested with unit tests (mocked HTTP, no DB) and end to end on a
throwaway Postgres 18 (all 36 migrations, synthetic pre-repair state that reproduces the inventory numbers: 416/426/441/456/472/490 members
at the year starts, live read-only Wikipedia and Alpaca calls). The numbers below are derived from this inventory and that
test; **the dry run on production is authoritative** - compare it with this table before every `--apply`.

| Step | Script | What it does |
|---|---|---|
| 2a | `scripts/repair/rebuild_sp500_membership.py` | `sp500` rows of `index_membership` = ground truth (current constituents + change table since 2021-01-01 + manual PARA/SKYD events); renames = one interval under the current symbol |
| 2b | `scripts/repair/onboard_removed_members.py` | inactive `universe` rows, price backfill from Alpaca (`source='alpaca_backfill'`), technical indicators; `--fix-para` repairs the PARA/Banzai collision; `--add-current-members` also inserts missing current members (active) and reactivates inactive ones (BF.B) |
| 3 | `scripts/repair/features_rebuild.py` (existing) | full feature rebuild on the corrected point-in-time universe |

Shared helpers: `src/trading_signals/utils/repair_support.py` (resolved DB target, read-only dry-run session, verified CSV backup).
Every script prints `database target: host:port/db` first, is a dry run by default (`SET TRANSACTION READ ONLY`), writes one transaction
per logical step with `--apply`, and is idempotent (a second `--apply` changes nothing).

### Preconditions

1. Commit, push and `alpaca-ops deploy` (new package module + scripts must be in the image). Run the scripts with
   `alpaca-ops run scripts/repair/<script>.py [args]` (refused 22:15-05:15; arguments may only contain `A-Za-z0-9_.,:=/@+-`).
2. Not on the 1st of a month around 03:00 (`index_sync`; the rebuild also locks the table), not on a Sunday 03:00 (`data_retention`),
   not while a feature rebuild or the nightly chain (04:30-05:15) writes.
3. The container needs the Alpaca keys (market-data GETs) and internet access (Wikipedia) even for the dry runs.

### Order of runs

| # | Command | Window | Expected |
|---|---|---|---|
| 1 | `rebuild_sp500_membership.py` (dry run) | any time before 22:15 | numbers in 2a below, `database target` = `192.168.1.93:5435/broker_data` |
| 2 | `rebuild_sp500_membership.py --apply` | 05:15-22:15 | seconds; prints the backup path (`/mnt/user/Workfiles/AlpacaBroker/ops/sp500_membership_backup_<ts>.csv`) |
| 3 | `rebuild_sp500_membership.py` (dry run) | | `unchanged 610, INSERT 0, UPDATE 0, DELETE 0` |
| 4 | `onboard_removed_members.py --fix-para` (dry run; add `--add-current-members` if section [1] lists missing or inactive current members) | | numbers in 2b below; about 1-3 min (about 65 Alpaca requests) |
| 5 | same command with `--apply` | 05:15-22:15 | minutes; four `STEP` lines; CSV backups of the deleted PARA rows and the removed blacklist rows |
| 6 | same command as dry run | | every `write` is 0, PARA `already fixed`, 0 tickers for indicators |
| 7 | `alpaca-ops run-bg scripts/repair/features_rebuild.py --apply --skip-ta --skip-clusters` (detached - `run` would end with the popup; indicators of the new tickers were computed in step 5; insider clusters do not read the membership) | start 05:15-09:00 on a weekday (4-12 h), any daytime on Saturday/Sunday (no chain run until Tuesday 04:30) | progress: `alpaca-ops run-log`; resumable: `--start <last committed date>`; do not start it while the chain runs |
| 8 | re-run the analyses that read `index_membership` / `feature_snapshots`: `feature_analysis` (`POST /analysis/trigger`), `scripts/analysis/event_study.py` (index additions: BE/P/ILMN dates move by 10 days, SW is no addition any more), `walkforward_hit_model.py`, `backtest_barrier.py`, `sprint10_readiness.py` | after 7 | results change, see section 1 |

### Expected numbers

**2a - `rebuild_sp500_membership.py`**

| Item | Expected |
|---|---|
| Ground truth | 610 intervals = 503 open + 107 closed (92 exits inside the window incl. 3 merger successors, 15 before 2021-10-01); 210 change-table events since 2021-01-01 (104 added, 106 removed), 2 manual events (PARA exit and SKYD entry 2025-08-08) |
| Diff vs the 506 `sp500` rows | INSERT 105 (102 closed + TWLO, VYLR, SKYD), UPDATE 9 (valid_to: TAP, TTD, BLDR -> 2026-09-21, CTVA, WBD -> 2026-10-06; valid_from: BE, P, ILMN -> 2026-09-21, SW -> 2021-01-01), DELETE 1 (PSKY), unchanged 496 |
| Members per first session of the year (DB now -> after) | 2021-01-04 416 -> 505; 2022-01-03 426 -> 505; 2023-01-03 441 -> 503; 2024-01-02 456 -> 503; 2025-01-02 472 -> 503; 2026-01-02 490 -> 503 |
| All 1,265 sessions 2021-10-01..2026-10-15 | before min 425 / max 503, 1,121 sessions outside 495-510; after min 503 (2022-06-21) / max 506 (2022-02-02), 0 outside |
| Nasdaq-100 rows | untouched (every statement is restricted to `index_name = 'sp500'`) |

The 15 exits before the window (TIF, CXO, FTI, FLS, SLG, VNT, XRX, VAR, FLIR, HFC, ALXN, MXIM, NOV, PRGO, UNM) are inserted as well so that the
2021-01-04 count is right; they have no universe row and no price backfill (the onboarding ignores intervals that end before `data_start_date()`).
If the DB already holds the pending monthly-sync rows (TWLO, VYLR, SKYD) the INSERT count is smaller by that number. A different number of DELETEs
than 1 must be understood before `--apply` (the run refuses more than `--max-deletes 10`).

**2b - `onboard_removed_members.py --fix-para`**

| Item | Expected |
|---|---|
| Universe | INSERT 68 inactive rows (`added_by='sp500_history'`, names from the change table or the Alpaca asset); current members missing in the universe (SKYD, possibly TWLO, VYLR) are only listed unless `--add-current-members` (then inserted ACTIVE and priced) |
| Reactivation | BF.B (false `MUTUALFUND` blacklisting 2026-04-15) - only with `--add-current-members` (without it section [1] lists it as a hint): `universe.is_active` -> true, its `ticker_blacklist` row removed after a CSV backup (`ticker_blacklist_reactivated_<ts>.csv`); `STEP universe: 68 rows inserted, 1 reactivated, 1 blacklist rows removed` (plus the inserted current members in the first number). BF.B has no price rows and is in the price plan with or without the flag (the plan ignores `is_active` and the blacklist), so it is priced and gets indicators like the others. A second run plans 0 reactivations |
| Price plan | about 72 tickers: the 68 absent ones plus ANSS, ATVI, AVB, XLNX (1-2 tail sessions missing; `write 0` unless their rows come from the old IEX backfill) |
| Bars | about 39,130 for the 68 (inventory 38,916 plus 214 warm-up bars before the membership start of MOH 104, SBNY 55, SEDG 55) plus 965 for PARA = about 40,100 rows (+4.2 % of 954,558) |
| Gaps | no head and no internal gaps; tail gaps (delisted before the index removal, sessions with no bar to fetch): CTLT 3, CTXS 1, DAY 3, FRC 3, HES 3, HOLX 2, INFO 2, JNPR 4, MRO 2, PXD 3, SIVB 3, TWTR 2 (and 1-2 for ANSS, ATVI, AVB, XLNX) |
| PARA | 300 price rows deleted (299 dated >= 2025-08-08 plus the foreign 2025-08-07), about 299 indicator rows deleted, 965 Paramount rows written (close 9.44-36.23), universe row "Paramount Global (Class B)" inactive; other tables only counted |
| Indicators | recomputed for every ticker that received rows plus PARA (about 70 tickers, roughly 39,000 rows) |

**3 - feature rebuild** (expected; actual result: +49,541 rows, 676,953 snapshots, see the execution log): +50,532 ticker-day rows (about 677,300 snapshots in total), 1,242 of 1,265 dates change (`breadth_*` and the new rows); the new
delisted tickers have 12 of 108 feature columns empty (13F, earnings, sector-relative, `st_earnings_reaction`), like ATVI today.

### Verification

```sql
-- after 2a
select count(*) filter (where valid_to is null) as open_n, count(*) as total
from signals.index_membership where index_name = 'sp500';                       -- 503 / 610
select ticker, valid_from, valid_to from signals.index_membership
where index_name = 'sp500' and ticker in ('PSKY','SKYD','PARA','SW','TAP','ILMN','DAY','CDAY') order by 1, 2;
-- no PSKY, no CDAY; PARA 2021-01-01..2025-08-08; SKYD 2025-08-08..open; DAY 2021-09-20..2026-02-09
-- after 2b
select count(*), count(*) filter (where is_active) from signals.universe where added_by = 'sp500_history';   -- 68 / 0 (+ current members with the flag)
select count(*), count(distinct ticker) from signals.prices_daily where source = 'alpaca_backfill';          -- about 40,100 / about 69
select is_active from signals.universe where ticker = 'BF.B';                                               -- true (with --add-current-members)
select count(*) from signals.ticker_blacklist where ticker = 'BF.B';                                        -- 0
select ticker, company_name, is_active, sector, cusip from signals.universe where ticker = 'PARA';          -- Paramount Global (Class B), false, null, null
select count(*), min(trade_date), max(trade_date), max(close) from signals.prices_daily where ticker = 'PARA'; -- 965, 2021-10-01, 2025-08-06, ~36
select count(*) from signals.technical_indicators where ticker = 'PARA' and trade_date >= '2025-08-08';       -- 0
-- after 3 (point-in-time members that have a snapshot, e.g. 2022-01-03)
select count(distinct f.ticker) from signals.feature_snapshots f join signals.index_membership m
  on m.ticker = f.ticker and m.index_name = 'sp500' and m.valid_from <= f.snapshot_date
 and (m.valid_to is null or m.valid_to > f.snapshot_date)
where f.snapshot_date = '2022-01-03';                                                                       -- 505
```

The next monthly `index_sync` (2026-11-01) must find nothing to change for the S&P 500 (open intervals = current list): a result with renames,
additions or removals there means the Wikipedia list changed in between.

### Rollback

* **2a**: `rebuild_sp500_membership.py --restore <backup.csv> --apply` (the CSV holds all old `sp500` rows incl. ids; the current state is backed up again
  first). Check with the dry run: `unchanged 506`. The Nasdaq-100 rows are never touched.
* **2b**: delete by marker, in this order: `delete from signals.technical_indicators where ticker in (select ticker from signals.universe where added_by = 'sp500_history')`,
  `delete from signals.prices_daily where source = 'alpaca_backfill'` (note: this also removes the repaired Paramount series and, since the run of 2026-10-17, the 1,555 rows of ANSS/ATVI/XLNX that were overwritten IEX-to-SIP - the marker count 44,495 = 43,530 + 965 shows they carry it; their old IEX values are not backed up), `delete from signals.universe where added_by = 'sp500_history'`.
  The deleted PARA rows and the old PARA universe row are in `para_prices_daily_<ts>.csv`, `para_technical_indicators_<ts>.csv`, `para_universe_<ts>.csv` in the backup directory
  (Banzai's series; only needed to undo the fix, the data are wrong for PARA anyway).
  The removed `ticker_blacklist` row of BF.B is in `ticker_blacklist_reactivated_<ts>.csv` in the same directory; the reactivation itself is not rolled back (BF.B is a current S&P 500 member).
* **3**: the feature rebuild replaces rows day by day; there is no backup (no database backups by decision 2026-10-05). Re-running it is the recovery.

### Deviations from the brief and findings during the test

1. **`asof` = last membership day fails for INFO.** Alpaca: `INFO` with `asof=2022-03-01` returns 104 bars (2 of them the Harbor ETF that re-used the symbol on
   2022-02-28), `asof=2021-10-01` or `asof=2022-02-25` return the 102 IHS Markit bars. The index removal (2022-03-02) came after the delisting (last bar
   2022-02-25). The default is therefore `--asof first` (the first member day of each request, clamped to the symbol's period); `--asof last` is the literal rule.
   For all other tickers of the removal list both give the same bars (checked: CDAY, TWTR, PARA, VIAC, SIVB, GPS, FBHS, MBC; SOLS returns 8 extra when-issued bars
   with `asof=2025-10-30`). A full dry run on a throwaway DB reproduced the inventory's bar count of every absent ticker (differences = warm-up bars only).
2. Current members that are missing in `universe` (SKYD, maybe TWLO/VYLR) are not inserted as inactive rows (nobody would collect them): listed, or inserted ACTIVE with `--add-current-members`.
3. A rename is one interval under the current symbol also for DAY (the inventory proposed CDAY + DAY) and for FBHS (keyed FBIN); `universe` gets no CDAY/VIAC rows.
4. Intervals of the 15 exits before the window are inserted (they only matter for the 2021 year-start count).
5. The price backfill requests `[data_start_date(), last member day]` for every ticker, i.e. also the warm-up before a late membership start (needed for SMA200/RSI).
6. BF.B (Brown-Forman Class B, current member) is inactive in `universe` and in `ticker_blacklist` as `MUTUALFUND`/`manual_enrichment` (2026-04-15): the sector enrichment asked Yahoo for the raw symbol `BF.B` before `to_yahoo_symbol` mapped it to `BF-B`. `--add-current-members` reactivates it and drops the blacklist row; the sector enrichment no longer blacklists tickers with an open `index_membership` interval, so the next `index_sync` (which enriches active tickers without a sector) cannot undo it. It is the only false positive in the blacklist.

### Execution log 2026-10-17

Deploys: `771ab1d` 09:25-09:29 (step 1 code), `8de96a1` 09:42-09:44 (`--add-current-members`, index-member guard in `apply_sector_records`). `d03955c` (same guard in `NewTickerOnboarder`, benchmark ETFs never deactivated) was deployed with `f92fb28` at 17:20-17:22 (26 jobs, Alembic `036`). Backups are in `/mnt/user/Workfiles/AlpacaBroker/ops/` (share: `\\HOMENAS\Workfiles\AlpacaBroker\ops\`).

| Step | Time | Result |
|---|---|---|
| 2a dry run | before 09:35 | Matched the table above exactly: INSERT 105, UPDATE 9, DELETE 1 (PSKY), unchanged 496; 1,266 sessions, after the repair min 503 / max 506 members, 0 outside 495-510 |
| 2b dry run, *before* 2a was applied | | Found nothing to onboard - by design, the script reads `index_membership` (it warns "run rebuild --apply first"); only the PARA part is informative. It surfaced BF.B (finding 6 above) |
| 2a `--apply` | 09:35 (Sebastian's OK) | 1 deleted, 9 updated, 105 inserted, post-check OK; backup `sp500_membership_backup_20261017_093532.csv` |
| 2b dry run `--fix-para --add-current-members` | after 2a | universe INSERT 71 (68 inactive exits + SKYD, TWLO, VYLR active), REACTIVATE BF.B; prices 43,530 rows for 76 tickers; PARA 301 Banzai rows to delete, 965 Paramount rows to write; indicators for 76 tickers |
| 2b `--apply` | 09:51 (Sebastian's explicit OK) | `STEP universe: 71 rows inserted, 1 reactivated, 1 blacklist rows removed`; `STEP fix-para: prices_deleted 301, ta_deleted 301, prices_written 965`; `STEP prices: 43530 rows upserted for 75 tickers`; `STEP technical indicators: 76 tickers, 44405 rows, 0 errors`. Backups (all `_20261017_095146.csv`): `ticker_blacklist_reactivated`, `para_prices_daily`, `para_technical_indicators`, `para_universe` |
| 2a + 2b idempotency dry runs (runbook steps 3 and 6) | 10:13 | `rebuild_sp500_membership.py`: `unchanged 610, INSERT 0, UPDATE 0, DELETE 0`, all year starts and all 1,266 sessions 503-506; `onboard_removed_members.py --fix-para --add-current-members`: universe INSERT 0, no reactivation, prices `rows to write 0` (only the 16 tickers with the 37 known tail gaps left in the plan), PARA `already fixed` (965 rows, 0 with close > 1,000), indicators for 0 tickers. AVB is the 76th price-plan ticker with `write 0` (hence "75 tickers" in the 2b `STEP prices` line); PARA's 964 pre-exit indicator rows were recomputed in 2b (max SMA20 35.09) |
| 3 | 09:53-17:13, exit code 0 | `alpaca-ops run-bg scripts/repair/features_rebuild.py --apply --skip-ta --skip-clusters`; window 2021-10-01 to 2026-10-16 (1,266 sessions), first session 527 tickers; feature step until about 17:09 (about 20 s per session, not the estimated 15 s), targets for 768 tickers 17:09-17:13, about 7.3 h in total. `feature_snapshots` **676,953** rows (before 627,412, +49,541; the runbook expected about 677,300), 1,266 sessions 2021-10-01..2026-10-16, all `FEATURE_VERSION` 2026.10-3. Target coverage: `return_1d` 675,156, `return_20d` 661,453, `return_barrier_14d` 665,773 of 676,953 |
| deploy `f92fb28` | 17:20-17:22 | `alpaca-ops deploy`; contains `d03955c` (index-member guard in the onboarder, benchmark ETFs stay active); 26 jobs, Alembic `036` |
| 8 | analyses re-run on the repaired data (read-only, `alpaca-ops run-bg ... --out /mnt/user/Workfiles/AlpacaBroker/ops/reports_20261017`) | Reports: [event study index additions](2026-10-17_event_study_index_add.md) (110 labelled events, difference to the universe -0.320 %, before -0.321 %), [walk-forward hit model](2026-10-17_hit_model_walkforward.md) (557,275 snapshots, holdout AUC logit 0.511 / gbm 0.499, holdout 954 trades, difference +0.014 % n.s., still not passed), [barrier backtest](2026-10-17_barrier_backtest.md) (557,166 universe trades, no significantly positive net return, score approx. random); Sprint 10 readiness 29/31 GO (ML window 568,079 snapshots, 752 tickers). The 2026-10-06 reports are the pre-repair comparison. Monthly `feature_analysis` (Sebastian's explicit OK, `POST /api/v1/analysis/trigger` at 18:07, job `success` 18:07-18:10, 167 s): new report `report_date` 2026-10-17 (before 2026-10-06 with 527,085 snapshots); 568,079 snapshots, 752 tickers, 2022-07-28..2026-10-16, 87 features in the correlations, RF importance 86 features, LASSO selects nothing, no feature significant, hypotheses unchanged. First FINRA short-interest evaluation: `short_interest_days_to_cover` has a consistently negative daily rank IC (`return_20d` rho -0.029, t_NW -3.20; `return_60d` rho -0.044, t_NW -3.40), not significant after the Bonferroni correction ([LEARNINGS.md](../LEARNINGS.md) 2026-10-17) |

**Differences to the expected numbers above**

* Universe INSERT 71 instead of 68: SKYD, TWLO, VYLR were inserted active through `--add-current-members` (listed in the 2b table as "possibly").
* Price rows 43,530 (plus 965 PARA = 44,495 `alpaca_backfill` rows) instead of about 40,100: the 68 exits gave exactly the expected 39,130; on top came BF.B 1,266, TWLO 1,266 (full history as warm-up for a new active ticker), SKYD 301, VYLR 12 and 1,555 IEX-to-SIP overwrites (ANSS 950, ATVI 512, XLNX 93). The dry run planned 76 tickers, `--apply` reported rows upserted for 75.
* PARA: 301 rows deleted (300 dated >= 2025-08-08 plus the foreign 2025-08-07 row; indicator rows 301 as well) instead of about 300/299; 965 Paramount rows as expected.
* Tail gaps as expected: 16 tickers, 37 member-sessions without a bar.
* Snapshots +49,541 (676,953 in total) instead of the expected +50,532 (about 677,300): the difference of 991 rows was not analysed; coverage of the point-in-time members is complete (see "Verified afterwards").
* Feature step about 20 s per session instead of the estimated 15 s (about 7.3 h in total instead of 5.3 h + targets).

**Verified afterwards (read-only SQL)**

* `index_membership` `sp500`: 503 open / 610 total; PSKY and CDAY gone; PARA 2021-01-01..2025-08-08, SKYD 2025-08-08..open, DAY 2021-09-20..2026-02-09; Nasdaq-100 untouched (157 rows, 101 open); all 1,266 sessions have 503-506 members.
* `universe` with `added_by = 'sp500_history'`: 71 rows, 3 active; `prices_daily` with `source = 'alpaca_backfill'`: 44,495 rows for 76 tickers.
* PARA: "Paramount Global (Class B)", inactive, sector and cusip NULL; 965 price rows 2021-10-01..2025-08-06, max close 36.23; no technical indicators dated >= 2025-08-08.
* BF.B: active, not blacklisted, 1,266 price rows up to 2026-10-16, 1,265 indicator rows.
* Active universe: 760 (757 - PARA + SKYD/TWLO/VYLR + BF.B).
* On 2022-01-03 all 505 point-in-time members have a price row; the only member-sessions without a price are the 37 expected tail gaps.
* After step 3 (read-only SQL): every point-in-time S&P 500 member has a `feature_snapshots` row on every session (0 member-sessions missing); on 2022-01-03 505 members have a snapshot (runbook expectation). BF.B: 1,266 snapshots with price features (`rsi_14` 1,265) and targets (`return_20d` 1,246). PARA: 966 snapshots 2021-10-01..2025-08-07. Exits such as ATVI, FRC, SIVB and TWTR have snapshots up to their last member day.
* Known limit (no delisting returns): for abruptly delisted tickers (SIVB, FRC) the targets of roughly the last 20 sessions are NULL because no later price exists ([LEARNINGS.md](../LEARNINGS.md) 2026-10-17).
