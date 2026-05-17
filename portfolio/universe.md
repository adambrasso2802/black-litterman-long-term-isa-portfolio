# Final ETF Universe

All instruments are Ireland-domiciled UCITS, LSE-listed, GBP-traded (price feed in GBX), accumulating share classes where available, and confirmed ISA-eligible on Vanguard UK, Hargreaves Lansdown, Trading 212, Freetrade, and InvestEngine (May 2026).

| # | Ticker | Name | Exchange | TER | Asset class | Index tracked | AUM (£bn, approx.) | Rationale |
|---|--------|------|----------|-----|-------------|---------------|---------------------|-----------|
| 1 | VWRP | Vanguard FTSE All-World UCITS ETF (Acc) | LSE | 0.22% | Global equity | FTSE All-World | 18.4 | Core equity anchor; closest single-ticker proxy to the market portfolio used as the BL prior. |
| 2 | VUSA | Vanguard S&P 500 UCITS ETF (Dist) | LSE | 0.07% | US large-cap equity | S&P 500 | 42.1 | Tilt to highest-margin global equity benchmark; lowest TER in class. |
| 3 | VUKE | Vanguard FTSE 100 UCITS ETF (Dist) | LSE | 0.09% | UK large-cap equity | FTSE 100 | 4.6 | Home-currency tilt, value / commodity by-product exposure. |
| 4 | VFEM | Vanguard FTSE Emerging Markets UCITS ETF (Dist) | LSE | 0.22% | EM equity | FTSE Emerging | 3.2 | Captures EM valuation discount and demographic tail wind (View 1). |
| 5 | WLDS | iShares MSCI World Small Cap UCITS ETF (Acc) | LSE | 0.35% | DM small-cap equity | MSCI World Small Cap | 1.5 | Size premium; diversifies away large-cap concentration in VWRP. |
| 6 | IWVL | iShares Edge MSCI World Value Factor UCITS ETF (Acc) | LSE | 0.30% | Value factor | MSCI World Enhanced Value | 1.0 | Implements value tilt (View 2). |
| 7 | IWQU | iShares Edge MSCI World Quality Factor UCITS ETF (Acc) | LSE | 0.30% | Quality factor | MSCI World Sector Neutral Quality | 4.8 | Defensive equity sleeve (View 5). |
| 8 | INFR | iShares Global Infrastructure UCITS ETF (Dist) | LSE | 0.40% | Listed infrastructure | FTSE Global Core Infrastructure | 1.3 | Inflation-linked cash flows; real asset (View 4). |
| 9 | AGGG | iShares Core Global Aggregate Bond UCITS ETF (GBP-Hedged, Acc) | LSE | 0.10% | Global bonds | Bloomberg Global Aggregate (Hedged GBP) | 2.7 | Diversified fixed-income ballast, currency-hedged. |
| 10 | IGLS | iShares UK Gilts 0-5yr UCITS ETF (Dist) | LSE | 0.07% | UK short gilts | FTSE Actuaries UK Gilts 0-5yr | 1.9 | Liquidity / rebalancing reserve; near-cash sterling ballast. |
| 11 | SGLN | iShares Physical Gold ETC | LSE | 0.12% | Gold | LBMA AM Gold Price | 16.0 | Tail-risk hedge; uncorrelated to risk assets in stress. |

**Blended TER of the recommended portfolio (using BL constrained weights from Step 5): ~0.19% p.a.**

Cap-weighted reference weights (used as `market_cap_weights` input to BL) are derived from MSCI ACWI IMI + Bloomberg Global Aggregate market caps as at Q1 2026, normalised to the universe:

| Ticker | Reference weight |
|--------|------------------|
| VWRP | 35.0% |
| VUSA | 18.0% |
| VUKE | 3.0% |
| VFEM | 6.0% |
| WLDS | 4.0% |
| IWVL | 3.0% |
| IWQU | 4.0% |
| INFR | 3.0% |
| AGGG | 18.0% |
| IGLS | 4.0% |
| SGLN | 2.0% |
| **Total** | **100.0%** |

These weights have meaningful overlap (VWRP already contains VUSA, VUKE, VFEM constituents). The BL model treats them as independent tickers — the covariance matrix captures their high pairwise correlation, and the optimiser will naturally avoid double-counting unless a view tilts a specific sleeve.
