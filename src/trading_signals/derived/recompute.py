"""Downstream recomputation after a ticker's price history was refreshed.

Called by the price collector when it detects that Alpaca re-adjusted a
ticker's history (split / dividend with ``adjustment=all``) and the full
history has been re-downloaded with ON CONFLICT DO UPDATE.

Contract (owned by the derived/ML layer):
    recompute_after_price_refresh(session, tickers) -> dict[str, int]
must, for every ticker:
    1. recompute ``technical_indicators`` for the full history,
    2. recompute all forward-return targets in ``feature_snapshots``.
It must not commit; the caller controls the transaction.

Note: price-ratio features stored in ``feature_snapshots`` (price_vs_sma*,
atr_14_pct, …) are scale-invariant and therefore unaffected by a split
re-adjustment; volume-based features (dollar_volume_20d, volume_ratio_20d)
of past dates are not recomputed here (use scripts/recompute_features.py).
If SPY itself was refreshed, ``relative_strength_spy`` of *all* tickers is
stale – this is logged; run a full TA backfill in that case.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from trading_signals.derived.target_backfill import TargetBackfillComputer
from trading_signals.derived.technical_indicators import (
    SPY_TICKER,
    TechnicalIndicatorsComputer,
)
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


def recompute_after_price_refresh(
    session: Session, tickers: list[str]
) -> dict[str, int]:
    """Recompute TA + targets for tickers whose price history changed.

    Returns a dict with counters
    ``{"tickers": n, "ta_rows": x, "targets": y, "errors": e}``.
    """
    unique = sorted({t for t in tickers if t})
    if not unique:
        return {"tickers": 0, "ta_rows": 0, "targets": 0, "errors": 0}

    ta = TechnicalIndicatorsComputer(session)
    ta_rows = 0
    errors = 0
    for ticker in unique:
        try:
            # SAVEPOINT: a failing ticker must not abort the caller's transaction
            with session.begin_nested():
                ta_rows += ta.recompute_ticker(ticker)
        except Exception as e:  # keep going for the other tickers
            errors += 1
            logger.error(f"[recompute] TA recompute failed for {ticker}: {e}")
    session.flush()

    if SPY_TICKER in unique:
        logger.warning(
            "[recompute] SPY history was refreshed – relative_strength_spy of all "
            "other tickers is stale; run a full TA backfill "
            "(scripts/recompute_spy_rs.py)."
        )

    targets = TargetBackfillComputer(session).recompute_tickers(unique)
    session.flush()

    result = {
        "tickers": len(unique),
        "ta_rows": ta_rows,
        "targets": targets,
        "errors": errors,
    }
    logger.info(f"[recompute] {result}")
    return result
