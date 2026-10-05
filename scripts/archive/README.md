# scripts/archive

One-off debug, diagnosis, verification and legacy backfill scripts from earlier
sprints. They are **not maintained**, are excluded from the Docker image
(`.dockerignore`) and may reference outdated schema/constraints
(e.g. `backfill_form4.py` → nonexistent `uq_insider_trade_dedup`) or the old
fixed `2021-01-01` data start.

Replacements:

| Archived | Use instead |
|---|---|
| `backfill_prices.py`, `backfill_spy.py`, `backfill_delisted_prices.py` | `scripts/repair/collectors_refetch_prices.py` or Settings → Operations → Price backfill (`PriceCollectorAlpaca.refresh_full_history`, window = `data_start_date()`) |
| `backfill_form4.py` | regular `form4_collector` job (`Form4Collector.store()` with the current dedup constraint) |
| `sprint8_readiness.py`, `sprint9_readiness.py` | `scripts/sprint10_readiness.py` |
