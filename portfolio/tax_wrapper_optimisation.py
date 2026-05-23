"""Tax-wrapper optimisation for Adam's long-term savings (v4).

Where should each marginal pound of saving go — ISA, SIPP, LISA, or GIA —
to maximise terminal *real* wealth net of all taxes?

v1-v3 optimised the *portfolio* inside a single wrapper. v4 holds the
portfolio constant (v2's BL-with-hedged-US sleeve: 8.16% nominal gross,
11.79% vol, 0.24% TER) and optimises the *wrapper allocation* of a
career-aware saving stream.

Run end-to-end with:

    python portfolio/tax_wrapper_optimisation.py

Outputs CSVs to portfolio/results/v4_*.csv and charts to the same folder.

Tax rules are 2026/27 (verified via web search May 2026); every
date-sensitive assumption is flagged in docs/tax_wrapper_optimisation.md.
This is personal-planning analysis, not regulated financial advice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


# ---------------------------------------------------------------------------
# CONFIG — every magic number lives here
# ---------------------------------------------------------------------------

CONFIG: Dict = {
    # --- Portfolio (held constant across all wrapper variants) -------------
    # v2 "BL with hedged US" full-stack: 8.16% nominal gross E[r], 0.24% TER.
    "gross_nominal_return": 0.0816,
    "ter": 0.0024,
    "volatility": 0.1179,
    "inflation": 0.025,

    # --- Timeline ----------------------------------------------------------
    "start_date": pd.Timestamp("2026-05-01"),
    "horizon_years": 45,                      # to age 70 (≈ 2071)
    "birth_year": 2001,                       # Adam is 25 in May 2026
    "initial_lump_sum": 3_001.0,              # current Trading 212 ISA balance
    "career_start": pd.Timestamp("2027-09-01"),  # M&G graduate role begins

    # --- Saving capacity ---------------------------------------------------
    "saving_rate": 0.25,                      # save 25% of gross income
    "status_quo_monthly": 1_600.0,            # literal current plan (all-ISA)

    # Income trajectory anchors (age, gross GBP, REAL 2026 terms).
    # Brief's central case. M&G's published grad salary is lower (~£35-46k);
    # that is captured by the -25% income sensitivity (see income_path()).
    "income_anchors": [
        (26, 55_000.0), (29, 80_000.0), (32, 110_000.0),
        (35, 150_000.0), (40, 200_000.0), (70, 200_000.0),
    ],

    # --- 2026/27 tax parameters (England) ----------------------------------
    "personal_allowance": 12_570.0,
    "pa_taper_start": 100_000.0,              # £1 PA lost per £2 over this
    "basic_rate_limit": 50_270.0,             # 20% up to here
    "additional_rate_threshold": 125_140.0,   # 45% above here
    "rate_basic": 0.20,
    "rate_higher": 0.40,
    "rate_additional": 0.45,
    "ni_primary_threshold": 12_570.0,
    "ni_upper_limit": 50_270.0,
    "ni_main_rate": 0.08,                     # employee Class 1 2026/27
    "ni_upper_rate": 0.02,

    # --- Wrapper allowances & mechanics ------------------------------------
    "isa_limit": 20_000.0,
    "lisa_limit": 4_000.0,                    # counts within the ISA limit
    "lisa_bonus": 0.25,
    "lisa_open_max_age": 40,
    "lisa_contrib_max_age": 50,
    "lisa_penalty_free_age": 60,
    "lisa_property_cap": 450_000.0,
    "sipp_annual_allowance": 60_000.0,
    "sipp_access_age": 57,                    # from April 2028
    "sipp_taxfree_fraction": 0.25,            # 25% pension commencement lump sum
    "sipp_retirement_tax_rate": 0.20,         # assumed effective rate in drawdown

    # --- GIA tax (overflow account) ----------------------------------------
    "gia_dividend_drag": 0.005,               # ~0.5%/yr dividend tax leakage
    "gia_cgt_effective": 0.20,                # effective CGT on terminal gain

    # --- House purchase -----------------------------------------------------
    "house_age": 30,
    "house_deposit": 50_000.0,                # real GBP, central case
    "house_deposit_scenarios": [40_000.0, 50_000.0, 60_000.0],
    "mortgage_monthly_drag": 800.0,           # Mod 4: capacity hit, age 30-55
    "mortgage_years": 25,

    # --- Monte Carlo --------------------------------------------------------
    "n_paths": 10_000,
    "seed": 42,

    "results_dir": os.path.join(os.path.dirname(__file__), "results"),
}

AGE_CHECKPOINTS = (30, 40, 50, 60, 70)
WEALTH_TARGETS = (1_000_000, 2_000_000, 5_000_000)


# ---------------------------------------------------------------------------
# Tax & income functions
# ---------------------------------------------------------------------------

def personal_allowance(gross: float) -> float:
    """Personal allowance after the £1-per-£2 taper above £100k."""
    pa = CONFIG["personal_allowance"]
    taper_start = CONFIG["pa_taper_start"]
    if gross <= taper_start:
        return pa
    return max(0.0, pa - (gross - taper_start) / 2.0)


def income_tax(gross: float) -> float:
    """Annual income tax on an employment income (England, 2026/27)."""
    pa = personal_allowance(gross)
    basic_limit = CONFIG["basic_rate_limit"]
    add_threshold = CONFIG["additional_rate_threshold"]

    tax = 0.0
    # Additional rate band (above £125,140; PA already zero here).
    if gross > add_threshold:
        tax += (gross - add_threshold) * CONFIG["rate_additional"]
        gross_h = add_threshold
    else:
        gross_h = gross
    # Higher rate band (PA..basic_limit boundary up to add_threshold).
    higher_floor = basic_limit
    if gross_h > higher_floor:
        tax += (gross_h - higher_floor) * CONFIG["rate_higher"]
        gross_b = higher_floor
    else:
        gross_b = gross_h
    # Basic rate band (taxable income above PA up to basic_limit).
    taxable_basic = max(0.0, gross_b - pa)
    tax += taxable_basic * CONFIG["rate_basic"]
    return tax


def national_insurance(gross: float) -> float:
    """Employee Class 1 NI (2026/27): 8% PT->UEL, 2% above."""
    pt = CONFIG["ni_primary_threshold"]
    uel = CONFIG["ni_upper_limit"]
    if gross <= pt:
        return 0.0
    main = (min(gross, uel) - pt) * CONFIG["ni_main_rate"]
    upper = max(0.0, gross - uel) * CONFIG["ni_upper_rate"]
    return main + upper


def net_income(gross: float) -> float:
    """Take-home pay after income tax and employee NI."""
    return gross - income_tax(gross) - national_insurance(gross)


def marginal_income_tax_rate(gross: float) -> float:
    """Marginal *income-tax* rate used for SIPP relief.

    Returns 0.60 inside the £100k-£125,140 personal-allowance taper, where
    each extra pound of income costs 40% tax plus 20% of restored PA.
    """
    if gross <= CONFIG["personal_allowance"]:
        return 0.0
    if gross <= CONFIG["basic_rate_limit"]:
        return CONFIG["rate_basic"]
    if gross <= CONFIG["pa_taper_start"]:
        return CONFIG["rate_higher"]
    if gross <= CONFIG["additional_rate_threshold"]:
        return 0.60                            # PA taper trap
    return CONFIG["rate_additional"]


# ---------------------------------------------------------------------------
# Income trajectory
# ---------------------------------------------------------------------------

def income_path(age: float, scale: float = 1.0,
                anchors: List[Tuple[float, float]] | None = None) -> float:
    """Real gross income at a given age. Zero before the graduate role.

    `scale` applies the ±25% income sensitivity; `anchors` overrides the
    trajectory for the stress tests (career stall / acceleration).
    """
    if age < 26:
        return 0.0
    pts = anchors if anchors is not None else CONFIG["income_anchors"]
    ages = [a for a, _ in pts]
    vals = [v for _, v in pts]
    return float(np.interp(age, ages, vals)) * scale


# ---------------------------------------------------------------------------
# Wrapper economics: per-£ terminal multipliers (growth-neutral)
# ---------------------------------------------------------------------------

def sipp_multiplier(marginal_rate: float, taxfree_frac: float | None = None,
                    relief_cap: float | None = None,
                    ret_rate: float | None = None) -> float:
    """Net terminal £ per £1 of take-home cost directed to a SIPP.

    £1 net cost grosses up to 1/(1-m) in the pot (higher-rate relief assumed
    recycled into savings). At withdrawal: `taxfree_frac` is tax-free, the
    rest taxed at `ret_rate`.  `relief_cap` caps the relief rate (Mod 6e).
    """
    m = marginal_rate
    if relief_cap is not None:
        m = min(m, relief_cap)
    tf = CONFIG["sipp_taxfree_fraction"] if taxfree_frac is None else taxfree_frac
    rr = CONFIG["sipp_retirement_tax_rate"] if ret_rate is None else ret_rate
    grossup = 1.0 / (1.0 - m) if m < 1.0 else np.inf
    withdrawal_factor = tf + (1.0 - tf) * (1.0 - rr)
    return grossup * withdrawal_factor


LISA_MULTIPLIER = 1.0 + CONFIG["lisa_bonus"]          # 1.25, tax-free out
ISA_MULTIPLIER = 1.0


# ---------------------------------------------------------------------------
# Allocation policies
# ---------------------------------------------------------------------------
# Each policy maps (age, gross_income, net_saving S) -> dict of NET (take-home)
# pounds directed to each wrapper. SIPP/LISA are grossed up downstream.
# Constraint: isa_net + lisa_net <= isa_limit; lisa_net <= lisa_limit;
#             sipp gross (= sipp_net/(1-m)) <= sipp_allowance & <= earnings.

def _cap_sipp_net(sipp_net: float, m: float, gross: float) -> float:
    """Clip SIPP net cost so gross contribution respects allowance/earnings."""
    if m >= 1.0:
        return 0.0
    max_gross = min(CONFIG["sipp_annual_allowance"], max(gross, 0.0))
    return min(sipp_net, max_gross * (1.0 - m))


def policy_all_isa(age, gross, S, **kw):
    """Status-quo career-aware baseline: ISA first, GIA overflow."""
    isa = min(S, CONFIG["isa_limit"])
    return {"isa": isa, "lisa": 0.0, "sipp": 0.0, "gia": S - isa}


def policy_lisa_priority(age, gross, S, **kw):
    """Mod 2: £4k LISA first, then ISA to the limit, GIA overflow."""
    lisa = min(CONFIG["lisa_limit"], S) if age <= CONFIG["lisa_contrib_max_age"] else 0.0
    isa = min(S - lisa, CONFIG["isa_limit"] - lisa)
    gia = S - lisa - isa
    return {"isa": isa, "lisa": lisa, "sipp": 0.0, "gia": gia}


def policy_sipp_max(age, gross, S, **kw):
    """Mod 3: rate-driven SIPP maximisation.

    Higher rate (40%): SIPP gross up to £20k/yr, then ISA, then GIA.
    Additional/taper (>=45%): SIPP gross up to £40k/yr, then ISA, LISA, GIA.
    Basic rate (20%): ISA priority, no SIPP (only 6.25% better but locks money).
    """
    m = marginal_income_tax_rate(gross)
    sipp = 0.0
    if m >= CONFIG["rate_additional"]:
        sipp = _cap_sipp_net(min(S, 40_000.0 * (1 - m)), m, gross)
    elif m >= CONFIG["rate_higher"]:
        sipp = _cap_sipp_net(min(S, 20_000.0 * (1 - m)), m, gross)
    rem = S - sipp
    isa = min(rem, CONFIG["isa_limit"])
    gia = rem - isa
    return {"isa": isa, "lisa": 0.0, "sipp": sipp, "gia": gia}


def policy_lisa_plus_sipp(age, gross, S, **kw):
    """Mod 4: LISA (house + retirement) + SIPP for relief + ISA bridge."""
    m = marginal_income_tax_rate(gross)
    lisa = min(CONFIG["lisa_limit"], S) if age <= CONFIG["lisa_contrib_max_age"] else 0.0
    rem = S - lisa
    sipp = 0.0
    if m >= CONFIG["rate_additional"]:
        sipp = _cap_sipp_net(min(rem, 40_000.0 * (1 - m)), m, gross)
    elif m >= CONFIG["rate_higher"]:
        sipp = _cap_sipp_net(min(rem, 20_000.0 * (1 - m)), m, gross)
    rem -= sipp
    isa = min(rem, CONFIG["isa_limit"] - lisa)
    gia = rem - isa
    return {"isa": isa, "lisa": lisa, "sipp": sipp, "gia": gia}


def policy_lifecycle_optimum(age, gross, S, house_reserve_annual=0.0,
                             need_house=True, **kw):
    """Mod 5: greedy by after-tax multiplier, reserving accessible house funds.

    Pre-house (age<30) and if a deposit is needed, first route up to
    `house_reserve_annual` into LISA (first-home eligible, +25%) then ISA
    (both accessible). The surplus is allocated by multiplier priority:
    SIPP (if >=higher rate) > LISA (to age 50) > ISA > GIA.
    """
    m = marginal_income_tax_rate(gross)
    alloc = {"isa": 0.0, "lisa": 0.0, "sipp": 0.0, "gia": 0.0}
    remaining = S
    lisa_room = CONFIG["lisa_limit"] if age <= CONFIG["lisa_contrib_max_age"] else 0.0
    isa_room = CONFIG["isa_limit"]

    # 1. Reserve accessible funds for the house deposit (LISA then ISA).
    if need_house and age < CONFIG["house_age"] and house_reserve_annual > 0:
        reserve = min(house_reserve_annual, remaining)
        to_lisa = min(reserve, lisa_room)
        alloc["lisa"] += to_lisa; lisa_room -= to_lisa; remaining -= to_lisa
        reserve -= to_lisa
        to_isa = min(reserve, isa_room - alloc["lisa"])
        alloc["isa"] += to_isa; isa_room -= to_isa; remaining -= to_isa

    # 2. Surplus by multiplier priority.
    sipp_better_than_lisa = sipp_multiplier(m) > LISA_MULTIPLIER
    if m >= CONFIG["rate_higher"] and sipp_better_than_lisa:
        target_gross = 40_000.0 if m >= CONFIG["rate_additional"] else 40_000.0
        sipp = _cap_sipp_net(min(remaining, target_gross * (1 - m)), m, gross)
        alloc["sipp"] += sipp; remaining -= sipp
    # LISA next (still beats ISA, and beats basic-rate SIPP).
    to_lisa = min(remaining, lisa_room, CONFIG["isa_limit"] - alloc["isa"] - alloc["lisa"])
    to_lisa = max(0.0, to_lisa)
    alloc["lisa"] += to_lisa; remaining -= to_lisa
    # ISA next.
    to_isa = min(remaining, CONFIG["isa_limit"] - alloc["isa"] - alloc["lisa"])
    to_isa = max(0.0, to_isa)
    alloc["isa"] += to_isa; remaining -= to_isa
    # GIA overflow.
    alloc["gia"] += max(0.0, remaining)
    return alloc


# ---------------------------------------------------------------------------
# Contribution-schedule builder (deterministic; path-independent)
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    """A wrapper-allocation strategy to simulate."""
    name: str
    policy: Callable
    saving: str = "career"           # "career" (25% of income) or "fixed"
    need_house: bool = False
    house_deposit: float = 0.0
    income_scale: float = 1.0
    income_anchors: List[Tuple[float, float]] | None = None
    mortgage: bool = False
    early_retire: bool = False       # Mod 6c: £40k/yr from 50 to 57
    sipp_taxfree_frac: float = field(default_factory=lambda: CONFIG["sipp_taxfree_fraction"])
    sipp_relief_cap: float | None = None
    house_reserve_annual: float = 0.0


def build_schedule(v: Variant) -> pd.DataFrame:
    """Monthly GROSS-into-pot contributions per wrapper, plus bookkeeping.

    Returns a dataframe indexed by month with columns:
    c_isa, c_lisa, c_sipp, c_gia (pot inflows), and the annual relief/bonus.
    """
    months = pd.date_range(
        CONFIG["start_date"],
        CONFIG["start_date"] + pd.DateOffset(years=CONFIG["horizon_years"]),
        freq="MS")
    cols = ["c_isa", "c_lisa", "c_sipp", "c_gia",
            "net_isa", "net_lisa", "net_sipp", "net_gia",
            "sipp_relief", "lisa_bonus"]
    df = pd.DataFrame(0.0, index=months, columns=cols)

    # Initial lump sum into the ISA (current Trading 212 balance).
    df.loc[months[0], "c_isa"] += CONFIG["initial_lump_sum"]
    df.loc[months[0], "net_isa"] += CONFIG["initial_lump_sum"]

    mort_end_age = CONFIG["house_age"] + CONFIG["mortgage_years"]

    for ts in months:
        age = ts.year + (ts.month - 1) / 12.0 - CONFIG["birth_year"]

        if v.saving == "fixed":
            # Literal status-quo plan: flat £1,600/m, all ISA, from career start.
            if ts < CONFIG["career_start"]:
                continue
            S = CONFIG["status_quo_monthly"]
            alloc = {"isa": min(S, CONFIG["isa_limit"] / 12.0),
                     "lisa": 0.0, "sipp": 0.0,
                     "gia": max(0.0, S - CONFIG["isa_limit"] / 12.0)}
            gross = 0.0
        else:
            if ts < CONFIG["career_start"]:
                continue
            gross = income_path(age, v.income_scale, v.income_anchors)
            if gross <= 0:
                continue
            S_annual = CONFIG["saving_rate"] * gross
            if v.mortgage and CONFIG["house_age"] <= age < mort_end_age:
                S_annual = max(0.0, S_annual - CONFIG["mortgage_monthly_drag"] * 12.0)
            # Policies allocate in ANNUAL terms (caps are annual); spread to months.
            alloc_annual = v.policy(age, gross, S_annual,
                                    house_reserve_annual=v.house_reserve_annual,
                                    need_house=v.need_house)
            alloc = {k: val / 12.0 for k, val in alloc_annual.items()}

        m = marginal_income_tax_rate(gross) if gross > 0 else 0.0
        if v.sipp_relief_cap is not None:
            m = min(m, v.sipp_relief_cap)

        # Net amounts (take-home cost).
        df.loc[ts, "net_isa"] += alloc["isa"]
        df.loc[ts, "net_lisa"] += alloc["lisa"]
        df.loc[ts, "net_sipp"] += alloc["sipp"]
        df.loc[ts, "net_gia"] += alloc["gia"]

        # Pot inflows (grossed up).
        df.loc[ts, "c_isa"] += alloc["isa"]
        df.loc[ts, "c_gia"] += alloc["gia"]
        lisa_pot = alloc["lisa"] * (1.0 + CONFIG["lisa_bonus"])
        df.loc[ts, "c_lisa"] += lisa_pot
        df.loc[ts, "lisa_bonus"] += alloc["lisa"] * CONFIG["lisa_bonus"]
        sipp_pot = alloc["sipp"] / (1.0 - m) if m < 1.0 else 0.0
        df.loc[ts, "c_sipp"] += sipp_pot
        df.loc[ts, "sipp_relief"] += sipp_pot - alloc["sipp"]

    return df


# ---------------------------------------------------------------------------
# Monte Carlo engine
# ---------------------------------------------------------------------------

def generate_returns() -> Tuple[np.ndarray, pd.DatetimeIndex]:
    """Common monthly REAL return draws reused across all variants (CRN)."""
    months = pd.date_range(
        CONFIG["start_date"],
        CONFIG["start_date"] + pd.DateOffset(years=CONFIG["horizon_years"]),
        freq="MS")
    n_months = len(months)
    net_nominal = CONFIG["gross_nominal_return"] - CONFIG["ter"]
    mu_real = (1 + net_nominal) / (1 + CONFIG["inflation"]) - 1
    mu_m = (1 + mu_real) ** (1 / 12) - 1
    sig_m = CONFIG["volatility"] / np.sqrt(12)
    rng = np.random.default_rng(CONFIG["seed"])
    log_mu = np.log(1 + mu_m) - 0.5 * sig_m ** 2
    log_rets = rng.normal(log_mu, sig_m, size=(CONFIG["n_paths"], n_months))
    return np.exp(log_rets) - 1.0, months


@dataclass
class SimResult:
    name: str
    terminal_total: np.ndarray                 # net real wealth at 70, per path
    by_age_net: Dict[int, Dict[str, float]]    # median net wealth per wrapper
    by_age_total_pct: Dict[int, Dict[str, float]]  # p10/p50/p90 total
    relief_total: float
    bonus_total: float
    sipp_tax_paid: float
    gia_tax_paid: float
    accessible_pre57_pct: float
    house_feasible: float                      # P(accessible >= deposit at 30)
    house_shortfall_paths: float
    income_at_60_4pct: float


def simulate(v: Variant, returns: np.ndarray, months: pd.DatetimeIndex) -> SimResult:
    """Forward Monte Carlo of the four wrapper balances for one variant."""
    sched = build_schedule(v)
    c_isa = sched["c_isa"].values
    c_lisa = sched["c_lisa"].values
    c_sipp = sched["c_sipp"].values
    c_gia = sched["c_gia"].values

    n_paths = returns.shape[0]
    isa = np.zeros(n_paths)
    lisa = np.zeros(n_paths)
    sipp = np.zeros(n_paths)
    gia = np.zeros(n_paths)
    gia_basis = np.zeros(n_paths)

    ages = np.array([ts.year + (ts.month - 1) / 12.0 - CONFIG["birth_year"]
                     for ts in months])
    house_month = int(np.argmin(np.abs(ages - CONFIG["house_age"])))
    gia_drag_m = CONFIG["gia_dividend_drag"] / 12.0

    # Early-retirement bridge withdrawals (Mod 6c): £40k/yr, age 50-57, from ISA/GIA.
    er_months = set()
    if v.early_retire:
        er_months = {i for i, a in enumerate(ages)
                     if CONFIG["sipp_access_age"] > a >= 50}
    er_monthly = 40_000.0 / 12.0

    checkpoints = {age: int(np.argmin(np.abs(ages - age))) for age in AGE_CHECKPOINTS}
    by_age_net: Dict[int, Dict[str, float]] = {}
    by_age_total_pct: Dict[int, Dict[str, float]] = {}
    house_shortfall = np.zeros(n_paths)

    tf = v.sipp_taxfree_frac
    rr = CONFIG["sipp_retirement_tax_rate"]
    cgt = CONFIG["gia_cgt_effective"]

    def net_balances():
        """Net (after eventual tax) wrapper balances at a checkpoint."""
        sipp_net = sipp * (tf + (1 - tf) * (1 - rr))
        gia_net = gia - cgt * np.maximum(0.0, gia - gia_basis)
        return isa.copy(), lisa.copy(), sipp_net, gia_net

    def draw_basis(take):
        """Proportionally reduce GIA cost basis when GIA is drawn down."""
        return np.divide(take, gia, out=np.zeros_like(gia), where=gia > 0)

    house_accessible_at_30 = None

    for t in range(len(months)):
        r = returns[:, t]
        isa = isa * (1 + r) + c_isa[t]
        lisa = lisa * (1 + r) + c_lisa[t]
        sipp = sipp * (1 + r) + c_sipp[t]
        gia = gia * (1 + r - gia_drag_m) + c_gia[t]
        gia_basis += c_gia[t]

        # First-home accessible wealth (ISA + LISA + GIA), measured pre-withdrawal.
        if t == checkpoints[30]:
            house_accessible_at_30 = isa + lisa + gia

        if t == house_month and v.need_house and v.house_deposit > 0:
            need = np.full(n_paths, v.house_deposit)
            take = np.minimum(need, lisa); lisa -= take; need -= take
            take = np.minimum(need, isa); isa -= take; need -= take
            take = np.minimum(need, gia)
            gia_basis *= (1 - draw_basis(take))
            gia -= take; need -= take
            house_shortfall = need

        if t in er_months:
            need = np.full(n_paths, er_monthly)
            take = np.minimum(need, isa); isa -= take; need -= take
            take = np.minimum(need, gia)
            gia_basis *= (1 - draw_basis(take))
            gia -= take

        for age, idx in checkpoints.items():
            if t == idx:
                ib, lb, sb, gb = net_balances()
                by_age_net[age] = {
                    "isa": float(np.median(ib)), "lisa": float(np.median(lb)),
                    "sipp": float(np.median(sb)), "gia": float(np.median(gb)),
                }
                total = ib + lb + sb + gb
                by_age_total_pct[age] = {
                    "p10": float(np.percentile(total, 10)),
                    "p50": float(np.percentile(total, 50)),
                    "p90": float(np.percentile(total, 90)),
                }

    # Terminal net wealth at 70.
    ib, lb, sb, gb = net_balances()
    terminal_total = ib + lb + sb + gb

    # Deterministic life-time relief & bonus.
    relief_total = float(sched["sipp_relief"].sum())
    bonus_total = float(sched["lisa_bonus"].sum())

    # Withdrawal taxes (median, path-dependent on terminal pots).
    sipp_tax_paid = float(np.median(sipp * (1 - tf) * rr))
    gia_tax_paid = float(np.median(cgt * np.maximum(0.0, gia - gia_basis)))

    # Access flexibility: % of net wealth accessible before age 57 (ISA+GIA).
    pre57 = by_age_net.get(50, {})
    accessible = pre57.get("isa", 0) + pre57.get("gia", 0)
    total50 = sum(pre57.values()) or 1.0
    accessible_pct = accessible / total50

    # House feasibility: can the £50k deposit be met from accessible wealth
    # (ISA + LISA-for-first-home + GIA) at age 30? Tested for every variant.
    deposit = v.house_deposit if v.house_deposit > 0 else CONFIG["house_deposit"]
    if house_accessible_at_30 is not None:
        house_feasible = float((house_accessible_at_30 >= deposit).mean())
        shortfall = float((house_shortfall > 1.0).mean())
    else:
        house_feasible = float("nan")
        shortfall = float("nan")

    income_60 = 0.04 * by_age_total_pct.get(60, {}).get("p50", 0.0)

    return SimResult(
        name=v.name, terminal_total=terminal_total, by_age_net=by_age_net,
        by_age_total_pct=by_age_total_pct, relief_total=relief_total,
        bonus_total=bonus_total, sipp_tax_paid=sipp_tax_paid,
        gia_tax_paid=gia_tax_paid, accessible_pre57_pct=accessible_pct,
        house_feasible=house_feasible, house_shortfall_paths=shortfall,
        income_at_60_4pct=income_60)


# ---------------------------------------------------------------------------
# House-deposit reserve calibration for Mod 5
# ---------------------------------------------------------------------------

def calibrate_house_reserve(deposit: float) -> float:
    """Annual accessible £ to set aside before age 30 to hit the deposit.

    Deliberately conservative: target the deposit on *contributions alone*
    (zero assumed growth) over the actual contributing window from the
    graduate start to age 30. Investment growth and the LISA 25% bonus then
    act as a safety buffer, lifting the probability of success well above 50%
    rather than the ~50% a median-growth target would give.
    """
    start_age = CONFIG["career_start"].year + (CONFIG["career_start"].month - 1) / 12 \
        - CONFIG["birth_year"]
    years = max(0.5, CONFIG["house_age"] - start_age)   # ~3.7 years
    return deposit / years


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def results_row(res: SimResult) -> Dict:
    p = lambda q: float(np.percentile(res.terminal_total, q))
    return {
        "variant": res.name,
        "terminal_p10": p(10), "terminal_p50": p(50), "terminal_p90": p(90),
        "wealth_at_60_p50": res.by_age_total_pct.get(60, {}).get("p50", np.nan),
        "wealth_at_30_p50": res.by_age_total_pct.get(30, {}).get("p50", np.nan),
        "cumulative_relief": res.relief_total,
        "lisa_bonus_total": res.bonus_total,
        "sipp_tax_paid": res.sipp_tax_paid,
        "gia_tax_paid": res.gia_tax_paid,
        "accessible_pre57_pct": res.accessible_pre57_pct,
        "house_feasible_prob": res.house_feasible,
        "retirement_income_60_4pct": res.income_at_60_4pct,
    }


def _setup_style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 160,
                         "axes.titleweight": "bold", "font.family": "DejaVu Sans"})


def _gbp(x, _=None):
    return f"£{x:,.0f}" if x < 1e6 else f"£{x/1e6:.1f}m"


def plot_wealth_trajectory(results: Dict[str, SimResult], path: str) -> None:
    _setup_style()
    fig, ax = plt.subplots(figsize=(13, 7.5))
    ages = list(AGE_CHECKPOINTS)
    for name, res in results.items():
        med = [res.by_age_total_pct.get(a, {}).get("p50", np.nan) for a in ages]
        ax.plot(ages, med, marker="o", lw=2.3, label=name)
    ax.set_yscale("log")
    ax.set_title("Net real wealth by age — wrapper strategies (median, 10k paths)")
    ax.set_xlabel("Age")
    ax.set_ylabel("Net wealth (GBP, May-2026 real)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_gbp))
    ax.legend(loc="upper left", frameon=True, fontsize=11)
    ax.text(0.01, -0.12,
            "Source: tax_wrapper_optimisation.py. SIPP shown net of 20% "
            "retirement tax (25% tax-free). Portfolio: 8.16% nominal gross, 11.79% vol.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_wrapper_allocation(res: SimResult, path: str) -> None:
    _setup_style()
    fig, ax = plt.subplots(figsize=(13, 7.5))
    ages = list(AGE_CHECKPOINTS)
    wrappers = ["isa", "lisa", "sipp", "gia"]
    labels = {"isa": "ISA", "lisa": "LISA", "sipp": "SIPP (net)", "gia": "GIA (net)"}
    colours = {"isa": "#4C72B0", "lisa": "#55A868", "sipp": "#C44E52", "gia": "#8172B3"}
    data = {w: [res.by_age_net.get(a, {}).get(w, 0.0) for a in ages] for w in wrappers}
    bottom = np.zeros(len(ages))
    for w in wrappers:
        ax.bar(ages, data[w], bottom=bottom, label=labels[w], color=colours[w], width=4)
        bottom += np.array(data[w])
    ax.set_title(f"Wrapper split of net wealth by age — {res.name} (median)")
    ax.set_xlabel("Age"); ax.set_ylabel("Net wealth (GBP, May-2026 real)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_gbp))
    ax.legend(loc="upper left", frameon=True)
    ax.text(0.01, -0.12,
            "Source: tax_wrapper_optimisation.py. SIPP/GIA shown net of "
            "eventual tax. SIPP inaccessible before 57; LISA before 60.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_tax_relief(sched: pd.DataFrame, path: str) -> None:
    _setup_style()
    fig, ax = plt.subplots(figsize=(13, 7.5))
    annual = sched[["sipp_relief", "lisa_bonus"]].groupby(sched.index.year).sum()
    annual["age"] = annual.index - CONFIG["birth_year"]
    ax.bar(annual["age"], annual["sipp_relief"], label="SIPP tax relief",
           color="#C44E52", width=0.8)
    ax.bar(annual["age"], annual["lisa_bonus"], bottom=annual["sipp_relief"],
           label="LISA government bonus", color="#55A868", width=0.8)
    ax.set_title("Government top-ups per year — lifecycle optimum (Mod 5)")
    ax.set_xlabel("Age"); ax.set_ylabel("Free money per year (GBP, real)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
    ax.legend(loc="upper left", frameon=True)
    ax.text(0.01, -0.12,
            "Source: tax_wrapper_optimisation.py. SIPP relief assumes "
            "higher-rate relief recycled into savings.",
            transform=ax.transAxes, fontsize=9, color="gray")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def plot_terminal_comparison(rows: List[Dict], path: str) -> None:
    _setup_style()
    fig, ax = plt.subplots(figsize=(13, 7.5))
    names = [r["variant"] for r in rows]
    p50 = [r["terminal_p50"] for r in rows]
    lo = [r["terminal_p50"] - r["terminal_p10"] for r in rows]
    hi = [r["terminal_p90"] - r["terminal_p50"] for r in rows]
    y = np.arange(len(names))
    ax.barh(y, p50, xerr=[lo, hi], color="#4C72B0", alpha=0.85,
            error_kw={"ecolor": "#1f3a64", "capsize": 4})
    ax.set_yticks(y); ax.set_yticklabels(names, fontsize=11)
    ax.invert_yaxis()
    ax.set_title("Terminal net real wealth at 70 (median, p10–p90 whiskers)")
    ax.set_xlabel("Net wealth (GBP, May-2026 real)")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_gbp))
    fig.text(0.5, 0.01,
             "Source: tax_wrapper_optimisation.py. All career-aware variants "
             "spend the same take-home saving; SIPP/LISA add government top-ups.",
             ha="center", fontsize=9, color="gray")
    plt.tight_layout(rect=[0, 0.05, 1, 1]); fig.savefig(path); plt.close(fig)


def wrapper_rules_table() -> pd.DataFrame:
    """2026/27 reference table for the four wrappers."""
    rows = [
        {"wrapper": "ISA (S&S)", "annual_limit": "£20,000",
         "contribution_relief": "none", "growth_tax": "none",
         "withdrawal_tax": "none", "access": "any age",
         "2026_note": "cash-ISA restriction mooted Apr-2027 (S&S unaffected)"},
        {"wrapper": "LISA", "annual_limit": "£4,000 (within ISA £20k)",
         "contribution_relief": "25% govt bonus (£1,000/yr)", "growth_tax": "none",
         "withdrawal_tax": "none for first home (<=£450k) or age 60+",
         "access": "first home or 60+; else 25% penalty",
         "2026_note": "open 18-39; contribute to 50; £450k cap binds in London"},
        {"wrapper": "SIPP", "annual_limit": "£60,000 or 100% earnings",
         "contribution_relief": "marginal rate (20/40/45%, 60% in PA taper)",
         "growth_tax": "none",
         "withdrawal_tax": "25% tax-free, rest at marginal rate",
         "access": "age 57 from Apr-2028 (was 55)",
         "2026_note": "LTA abolished; enters IHT estate from Apr-2027"},
        {"wrapper": "GIA", "annual_limit": "none",
         "contribution_relief": "none",
         "growth_tax": "dividends >£500 (10.75/35.75%); ",
         "withdrawal_tax": "CGT >£3k exemption (18%/24%)",
         "access": "any age",
         "2026_note": "exemptions frozen; dividend rates +2% from Autumn-25"},
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(CONFIG["results_dir"], exist_ok=True)
    rd = CONFIG["results_dir"]
    returns, months = generate_returns()

    house_reserve = calibrate_house_reserve(CONFIG["house_deposit"])

    # --- Core variants -----------------------------------------------------
    variants = [
        Variant("Status quo (£1,600/m, all-ISA)", policy_all_isa, saving="fixed"),
        Variant("All-ISA (career-aware)", policy_all_isa),
        Variant("All-ISA + house", policy_all_isa,
                need_house=True, house_deposit=CONFIG["house_deposit"]),
        Variant("Mod 2 — LISA priority", policy_lisa_priority,
                need_house=True, house_deposit=CONFIG["house_deposit"]),
        Variant("Mod 3 — SIPP-max (career)", policy_sipp_max),
        Variant("Mod 4 — LISA + SIPP", policy_lisa_plus_sipp,
                need_house=True, house_deposit=CONFIG["house_deposit"], mortgage=True),
        Variant("Mod 5 — Lifecycle optimum", policy_lifecycle_optimum,
                need_house=True, house_deposit=CONFIG["house_deposit"],
                house_reserve_annual=house_reserve),
    ]

    results: Dict[str, SimResult] = {}
    rows: List[Dict] = []
    for v in variants:
        res = simulate(v, returns, months)
        results[v.name] = res
        rows.append(results_row(res))
        print(f"{v.name:38s}  p50 £{np.median(res.terminal_total):>14,.0f}  "
              f"relief £{res.relief_total:>11,.0f}  bonus £{res.bonus_total:>9,.0f}")

    df_results = pd.DataFrame(rows)
    df_results.to_csv(os.path.join(rd, "v4_results_table.csv"), index=False)

    # --- Mod 5 deep-dive artefacts ----------------------------------------
    mod5 = variants[-1]
    sched5 = build_schedule(mod5)
    annual_alloc = sched5[["net_isa", "net_lisa", "net_sipp", "net_gia",
                           "c_sipp", "c_lisa", "sipp_relief", "lisa_bonus"]] \
        .groupby(sched5.index.year).sum()
    annual_alloc["age"] = annual_alloc.index - CONFIG["birth_year"]
    annual_alloc = annual_alloc[annual_alloc[["net_isa", "net_lisa", "net_sipp",
                                              "net_gia"]].sum(axis=1) > 0]
    annual_alloc.to_csv(os.path.join(rd, "v4_annual_allocation_mod5.csv"))

    by_age_rows = []
    for age in AGE_CHECKPOINTS:
        d = results[mod5.name].by_age_net.get(age, {})
        d2 = {"age": age, **d,
              "total_p50": results[mod5.name].by_age_total_pct.get(age, {}).get("p50")}
        by_age_rows.append(d2)
    pd.DataFrame(by_age_rows).to_csv(
        os.path.join(rd, "v4_wrapper_by_age_mod5.csv"), index=False)

    # --- House-deposit scenarios (Mod 2 with 40/50/60k) -------------------
    house_rows = []
    for dep in CONFIG["house_deposit_scenarios"]:
        v = Variant(f"LISA priority, £{dep/1000:.0f}k deposit",
                    policy_lisa_priority, need_house=True, house_deposit=dep)
        res = simulate(v, returns, months)
        house_rows.append({
            "deposit": dep,
            "house_feasible_prob": res.house_feasible,
            "terminal_p50": float(np.median(res.terminal_total)),
            "wealth_at_30_p50": res.by_age_total_pct.get(30, {}).get("p50"),
        })
    pd.DataFrame(house_rows).to_csv(
        os.path.join(rd, "v4_house_scenarios.csv"), index=False)

    # --- Stress tests (Mod 6a-6e on the lifecycle optimum) ----------------
    stall = [(26, 55_000.0), (29, 80_000.0), (32, 80_000.0), (70, 80_000.0)]
    accel = [(26, 55_000.0), (29, 90_000.0), (34, 180_000.0),
             (40, 300_000.0), (70, 300_000.0)]
    stress = [
        ("Mod 5 baseline", Variant("s5", policy_lifecycle_optimum, need_house=True,
                                   house_deposit=CONFIG["house_deposit"],
                                   house_reserve_annual=house_reserve)),
        ("6a Career stall (£80k)", Variant("s6a", policy_lifecycle_optimum,
                                           need_house=True,
                                           house_deposit=CONFIG["house_deposit"],
                                           house_reserve_annual=house_reserve,
                                           income_anchors=stall)),
        ("6b Career accelerates (£300k)", Variant("s6b", policy_lifecycle_optimum,
                                                  need_house=True,
                                                  house_deposit=CONFIG["house_deposit"],
                                                  house_reserve_annual=house_reserve,
                                                  income_anchors=accel)),
        ("6c Early retirement at 50", Variant("s6c", policy_lifecycle_optimum,
                                              need_house=True,
                                              house_deposit=CONFIG["house_deposit"],
                                              house_reserve_annual=house_reserve,
                                              early_retire=True)),
        ("6d No house (rent)", Variant("s6d", policy_lifecycle_optimum,
                                       need_house=False)),
        ("6e SIPP rules worsen", Variant("s6e", policy_lifecycle_optimum,
                                         need_house=True,
                                         house_deposit=CONFIG["house_deposit"],
                                         house_reserve_annual=house_reserve,
                                         sipp_taxfree_frac=0.0, sipp_relief_cap=0.20)),
    ]
    stress_rows = []
    for label, v in stress:
        res = simulate(v, returns, months)
        stress_rows.append({
            "scenario": label,
            "terminal_p10": float(np.percentile(res.terminal_total, 10)),
            "terminal_p50": float(np.percentile(res.terminal_total, 50)),
            "terminal_p90": float(np.percentile(res.terminal_total, 90)),
            "wealth_at_60_p50": res.by_age_total_pct.get(60, {}).get("p50"),
            "cumulative_relief": res.relief_total,
            "accessible_pre57_pct": res.accessible_pre57_pct,
        })
    pd.DataFrame(stress_rows).to_csv(
        os.path.join(rd, "v4_stress_tests.csv"), index=False)

    # --- Income sensitivity (+/-25%) on Mod 5 -----------------------------
    sens_rows = []
    for scale in (0.75, 1.0, 1.25):
        v = Variant(f"income x{scale}", policy_lifecycle_optimum, need_house=True,
                    house_deposit=CONFIG["house_deposit"],
                    house_reserve_annual=house_reserve, income_scale=scale)
        res = simulate(v, returns, months)
        sens_rows.append({
            "income_scale": scale,
            "terminal_p50": float(np.median(res.terminal_total)),
            "cumulative_relief": res.relief_total,
            "wealth_at_60_p50": res.by_age_total_pct.get(60, {}).get("p50"),
        })
    pd.DataFrame(sens_rows).to_csv(
        os.path.join(rd, "v4_income_sensitivity.csv"), index=False)

    # --- Reference rules table & probability targets ----------------------
    wrapper_rules_table().to_csv(os.path.join(rd, "v4_wrapper_rules.csv"), index=False)

    prob_rows = []
    for name, res in results.items():
        row = {"variant": name}
        for tgt in WEALTH_TARGETS:
            row[f"P(>=£{tgt//1_000_000}m)"] = float((res.terminal_total >= tgt).mean())
        prob_rows.append(row)
    pd.DataFrame(prob_rows).to_csv(
        os.path.join(rd, "v4_probability_targets.csv"), index=False)

    # --- Charts ------------------------------------------------------------
    traj = {k: results[k] for k in [
        "All-ISA (career-aware)", "Mod 2 — LISA priority", "Mod 3 — SIPP-max (career)",
        "Mod 4 — LISA + SIPP", "Mod 5 — Lifecycle optimum"]}
    plot_wealth_trajectory(traj, os.path.join(rd, "v4_wealth_trajectory.png"))
    plot_wrapper_allocation(results[mod5.name],
                            os.path.join(rd, "v4_wrapper_allocation.png"))
    plot_tax_relief(sched5, os.path.join(rd, "v4_tax_relief.png"))
    plot_terminal_comparison(rows, os.path.join(rd, "v4_terminal_comparison.png"))

    # --- Console summary ---------------------------------------------------
    base = df_results.loc[df_results["variant"] == "All-ISA (career-aware)",
                          "terminal_p50"].values[0]
    print("\nNet tax benefit vs all-ISA (career-aware) baseline, median terminal:")
    for _, r in df_results.iterrows():
        diff = r["terminal_p50"] - base
        print(f"  {r['variant']:38s}  £{r['terminal_p50']:>14,.0f}  "
              f"({diff:+,.0f}, {diff/base:+.1%})")
    print(f"\nHouse reserve calibrated: £{house_reserve:,.0f}/yr accessible, age 26-30.")
    print("All v4 outputs written to", rd)


if __name__ == "__main__":
    main()
