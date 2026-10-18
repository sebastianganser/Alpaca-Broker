"""Constants of the context pack v2 (``docs/CONTEXT_PACK.md``)."""

from __future__ import annotations

#: Written into every ticker file's frontmatter; a change of the layout that
#: the analysis skill depends on bumps it (hard cut, ``docs/CONTEXT_PACK.md``).
PACK_FORMAT = 2

OVERVIEW_FILE = "00_uebersicht.md"
SCREEN_FILE = "screen.csv"
#: Written by the Claude skill into the pack folder (``docs/FORECASTS.md``).
FORECASTS_FILE = "forecasts.yaml"
#: Report of the nightly ``forecast_review`` step, one level above the folders.
REPORT_FILE = "forecast_auswertung.md"
#: Optional file ``context_packs/requested_tickers.txt``: one ticker per line,
#: ``#`` starts a comment. The listed tickers get a full file in every pack
#: until they are removed from the file.
REQUESTED_FILE = "requested_tickers.txt"
#: Upper bound of requested tickers per pack (keeps the pack readable).
MAX_REQUESTED = 25

#: Return horizons (sessions) shown in the trend section.
RETURN_HORIZONS = (1, 5, 20, 60, 120, 252)
#: Calendar days looked back to find the session ``max(RETURN_HORIZONS)`` back.
RETURN_LOOKBACK_DAYS = 420

#: A cross-section needs this many observations for a percentile.
MIN_PERCENTILE_OBS = 20
#: A sector needs this many gate tickers with a value for a sector percentile.
MIN_SECTOR_OBS = 10

#: Max age (calendar days) of snapshot-type sources, as in the feature
#: pipeline (``MAX_SNAPSHOT_AGE_DAYS``).
SNAPSHOT_MAX_AGE_DAYS = 14

# ── Row limits of the raw-table sections ─────────────────────────────

INSIDER_WINDOW_DAYS = 180
INSIDER_ROWS = 6
RATING_WINDOW_DAYS = 180
RATING_ROWS = 5
POLITICIAN_WINDOW_DAYS = 180
POLITICIAN_ROWS = 4
HEADLINE_WINDOW_DAYS = 30
HEADLINE_ROWS = 5
HOLDER_ROWS = 5
EARNINGS_PAST_ROWS = 4
SHORT_INTEREST_ROWS = 3
#: An options IV rank needs a year of daily snapshots.
IV_RANK_MIN_OBS = 252
