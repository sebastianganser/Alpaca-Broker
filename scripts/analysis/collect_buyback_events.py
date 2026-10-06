"""Collect 8-K buyback authorizations for the event study (concept §7.4, D1).

Searches EDGAR full-text search month by month, keeps 8-K filings of
universe companies, downloads the matching documents and classifies them
(:mod:`trading_signals.collectors.buyback_events`). Writes CSV files only –
nothing is written to the database (the universe is read).

Outputs in ``--out``:
* ``buyback_events.csv`` – one row per filing classified as a new/increased
  authorization: ticker, cik, event_date (8-K filing date), accession_number,
  document, items, with_earnings (Item 2.02 in the same 8-K), amount_usd,
  snippet.
* ``buyback_sentences.jsonl`` – cache of the candidate sentences of every
  downloaded document (resume-safe; delete it to start over). Classification
  is re-run from this cache on every run, so classifier changes need no new
  downloads.
* ``buyback_audit_<date>.md`` – random sample of positives / negatives for a
  manual precision check.

Usage:
    .venv/bin/python scripts/analysis/collect_buyback_events.py --out /app/repair_logs
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.collect_buyback_events")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", type=date.fromisoformat, default=date(2022, 6, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--out", default="reports", help="Output directory")
    p.add_argument("--audit-n", type=int, default=25, help="Audit sample size per class")
    return p.parse_args(argv)


def load_universe(session) -> list[str]:
    from sqlalchemy import text

    return list(session.execute(text(
        "SELECT DISTINCT ticker FROM signals.universe ORDER BY 1"
    )).scalars())


def load_cache(path: Path) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    cache[rec["key"]] = rec
    return cache


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)

    from trading_signals.collectors.buyback_events import (
        SNIPPET_LEN,
        BuybackEventSearcher,
        candidate_sentences,
        classify_sentences,
        html_to_text,
    )
    from trading_signals.collectors.sec_client import SECClient
    from trading_signals.db.session import get_session

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "buyback_sentences.jsonl"
    cache = load_cache(cache_path)

    with get_session() as session:
        universe = load_universe(session)
    client = SECClient()
    cik_to_ticker: dict[str, str] = {}
    for t in universe:
        cik = client.get_cik(t)
        if cik:
            cik_to_ticker.setdefault(cik, t)
    logger.info(f"{len(universe)} universe tickers, {len(cik_to_ticker)} mapped to a CIK")

    searcher = BuybackEventSearcher(client)
    t0 = time.monotonic()
    hits = searcher.search(args.start, args.end)
    ours = [h for h in hits if h.cik in cik_to_ticker]
    logger.info(f"{len(hits)} 8-K document hits, {len(ours)} of universe companies "
                f"({time.monotonic() - t0:.0f}s)")

    by_filing: dict[str, list] = defaultdict(list)
    for h in ours:
        by_filing[h.accession_number].append(h)

    events = []
    audit_pos: list[tuple[str, str, str]] = []
    audit_neg: list[tuple[str, str, str]] = []
    downloads = 0
    with cache_path.open("a", encoding="utf-8") as cache_fh:
        for i, (adsh, docs) in enumerate(sorted(by_filing.items()), 1):
            # main 8-K document first, then exhibits
            docs.sort(key=lambda h: (h.file_type != "8-K", h.document))
            found = None
            for h in docs:
                key = f"{adsh}:{h.document}"
                rec = cache.get(key)
                if rec is None:
                    try:
                        raw = client.download_filing_document(h.cik, adsh, h.document)
                    except Exception as e:  # noqa: BLE001 – keep going, log it
                        logger.warning(f"download failed {key}: {e}")
                        continue
                    downloads += 1
                    rec = {"key": key, "sentences": candidate_sentences(html_to_text(raw))}
                    cache[key] = rec
                    cache_fh.write(json.dumps(rec) + "\n")
                    cache_fh.flush()
                res = classify_sentences(rec["sentences"], as_of=h.file_date)
                ticker = cik_to_ticker[h.cik]
                if res.is_event:
                    found = (h, res)
                    audit_pos.append((ticker, h.file_date.isoformat(), res.snippet or ""))
                    break
                if rec["sentences"]:
                    audit_neg.append((ticker, h.file_date.isoformat(),
                                      rec["sentences"][0][:300]))
            if found:
                h, res = found
                events.append({
                    "ticker": cik_to_ticker[h.cik], "cik": h.cik,
                    "event_date": h.file_date.isoformat(), "accession_number": adsh,
                    "document": h.document, "items": " ".join(h.items),
                    "with_earnings": "2.02" in h.items, "amount_usd": res.amount_usd,
                    "snippet": (res.snippet or "")[:SNIPPET_LEN],
                })
            if i % 500 == 0:
                logger.info(f"{i}/{len(by_filing)} filings classified, {len(events)} events, "
                            f"{downloads} downloads")

    ev = pd.DataFrame(events).sort_values(["event_date", "ticker"]) if events else pd.DataFrame()
    ev.to_csv(out_dir / "buyback_events.csv", index=False)
    logger.info(f"{len(by_filing)} filings → {len(ev)} buyback authorization events "
                f"({downloads} new downloads)")

    # audit sample for a manual precision check
    rng = random.Random(42)
    lines = [f"# Audit Rückkauf-Klassifizierung ({date.today()})", "",
             f"Filings: {len(by_filing)}, Ereignisse: {len(ev)}",
             "", "## Stichprobe positiv", ""]
    for t, d, s in rng.sample(audit_pos, min(args.audit_n, len(audit_pos))):
        lines.append(f"- {t} {d}: {s}")
    lines += ["", "## Stichprobe negativ (erster Kandidatensatz)", ""]
    for t, d, s in rng.sample(audit_neg, min(args.audit_n, len(audit_neg))):
        lines.append(f"- {t} {d}: {s}")
    (out_dir / f"buyback_audit_{date.today():%Y-%m-%d}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
