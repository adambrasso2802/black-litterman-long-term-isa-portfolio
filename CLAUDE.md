# Black-Litterman Long-Term ISA Portfolio — Claude Code Brief

You are the world's best quantitative portfolio manager and financial engineer. Your task is to research, build, and fully document a production-grade **Black-Litterman portfolio** for a UK retail investor. Treat this as a professional deliverable — the kind a top-tier asset manager would hand to a client.

---

## Investor Profile

| Parameter | Value |
|---|---|
| Name | Adam |
| Domicile | United Kingdom |
| Account type | Stocks & Shares ISA (GBP-denominated) |
| Starting capital | £3,000 (lump sum, invested immediately) |
| Phase 1 contributions | £500 / month from **September 2027** for **3 years** (Sep 2027 – Aug 2030) |
| Phase 2 contributions | £1,600 / month from **September 2030** for **40 years** (Sep 2030 – Aug 2070) |
| Investment horizon | ~45 years (long-term, retirement-oriented) |
| Risk tolerance | Moderately high — young investor, long horizon, can tolerate volatility |
| Liquidity needs | None in the short term |
| Tax wrapper | ISA — no CGT, no dividend tax within the wrapper |
| Currency | GBP throughout |

---

## What You Must Build

Work through the following steps **in order**. Do not skip any.

### STEP 1 — Research Phase (use web search)

Search for and gather the following. Save all findings with citations to `research/sources.md`:

1. **Black-Litterman model** — original He & Litterman (1999) paper summary, key equations, tau parameter guidance, how views are incorporated (P, Q, Omega matrices), posterior expected returns formula.
2. **Equilibrium market portfolio** — how to derive implied equilibrium returns from the CAPM / market-cap weights. Use MSCI ACWI or equivalent as the reference market.
3. **Asset class universe** — research the best ETFs available to UK ISA investors on platforms like Vanguard, iShares, HSBC, and Xtrackers. Focus on:
   - Global equities (developed + emerging)
   - UK equities
   - US equities / S&P 500
   - Small cap / factor ETFs (value, momentum, quality)
   - Infrastructure / real assets
   - Bonds (global aggregate, short-duration gilts as ballast)
   - Commodities (optional, if Sharpe-enhancing)
4. **Long-horizon portfolio theory** — research lifecycle investing, the case for equity-heavy allocation for 40+ year horizons, rebalancing strategies, and glide-path considerations.
5. **Current macro views** — search for current consensus views on: US vs international equity premium, value vs growth, inflation outlook, UK vs global allocation. Use these to form the BL "investor views".
6. **Historical return data** — retrieve or estimate long-run annualised returns, volatilities, and correlations for your chosen asset classes (10–30 year data where available). Use published sources (MSCI, Vanguard, Dimson-Marsh-Staunton if accessible).

---

### STEP 2 — Asset Universe Selection

Based on your research, select **8–12 ETFs** that:
- Are domiciled in Ireland (UCITS-compliant, UK ISA eligible)
- Have TERs as low as possible
- Are GBP-traded (LSE-listed) or hedged to GBP where appropriate
- Are available on major UK platforms (Vanguard, Hargreaves Lansdown, Freetrade, Trading 212)
- Cover a diversified set of risk premia

Create `portfolio/universe.md` with a table showing: ETF name, ticker, exchange, TER, asset class, index tracked, AUM, and rationale for inclusion.

---

### STEP 3 — Black-Litterman Model Implementation

Create `portfolio/bl_model.py` — a clean, well-commented Python implementation of the full BL model:

```
Required inputs:
- market_cap_weights: dict of asset → weight in reference market portfolio
- sigma: covariance matrix (annualised) estimated from historical returns
- tau: scalar (suggest 0.05 as starting point; discuss sensitivity)
- risk_aversion (delta): derived from market Sharpe ratio (suggest ~2.5)
- P: views matrix (k × n) — which assets each view is about
- Q: views vector (k × 1) — the expected return for each view
- Omega: uncertainty matrix for views (k × k diagonal)

Required outputs:
- pi: implied equilibrium excess returns vector
- posterior_mu: BL posterior expected returns (the key output)
- posterior_sigma: BL posterior covariance matrix
- optimal_weights: mean-variance optimal weights using posterior inputs
- portfolio_metrics: dict with expected return, volatility, Sharpe ratio
```

Also implement:
- `efficient_frontier()` — plot the efficient frontier with and without BL views
- `rebalancing_schedule()` — annual rebalancing logic
- `sensitivity_analysis()` — show how weights change with tau and view confidence

Use `numpy`, `scipy`, `pandas`, `matplotlib`, `seaborn`. Do not use PyPortfolioOpt — implement from scratch to demonstrate the mechanics.

---

### STEP 4 — Investor Views

Based on your macro research (Step 1), define **5–8 explicit investor views** to feed into the BL model. Each view must have:
- A clear economic rationale (2–3 sentences)
- A confidence level (expressed as Omega uncertainty)
- A specific expected outperformance / return figure

Document these in `portfolio/views.md` with full justification.

Example view format:
```
View 3: Emerging market equities will outperform developed ex-US equities 
        by 1.5% per annum over the next 5 years.
Rationale: Valuation discount at historic widths; demographic tailwinds in India/SE Asia;
           China policy normalisation expected.
Confidence: Medium (Omega diagonal = 0.002)
```

---

### STEP 5 — Run the Model & Optimise

Run `bl_model.py` end-to-end and produce:

1. A table of **implied equilibrium returns** (pi) vs **BL posterior returns** (mu*) for each asset
2. The **optimal BL weights** (both unconstrained and with practical constraints: no short selling, max 40% any single asset, min 2% any included asset)
3. The **final recommended portfolio** — a clean allocation table with:
   - Asset name, ticker, weight (%), £ amount from £3,000 initial
   - Number of shares (approximate, based on prices at time of running)
4. Portfolio-level statistics: expected annual return, volatility, Sharpe ratio, max drawdown estimate
5. Comparison vs a naive 60/40 and vs 100% MSCI ACWI

Save all outputs to `portfolio/results/` as both `.csv` and `.png` charts.

---

### STEP 6 — Wealth Projection Model

Create `projections/wealth_model.py` with a full Monte Carlo simulation:

```
Parameters:
- t=0: £3,000 lump sum
- Phase 1: £500/month, Sep 2027 – Aug 2030 (36 months)
- Phase 2: £1,600/month, Sep 2030 – Aug 2070 (480 months)
- Simulate using BL posterior expected return and volatility
- Run 10,000 paths
- Use GBP real returns (adjust for 2.5% inflation assumption)
- Account for ISA contribution limits (currently £20,000/year — note if contributions approach this)

Output:
- Median, 10th, 25th, 75th, 90th percentile wealth at each year
- Probability of reaching £1m, £2m, £5m, £10m in real terms
- Expected portfolio value at age 30, 40, 50, 60, 65, 70
- Chart: fan chart showing wealth distribution over time (publication quality)
- Sensitivity table: what if returns are 1% lower / 1% higher than BL estimate?
```

---

### STEP 7 — Implementation Guide

Create `docs/implementation_guide.md`:

1. **Platform recommendation** — which UK platform to use and why (Freetrade, Trading 212, HL, Vanguard, InvestEngine) given the starting capital and contribution amounts
2. **How to place the initial trades** — step by step, how to invest £3,000 across the chosen ETFs on day one
3. **Contribution strategy** — how to deploy £500 and then £1,600/month (lump sum each month vs DCA within month)
4. **Rebalancing rules** — annual calendar rebalancing, or threshold-based (5% drift trigger)? Recommend the best approach for a long-horizon ISA investor
5. **ISA contribution limit management** — £20,000/year limit planning; note that £1,600/month = £19,200/year, safely within the limit
6. **Tax considerations** — within ISA wrapper; note any withholding tax on foreign dividends (30% US withholding on Irish UCITS is reduced to 15% via US-Ireland treaty)
7. **Glide path** — at what age / portfolio size to begin shifting toward less volatile assets; suggest a trigger and target allocation

---

### STEP 8 — README (the centrepiece document)

Create `README.md` — a world-class document that a CFA charterholder would be proud to hand to a client. It must include:

```
# Adam's Black-Litterman ISA Portfolio

## Executive Summary
## The Black-Litterman Framework (plain English + equations)
## Asset Universe & Rationale
## Investor Views & Economic Rationale
## Portfolio Allocation (the final table)
## Expected Performance Statistics
## Wealth Projections (with fan chart inline)
## Risk Factors & Caveats
## Implementation Guide (summary)
## Rebalancing & Maintenance
## Appendix: BL Model Equations
## Appendix: Data Sources
## Appendix: Assumptions
```

The README should be readable by an intelligent non-quant, but rigorous enough to satisfy a professional investor. Use tables, charts (embed as images), and clear headings throughout.

---

### STEP 9 — Risk & Assumptions Document

Create `docs/risks_and_assumptions.md` covering:

1. Model risk (BL is sensitive to input assumptions)
2. Estimation error in the covariance matrix
3. The stationarity assumption — returns may not be normally distributed
4. Sequence-of-returns risk in Phase 1 (early years matter most)
5. Currency risk (GBP investor holding USD-denominated assets)
6. Political / regulatory risk (ISA rules, UK tax law changes)
7. Platform risk (broker failure — FSCS protects up to £85,000)
8. Inflation risk — real vs nominal return distinction
9. Behavioural risk — staying the course through drawdowns
10. Liquidity risk — ETF bid-ask spreads, especially in stress

---

### STEP 10 — Final File Structure

When complete, the project should look like this:

```
bl-portfolio/
├── README.md                          ← Main document (centrepiece)
├── CLAUDE.md                          ← This file
├── research/
│   ├── sources.md                     ← All citations from web search
│   ├── asset_universe_research.md     ← ETF screening notes
│   └── macro_views_research.md        ← Macro backdrop and views rationale
├── portfolio/
│   ├── universe.md                    ← Final ETF universe table
│   ├── views.md                       ← BL investor views
│   ├── bl_model.py                    ← Core BL implementation
│   └── results/
│       ├── equilibrium_vs_posterior.csv
│       ├── optimal_weights.csv
│       ├── efficient_frontier.png
│       ├── portfolio_allocation.csv
│       └── bl_weights_chart.png
├── projections/
│   ├── wealth_model.py                ← Monte Carlo simulation
│   └── results/
│       ├── wealth_paths.csv
│       ├── wealth_fan_chart.png
│       └── scenario_table.csv
└── docs/
    ├── implementation_guide.md
    └── risks_and_assumptions.md
```

---

## Code Quality Standards

- All Python must be PEP 8 compliant
- Every function must have a docstring
- Use type hints throughout
- No hard-coded magic numbers — everything in a `CONFIG` dict at the top of each file
- All charts must be publication quality: proper titles, axis labels, legends, source annotations
- All monetary values in GBP (£) with comma separators
- Comments must explain *why*, not just *what*

---

## Final Checks Before Finishing

Before declaring complete, verify:
- [ ] `bl_model.py` runs end-to-end without errors
- [ ] `wealth_model.py` runs end-to-end and produces the fan chart
- [ ] All ETFs selected are genuinely ISA-eligible and UK-listed
- [ ] ISA contribution limits are correctly modelled (£20,000/year)
- [ ] All charts are saved as high-resolution PNGs
- [ ] README.md reads as a complete, standalone document
- [ ] All citations in `research/sources.md` are real and verifiable

---

## How to Run

```bash
# Install dependencies
pip install numpy scipy pandas matplotlib seaborn yfinance requests

# Run BL model
cd portfolio && python bl_model.py

# Run wealth projections
cd projections && python wealth_model.py
```

---

*This brief was generated for Adam's personal long-term ISA portfolio. All outputs are for educational and personal financial planning purposes. This is not regulated financial advice.*
