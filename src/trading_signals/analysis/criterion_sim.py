"""Size and power of the decisive forecast criterion by simulation.

Plan 2026-10-17 review M1: the claim "false pass rate <= 2.5 %" of the
decisive criterion (FORECASTS.md section 6) has to be measured, not argued.

World (all synthetic, no DB): daily returns ``market + idiosyncratic shock``
for ``n_tickers`` names; the ``h``-day forward return of a forecast on session
``d`` is the sum of the daily returns of ``d+1 .. d+h``, i.e. the returns of
consecutive sessions OVERLAP (the case that makes naive intervals too narrow).
A persistent latent state per name (AR(1), ``persistence``) decides the calls:
the ``calls_per_side`` highest states are called up and the lowest down, so a
no-skill forecaster keeps calling the SAME names on consecutive days. With
``n_sectors`` > 0 the names also share sector shocks (``sector_sd`` per day)
and the state has a sector component (``sector_weight`` of its variance), so
the calls cluster in a few sectors whose returns move together - the main way
real calls differ from independent picks.
``skill`` > 0 lets the state also drive the next days' returns (a planted
edge). Everything the forecaster uses at session ``d`` is the state at ``d``;
the returns it is scored on start at ``d+1``.

The simulated object is the real code: forecasts and returns go through
:func:`forecast_eval.attach_outcomes` and the per-session table of the
criterion; only the *interval method* is swapped (:data:`METHODS`), so
the numbers compare like with like. ``jackknife_2h`` is the production
method (``stats.batch_jackknife``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy import stats as _sps

from trading_signals.analysis import forecast_eval as fe
from trading_signals.analysis.stats import batch_jackknife

#: Interval methods compared. Each gives a 95 % two-sided interval whose lower
#: bound has to exceed zero for a pass (so the one-sided size is nominally 2.5 %).
METHODS: tuple[str, ...] = (
    "bootstrap_moving_h",
    "bootstrap_circular_h",
    "bootstrap_circular_2h",
    "bootstrap_stationary_2h",
    "newey_west_h",
    "newey_west_2h",
    "jackknife_h",
    "jackknife_2h",
)
#: The production method (``forecast_eval`` / ``stats.batch_jackknife``).
PRODUCTION_METHOD = "jackknife_2h"
#: The two decisive statistics of the ``system`` mode.
STATS = ("relative", "selection")


@dataclass(frozen=True)
class Scenario:
    """One simulated world (defaults: the 200-session minimum of the criterion)."""

    n_sessions: int = 200
    n_tickers: int = 300
    horizon: int = 20
    persistence: float = 0.95
    calls_per_side: int = 8
    skill: float = 0.0
    market_sd: float = 0.01
    idio_sd: float = 0.02
    #: Student-t degrees of freedom of the idiosyncratic shocks (None: normal).
    tail_df: int | None = None
    #: Sector structure: shocks shared within a sector, calls clustered by sector.
    n_sectors: int = 0
    sector_sd: float = 0.0
    sector_weight: float = 0.0


#: Named scenarios of the size report. All have ``skill = 0``.
SCENARIOS: dict[str, Scenario] = {
    "base (rho 0.95)": Scenario(),
    "no persistence (rho 0)": Scenario(persistence=0.0),
    "very persistent calls (rho 0.99)": Scenario(persistence=0.99),
    "15 calls per side (rho 0.9)": Scenario(persistence=0.9, calls_per_side=15),
    "300 sessions": Scenario(n_sessions=300),
    "400 sessions": Scenario(n_sessions=400),
    "heavy tails (t 4)": Scenario(tail_df=4),
    "calls clustered in sectors": Scenario(n_sectors=10, sector_sd=0.01,
                                           sector_weight=0.7),
    "horizon 60 (600 sessions)": Scenario(n_sessions=600, horizon=60),
    "horizon 5": Scenario(horizon=5),
}


# ── World ────────────────────────────────────────────────────────────


def _ar1(rng: np.random.Generator, days: int, n: int, rho: float) -> np.ndarray:
    """``n`` standardised AR(1) paths of ``days`` steps."""
    out = np.empty((days, n))
    out[0] = rng.normal(size=n)
    shock = math.sqrt(1.0 - rho ** 2)
    for t in range(1, days):
        out[t] = rho * out[t - 1] + shock * rng.normal(size=n)
    return out


def simulate_world(
    scenario: Scenario, rng: np.random.Generator
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(forecasts, universe_returns)`` frames of one simulated world."""
    sc = scenario
    h, n_t, n = sc.horizon, sc.n_sessions, sc.n_tickers
    days = n_t + h + 2
    market = rng.normal(0.0, sc.market_sd, days)
    if sc.tail_df:
        idio = (rng.standard_t(sc.tail_df, (days, n)) * sc.idio_sd
                / math.sqrt(sc.tail_df / (sc.tail_df - 2.0)))
    else:
        idio = rng.normal(0.0, sc.idio_sd, (days, n))
    state = _ar1(rng, days, n, sc.persistence)
    daily = market[:, None] + idio
    if sc.n_sectors > 0:
        sector_of = np.arange(n) % sc.n_sectors
        daily += rng.normal(0.0, sc.sector_sd, (days, sc.n_sectors))[:, sector_of]
        w = sc.sector_weight
        sector_state = _ar1(rng, days, sc.n_sectors, sc.persistence)
        state = math.sqrt(w) * sector_state[:, sector_of] + math.sqrt(1 - w) * state
    daily[1:] += sc.skill * state[:-1]  # the state of day t drives day t+1
    cum = np.vstack([np.zeros((1, n)), np.cumsum(daily, axis=0)])
    d = np.arange(n_t)
    fwd = cum[d + h + 1] - cum[d + 1]  # open(d+1) .. close(d+h)

    sessions = pd.bdate_range("2023-01-02", periods=n_t)
    tickers = np.array([f"T{i:04d}" for i in range(n)])
    universe = pd.DataFrame({
        "snapshot_date": np.repeat(sessions.to_numpy(), n),
        "ticker": np.tile(tickers, n_t),
        f"return_{h}d": fwd.ravel(),
    })
    order = np.argsort(state[:n_t], axis=1)
    k = sc.calls_per_side
    rows = []
    for direction, idx in (("up", order[:, -k:]), ("down", order[:, :k])):
        rows.append(pd.DataFrame({
            "session_date": np.repeat(sessions.to_numpy(), k),
            "ticker": tickers[idx.ravel()], "horizon_days": h,
            "direction": direction, "confidence": 0.6, "conviction": 3,
        }))
    forecasts = pd.concat(rows, ignore_index=True)
    return forecasts, universe


def session_table(forecasts: pd.DataFrame, universe: pd.DataFrame,
                  horizon: int) -> pd.DataFrame:
    """The per-session table of the criterion for a simulated world."""
    joined = fe.attach_outcomes(forecasts, universe)
    dec = joined[(joined["horizon_days"] == horizon) & joined["ret"].notna()
                 & (joined["sigma"] != 0)]
    return fe._per_date_table(dec)


def _stat_functions(table: pd.DataFrame) -> dict[str, Callable[[np.ndarray], float]]:
    ix = {c: i for i, c in enumerate(table.columns)}
    return {
        "relative": fe._diff_of_means(ix["up_uni"], ix["dn_uni"]),
        "selection": fe._mean_of(ix["sel_edge"]),
    }


# ── Interval methods (lower bounds) ──────────────────────────────────


def _index_moving(rng, n: int, b: int, n_boot: int) -> np.ndarray:
    n_blocks = -(-n // b)
    starts = rng.integers(0, n - b + 1, size=(n_boot, n_blocks))
    return (starts[:, :, None] + np.arange(b)).reshape(n_boot, -1)[:, :n]


def _index_circular(rng, n: int, b: int, n_boot: int) -> np.ndarray:
    n_blocks = -(-n // b)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    return ((starts[:, :, None] + np.arange(b)) % n).reshape(n_boot, -1)[:, :n]


def _index_stationary(rng, n: int, mean_b: int, n_boot: int) -> np.ndarray:
    """Politis-Romano stationary bootstrap: geometric block lengths."""
    new = rng.random((n_boot, n)) < 1.0 / mean_b
    new[:, 0] = True
    starts = rng.integers(0, n, size=(n_boot, n))
    pos = np.arange(n)[None, :]
    last = np.maximum.accumulate(np.where(new, pos, 0), axis=1)
    base = np.take_along_axis(starts, last, axis=1)
    return (base + (pos - last)) % n


_BOOTSTRAPS = {
    "bootstrap_moving_h": (_index_moving, 1),
    "bootstrap_circular_h": (_index_circular, 1),
    "bootstrap_circular_2h": (_index_circular, 2),
    "bootstrap_stationary_2h": (_index_stationary, 2),
}


def _newey_west_se(x: np.ndarray, lag: int) -> float:
    n = x.size
    e = x - x.mean()
    s = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - k / (lag + 1.0)) * float(e[k:] @ e[:-k]) / n
    return math.sqrt(max(s, 1e-30) / n)


def lower_bounds(
    method: str,
    table: pd.DataFrame,
    horizon: int,
    rng: np.random.Generator,
    n_boot: int = 1000,
) -> dict[str, float]:
    """Lower 95 % bound of both decisive statistics by one interval method."""
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    stats = _stat_functions(table)
    arr = table.to_numpy(dtype=float)
    n = len(arr)
    if method.startswith("jackknife"):
        factor = 1 if method == "jackknife_h" else None
        res = batch_jackknife(arr, stats, horizon, batch_factor=factor)
        return {k: v[1] for k, v in res.items()}
    if method.startswith("bootstrap"):
        make, mult = _BOOTSTRAPS[method]
        idx = make(rng, n, mult * horizon, n_boot)
        out = {}
        for name, fn in stats.items():
            draws = np.array([fn(arr[row]) for row in idx])
            out[name] = float(np.percentile(draws[~np.isnan(draws)], 2.5))
        return out
    # Newey-West on the influence series of each statistic, t critical value
    lag, per = (horizon - 1, horizon) if method == "newey_west_h" else (
        2 * horizon, 2 * horizon)
    crit = float(_sps.t.ppf(0.975, max(n / per - 1.0, 1.0)))
    out = {}
    for name, fn in stats.items():
        est = fn(arr)
        # influence series by leave-one-out (exact for the means used here)
        infl = np.array([n * est - (n - 1) * fn(np.delete(arr, i, axis=0))
                         for i in range(n)])
        out[name] = est - crit * _newey_west_se(infl, max(lag, 1))
    return out


# ── Experiment ───────────────────────────────────────────────────────


def pass_rates(
    scenario: Scenario,
    n_runs: int,
    seed: int = 0,
    methods: Sequence[str] = METHODS,
    n_boot: int = 1000,
) -> pd.DataFrame:
    """Share of simulated worlds in which each method passes the criterion.

    With ``skill = 0`` this is the false-pass rate (size): per decisive
    statistic (``relative``, ``selection``; nominal one-sided 2.5 %) and the
    ``joint`` rate of the criterion itself (both lower bounds above zero).
    With ``skill > 0`` it is the power. One row per method; ``n_runs`` worlds,
    every method sees the same worlds.
    """
    rng = np.random.default_rng(seed)
    counts = {m: {"relative": 0, "selection": 0, "joint": 0} for m in methods}
    est_sum = dict.fromkeys(STATS, 0.0)
    for _ in range(n_runs):
        forecasts, universe = simulate_world(scenario, rng)
        table = session_table(forecasts, universe, scenario.horizon)
        arr = table.to_numpy(dtype=float)
        for name, fn in _stat_functions(table).items():
            est_sum[name] += fn(arr)
        for m in methods:
            lo = lower_bounds(m, table, scenario.horizon, rng, n_boot)
            r, s = lo["relative"] > 0, lo["selection"] > 0
            counts[m]["relative"] += r
            counts[m]["selection"] += s
            counts[m]["joint"] += r and s
    rows = [{"method": m, **{k: v / n_runs for k, v in c.items()}}
            for m, c in counts.items()]
    return pd.DataFrame(rows).assign(
        n_runs=n_runs,
        mean_relative_estimate=est_sum["relative"] / n_runs,
        mean_selection_estimate=est_sum["selection"] / n_runs,
    )


def with_skill(scenario: Scenario, skill: float) -> Scenario:
    """The same world with a planted edge."""
    return replace(scenario, skill=skill)


__all__ = [
    "METHODS",
    "PRODUCTION_METHOD",
    "SCENARIOS",
    "Scenario",
    "lower_bounds",
    "pass_rates",
    "session_table",
    "simulate_world",
    "with_skill",
]
