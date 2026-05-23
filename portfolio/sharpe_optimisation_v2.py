"""Second-round Sharpe-ratio optimisation for Adam's Black-Litterman ISA.

Round 1 (``sharpe_optimisation.py`` / ``docs/sharpe_optimisation_report.md``)
established that adding *more equity-correlated* building blocks (momentum,
gold, infrastructure) cannot lift the long-only Sharpe above ~0.29 — the
opportunity set, not the position limits, is the binding constraint.

This round tests genuinely *different* levers, each designed to break the
equity-beta trap:

  Mod 1  Currency-hedge US equity (GSPX replaces VUSA) — pure vol reduction.
  Mod 2  Add a 12% trend-following / managed-futures sleeve (TREND).
  Mod 3  Sharpen the views (drop the two weak ones, tighten the strong ones).
  Mod 4  Recalibrate the risk-free rate 4.0% -> 2.5% (benchmark change only).
  Mod 5  Add a 2% Bitcoin sleeve (BTC), with E[r] sensitivity 8/12/16%.
  Mod 6  Equal-risk-contribution (ERC) risk parity — a parallel framework.
  Mod 7  Full stack: Mods 1+2+3+4 combined (the realistic ceiling).
  Mod 8  Full stack + 2% crypto.

The He-Litterman engine itself lives in ``bl_model.py`` and is imported, never
mutated. The Monte-Carlo engine is imported from ``projections/wealth_model.py``.

Run end-to-end with::

    python portfolio/sharpe_optimisation_v2.py

All result tables are written to ``portfolio/results/`` with a ``v2_`` prefix;
two charts (allocation + efficient frontier) are saved alongside. Every figure
quoted in ``docs/sharpe_optimisation_v2.md`` is produced by this script.

Data note
---------
No historical price series is bundled with the repository and the execution
environment has no market-data network access, so volatilities, correlations
and the alternative-asset return assumptions are taken from the brief (and, for
the unspecified correlation cells, from documented analyst estimates). Every
such assumption is flagged in ``CONFIG_V2`` and in the report's Methodology.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.optimize import minimize

# --- BL engine (imported, never mutated) -----------------------------------
sys.path.insert(0, os.path.dirname(__file__))
from bl_model import (  # noqa: E402
    CONFIG as BL_CONFIG,
    black_litterman_posterior,
    covariance_from_corr,
    efficient_frontier,
    implied_equilibrium_returns,
    mean_variance_constrained,
)

# --- Monte-Carlo engine ------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "projections"))
from wealth_model import (  # noqa: E402
    CONFIG as WM_CONFIG,
    WEALTH_TARGETS,
    build_cashflow_schedule,
    probability_of_reaching,
    simulate_paths,
    value_at_ages,
)


# ---------------------------------------------------------------------------
# CONFIG — everything v2 needs that is not already in BL_CONFIG
# ---------------------------------------------------------------------------

BASE_ASSETS: List[str] = list(BL_CONFIG["assets"])  # 11-asset base universe

CONFIG_V2: Dict = {
    "inflation": 0.025,
    "drawdown_z": 2.33,                 # 1-in-20-year normal quantile (~5%)
    "rf_base": BL_CONFIG["risk_free"],  # 4.0% — the rate the views were set at
    "rf_recal": 0.025,                  # 2.5% long-run real gilt proxy (Mod 4)
    "results_dir": BL_CONFIG["results_dir"],
    "delta": BL_CONFIG["risk_aversion"],
    "tau": BL_CONFIG["tau"],

    # Ongoing charge (TER) per ticker, including the v2 additions.
    "ter": {
        "VWRP": 0.0022, "VUSA": 0.0007, "VUKE": 0.0009, "VFEM": 0.0022,
        "WLDS": 0.0035, "IWVL": 0.0030, "IWQU": 0.0030, "INFR": 0.0040,
        "AGGG": 0.0010, "IGLS": 0.0007, "SGLN": 0.0012,
        "GSPX": 0.0010,   # iShares Core S&P 500 GBP Hedged
        "IWDG": 0.0030,   # iShares MSCI World GBP Hedged (TER drag flagged)
        "TREND": 0.0080,  # blended managed-futures UCITS (0.65-0.95% range)
        "BTC": 0.0095,    # WisdomTree / 21Shares physical bitcoin ETP
    },

    # Sleeve classification for the roll-up (now incl. trend + crypto).
    "sleeve": {
        "VWRP": "equity", "VUSA": "equity", "VUKE": "equity", "VFEM": "equity",
        "WLDS": "equity", "IWVL": "equity", "IWQU": "equity", "GSPX": "equity",
        "IWDG": "equity", "INFR": "real_asset", "AGGG": "bond", "IGLS": "bond",
        "SGLN": "gold", "TREND": "trend", "BTC": "crypto",
    },

    # ---- Mod 1: GBP-hedged US equity (GSPX) --------------------------------
    # Hedging strips ~8-10% annualised GBP/USD vol out of the US sleeve and the
    # FX noise out of its cross-correlations. Expected return is held at VUSA's
    # pre-hedge equilibrium value (CIP => hedging is return-neutral long-run).
    "gspx": {
        "volatility": 0.140,            # vs VUSA 0.165 unhedged
        "corr_reduction": 0.075,        # subtract from every VUSA cross-corr
    },
    # ---- Mod 1 (optional): GBP-hedged global equity (IWDG) -----------------
    "iwdg": {
        "volatility": 0.135,            # vs VWRP 0.150 unhedged
        "corr_reduction": 0.060,
    },

    # ---- Mod 2: trend-following / managed futures (TREND) ------------------
    "trend": {
        "weight": 0.12,                 # forced sleeve size (funded from bonds)
        "excess_return": 0.020,         # 6.0% nominal - 4.0% rf  (~0.4 Sharpe)
        "volatility": 0.10,
        # Correlation vs the 11 base assets, in BASE_ASSETS order. Brief pins
        # VWRP=0.05 and AGGG=0.10; the rest are analyst estimates (trend is the
        # canonical "crisis-alpha" diversifier: ~0 vs equities/real assets,
        # mildly positive vs bonds/gold via its rates & commodity legs).
        #          VWRP VUSA VUKE VFEM WLDS IWVL IWQU INFR AGGG IGLS SGLN
        "corr":   [0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.10,0.10,0.10],
        "view_q": 0.025,                # TREND > AGGG by 2.5% p.a.
        "view_omega": 0.0015,
    },

    # ---- Mod 5: Bitcoin sleeve (BTC) ---------------------------------------
    "btc": {
        "weight": 0.02,                 # forced sleeve size
        "excess_return_central": 0.080, # 12% nominal - 4% rf (central case)
        "scenarios_nominal": [0.08, 0.12, 0.16],
        "volatility": 0.60,
        # Brief pins VWRP=0.25, AGGG=-0.05, SGLN=0.15; rest analyst estimates
        # (rising equity beta with institutional adoption, ~0 vs duration).
        #          VWRP VUSA VUKE VFEM WLDS IWVL IWQU INFR  AGGG  IGLS SGLN
        "corr":   [0.25,0.22,0.18,0.25,0.25,0.18,0.22,0.15,-0.05,-0.05,0.15],
        "corr_vs_trend": 0.10,
    },

    # ---- Mod 3 / 7 / 8: view recalibration ---------------------------------
    # Keep views 1 (EM, idx0), 2 (Value, idx1), 4 (Infra, idx3); drop views
    # 3 (UK, idx2) and 5 (Quality, idx4). Tighten the kept views' Omega.
    "recal_keep_idx": [0, 1, 3],
    "recal_omega": 0.0010,

    # ---- Sensitivity grids -------------------------------------------------
    "trend_sharpe_grid": [0.2, 0.4, 0.6],   # implied excess = sharpe * 10% vol
    "rf_grid": [0.020, 0.025, 0.030, 0.040],
}


# ---------------------------------------------------------------------------
# Variant specification + runner
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    """A fully-specified BL optimisation problem."""

    name: str
    assets: List[str]
    pi: np.ndarray                          # prior excess returns (rf-relative)
    sigma: np.ndarray                       # covariance
    P: np.ndarray
    Q: np.ndarray
    omega_diag: np.ndarray
    forced: Dict[int, float] = field(default_factory=dict)  # exact weights


def _base_pi_sigma() -> Tuple[np.ndarray, np.ndarray]:
    """Base 11-asset equilibrium prior and covariance."""
    sigma = covariance_from_corr(BL_CONFIG["volatilities"], BL_CONFIG["correlations"])
    pi = implied_equilibrium_returns(CONFIG_V2["delta"], sigma,
                                     BL_CONFIG["market_cap_weights"])
    return pi, sigma


def _base_views() -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The five existing views (P, Q, Omega-diag) as float copies."""
    P = BL_CONFIG["views"]["P"].astype(float).copy()
    Q = BL_CONFIG["views"]["Q"].astype(float).copy()
    om = BL_CONFIG["views"]["omega_diag"].astype(float).copy()
    return P, Q, om


def _recalibrated_views() -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mod 3: keep views 1/2/4, drop 3/5, tighten kept Omega to 0.0010."""
    P, Q, _ = _base_views()
    keep = CONFIG_V2["recal_keep_idx"]
    P_r = P[keep, :]
    Q_r = Q[keep]
    om_r = np.full(len(keep), CONFIG_V2["recal_omega"])
    return P_r, Q_r, om_r


def _hedge_corr(base_corr: np.ndarray, idx: int, reduction: float) -> np.ndarray:
    """Return a copy of ``base_corr`` with row/col ``idx`` cross-corrs reduced.

    Subtracting a constant from every cross-correlation models the removal of
    FX noise from a hedged sleeve. The diagonal stays 1.0 and values are
    clipped to a sane band.
    """
    c = base_corr.copy()
    n = c.shape[0]
    for j in range(n):
        if j == idx:
            continue
        v = np.clip(c[idx, j] - reduction, -0.99, 0.99)
        c[idx, j] = v
        c[j, idx] = v
    return c


def _append_asset(corr: np.ndarray, new_corr_row: np.ndarray) -> np.ndarray:
    """Append one asset (row/col) to a correlation matrix; self-corr = 1."""
    n = corr.shape[0]
    out = np.zeros((n + 1, n + 1))
    out[:n, :n] = corr
    out[n, :n] = new_corr_row
    out[:n, n] = new_corr_row
    out[n, n] = 1.0
    return out


# -- Forced-weight mean-variance optimiser ----------------------------------

def mv_constrained_forced(
    delta: float, mu: np.ndarray, sigma: np.ndarray,
    forced: Optional[Dict[int, float]] = None,
    max_weight: float = 0.40, min_weight_if_held: float = 0.02,
) -> np.ndarray:
    """Long-only MV-utility optimum with optional *exact* forced weights.

    When ``forced`` is empty this reduces to ``bl_model.mean_variance_constrained``
    (verified to reproduce the base portfolio). When some assets are pinned to
    an exact weight (e.g. TREND=12%, BTC=2%), only the *free* block is optimised
    and renormalised to ``1 - sum(forced)``; the min-2%-if-held rule is applied
    to the free block alone so the forced sleeves stay exact.
    """
    forced = forced or {}
    if not forced:
        return mean_variance_constrained(
            delta, mu, sigma, max_weight=max_weight,
            min_weight_if_held=min_weight_if_held, long_only=True,
        )

    n = len(mu)
    forced_sum = sum(forced.values())
    free_idx = [i for i in range(n) if i not in forced]
    budget = 1.0 - forced_sum

    def assemble(w_free: np.ndarray) -> np.ndarray:
        w = np.zeros(n)
        for k, i in enumerate(free_idx):
            w[i] = w_free[k]
        for i, v in forced.items():
            w[i] = v
        return w

    def neg_utility(w_free: np.ndarray) -> float:
        w = assemble(w_free)
        return -(w @ mu - 0.5 * delta * w @ sigma @ w)

    def solve(active: List[int]) -> np.ndarray:
        m = len(active)
        bounds = [(0.0, max_weight) for _ in range(m)]
        cons = ({"type": "eq", "fun": lambda wf: np.sum(wf) - budget},)
        w0 = np.full(m, budget / m)

        def neg_u(wf: np.ndarray) -> float:
            w = np.zeros(n)
            for k, i in enumerate(active):
                w[i] = wf[k]
            for i, v in forced.items():
                w[i] = v
            return -(w @ mu - 0.5 * delta * w @ sigma @ w)

        res = minimize(neg_u, w0, method="SLSQP", bounds=bounds,
                       constraints=cons, options={"maxiter": 800, "ftol": 1e-11})
        w = np.zeros(n)
        for k, i in enumerate(active):
            w[i] = res.x[k]
        return w

    w = solve(free_idx)
    # Drop sub-threshold free lines and re-solve once.
    keep = [i for i in free_idx if w[i] >= min_weight_if_held]
    if len(keep) < len(free_idx) and keep:
        w = solve(keep)
    for i, v in forced.items():
        w[i] = v
    return w


def run_variant(v: Variant, use_forced: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (posterior_mu_excess, posterior_sigma, weights) for a variant."""
    omega = np.diag(v.omega_diag)
    mu_post, sig_post = black_litterman_posterior(
        v.pi, v.sigma, CONFIG_V2["tau"], v.P, v.Q, omega,
    )
    forced = v.forced if use_forced else {}
    w = mv_constrained_forced(CONFIG_V2["delta"], mu_post, sig_post, forced=forced)
    return mu_post, sig_post, w


# ---------------------------------------------------------------------------
# Variant builders
# ---------------------------------------------------------------------------

def build_base() -> Variant:
    pi, sigma = _base_pi_sigma()
    P, Q, om = _base_views()
    return Variant("Base (current)", BASE_ASSETS, pi, sigma, P, Q, om)


def build_mod1_gspx() -> Variant:
    """GSPX (GBP-hedged S&P 500) replaces VUSA in the US slot."""
    pi, sigma_base = _base_pi_sigma()
    assets = BASE_ASSETS.copy()
    us = assets.index("VUSA")
    assets[us] = "GSPX"

    vols = BL_CONFIG["volatilities"].astype(float).copy()
    vols[us] = CONFIG_V2["gspx"]["volatility"]
    corr = _hedge_corr(BL_CONFIG["correlations"].astype(float),
                       us, CONFIG_V2["gspx"]["corr_reduction"])
    sigma = covariance_from_corr(vols, corr)

    # Returns held at base equilibrium (pi unchanged) -> pure vol/corr effect.
    P, Q, om = _base_views()
    return Variant("Mod 1: GSPX hedge US", assets, pi, sigma, P, Q, om)


def build_mod1b_iwdg() -> Variant:
    """Optional sub-experiment: also hedge the global sleeve (VWRP -> IWDG)."""
    pi, _ = _base_pi_sigma()
    assets = BASE_ASSETS.copy()
    us, gl = assets.index("VUSA"), assets.index("VWRP")
    assets[us] = "GSPX"
    assets[gl] = "IWDG"

    vols = BL_CONFIG["volatilities"].astype(float).copy()
    vols[us] = CONFIG_V2["gspx"]["volatility"]
    vols[gl] = CONFIG_V2["iwdg"]["volatility"]
    corr = _hedge_corr(BL_CONFIG["correlations"].astype(float),
                       us, CONFIG_V2["gspx"]["corr_reduction"])
    corr = _hedge_corr(corr, gl, CONFIG_V2["iwdg"]["corr_reduction"])
    sigma = covariance_from_corr(vols, corr)

    P, Q, om = _base_views()
    return Variant("Mod 1b: GSPX+IWDG hedge", assets, pi, sigma, P, Q, om)


def _add_trend(assets: List[str], pi: np.ndarray, corr: np.ndarray,
               vols: np.ndarray) -> Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]:
    """Append the TREND sleeve to (assets, pi, corr, vols). Returns extended."""
    tr = CONFIG_V2["trend"]
    assets = assets + ["TREND"]
    pi = np.append(pi, tr["excess_return"])
    vols = np.append(vols, tr["volatility"])
    corr = _append_asset(corr, np.array(tr["corr"], dtype=float))
    return assets, pi, corr, vols


def _trend_view(assets: List[str], P: np.ndarray, Q: np.ndarray,
                om: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Append the 'TREND > AGGG by 2.5%' view, widening P by one column."""
    tr = CONFIG_V2["trend"]
    P = np.hstack([P, np.zeros((P.shape[0], 1))])
    row = np.zeros(len(assets))
    row[assets.index("TREND")] = 1.0
    row[assets.index("AGGG")] = -1.0
    P = np.vstack([P, row])
    Q = np.append(Q, tr["view_q"])
    om = np.append(om, tr["view_omega"])
    return P, Q, om


def build_mod2_trend() -> Variant:
    """Add a 12% managed-futures sleeve to the base universe + a trend view."""
    pi, _ = _base_pi_sigma()
    vols = BL_CONFIG["volatilities"].astype(float).copy()
    corr = BL_CONFIG["correlations"].astype(float).copy()
    assets, pi, corr, vols = _add_trend(BASE_ASSETS.copy(), pi, corr, vols)
    sigma = covariance_from_corr(vols, corr)

    P, Q, om = _base_views()
    P, Q, om = _trend_view(assets, P, Q, om)
    forced = {assets.index("TREND"): CONFIG_V2["trend"]["weight"]}
    return Variant("Mod 2: +Trend 12%", assets, pi, sigma, P, Q, om, forced)


def build_mod3_views() -> Variant:
    """Recalibrate views: drop UK & Quality, tighten EM/Value/Infra Omega."""
    pi, sigma = _base_pi_sigma()
    P, Q, om = _recalibrated_views()
    return Variant("Mod 3: Sharpen views", BASE_ASSETS, pi, sigma, P, Q, om)


def _add_btc(assets: List[str], pi: np.ndarray, corr: np.ndarray,
             vols: np.ndarray, excess: float) -> Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]:
    """Append the BTC sleeve. ``excess`` lets us run the 8/12/16% sensitivity."""
    bt = CONFIG_V2["btc"]
    has_trend = "TREND" in assets
    crow = list(bt["corr"])
    if has_trend:
        crow = crow + [bt["corr_vs_trend"]]
    assets = assets + ["BTC"]
    pi = np.append(pi, excess)
    vols = np.append(vols, bt["volatility"])
    corr = _append_asset(corr, np.array(crow, dtype=float))
    return assets, pi, corr, vols


def build_mod5_crypto(excess: Optional[float] = None) -> Variant:
    """Add a 2% Bitcoin sleeve to the base universe (no crypto view)."""
    if excess is None:
        excess = CONFIG_V2["btc"]["excess_return_central"]
    pi, _ = _base_pi_sigma()
    vols = BL_CONFIG["volatilities"].astype(float).copy()
    corr = BL_CONFIG["correlations"].astype(float).copy()
    assets, pi, corr, vols = _add_btc(BASE_ASSETS.copy(), pi, corr, vols, excess)
    sigma = covariance_from_corr(vols, corr)

    P, Q, om = _base_views()
    P = np.hstack([P, np.zeros((P.shape[0], 1))])  # zero column for BTC
    forced = {assets.index("BTC"): CONFIG_V2["btc"]["weight"]}
    return Variant("Mod 5: +Crypto 2%", assets, pi, sigma, P, Q, om, forced)


def build_full_stack(with_crypto: bool = False,
                     btc_excess: Optional[float] = None) -> Variant:
    """Mod 7 (=1+2+3+4) or Mod 8 (=7+crypto).

    GSPX replaces VUSA, a 12% TREND sleeve is added, views are recalibrated,
    and rf=2.5% is applied at the *reporting* stage (weights unchanged).
    """
    pi, _ = _base_pi_sigma()
    assets = BASE_ASSETS.copy()
    us = assets.index("VUSA")
    assets[us] = "GSPX"

    vols = BL_CONFIG["volatilities"].astype(float).copy()
    vols[us] = CONFIG_V2["gspx"]["volatility"]
    corr = _hedge_corr(BL_CONFIG["correlations"].astype(float),
                       us, CONFIG_V2["gspx"]["corr_reduction"])

    # Add trend sleeve.
    assets, pi, corr, vols = _add_trend(assets, pi, corr, vols)

    # Recalibrated views + trend view.
    P, Q, om = _recalibrated_views()
    P, Q, om = _trend_view(assets, P, Q, om)

    forced = {assets.index("TREND"): CONFIG_V2["trend"]["weight"]}
    name = "Mod 7: Full stack"

    if with_crypto:
        if btc_excess is None:
            btc_excess = CONFIG_V2["btc"]["excess_return_central"]
        assets, pi, corr, vols = _add_btc(assets, pi, corr, vols, btc_excess)
        P = np.hstack([P, np.zeros((P.shape[0], 1))])  # zero col for BTC
        forced[assets.index("BTC")] = CONFIG_V2["btc"]["weight"]
        name = "Mod 8: Full stack +Crypto"

    sigma = covariance_from_corr(vols, corr)
    return Variant(name, assets, pi, sigma, P, Q, om, forced)


# ---------------------------------------------------------------------------
# Risk parity (ERC) — Modification 6
# ---------------------------------------------------------------------------

def risk_contributions(w: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Absolute risk contributions RC_i = w_i (Sigma w)_i (sum = variance)."""
    return w * (sigma @ w)


def erc_weights(sigma: np.ndarray) -> np.ndarray:
    """Solve for long-only, unleveraged equal-risk-contribution weights.

    Minimises the dispersion of risk contributions subject to sum(w)=1, w>=0.
    Seeded at inverse-volatility weights, which is the analytic ERC solution
    when all correlations are equal.
    """
    n = sigma.shape[0]
    vols = np.sqrt(np.diag(sigma))
    w0 = (1.0 / vols) / np.sum(1.0 / vols)

    def objective(w: np.ndarray) -> float:
        rc = risk_contributions(w, sigma)
        return np.sum((rc - rc.mean()) ** 2)

    cons = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
    bounds = [(0.0, 1.0)] * n
    res = minimize(objective, w0, method="SLSQP", bounds=bounds,
                   constraints=cons, options={"maxiter": 2000, "ftol": 1e-14})
    w = np.clip(res.x, 0.0, None)
    return w / w.sum()


# ---------------------------------------------------------------------------
# Statistics for one portfolio
# ---------------------------------------------------------------------------

ALL_TICKERS: List[str] = BASE_ASSETS + ["GSPX", "IWDG", "TREND", "BTC"]
SLEEVES = ["equity", "bond", "real_asset", "gold", "trend", "crypto"]


def effective_bets(w: np.ndarray, sigma: np.ndarray) -> float:
    """Effective number of independent bets = 1 / sum(RC_share^2).

    Herfindahl concentration measured on *risk contributions*, not weights.
    """
    rc = risk_contributions(w, sigma)
    total = rc.sum()
    if total <= 0:
        return np.nan
    share = rc / total
    return 1.0 / np.sum(share ** 2)


def variant_stats(name: str, assets: List[str], w: np.ndarray,
                  mu_excess: np.ndarray, sigma: np.ndarray) -> Dict:
    """Full statistics row for one portfolio variant.

    ``mu_excess`` is the BL posterior excess return over the 4.0% baseline rf
    at which the views were calibrated, so nominal E[r] = w.mu + 4.0% for every
    variant. Sharpe is then reported at *both* rf=4.0% and rf=2.5% by varying
    only the subtracted rate (the Mod-4 'benchmark change' interpretation).
    """
    excess = float(w @ mu_excess)
    nominal = excess + CONFIG_V2["rf_base"]
    real = (1 + nominal) / (1 + CONFIG_V2["inflation"]) - 1
    vol = float(np.sqrt(w @ sigma @ w))

    sharpe_4 = (nominal - CONFIG_V2["rf_base"]) / vol if vol else np.nan
    sharpe_25 = (nominal - CONFIG_V2["rf_recal"]) / vol if vol else np.nan

    wmap = dict(zip(assets, w))
    ter = sum(wmap.get(t, 0.0) * CONFIG_V2["ter"][t] for t in assets)

    sleeves = {s: 0.0 for s in SLEEVES}
    for t, wt in wmap.items():
        sleeves[CONFIG_V2["sleeve"][t]] += wt

    row = {
        "variant": name,
        "E[r] nominal": nominal,
        "E[r] real": real,
        "volatility": vol,
        "sharpe_rf4.0": sharpe_4,
        "sharpe_rf2.5": sharpe_25,
        "max_drawdown_est": -CONFIG_V2["drawdown_z"] * vol,
        "blended_TER": ter,
        "eff_num_bets": effective_bets(w, sigma),
    }
    for s in SLEEVES:
        row[f"{s}_pct"] = sleeves[s]
    for t in ALL_TICKERS:
        row[f"w_{t}"] = wmap.get(t, 0.0)
    return row


def benchmark_stats(name: str, weights: Dict[str, float]) -> Dict:
    """Stats for a fixed-weight benchmark, evaluated on the base posterior."""
    pi, sigma = _base_pi_sigma()
    P, Q, om = _base_views()
    mu_post, sig_post = black_litterman_posterior(
        pi, sigma, CONFIG_V2["tau"], P, Q, np.diag(om))
    w = np.array([weights.get(a, 0.0) for a in BASE_ASSETS])
    return variant_stats(name, BASE_ASSETS, w, mu_post, sig_post)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all() -> Dict:
    """Build & solve every variant; return a dict of artefacts for the report."""
    art: Dict = {}
    rows: List[Dict] = []
    posteriors: Dict[str, Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]] = {}

    def record(v: Variant) -> Dict:
        mu, sig, w = run_variant(v)
        posteriors[v.name] = (v.assets, mu, sig, w)
        row = variant_stats(v.name, v.assets, w, mu, sig)
        rows.append(row)
        return row

    base = build_base()
    base_row = record(base)

    record(build_mod1_gspx())
    record(build_mod2_trend())
    record(build_mod3_views())

    # Mod 4 — rf recalibration: identical weights/returns to base, only the
    # reported Sharpe@2.5% differs. We surface it as its own row by copying the
    # base row and renaming (its sharpe_rf2.5 column already carries the uplift).
    mod4 = dict(base_row)
    mod4["variant"] = "Mod 4: rf 4.0%->2.5%"
    rows.append(mod4)

    record(build_mod5_crypto())

    # Mod 6 — ERC risk parity on (base 11 + TREND), evaluated on Mod-2 posterior.
    a2, mu2, sig2, _ = posteriors["Mod 2: +Trend 12%"]
    w_erc = erc_weights(sig2)
    erc_row = variant_stats("Mod 6: Risk parity (ERC)", a2, w_erc, mu2, sig2)
    rows.append(erc_row)
    posteriors["Mod 6: Risk parity (ERC)"] = (a2, mu2, sig2, w_erc)

    record(build_full_stack(with_crypto=False))   # Mod 7
    record(build_full_stack(with_crypto=True))     # Mod 8

    # Benchmarks (on base posterior, matching bl_model.py).
    rows.append(benchmark_stats("Benchmark: 60/40", {"VWRP": 0.60, "AGGG": 0.40}))
    rows.append(benchmark_stats("Benchmark: 100% VWRP", {"VWRP": 1.0}))

    df = pd.DataFrame(rows)
    art["df"] = df
    art["posteriors"] = posteriors
    art["base_row"] = base_row
    return art


# ---------------------------------------------------------------------------
# Sensitivities
# ---------------------------------------------------------------------------

def trend_sharpe_sensitivity() -> pd.DataFrame:
    """Re-solve Mod 2 with the trend sleeve's implied Sharpe = 0.2 / 0.4 / 0.6."""
    rows = []
    for s in CONFIG_V2["trend_sharpe_grid"]:
        excess = s * CONFIG_V2["trend"]["volatility"]
        # Rebuild Mod 2 with a different trend prior + view aligned to it.
        pi, _ = _base_pi_sigma()
        vols = BL_CONFIG["volatilities"].astype(float).copy()
        corr = BL_CONFIG["correlations"].astype(float).copy()
        assets, pi, corr, vols = _add_trend(BASE_ASSETS.copy(), pi, corr, vols)
        pi[assets.index("TREND")] = excess
        sigma = covariance_from_corr(vols, corr)
        P, Q, om = _base_views()
        P, Q, om = _trend_view(assets, P, Q, om)
        # View Q tracks the assumed premium (trend beats bonds by ~excess+0.5%).
        Q[-1] = excess + 0.005
        forced = {assets.index("TREND"): CONFIG_V2["trend"]["weight"]}
        v = Variant(f"Trend Sharpe={s}", assets, pi, sigma, P, Q, om, forced)
        mu, sig, w = run_variant(v)
        rows.append(variant_stats(v.name, assets, w, mu, sig))
    return pd.DataFrame(rows)


def crypto_excess_sensitivity() -> pd.DataFrame:
    """Re-solve Mod 8 (full stack + crypto) with BTC nominal E[r] = 8/12/16%."""
    rows = []
    for nominal in CONFIG_V2["btc"]["scenarios_nominal"]:
        excess = nominal - CONFIG_V2["rf_base"]
        v = build_full_stack(with_crypto=True, btc_excess=excess)
        mu, sig, w = run_variant(v)
        rows.append(variant_stats(f"Full stack, BTC E[r]={nominal:.0%}",
                                  v.assets, w, mu, sig))
    return pd.DataFrame(rows)


def rf_sensitivity(full_stack_row: Dict) -> pd.DataFrame:
    """Sharpe of base and full-stack across the rf grid (weights fixed)."""
    rows = []
    for rf in CONFIG_V2["rf_grid"]:
        rows.append({
            "rf": rf,
            "base_sharpe": (full_stack_row["_base_nominal"] - rf) / full_stack_row["_base_vol"],
            "fullstack_sharpe": (full_stack_row["E[r] nominal"] - rf) / full_stack_row["volatility"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

def monte_carlo(label_to_stats: Dict[str, Dict]) -> Dict:
    """Run the wealth-model MC for each (label -> stats row)."""
    cf = build_cashflow_schedule()["contribution_gbp"]
    out = {}
    for label, st in label_to_stats.items():
        wealth = simulate_paths(cf, st["E[r] nominal"], st["volatility"],
                                WM_CONFIG["n_paths"], WM_CONFIG["seed"],
                                WM_CONFIG["inflation"])
        out[label] = {
            "ages": value_at_ages(wealth, cf.index),
            "targets": probability_of_reaching(wealth, WEALTH_TARGETS),
        }
    return out


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 160,
                         "axes.titleweight": "bold", "font.family": "DejaVu Sans"})


def plot_allocation(assets: List[str], w: np.ndarray, title: str, path: str) -> None:
    _style()
    order = np.argsort(w)[::-1]
    a = [assets[i] for i in order]
    wv = w[order]
    mask = wv > 1e-4
    a = [x for x, m in zip(a, mask) if m]
    wv = wv[mask]
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = sns.color_palette("crest", len(wv))
    ax.bar(a, wv, color=colors)
    for i, val in enumerate(wv):
        ax.text(i, val + 0.005, f"{val:.0%}", ha="center", fontsize=10)
    ax.set_ylabel("Portfolio weight")
    ax.set_title(title)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
    ax.text(0.01, -0.18, "Source: sharpe_optimisation_v2.py — long-only, max 40%/line, "
            "TREND forced 12%.", transform=ax.transAxes, fontsize=9, color="gray")
    plt.xticks(rotation=0)
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_frontiers(base_post: Tuple, full_post: Tuple, path: str) -> None:
    """Efficient frontiers: base posterior vs full-stack posterior, with points."""
    _style()
    fig, ax = plt.subplots(figsize=(11, 7))
    for (assets, mu, sig, w), color, lbl in (
        (base_post, "#888888", "Base posterior"),
        (full_post, "#4C72B0", "Full-stack posterior (Mod 7)"),
    ):
        fr = efficient_frontier(mu, sig, n_points=40)
        ax.plot(fr["volatility"], fr["target_return"], lw=2.4, color=color, label=lbl)
        ax.scatter([np.sqrt(w @ sig @ w)], [w @ mu], color=color, s=110,
                   edgecolor="black", zorder=5)
    ax.set_xlabel("Volatility (σ, annualised)")
    ax.set_ylabel("Excess return over rf")
    ax.set_title("Efficient frontier — base vs full-stack (with trend sleeve)")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1%}"))
    ax.legend(loc="lower right", frameon=True)
    ax.text(0.01, -0.13, "Source: sharpe_optimisation_v2.py. Points = recommended "
            "portfolios.", transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _fmt_pct(x: float) -> str:
    return f"{x:,.2%}"


def main() -> None:
    os.makedirs(CONFIG_V2["results_dir"], exist_ok=True)
    rdir = CONFIG_V2["results_dir"]
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 60)

    art = run_all()
    df = art["df"]
    posteriors = art["posteriors"]
    base_row = art["base_row"]

    # ---- Summary table ----------------------------------------------------
    summary_cols = [
        "variant", "E[r] nominal", "E[r] real", "volatility",
        "sharpe_rf4.0", "sharpe_rf2.5", "max_drawdown_est", "blended_TER",
        "equity_pct", "bond_pct", "real_asset_pct", "gold_pct", "trend_pct",
        "crypto_pct", "eff_num_bets",
    ]
    summary = df[summary_cols].copy()
    summary.to_csv(os.path.join(rdir, "v2_summary.csv"), index=False)

    weight_cols = ["variant"] + [f"w_{t}" for t in ALL_TICKERS]
    df[weight_cols].to_csv(os.path.join(rdir, "v2_weights.csv"), index=False)

    # ---- Identify the single highest-Sharpe modification (genuine, rf=4%) --
    single_mods = df[df["variant"].str.startswith("Mod")
                     & ~df["variant"].str.contains("Full stack")
                     & ~df["variant"].str.contains("rf 4.0")]
    best_single = single_mods.loc[single_mods["sharpe_rf4.0"].idxmax()]

    full7 = df[df["variant"] == "Mod 7: Full stack"].iloc[0]
    full8 = df[df["variant"] == "Mod 8: Full stack +Crypto"].iloc[0]

    # ---- Sensitivities ----------------------------------------------------
    trend_sens = trend_sharpe_sensitivity()
    trend_sens[summary_cols].to_csv(os.path.join(rdir, "v2_trend_sensitivity.csv"),
                                    index=False)
    crypto_sens = crypto_excess_sensitivity()
    crypto_sens[summary_cols].to_csv(os.path.join(rdir, "v2_crypto_sensitivity.csv"),
                                     index=False)

    full7_for_rf = dict(full7)
    full7_for_rf["_base_nominal"] = base_row["E[r] nominal"]
    full7_for_rf["_base_vol"] = base_row["volatility"]
    rf_sens = rf_sensitivity(full7_for_rf)
    rf_sens.to_csv(os.path.join(rdir, "v2_rf_sensitivity.csv"), index=False)

    # ---- Monte Carlo: base, best single mod, full stack -------------------
    mc_inputs = {
        "Base": base_row,
        f"Best single ({best_single['variant']})": best_single.to_dict(),
        "Full stack (Mod 7)": full7.to_dict(),
    }
    mc = monte_carlo(mc_inputs)
    for label, res in mc.items():
        safe = (label.replace(" ", "_").replace("(", "").replace(")", "")
                .replace(":", "").replace("+", "").replace(".", "")
                .replace("%", "").replace("->", "to"))
        res["ages"].to_csv(os.path.join(rdir, f"v2_mc_{safe}_by_age.csv"), index=False)
        res["targets"].to_csv(os.path.join(rdir, f"v2_mc_{safe}_targets.csv"), index=False)

    # ---- Charts -----------------------------------------------------------
    plot_allocation(*posteriors["Mod 7: Full stack"][:1],
                    posteriors["Mod 7: Full stack"][3],
                    "Full-stack portfolio (Mod 7) — recommended allocation",
                    os.path.join(rdir, "v2_full_stack_allocation.png"))
    plot_frontiers(posteriors["Base (current)"], posteriors["Mod 7: Full stack"],
                   os.path.join(rdir, "v2_efficient_frontier.png"))

    # ---- Console report ---------------------------------------------------
    print("=" * 110)
    print("SHARPE OPTIMISATION v2 — variant comparison")
    print("=" * 110)
    show = summary.copy()
    for c in ["E[r] nominal", "E[r] real", "volatility", "max_drawdown_est",
              "blended_TER", "equity_pct", "bond_pct", "real_asset_pct",
              "gold_pct", "trend_pct", "crypto_pct"]:
        show[c] = show[c].map(_fmt_pct)
    show["sharpe_rf4.0"] = summary["sharpe_rf4.0"].map(lambda x: f"{x:.4f}")
    show["sharpe_rf2.5"] = summary["sharpe_rf2.5"].map(lambda x: f"{x:.4f}")
    show["eff_num_bets"] = summary["eff_num_bets"].map(lambda x: f"{x:.2f}")
    print(show.to_string(index=False))

    base_s4 = base_row["sharpe_rf4.0"]
    print(f"\nBase Sharpe@4% = {base_s4:.4f}.  Uplift vs base (rf=4%):")
    for _, r in df[df["variant"].str.startswith("Mod")].iterrows():
        d = r["sharpe_rf4.0"] - base_s4
        print(f"  {r['variant']:<28} {r['sharpe_rf4.0']:.4f}  "
              f"({d*100:+.2f} pts, {(r['sharpe_rf4.0']/base_s4-1)*100:+.1f}%)")

    print(f"\nHighest-Sharpe single modification (rf=4%): {best_single['variant']} "
          f"-> {best_single['sharpe_rf4.0']:.4f}")
    print(f"Full stack (Mod 7): Sharpe@4%={full7['sharpe_rf4.0']:.4f}, "
          f"Sharpe@2.5%={full7['sharpe_rf2.5']:.4f}, "
          f"E[r]={full7['E[r] nominal']:.2%}, σ={full7['volatility']:.2%}")
    print(f"Full stack +Crypto (Mod 8): Sharpe@4%={full8['sharpe_rf4.0']:.4f}, "
          f"Sharpe@2.5%={full8['sharpe_rf2.5']:.4f}")

    print("\nMod 1b (also hedge global, IWDG) sub-experiment:")
    v1b = build_mod1b_iwdg()
    mu1b, sig1b, w1b = run_variant(v1b)
    r1b = variant_stats(v1b.name, v1b.assets, w1b, mu1b, sig1b)
    print(f"  {r1b['variant']}: E[r]={r1b['E[r] nominal']:.2%}, "
          f"σ={r1b['volatility']:.2%}, Sharpe@4%={r1b['sharpe_rf4.0']:.4f}, "
          f"TER={r1b['blended_TER']:.2%}")

    print("\nTrend-Sharpe sensitivity (Mod 2):")
    print(trend_sens[["variant", "E[r] nominal", "volatility",
                      "sharpe_rf4.0"]].to_string(index=False))
    print("\nCrypto E[r] sensitivity (Mod 8):")
    print(crypto_sens[["variant", "E[r] nominal", "volatility",
                       "sharpe_rf4.0"]].to_string(index=False))
    print("\nrf sensitivity (base vs full stack):")
    print(rf_sens.to_string(index=False))

    # Risk-parity weight detail (Mod 6).
    a6, _, sig6, w6 = posteriors["Mod 6: Risk parity (ERC)"]
    rc6 = risk_contributions(w6, sig6)
    print("\nMod 6 ERC weights & risk-contribution shares:")
    for t, wt, rc in zip(a6, w6, rc6 / rc6.sum()):
        print(f"  {t:<6} w={wt:6.2%}  RC share={rc:6.2%}")

    print("\nMonte Carlo (real GBP, by age):")
    for label, res in mc.items():
        print(f"\n[{label}]")
        print(res["ages"].to_string(index=False, float_format=lambda x: f"£{x:,.0f}"))
        print("  Targets (ever / at end):", {
            int(t): (round(e, 3), round(a, 3)) for t, e, a in zip(
                res["targets"]["target_gbp_real"],
                res["targets"]["prob_ever_reach"],
                res["targets"]["prob_at_end"])})

    print("\nFull weight matrix (recommended portfolios):")
    wshow = df[weight_cols].copy()
    for t in ALL_TICKERS:
        wshow[f"w_{t}"] = wshow[f"w_{t}"].map(lambda x: f"{x:.3f}")
    print(wshow.to_string(index=False))


if __name__ == "__main__":
    main()
