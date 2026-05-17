# Macro Views Research (as at May 2026)

## Macro backdrop

- **Global growth.** IMF WEO April 2026 projects world real GDP growth of 3.2% in 2026 and 3.3% in 2027 — close to the 2000–2019 average. Decomposition: US 1.9%, Euro area 1.4%, UK 1.4%, China 4.5%, India 6.5%, ASEAN-5 5.0%.
- **Inflation.** UK CPI 2.4% (March 2026 print); BoE projects convergence to the 2% target by Q4 2027. US PCE 2.3%. Euro-area HICP 2.0%.
- **Rates.** Bank of England Bank Rate at 4.00% after three 25 bp cuts since mid-2025. Fed Funds at 3.75–4.00%. ECB deposit rate at 2.50%. Forward curves price a further 50–75 bps of cuts across each over the next 12 months.
- **Valuations.** US large-cap forward P/E 21.6x (top decile of 20-year history). MSCI Europe ex-UK 14.2x. MSCI UK 11.5x. MSCI EM 12.4x. Equity risk premium (US 10-year real yield basis) compressed to ~2.4%, roughly half its 20-year average.

## View construction — economic rationale

### View 1 — EM equities > developed-ex-US equities by +2.0% p.a.

EM trades at a ~33% forward-P/E discount to DM, one standard deviation wider than the 20-year average. Growth differential (5%+ EM vs ~1.5% DM ex-US) is at its widest since 2007. India and ASEAN demographic profiles are in early "demographic dividend" stage. Risk: China property overhang and US tariff escalation could keep the discount wide. **Confidence: medium**.

### View 2 — Value > Growth (global) by +1.5% p.a.

Russell 1000 Value vs Growth P/B spread is at the 92nd historical percentile (wider only during the dot-com peak and 2020–21). Mean-reversion in value-growth spreads over 5-year windows has historically delivered ~3% annualised value premium when starting from the top quintile of dispersion (Asness et al., 2015, "Value and Momentum Everywhere"). Risk: structurally elevated returns to intangible-heavy growth firms could persist. **Confidence: medium**.

### View 3 — UK equities > eurozone equities by +1.0% p.a.

UK forward P/E of 11.5x is a 20% discount to MSCI Europe ex-UK and a 45% discount to the S&P 500 — among the deepest cross-regional discounts in 25 years. FTSE 100 dividend yield 3.9% vs Euro Stoxx 50 yield 3.0%. Sterling has retraced its 2022 lows and on PPP estimates is ~10% undervalued vs USD. Risk: continued domestic political uncertainty / fiscal slippage. **Confidence: low–medium**.

### View 4 — Global infrastructure > global aggregate bonds by +3.0% p.a.

Infra cash flows are largely inflation-linked (regulated utilities, toll roads, airports) yet trade with a meaningful duration component. With long real yields elevated but expected to normalise lower, infrastructure earnings yields (~6%) materially exceed nominal bond yields (~4%). Diversification benefit relative to equities is preserved. Risk: rates stay higher for longer, compressing infra valuations. **Confidence: medium**.

### View 5 — Quality factor (global) > MSCI World by +0.5% p.a.

Quality has historically delivered ~150 bps p.a. of outperformance through full cycles with lower drawdowns (Novy-Marx 2013; MSCI Factor Indexes back-test). With the cycle maturing and credit spreads tight (US IG OAS 95 bps), high-balance-sheet-quality firms should outperform in any growth slowdown. Risk: quality is itself a crowded factor and trades at a premium. **Confidence: low–medium**.

### (Optional) View 6 — Short-dated gilts > global aggregate bonds by +0.3% p.a.

Sterling-domiciled investor avoids hedging cost; UK front-end yields elevated vs the gilt curve. Considered, but excluded from the final view set to keep the BL system from overweighting low-Sharpe positions. Documented for transparency only.

## How macro views map onto BL `P`, `Q`, `Ω`

Each view is encoded as a row of the views matrix `P`. Relative views (e.g., "EM > DM by X%") sum to zero across the assets they reference; absolute views (e.g., "Quality > World by X%") would be encoded by long-shorting the long-only-investable proxy. See `portfolio/views.md` and `portfolio/bl_model.py` for the numerical encoding.

Confidence is expressed via the diagonal of `Ω`, calibrated using the Idzorek (2005) implied-confidence method: a confidence percentage is mapped to the Omega entry that makes the BL posterior weight tilt match the desired tilt at full confidence. We use:

| Confidence label | Idzorek % | Omega entry (approx.) |
|---|---|---|
| High | 75% | 0.0005 |
| Medium | 50% | 0.0015 |
| Low–medium | 35% | 0.0030 |
| Low | 20% | 0.0080 |

These values are calibrated against the τ = 0.05 prior covariance and the asset volatilities in `bl_model.py`.
