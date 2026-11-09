"""Vectorised politician features for ONE ticker over many sessions.

Re-implements ``FeaturePipeline._politician_features(ticker, d)`` (SQL per
ticker and day) for a whole history at once, so a targeted repair can recompute
the four politician columns of ``feature_snapshots`` in minutes instead of a
7-12 hour full feature rebuild. The semantics are a literal copy of the
pipeline (any change there must be mirrored here; the repair script verifies
a random sample against the real pipeline before it writes anything):

For session ``d`` the *eligible* trades are those with ``transaction_date`` and
``disclosure_date`` set, ``disclosure_date <= d`` (public by ``d``) and
``transaction_date >= disclosure_date - 45 days`` (STOCK Act lag filter).

* ``politician_buy_count_60d_disclosure``: eligible ``Purchase`` trades with
  ``disclosure_date`` in ``[d-60, d]``;
* ``politician_distinct_90d_disclosure``: distinct non-NULL ``politician_name``
  of eligible trades (any type) with ``disclosure_date`` in ``[d-90, d]``;
* ``..._transaction`` variants: the same with ``transaction_date`` in the
  window (``disclosure_date <= d`` still applies);
* *covered* = at least one eligible trade exists (any time up to ``d``). Not
  covered -> all four values NA (the pipeline returns ``{}``), covered -> four
  integers, zeros allowed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

BUY_COUNT_DISCLOSURE = "politician_buy_count_60d_disclosure"
DISTINCT_DISCLOSURE = "politician_distinct_90d_disclosure"
BUY_COUNT_TRANSACTION = "politician_buy_count_60d_transaction"
DISTINCT_TRANSACTION = "politician_distinct_90d_transaction"

POLITICIAN_FEATURE_COLUMNS: tuple[str, ...] = (
    BUY_COUNT_DISCLOSURE,
    DISTINCT_DISCLOSURE,
    BUY_COUNT_TRANSACTION,
    DISTINCT_TRANSACTION,
)

#: STOCK Act maximum reporting deadline (same constant as in the pipeline).
MAX_LAG_DAYS = 45
BUY_WINDOW_DAYS = 60
DISTINCT_WINDOW_DAYS = 90
PURCHASE = "Purchase"

#: Upper bound of session-chunk x trade matrix cells held in memory at once.
_CELLS_PER_CHUNK = 2_000_000


def _to_days(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """``(days since epoch as int64, valid mask)`` of a date-like series."""
    ts = pd.to_datetime(values, errors="coerce")
    valid = ts.notna().to_numpy()
    days = ts.to_numpy().astype("datetime64[D]").astype("int64")
    return days, valid


def _empty(sessions: Sequence[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.array([pd.NA] * len(sessions), dtype="Int64") for c in
         POLITICIAN_FEATURE_COLUMNS},
        index=pd.Index(list(sessions)),
    )


def compute_politician_features(
    trades: pd.DataFrame, sessions: Sequence[date]
) -> pd.DataFrame:
    """Four politician features of one ticker for every session.

    Args:
        trades: all stored trades of ONE ticker with the columns
            ``transaction_date``, ``disclosure_date``, ``transaction_type``,
            ``politician_name`` (NULLs allowed anywhere).
        sessions: the sessions to compute (any order, need not be NYSE days).

    Returns:
        One row per session (index = ``sessions`` as given), the four
        :data:`POLITICIAN_FEATURE_COLUMNS` as nullable ``Int64``; all NA where
        the ticker is not covered on that session.
    """
    result = _empty(sessions)
    if len(trades) == 0 or len(sessions) == 0:
        return result

    disc, disc_ok = _to_days(trades["disclosure_date"])
    txn, txn_ok = _to_days(trades["transaction_date"])
    eligible = disc_ok & txn_ok & (txn >= disc - MAX_LAG_DAYS)
    if not eligible.any():
        return result

    disc, txn = disc[eligible], txn[eligible]
    purchase = (trades["transaction_type"] == PURCHASE).to_numpy(bool)[eligible]
    codes, _ = pd.factorize(trades["politician_name"].to_numpy(object)[eligible])
    n_names = int(codes.max()) + 1 if (codes >= 0).any() else 0
    onehot = None
    if n_names:
        onehot = np.zeros((len(codes), n_names), dtype=np.float32)
        known = codes >= 0
        onehot[np.nonzero(known)[0], codes[known]] = 1.0

    sess = np.array(
        [np.datetime64(pd.Timestamp(s).date(), "D").astype("int64") for s in sessions],
        dtype="int64",
    )
    n_trades = len(disc)
    chunk = max(1, _CELLS_PER_CHUNK // n_trades)
    first_public = disc.min()

    def distinct(mask: np.ndarray) -> np.ndarray:
        if onehot is None:
            return np.zeros(mask.shape[0], dtype="int64")
        return ((mask.astype(np.float32) @ onehot) > 0).sum(axis=1)

    out = {c: np.zeros(len(sess), dtype="int64") for c in POLITICIAN_FEATURE_COLUMNS}
    for lo in range(0, len(sess), chunk):
        d = sess[lo : lo + chunk, None]
        public = disc[None, :] <= d
        in_disc_60 = (disc[None, :] >= d - BUY_WINDOW_DAYS) & public
        in_disc_90 = (disc[None, :] >= d - DISTINCT_WINDOW_DAYS) & public
        in_txn_60 = (
            (txn[None, :] >= d - BUY_WINDOW_DAYS) & (txn[None, :] <= d) & public
        )
        in_txn_90 = (
            (txn[None, :] >= d - DISTINCT_WINDOW_DAYS) & (txn[None, :] <= d) & public
        )
        sl = slice(lo, lo + chunk)
        out[BUY_COUNT_DISCLOSURE][sl] = (in_disc_60 & purchase[None, :]).sum(axis=1)
        out[BUY_COUNT_TRANSACTION][sl] = (in_txn_60 & purchase[None, :]).sum(axis=1)
        out[DISTINCT_DISCLOSURE][sl] = distinct(in_disc_90)
        out[DISTINCT_TRANSACTION][sl] = distinct(in_txn_90)

    covered = sess >= first_public  # some eligible trade is public by d
    for col in POLITICIAN_FEATURE_COLUMNS:
        values = pd.array(out[col], dtype="Int64")
        values[~covered] = pd.NA
        result[col] = values
    return result
