"""Point-in-time short-interest features from FINRA settlement data.

``signals.short_interest`` holds the twice-monthly FINRA short-interest
snapshots (``settlement_date``). FINRA publishes a snapshot roughly seven
business days after its settlement date, and the table stores no
publication date. To stay free of lookahead the availability of a
settlement is derived from the calendar:

  * a settlement date ``s`` may be used for feature day ``d`` only if
    ``s <= cutoff(d)``, where ``cutoff(d)`` is the NYSE session
    :data:`SI_PUBLICATION_LAG_SESSIONS` (9) sessions before ``d`` (two
    sessions of safety margin on top of the ~7 business days);
  * if the newest usable settlement lies more than :data:`SI_MAX_AGE_DAYS`
    (40) calendar days before ``cutoff(d)`` the data is stale and both
    features are NULL.

Features (columns of ``feature_snapshots``, migration 036):

  short_interest_days_to_cover  ``days_to_cover`` of the newest usable
                                settlement (NULL stays NULL)
  short_interest_change_1m      short_interest(newest) /
                                short_interest(2 usable settlements earlier)
                                - 1  (≈ one month); NULL if that earlier
                                settlement is missing, more than
                                :data:`SI_CHANGE_MAX_SPAN_DAYS` (45) days
                                before the newest, or has no positive
                                ``short_interest``

Two implementations of the same rules, kept identical by a unit test
(``tests/unit/test_short_interest_features.py``):

  * :func:`short_interest_features_at` – pure Python for ONE ticker and day,
    used by the nightly ``FeaturePipeline``;
  * :func:`compute_short_interest_frame` – vectorised (``merge_asof``) for
    many tickers/days, used by
    ``scripts/repair/backfill_short_interest_features.py``.

Both round to :data:`DECIMALS` places with ``numpy.round`` (the DB columns
are ``NUMERIC(10,4)``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trading_signals.utils.market_calendar import trading_days

#: NYSE sessions between the settlement date and the first day the data is
#: treated as public (FINRA publishes after ~7 business days + 2 margin).
SI_PUBLICATION_LAG_SESSIONS = 9
#: Max age (calendar days) of the newest usable settlement, measured from
#: ``cutoff(d)``; older → both features NULL.
SI_MAX_AGE_DAYS = 40
#: ``short_interest_change_1m`` compares with the settlement this many
#: usable settlements earlier (twice a month → ≈ one month).
SI_CHANGE_STEPS = 2
#: Max calendar-day distance between the newest and the comparison
#: settlement for ``short_interest_change_1m``.
SI_CHANGE_MAX_SPAN_DAYS = 45
#: Calendar days before ``cutoff(d)`` that contain every settlement which
#: can influence the result (newest ≤ 40 d, comparison ≤ 45 d further back).
#: Loading more is harmless, loading less changes results.
SI_LOAD_WINDOW_DAYS = SI_MAX_AGE_DAYS + SI_CHANGE_MAX_SPAN_DAYS
DECIMALS = 4

SHORT_INTEREST_COLUMNS: tuple[str, ...] = (
    "short_interest_days_to_cover",
    "short_interest_change_1m",
)
#: Input columns of :func:`compute_short_interest_frame` (settlements).
SETTLEMENT_COLUMNS: tuple[str, ...] = (
    "ticker",
    "settlement_date",
    "short_interest",
    "days_to_cover",
)


# ── Availability cutoff ──────────────────────────────────────────────


def publication_cutoffs(dates: Iterable[date]) -> dict[date, date | None]:
    """``cutoff(d)`` for many days with ONE calendar lookup.

    ``cutoff(d)`` is the :data:`SI_PUBLICATION_LAG_SESSIONS`-th NYSE session
    strictly before ``d`` (``d`` itself may or may not be a session).
    ``None`` only if the calendar window does not reach that far back.
    """
    uniq = sorted({d for d in dates if d is not None})
    if not uniq:
        return {}
    # 9 sessions ≈ 13 calendar days; generous margin for holiday clusters.
    lookback = timedelta(days=int(SI_PUBLICATION_LAG_SESSIONS * 1.6) + 10)
    sessions = trading_days(uniq[0] - lookback, uniq[-1])
    arr = np.array(sessions, dtype="datetime64[D]")
    wanted = np.array(uniq, dtype="datetime64[D]")
    # searchsorted(left) = number of sessions strictly before d
    idx = np.searchsorted(arr, wanted, side="left") - SI_PUBLICATION_LAG_SESSIONS
    return {d: (sessions[i] if i >= 0 else None) for d, i in zip(uniq, idx.tolist())}


def publication_cutoff(d: date) -> date | None:
    """``cutoff(d)`` for a single day (see :func:`publication_cutoffs`)."""
    return publication_cutoffs([d])[d]


# ── Pure implementation (nightly pipeline) ───────────────────────────


def _clean(value) -> float | None:
    """float, with None / NaN → None."""
    if value is None:
        return None
    f = float(value)
    return None if np.isnan(f) else f


def _round(value: float) -> float:
    # ``+ 0.0`` turns -0.0 into 0.0
    return float(np.round(value, DECIMALS)) + 0.0


def short_interest_features_at(
    rows: Iterable[Sequence],
    d: date,
    cutoff: date | None = None,
) -> dict[str, float | None]:
    """Both short-interest features of ONE ticker on feature day ``d``.

    Args:
        rows: ``(settlement_date, short_interest, days_to_cover)`` of the
            ticker in any order. Duplicate settlement dates: the last one
            in iteration order wins. Rows after ``cutoff(d)`` are ignored.
        d: Feature day.
        cutoff: ``cutoff(d)`` if the caller already has it (the pipeline
            caches it per day); computed otherwise.

    Returns:
        ``{column: value | None}`` for every column of
        :data:`SHORT_INTEREST_COLUMNS` (always both keys).
    """
    result: dict[str, float | None] = dict.fromkeys(SHORT_INTEREST_COLUMNS)
    if cutoff is None:
        cutoff = publication_cutoff(d)
    if cutoff is None:
        return result

    usable: dict[date, tuple] = {}
    for settlement_date, short_interest, days_to_cover in (r[:3] for r in rows):
        if settlement_date is not None and settlement_date <= cutoff:
            usable[settlement_date] = (short_interest, days_to_cover)
    if not usable:
        return result

    dates = sorted(usable)
    newest = dates[-1]
    if (cutoff - newest).days > SI_MAX_AGE_DAYS:
        return result

    si_new, dtc_new = usable[newest]
    dtc = _clean(dtc_new)
    if dtc is not None:
        result["short_interest_days_to_cover"] = _round(dtc)

    if len(dates) > SI_CHANGE_STEPS:
        earlier = dates[-1 - SI_CHANGE_STEPS]
        si_old = _clean(usable[earlier][0])
        si_new = _clean(si_new)
        if (
            (newest - earlier).days <= SI_CHANGE_MAX_SPAN_DAYS
            and si_new is not None
            and si_old is not None
            and si_old > 0
        ):
            result["short_interest_change_1m"] = _round(si_new / si_old - 1.0)
    return result


# ── Vectorised implementation (history backfill) ─────────────────────


def _to_ns(s: pd.Series) -> pd.Series:
    """Datetime64[ns] (merge_asof needs identical key dtypes)."""
    return pd.to_datetime(s).astype("datetime64[ns]")


def _to_float(s: pd.Series) -> pd.Series:
    """Float series (Decimal / None / object input; unparsable -> NaN)."""
    return pd.to_numeric(s, errors="coerce").astype(float)


def compute_short_interest_frame(
    settlements: pd.DataFrame, keys: pd.DataFrame
) -> pd.DataFrame:
    """Both features for many ``(ticker, snapshot_date)`` keys at once.

    Same rules as :func:`short_interest_features_at` (a unit test compares
    them on the same data).

    Args:
        settlements: Columns :data:`SETTLEMENT_COLUMNS` for any number of
            tickers, in any order. Duplicate ``(ticker, settlement_date)``:
            the last row wins (after a stable sort by date).
        keys: Columns ``ticker`` and ``snapshot_date`` (dates).

    Returns:
        ``ticker``, ``snapshot_date`` (as Python dates) +
        :data:`SHORT_INTEREST_COLUMNS`, one row per distinct key; missing →
        NaN.
    """
    out_cols = ["ticker", "snapshot_date", *SHORT_INTEREST_COLUMNS]
    if keys is None or keys.empty:
        return pd.DataFrame(columns=out_cols)
    k = keys[["ticker", "snapshot_date"]].copy()
    k["snapshot_date"] = pd.to_datetime(k["snapshot_date"]).dt.date
    k["ticker"] = k["ticker"].astype(str)
    k = k.drop_duplicates().reset_index(drop=True)
    cutoffs = publication_cutoffs(k["snapshot_date"])
    k["_cutoff"] = _to_ns(
        pd.Series([cutoffs[d] for d in k["snapshot_date"]], index=k.index)
    )

    s = settlements[list(SETTLEMENT_COLUMNS)].dropna(
        subset=["ticker", "settlement_date"]
    )
    s = s.assign(
        ticker=s["ticker"].astype(str),
        settlement_date=_to_ns(s["settlement_date"]),
        short_interest=_to_float(s["short_interest"]),
        days_to_cover=_to_float(s["days_to_cover"]),
    )
    s = (
        s.sort_values(["ticker", "settlement_date"], kind="stable")
        .drop_duplicates(["ticker", "settlement_date"], keep="last")
        .reset_index(drop=True)
    )
    per_ticker = s.groupby("ticker", sort=False)
    # The usable settlements of a day are a prefix of the ticker's sorted
    # history, so "2 usable settlements earlier" = position - 2 overall.
    s["_earlier_si"] = per_ticker["short_interest"].shift(SI_CHANGE_STEPS)
    s["_earlier_date"] = per_ticker["settlement_date"].shift(SI_CHANGE_STEPS)

    left = k[k["_cutoff"].notna()].sort_values("_cutoff")
    if s.empty or left.empty:
        merged = left.assign(
            settlement_date=pd.NaT,
            short_interest=np.nan,
            days_to_cover=np.nan,
            _earlier_si=np.nan,
            _earlier_date=pd.NaT,
        )
        merged["settlement_date"] = _to_ns(merged["settlement_date"])
        merged["_earlier_date"] = _to_ns(merged["_earlier_date"])
    else:
        merged = pd.merge_asof(
            left,
            s.sort_values("settlement_date"),
            left_on="_cutoff",
            right_on="settlement_date",
            by="ticker",
            direction="backward",
            allow_exact_matches=True,
        )

    age = (merged["_cutoff"] - merged["settlement_date"]).dt.days
    fresh = merged["settlement_date"].notna() & (age <= SI_MAX_AGE_DAYS)
    span = (merged["settlement_date"] - merged["_earlier_date"]).dt.days
    can_change = (
        fresh
        & (span <= SI_CHANGE_MAX_SPAN_DAYS)
        & merged["short_interest"].notna()
        & (merged["_earlier_si"] > 0)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        change = merged["short_interest"] / merged["_earlier_si"] - 1.0
    merged["short_interest_days_to_cover"] = merged["days_to_cover"].where(fresh)
    merged["short_interest_change_1m"] = change.where(can_change)
    cols = list(SHORT_INTEREST_COLUMNS)
    # "+ 0.0" turns -0.0 into 0.0
    merged[cols] = (
        merged[cols].replace([np.inf, -np.inf], np.nan).round(DECIMALS) + 0.0
    )

    out = k[["ticker", "snapshot_date"]].merge(
        merged[["ticker", "snapshot_date", *cols]],
        on=["ticker", "snapshot_date"],
        how="left",
    )
    return out[out_cols]


__all__ = [
    "SETTLEMENT_COLUMNS",
    "SHORT_INTEREST_COLUMNS",
    "SI_CHANGE_MAX_SPAN_DAYS",
    "SI_CHANGE_STEPS",
    "SI_LOAD_WINDOW_DAYS",
    "SI_MAX_AGE_DAYS",
    "SI_PUBLICATION_LAG_SESSIONS",
    "compute_short_interest_frame",
    "publication_cutoff",
    "publication_cutoffs",
    "short_interest_features_at",
]
