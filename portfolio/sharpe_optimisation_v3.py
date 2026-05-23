"""Third-round Sharpe optimisation: DYNAMIC strategies, walk-forward backtest.

v1 tested static asset *additions* (failed: the opportunity set, not the limits,
was binding). v2 tested static *levers* — currency hedging, a managed-futures
sleeve, view recalibration, risk parity (partial success, ceiling ~0.34 @4%).

v3 tests rules-based *dynamic* allocation overlays applied to a static base:
factor timing, macro-regime rotation, volatility targeting, drawdown control,
Faber trend filtering, cross-sectional momentum, tail hedging, and stacks.

These are fundamentally different from v1/v2 — they adapt through time — and they
carry serious methodological risk (overfitting, look-ahead, transaction costs).
The whole point of this module is to test them HONESTLY:

  * No look-ahead: every signal at month t uses data through t-1 only (.shift(1)).
  * Walk-forward: IS (2005-2015) tunes / OOS (2016-2026) validates, reported apart.
  * Transaction costs: 5bps round-trip on monthly turnover, subtracted from returns.
  * Robustness: lookback / threshold / target perturbed +/-20% (and a null model).
  * Real data, real regimes (GFC, 2011, 2018, COVID, 2022) — see data note below.

Run end-to-end (fetches+caches data on first run, then offline)::

    python portfolio/data_fetch.py          # one-off: cache real price history
    python portfolio/sharpe_optimisation_v3.py

All result tables are written to ``portfolio/results/`` with a ``v3_`` prefix;
charts (equity curves, drawdowns, rolling Sharpe, turnover, allocation, MC fan)
are saved alongside. Every number in ``docs/sharpe_optimisation_v3.md`` is
produced here.

DATA NOTE
---------
The investable UCITS ETFs are too young to backtest across real regimes, so each
sleeve is mapped to a long-history GBP-converted proxy (see ``data_fetch.py`` /
``data/proxy_map.csv``). Two sleeves from v2's full stack are NOT backtestable on
real data: the GBP-hedged S&P (GSPX) is proxied by the USD total return of the
S&P 500 (the hedge strips the FX leg; the small carry differential is ignored),
and the managed-futures TREND sleeve has no reachable long total-return series —
so the "v2 base" used here is the BL portfolio with its US sleeve hedged (v2's
dominant real lever), and v2's Mod-7 assumption-based 0.343 Sharpe is carried
forward only as a labelled reference. The macro-regime overlay (Mod 2) uses
market-based growth/inflation proxies because FRED (OECD CLI, UK CPI) is
unreachable from this environment. All such substitutions are flagged in the
report's Methodology.
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

sys.path.insert(0, os.path.dirname(__file__))
from bl_model import (  # noqa: E402
    CONFIG as BL_CONFIG,
    black_litterman_posterior,
    covariance_from_corr,
    implied_equilibrium_returns,
    mean_variance_constrained,
)
from data_fetch import fetch_all  # noqa: E402

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
# CONFIG
# ---------------------------------------------------------------------------

CONFIG_V3: Dict = {
    "results_dir": BL_CONFIG["results_dir"],
    "rf_base": BL_CONFIG["risk_free"],        # 4.0%
    "rf_recal": 0.025,                        # 2.5% long-run gilt proxy
    "inflation": WM_CONFIG["inflation"],      # 2.5%

    # Walk-forward split (signals warm up over <= 12m before IS starts).
    "backtest_start": "2005-01-31",           # 12m warm-up after 2004-01 data start
    "is_end": "2015-12-31",                   # in-sample tuning window end
    "oos_start": "2016-01-31",                # out-of-sample validation start

    # Transaction costs.
    "roundtrip_bps": 0.0005,                  # 5bps round-trip per unit one-way turnover

    "cash_asset": "IGLS",                     # de-risking destination / cash proxy
    "value_asset": "IWVL",
    "quality_asset": "IWQU",
    "gold_asset": "SGLN",
    "bond_asset": "AGGG",
    "growth_signal_asset": "VWRP",            # 6m momentum -> growth-direction proxy

    # ---- Mod 1: valuation-based Value/Quality factor timing ----------------
    "ft_lookback": 120,                       # 10y history for the quintile rank
    "ft_min_obs": 60,                         # expanding until 120 available
    "ft_tilt": 0.05,                          # +/-5% IWVL vs IWQU
    "ft_low_q": 0.20,                         # bottom quintile of V/G ratio = value cheap
    "ft_high_q": 0.80,

    # ---- Mod 2: macro regime factor rotation -------------------------------
    "regime_growth_lb": 6,                    # 6m change in growth proxy
    "regime_infl_lb": 12,                     # 12m change in 10y yield
    "regime_tilt": 0.05,

    # ---- Mod 3: volatility targeting ---------------------------------------
    "vol_lookback": 6,                        # months of monthly returns for realised vol
    "vol_target": 0.10,
    "vol_target_grid": [0.08, 0.10, 0.12, None],   # None = unconstrained (base)
    "vol_risky_cap": 1.00,                    # no leverage in an ISA
    "vol_risky_floor": 0.60,

    # ---- Mod 4: drawdown-controlled exposure -------------------------------
    "dd_peak_lb": 12,                         # trailing 12m peak
    "dd_trig_1": 0.10, "dd_mult_1": 0.75,
    "dd_trig_2": 0.20, "dd_mult_2": 0.50,
    "dd_restore": 0.05,

    # ---- Mod 5: Faber GTAA trend overlay -----------------------------------
    "faber_ma": 10,                           # 10-month SMA

    # ---- Mod 6: cross-sectional momentum -----------------------------------
    "xmom_lb": 12, "xmom_skip": 1,            # 12-minus-1 month
    "xmom_tilt": 0.20,

    # ---- Mod 7: tail-risk hedge (synthetic, stylised) ----------------------
    "tail_weight": 0.03,
    "tail_carry_ann": -0.02,                  # -2% nominal carry
    "tail_crash_thresh": -0.05,               # equity monthly return below this = crash
    "tail_crash_beta": 4.0,                   # convex payoff multiple beyond threshold

    "mc_seed_paths": WM_CONFIG["n_paths"],
    "mc_seed": WM_CONFIG["seed"],
}


# ---------------------------------------------------------------------------
# Base portfolios (real-data-backtestable)
# ---------------------------------------------------------------------------

def _bl_base_weights() -> Dict[str, float]:
    """The original BL constrained recommended weights (11 assets)."""
    sigma = covariance_from_corr(BL_CONFIG["volatilities"], BL_CONFIG["correlations"])
    pi = implied_equilibrium_returns(BL_CONFIG["risk_aversion"], sigma,
                                     BL_CONFIG["market_cap_weights"])
    mu, sg = black_litterman_posterior(
        pi, sigma, BL_CONFIG["tau"], BL_CONFIG["views"]["P"],
        BL_CONFIG["views"]["Q"], np.diag(BL_CONFIG["views"]["omega_diag"]))
    w = mean_variance_constrained(BL_CONFIG["risk_aversion"], mu, sg, 0.40, 0.02, True)
    return {a: float(wt) for a, wt in zip(BL_CONFIG["assets"], w) if wt > 1e-6}


def _v2_base_weights() -> Dict[str, float]:
    """v2 base = BL weights with the US sleeve currency-hedged (VUSA -> GSPX).

    This embodies v2's single largest *real-data-backtestable* lever (currency
    hedging, Mod 1). It is NOT v2's Mod-7 full stack, which additionally needed a
    managed-futures sleeve that has no reachable long total-return series.
    """
    bl = _bl_base_weights()
    out: Dict[str, float] = {}
    for a, w in bl.items():
        out["GSPX" if a == "VUSA" else a] = w
    return out


# ---------------------------------------------------------------------------
# Data loading -> monthly return panel
# ---------------------------------------------------------------------------

def load_panel() -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Return (returns_gbp, prices_gbp, us10y_yield) on the common monthly grid.

    The GBP-hedged S&P proxy 'GSPX' is appended as the USD total return of the
    S&P 500 (VUSA native), i.e. the FX leg removed.
    """
    frames = fetch_all(force=False)
    px = frames["prices_gbp"].copy()
    nat = frames["prices_native"].copy()
    macro = frames["macro"].copy()

    # Common window across all 11 sleeves.
    px = px.dropna()
    rets = px.pct_change().dropna(how="all")

    # GSPX (hedged S&P) = USD total return of the S&P 500.
    gspx_ret = nat["VUSA"].pct_change().reindex(rets.index)
    rets["GSPX"] = gspx_ret

    rets = rets.dropna()
    px = px.reindex(rets.index)
    yld = macro["US10Y_YIELD"].reindex(rets.index).ffill()
    return rets, px, yld


# ---------------------------------------------------------------------------
# Backtest evaluation core
# ---------------------------------------------------------------------------

def evaluate(weights: pd.DataFrame, rets: pd.DataFrame) -> pd.DataFrame:
    """Given a target-weight matrix (decided at t, applied over month t), return
    a frame with gross/net monthly returns and one-way turnover.

    Weights for month t must already be lagged (use only info <= t-1). Turnover
    compares the new target to the *drifted* prior weights. Cost is charged at
    the round-trip rate on one-way turnover.
    """
    cols = weights.columns
    R = rets[cols].reindex(weights.index).fillna(0.0)
    w_prev_drift = pd.Series(0.0, index=cols)
    gross, net, turn = [], [], []
    for t in weights.index:
        w_t = weights.loc[t].fillna(0.0)
        traded = (w_t - w_prev_drift).abs().sum()      # buys+sells fraction
        oneway = 0.5 * traded
        cost = CONFIG_V3["roundtrip_bps"] * oneway
        r_t = float((w_t * R.loc[t]).sum())
        gross.append(r_t)
        net.append(r_t - cost)
        turn.append(oneway)
        # Drift the held weights to the end of month t.
        grown = w_t * (1.0 + R.loc[t])
        s = grown.sum()
        w_prev_drift = grown / s if s != 0 else w_t
    return pd.DataFrame({"gross": gross, "net": net, "turnover": turn},
                        index=weights.index)


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------

def _ann_return(monthly: pd.Series) -> float:
    if len(monthly) == 0:
        return np.nan
    return float((1 + monthly).prod() ** (12 / len(monthly)) - 1)


def _ann_vol(monthly: pd.Series) -> float:
    return float(monthly.std(ddof=1) * np.sqrt(12))


def _max_drawdown(monthly: pd.Series) -> float:
    nav = (1 + monthly).cumprod()
    peak = nav.cummax()
    return float((nav / peak - 1).min())


def _sortino(monthly: pd.Series, rf_ann: float) -> float:
    rf_m = (1 + rf_ann) ** (1 / 12) - 1
    downside = monthly[monthly < rf_m] - rf_m
    dd = np.sqrt((downside ** 2).mean()) * np.sqrt(12) if len(downside) else np.nan
    if not dd or np.isnan(dd):
        return np.nan
    return (_ann_return(monthly) - rf_ann) / dd


def metrics(net: pd.Series, gross: pd.Series, turnover: pd.Series,
            label: str) -> Dict:
    ann_net = _ann_return(net)
    ann_gross = _ann_return(gross)
    vol = _ann_vol(net)
    mdd = _max_drawdown(net)
    ann_turn = float(turnover.mean() * 12)
    tc_drag = ann_turn * CONFIG_V3["roundtrip_bps"]
    return {
        "strategy": label,
        "ann_return_gross": ann_gross,
        "ann_return_net": ann_net,
        "ann_vol": vol,
        "sharpe_rf4.0": (ann_net - CONFIG_V3["rf_base"]) / vol if vol else np.nan,
        "sharpe_rf2.5": (ann_net - CONFIG_V3["rf_recal"]) / vol if vol else np.nan,
        "sortino_rf4.0": _sortino(net, CONFIG_V3["rf_base"]),
        "max_drawdown": mdd,
        "calmar": (ann_net / abs(mdd)) if mdd else np.nan,
        "ann_turnover": ann_turn,
        "tc_drag": tc_drag,
        "hit_rate": float((net > 0).mean()),
    }


def windowed_metrics(perf: pd.DataFrame, label: str) -> Dict:
    """Compute metrics on full / IS / OOS windows and the OOS-IS Sharpe gap."""
    is_mask = perf.index <= pd.Timestamp(CONFIG_V3["is_end"])
    oos_mask = perf.index >= pd.Timestamp(CONFIG_V3["oos_start"])
    full = metrics(perf["net"], perf["gross"], perf["turnover"], label)
    ism = metrics(perf["net"][is_mask], perf["gross"][is_mask],
                  perf["turnover"][is_mask], label)
    oosm = metrics(perf["net"][oos_mask], perf["gross"][oos_mask],
                   perf["turnover"][oos_mask], label)
    full["sharpe_IS"] = ism["sharpe_rf4.0"]
    full["sharpe_OOS"] = oosm["sharpe_rf4.0"]
    full["sharpe_gap_OOS_minus_IS"] = oosm["sharpe_rf4.0"] - ism["sharpe_rf4.0"]
    full["ann_return_net_OOS"] = oosm["ann_return_net"]
    full["max_drawdown_OOS"] = oosm["max_drawdown"]
    return full


# ---------------------------------------------------------------------------
# Weight-matrix builders (each returns a lagged target-weight DataFrame)
# ---------------------------------------------------------------------------

def _full_index(rets: pd.DataFrame) -> pd.DatetimeIndex:
    start = pd.Timestamp(CONFIG_V3["backtest_start"])
    return rets.index[rets.index >= start]


def _const_weights(base: Dict[str, float], rets: pd.DataFrame) -> pd.DataFrame:
    idx = _full_index(rets)
    W = pd.DataFrame(0.0, index=idx, columns=rets.columns)
    for a, w in base.items():
        W[a] = w
    return W


def _normalise(s: pd.Series) -> pd.Series:
    s = s.clip(lower=0.0)
    tot = s.sum()
    return s / tot if tot > 0 else s


def _risky_cash_split(base: Dict[str, float]) -> Tuple[Dict[str, float], float]:
    cash = CONFIG_V3["cash_asset"]
    risky = {a: w for a, w in base.items() if a != cash}
    cash_w = base.get(cash, 0.0)
    return risky, cash_w


def build_static(base: Dict[str, float], rets: pd.DataFrame) -> pd.DataFrame:
    return _const_weights(base, rets)


def build_factor_timing(base: Dict[str, float], rets: pd.DataFrame,
                        prices: pd.DataFrame, tilt: Optional[float] = None,
                        low_q: Optional[float] = None,
                        high_q: Optional[float] = None) -> pd.DataFrame:
    """Mod 1. Value/Quality tilt off the Value-vs-Growth valuation percentile.

    Proxy for the P/B spread: the cumulative relative *price* of Value (IVE) vs
    Growth (IVW). When that ratio sits in the bottom quintile of its trailing
    10y history, Value has cheapened -> overweight IWVL / underweight IWQU.
    Hysteresis: hold the current tilt until a new quintile boundary is crossed.
    """
    tilt = CONFIG_V3["ft_tilt"] if tilt is None else tilt
    low_q = CONFIG_V3["ft_low_q"] if low_q is None else low_q
    high_q = CONFIG_V3["ft_high_q"] if high_q is None else high_q
    val, qual = CONFIG_V3["value_asset"], CONFIG_V3["quality_asset"]
    idx = _full_index(rets)
    W = _const_weights(base, rets)

    ratio = (prices[val] / prices[qual])           # value/quality relative level
    state = 0                                       # -1 expensive, 0 neutral, +1 cheap
    for t in idx:
        hist = ratio.loc[:t].iloc[:-1]              # info through t-1 only
        n = len(hist)
        if n >= CONFIG_V3["ft_min_obs"]:
            window = hist.iloc[-CONFIG_V3["ft_lookback"]:] if n >= CONFIG_V3["ft_lookback"] else hist
            pct = (window < hist.iloc[-1]).mean()   # percentile rank of last obs
            if pct <= low_q:
                state = 1                            # value historically cheap
            elif pct >= high_q:
                state = -1                           # value historically expensive
            # middle quintiles: keep prior state (hysteresis)
        if state != 0 and val in base:
            W.loc[t, val] = base.get(val, 0.0) + state * tilt
            W.loc[t, qual] = base.get(qual, 0.0) - state * tilt
            W.loc[t] = _normalise(W.loc[t])
    return W


def build_macro_regime(base: Dict[str, float], rets: pd.DataFrame,
                       prices: pd.DataFrame, yld: pd.Series,
                       tilt: Optional[float] = None) -> pd.DataFrame:
    """Mod 2. 4-quadrant growth/inflation regime tilt (market-proxy signals)."""
    tilt = CONFIG_V3["regime_tilt"] if tilt is None else tilt
    g_asset = CONFIG_V3["growth_signal_asset"]
    val, qual = CONFIG_V3["value_asset"], CONFIG_V3["quality_asset"]
    gold, bond = CONFIG_V3["gold_asset"], CONFIG_V3["bond_asset"]
    idx = _full_index(rets)
    W = _const_weights(base, rets)

    g_lvl = prices[g_asset]
    growth_chg = g_lvl.pct_change(CONFIG_V3["regime_growth_lb"]).shift(1)   # t-1
    infl_chg = yld.diff(CONFIG_V3["regime_infl_lb"]).shift(1)               # t-1

    for t in idx:
        g = growth_chg.get(t, np.nan)
        i = infl_chg.get(t, np.nan)
        if np.isnan(g) or np.isnan(i):
            continue
        rising_g, rising_i = g > 0, i > 0
        if rising_g and rising_i:        # Reflation -> Value
            fav = val
        elif rising_g and not rising_i:  # Goldilocks -> Quality
            fav = qual
        elif not rising_g and rising_i:  # Stagflation -> Gold
            fav = gold
        else:                            # Deflation/Recession -> Bonds (defensives)
            fav = bond
        w = W.loc[t].copy()
        add = tilt
        w[fav] = w.get(fav, 0.0) + add
        # Fund pro-rata from the other held sleeves.
        others = [a for a in base if a != fav]
        cur = {a: W.loc[t, a] for a in others}
        tot = sum(cur.values())
        if tot > 0:
            for a in others:
                w[a] = cur[a] - add * cur[a] / tot
        W.loc[t] = _normalise(w)
    return W


def _portfolio_returns_for_signal(base: Dict[str, float],
                                  rets: pd.DataFrame) -> pd.Series:
    """Monthly return of the *static base* — used as the signal series for the
    vol-target and drawdown overlays (these scale the base portfolio)."""
    cols = list(base.keys())
    w = pd.Series(base)
    return (rets[cols] * w).sum(axis=1)


def build_vol_target(base: Dict[str, float], rets: pd.DataFrame,
                     target: Optional[float] = None,
                     lookback: Optional[int] = None) -> pd.DataFrame:
    """Mod 3. Scale risky exposure to a target vol; cash buffer in IGLS."""
    target = CONFIG_V3["vol_target"] if target is None else target
    lookback = CONFIG_V3["vol_lookback"] if lookback is None else lookback
    cash = CONFIG_V3["cash_asset"]
    idx = _full_index(rets)
    W = pd.DataFrame(0.0, index=idx, columns=rets.columns)
    risky, _ = _risky_cash_split(base)
    risky_tot = sum(risky.values())
    base_port = _portfolio_returns_for_signal(base, rets)

    if target is None:                              # unconstrained = static base
        return _const_weights(base, rets)

    realised = base_port.rolling(lookback).std(ddof=1) * np.sqrt(12)
    realised = realised.shift(1)                    # use t-1
    for t in idx:
        rv = realised.get(t, np.nan)
        if np.isnan(rv) or rv <= 0:
            L = risky_tot                            # warm-up: hold base risky level
        else:
            L = np.clip(target / rv, CONFIG_V3["vol_risky_floor"],
                        CONFIG_V3["vol_risky_cap"])
        for a, w in risky.items():
            W.loc[t, a] = (w / risky_tot) * L
        W.loc[t, cash] = 1.0 - L
    return W


def build_drawdown_control(base: Dict[str, float], rets: pd.DataFrame,
                           t1: Optional[float] = None,
                           t2: Optional[float] = None) -> pd.DataFrame:
    """Mod 4. De-risk on drawdown from the trailing 12m peak."""
    t1 = CONFIG_V3["dd_trig_1"] if t1 is None else t1
    t2 = CONFIG_V3["dd_trig_2"] if t2 is None else t2
    cash = CONFIG_V3["cash_asset"]
    idx = _full_index(rets)
    W = pd.DataFrame(0.0, index=idx, columns=rets.columns)
    risky, _ = _risky_cash_split(base)
    risky_tot = sum(risky.values())
    base_port = _portfolio_returns_for_signal(base, rets)
    nav = (1 + base_port).cumprod()

    mult = 1.0
    for t in idx:
        hist = nav.loc[:t].iloc[:-1]                 # info through t-1
        if len(hist) >= 2:
            peak = hist.iloc[-CONFIG_V3["dd_peak_lb"]:].max()
            dd = hist.iloc[-1] / peak - 1.0
            if dd <= -t2:
                mult = CONFIG_V3["dd_mult_2"]
            elif dd <= -t1:
                mult = CONFIG_V3["dd_mult_1"]
            elif dd >= -CONFIG_V3["dd_restore"]:
                mult = 1.0
            # else: hold prior mult (hysteresis)
        L = risky_tot * mult
        for a, w in risky.items():
            W.loc[t, a] = w * mult
        W.loc[t, cash] = 1.0 - L
    return W


def build_faber(base: Dict[str, float], rets: pd.DataFrame, prices: pd.DataFrame,
                ma: Optional[int] = None) -> pd.DataFrame:
    """Mod 5. Per-asset 10-month MA: hold if price>MA else move sleeve to cash."""
    ma = CONFIG_V3["faber_ma"] if ma is None else ma
    cash = CONFIG_V3["cash_asset"]
    idx = _full_index(rets)
    W = pd.DataFrame(0.0, index=idx, columns=rets.columns)
    risky, _ = _risky_cash_split(base)
    sma = prices.rolling(ma).mean()
    above = (prices > sma).shift(1)                  # signal from t-1

    for t in idx:
        cash_acc = base.get(cash, 0.0)
        for a, w in risky.items():
            on = bool(above.get(a, pd.Series(dtype=float)).get(t, False)) \
                if a in above.columns else False
            if a in above.columns and not np.isnan(sma[a].shift(1).get(t, np.nan)) and on:
                W.loc[t, a] = w
            else:
                cash_acc += w                         # off -> to cash
        W.loc[t, cash] = cash_acc
    return W


def build_xmom(base: Dict[str, float], rets: pd.DataFrame,
               tilt: Optional[float] = None,
               lb: Optional[int] = None) -> pd.DataFrame:
    """Mod 6. Cross-sectional 12-1 momentum: top half +20%, bottom half -20%."""
    tilt = CONFIG_V3["xmom_tilt"] if tilt is None else tilt
    lb = CONFIG_V3["xmom_lb"] if lb is None else lb
    skip = CONFIG_V3["xmom_skip"]
    idx = _full_index(rets)
    W = _const_weights(base, rets)
    held = list(base.keys())
    # 12-1 momentum on price levels: cumret from t-lb to t-skip.
    cum = (1 + rets[held]).cumprod()
    mom = (cum.shift(skip) / cum.shift(lb) - 1.0)    # info through t-1

    for t in idx:
        m = mom.loc[t] if t in mom.index else None
        if m is None or m.isna().any():
            continue
        ranked = m.sort_values(ascending=False)
        n = len(ranked)
        top = set(ranked.index[: n // 2])
        w = pd.Series({a: base[a] * (1 + tilt) if a in top else base[a] * (1 - tilt)
                       for a in held})
        W.loc[t, held] = _normalise(w).values
    return W


def build_tail_hedge(base: Dict[str, float], rets: pd.DataFrame
                     ) -> Tuple[pd.DataFrame, pd.Series]:
    """Mod 7. Add a 3% synthetic tail-hedge sleeve funded pro-rata from equity.

    The hedge is a STYLISED instrument (no real long VIX/put series is reachable
    here), calibrated to the brief's parameters so it cannot flatter itself:

      * convex crash payoff: pays a multiple of the equity loss beyond a -5%
        monthly threshold, and ~0 in calm months (=> ~0 calm correlation, and a
        strongly negative crash correlation with equities);
      * the multiple is scaled so the sleeve's annualised vol ~= 25%;
      * a constant carry is then subtracted so the sleeve's annualised mean
        return = -2.0% (the realistic cost of always-on tail insurance).

    Because the sleeve's *expected* return is negative, this is a drag in normal
    times: the test is whether crisis convexity improves drawdown / Sortino, not
    whether it lifts the headline Sharpe. Returns the weight matrix (with a
    synthetic 'TAIL' column) and the TAIL return series so ``evaluate`` prices it.
    """
    w_tail = CONFIG_V3["tail_weight"]
    idx = _full_index(rets)
    eq_assets = [a for a in base if a in
                 ("VWRP", "VUSA", "GSPX", "VUKE", "VFEM", "WLDS", "IWVL", "IWQU")]
    eq_tot = sum(base[a] for a in eq_assets)
    new_base = dict(base)
    for a in eq_assets:                              # fund from equity pro-rata
        new_base[a] = base[a] * (1 - w_tail / eq_tot) if eq_tot > 0 else base[a]
    new_base["TAIL"] = w_tail

    W = pd.DataFrame(0.0, index=idx, columns=list(rets.columns) + ["TAIL"])
    for a, w in new_base.items():
        W[a] = w

    # Equity-sleeve monthly return (the thing the hedge reacts to).
    eq_ret = (rets[eq_assets] * pd.Series({a: base[a] for a in eq_assets})).sum(axis=1)
    eq_ret = (eq_ret / eq_tot if eq_tot > 0 else eq_ret).reindex(idx).fillna(0.0)
    crash = (-(eq_ret - CONFIG_V3["tail_crash_thresh"])).clip(lower=0.0)
    # Scale convexity to ~25% annualised vol, then offset to -2% annualised mean.
    raw_vol = crash.std(ddof=1) * np.sqrt(12)
    beta = (0.25 / raw_vol) if raw_vol > 0 else 0.0
    raw = beta * crash
    target_mean_m = (1 + CONFIG_V3["tail_carry_ann"]) ** (1 / 12) - 1
    carry = raw.mean() - target_mean_m              # subtract so mean hits -2%/yr
    tail_ret = raw - carry
    return W, tail_ret


# ---------------------------------------------------------------------------
# Dynamic stacks (Mod 8 / Mod 9)
# ---------------------------------------------------------------------------

def build_full_stack(base: Dict[str, float], rets: pd.DataFrame,
                     prices: pd.DataFrame, yld: pd.Series) -> pd.DataFrame:
    """Mod 8. base -> macro-regime tilt -> Faber on/off -> vol-target overall.

    Applied in sequence each month, all signals lagged to t-1.
    """
    cash = CONFIG_V3["cash_asset"]
    idx = _full_index(rets)

    # 1. Macro-regime tilted weights (already a full matrix).
    W_regime = build_macro_regime(base, rets, prices, yld)

    # 2. Faber on/off per asset, applied to the regime weights row by row.
    ma = CONFIG_V3["faber_ma"]
    sma = prices.rolling(ma).mean()
    above = (prices > sma).shift(1)

    # 3. Vol target uses the *base* portfolio realised vol (the exposure dial).
    base_port = _portfolio_returns_for_signal(base, rets)
    realised = (base_port.rolling(CONFIG_V3["vol_lookback"]).std(ddof=1)
                * np.sqrt(12)).shift(1)

    W = pd.DataFrame(0.0, index=idx, columns=rets.columns)
    for t in idx:
        w = W_regime.loc[t].copy()
        # Faber filter: assets below their MA move to cash.
        cash_acc = w[cash]
        for a in rets.columns:
            if a == cash:
                continue
            if w[a] <= 0:
                continue
            on = (a in above.columns
                  and not np.isnan(sma[a].shift(1).get(t, np.nan))
                  and bool(above[a].get(t, False)))
            if not on:
                cash_acc += w[a]
                w[a] = 0.0
        w[cash] = cash_acc
        # Vol target: scale risky block, top up cash.
        rv = realised.get(t, np.nan)
        risky_mask = [a for a in rets.columns if a != cash]
        risky_sum = w[risky_mask].sum()
        if not np.isnan(rv) and rv > 0 and risky_sum > 0:
            L = np.clip(CONFIG_V3["vol_target"] / rv,
                        CONFIG_V3["vol_risky_floor"], CONFIG_V3["vol_risky_cap"])
            scale = min(L, risky_sum) / risky_sum
            for a in risky_mask:
                w[a] *= scale
            w[cash] = 1.0 - w[risky_mask].sum()
        W.loc[t] = w
    return W


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class Strategy:
    name: str
    perf: pd.DataFrame
    weights: pd.DataFrame
    row: Dict = field(default_factory=dict)


def run_all() -> Dict:
    rets, prices, yld = load_panel()
    bl_base = _bl_base_weights()
    v2_base = _v2_base_weights()

    strategies: Dict[str, Strategy] = {}

    def add(name: str, W: pd.DataFrame,
            extra_ret: Optional[Tuple[str, pd.Series]] = None) -> None:
        R = rets
        if extra_ret is not None:
            col, series = extra_ret
            R = rets.copy()
            R[col] = series.reindex(rets.index).fillna(0.0)
        perf = evaluate(W, R)
        strategies[name] = Strategy(name, perf, W, windowed_metrics(perf, name))

    # ---- Benchmarks & bases ------------------------------------------------
    add("Original BL (static)", build_static(bl_base, rets))
    add("v2 base (hedged, static)", build_static(v2_base, rets))
    add("60/40 (VWRP/AGGG)", build_static({"VWRP": 0.60, "AGGG": 0.40}, rets))
    add("100% MSCI ACWI (VWRP)", build_static({"VWRP": 1.0}, rets))

    # ---- Modifications 1-7 (overlays on the original BL base) --------------
    add("Mod 1: Factor timing (V/Q)", build_factor_timing(bl_base, rets, prices))
    add("Mod 2: Macro regime rotation", build_macro_regime(bl_base, rets, prices, yld))
    add("Mod 3: Vol target 10%", build_vol_target(bl_base, rets, 0.10))
    add("Mod 4: Drawdown control", build_drawdown_control(bl_base, rets))
    add("Mod 5: Faber GTAA trend", build_faber(bl_base, rets, prices))
    add("Mod 6: Cross-sec momentum", build_xmom(bl_base, rets))
    W_tail, tail_ret = build_tail_hedge(bl_base, rets)
    add("Mod 7: Tail hedge 3%", W_tail, extra_ret=("TAIL", tail_ret))

    # ---- Mod 8 / Mod 9 stacks ----------------------------------------------
    add("Mod 8: Full dynamic stack", build_full_stack(bl_base, rets, prices, yld))
    add("Mod 9: v2 base + dyn stack", build_full_stack(v2_base, rets, prices, yld))

    return {"strategies": strategies, "rets": rets, "prices": prices,
            "yld": yld, "bl_base": bl_base, "v2_base": v2_base}


# ---------------------------------------------------------------------------
# Robustness / overfitting tests
# ---------------------------------------------------------------------------

def vol_target_grid(bl_base: Dict[str, float], rets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tv in CONFIG_V3["vol_target_grid"]:
        W = build_vol_target(bl_base, rets, tv)
        perf = evaluate(W, rets)
        r = windowed_metrics(perf, f"vol_target={tv if tv else 'uncapped'}")
        rows.append(r)
    return pd.DataFrame(rows)


def robustness_vol_target(bl_base: Dict[str, float],
                          rets: pd.DataFrame) -> pd.DataFrame:
    """+/-20% perturbation of vol target AND lookback for the vol-target rule."""
    rows = []
    base_tv, base_lb = CONFIG_V3["vol_target"], CONFIG_V3["vol_lookback"]
    for tv in [base_tv * 0.8, base_tv, base_tv * 1.2]:
        for lb in sorted({max(2, round(base_lb * 0.8)), base_lb,
                          round(base_lb * 1.2)}):
            W = build_vol_target(bl_base, rets, tv, lb)
            perf = evaluate(W, rets)
            m = windowed_metrics(perf, f"tv={tv:.3f}, lb={lb}")
            rows.append({"vol_target": tv, "lookback": lb,
                         "sharpe_full": m["sharpe_rf4.0"],
                         "sharpe_IS": m["sharpe_IS"],
                         "sharpe_OOS": m["sharpe_OOS"],
                         "max_dd": m["max_drawdown"]})
    return pd.DataFrame(rows)


def null_model_vol_target(bl_base: Dict[str, float], rets: pd.DataFrame,
                          n: int = 200, seed: int = 7) -> pd.DataFrame:
    """Sanity check: replace the realised-vol signal with random exposure draws.

    If random scaling does about as well as the real vol signal, the strategy's
    edge is an artefact of the de-risking *range*, not the signal's information.
    """
    rng = np.random.default_rng(seed)
    cash = CONFIG_V3["cash_asset"]
    risky, _ = _risky_cash_split(bl_base)
    risky_tot = sum(risky.values())
    idx = _full_index(rets)
    sharpes = []
    for _ in range(n):
        L_path = rng.uniform(CONFIG_V3["vol_risky_floor"],
                             CONFIG_V3["vol_risky_cap"], size=len(idx))
        W = pd.DataFrame(0.0, index=idx, columns=rets.columns)
        for k, t in enumerate(idx):
            L = L_path[k]
            for a, w in risky.items():
                W.loc[t, a] = (w / risky_tot) * L
            W.loc[t, cash] = 1.0 - L
        perf = evaluate(W, rets)
        sharpes.append(metrics(perf["net"], perf["gross"],
                               perf["turnover"], "null")["sharpe_rf4.0"])
    return pd.DataFrame({"null_sharpe": sharpes})


# ---------------------------------------------------------------------------
# Monte Carlo (reuse the wealth model) + contribution-rate side calculation
# ---------------------------------------------------------------------------

def mc_for_strategies(labelled: Dict[str, Dict],
                      monthly_override: Optional[float] = None) -> Dict:
    """Run the 45y wealth MC for each (label -> metrics-row), using the
    strategy's realised net annualised return and vol (real GBP inside the MC).
    """
    cf = build_cashflow_schedule()["contribution_gbp"]
    out = {}
    for label, row in labelled.items():
        wealth = simulate_paths(cf, row["ann_return_net"], row["ann_vol"],
                                CONFIG_V3["mc_seed_paths"], CONFIG_V3["mc_seed"],
                                CONFIG_V3["inflation"])
        out[label] = {
            "ages": value_at_ages(wealth, cf.index),
            "targets": probability_of_reaching(wealth, WEALTH_TARGETS),
            "final": wealth[:, -1],
        }
    return out


def contribution_vs_dynamics(v2_row: Dict, best_dyn_row: Dict) -> pd.DataFrame:
    """The meta-question: dynamic alpha vs a higher savings rate.

    Compare terminal real wealth for:
      A. v2 static base + GBP1,600/month
      B. best v3 dynamic + GBP1,600/month
      C. v2 static base + GBP1,800/month (+12.5% contributions)
    """
    base_cf = build_cashflow_schedule()["contribution_gbp"]
    # Scale phase-2 (and phase-1) monthly contributions by 1.125 for case C.
    hi_cf = base_cf.copy()
    hi_cf.iloc[1:] = base_cf.iloc[1:] * 1.125         # leave the t=0 lump sum

    seed, n = CONFIG_V3["mc_seed"], CONFIG_V3["mc_seed_paths"]
    infl = CONFIG_V3["inflation"]
    cases = {
        "A: v2 static + GBP1,600/m": (base_cf, v2_row),
        "B: best v3 dynamic + GBP1,600/m": (base_cf, best_dyn_row),
        "C: v2 static + GBP1,800/m": (hi_cf, v2_row),
    }
    rows = []
    for label, (cf, row) in cases.items():
        wealth = simulate_paths(cf, row["ann_return_net"], row["ann_vol"],
                                n, seed, infl)
        final = wealth[:, -1]
        rows.append({
            "case": label,
            "ann_return_net": row["ann_return_net"],
            "ann_vol": row["ann_vol"],
            "p10_final": np.percentile(final, 10),
            "p50_final": np.percentile(final, 50),
            "p90_final": np.percentile(final, 90),
            "prob_2m": (final >= 2_000_000).mean(),
            "prob_5m": (final >= 5_000_000).mean(),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 160,
                         "axes.titleweight": "bold", "font.family": "DejaVu Sans"})


def _equity_curve(perf: pd.DataFrame) -> pd.Series:
    return (1 + perf["net"]).cumprod()


def plot_equity_curves(strategies: Dict[str, Strategy], names: List[str],
                       path: str) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(13, 7))
    for nm in names:
        ec = _equity_curve(strategies[nm].perf)
        ax.plot(ec.index, ec.values, lw=2, label=nm)
    ax.axvspan(pd.Timestamp(CONFIG_V3["oos_start"]), ec.index[-1],
               color="grey", alpha=0.08, label="Out-of-sample")
    ax.set_yscale("log")
    ax.set_ylabel("Growth of GBP1 (net, log scale)")
    ax.set_title("v3 dynamic strategies — net equity curves (real-data backtest)")
    ax.legend(loc="upper left", fontsize=10, frameon=True)
    ax.text(0.01, -0.12, "Source: sharpe_optimisation_v3.py. Net of 5bps "
            "round-trip costs. Shaded = out-of-sample (2016+).",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_drawdowns(strategies: Dict[str, Strategy], names: List[str],
                   path: str) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(13, 7))
    for nm in names:
        net = strategies[nm].perf["net"]
        nav = (1 + net).cumprod()
        dd = nav / nav.cummax() - 1.0
        ax.plot(dd.index, dd.values, lw=2, label=nm)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
    ax.set_ylabel("Drawdown")
    ax.set_title("v3 dynamic strategies — drawdown profile")
    ax.legend(loc="lower left", fontsize=10, frameon=True)
    ax.text(0.01, -0.12, "Source: sharpe_optimisation_v3.py. Peak-to-trough on "
            "net real-data returns.", transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_rolling_sharpe(strategies: Dict[str, Strategy], names: List[str],
                        path: str, window: int = 36) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(13, 7))
    rf_m = (1 + CONFIG_V3["rf_base"]) ** (1 / 12) - 1
    for nm in names:
        net = strategies[nm].perf["net"]
        roll = ((net - rf_m).rolling(window).mean()
                / net.rolling(window).std(ddof=1)) * np.sqrt(12)
        ax.plot(roll.index, roll.values, lw=2, label=nm)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel(f"{window}m rolling Sharpe (rf=4%)")
    ax.set_title("v3 dynamic strategies — 3-year rolling Sharpe")
    ax.legend(loc="upper left", fontsize=10, frameon=True)
    ax.text(0.01, -0.12, "Source: sharpe_optimisation_v3.py.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_turnover(strategies: Dict[str, Strategy], names: List[str],
                  path: str) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(13, 6.5))
    for nm in names:
        roll = strategies[nm].perf["turnover"].rolling(12).sum()
        ax.plot(roll.index, roll.values, lw=2, label=nm)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
    ax.set_ylabel("Trailing-12m one-way turnover")
    ax.set_title("v3 dynamic strategies — turnover over time")
    ax.legend(loc="upper left", fontsize=10, frameon=True)
    ax.text(0.01, -0.12, "Source: sharpe_optimisation_v3.py.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_mc_fan(mc: Dict, label: str, path: str) -> None:
    _style()
    cf = build_cashflow_schedule()["contribution_gbp"]
    wealth = simulate_paths(cf, mc[label]["_mu"], mc[label]["_vol"],
                            CONFIG_V3["mc_seed_paths"], CONFIG_V3["mc_seed"],
                            CONFIG_V3["inflation"])
    dates = cf.index
    fig, ax = plt.subplots(figsize=(13, 7.5))
    p10, p25, p50, p75, p90 = (np.percentile(wealth, q, axis=0)
                               for q in (10, 25, 50, 75, 90))
    ax.fill_between(dates, p10, p90, alpha=0.20, color="#4C72B0", label="10-90th pct")
    ax.fill_between(dates, p25, p75, alpha=0.35, color="#4C72B0", label="25-75th pct")
    ax.plot(dates, p50, color="#1f3a64", lw=2.5, label="Median")
    ax.plot(dates, cf.cumsum(), color="#C44E52", lw=2, ls="--",
            label="Cumulative contributions")
    ax.set_yscale("log")
    ax.set_title(f"Wealth fan — {label} (real GBP, 10,000 paths)")
    ax.set_xlabel("Year"); ax.set_ylabel("Portfolio value (GBP, May-2026 real)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"GBP{x:,.0f}" if x < 1e6 else f"GBP{x/1e6:.1f}m"))
    ax.legend(loc="upper left", frameon=True)
    ax.text(0.01, -0.12, "Source: sharpe_optimisation_v3.py. MC on realised net "
            "mu/sigma, deflated 2.5%.", transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SUMMARY_COLS = ["strategy", "ann_return_gross", "ann_return_net", "ann_vol",
                "sharpe_rf4.0", "sharpe_rf2.5", "sortino_rf4.0", "calmar",
                "max_drawdown", "ann_turnover", "tc_drag", "hit_rate",
                "sharpe_IS", "sharpe_OOS", "sharpe_gap_OOS_minus_IS"]


def main() -> None:
    rdir = CONFIG_V3["results_dir"]
    os.makedirs(rdir, exist_ok=True)
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)

    art = run_all()
    strategies = art["strategies"]
    rets, prices, bl_base = art["rets"], art["prices"], art["bl_base"]

    print("=" * 120)
    print(f"SHARPE OPTIMISATION v3 — dynamic strategies, walk-forward backtest")
    print(f"Window: {rets.index[0].date()} -> {rets.index[-1].date()}  "
          f"(IS <= {CONFIG_V3['is_end']}, OOS >= {CONFIG_V3['oos_start']})")
    print("=" * 120)

    summary = pd.DataFrame([s.row for s in strategies.values()])[SUMMARY_COLS]
    summary.to_csv(os.path.join(rdir, "v3_summary.csv"), index=False)

    show = summary.copy()
    for c in SUMMARY_COLS[1:]:
        show[c] = summary[c].map(lambda x: f"{x:.4f}" if pd.notna(x) else "—")
    print(show.to_string(index=False))

    # Time-averaged weights per strategy.
    wrows = []
    for s in strategies.values():
        avg = s.weights.mean()
        wrows.append({"strategy": s.name,
                      **{a: avg.get(a, 0.0) for a in
                         list(rets.columns) + ["TAIL"] if a in avg.index}})
    pd.DataFrame(wrows).to_csv(os.path.join(rdir, "v3_avg_weights.csv"), index=False)

    # ---- Robustness + overfitting -----------------------------------------
    grid = vol_target_grid(bl_base, rets)
    grid.to_csv(os.path.join(rdir, "v3_vol_target_grid.csv"), index=False)
    print("\nVol-target grid (the most robust dynamic lever):")
    print(grid[["strategy", "ann_return_net", "ann_vol", "sharpe_rf4.0",
                "sharpe_IS", "sharpe_OOS", "max_drawdown"]].to_string(index=False))

    robust = robustness_vol_target(bl_base, rets)
    robust.to_csv(os.path.join(rdir, "v3_robustness_vol_target.csv"), index=False)
    print("\nRobustness — vol target & lookback +/-20%:")
    print(robust.to_string(index=False))

    nulls = null_model_vol_target(bl_base, rets)
    nulls.to_csv(os.path.join(rdir, "v3_null_model.csv"), index=False)
    real_vt = strategies["Mod 3: Vol target 10%"].row["sharpe_rf4.0"]
    print(f"\nNull-model check (random exposure in [{CONFIG_V3['vol_risky_floor']},"
          f"{CONFIG_V3['vol_risky_cap']}]): real vol-target Sharpe={real_vt:.4f} vs "
          f"null mean={nulls['null_sharpe'].mean():.4f} "
          f"(pctile={ (nulls['null_sharpe'] < real_vt).mean()*100:.0f}%)")

    # ---- Identify best OOS dynamic strategy --------------------------------
    mods = {k: v for k, v in strategies.items() if k.startswith("Mod")}
    best_oos = max(mods.values(), key=lambda s: s.row["sharpe_OOS"])
    print(f"\nBest dynamic by OOS Sharpe: {best_oos.name} "
          f"(OOS={best_oos.row['sharpe_OOS']:.4f}, full={best_oos.row['sharpe_rf4.0']:.4f})")

    # ---- Monte Carlo: v2 base, best dynamic, original BL -------------------
    mc_rows = {
        "Original BL (static)": strategies["Original BL (static)"].row,
        "v2 base (hedged, static)": strategies["v2 base (hedged, static)"].row,
        best_oos.name: best_oos.row,
        "Mod 8: Full dynamic stack": strategies["Mod 8: Full dynamic stack"].row,
    }
    mc = mc_for_strategies(mc_rows)
    for label, res in mc.items():
        res["_mu"] = mc_rows[label]["ann_return_net"]
        res["_vol"] = mc_rows[label]["ann_vol"]
        safe = (label.replace(" ", "_").replace("(", "").replace(")", "")
                .replace(":", "").replace("+", "").replace("%", "")
                .replace("/", "").replace(",", "").replace(".", ""))
        res["ages"].to_csv(os.path.join(rdir, f"v3_mc_{safe}_by_age.csv"), index=False)
        res["targets"].to_csv(os.path.join(rdir, f"v3_mc_{safe}_targets.csv"), index=False)

    print("\nMonte Carlo terminal wealth (age 70, real GBP):")
    for label, res in mc.items():
        a = res["ages"].iloc[-1]
        tg = res["targets"].set_index("target_gbp_real")["prob_at_end"]
        print(f"  {label:34s} p10=GBP{a['p10']:,.0f}  p50=GBP{a['p50']:,.0f}  "
              f"p90=GBP{a['p90']:,.0f}  P(2m end)={tg.get(2_000_000,0):.1%}")

    # ---- Contribution-rate side calculation --------------------------------
    side = contribution_vs_dynamics(strategies["v2 base (hedged, static)"].row,
                                    best_oos.row)
    side.to_csv(os.path.join(rdir, "v3_contribution_vs_dynamics.csv"), index=False)
    print("\nDynamics vs a higher savings rate (terminal real GBP):")
    print(side.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))

    # ---- Charts ------------------------------------------------------------
    headline = ["v2 base (hedged, static)", "Mod 3: Vol target 10%",
                "Mod 5: Faber GTAA trend", "Mod 8: Full dynamic stack",
                "100% MSCI ACWI (VWRP)"]
    plot_equity_curves(strategies, headline, os.path.join(rdir, "v3_equity_curves.png"))
    plot_drawdowns(strategies, headline, os.path.join(rdir, "v3_drawdowns.png"))
    plot_rolling_sharpe(strategies, headline, os.path.join(rdir, "v3_rolling_sharpe.png"))
    plot_turnover(strategies, ["Mod 1: Factor timing (V/Q)",
                               "Mod 2: Macro regime rotation",
                               "Mod 5: Faber GTAA trend",
                               "Mod 6: Cross-sec momentum",
                               "Mod 8: Full dynamic stack"],
                  os.path.join(rdir, "v3_turnover.png"))
    plot_mc_fan(mc, "Mod 8: Full dynamic stack",
                os.path.join(rdir, "v3_mc_fan_full_stack.png"))

    print(f"\nAll v3 outputs written to {rdir}")


if __name__ == "__main__":
    main()
