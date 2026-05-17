# Risks and Assumptions

A long-horizon portfolio is only as good as the assumptions behind it. This document catalogues the assumptions that drive the BL recommendation and the wealth model, and the risks they expose Adam to.

## 1. Model risk — BL is sensitive to its inputs

The posterior weights are a function of: τ, the prior covariance Σ, the views matrix `P`, the view returns `Q`, and the uncertainty matrix Ω. We mitigate this by:

- Running `sensitivity_analysis()` over (τ ∈ {0.025, 0.05, 0.10}) × (Ω-scale ∈ {0.5, 1, 2}). The output, `portfolio/results/sensitivity_weights.csv`, shows that no single line moves more than ~4% across the 9 combinations, which is reassuring.
- Constraining the optimiser (long-only, max 40%, min 2% if held). Constraints dominate in extreme parameter regions, capping the damage.
- Documenting every view in `portfolio/views.md` with an explicit economic rationale and confidence level. Future Adam can read those rationales, decide whether they still hold, and adjust.

## 2. Estimation error in Σ

The covariance matrix is the largest source of variance in any optimiser output. Our 11×11 Σ uses long-run volatility estimates from MSCI / Bloomberg / LBMA and correlation entries rounded to 2 dp from realised post-1995 monthly data. We do **not** use a recent rolling window because:

- Recent samples overweight the regime that produced them (e.g. post-2008 bond-equity correlation was unusually negative).
- A 45-year horizon investor cares about the unconditional long-run distribution, not the last 5 years.

**Risk:** the true Σ is unobservable; correlations rise toward 1 in crises, which our matrix understates.

## 3. Stationarity / non-normality

Returns are not lognormal — they exhibit fat tails, volatility clustering, and skew. The wealth model uses a lognormal monthly model, which is conservative on volatility (Σ-implied) but **understates tail risk**. A 1-in-100 year drawdown realised over a 45-year horizon (which contains ~5 such bins) could be materially worse than the 10th percentile of the fan chart.

## 4. Sequence-of-returns risk in Phase 1

Returns in the early years matter most because compounding works for longer. A 2027–2029 drawdown of 30% would shave a much larger absolute amount off Adam's terminal wealth than the same drawdown in 2065–2067. Two mitigants are baked in:

- The Phase 1 contributions are themselves a **DCA effect**: low markets buy more shares.
- The portfolio retains ~17% bonds and ~8% short gilts during Phase 1 — these dampen the early drawdown.

## 5. Currency risk

Adam is a GBP investor but ~50% of the equity sleeve is USD-denominated and ~10% EM-currency. Currency is a (mostly) zero-mean risk factor over decades — but over 5-year windows can add or subtract several percent. We mitigate by GBP-hedging the bond sleeve (AGGG-H) and by adding a domestic-currency tilt (VUKE). We deliberately do **not** hedge the equity sleeve because the cost of long-dated FX hedging exceeds its long-run benefit (Campbell, Serfaty-de Medeiros & Viceira 2010).

## 6. Political / regulatory risk

- ISA rules could change. Both 2024 manifestos discussed lifetime ISA caps; either party could implement one. Mitigation: the strategy is portable to a GIA + pension if needed.
- Tax wrappers themselves could change (dividend tax rules, CGT rates outside the wrapper, inheritance tax treatment).
- LSE listing of Irish UCITS funds could be affected by post-Brexit changes. Risk is low but non-zero.

## 7. Platform risk

FSCS protects investments up to £85,000 per authorised firm. Once the portfolio exceeds that threshold (estimated ~2032 at the median path), Adam should consider spreading across two platforms. Note that for funds the underlying assets sit in segregated custody — most "platform failure" risks are operational delays, not asset loss.

## 8. Inflation risk

All wealth-model outputs are quoted in **May-2026 real GBP**, deflated at 2.5% p.a. — the BoE long-run target. If realised inflation runs persistently higher (e.g., 4%), nominal wealth will look fine but purchasing power will be lower than the chart suggests. The infrastructure and gold positions provide some inflation pass-through; the short-gilt sleeve does not.

## 9. Behavioural risk

The single largest predictor of long-term return is *staying invested*. The fan chart in `projections/results/wealth_fan_chart.png` shows median wealth tripling between ages 50 and 60 — most of that compounding is fragile to panic-selling during the next bear market. Mitigations:

- Annual rebalancing forces a contrarian discipline (sell what went up, buy what went down).
- Monthly contributions are automated.
- Re-read this document during the next 30%+ drawdown.

## 10. Liquidity risk

Bid-ask spreads on the chosen ETFs are typically ≤ 0.10% in normal conditions. In stress (e.g., March 2020), spreads on global aggregate bond ETFs widened to ~1% intraday for several hours before authorised participants restored arbitrage. None of the chosen lines have closed or suspended in any recorded stress event since their inception. Use **limit orders**, not market orders, especially for the smaller lines (WLDS, INFR, IWVL).

---

## Key assumptions table (everything in one place)

| Assumption | Value | Source |
|---|---|---|
| τ | 0.05 | He & Litterman (1999) |
| Risk aversion δ | 2.5 | Long-run global Sharpe ~0.35, σ ~15% |
| Risk-free rate | 4.0% | BoE Bank Rate, May 2026 |
| Inflation | 2.5% | BoE target |
| Equity vols | 14–21% | MSCI long-run |
| Bond vols | 3–5.5% | Bloomberg |
| ISA annual limit | £20,000 | HMRC (2026/27) |
| Initial lump sum | £3,000 | Investor |
| Phase 1 monthly | £500 (Sep 27 – Aug 30) | Investor |
| Phase 2 monthly | £1,600 (Sep 30 – Aug 70) | Investor |
| Monte Carlo paths | 10,000 | Engineering choice — convergence < 1% |
| Investor DOB | 2001 (assumed) | For age-based outputs only |

If any of these change, edit the `CONFIG` dict at the top of `portfolio/bl_model.py` or `projections/wealth_model.py` and re-run.
