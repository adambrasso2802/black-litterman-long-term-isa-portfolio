"""Monte Carlo wealth projection for Adam's BL ISA portfolio.

Run end-to-end with:

    python projections/wealth_model.py

Reads the recommended portfolio's expected return and volatility from the
BL model (re-running it to ensure consistency), then simulates 10,000 paths
of monthly returns over a 45-year horizon, layering in the planned
contributions.
"""

from __future__ import annotations

import os
import sys
from typing import Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


# Re-use the BL model so the assumed return and volatility are consistent.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "portfolio"))
from bl_model import main as run_bl, CONFIG as BL_CONFIG, portfolio_metrics  # noqa: E402


CONFIG: Dict = {
    "initial_lump_sum": 3_000.0,             # invested t=0 (May 2026)
    "start_date": pd.Timestamp("2026-05-01"),
    "horizon_years": 45,

    # Contribution phases.
    "phase1_start": pd.Timestamp("2027-09-01"),
    "phase1_end":   pd.Timestamp("2030-08-01"),
    "phase1_monthly": 500.0,

    "phase2_start": pd.Timestamp("2030-09-01"),
    "phase2_end":   pd.Timestamp("2070-08-01"),
    "phase2_monthly": 1_600.0,

    "inflation": 0.025,
    "isa_annual_limit": 20_000.0,

    "n_paths": 10_000,
    "seed": 42,
    "results_dir": os.path.join(os.path.dirname(__file__), "results"),

    # Investor's date-of-birth proxy — for the "value at age X" outputs.
    # Adam is 25 today (May 2026); adjust if not.
    "birth_year": 2001,
}

WEALTH_TARGETS = [1_000_000, 2_000_000, 5_000_000, 10_000_000]


# ---------------------------------------------------------------------------
# Cash-flow schedule
# ---------------------------------------------------------------------------

def build_cashflow_schedule() -> pd.DataFrame:
    """Return a month-end-indexed dataframe with the planned £-contribution."""
    months = pd.date_range(CONFIG["start_date"],
                           CONFIG["start_date"] + pd.DateOffset(years=CONFIG["horizon_years"]),
                           freq="MS")
    cf = pd.Series(0.0, index=months)
    cf.iloc[0] = CONFIG["initial_lump_sum"]
    phase1_mask = (months >= CONFIG["phase1_start"]) & (months <= CONFIG["phase1_end"])
    phase2_mask = (months >= CONFIG["phase2_start"]) & (months <= CONFIG["phase2_end"])
    cf[phase1_mask] += CONFIG["phase1_monthly"]
    cf[phase2_mask] += CONFIG["phase2_monthly"]

    # ISA limit sanity check — phase 2 = 19,200/year (safely under 20,000).
    annual = cf.groupby(cf.index.year).sum()
    over = annual[annual > CONFIG["isa_annual_limit"]]
    if not over.empty:
        print(f"WARNING: annual contribution exceeds ISA limit in years: "
              f"{over.to_dict()}")
    return pd.DataFrame({"contribution_gbp": cf})


# ---------------------------------------------------------------------------
# Monte Carlo engine
# ---------------------------------------------------------------------------

def simulate_paths(
    cashflows: pd.Series, mu_annual: float, sigma_annual: float,
    n_paths: int, seed: int, inflation: float,
) -> np.ndarray:
    """Simulate monthly returns using a lognormal model on *real* returns.

    We deflate the nominal expected return by the inflation assumption, so
    every figure in the output is in May-2026 GBP purchasing power.
    """
    n_months = len(cashflows)
    mu_real_annual = (1 + mu_annual) / (1 + inflation) - 1
    mu_m = (1 + mu_real_annual) ** (1 / 12) - 1
    sig_m = sigma_annual / np.sqrt(12)

    rng = np.random.default_rng(seed)
    # Lognormal returns: log(1+r) ~ N(log(1+mu_m) - 0.5σ², σ)
    log_mu = np.log(1 + mu_m) - 0.5 * sig_m ** 2
    log_rets = rng.normal(loc=log_mu, scale=sig_m, size=(n_paths, n_months))
    monthly_returns = np.exp(log_rets) - 1.0

    wealth = np.zeros((n_paths, n_months))
    cf = cashflows.values
    # Apply contribution at the start of the month, then return over the month.
    state = np.zeros(n_paths)
    for t in range(n_months):
        state = (state + cf[t]) * (1.0 + monthly_returns[:, t])
        wealth[:, t] = state
    return wealth


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def percentile_table(wealth: np.ndarray, dates: pd.DatetimeIndex) -> pd.DataFrame:
    pct = [10, 25, 50, 75, 90]
    arr = np.percentile(wealth, pct, axis=0)
    df = pd.DataFrame(arr.T, index=dates, columns=[f"p{p}" for p in pct])
    df.index.name = "month"
    return df


def probability_of_reaching(wealth: np.ndarray, targets) -> pd.DataFrame:
    rows = []
    for t in targets:
        ever_hit = (wealth >= t).any(axis=1).mean()
        end_hit = (wealth[:, -1] >= t).mean()
        rows.append({"target_gbp_real": t,
                     "prob_ever_reach": ever_hit,
                     "prob_at_end": end_hit})
    return pd.DataFrame(rows)


def value_at_ages(wealth: np.ndarray, dates: pd.DatetimeIndex,
                  ages=(30, 40, 50, 60, 65, 70)) -> pd.DataFrame:
    rows = []
    for age in ages:
        target_year = CONFIG["birth_year"] + age
        idx = np.argmin(np.abs(dates.year - target_year))
        col = wealth[:, idx]
        rows.append({
            "age": age,
            "year": dates[idx].year,
            "p10": np.percentile(col, 10),
            "p50": np.percentile(col, 50),
            "p90": np.percentile(col, 90),
        })
    return pd.DataFrame(rows)


def sensitivity_table(cashflows: pd.Series, mu: float, sigma: float,
                      n_paths: int, seed: int, inflation: float,
                      deltas=(-0.01, 0.0, 0.01)) -> pd.DataFrame:
    rows = []
    for d in deltas:
        wealth = simulate_paths(cashflows, mu + d, sigma, n_paths // 2, seed, inflation)
        final = wealth[:, -1]
        rows.append({
            "return_shift": d,
            "median_final": np.median(final),
            "p10_final": np.percentile(final, 10),
            "p90_final": np.percentile(final, 90),
            "prob_real_1m": (final >= 1_000_000).mean(),
            "prob_real_5m": (final >= 5_000_000).mean(),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _setup_style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 160,
                          "axes.titleweight": "bold",
                          "font.family": "DejaVu Sans"})


def plot_fan_chart(wealth: np.ndarray, dates: pd.DatetimeIndex,
                   cashflows: pd.Series, path: str) -> None:
    _setup_style()
    fig, ax = plt.subplots(figsize=(13, 7.5))

    p10, p25, p50, p75, p90 = (np.percentile(wealth, q, axis=0)
                               for q in (10, 25, 50, 75, 90))
    ax.fill_between(dates, p10, p90, alpha=0.20, color="#4C72B0",
                    label="10–90th percentile")
    ax.fill_between(dates, p25, p75, alpha=0.35, color="#4C72B0",
                    label="25–75th percentile")
    ax.plot(dates, p50, color="#1f3a64", lw=2.5, label="Median")

    cum_contrib = cashflows.cumsum()
    ax.plot(dates, cum_contrib, color="#C44E52", lw=2, ls="--",
            label="Cumulative contributions")

    ax.set_yscale("log")
    ax.set_title("Adam's BL ISA — projected wealth (real GBP, 10,000 paths)")
    ax.set_xlabel("Year")
    ax.set_ylabel("Portfolio value (GBP, May-2026 real)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"£{x:,.0f}" if x < 1e6 else f"£{x/1e6:.1f}m"))
    ax.legend(loc="upper left", frameon=True)
    ax.text(0.01, -0.13,
            "Source: wealth_model.py. Real returns deflated at 2.5% p.a. "
            "Lognormal monthly model; BL posterior μ/σ.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(CONFIG["results_dir"], exist_ok=True)

    # 1. Pull the BL posterior metrics so this stays in lockstep.
    bl = run_bl()
    sigma_post = bl.posterior_sigma
    mu_post = bl.posterior_mu
    w = bl.w_constrained
    metrics = portfolio_metrics(w, mu_post, sigma_post, BL_CONFIG["risk_free"])
    mu_total = metrics["expected_return"]
    vol = metrics["volatility"]
    print(f"\nBL portfolio assumed: μ = {mu_total:.2%}, σ = {vol:.2%} (nominal)")

    # 2. Cash flows.
    cf = build_cashflow_schedule()
    cashflows = cf["contribution_gbp"]

    # 3. Simulate.
    wealth = simulate_paths(cashflows, mu_total, vol,
                            CONFIG["n_paths"], CONFIG["seed"], CONFIG["inflation"])

    # 4. Save outputs.
    pct_df = percentile_table(wealth, cashflows.index)
    pct_df.to_csv(os.path.join(CONFIG["results_dir"], "wealth_paths.csv"))

    prob_df = probability_of_reaching(wealth, WEALTH_TARGETS)
    prob_df.to_csv(os.path.join(CONFIG["results_dir"], "probability_targets.csv"),
                   index=False)

    age_df = value_at_ages(wealth, cashflows.index)
    age_df.to_csv(os.path.join(CONFIG["results_dir"], "value_by_age.csv"), index=False)

    sens_df = sensitivity_table(cashflows, mu_total, vol,
                                CONFIG["n_paths"], CONFIG["seed"],
                                CONFIG["inflation"])
    sens_df.to_csv(os.path.join(CONFIG["results_dir"], "scenario_table.csv"),
                   index=False)

    plot_fan_chart(wealth, cashflows.index, cashflows,
                   os.path.join(CONFIG["results_dir"], "wealth_fan_chart.png"))

    # 5. Console summary.
    print("\nProbability of reaching real-GBP wealth targets (over 45y):")
    print(prob_df.to_string(index=False, float_format=lambda x: f"{x:,.2%}"))
    print("\nProjected portfolio value by age (real GBP):")
    print(age_df.to_string(index=False, float_format=lambda x: f"£{x:,.0f}"))
    print("\nSensitivity to ±1% shifts in expected return:")
    print(sens_df.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))


if __name__ == "__main__":
    main()
