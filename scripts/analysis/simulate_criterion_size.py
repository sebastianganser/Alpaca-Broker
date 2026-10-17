"""Size (and power) of the decisive forecast criterion by simulation.

Plan 2026-10-17 review M1. Simulates worlds with overlapping h-day returns and
a NO-SKILL forecaster that keeps calling the same names on consecutive days
(``trading_signals.analysis.criterion_sim``), runs the decisive criterion of
``docs/FORECASTS.md`` section 6 - lower bounds of BOTH the relative up-minus-
down excess and the selection hit edge above zero - with several interval
methods and counts how often it "passes". Nominal one-sided size per statistic
is 2.5 %; the joint rate is the false-pass rate of the criterion itself.

Pure CPU, no database. About 0.15 s per simulated world and method set at 300
tickers x 200 sessions; the default of 1,000 runs per scenario takes a few
minutes with ``--workers``.

Usage:
    uv run python scripts/analysis/simulate_criterion_size.py --runs 1000 \
        --out reports
    uv run python scripts/analysis/simulate_criterion_size.py --runs 50 \
        --scenarios "base (rho 0.95)" --no-power
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

from trading_signals.analysis.criterion_sim import (
    METHODS,
    PRODUCTION_METHOD,
    SCENARIOS,
    Scenario,
    pass_rates,
    with_skill,
)
from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.simulate_criterion_size")

#: Planted edges for the power table (daily drift per unit of the call state);
#: the report prints the resulting mean relative excess per horizon.
DEFAULT_SKILLS = (0.0002, 0.0003, 0.0005)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=int, default=1000, help="Worlds per scenario")
    p.add_argument("--n-boot", type=int, default=1000, help="Bootstrap draws")
    p.add_argument("--seed", type=int, default=20261017)
    p.add_argument("--scenarios", nargs="*", default=None,
                   help=f"Subset of: {', '.join(SCENARIOS)}")
    p.add_argument("--workers", type=int, default=4, help="Parallel processes")
    p.add_argument("--skills", type=float, nargs="*", default=list(DEFAULT_SKILLS))
    p.add_argument("--no-power", action="store_true", help="Skip the power table")
    p.add_argument("--out", default="reports", help="Output directory")
    return p.parse_args(argv)


def _run(job: tuple[str, Scenario, int, int, int, tuple[str, ...]]) -> pd.DataFrame:
    name, scenario, runs, seed, n_boot, methods = job
    res = pass_rates(scenario, runs, seed=seed, methods=methods, n_boot=n_boot)
    return res.assign(scenario=name)


def _map(jobs: list, workers: int) -> list[pd.DataFrame]:
    if workers <= 1 or len(jobs) == 1:
        return [_run(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run, jobs))


def _rate(x: float) -> str:
    return f"{x * 100:.1f} %"


def render(size: pd.DataFrame, power: pd.DataFrame | None, runs: int) -> str:
    lines = [
        "# Simulated size of the decisive forecast criterion",
        "",
        f"Created {date.today():%Y-%m-%d} · {runs} simulated worlds per scenario · "
        "no-skill forecaster with persistent calls on overlapping h-day returns "
        "(`analysis/criterion_sim.py`).",
        "",
        "Each cell is the share of worlds in which the lower bound of the 95 % "
        "interval is above zero. Nominal one-sided size per statistic: 2.5 %. "
        "**joint** = both decisive statistics (relative up-minus-down excess AND "
        "selection hit edge) pass = the false-pass rate of the criterion. "
        f"Production method: `{PRODUCTION_METHOD}`.",
        "",
    ]
    se = (0.025 * 0.975 / runs) ** 0.5
    lines += [f"Monte-Carlo standard error of a rate near 2.5 %: {se * 100:.2f} "
              "percentage points.", ""]
    for scenario, grp in size.groupby("scenario", sort=False):
        lines += [f"## {scenario}", "",
                  "| Method | relative | selection hit edge | **joint** |",
                  "|---|---|---|---|"]
        present = set(grp["method"])
        by = grp.set_index("method").loc[[m for m in METHODS if m in present]]
        for m, r in by.iterrows():
            lines.append(f"| {m} | {_rate(r['relative'])} | {_rate(r['selection'])} "
                         f"| **{_rate(r['joint'])}** |")
        lines.append("")
    worst = (size[size["method"] == PRODUCTION_METHOD]
             .sort_values("joint", ascending=False).iloc[0])
    lines += [f"Largest joint rate of `{PRODUCTION_METHOD}`: "
              f"{_rate(worst['joint'])} ({worst['scenario']}).", ""]
    if power is not None and len(power):
        lines += ["## Power of the production method (planted edge)", "",
                  "| Scenario | skill | mean relative excess | mean selection edge "
                  "| joint pass rate |", "|---|---|---|---|---|"]
        for _, r in power.iterrows():
            lines.append(
                f"| {r['scenario']} | {r['skill']:g} | "
                f"{r['mean_relative_estimate'] * 100:+.2f} % | "
                f"{r['mean_selection_estimate'] * 100:+.2f} pp | "
                f"{_rate(r['joint'])} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    names = args.scenarios or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        logger.error(f"unknown scenarios {unknown}; choose from {list(SCENARIOS)}")
        return 2
    jobs = [(n, SCENARIOS[n], args.runs, args.seed, args.n_boot, METHODS)
            for n in names]
    size = pd.concat(_map(jobs, args.workers), ignore_index=True)
    power = None
    if not args.no_power:
        base = names[0]
        pjobs = [(base, with_skill(SCENARIOS[base], s), args.runs, args.seed,
                  args.n_boot, (PRODUCTION_METHOD,)) for s in args.skills]
        parts = [r.assign(skill=s) for r, s in zip(
            _map(pjobs, args.workers), args.skills, strict=True)]
        power = pd.concat(parts, ignore_index=True)
    report = render(size, power, args.runs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"criterion_size_{date.today():%Y-%m-%d}.md"
    out_file.write_text(report, encoding="utf-8")
    print(report)
    logger.info(f"Report written to {out_file.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
