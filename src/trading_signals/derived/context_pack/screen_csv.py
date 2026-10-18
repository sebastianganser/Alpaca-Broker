"""``screen.csv`` - every ticker that passed the quality gate, one row each.

The table lets the reader filter the whole gated universe themselves and
forecast tickers that have no full file. It carries the screen's evidence
(group percentiles, breadth, composite, event flags) for ALL gate tickers and
no information about which tickers got a full file (no bucket, no rank): rows
are sorted alphabetically.
"""

from __future__ import annotations

import pandas as pd

from trading_signals.analysis.screen import EVENT_RULES, GROUP_NAMES

#: Output columns in order; ``p_<group>`` are the group percentiles (0-100).
BASE_COLUMNS = (
    "ticker",
    "company",
    "sector",
    "close",
    "ret_20d",
    "ret_60d",
    "dollar_volume_musd",
    "earnings_days_until",
    "n_groups",
    "evidence_breadth",
    "composite",
    "composite_pct",
    "n_bull_groups",
    "n_bear_groups",
)
GROUP_COLUMNS = tuple(f"p_{g}" for g in GROUP_NAMES)
EVENT_COLUMNS = ("events_bull", "events_bear")
COLUMNS = (*BASE_COLUMNS, *GROUP_COLUMNS, *EVENT_COLUMNS)


def _events(screened: pd.DataFrame, direction: str) -> pd.Series:
    names = [r.name for r in EVENT_RULES if r.direction == direction]
    cols = [f"ev_{n}" for n in names]
    if not cols:
        return pd.Series("", index=screened.index)
    flags = screened[cols].fillna(False).astype(bool).to_numpy()
    return pd.Series(
        [";".join(n for n, f in zip(names, row, strict=True) if f) for row in flags],
        index=screened.index,
    )


def screen_frame(screened: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """The ``screen.csv`` rows as a DataFrame (gate tickers, A-Z).

    Args:
        screened: Result of ``screen_cross_section`` of the session.
        extra: Per-ticker columns indexed by ticker: ``company``, ``sector``,
            ``close``, ``ret_20d``, ``ret_60d``,
            ``dollar_volume_20d``, ``earnings_days_until``.
    """
    gated = screened[screened["gate_ok"]].copy()
    gated = gated.set_index("ticker", drop=False).rename_axis(None)
    out = pd.DataFrame(index=gated.index)
    out["ticker"] = gated["ticker"]
    for col in ("company", "sector", "close", "ret_20d", "ret_60d"):
        out[col] = extra[col].reindex(gated.index) if col in extra else None
    out["close"] = pd.to_numeric(out["close"], errors="coerce").round(2)
    for col in ("ret_20d", "ret_60d"):
        out[col] = pd.to_numeric(out[col], errors="coerce").round(4)
    dv = (
        extra["dollar_volume_20d"].reindex(gated.index)
        if "dollar_volume_20d" in extra
        else None
    )
    out["dollar_volume_musd"] = (
        pd.to_numeric(dv, errors="coerce").div(1e6).round(1) if dv is not None else None
    )
    ed = (
        extra["earnings_days_until"].reindex(gated.index)
        if "earnings_days_until" in extra
        else None
    )
    out["earnings_days_until"] = (
        pd.to_numeric(ed, errors="coerce").round().astype("Int64")
        if ed is not None
        else None
    )
    out["n_groups"] = gated["n_groups"].astype("Int64")
    out["evidence_breadth"] = (
        pd.to_numeric(gated["evidence_breadth"], errors="coerce")
        .round()
        .astype("Int64")
    )
    out["composite"] = pd.to_numeric(gated["composite"], errors="coerce").round(4)
    out["composite_pct"] = (
        (pd.to_numeric(gated["composite_pct"], errors="coerce") * 100)
        .round()
        .astype("Int64")
    )
    out["n_bull_groups"] = gated["n_bull_groups"].astype("Int64")
    out["n_bear_groups"] = gated["n_bear_groups"].astype("Int64")
    for g in GROUP_NAMES:
        out[f"p_{g}"] = (
            (pd.to_numeric(gated[f"gp_{g}"], errors="coerce") * 100)
            .round()
            .astype("Int64")
        )
    out["events_bull"] = _events(gated, "bull")
    out["events_bear"] = _events(gated, "bear")
    return (
        out[list(COLUMNS)].sort_values("ticker", kind="stable").reset_index(drop=True)
    )


def screen_csv_text(screened: pd.DataFrame, extra: pd.DataFrame) -> str:
    """CSV text (UTF-8, ``\\n`` line ends, empty cell = no value)."""
    return screen_frame(screened, extra).to_csv(index=False, lineterminator="\n")
