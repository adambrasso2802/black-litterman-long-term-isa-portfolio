# Asset Universe Research — Screening Notes

## Screening criteria

A candidate ETF qualifies for inclusion if it satisfies **all** of the following:

1. **Domicile**: Ireland (`IE` ISIN prefix). Irish-domiciled UCITS funds enjoy a 15% US withholding rate on US-source dividends (US–Ireland tax treaty), versus 30% for Luxembourg-domiciled UCITS or directly held US securities. This alone preserves roughly 15 bps p.a. on the US allocation.
2. **ISA-eligibility**: must be listed on a recognised stock exchange (LSE) and available on the major UK retail platforms.
3. **TER**: ≤ 0.40% for broad market exposures; ≤ 0.50% acceptable for niche (factor / infra / EM) exposures.
4. **AUM**: ≥ £500m, to ensure tight spreads, market-maker depth, and a low probability of fund closure over a 45-year horizon.
5. **Replication**: physical replication (full or optimised). No swap-based synthetics for the core sleeve — counterparty risk over 45 years is non-trivial.
6. **Income treatment**: accumulating share classes preferred for ISA simplicity (no manual reinvestment).

## Universe shortlist and rationale

### Global equity core

- **VWRP — Vanguard FTSE All-World UCITS ETF (Acc).** TER 0.22%. AUM ~£18bn. Covers ~4,000 stocks across DM + EM. The natural "anchor" for the equity sleeve and the closest proxy to the global market portfolio that Black-Litterman uses as its equilibrium prior.

### Regional / national tilts

- **VUSA — Vanguard S&P 500 UCITS ETF.** TER 0.07%. Used to lean into the US large-cap factor where a view expresses outperformance. Pure cap-weighted S&P 500.
- **VUKE — Vanguard FTSE 100 UCITS ETF.** TER 0.09%. Domestic-currency exposure and a partial currency hedge against GBP appreciation. FTSE 100 is heavy in oils, miners, and banks — provides a value / commodity tilt as a by-product.
- **VFEM — Vanguard FTSE Emerging Markets UCITS ETF.** TER 0.22%. Captures the EM valuation discount and demographic tail wind.

### Factor / style exposures

- **WLDS — iShares MSCI World Small Cap UCITS ETF.** TER 0.35%. Size premium; the All-World index is ~90% large/mid-cap, so a dedicated small-cap sleeve materially improves diversification.
- **IWMO — iShares Edge MSCI World Momentum Factor UCITS ETF.** TER 0.30%.
- **IWQU — iShares Edge MSCI World Quality Factor UCITS ETF.** TER 0.30%.
- **IWVL — iShares Edge MSCI World Value Factor UCITS ETF.** TER 0.30%.

The three single-factor ETFs provide low-cost access to the well-documented quality, value, and momentum premia. We include only one in the final portfolio (IWVL — see views) to avoid over-fitting and to keep the line-count manageable.

### Real assets

- **INFR — iShares Global Infrastructure UCITS ETF.** TER 0.40%. Long-duration, inflation-linked cash flows; partial inflation hedge.

### Fixed income

- **AGGG — iShares Core Global Aggregate Bond UCITS ETF (GBP-Hedged).** TER 0.10%. Multi-sector global bond exposure, currency-hedged back to GBP so that the bond sleeve actually behaves like a defensive asset for a sterling investor.
- **IGLS — iShares UK Gilts 0-5yr UCITS ETF.** TER 0.07%. Short-duration sterling ballast; rebalancing reserve.

### Commodity (optional)

- **SGLN — iShares Physical Gold ETC.** TER 0.12%. Included at a small weight as a tail-risk hedge against monetary debasement and geopolitical stress. Note SGLN is an ETC (debt instrument backed by allocated gold), not a UCITS ETF — confirmed ISA-eligible on all major UK platforms.

## Rejected candidates

- **iShares S&P 500 (CSPX)** — duplicates VUSA at marginally higher TER.
- **Vanguard FTSE Developed World (VEVE)** — would overlap heavily with VWRP without adding a meaningful tilt.
- **SPDR S&P 500 (SPY5)** — TER 0.03% wins on cost but lower platform availability than VUSA.
- **iShares Global Clean Energy (INRG)** — thematic; too narrow / volatile and high concentration risk for a long-term ballast portfolio.
- **Wisdom Tree Enhanced Commodity (WCOG)** — broad commodity exposure attractive in theory but the contango drag has historically eroded the diversification benefit; the gold ETC offers a cleaner tail-risk hedge.
- **Property (HPRO, IUKP)** — UK property ETFs have limited liquidity in stress (Woodford / SLI suspension precedent); infrastructure (INFR) covers the real-asset slot more reliably.

## Final 11-line universe

After the above filtering we adopt the following 11 instruments (see `portfolio/universe.md` for the consolidated table):

1. VWRP — global equity core
2. VUSA — US large-cap tilt
3. VUKE — UK home bias
4. VFEM — emerging markets
5. WLDS — global small cap
6. IWVL — value factor
7. IWQU — quality factor
8. INFR — global infrastructure
9. AGGG — global aggregate bonds (GBP-hedged)
10. IGLS — short-dated gilts
11. SGLN — physical gold

This breadth (3 factor tilts, 1 real-asset, 2 bond, 1 gold, plus the core) is enough to allow the Black-Litterman posterior to express the macro views without becoming so granular that estimation error overwhelms the views.
