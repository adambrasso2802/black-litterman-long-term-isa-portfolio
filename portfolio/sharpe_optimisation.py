"""Sharpe-ratio optimisation experiments on Adam's Black-Litterman ISA portfolio.

This module tests six modifications to the base BL portfolio (defined in
``bl_model.py``) and asks, for each, what allocation maximises the Sharpe ratio
subject to the stated constraints. It is a *companion* to ``bl_model.py`` — it
imports the BL machinery from there and never mutates it.

Run end-to-end with:

    python portfolio/sharpe_optimisation.py

All result tables land in ``portfolio/results/`` as CSVs. The accompanying
write-up lives in ``docs/sharpe_optimisation_report.md`` and quotes the numbers
this script prints.

Design choices (documented in the report's Methodology section):

* The *base* portfolio is the existing recommendation from ``bl_model.py`` — the
  δ = 2.5 mean-variance-utility optimum. Its headline stats (7.42% / 11.80% /
  0.29) are taken as the status quo to improve upon.
* Each *modification* is optimised by **maximising the Sharpe ratio directly**
  via ``scipy.optimize`` (SLSQP) — the same optimiser family ``bl_model.py``
  uses — subject to long-only, sum-to-one, and the per-asset weight bounds the
  modification specifies. This is the natural lens for a report whose remit is
  "maximise Sharpe".
* All other BL inputs (τ, δ, rf, the prior covariance, and the five investor
  views) are held identical to ``bl_model.py`` unless a modification explicitly
  changes them.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize

# Import the BL engine without re-running it. The file sits next to this one.
sys.path.insert(0, os.path.dirname(__file__))
from bl_model import (  # noqa: E402
    CONFIG as BL_CONFIG,
    black_litterman_posterior,
    covariance_from_corr,
    implied_equilibrium_returns,
    portfolio_metrics,
)

# Re-use the Monte Carlo engine for the deep-dive (it imports cleanly).
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
# CONFIG — everything the experiments need that is *not* already in BL_CONFIG
# ---------------------------------------------------------------------------

CONFIG: Dict = {
    "inflation": 0.025,                 # for nominal -> real conversion
    "drawdown_z": 2.33,                 # 1-in-20-year normal quantile (~5%)
    "results_dir": BL_CONFIG["results_dir"],

    # Per-ETF ongoing charge (TER). Keyed by ticker. IWMO added for the
    # momentum experiments.
    "ter": {
        "VWRP": 0.0022, "VUSA": 0.0007, "VUKE": 0.0009, "VFEM": 0.0022,
        "WLDS": 0.0035, "IWVL": 0.0030, "IWQU": 0.0030, "INFR": 0.0040,
        "AGGG": 0.0010, "IGLS": 0.0007, "SGLN": 0.0012, "IWMO": 0.0030,
    },

    # Sleeve classification for the equity / bond / real-asset / gold roll-up.
    "sleeve": {
        "VWRP": "equity", "VUSA": "equity", "VUKE": "equity", "VFEM": "equity",
        "WLDS": "equity", "IWVL": "equity", "IWQU": "equity", "IWMO": "equity",
        "INFR": "real_asset", "AGGG": "bond", "IGLS": "bond", "SGLN": "gold",
    },

    # ---- Modification 1: iShares MSCI World Momentum (IWMO) -----------------
    "iwmo": {
        "ticker": "IWMO",
        "market_cap_weight_raw": 0.04,  # ~4% of the global equity universe
        "volatility": 0.155,            # annualised, given
        # Correlation of IWMO with each *base* asset, in BL_CONFIG["assets"]
        # order. Three are specified in the brief (VWRP 0.92, IWVL 0.55,
        # AGGG -0.05); the remainder are analyst estimates (a momentum sleeve
        # is a high-beta equity factor, near-zero vs bonds/gold). Flagged in
        # the report Methodology.
        #          VWRP  VUSA  VUKE  VFEM  WLDS  IWVL  IWQU  INFR  AGGG  IGLS  SGLN
        "corr":  [0.92, 0.88, 0.65, 0.62, 0.78, 0.55, 0.82, 0.55, -0.05, 0.00, 0.10],
        # New view: IWMO beats global equities (VWRP) by +1.5% p.a.
        "view_q": 0.015,
        "view_omega": 0.0015,
    },
}


# ---------------------------------------------------------------------------
# Universe assembly — base 11-asset, or 12-asset with IWMO bolted on
# ---------------------------------------------------------------------------

def _base_inputs() -> Dict:
    """Return the base 11-asset BL inputs straight from BL_CONFIG."""
    assets = list(BL_CONFIG["assets"])
    w_mkt = BL_CONFIG["market_cap_weights"].astype(float).copy()
    vols = BL_CONFIG["volatilities"].astype(float).copy()
    corr = BL_CONFIG["correlations"].astype(float).copy()
    P = BL_CONFIG["views"]["P"].astype(float).copy()
    Q = BL_CONFIG["views"]["Q"].astype(float).copy()
    omega_diag = BL_CONFIG["views"]["omega_diag"].astype(float).copy()
    return {
        "assets": assets, "w_mkt": w_mkt, "vols": vols, "corr": corr,
        "P": P, "Q": Q, "omega_diag": omega_diag,
    }


def _inputs_with_iwmo() -> Dict:
    """Return 12-asset BL inputs: base universe + IWMO momentum sleeve + view.

    IWMO is appended as the 12th asset. The market-cap vector gains a raw 4%
    slot and is renormalised to sum to one. The correlation matrix gains a
    row/column. A sixth view ("IWMO > VWRP by 1.5% p.a.") is appended to P/Q/Ω.
    """
    base = _base_inputs()
    iwmo = CONFIG["iwmo"]

    assets = base["assets"] + [iwmo["ticker"]]

    # Market weights: carve IWMO's ~4% slot OUT of VWRP rather than
    # renormalising the whole vector. Momentum stocks already sit inside the
    # FTSE All-World (VWRP), so this avoids double-counting AND avoids the
    # artefact of scaling every equilibrium return down by ~4% (which would
    # mechanically deflate every Sharpe ratio). The vector still sums to one.
    w_mkt = np.append(base["w_mkt"].copy(), iwmo["market_cap_weight_raw"])
    w_mkt[base["assets"].index("VWRP")] -= iwmo["market_cap_weight_raw"]

    vols = np.append(base["vols"], iwmo["volatility"])

    # Extend the correlation matrix with IWMO's row/column.
    n = len(base["assets"])
    corr = np.zeros((n + 1, n + 1))
    corr[:n, :n] = base["corr"]
    iwmo_corr = np.array(iwmo["corr"], dtype=float)
    corr[n, :n] = iwmo_corr
    corr[:n, n] = iwmo_corr
    corr[n, n] = 1.0

    # Existing views gain a zero column for IWMO.
    P = np.hstack([base["P"], np.zeros((base["P"].shape[0], 1))])
    # New momentum view: long IWMO (+1), short VWRP (-1).
    new_row = np.zeros(n + 1)
    new_row[assets.index("VWRP")] = -1.0
    new_row[assets.index("IWMO")] = +1.0
    P = np.vstack([P, new_row])
    Q = np.append(base["Q"], iwmo["view_q"])
    omega_diag = np.append(base["omega_diag"], iwmo["view_omega"])

    return {
        "assets": assets, "w_mkt": w_mkt, "vols": vols, "corr": corr,
        "P": P, "Q": Q, "omega_diag": omega_diag,
    }


def compute_posterior(inp: Dict, q_override: Optional[Dict[int, float]] = None
                      ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run the BL pipeline for an inputs dict.

    ``q_override`` optionally replaces individual entries of Q (used for the
    "what if the momentum view is wrong" sensitivity, where we set Q=0).

    Returns (pi, mu_post_excess, sigma_post).
    """
    sigma = covariance_from_corr(inp["vols"], inp["corr"])
    pi = implied_equilibrium_returns(BL_CONFIG["risk_aversion"], sigma, inp["w_mkt"])
    Q = inp["Q"].copy()
    if q_override:
        for idx, val in q_override.items():
            Q[idx] = val
    omega = np.diag(inp["omega_diag"])
    mu_post, sigma_post = black_litterman_posterior(
        pi, sigma, BL_CONFIG["tau"], inp["P"], Q, omega,
    )
    return pi, mu_post, sigma_post


# ---------------------------------------------------------------------------
# Sharpe-maximising optimiser (SLSQP, same family as bl_model.py)
# ---------------------------------------------------------------------------

def max_sharpe_weights(
    mu_excess: np.ndarray,
    sigma: np.ndarray,
    max_weight: Optional[float] = 0.40,
    forced_min: Optional[Dict[int, float]] = None,
    min_weight_if_held: float = 0.02,
    apply_min_if_held: bool = True,
) -> np.ndarray:
    """Find long-only, fully-invested weights that maximise the Sharpe ratio.

    Parameters
    ----------
    mu_excess : posterior expected *excess* returns.
    sigma : posterior covariance.
    max_weight : per-asset cap; ``None`` removes the cap (Modification 6).
    forced_min : {asset_index: minimum weight} hard floors (e.g. SGLN >= 5%).
    min_weight_if_held : assets the optimiser puts below this (and that are not
        force-floored) are dropped to zero and the problem re-solved, mirroring
        the min-2%-if-held rule used in bl_model.py.
    apply_min_if_held : set False for the theoretical-ceiling run (Mod 6), where
        we allow arbitrarily small positions.
    """
    n = len(mu_excess)
    forced_min = forced_min or {}
    cap = 1.0 if max_weight is None else max_weight

    def bounds_for(active: np.ndarray) -> List[Tuple[float, float]]:
        b = []
        for i in range(n):
            lo = forced_min.get(i, 0.0)
            hi = cap
            if not active[i]:
                lo, hi = 0.0, 0.0  # forced out
            b.append((lo, hi))
        return b

    def neg_sharpe(w: np.ndarray) -> float:
        vol = np.sqrt(w @ sigma @ w)
        if vol < 1e-12:
            return 0.0
        return -(w @ mu_excess) / vol

    cons = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
    w0 = np.full(n, 1.0 / n)

    def solve(active: np.ndarray) -> np.ndarray:
        res = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds_for(active),
                       constraints=cons, options={"maxiter": 1000, "ftol": 1e-12})
        return res.x

    active = np.ones(n, dtype=bool)
    w = solve(active)

    if apply_min_if_held:
        # Drop sub-threshold, non-floored lines and re-solve once.
        forced_idx = set(forced_min)
        keep = np.array([
            (i in forced_idx) or (w[i] >= min_weight_if_held) for i in range(n)
        ])
        if not keep.all():
            w = solve(keep)
            w = np.where(keep, w, 0.0)

    w = np.clip(w, 0.0, None)
    return w / w.sum()


# ---------------------------------------------------------------------------
# Metrics for a single variant
# ---------------------------------------------------------------------------

ALL_TICKERS = list(BL_CONFIG["assets"]) + ["IWMO"]


def variant_stats(
    name: str,
    assets: List[str],
    weights: np.ndarray,
    mu_excess: np.ndarray,
    sigma_post: np.ndarray,
) -> Dict:
    """Build the full statistics row for one portfolio variant."""
    rf = BL_CONFIG["risk_free"]
    m = portfolio_metrics(weights, mu_excess, sigma_post, rf)
    nominal = m["expected_return"]
    real = (1 + nominal) / (1 + CONFIG["inflation"]) - 1
    vol = m["volatility"]

    wmap = dict(zip(assets, weights))
    ter = sum(wmap.get(t, 0.0) * CONFIG["ter"][t] for t in assets)

    sleeves = {"equity": 0.0, "bond": 0.0, "real_asset": 0.0, "gold": 0.0}
    for t, w in wmap.items():
        sleeves[CONFIG["sleeve"][t]] += w

    row = {
        "variant": name,
        "E[r] nominal": nominal,
        "E[r] real": real,
        "volatility": vol,
        "sharpe": m["sharpe"],
        "max_drawdown_est": -CONFIG["drawdown_z"] * vol,
        "blended_TER": ter,
        "equity_pct": sleeves["equity"],
        "bond_pct": sleeves["bond"],
        "real_asset_pct": sleeves["real_asset"],
        "gold_pct": sleeves["gold"],
    }
    # Append the full weight vector across the 12-ticker superset.
    for t in ALL_TICKERS:
        row[f"w_{t}"] = wmap.get(t, 0.0)
    return row


# ---------------------------------------------------------------------------
# Build all variants
# ---------------------------------------------------------------------------

def run_all_variants() -> Tuple[pd.DataFrame, Dict]:
    """Compute the base, six modifications, and three benchmarks.

    Returns (stats_dataframe, extras) where extras carries the objects the
    deep-dive and report need (best variant, posteriors, sensitivities).
    """
    rows: List[Dict] = []
    extras: Dict = {}

    # --- Base posterior (11 assets) ----------------------------------------
    base = _base_inputs()
    _, mu_base, sig_base = compute_posterior(base)
    a_base = base["assets"]
    idx_base = {t: i for i, t in enumerate(a_base)}

    # Base portfolio = existing bl_model recommendation (status quo).
    w_base = _read_base_recommended(a_base)
    rows.append(variant_stats("Base (current)", a_base, w_base, mu_base, sig_base))
    extras["base"] = {
        "assets": a_base, "mu": mu_base, "sigma": sig_base, "weights": w_base,
    }

    # --- 12-asset posterior (base + IWMO), reused by Mods 1/4/5/6 ----------
    ext = _inputs_with_iwmo()
    _, mu_ext, sig_ext = compute_posterior(ext)
    a_ext = ext["assets"]
    idx_ext = {t: i for i, t in enumerate(a_ext)}

    # --- Modification 1: add momentum --------------------------------------
    w_m1 = max_sharpe_weights(mu_ext, sig_ext)
    rows.append(variant_stats("Mod 1: +Momentum (IWMO)", a_ext, w_m1, mu_ext, sig_ext))

    # --- Modification 2: restore gold, SGLN >= 5% (base universe) ----------
    w_m2 = max_sharpe_weights(mu_base, sig_base,
                              forced_min={idx_base["SGLN"]: 0.05})
    rows.append(variant_stats("Mod 2: Gold >=5% (SGLN)", a_base, w_m2, mu_base, sig_base))

    # --- Modification 3: infrastructure INFR >= 7% (base universe) ---------
    w_m3 = max_sharpe_weights(mu_base, sig_base,
                              forced_min={idx_base["INFR"]: 0.07})
    rows.append(variant_stats("Mod 3: Infra >=7% (INFR)", a_base, w_m3, mu_base, sig_base))

    # --- Modification 4: value + momentum combo ----------------------------
    # IWMO added (Mod 1) + IWVL >= 10% (reinforced value), NO gold floor.
    # NOTE: the brief's parenthetical mentions "SGLN restored at 5%" but its
    # closing sentence states "No gold restoration in this variant"; the title
    # is "Value + Momentum". We follow the explicit no-gold instruction and the
    # title — see the report for this resolution.
    w_m4 = max_sharpe_weights(mu_ext, sig_ext,
                              forced_min={idx_ext["IWVL"]: 0.10})
    rows.append(variant_stats("Mod 4: Value+Momentum", a_ext, w_m4, mu_ext, sig_ext))

    # --- Modification 5: full kitchen sink ---------------------------------
    w_m5 = max_sharpe_weights(mu_ext, sig_ext, forced_min={
        idx_ext["SGLN"]: 0.05, idx_ext["INFR"]: 0.07, idx_ext["IWVL"]: 0.10,
    })
    rows.append(variant_stats("Mod 5: Kitchen sink", a_ext, w_m5, mu_ext, sig_ext))

    # --- Modification 6: unconstrained Sharpe max (no 40% cap) -------------
    w_m6 = max_sharpe_weights(mu_ext, sig_ext, max_weight=None,
                              apply_min_if_held=False)
    rows.append(variant_stats("Mod 6: Unconstrained", a_ext, w_m6, mu_ext, sig_ext))

    # --- Benchmarks (computed on the base posterior, as in bl_model.py) ----
    w_6040 = np.zeros(len(a_base))
    w_6040[idx_base["VWRP"]] = 0.60
    w_6040[idx_base["AGGG"]] = 0.40
    rows.append(variant_stats("Benchmark: 60/40", a_base, w_6040, mu_base, sig_base))

    w_acwi = np.zeros(len(a_base))
    w_acwi[idx_base["VWRP"]] = 1.0
    rows.append(variant_stats("Benchmark: 100% VWRP", a_base, w_acwi, mu_base, sig_base))

    # Reference: base universe re-optimised for max Sharpe under the same
    # constraints (max 40%, min 2%). Lets the report separate the effect of the
    # objective change from the effect of the modifications themselves.
    w_ref = max_sharpe_weights(mu_base, sig_base)
    ref_row = variant_stats("Ref: base universe max-Sharpe", a_base, w_ref, mu_base, sig_base)

    df = pd.DataFrame(rows)

    extras.update({
        "ext_assets": a_ext, "mu_ext": mu_ext, "sig_ext": sig_ext,
        "weights": {
            "Mod 1: +Momentum (IWMO)": (a_ext, w_m1),
            "Mod 2: Gold >=5% (SGLN)": (a_base, w_m2),
            "Mod 3: Infra >=7% (INFR)": (a_base, w_m3),
            "Mod 4: Value+Momentum": (a_ext, w_m4),
            "Mod 5: Kitchen sink": (a_ext, w_m5),
            "Mod 6: Unconstrained": (a_ext, w_m6),
            "Base (current)": (a_base, w_base),
        },
        "ref_row": ref_row,
        "ext_inputs": ext,
    })
    return df, extras


def _read_base_recommended(assets: List[str]) -> np.ndarray:
    """Load the existing recommended weights from optimal_weights.csv."""
    path = os.path.join(CONFIG["results_dir"], "optimal_weights.csv")
    w = pd.read_csv(path).set_index("asset")["bl_constrained_recommended"]
    return np.array([w[a] for a in assets], dtype=float)


# ---------------------------------------------------------------------------
# Sensitivity: what if the IWMO momentum view is wrong (Q = 0)?
# ---------------------------------------------------------------------------

def momentum_view_off(extras: Dict, variant_name: str) -> Dict:
    """Re-optimise a momentum-bearing variant with the momentum view Q set to 0."""
    ext = extras["ext_inputs"]
    mom_idx = len(ext["Q"]) - 1  # the appended momentum view is last
    _, mu_off, sig_off = compute_posterior(ext, q_override={mom_idx: 0.0})
    a_ext = ext["assets"]
    # Re-solve with the same constraints used for that variant.
    if variant_name == "Mod 1: +Momentum (IWMO)":
        w = max_sharpe_weights(mu_off, sig_off)
    elif variant_name == "Mod 4: Value+Momentum":
        w = max_sharpe_weights(mu_off, sig_off,
                               forced_min={a_ext.index("IWVL"): 0.10})
    else:
        w = max_sharpe_weights(mu_off, sig_off)
    return variant_stats(f"{variant_name} (Q_mom=0)", a_ext, w, mu_off, sig_off)


# ---------------------------------------------------------------------------
# Monte Carlo deep-dive for the best practical variant vs base
# ---------------------------------------------------------------------------

def monte_carlo_compare(best_name: str, best_stats: Dict,
                        base_stats: Dict) -> Dict:
    """Run the wealth_model Monte Carlo for the best variant and the base.

    Returns percentile-by-age tables and target-probability tables for both.
    """
    cf = build_cashflow_schedule()["contribution_gbp"]
    n_paths = WM_CONFIG["n_paths"]
    seed = WM_CONFIG["seed"]
    infl = WM_CONFIG["inflation"]

    out = {}
    for label, st in (("base", base_stats), ("best", best_stats)):
        wealth = simulate_paths(cf, st["E[r] nominal"], st["volatility"],
                                n_paths, seed, infl)
        out[label] = {
            "ages": value_at_ages(wealth, cf.index),
            "targets": probability_of_reaching(wealth, WEALTH_TARGETS),
            "mu": st["E[r] nominal"], "sigma": st["volatility"],
        }
    out["best_name"] = best_name
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(CONFIG["results_dir"], exist_ok=True)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)

    df, extras = run_all_variants()

    # ---- Save the headline comparison table -------------------------------
    summary_cols = [
        "variant", "E[r] nominal", "E[r] real", "volatility", "sharpe",
        "max_drawdown_est", "blended_TER", "equity_pct", "bond_pct",
        "real_asset_pct", "gold_pct",
    ]
    summary = df[summary_cols].copy()
    summary.to_csv(os.path.join(CONFIG["results_dir"],
                                "sharpe_opt_summary.csv"), index=False)

    # ---- Save the full weight matrix --------------------------------------
    weight_cols = ["variant"] + [f"w_{t}" for t in ALL_TICKERS]
    df[weight_cols].to_csv(os.path.join(CONFIG["results_dir"],
                                        "sharpe_opt_weights.csv"), index=False)

    # ---- Identify best practical (constrained) and best overall -----------
    practical = df[df["variant"].str.startswith("Mod")
                   & ~df["variant"].str.contains("Unconstrained")]
    best_practical = practical.loc[practical["sharpe"].idxmax()]
    best_overall = df[df["variant"].str.startswith("Mod")].loc[
        df[df["variant"].str.startswith("Mod")]["sharpe"].idxmax()]

    # ---- Sensitivity: momentum view off (applies to the best practical) ---
    sens_rows = []
    if "Momentum" in best_practical["variant"] or "Value+Momentum" in best_practical["variant"]:
        sens_rows.append(momentum_view_off(extras, best_practical["variant"]))
    # Always also report Mod 1 with the view off for reference.
    sens_rows.append(momentum_view_off(extras, "Mod 1: +Momentum (IWMO)"))
    sens_df = pd.DataFrame(sens_rows)[summary_cols]
    sens_df.to_csv(os.path.join(CONFIG["results_dir"],
                                "sharpe_opt_sensitivity.csv"), index=False)

    # ---- Monte Carlo deep-dive --------------------------------------------
    base_stats = df[df["variant"] == "Base (current)"].iloc[0].to_dict()
    best_stats = best_practical.to_dict()
    mc = monte_carlo_compare(best_practical["variant"], best_stats, base_stats)
    mc["base"]["ages"].to_csv(os.path.join(CONFIG["results_dir"],
                              "sharpe_opt_mc_base_by_age.csv"), index=False)
    mc["best"]["ages"].to_csv(os.path.join(CONFIG["results_dir"],
                              "sharpe_opt_mc_best_by_age.csv"), index=False)
    mc["base"]["targets"].to_csv(os.path.join(CONFIG["results_dir"],
                                 "sharpe_opt_mc_base_targets.csv"), index=False)
    mc["best"]["targets"].to_csv(os.path.join(CONFIG["results_dir"],
                                 "sharpe_opt_mc_best_targets.csv"), index=False)

    # ---- Console report ---------------------------------------------------
    pct = lambda x: f"{x:,.2%}"
    print("=" * 100)
    print("SHARPE OPTIMISATION — variant comparison")
    print("=" * 100)
    show = summary.copy()
    for c in ["E[r] nominal", "E[r] real", "volatility", "max_drawdown_est",
              "blended_TER", "equity_pct", "bond_pct", "real_asset_pct", "gold_pct"]:
        show[c] = show[c].map(pct)
    show["sharpe"] = summary["sharpe"].map(lambda x: f"{x:.4f}")
    print(show.to_string(index=False))

    print("\nReference (base universe re-optimised for max Sharpe, same constraints):")
    ref = extras["ref_row"]
    print(f"  Sharpe={ref['sharpe']:.4f}  E[r]={ref['E[r] nominal']:.2%}  "
          f"vol={ref['volatility']:.2%}")

    print(f"\nBest practical modification: {best_practical['variant']}  "
          f"(Sharpe {best_practical['sharpe']:.4f})")
    print(f"Best overall (incl. unconstrained): {best_overall['variant']}  "
          f"(Sharpe {best_overall['sharpe']:.4f})")

    base_sharpe = base_stats["sharpe"]
    print(f"\nSharpe uplift vs base ({base_sharpe:.4f}):")
    for _, r in df[df["variant"].str.startswith("Mod")].iterrows():
        print(f"  {r['variant']:<28} {r['sharpe']:.4f}  "
              f"({(r['sharpe']-base_sharpe)*100:+.2f} pts, "
              f"{(r['sharpe']/base_sharpe-1)*100:+.1f}%)")

    print("\nMomentum-view sensitivity (Q=0):")
    print(sens_df[["variant", "E[r] nominal", "volatility", "sharpe"]]
          .to_string(index=False))

    print(f"\nMonte Carlo — {best_practical['variant']} vs Base (real GBP, by age):")
    print("Base:")
    print(mc["base"]["ages"].to_string(index=False,
          float_format=lambda x: f"£{x:,.0f}"))
    print("Best:")
    print(mc["best"]["ages"].to_string(index=False,
          float_format=lambda x: f"£{x:,.0f}"))
    print("\nTarget probabilities (prob at horizon end):")
    print("Base:", dict(zip(mc["base"]["targets"]["target_gbp_real"],
                            mc["base"]["targets"]["prob_at_end"].round(3))))
    print("Best:", dict(zip(mc["best"]["targets"]["target_gbp_real"],
                            mc["best"]["targets"]["prob_at_end"].round(3))))

    print("\nFull weight matrix:")
    wshow = df[weight_cols].copy()
    for t in ALL_TICKERS:
        wshow[f"w_{t}"] = wshow[f"w_{t}"].map(lambda x: f"{x:.3f}")
    print(wshow.to_string(index=False))


if __name__ == "__main__":
    main()
