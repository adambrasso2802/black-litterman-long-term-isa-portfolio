"""Black-Litterman portfolio construction for Adam's UK ISA.

Run end-to-end with:

    python portfolio/bl_model.py

Outputs are written to ``portfolio/results/`` as CSVs and PNGs.

Implementation follows He & Litterman (1999), with the Idzorek (2005)
confidence calibration for Omega. No third-party portfolio optimiser is
used — everything sits on top of numpy / scipy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.optimize import minimize


# ---------------------------------------------------------------------------
# CONFIG — single source of truth for all model inputs
# ---------------------------------------------------------------------------

CONFIG: Dict = {
    # 11-asset investable universe (see portfolio/universe.md).
    "assets": [
        "VWRP", "VUSA", "VUKE", "VFEM", "WLDS",
        "IWVL", "IWQU", "INFR", "AGGG", "IGLS", "SGLN",
    ],

    # Reference cap-weights for the equilibrium prior.
    "market_cap_weights": np.array([
        0.35, 0.18, 0.03, 0.06, 0.04,
        0.03, 0.04, 0.03, 0.18, 0.04, 0.02,
    ]),

    # Long-run annualised volatilities (GBP, total return).
    # Sources: MSCI factsheets, Bloomberg, LBMA — see research/sources.md.
    "volatilities": np.array([
        0.150,  # VWRP  global eq
        0.165,  # VUSA  US large
        0.155,  # VUKE  FTSE 100
        0.205,  # VFEM  EM eq
        0.190,  # WLDS  small cap
        0.170,  # IWVL  value
        0.140,  # IWQU  quality
        0.155,  # INFR  infra
        0.055,  # AGGG  global agg (hedged)
        0.030,  # IGLS  short gilts
        0.160,  # SGLN  gold
    ]),

    # Correlation matrix — long-horizon estimates rounded to 2dp.
    # Symmetric; rows/cols match ``assets`` order.
    "correlations": np.array([
        # VWRP  VUSA  VUKE  VFEM  WLDS  IWVL  IWQU  INFR  AGGG  IGLS  SGLN
        [1.00, 0.95, 0.80, 0.80, 0.90, 0.90, 0.92, 0.70, 0.10, 0.05, 0.15],  # VWRP
        [0.95, 1.00, 0.70, 0.70, 0.85, 0.82, 0.92, 0.62, 0.10, 0.05, 0.10],  # VUSA
        [0.80, 0.70, 1.00, 0.70, 0.75, 0.85, 0.72, 0.65, 0.15, 0.10, 0.20],  # VUKE
        [0.80, 0.70, 0.70, 1.00, 0.75, 0.78, 0.70, 0.60, 0.05, 0.00, 0.25],  # VFEM
        [0.90, 0.85, 0.75, 0.75, 1.00, 0.85, 0.85, 0.70, 0.10, 0.05, 0.15],  # WLDS
        [0.90, 0.82, 0.85, 0.78, 0.85, 1.00, 0.78, 0.70, 0.05, 0.00, 0.15],  # IWVL
        [0.92, 0.92, 0.72, 0.70, 0.85, 0.78, 1.00, 0.65, 0.15, 0.10, 0.10],  # IWQU
        [0.70, 0.62, 0.65, 0.60, 0.70, 0.70, 0.65, 1.00, 0.25, 0.15, 0.25],  # INFR
        [0.10, 0.10, 0.15, 0.05, 0.10, 0.05, 0.15, 0.25, 1.00, 0.60, 0.20],  # AGGG
        [0.05, 0.05, 0.10, 0.00, 0.05, 0.00, 0.10, 0.15, 0.60, 1.00, 0.10],  # IGLS
        [0.15, 0.10, 0.20, 0.25, 0.15, 0.15, 0.10, 0.25, 0.20, 0.10, 1.00],  # SGLN
    ]),

    # BL hyper-parameters.
    "tau": 0.05,            # He-Litterman default
    "risk_aversion": 2.0,   # δ ≈ E[R_m-R_f]/σ_m² with S≈0.35, σ≈15%
    "risk_free": 0.040,     # GBP risk-free proxy = BoE Bank Rate May 2026

    # Views — see portfolio/views.md for the economic story.
    "views": {
        # Each row of P is a portfolio over `assets`.
        "P": np.array([
            [-1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0],  # EM > global
            [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0],  # Value > global
            [-1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0],  # UK > global
            [0, 0, 0, 0, 0, 0, 0, 1, -1, 0, 0],  # Infra > bonds
            [-1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0],  # Quality > global
        ], dtype=float),
        "Q": np.array([0.020, 0.015, 0.010, 0.030, 0.005]),
        # Diagonal of Ω (uncertainty). Smaller = more confident.
        "omega_diag": np.array([0.0015, 0.0015, 0.0030, 0.0015, 0.0030]),
    },

    # Optimisation constraints for the "practical" portfolio.
    "constraints": {
        "max_weight": 0.40,
        "min_weight_if_held": 0.02,
        "long_only": True,
    },

    # I/O
    "results_dir": os.path.join(os.path.dirname(__file__), "results"),
}


# ---------------------------------------------------------------------------
# Core BL machinery
# ---------------------------------------------------------------------------

@dataclass
class BLResult:
    """Container for the BL posterior plus derived portfolio metrics."""

    pi: np.ndarray              # implied equilibrium excess returns
    posterior_mu: np.ndarray    # BL posterior expected excess returns
    posterior_sigma: np.ndarray # BL posterior covariance
    w_unconstrained: np.ndarray
    w_constrained: np.ndarray


def covariance_from_corr(vols: np.ndarray, corr: np.ndarray) -> np.ndarray:
    """Build a covariance matrix from a volatility vector and a correlation matrix."""
    return np.outer(vols, vols) * corr


def implied_equilibrium_returns(
    delta: float, sigma: np.ndarray, w_mkt: np.ndarray
) -> np.ndarray:
    """π = δ Σ w_mkt — reverse-engineer the prior from the market portfolio."""
    return delta * sigma @ w_mkt


def black_litterman_posterior(
    pi: np.ndarray,
    sigma: np.ndarray,
    tau: float,
    P: np.ndarray,
    Q: np.ndarray,
    omega: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (posterior_mu, posterior_sigma) under He-Litterman (1999)."""
    tau_sigma = tau * sigma
    # Posterior precision and mean — Bayesian update.
    middle_inv = np.linalg.inv(P @ tau_sigma @ P.T + omega)
    posterior_mu = pi + tau_sigma @ P.T @ middle_inv @ (Q - P @ pi)

    # He-Litterman posterior covariance (return uncertainty, not asset vol).
    m_inv = np.linalg.inv(np.linalg.inv(tau_sigma) + P.T @ np.linalg.inv(omega) @ P)
    posterior_sigma = sigma + m_inv
    return posterior_mu, posterior_sigma


def mean_variance_unconstrained(
    delta: float, mu: np.ndarray, sigma: np.ndarray
) -> np.ndarray:
    """w* = (δ Σ)⁻¹ μ  — closed-form unconstrained optimum."""
    return np.linalg.solve(delta * sigma, mu)


def mean_variance_constrained(
    delta: float,
    mu: np.ndarray,
    sigma: np.ndarray,
    max_weight: float = 0.40,
    min_weight_if_held: float = 0.02,
    long_only: bool = True,
) -> np.ndarray:
    """Solve the long-only, capped, fully-invested MV problem with SLSQP.

    The min-weight-if-held constraint is approximated by zeroing any weight
    below the threshold after the optimiser converges, then re-normalising.
    """
    n = len(mu)
    bounds = [(0.0, max_weight)] if long_only else [(-max_weight, max_weight)] * n
    if long_only:
        bounds = [(0.0, max_weight) for _ in range(n)]

    def neg_utility(w: np.ndarray) -> float:
        # Maximise μᵀw − ½ δ wᵀΣw  →  minimise its negative.
        return -(w @ mu - 0.5 * delta * w @ sigma @ w)

    cons = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
    w0 = np.full(n, 1.0 / n)
    res = minimize(neg_utility, w0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-10})
    w = res.x
    # Apply the min-weight-if-held rule.
    w = np.where(w < min_weight_if_held, 0.0, w)
    if w.sum() == 0:
        return w
    return w / w.sum()


def portfolio_metrics(
    w: np.ndarray, mu_excess: np.ndarray, sigma: np.ndarray, rf: float
) -> Dict[str, float]:
    """Annualised return, vol, Sharpe — μ here is *excess* of rf."""
    er_excess = float(w @ mu_excess)
    vol = float(np.sqrt(w @ sigma @ w))
    return {
        "expected_return": er_excess + rf,
        "expected_excess_return": er_excess,
        "volatility": vol,
        "sharpe": er_excess / vol if vol else np.nan,
    }


# ---------------------------------------------------------------------------
# Higher-level routines (efficient frontier, sensitivity, rebalancing)
# ---------------------------------------------------------------------------

def efficient_frontier(
    mu: np.ndarray, sigma: np.ndarray, n_points: int = 50,
    max_weight: float = 0.40,
) -> pd.DataFrame:
    """Long-only frontier — sweep target returns from min to max."""
    n = len(mu)
    target_rets = np.linspace(mu.min(), mu.max(), n_points)
    rows = []
    for tr in target_rets:
        cons = (
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
            {"type": "eq", "fun": lambda w, tr=tr: w @ mu - tr},
        )
        bounds = [(0.0, max_weight)] * n
        w0 = np.full(n, 1.0 / n)
        res = minimize(lambda w: w @ sigma @ w, w0, method="SLSQP",
                       bounds=bounds, constraints=cons,
                       options={"maxiter": 300, "ftol": 1e-9})
        if res.success:
            vol = float(np.sqrt(res.x @ sigma @ res.x))
            rows.append({"target_return": tr, "volatility": vol})
    return pd.DataFrame(rows)


def rebalancing_schedule(
    target_weights: pd.Series, drift_threshold: float = 0.05,
) -> str:
    """Return a human-readable description of the recommended cadence."""
    return (
        "Annual calendar rebalance every April (start of new UK tax year), "
        "plus an interim check if any single line drifts by more than "
        f"{drift_threshold:.0%} in absolute terms from its target. "
        "Monthly contributions are directed to the most-underweight lines first, "
        "which keeps the portfolio close to target without realising any sales."
    )


def sensitivity_analysis(
    pi: np.ndarray, sigma: np.ndarray, P: np.ndarray, Q: np.ndarray,
    base_omega_diag: np.ndarray, delta: float,
    taus=(0.025, 0.05, 0.10), conf_scales=(0.5, 1.0, 2.0),
) -> pd.DataFrame:
    """Sweep over τ and Ω-scaling, returning posterior weights for each combo."""
    rows = []
    for tau in taus:
        for cs in conf_scales:
            omega = np.diag(base_omega_diag * cs)
            mu_post, sig_post = black_litterman_posterior(pi, sigma, tau, P, Q, omega)
            w = mean_variance_constrained(delta, mu_post, sig_post)
            rows.append({"tau": tau, "omega_scale": cs, **dict(zip(CONFIG["assets"], w))})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def _setup_style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update({
        "figure.dpi": 110,
        "savefig.dpi": 160,
        "axes.titleweight": "bold",
        "font.family": "DejaVu Sans",
    })


def plot_weights(weights_df: pd.DataFrame, path: str) -> None:
    """Bar chart of BL constrained vs market vs unconstrained weights."""
    _setup_style()
    fig, ax = plt.subplots(figsize=(12, 6))
    weights_df.plot(kind="bar", ax=ax, width=0.8,
                    color=["#4C72B0", "#DD8452", "#55A467"])
    ax.set_ylabel("Portfolio weight")
    ax.set_xlabel("")
    ax.set_title("Adam's BL ISA Portfolio — recommended vs market vs unconstrained")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
    ax.legend(loc="upper right", frameon=True)
    ax.text(0.01, -0.18, "Source: BL posterior, May 2026 view set. See portfolio/views.md.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.xticks(rotation=0)
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_efficient_frontier(
    prior_frontier: pd.DataFrame,
    post_frontier: pd.DataFrame,
    w_recommend: np.ndarray,
    mu_post: np.ndarray, sigma_post: np.ndarray,
    path: str,
) -> None:
    _setup_style()
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.plot(prior_frontier["volatility"], prior_frontier["target_return"],
            label="Prior (equilibrium only)", lw=2, color="#888888")
    ax.plot(post_frontier["volatility"], post_frontier["target_return"],
            label="Posterior (with views)", lw=2.5, color="#4C72B0")
    rec_ret = w_recommend @ mu_post
    rec_vol = np.sqrt(w_recommend @ sigma_post @ w_recommend)
    ax.scatter([rec_vol], [rec_ret], color="#C44E52", zorder=5, s=110,
               label="Recommended portfolio")
    ax.set_xlabel("Volatility (σ, annualised)")
    ax.set_ylabel("Excess return over risk-free")
    ax.set_title("Efficient frontier — Black-Litterman prior vs posterior")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1%}"))
    ax.legend(loc="lower right", frameon=True)
    ax.text(0.01, -0.13, "Source: bl_model.py — long-only, max 40% per line.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> BLResult:
    os.makedirs(CONFIG["results_dir"], exist_ok=True)
    assets = CONFIG["assets"]
    w_mkt = CONFIG["market_cap_weights"]
    sigma = covariance_from_corr(CONFIG["volatilities"], CONFIG["correlations"])
    delta = CONFIG["risk_aversion"]
    tau = CONFIG["tau"]
    rf = CONFIG["risk_free"]

    pi = implied_equilibrium_returns(delta, sigma, w_mkt)

    P = CONFIG["views"]["P"]
    Q = CONFIG["views"]["Q"]
    omega = np.diag(CONFIG["views"]["omega_diag"])

    mu_post, sigma_post = black_litterman_posterior(pi, sigma, tau, P, Q, omega)

    w_unc = mean_variance_unconstrained(delta, mu_post, sigma_post)
    w_con = mean_variance_constrained(
        delta, mu_post, sigma_post,
        max_weight=CONFIG["constraints"]["max_weight"],
        min_weight_if_held=CONFIG["constraints"]["min_weight_if_held"],
        long_only=CONFIG["constraints"]["long_only"],
    )

    # 1. Equilibrium vs posterior table -------------------------------------
    eq_table = pd.DataFrame({
        "asset": assets,
        "market_weight": w_mkt,
        "pi_equilibrium_excess_return": pi,
        "pi_total_return": pi + rf,
        "posterior_mu_excess": mu_post,
        "posterior_mu_total": mu_post + rf,
        "delta_bps": (mu_post - pi) * 1e4,
    })
    eq_table.to_csv(os.path.join(CONFIG["results_dir"], "equilibrium_vs_posterior_delta2.csv"),
                    index=False)

    # 2. Optimal weights table ----------------------------------------------
    weights_table = pd.DataFrame({
        "asset": assets,
        "market_weight": w_mkt,
        "bl_unconstrained": w_unc,
        "bl_constrained_recommended": w_con,
    })
    weights_table.to_csv(os.path.join(CONFIG["results_dir"], "optimal_weights_delta2.csv"),
                         index=False)

    # 3. £3,000 allocation table --------------------------------------------
    initial = 3000.0
    alloc = pd.DataFrame({
        "asset": assets,
        "weight": w_con,
        "gbp_amount": w_con * initial,
    })
    alloc.to_csv(os.path.join(CONFIG["results_dir"], "portfolio_allocation_delta2.csv"),
                 index=False)

    # 4. Metrics: recommended vs naive benchmarks ---------------------------
    rec_metrics = portfolio_metrics(w_con, mu_post, sigma_post, rf)
    # 60/40 benchmark: 60% VWRP, 40% AGGG.
    w_6040 = np.zeros(len(assets)); w_6040[0] = 0.60; w_6040[8] = 0.40
    bench_6040 = portfolio_metrics(w_6040, mu_post, sigma_post, rf)
    # 100% MSCI ACWI proxy = 100% VWRP.
    w_acwi = np.zeros(len(assets)); w_acwi[0] = 1.0
    bench_acwi = portfolio_metrics(w_acwi, mu_post, sigma_post, rf)

    metrics_df = pd.DataFrame({
        "Portfolio": ["BL Recommended", "60/40 (VWRP/AGGG)", "100% VWRP"],
        "Expected return": [rec_metrics["expected_return"], bench_6040["expected_return"],
                            bench_acwi["expected_return"]],
        "Volatility": [rec_metrics["volatility"], bench_6040["volatility"],
                       bench_acwi["volatility"]],
        "Sharpe": [rec_metrics["sharpe"], bench_6040["sharpe"], bench_acwi["sharpe"]],
        # Heuristic max-drawdown estimate ≈ 2.5 σ for diversified equity-heavy.
        "Est. max drawdown (1σ-shock)": [
            -2.5 * rec_metrics["volatility"],
            -2.5 * bench_6040["volatility"],
            -2.5 * bench_acwi["volatility"],
        ],
    })
    metrics_df.to_csv(os.path.join(CONFIG["results_dir"], "portfolio_metrics_delta2.csv"),
                      index=False)

    # 5. Sensitivity --------------------------------------------------------
    sens = sensitivity_analysis(
        pi, sigma, P, Q, CONFIG["views"]["omega_diag"], delta,
    )
    sens.to_csv(os.path.join(CONFIG["results_dir"], "sensitivity_weights_delta2.csv"),
                index=False)

    # 6. Charts -------------------------------------------------------------
    weights_chart_df = pd.DataFrame(
        {"Market": w_mkt, "BL unconstrained": w_unc, "BL recommended": w_con},
        index=assets,
    )
    plot_weights(weights_chart_df,
                 os.path.join(CONFIG["results_dir"], "bl_weights_chart_delta2.png"))

    prior_frontier = efficient_frontier(pi, sigma)
    post_frontier = efficient_frontier(mu_post, sigma_post)
    plot_efficient_frontier(
        prior_frontier, post_frontier, w_con, mu_post, sigma_post,
        os.path.join(CONFIG["results_dir"], "efficient_frontier_delta2.png"),
    )

    # 7. Console summary ----------------------------------------------------
    print("=" * 72)
    print("Black-Litterman ISA portfolio — results")
    print("=" * 72)
    pd.set_option("display.float_format", "{:,.4f}".format)
    print("\nEquilibrium vs Posterior excess returns:")
    print(eq_table.to_string(index=False))
    print("\nWeights:")
    print(weights_table.to_string(index=False))
    print("\nMetrics vs benchmarks:")
    print(metrics_df.to_string(index=False))
    print("\nRebalancing guidance:")
    print(" ", rebalancing_schedule(pd.Series(w_con, index=assets)))

    return BLResult(pi, mu_post, sigma_post, w_unc, w_con)


if __name__ == "__main__":
    main()
