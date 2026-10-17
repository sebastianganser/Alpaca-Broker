"""Pre-registered evaluation of Claude's forecasts (``docs/FORECASTS.md``).

Plan 2026-10-17 (strategy-neutral rework), step 2: pure evaluation functions;
the ingest (``forecasts.yaml`` -> DB) and the nightly wiring come in step 4.

A forecast is ``(session d, ticker, horizon h, direction, confidence,
conviction)``. The realised value is the strategy-neutral forward return of the
snapshot table, ``close(d+h) / open(d+1) - 1``. Everything is evaluated

* **absolute** - the raw return: raw hit rate and mean raw return of the up /
  down calls. Descriptive only: over a sample of a few months the market's
  own path dominates, so this block is NOT decisive, and
* **relative** - the excess over the same-day mean of the gated universe and
  over SPY (selection skill without the market move). The *selection hit
  edge* (hit rate minus the same-day share of the universe with the same sign)
  is a cross-sectional measure too: market timing contributes zero to it.

All figures are date-weighted (every session counts once: the unit of
independence is the date, not the forecast). Intervals are 95 % **batch
jackknife** intervals (batches of 2h sessions, t critical value with g - 1
degrees of freedom, :func:`trading_signals.analysis.stats.batch_jackknife`):
h-day returns of consecutive sessions overlap, and a simulation of the
decisive criterion showed that date-block bootstraps are far too narrow at
10 - 25 independent periods (``analysis/criterion_sim.py``, FORECASTS.md
section 6).

Inputs (all plain DataFrames, no DB):

* ``forecasts``: ``session_date``, ``ticker``, ``horizon_days``, ``direction``
  (``up`` | ``down`` | ``neutral``), ``confidence``, ``conviction``; optional
  ``on_time`` (default True), ``forecaster_version``, ``forecast_id``,
  ``expected_pct``, ``drivers`` (list or comma separated string).
* ``universe_returns``: ``snapshot_date``, ``ticker``, ``return_5d`` /
  ``return_20d`` / ``return_60d`` of every ticker of the gated universe on the
  evaluated sessions (source of the universe means and base rates; may cover a
  longer history than the forecasts - it then also gives the long-run share of
  positive returns).
* ``spy_returns``: SPY ``return_<h>d`` per session (see ``analysis.benchmark``).
* ``pack_candidates`` (optional): ``session_date``, ``ticker``, ``bucket``
  (``bull`` | ``bear`` | ``random`` | ``requested``) for the comparison of
  Claude with the shortlist, the random controls and the universe, and for
  the ``claude_value_add`` criterion.

Decisive criterion (PROPOSAL, awaiting Sebastian's confirmation - see
``docs/FORECASTS.md`` section 6): :func:`freeze_sample` fixes the sample from
the forecasts alone (independent of any return), :func:`verdict_from_frozen`
computes the one-look verdict once the windows are realised, and
:func:`verdict_from_values` recomputes it from persisted values. Step 4
persists the frozen sample and the verdict once.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from trading_signals.analysis.feature_report import min_dates_for_horizon
from trading_signals.analysis.report_format import DASH
from trading_signals.analysis.report_format import ci_cell as _ci
from trading_signals.analysis.report_format import num as _num
from trading_signals.analysis.report_format import pct as _pct
from trading_signals.analysis.stats import (
    batch_jackknife,
    batch_jackknife_diff,
    ci_supports_verdict,
    daily_rank_ic,
    has_batch_ci,
    ic_summary,
    mean_test,
    nw_lag_for_horizon,
)
from trading_signals.utils.logging import get_logger

logger = get_logger("analysis.forecast_eval")

HORIZONS = (5, 20, 60)
PRIMARY_HORIZON = 20
DIRECTIONS = ("up", "down", "neutral")
SIGN = {"up": 1, "down": -1, "neutral": 0}
DATE_COL = "session_date"
#: Version label of forecasts that carry none.
UNVERSIONED = "unversioned"
#: Confidence bins (``P(direction correct)``): [0.50, 0.60), ... , [0.80, 0.95].
CONFIDENCE_BINS = (0.50, 0.60, 0.70, 0.80, 0.9501)
#: Constant forecaster that serves as the Brier reference (known at time d).
BRIER_REFERENCE_P = 0.5
#: Minimum forecasts per session for a rank IC.
MIN_IC_OBS = 5
MIN_EXPECTED_PCT_OBS = 30

# ── Pre-registered decisive criterion (PROPOSAL, see docs/FORECASTS.md) ──

#: Minimum on-time up AND down forecasts in the frozen sample.
CRITERION_MIN_PER_SIDE = 100
#: After the last frozen window has closed, wait at most this many sessions for
#: missing returns (delistings, data gaps); then the verdict uses what exists.
CRITERION_GRACE_SESSIONS = 10
#: ``system``: Claude's final calls against the gated universe;
#: ``claude_value_add``: Claude's calls against the same-day screen shortlist.
CRITERION_MODES = ("system", "claude_value_add")
DEFAULT_CRITERION_MODE = "system"
#: Columns of the per-forecast values a verdict is computed from (persisted).
VALUE_COLUMNS = (
    "forecast_id", DATE_COL, "ticker", "horizon_days", "sigma", "ret", "ex_uni",
    "ex_spy", "hit", "base_hit", "bench_ex", "bench_hit", "bench_from_bucket",
)


def reliable_dates(horizon: int) -> int:
    """Sessions needed for 10 independent ``horizon``-day periods."""
    return min_dates_for_horizon(int(horizon))


# ── Preparation ──────────────────────────────────────────────────────


def _day(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.normalize()


def prepare_forecasts(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalise a forecast frame (copy, idempotent).

    Adds ``sigma`` (+1 up, -1 down, 0 neutral), ``signed_conviction``,
    ``forecast_id`` (natural key ``session|ticker|horizon`` unless given) and
    defaults ``on_time`` to True and ``forecaster_version`` to
    :data:`UNVERSIONED`. Raises ``ValueError`` for unknown directions or
    missing columns.
    """
    need = {DATE_COL, "ticker", "horizon_days", "direction"}
    missing = need - set(forecasts.columns)
    if missing:
        raise ValueError(f"forecasts lack columns: {sorted(missing)}")
    f = forecasts.copy()
    f[DATE_COL] = _day(f[DATE_COL])
    f["ticker"] = f["ticker"].astype(str).str.upper()
    f["direction"] = f["direction"].astype(str).str.lower()
    bad = sorted(set(f["direction"]) - set(DIRECTIONS))
    if bad:
        raise ValueError(f"unknown directions: {bad}")
    f["horizon_days"] = f["horizon_days"].astype(int)
    f["sigma"] = f["direction"].map(SIGN).astype(int)
    for col in ("confidence", "conviction", "expected_pct"):
        f[col] = pd.to_numeric(f[col], errors="coerce") if col in f else np.nan
    f["signed_conviction"] = f["sigma"] * f["conviction"].fillna(0.0)
    if "on_time" not in f.columns:
        f["on_time"] = True
    f["on_time"] = f["on_time"].fillna(False).astype(bool)
    if "forecaster_version" not in f.columns:
        f["forecaster_version"] = UNVERSIONED
    version = f["forecaster_version"].astype("object")
    version = version.where(version.notna(), UNVERSIONED).astype(str).str.strip()
    f["forecaster_version"] = version.where(version != "", UNVERSIONED)
    if "forecast_id" not in f.columns:
        f["forecast_id"] = (
            f[DATE_COL].dt.strftime("%Y-%m-%d") + "|" + f["ticker"] + "|"
            + f["horizon_days"].astype(str)
        )
    return f


def _universe_frame(universe_returns: pd.DataFrame) -> pd.DataFrame:
    """Normalised copy with ``session_date``, upper-case ticker and ``gate_ok``."""
    uni = universe_returns.rename(columns={"snapshot_date": DATE_COL}).copy()
    uni[DATE_COL] = _day(uni[DATE_COL])
    uni["ticker"] = uni["ticker"].astype(str).str.upper()
    if "gate_ok" not in uni.columns:
        uni["gate_ok"] = True
    uni["gate_ok"] = uni["gate_ok"].fillna(False).astype(bool)
    return uni


def _spy_frame(spy_returns: pd.DataFrame | None) -> pd.DataFrame | None:
    """SPY returns indexed by normalised session date (or ``None``)."""
    if spy_returns is None:
        return None
    spy = spy_returns.copy()
    if not isinstance(spy.index, pd.DatetimeIndex):
        key = "snapshot_date" if "snapshot_date" in spy.columns else DATE_COL
        spy = spy.set_index(key)
    spy.index = pd.to_datetime(spy.index).normalize()
    return spy


def _pack_frame(pack_candidates: pd.DataFrame | None) -> pd.DataFrame | None:
    """Normalised, de-duplicated pack candidates (or ``None`` if empty)."""
    if pack_candidates is None or not len(pack_candidates):
        return None
    pc = pack_candidates.copy()
    pc[DATE_COL] = _day(pc[DATE_COL])
    pc["ticker"] = pc["ticker"].astype(str).str.upper()
    return pc.drop_duplicates([DATE_COL, "ticker"])


def _unscored(grp: pd.DataFrame) -> pd.DataFrame:
    """Forecast rows without any realised value (window or column missing)."""
    return grp.assign(
        ret=np.nan, uni_mean=np.nan, ex_uni=np.nan, spy_ret=np.nan, ex_spy=np.nan,
        hit=np.nan, base_hit=np.nan, bench_ex=np.nan, bench_hit=np.nan,
        bench_from_bucket=False,
    )


def _bucket_benchmarks(
    pc: pd.DataFrame | None, u: pd.DataFrame, day: pd.DataFrame
) -> pd.DataFrame:
    """Per session: mean excess return / hit share of the bull and bear shortlist.

    ``bull`` members hit when the return is positive, ``bear`` members when it
    is negative (the direction of the call they would back). Realised returns
    only; the whole shortlist counts, whether Claude called it or not.
    """
    cols = ["bull_ex", "bull_hit", "bear_ex", "bear_hit"]
    out = pd.DataFrame(index=day.index, columns=cols, dtype="float64")
    if pc is None:
        return out
    pj = pc.merge(u.drop_duplicates([DATE_COL, "ticker"])[[DATE_COL, "ticker", "ret"]],
                  on=[DATE_COL, "ticker"], how="inner")
    pj = pj.merge(day[["uni_mean"]], left_on=DATE_COL, right_index=True, how="inner")
    pj["ex"] = pj["ret"] - pj["uni_mean"]
    for bucket, sign in (("bull", 1), ("bear", -1)):
        sub = pj[pj["bucket"] == bucket]
        if sub.empty:
            continue
        out[f"{bucket}_ex"] = sub.groupby(DATE_COL)["ex"].mean()
        out[f"{bucket}_hit"] = (sign * sub["ret"] > 0).astype(float).groupby(
            sub[DATE_COL]).mean()
    return out


def attach_outcomes(
    forecasts: pd.DataFrame,
    universe_returns: pd.DataFrame,
    spy_returns: pd.DataFrame | None = None,
    pack_candidates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Join realised returns, universe means / base rates, SPY and benchmarks.

    ``universe_returns`` may carry a boolean ``gate_ok`` column: realised
    returns are then looked up in all rows (so requested tickers outside the
    gate can be evaluated) while the universe means / base rates use the
    gated rows only (default: every row). A horizon without a
    ``return_<h>d`` column is skipped with a log line: its forecasts stay in
    the frame without a realised value (they count as pending).

    Returns one row per forecast with ``ret`` (NaN while the window is open),
    ``uni_mean`` (mean raw return of the universe on that session),
    ``ex_uni`` / ``ex_spy`` (excess returns), ``hit`` (``sigma * ret > 0``) and
    ``base_hit`` (share of the universe whose return has the called sign on
    that session - the hit rate of a forecaster without skill in the
    cross-section). For the ``claude_value_add`` criterion: ``bench_ex`` /
    ``bench_hit`` = mean excess return / hit share of the same-day shortlist of
    the called direction (``bull`` for up, ``bear`` for down), falling back to
    the universe (0 / ``base_hit``) where that bucket is empty
    (``bench_from_bucket`` tells which).
    """
    f = prepare_forecasts(forecasts)
    uni = _universe_frame(universe_returns)
    spy = _spy_frame(spy_returns)
    pc = _pack_frame(pack_candidates)

    parts = []
    for h, grp in f.groupby("horizon_days", sort=True):
        col = f"return_{h}d"
        if col not in uni.columns:
            logger.warning(
                f"universe_returns lacks column {col!r}: {len(grp)} forecast(s) "
                f"of horizon {h} are not scored"
            )
            parts.append(_unscored(grp))
            continue
        u = uni[[DATE_COL, "ticker", "gate_ok", col]].rename(columns={col: "ret"})
        u["ret"] = pd.to_numeric(u["ret"], errors="coerce")
        u = u[u["ret"].notna()]
        gated = u[u["gate_ok"]]  # universe means / base rates: gated rows only
        by = gated.assign(pos=(gated["ret"] > 0).astype(float),
                          neg=(gated["ret"] < 0).astype(float)).groupby(DATE_COL)
        day = pd.DataFrame({
            "uni_mean": by["ret"].mean(), "uni_pos": by["pos"].mean(),
            "uni_neg": by["neg"].mean(),
        })
        j = grp.merge(u.drop_duplicates([DATE_COL, "ticker"]),
                      on=[DATE_COL, "ticker"], how="left")
        j = j.merge(day, left_on=DATE_COL, right_index=True, how="left")
        j["ex_uni"] = j["ret"] - j["uni_mean"]
        if spy is not None and col in spy.columns:
            j["spy_ret"] = j[DATE_COL].map(spy[col])
        else:
            j["spy_ret"] = np.nan
        j["ex_spy"] = j["ret"] - j["spy_ret"]
        directional = j["sigma"] != 0
        j["hit"] = np.where(j["ret"].notna() & directional,
                            (j["sigma"] * j["ret"] > 0).astype(float), np.nan)
        base = np.where(j["sigma"] > 0, j["uni_pos"],
                        np.where(j["sigma"] < 0, j["uni_neg"], np.nan))
        j["base_hit"] = np.where(j["hit"].notna(), base, np.nan)

        bench = _bucket_benchmarks(pc, u, day)
        b_ex = np.where(j["sigma"] > 0, j[DATE_COL].map(bench["bull_ex"]),
                        np.where(j["sigma"] < 0, j[DATE_COL].map(bench["bear_ex"]),
                                 np.nan))
        b_hit = np.where(j["sigma"] > 0, j[DATE_COL].map(bench["bull_hit"]),
                         np.where(j["sigma"] < 0, j[DATE_COL].map(bench["bear_hit"]),
                                  np.nan))
        from_bucket = directional.to_numpy() & ~np.isnan(b_ex)
        j["bench_from_bucket"] = from_bucket
        j["bench_ex"] = np.where(from_bucket, b_ex, np.where(directional, 0.0, np.nan))
        j["bench_hit"] = np.where(from_bucket, b_hit,
                                  np.where(directional, j["base_hit"], np.nan))
        parts.append(j)
    if not parts:
        return _unscored(f)
    return pd.concat(parts, ignore_index=True)


def long_run_shares(
    universe_returns: pd.DataFrame, horizon: int
) -> tuple[float, float]:
    """Pooled share of positive and of negative ``horizon``-day returns.

    Over the gated rows of ``universe_returns`` - pass the whole retention
    window (not only the forecast sessions) to get a long-run reference that
    does not carry the market path of the evaluated months.
    """
    col = f"return_{int(horizon)}d"
    uni = _universe_frame(universe_returns)
    if col not in uni.columns:
        return float("nan"), float("nan")
    r = pd.to_numeric(uni.loc[uni["gate_ok"], col], errors="coerce").dropna()
    if r.empty:
        return float("nan"), float("nan")
    return float((r > 0).mean()), float((r < 0).mean())


def sessions_after(universe_returns: pd.DataFrame, last_date: Any) -> int:
    """Number of sessions in ``universe_returns`` after ``last_date``."""
    uni = _universe_frame(universe_returns)
    return int((uni[DATE_COL].drop_duplicates() > pd.Timestamp(last_date)).sum())


# ── Per-session table and metric specs ───────────────────────────────


def _nanmean(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    return float(x.mean()) if x.size else float("nan")


def _mean_of(col: int) -> Callable[[np.ndarray], float]:
    return lambda arr: _nanmean(arr[:, col])


def _diff_of_means(a: int, b: int) -> Callable[[np.ndarray], float]:
    return lambda arr: _nanmean(arr[:, a]) - _nanmean(arr[:, b])


def _per_date_table(dec: pd.DataFrame) -> pd.DataFrame:
    """One row per session: hit / base, per-direction means, signed returns.

    Works on the realised directional rows (``VALUE_COLUMNS`` suffice; the
    benchmark columns of a frame without them default to the universe).
    """
    dec = dec.copy()
    if "bench_ex" not in dec.columns:
        dec["bench_ex"] = 0.0
    if "bench_hit" not in dec.columns:
        dec["bench_hit"] = dec["base_hit"]
    dec = dec.assign(
        sgn_raw=dec["sigma"] * dec["ret"], sgn_uni=dec["sigma"] * dec["ex_uni"],
        sgn_spy=dec["sigma"] * dec["ex_spy"],
        va=dec["ex_uni"] - dec["bench_ex"], va_hit=dec["hit"] - dec["bench_hit"],
    )
    g = dec.groupby(DATE_COL)
    up = dec[dec["sigma"] > 0].groupby(DATE_COL)
    dn = dec[dec["sigma"] < 0].groupby(DATE_COL)
    table = pd.DataFrame({
        "hit": g["hit"].mean(), "base": g["base_hit"].mean(),
        "up_hit": up["hit"].mean(), "up_base": up["base_hit"].mean(),
        "dn_hit": dn["hit"].mean(), "dn_base": dn["base_hit"].mean(),
        "up_raw": up["ret"].mean(), "dn_raw": dn["ret"].mean(),
        "up_uni": up["ex_uni"].mean(), "dn_uni": dn["ex_uni"].mean(),
        "up_spy": up["ex_spy"].mean(), "dn_spy": dn["ex_spy"].mean(),
        "sgn_raw": g["sgn_raw"].mean(), "sgn_uni": g["sgn_uni"].mean(),
        "sgn_spy": g["sgn_spy"].mean(),
        "va_up": up["va"].mean(), "va_dn": dn["va"].mean(),
        "va_hit": g["va_hit"].mean(),
    })
    table["sel_edge"] = table["hit"] - table["base"]
    table["up_sel_edge"] = table["up_hit"] - table["up_base"]
    table["dn_sel_edge"] = table["dn_hit"] - table["dn_base"]
    return table.sort_index()


def _metric_specs(
    table: pd.DataFrame,
) -> dict[str, tuple[Callable[[np.ndarray], float], int]]:
    """``{name: (statistic on the table array, sessions with data)}``."""
    ix = {c: i for i, c in enumerate(table.columns)}
    n = {c: int(table[c].notna().sum()) for c in table.columns}

    def mean(name: str, col: str):
        return name, (_mean_of(ix[col]), n[col])

    def diff(name: str, a: str, b: str):
        return name, (_diff_of_means(ix[a], ix[b]), min(n[a], n[b]))

    return dict([
        mean("selection_hit_edge", "sel_edge"),
        mean("up_selection_hit_edge", "up_sel_edge"),
        mean("down_selection_hit_edge", "dn_sel_edge"),
        diff("up_minus_down_raw", "up_raw", "dn_raw"),
        diff("up_minus_down_ex_uni", "up_uni", "dn_uni"),
        diff("up_minus_down_ex_spy", "up_spy", "dn_spy"),
        mean("signed_raw", "sgn_raw"), mean("signed_ex_uni", "sgn_uni"),
        mean("signed_ex_spy", "sgn_spy"),
        mean("up_raw", "up_raw"), mean("down_raw", "dn_raw"),
        mean("up_ex_uni", "up_uni"), mean("down_ex_uni", "dn_uni"),
        mean("up_ex_spy", "up_spy"), mean("down_ex_spy", "dn_spy"),
        mean("up_hit", "up_hit"), mean("down_hit", "dn_hit"),
        diff("va_up_minus_down", "va_up", "va_dn"),
        mean("va_hit_edge", "va_hit"),
    ])


def _intervals(
    table: pd.DataFrame, horizon: int, specs: Mapping[str, Any] | None = None,
    intervals: bool = True,
) -> dict[str, tuple[float, float, float]]:
    specs = specs or _metric_specs(table)
    stats = {k: v[0] for k, v in specs.items()}
    arr = table.to_numpy(dtype=float)
    if intervals:
        return batch_jackknife(arr, stats, horizon)
    nan = float("nan")
    return {k: (fn(arr), nan, nan) for k, fn in stats.items()}


# ── Direction metrics ────────────────────────────────────────────────


def evaluate_direction(
    joined: pd.DataFrame,
    horizon: int,
    intervals: bool = True,
    long_run: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Absolute and relative direction metrics for ONE horizon.

    Only forecasts with a realised return count. ``neutral`` forecasts are
    excluded from hit rates / differences / calibration (counted in
    ``n_neutral``) and enter the rank IC with signed conviction 0.

    Keys (``x`` = ``(estimate, ci_low, ci_high)`` triples, 95 % batch
    jackknife; ``n_obs[key]`` = sessions with data for that metric - a marker
    is only warranted from ``10 * horizon`` of them):

    * ``selection_hit_edge`` (+ ``up_`` / ``down_``): hit rate minus the
      same-day share of the universe with the called sign - a CROSS-SECTIONAL
      measure, market timing contributes zero. ``hit_rate`` and
      ``selection_base_rate`` are the date-weighted means; ``..._t_nw`` the
      Newey-West t of the edge;
    * ``up_minus_down_raw`` / ``_ex_uni`` / ``_ex_spy`` - mean return of the
      up forecasts minus that of the down forecasts (date-weighted per
      direction; unpaired, so it contains market timing in the raw basis),
      ``paired_up_minus_down`` (sessions with both directions; the market
      cancels, so raw = relative);
    * ``signed_raw`` / ``signed_ex_uni`` / ``signed_ex_spy`` - mean of
      ``sigma * return`` (what following the calls earns);
    * TRUE absolute, descriptive only (NOT decisive): ``up_hit`` /
      ``down_hit`` (raw share of up calls with a positive / down calls with a
      negative return), ``long_run_pos_share`` / ``long_run_neg_share`` (the
      reference from ``long_run``), ``up_raw`` / ``down_raw``;
    * ``va_up_minus_down`` / ``va_hit_edge`` - value-add over the same-day
      shortlist (``va_available`` is False without pack candidates);
    * ``rank_ic``, ``calibration`` (+ Brier), ``expected_pct`` check.
    """
    h = int(horizon)
    f = joined[(joined["horizon_days"] == h) & joined["ret"].notna()]
    dec = f[f["sigma"] != 0]
    out: dict[str, Any] = {
        "horizon": h, "n_forecasts": int(len(f)),
        "n_up": int((f["sigma"] > 0).sum()), "n_down": int((f["sigma"] < 0).sum()),
        "n_neutral": int((f["sigma"] == 0).sum()),
        "n_dates": int(dec[DATE_COL].nunique()),
        "min_dates": reliable_dates(h),
    }
    out["reliable"] = out["n_dates"] >= out["min_dates"]
    if dec.empty:
        return out

    table = _per_date_table(dec)
    specs = _metric_specs(table)
    out["hit_rate"] = float(table["hit"].mean())
    out["selection_base_rate"] = float(table["base"].mean())
    out["selection_hit_edge_t_nw"] = mean_test(
        table["sel_edge"].dropna(), nw_lag_for_horizon(h))["t_nw"]
    out.update(_intervals(table, h, specs, intervals))
    out["n_obs"] = {k: v[1] for k, v in specs.items()}
    out["va_available"] = bool(dec["bench_from_bucket"].any())
    pos, neg = long_run if long_run is not None else (float("nan"), float("nan"))
    out["long_run_pos_share"], out["long_run_neg_share"] = pos, neg

    both = table.dropna(subset=["up_raw", "dn_raw"])
    out["n_paired_dates"] = int(len(both))
    out["n_obs"]["paired_up_minus_down"] = int(len(both))
    if len(both):
        diff = float((both["up_raw"] - both["dn_raw"]).mean())
        out["paired_up_minus_down"] = (
            batch_jackknife_diff(both["up_raw"], both["dn_raw"], h)
            if intervals and has_batch_ci(len(both), h)
            else (diff, float("nan"), float("nan"))
        )

    ic = daily_rank_ic(f, "signed_conviction", "ret", DATE_COL, min_obs=MIN_IC_OBS)
    out["rank_ic"] = ic_summary(ic, h) if len(ic) else {}
    out.update(calibration(dec))
    out.update(_expected_pct_check(dec))
    return out


def calibration(dec: pd.DataFrame, bins: Sequence[float] = CONFIDENCE_BINS) -> dict:
    """Calibration of ``confidence`` (= P(direction correct)) on directional rows.

    Returns ``calibration`` (DataFrame: bin, n, mean_confidence, hit_rate),
    ``brier`` (mean squared error of the confidence), ``brier_ref`` (the same
    for the constant forecaster 0.5 - known at time d, so it cannot peek at
    the outcome), ``brier_skill`` (1 - brier / brier_ref), and as information
    only ``brier_ref_pooled`` / ``brier_skill_pooled`` against the constant
    forecaster that says the pooled whole-sample hit rate (known only
    afterwards, never the realised same-day base rate), plus
    ``mean_confidence``.
    """
    d = dec[dec["hit"].notna() & dec["confidence"].notna()]
    nan = float("nan")
    if d.empty:
        return {
            "calibration": pd.DataFrame(
                columns=["bin", "n", "mean_confidence", "hit_rate"]),
            "brier": nan, "brier_ref": nan, "brier_skill": nan,
            "brier_ref_pooled": nan, "brier_skill_pooled": nan,
            "pooled_hit_rate": nan, "mean_confidence": nan, "n_calibration": 0,
        }
    cut = pd.cut(d["confidence"], bins=list(bins), right=False)
    tab = (
        d.assign(bin=cut).groupby("bin", observed=True)
        .agg(n=("hit", "size"), mean_confidence=("confidence", "mean"),
             hit_rate=("hit", "mean"))
        .reset_index()
    )
    tab["bin"] = tab["bin"].astype(str)
    brier = float(((d["confidence"] - d["hit"]) ** 2).mean())
    ref = float(((BRIER_REFERENCE_P - d["hit"]) ** 2).mean())
    pooled = float(d["hit"].mean())
    ref_pooled = float(((pooled - d["hit"]) ** 2).mean())
    return {
        "calibration": tab, "brier": brier, "brier_ref": ref,
        "brier_skill": 1.0 - brier / ref if ref > 0 else nan,
        "brier_ref_pooled": ref_pooled,
        "brier_skill_pooled": 1.0 - brier / ref_pooled if ref_pooled > 0 else nan,
        "pooled_hit_rate": pooled,
        "mean_confidence": float(d["confidence"].mean()),
        "n_calibration": int(len(d)),
    }


def _expected_pct_check(dec: pd.DataFrame) -> dict[str, Any]:
    """Rank correlation and bias of the optional ``expected_pct`` (signed, %)."""
    d = dec[dec["expected_pct"].notna() & dec["ret"].notna()]
    out: dict[str, Any] = {"expected_pct_n": int(len(d))}
    if len(d) < MIN_EXPECTED_PCT_OBS:
        return out
    exp = d["expected_pct"] / 100.0
    out["expected_pct_rank_corr"] = float(exp.corr(d["ret"], method="spearman"))
    out["expected_pct_bias"] = float((exp - d["ret"]).mean())
    return out


# ── Drivers and comparison groups ────────────────────────────────────


def _driver_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [v.strip().lower() for v in value.split(",") if v.strip()]
    if isinstance(value, Iterable):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return []


_DRIVER_COLUMNS = ["driver", "n", "hit_rate", "selection_hit_edge", "signed_ex_uni"]


def by_driver(joined: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Descriptive table per named driver (no inference, small cells).

    Columns: driver, n, hit_rate, selection_hit_edge (hit rate minus the
    same-day universe share), signed_ex_uni (mean of ``sigma * excess over the
    universe``).
    """
    if "drivers" not in joined.columns:
        return pd.DataFrame(columns=_DRIVER_COLUMNS)
    d = joined[(joined["horizon_days"] == horizon) & joined["hit"].notna()].copy()
    d["driver"] = d["drivers"].map(_driver_list)
    d = d.explode("driver").dropna(subset=["driver"])
    if d.empty:
        return pd.DataFrame(columns=_DRIVER_COLUMNS)
    d["sgn_uni"] = d["sigma"] * d["ex_uni"]
    d["selection_hit_edge"] = d["hit"] - d["base_hit"]
    return (
        d.groupby("driver").agg(n=("hit", "size"), hit_rate=("hit", "mean"),
                                selection_hit_edge=("selection_hit_edge", "mean"),
                                signed_ex_uni=("sgn_uni", "mean"))
        .reset_index().sort_values("n", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def compare_groups(
    joined: pd.DataFrame,
    universe_returns: pd.DataFrame,
    pack_candidates: pd.DataFrame,
    horizon: int,
    spy_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Claude's up / down calls vs the shortlist, random controls and universe.

    Evaluated on the sessions where Claude made at least one directional
    on-time forecast. Rows: ``Claude hoch``, ``Claude runter``, one row per
    pack bucket (``bull`` shortlist, ``bear`` shortlist, ``random``
    controls, ``requested``), ``Universum``. Columns: n_obs, n_dates,
    mean_raw, ex_uni (+ batch-jackknife CI, ``n_ci`` = dates behind it) and,
    if SPY is given, ex_spy (+ CI), plus the row ``Claude hoch minus Shortlist
    bullisch`` (paired per session).
    """
    h = int(horizon)
    col = f"return_{h}d"
    f = joined[(joined["horizon_days"] == h) & joined["ret"].notna()
               & (joined["sigma"] != 0) & joined["on_time"]]
    days = pd.DatetimeIndex(sorted(f[DATE_COL].unique()))
    uni = _universe_frame(universe_returns)
    uni = uni[uni[col].notna() & uni[DATE_COL].isin(days)]
    uni_mean = uni[uni["gate_ok"]].groupby(DATE_COL)[col].mean()

    spy_frame = _spy_frame(spy_returns)
    spy = spy_frame[col] if spy_frame is not None and col in spy_frame.columns else None

    groups: dict[str, pd.DataFrame] = {
        "Claude hoch": f[f["sigma"] > 0][[DATE_COL, "ret"]],
        "Claude runter": f[f["sigma"] < 0][[DATE_COL, "ret"]],
    }
    labels = {"bull": "Shortlist bullisch", "bear": "Shortlist bärisch",
              "random": "Zufalls-Kontrollen", "requested": "Wunsch-Ticker"}
    pc = _pack_frame(pack_candidates)
    if pc is not None:
        pj = pc.merge(uni[[DATE_COL, "ticker", col]], on=[DATE_COL, "ticker"],
                      how="inner")
        for bucket, label in labels.items():
            sub = pj[pj["bucket"] == bucket]
            if len(sub):
                groups[label] = sub[[DATE_COL, col]].rename(columns={col: "ret"})
    gated_uni = uni[uni["gate_ok"]]
    groups["Universum"] = gated_uni[[DATE_COL, col]].rename(columns={col: "ret"})

    def _ci_of(m: pd.Series, ref: pd.Series) -> tuple[float, float, float, int]:
        d = (m - ref).dropna()
        if d.empty:
            nan = float("nan")
            return nan, nan, nan, 0
        if not has_batch_ci(len(d), h):
            return float(d.mean()), float("nan"), float("nan"), int(len(d))
        est, lo, hi = batch_jackknife_diff(m, ref, h)
        return est, lo, hi, int(len(d))

    rows = []
    means: dict[str, pd.Series] = {}
    for label, g in groups.items():
        m = g.groupby(DATE_COL)["ret"].mean()
        m = m[m.index.isin(days)]
        means[label] = m
        if m.empty:
            continue
        ex = _ci_of(m, uni_mean)
        row: dict[str, Any] = {
            "group": label, "n_obs": int(len(g[g[DATE_COL].isin(days)])),
            "n_dates": int(m.size), "mean_raw": float(m.mean()),
            "ex_uni": ex[0], "ex_uni_lo": ex[1], "ex_uni_hi": ex[2], "n_ci": ex[3],
        }
        if spy is not None:
            sp = _ci_of(m, spy)
            row.update(ex_spy=sp[0], ex_spy_lo=sp[1], ex_spy_hi=sp[2])
        rows.append(row)
    if "Claude hoch" in means and "Shortlist bullisch" in means:
        d = _ci_of(means["Claude hoch"], means["Shortlist bullisch"])
        rows.append({
            "group": "Claude hoch minus Shortlist bullisch", "n_dates": d[3],
            "ex_uni": d[0], "ex_uni_lo": d[1], "ex_uni_hi": d[2], "n_ci": d[3],
        })
    return pd.DataFrame(rows)


# ── Decisive criterion: freeze -> verdict ────────────────────────────


def sample_key(frozen: Mapping[str, Any]) -> str:
    """SHA-256 over horizon, version, session list and forecast ids of a sample."""
    text = "\n".join([
        f"h={int(frozen['horizon'])}", f"v={frozen['forecaster_version']}",
        "sessions=" + ",".join(frozen["sessions"]),
        "ids=" + ",".join(sorted(frozen["forecast_ids"])),
    ])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _freeze_version(
    sel: pd.DataFrame, horizon: int, version: str, need: int, per_side: int
) -> dict[str, Any]:
    """Freeze rule for the on-time directional forecasts of ONE version."""
    days = sorted(sel[DATE_COL].unique())
    daily = pd.DataFrame({
        "up": (sel["sigma"] > 0).groupby(sel[DATE_COL]).sum(),
        "down": (sel["sigma"] < 0).groupby(sel[DATE_COL]).sum(),
    }).reindex(days).fillna(0)
    cum = daily.cumsum()
    progress = {
        "dates": len(days), "dates_needed": need, "per_side_needed": per_side,
        "up": int(cum["up"].iloc[-1]) if len(cum) else 0,
        "down": int(cum["down"].iloc[-1]) if len(cum) else 0,
    }
    base: dict[str, Any] = {
        "horizon": horizon, "forecaster_version": version, "min_dates": need,
        "min_per_side": per_side, "progress": progress,
        "first_date": pd.Timestamp(days[0]).date() if days else None,
    }
    enough = (np.arange(1, len(days) + 1) >= need) & (
        (cum["up"] >= per_side) & (cum["down"] >= per_side)).to_numpy()
    if not enough.any():
        return {**base, "status": "insufficient_data"}
    m = int(np.argmax(enough)) + 1
    chosen = sel[sel[DATE_COL].isin(days[:m])]
    frozen = {
        **base, "status": "frozen", "n_dates": m,
        "last_date": pd.Timestamp(days[m - 1]).date(),
        "sessions": [pd.Timestamp(d).date().isoformat() for d in days[:m]],
        "forecast_ids": sorted(chosen["forecast_id"]),
        "up": int((chosen["sigma"] > 0).sum()),
        "down": int((chosen["sigma"] < 0).sum()),
    }
    frozen["progress"] = {**progress, "dates": m, "up": frozen["up"],
                          "down": frozen["down"]}
    frozen["key"] = sample_key(frozen)
    return frozen


def freeze_sample(
    forecasts: pd.DataFrame,
    horizon: int = PRIMARY_HORIZON,
    forecaster_version: str | None = None,
    min_dates: int | None = None,
    min_per_side: int = CRITERION_MIN_PER_SIDE,
) -> dict[str, Any]:
    """Fix the decisive sample from the FORECASTS alone (no return involved).

    The sample is the first ``m`` sessions with at least one on-time
    directional ``horizon``-day forecast of ONE forecaster version, where
    ``m`` is the smallest number >= ``min_dates`` (default
    :func:`reliable_dates`, 200 at 20 days) for which the sample holds at
    least ``min_per_side`` up AND down forecasts. Everything it uses is known
    at forecast time, so whether and when a sample freezes cannot depend on
    returns that happen to be (un)available. Late and neutral forecasts do
    not count.

    A change of the forecaster version (prompt / skill / model) automatically
    starts a new sample: the active version is that of the newest on-time
    directional forecast (or ``forecaster_version``); the other versions are
    listed under ``earlier_versions`` as ``frozen`` (reached the size) or
    ``aborted`` (partial sample, never evaluated).

    Returns ``status`` ``insufficient_data`` (with ``progress``) or ``frozen``
    with ``sessions`` (ISO dates), ``forecast_ids``, counts and a ``key``
    (:func:`sample_key`). Step 4 persists a frozen sample once.
    """
    h = int(horizon)
    need = int(min_dates or reliable_dates(h))
    f = prepare_forecasts(forecasts)
    base = f[(f["horizon_days"] == h) & (f["sigma"] != 0) & f["on_time"]]
    if base.empty:
        return _freeze_version(base, h, forecaster_version or UNVERSIONED, need,
                               int(min_per_side)) | {"earlier_versions": []}
    newest = base.sort_values([DATE_COL, "forecaster_version"], kind="stable")
    active = forecaster_version or str(newest["forecaster_version"].iloc[-1])
    out = _freeze_version(base[base["forecaster_version"] == active], h, active,
                          need, int(min_per_side))
    others = []
    for version in sorted(set(base["forecaster_version"]) - {active}):
        res = _freeze_version(base[base["forecaster_version"] == version], h,
                              version, need, int(min_per_side))
        others.append({
            "forecaster_version": version,
            "status": "frozen" if res["status"] == "frozen" else "aborted",
            "first_date": res["first_date"], "progress": res["progress"],
        })
    others.sort(key=lambda o: (o["first_date"], o["forecaster_version"]))
    out["earlier_versions"] = others
    return out


def _mode_checks(
    table: pd.DataFrame, horizon: int, has_benchmark: bool
) -> dict[str, dict[str, Any]]:
    """Both decisive statistics of both criterion modes with their intervals."""
    ix = {c: i for i, c in enumerate(table.columns)}
    n = {c: int(table[c].notna().sum()) for c in table.columns}
    stats = {
        "system:relative_up_minus_down": _diff_of_means(ix["up_uni"], ix["dn_uni"]),
        "system:selection_hit_edge": _mean_of(ix["sel_edge"]),
        "claude_value_add:relative_up_minus_down":
            _diff_of_means(ix["va_up"], ix["va_dn"]),
        "claude_value_add:selection_hit_edge": _mean_of(ix["va_hit"]),
    }
    counts = {
        "system:relative_up_minus_down": min(n["up_uni"], n["dn_uni"]),
        "system:selection_hit_edge": n["sel_edge"],
        "claude_value_add:relative_up_minus_down": min(n["va_up"], n["va_dn"]),
        "claude_value_add:selection_hit_edge": n["va_hit"],
    }
    res = batch_jackknife(table.to_numpy(dtype=float), stats, horizon)
    modes: dict[str, dict[str, Any]] = {}
    for mode in CRITERION_MODES:
        checks = {}
        for key, (est, lo, hi) in res.items():
            m, name = key.split(":")
            if m != mode:
                continue
            checks[name] = {"estimate": est, "ci_low": lo, "ci_high": hi,
                            "n_obs": counts[key], "passed": bool(lo > 0)}
        if mode == "claude_value_add" and not has_benchmark:
            modes[mode] = {"status": "not_available", "checks": checks}
            continue
        passed = all(c["passed"] for c in checks.values())
        modes[mode] = {"status": "passed" if passed else "failed", "checks": checks}
    return modes


def _verdict_summary(
    frozen: Mapping[str, Any], modes: dict[str, dict[str, Any]],
    decisive_mode: str, **extra: Any,
) -> dict[str, Any]:
    h = int(frozen["horizon"])
    return {
        "horizon": h, "registered": h == PRIMARY_HORIZON,
        "forecaster_version": frozen["forecaster_version"],
        "decisive_mode": decisive_mode, "status": modes[decisive_mode]["status"],
        "frozen": _frozen_meta(frozen), "progress": frozen["progress"],
        "earlier_versions": frozen.get("earlier_versions", []),
        "modes": modes, "checks": modes[decisive_mode]["checks"], **extra,
    }


def _frozen_meta(frozen: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("n_dates", "first_date", "last_date", "up", "down", "key")
    return {k: frozen[k] for k in keep if k in frozen}


def _check_mode(mode: str) -> str:
    if mode not in CRITERION_MODES:
        raise ValueError(f"unknown criterion mode {mode!r}; use {CRITERION_MODES}")
    return mode


def verdict_from_values(
    frozen: Mapping[str, Any],
    values: pd.DataFrame,
    decisive_mode: str = DEFAULT_CRITERION_MODE,
    n_missing: int = 0,
) -> dict[str, Any]:
    """The one-look verdict from the realised values of a frozen sample.

    ``values`` holds one row per frozen forecast with a realised return
    (:data:`VALUE_COLUMNS`; the ``values`` of an earlier verdict reproduce it
    exactly, no matter what later happens to the underlying returns). ``passed``
    needs BOTH decisive statistics of the ``decisive_mode`` to have a 95 %
    interval above zero:

    * relative: mean excess return of the up forecasts minus that of the down
      forecasts (``system``: excess over the gated universe;
      ``claude_value_add``: excess over the same-day shortlist of the called
      direction);
    * selection hit edge: hit rate minus the same-day share of the universe
      with the called sign (``claude_value_add``: of the shortlist).

    Both modes are always computed; ``status`` is that of ``decisive_mode``.
    """
    decisive_mode = _check_mode(decisive_mode)
    h = int(frozen["horizon"])
    vals = values.copy()
    vals[DATE_COL] = _day(vals[DATE_COL])
    if "bench_from_bucket" not in vals.columns:
        vals["bench_from_bucket"] = False
    vals["bench_from_bucket"] = vals["bench_from_bucket"].fillna(False).astype(bool)
    table = _per_date_table(vals)
    modes = _mode_checks(table, h, bool(vals["bench_from_bucket"].any()))
    records = [{c: (r[c].date().isoformat() if c == DATE_COL else r[c])
                for c in VALUE_COLUMNS if c in vals.columns}
               for _, r in vals.iterrows()]
    return _verdict_summary(
        frozen, modes, decisive_mode, n_sessions_used=int(len(table)),
        n_missing=int(n_missing), values=records,
    )


def verdict_from_frozen(
    frozen: Mapping[str, Any],
    joined: pd.DataFrame,
    elapsed_sessions: int | None = None,
    grace_sessions: int = CRITERION_GRACE_SESSIONS,
    decisive_mode: str = DEFAULT_CRITERION_MODE,
) -> dict[str, Any]:
    """Verdict of a frozen sample, computed once its windows are realised.

    ``joined`` is a frame from :func:`attach_outcomes`; only the rows of the
    frozen ``forecast_ids`` are used (late and other-version forecasts, even
    inside the frozen sessions, never enter). While frozen windows are still
    open the status is ``awaiting_returns``; it turns into ``passed`` /
    ``failed`` as soon as all returns exist, or once ``elapsed_sessions``
    (sessions after the last frozen one, see :func:`sessions_after`) reach the
    horizon plus ``grace_sessions`` - missing returns are then reported as
    ``n_missing``. Step 4 persists the result (with its ``values``) once and
    never recomputes it from live returns.
    """
    decisive_mode = _check_mode(decisive_mode)
    if frozen.get("status") != "frozen":
        return {
            "horizon": int(frozen["horizon"]),
            "registered": int(frozen["horizon"]) == PRIMARY_HORIZON,
            "forecaster_version": frozen["forecaster_version"],
            "decisive_mode": decisive_mode, "status": "insufficient_data",
            "progress": frozen["progress"],
            "earlier_versions": frozen.get("earlier_versions", []),
        }
    h = int(frozen["horizon"])
    ids = set(frozen["forecast_ids"])
    rows = joined[joined["forecast_id"].isin(ids)]
    realised = rows[rows["ret"].notna()]
    n_open = len(ids) - len(realised)
    deadline = h + int(grace_sessions)
    if n_open > 0 and (elapsed_sessions is None or elapsed_sessions < deadline):
        modes = {m: {"status": "awaiting_returns", "checks": {}}
                 for m in CRITERION_MODES}
        return _verdict_summary(
            frozen, modes, decisive_mode, realised=int(len(realised)),
            total=len(ids), deadline_sessions=deadline,
            elapsed_sessions=elapsed_sessions,
        )
    return verdict_from_values(frozen, realised, decisive_mode, n_missing=n_open)


def primary_criterion(
    forecasts: pd.DataFrame,
    universe_returns: pd.DataFrame,
    spy_returns: pd.DataFrame | None = None,
    pack_candidates: pd.DataFrame | None = None,
    horizon: int = PRIMARY_HORIZON,
    forecaster_version: str | None = None,
    min_dates: int | None = None,
    min_per_side: int = CRITERION_MIN_PER_SIDE,
    decisive_mode: str = DEFAULT_CRITERION_MODE,
    grace_sessions: int = CRITERION_GRACE_SESSIONS,
) -> dict[str, Any]:
    """:func:`freeze_sample` + :func:`verdict_from_frozen` on live data.

    A convenience for reports and tests; the nightly step of plan step 4 calls
    the two parts separately and persists their results.
    """
    frozen = freeze_sample(forecasts, horizon, forecaster_version, min_dates,
                           min_per_side)
    joined = attach_outcomes(forecasts, universe_returns, spy_returns,
                             pack_candidates)
    elapsed = (sessions_after(universe_returns, frozen["last_date"])
               if frozen["status"] == "frozen" else None)
    return verdict_from_frozen(frozen, joined, elapsed, grace_sessions,
                               decisive_mode)


# ── Orchestration and report ─────────────────────────────────────────


def evaluate_forecasts(
    forecasts: pd.DataFrame,
    universe_returns: pd.DataFrame,
    spy_returns: pd.DataFrame | None = None,
    pack_candidates: pd.DataFrame | None = None,
    horizons: Sequence[int] = HORIZONS,
    criterion_mode: str = DEFAULT_CRITERION_MODE,
    frozen: Mapping[int, Mapping[str, Any]] | None = None,
    verdicts: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full pre-registered evaluation of a forecast set.

    On-time forecasts get the full metrics per horizon; late forecasts
    (file written after the open of d+1) are summarised separately and never
    enter the criterion. ``pending`` counts forecasts whose window is still
    open. The criterion of every horizon present is reported (only 20 days is
    pre-registered, ``registered`` marks it); ``frozen`` / ``verdicts`` pass
    state persisted by an earlier run (plan step 4) - a given verdict is
    reported as it is, never recomputed.
    """
    _check_mode(criterion_mode)
    joined = attach_outcomes(forecasts, universe_returns, spy_returns,
                             pack_candidates)
    on_time = joined[joined["on_time"]]
    late = joined[~joined["on_time"]]
    res: dict[str, Any] = {
        "n_forecasts": int(len(joined)), "n_on_time": int(len(on_time)),
        "n_late": int(len(late)),
        "first_session": joined[DATE_COL].min() if len(joined) else None,
        "last_session": joined[DATE_COL].max() if len(joined) else None,
        "criterion_mode": criterion_mode, "horizons": {}, "late": {},
    }
    for h in horizons:
        if not (joined["horizon_days"] == h).any():
            continue
        lr = long_run_shares(universe_returns, h)
        entry = evaluate_direction(on_time, h, long_run=lr)
        entry["pending"] = int(((on_time["horizon_days"] == h)
                                & on_time["ret"].isna()).sum())
        entry["by_driver"] = by_driver(on_time, h)
        if pack_candidates is not None and len(pack_candidates):
            entry["comparison"] = compare_groups(
                on_time, universe_returns, pack_candidates, h, spy_returns)
        res["horizons"][h] = entry
        if len(late):
            res["late"][h] = evaluate_direction(late, h, intervals=False)
    for h in (PRIMARY_HORIZON, 60):
        if not (joined["horizon_days"] == h).any():
            continue
        if verdicts and h in verdicts:
            res.setdefault("criterion", {})[h] = dict(verdicts[h])
            continue
        fz = (frozen or {}).get(h) or freeze_sample(joined, h)
        elapsed = (sessions_after(universe_returns, fz["last_date"])
                   if fz["status"] == "frozen" else None)
        res.setdefault("criterion", {})[h] = verdict_from_frozen(
            fz, joined, elapsed, decisive_mode=criterion_mode)
    return res


def _d(x: Any) -> str:
    return DASH if x is None else f"{pd.Timestamp(x):%Y-%m-%d}"


def render_report(result: dict[str, Any], today: date | None = None) -> str:
    """German Markdown report (``context_packs/forecast_auswertung.md``)."""
    day = today or date.today()
    lines = [
        f"# Prognose-Auswertung – Stand {day.isoformat()}",
        "",
        "Claude prognostiziert die **absolute** Richtung (Kurs steigt / fällt) je "
        "Ticker und Horizont; ausgewertet wird absolut und relativ "
        "(Überschuss gegenüber dem Mittel des Gate-Universums am selben Tag und "
        "gegenüber SPY), datumsgewichtet, KI = Batch-Jackknife (Batches von "
        "2 × Horizont Sitzungen, t-Verteilung). Signifikanz-Marker erst ab 10 × "
        "Horizont Sitzungen mit Daten für die jeweilige Kennzahl. "
        "Bewertungsregeln: `docs/FORECASTS.md`.",
        "",
    ]
    if not result["n_forecasts"]:
        return "\n".join([*lines, "Noch keine `forecasts.yaml` eingelesen.", ""])
    lines += [
        f"- Prognosen: {result['n_forecasts']} (rechtzeitig {result['n_on_time']}, "
        f"verspätet {result['n_late']}) · Sitzungen {_d(result['first_session'])} … "
        f"{_d(result['last_session'])}",
        "",
    ]
    for h, crit in result.get("criterion", {}).items():
        lines += _criterion_lines(h, crit)
    for h, r in result["horizons"].items():
        lines += _horizon_lines(h, r)
    late = result.get("late") or {}
    if late:
        lines += ["## Verspätete Prognosen (nur beschreibend)", ""]
        for h, r in late.items():
            if r.get("n_up", 0) + r.get("n_down", 0):
                lines.append(
                    f"- {h} Tage: {r['n_up']} hoch, {r['n_down']} runter · "
                    f"Trefferquote {_pct(r.get('hit_rate'), 1, False)} "
                    f"(Basisrate {_pct(r.get('selection_base_rate'), 1, False)})"
                )
        lines.append("")
    lines += ["---", "*Automatisch erzeugt · Keine Anlageberatung*", ""]
    return "\n".join(lines)


_CHECK_NAMES = {
    "relative_up_minus_down": "relativ: Hoch minus Runter (Überschuss)",
    "selection_hit_edge": "Auswahl: Trefferquote minus Basisrate",
}
_MODE_NAMES = {
    "system": "System: Claudes Prognosen gegen das Gate-Universum",
    "claude_value_add": "Mehrwert: Claudes Prognosen gegen die Shortlist desselben "
                        "Tages",
}


def _mode_table(mode: str, info: dict[str, Any], h: int) -> list[str]:
    status = info["status"]
    if status == "not_available":
        return [f"*{_MODE_NAMES[mode]}:* nicht verfügbar (keine Shortlist-Daten "
                "in `pack_candidates`).", ""]
    out = [f"*{_MODE_NAMES[mode]}* – "
           f"{'bestanden' if status == 'passed' else 'nicht bestanden'}", "",
           "| Prüfung | Schätzer (95 %-KI) | |", "|---|---|---|"]
    for key, chk in info["checks"].items():
        mark = ci_supports_verdict(chk["n_obs"], h)
        out.append(
            f"| {_CHECK_NAMES[key]} | "
            f"{_ci(chk['estimate'], chk['ci_low'], chk['ci_high'], mark=mark)} | "
            f"{'✅' if chk['passed'] else '❌'} |")
    return [*out, ""]


def _criterion_lines(h: int, c: dict[str, Any]) -> list[str]:
    p = c["progress"]
    tag = ("vorab registriert, Vorschlag" if c.get("registered")
           else "nicht registriert – nur zur Orientierung")
    head = f"## Entscheidungskriterium {h} Tage ({tag})"
    lines = [head, "", f"Prognose-Version: `{c['forecaster_version']}`"]
    for old in c.get("earlier_versions", []):
        what = ("erreichte die Stichprobengröße" if old["status"] == "frozen"
                else "abgebrochene Teilstichprobe, nicht ausgewertet")
        lines.append(f"- frühere Version `{old['forecaster_version']}` seit "
                     f"{_d(old['first_date'])}: {what} "
                     f"({old['progress']['dates']} Sitzungen, "
                     f"{old['progress']['up']} hoch, {old['progress']['down']} "
                     "runter)")
    lines.append("")
    if c["status"] == "insufficient_data":
        return [*lines, f"**Noch kein Urteil:** {p['dates']}/{p['dates_needed']} "
                f"Sitzungen, {p['up']}/{p['per_side_needed']} Hoch-, "
                f"{p['down']}/{p['per_side_needed']} Runter-Prognosen. "
                "Bis dahin sind alle Zahlen nur beschreibend.", ""]
    fz = c["frozen"]
    sample = (f"eingefrorene Stichprobe: {fz['n_dates']} Sitzungen "
              f"{fz['first_date']} … {fz['last_date']}, {fz['up']} hoch, "
              f"{fz['down']} runter, Schlüssel `{str(fz['key'])[:12]}`")
    if c["status"] == "awaiting_returns":
        return [*lines, f"**Stichprobe eingefroren, Urteil ausstehend** ({sample}); "
                f"{c['realised']}/{c['total']} Prognosen haben ihre Rendite; das "
                f"Urteil folgt, sobald alle Fenster abgeschlossen sind, spätestens "
                f"{c['deadline_sessions']} Sitzungen nach der letzten Sitzung der "
                "Stichprobe.", ""]
    if c["status"] == "not_available":
        return [*lines, f"**Urteil nicht verfügbar** ({sample}): der entscheidende "
                "Modus braucht Shortlist-Daten.", ""]
    verdict = "bestanden" if c["status"] == "passed" else "nicht bestanden"
    missing = (f"; {c['n_missing']} Prognosen ohne Rendite ausgelassen"
               if c.get("n_missing") else "")
    lines += [f"**Urteil: {verdict}** (entscheidend: "
              f"`{c['decisive_mode']}`; {sample}{missing})", ""]
    for mode in CRITERION_MODES:
        if mode in c["modes"]:
            lines += _mode_table(mode, c["modes"][mode], h)
    return lines


def _fmt(r: dict[str, Any], key: str, h: int) -> str:
    """Interval cell of ``r[key]``; a marker only with enough sessions."""
    x = r.get(key)
    if not isinstance(x, tuple):
        return DASH
    n = r.get("n_obs", {}).get(key)
    return _ci(*x, mark=n is not None and ci_supports_verdict(n, h))


def _horizon_lines(h: int, r: dict[str, Any]) -> list[str]:
    lines = [f"## Horizont {h} Handelstage", ""]
    lines.append(
        f"{r['n_up']} hoch, {r['n_down']} runter, {r['n_neutral']} neutral "
        f"abgeschlossen ({r['n_dates']} Sitzungen), {r.get('pending', 0)} noch offen."
    )
    if not r["reliable"]:
        lines.append(
            f"**Nur beschreibend:** belastbar erst ab {r['min_dates']} Sitzungen "
            f"(10 unabhängige {h}-Tage-Perioden), vorhanden {r['n_dates']}."
        )
    lines.append("")
    if "hit_rate" not in r:
        return [*lines, "Keine abgeschlossenen Richtungs-Prognosen.", ""]
    lines += [
        "| Kennzahl | Absolut | Relativ zum Universum | Relativ zu SPY |",
        "|---|---|---|---|",
        f"| Hoch-Prognosen Ø Rendite | {_fmt(r, 'up_raw', h)} | "
        f"{_fmt(r, 'up_ex_uni', h)} | {_fmt(r, 'up_ex_spy', h)} |",
        f"| Runter-Prognosen Ø Rendite | {_fmt(r, 'down_raw', h)} | "
        f"{_fmt(r, 'down_ex_uni', h)} | {_fmt(r, 'down_ex_spy', h)} |",
        f"| Hoch minus Runter | {_fmt(r, 'up_minus_down_raw', h)} | "
        f"{_fmt(r, 'up_minus_down_ex_uni', h)} | "
        f"{_fmt(r, 'up_minus_down_ex_spy', h)} |",
        f"| Prognose folgen (σ · Rendite) | {_fmt(r, 'signed_raw', h)} | "
        f"{_fmt(r, 'signed_ex_uni', h)} | {_fmt(r, 'signed_ex_spy', h)} |",
        "",
        "- Auswahl-Trefferquote (querschnittlich, Markt-Timing zählt 0): "
        f"Trefferquote {_pct(r['hit_rate'], 1, False)} gegen den Anteil des "
        f"Universums am selben Tag mit gleichem Vorzeichen "
        f"{_pct(r['selection_base_rate'], 1, False)} → Differenz "
        f"{_fmt(r, 'selection_hit_edge', h)} "
        f"(t NW {_num(r.get('selection_hit_edge_t_nw'))}); hoch "
        f"{_fmt(r, 'up_selection_hit_edge', h)}, runter "
        f"{_fmt(r, 'down_selection_hit_edge', h)}",
    ]
    if "paired_up_minus_down" in r:
        lines.append(f"- Hoch minus Runter, gepaart an {r['n_paired_dates']} Sitzungen "
                     f"mit beiden Richtungen (Markt hebt sich auf): "
                     f"{_fmt(r, 'paired_up_minus_down', h)}")
    lines += _absolute_lines(r, h)
    if r.get("va_available"):
        lines.append(f"- Mehrwert gegenüber der Shortlist desselben Tages: Hoch "
                     f"minus Runter {_fmt(r, 'va_up_minus_down', h)}, Auswahl-"
                     f"Trefferquote {_fmt(r, 'va_hit_edge', h)}")
    ic = r.get("rank_ic") or {}
    if ic:
        lines.append(f"- Rang-IC (Konviktion × Richtung vs. Rendite): "
                     f"{_num(ic.get('mean_ic'), 3)} (t NW {_num(ic.get('t_nw'))}, "
                     f"{ic.get('n_dates', 0)} Sitzungen)")
    if r.get("n_calibration"):
        lines.append(
            f"- Kalibrierung: mittlere Konfidenz "
            f"{_pct(r['mean_confidence'], 1, False)}, Brier {_num(r['brier'], 4)} "
            f"(konstant 0,5: {_num(r['brier_ref'], 4)}, Skill "
            f"{_num(r['brier_skill'], 3)}; zur Information gegen die gepoolte "
            f"Trefferquote {_pct(r['pooled_hit_rate'], 1, False)}: "
            f"{_num(r['brier_ref_pooled'], 4)}, Skill "
            f"{_num(r['brier_skill_pooled'], 3)})"
        )
        cal = r["calibration"]
        lines += ["", "| Konfidenz-Bin | n | Ø Konfidenz | Trefferquote |",
                  "|---|---|---|---|"]
        for _, row in cal.iterrows():
            lines.append(f"| {row['bin']} | {int(row['n'])} | "
                         f"{_pct(row['mean_confidence'], 1, False)} | "
                         f"{_pct(row['hit_rate'], 1, False)} |")
    if "expected_pct_rank_corr" in r:
        lines.append(f"\n- `expected_pct`: Rang-Korrelation "
                     f"{_num(r['expected_pct_rank_corr'], 3)}, Bias "
                     f"{_pct(r['expected_pct_bias'])} (n = {r['expected_pct_n']})")
    lines += _comparison_lines(r.get("comparison"), h)
    drv = r.get("by_driver")
    if drv is not None and len(drv):
        lines += ["", "### Nach Treibern (beschreibend)", "",
                  "| Treiber | n | Trefferquote | Auswahl-Trefferquote minus "
                  "Basisrate | σ · Überschuss |", "|---|---|---|---|---|"]
        for _, row in drv.iterrows():
            lines.append(f"| {row['driver']} | {int(row['n'])} | "
                         f"{_pct(row['hit_rate'], 1, False)} | "
                         f"{_pct(row['selection_hit_edge'])} | "
                         f"{_pct(row['signed_ex_uni'])} |")
    lines.append("")
    return lines


def _absolute_lines(r: dict[str, Any], h: int) -> list[str]:
    """TRUE absolute block: raw hit rates against 50 % and the long-run share."""
    pos, neg = r.get("long_run_pos_share"), r.get("long_run_neg_share")
    ref = (f"der langfristige Anteil positiver {h}-Tage-Renditen "
           f"({_pct(pos, 1, False)}) bzw. negativer ({_pct(neg, 1, False)})"
           if pos is not None and pos == pos else "kein Langfrist-Referenzwert")
    return [
        "",
        "### Absolut (nur beschreibend, nicht entscheidend)",
        "",
        "Rohe Trefferquote ohne Bezug zum Universum – über wenige Monate "
        "dominiert der Verlauf des Gesamtmarkts. Referenzen: 50 % sowie "
        f"{ref}.",
        "",
        "| Prognose | Rohe Trefferquote (95 %-KI) | vs. 50 % | vs. Langfrist-Anteil "
        "| Ø Rohrendite |",
        "|---|---|---|---|---|",
        _abs_row("Hoch (Rendite > 0)", r, "up_hit", "up_raw", pos),
        _abs_row("Runter (Rendite < 0)", r, "down_hit", "down_raw", neg),
    ]


def _abs_row(
    label: str, r: dict[str, Any], hit_key: str, raw_key: str, ref: Any
) -> str:
    hit = r.get(hit_key)
    est = hit[0] if isinstance(hit, tuple) else float("nan")
    # descriptive: interval shown without a verdict marker
    cell = (_ci(*hit, 1, mark=False, signed=False) if isinstance(hit, tuple)
            else DASH)
    vs_ref = _pct(est - ref) if ref == ref and ref is not None else DASH
    raw = r.get(raw_key)
    return (f"| {label} | {cell} | {_pct(est - 0.5)} | {vs_ref} | "
            f"{_pct(raw[0]) if isinstance(raw, tuple) else DASH} |")


def _comparison_lines(comp: pd.DataFrame | None, h: int) -> list[str]:
    if comp is None or not len(comp):
        return []
    has_spy = "ex_spy" in comp.columns
    head = ("| Gruppe | Beobachtungen | Sitzungen | Ø Rendite | "
            "Überschuss vs. Universum"
            + (" | Überschuss vs. SPY" if has_spy else "") + " |")
    lines = ["", "### Claude gegen Shortlist, Zufalls-Kontrollen, Universum", "",
             head, "|---|---|---|---|---|" + ("---|" if has_spy else "")]
    for _, row in comp.iterrows():
        n_obs = row.get("n_obs")
        mark = ci_supports_verdict(int(row.get("n_ci") or 0), h)
        cells = [
            row["group"],
            str(int(n_obs)) if n_obs == n_obs and n_obs is not None else DASH,
            str(int(row["n_dates"])), _pct(row.get("mean_raw")),
            _ci(row["ex_uni"], row["ex_uni_lo"], row["ex_uni_hi"], mark=mark),
        ]
        if has_spy:
            cells.append(_ci(row.get("ex_spy"), row.get("ex_spy_lo"),
                             row.get("ex_spy_hi"), mark=mark))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


__all__ = [
    "BRIER_REFERENCE_P",
    "CRITERION_GRACE_SESSIONS",
    "CRITERION_MIN_PER_SIDE",
    "CRITERION_MODES",
    "DEFAULT_CRITERION_MODE",
    "DIRECTIONS",
    "HORIZONS",
    "PRIMARY_HORIZON",
    "UNVERSIONED",
    "VALUE_COLUMNS",
    "attach_outcomes",
    "by_driver",
    "calibration",
    "compare_groups",
    "evaluate_direction",
    "evaluate_forecasts",
    "freeze_sample",
    "long_run_shares",
    "prepare_forecasts",
    "primary_criterion",
    "reliable_dates",
    "render_report",
    "sample_key",
    "sessions_after",
    "verdict_from_frozen",
    "verdict_from_values",
]
