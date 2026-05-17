# Implementation Guide

A step-by-step playbook for turning the Black-Litterman recommendation into a live, maintained ISA portfolio.

## 1. Platform recommendation

| Platform | Custody fee | Fund-dealing fee | FX fee | ISA-eligible | Verdict |
|---|---|---|---|---|---|
| **Trading 212** | £0 | £0 | 0.15% | Yes | **Recommended for accumulation phase.** |
| **InvestEngine** | £0 (DIY) | £0 | 0.30% | Yes | Strong alternative; weaker on individual share purchases. |
| Freetrade | £4.99/m (ISA tier) | £0 | 0.45% | Yes | Reasonable, but flat fee bites on a £3k starting pot. |
| Hargreaves Lansdown | 0.45% capped £45/y ETFs | £11.95 | 1.00% | Yes | Best research, worst FX. Switch worth considering once portfolio > £200k. |
| Vanguard UK | 0.15% capped £375 | £0 | n/a (GBP) | Yes | Excellent for Vanguard-only investors; misses the iShares lines we need. |

**Recommendation:** start on Trading 212. Zero platform fee + zero dealing means 100% of monthly contributions get invested. FX cost of 0.15% applies only on the USD-denominated lines (which the LSE-listed GBX share classes don't trigger for buys settled in GBP — confirm at trade time). Migrate to Hargreaves Lansdown once the portfolio exceeds ~£200k, at which point the £45 cap dominates and research depth becomes useful.

## 2. Placing the initial £3,000 of trades

Use limit orders during LSE main hours (08:00–16:30 London) to control execution. Spread is widest in the first and last 30 minutes; the 10:00–15:30 window is the calmest.

| Ticker | Target % | £ amount | Notes |
|---|---|---|---|
| VUSA | 14.8% | £443 | Pence-traded, 4dp share count |
| VUKE | 10.5% | £316 | |
| VFEM | 17.2% | £515 | |
| WLDS | 3.4% | £101 | Smallest line; minimum-trade size check |
| IWVL | 16.1% | £484 | |
| IWQU | 9.6% | £287 | |
| INFR | 2.7% | £82 | Smallest; verify platform allows £80 fractional trades |
| AGGG | 17.3% | £520 | |
| IGLS | 8.4% | £253 | |
| **Total** | **100%** | **£3,001** | (£1 from rounding; cash drag absorbed in IGLS) |

For platforms that don't support fractional ETF shares (HL, II), round to whole shares and absorb the residual cash in IGLS (smallest tracking error per £).

## 3. Contribution strategy

- **Phase 1 (Sep 2027 – Aug 2030): £500 / month.** Deposit on the first business day of each month and invest the entire amount the same day across the four most underweight lines. £500 spread across 4 lines averages £125 per trade — comfortable on Trading 212 / InvestEngine fractional ETFs. **Do not** try to time the market.
- **Phase 2 (Sep 2030 – Aug 2070): £1,600 / month.** Same logic — invest immediately into the most underweight lines. £1,600 / month = £19,200 / year, which sits £800 under the £20,000 ISA limit; the buffer absorbs any ad-hoc top-ups (bonus, inheritance) without breaching it.
- **Lump-sum vs DCA within month.** Academic consensus (Vanguard 2012, Constantinides) favours lump-sum for ~2/3 of historical windows. With monthly DCA already baked in at the contribution cadence, no further intra-month splitting is needed.

## 4. Rebalancing rules

Hybrid annual-plus-threshold:

1. **Calendar rebalance every April**, on the first business day of the new UK tax year. Sell the most overweight lines and direct the proceeds — plus the next month's contribution — into the underweights. Inside an ISA there is no CGT, so realised gains are costless.
2. **Contribution-driven rebalance every month.** Allocate new cash to whichever line has the largest negative drift. This alone is usually enough to keep the portfolio within ±2% of target while wealth is small.
3. **Threshold trigger.** If any single line drifts more than ±5% absolute from its target between April rebalances, do an interim trim/top-up. This rule rarely fires in practice but bounds tracking error in a major dislocation (e.g. 2008, 2020).

## 5. ISA limit management

| Year | Planned subscription | ISA limit (assumed flat £20k) | Headroom |
|---|---|---|---|
| 2026/27 | £3,000 | £20,000 | £17,000 |
| 2027/28 – 2029/30 (Phase 1) | £6,000 | £20,000 | £14,000 |
| 2030/31 onwards (Phase 2) | £19,200 | £20,000 | £800 |

If the limit is raised in future Budgets, the £800 buffer absorbs any additional regular contribution Adam wishes to make. If the limit is reduced (politically possible — Labour and Conservative manifestos in 2024 both floated lower lifetime ISA caps), the Phase 2 contribution may need trimming or an additional GIA opened.

## 6. Tax considerations

- **Inside the wrapper.** No CGT, no UK dividend tax. Distributions from `VUSA`, `VUKE`, `INFR`, `IGLS` are paid quarterly and should be reinvested manually (or set the platform's auto-reinvest where supported).
- **Withholding tax.** Irish-domiciled UCITS funds pay 15% US dividend WHT (treaty rate) versus 30% otherwise. There is no further UK liability on those dividends within the ISA. WHT is irrecoverable but baked into the published TER of US-exposed funds.
- **Reporting.** ISAs are not on the SA tax return; no annual filing required.
- **Inheritance.** ISAs are subject to UK IHT unless an APS (Additional Permitted Subscription) to a spouse applies. For a 25-year-old this is a non-issue today but worth re-examining at retirement.

## 7. Glide path

A 100% equity sleeve is appropriate today. As Adam approaches drawdown, gradually shift toward the defensive lines. Trigger by **age** (simple) or by **portfolio size relative to the target spending pot** (more sophisticated, "funded ratio" approach). Recommended schedule:

| Age | Trigger | Equity (VWRP + tilts + INFR) | Bonds (AGGG + IGLS) | Gold (SGLN) |
|---|---|---|---|---|
| ≤ 50 | Current BL recommendation | ~74% | ~26% | 0% |
| 50–55 | Gradually shift each annual rebalance | 70% | 28% | 2% |
| 55–60 | Continue shifting | 60% | 35% | 5% |
| 60–65 | "Retirement red zone" | 50% | 45% | 5% |
| 65+ | Drawdown phase — sequence-of-returns matters most | 45% | 50% | 5% |

Re-running `bl_model.py` annually with updated views and an updated risk aversion δ (mechanical link: δ rises by ~0.1 per decade of remaining horizon shortening) implements this automatically.
