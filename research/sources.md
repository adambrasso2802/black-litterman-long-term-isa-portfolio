# Research Sources

All sources used to build the Black-Litterman ISA portfolio for Adam. Citations are organised by the research strand to which they belong.

---

## 1. Black-Litterman Model — Foundational Literature

1. **Black, F. and Litterman, R. (1992).** "Global Portfolio Optimization." *Financial Analysts Journal*, 48(5), 28–43. — Original publication of the model.
2. **He, G. and Litterman, R. (1999).** "The Intuition Behind Black-Litterman Model Portfolios." *Goldman Sachs Investment Management Research*. — The canonical explanation of the model mechanics, posterior return formula, and worked examples. PDF: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=334304
3. **Idzorek, T. (2005).** "A Step-By-Step Guide to the Black-Litterman Model: Incorporating User-Specified Confidence Levels." *Ibbotson Associates Working Paper*. — Practitioner-oriented guide; introduces the "Idzorek confidence" method that maps a 0–100% confidence to an Omega entry. https://corporate.morningstar.com/ib/documents/methodologydocuments/ibbotsonassociates/blacklitterman.pdf
4. **Walters, J. (2014).** "The Black-Litterman Model in Detail." *SSRN Working Paper 1314585*. — Comprehensive derivation including alternative tau specifications and confidence calibration.
5. **Meucci, A. (2010).** "The Black-Litterman Approach: Original Model and Extensions." *Encyclopedia of Quantitative Finance*. — Bayesian interpretation and generalisations.

### Key equations used

Implied equilibrium excess returns: **π = δ Σ w_mkt**

Posterior expected returns:
**μ\* = π + τ Σ Pᵀ (P τ Σ Pᵀ + Ω)⁻¹ (Q − P π)**

Posterior covariance (He–Litterman form):
**Σ\* = Σ + [(τ Σ)⁻¹ + Pᵀ Ω⁻¹ P]⁻¹**

Unconstrained mean-variance optimal weights:
**w\* = (δ Σ\*)⁻¹ μ\***

Tau guidance: typical values 0.025–0.10. He–Litterman use 0.05; Meucci argues τ ≈ 1/T where T is the sample size; we adopt **τ = 0.05** and run sensitivity at 0.025 and 0.10.

Risk aversion δ guidance: derived from market Sharpe S and market vol σ as δ = (E[R_m] − R_f) / σ_m². For a long-run global equity Sharpe of ~0.35 and vol of ~15%, δ ≈ 2.5.

---

## 2. Equilibrium Market Portfolio

1. **MSCI ACWI IMI Index Factsheet (2024).** https://www.msci.com/documents/10199/b1435d2a-e6f2-487a-9b6b-7d0a8c0b9ad4 — Global investable equity universe; ~99% of investable equity. Used to anchor the equity-market-cap weights.
2. **FTSE Russell Global All Cap Index Factsheet.** https://research.ftserussell.com/Analytics/FactSheets/temp/2caa8c44-9436-44ed-9d09-7c8b58cabf94.pdf — Cross-check on regional weights.
3. **Bloomberg Global Aggregate Index Methodology.** https://assets.bbhub.io/professional/sites/27/Global-Aggregate-Index.pdf — Reference for the global bond universe and its market capitalisation versus equities.
4. **BIS Quarterly Review (Dec 2024).** Estimates of the global stock-of-financial-assets split between equities and bonds. https://www.bis.org/publ/qtrpdf/r_qt2412.htm

The implied equilibrium portfolio is built from these reference weights (Section 4 of `portfolio/bl_model.py`).

---

## 3. Asset Class Universe — UK-eligible UCITS ETFs

ETF factsheets (all Ireland-domiciled, UCITS, LSE-listed in GBP / GBX):

| Ticker | Name | Source |
|---|---|---|
| VWRP | Vanguard FTSE All-World UCITS ETF (Acc) | https://www.vanguard.co.uk/professional/product/etf/equity/9679/ftse-all-world-ucits-etf-usd-accumulating |
| VUKE | Vanguard FTSE 100 UCITS ETF (Dist) | https://www.vanguard.co.uk/professional/product/etf/equity/9504/ftse-100-ucits-etf |
| VUSA | Vanguard S&P 500 UCITS ETF | https://www.vanguard.co.uk/professional/product/etf/equity/9501/sp-500-ucits-etf |
| VFEM | Vanguard FTSE Emerging Markets UCITS ETF | https://www.vanguard.co.uk/professional/product/etf/equity/9506/ftse-emerging-markets-ucits-etf |
| WLDS | iShares MSCI World Small Cap UCITS ETF | https://www.ishares.com/uk/individual/en/products/296576/ |
| IWMO | iShares Edge MSCI World Momentum Factor UCITS ETF | https://www.ishares.com/uk/individual/en/products/270056/ |
| IWQU | iShares Edge MSCI World Quality Factor UCITS ETF | https://www.ishares.com/uk/individual/en/products/270054/ |
| IWVL | iShares Edge MSCI World Value Factor UCITS ETF | https://www.ishares.com/uk/individual/en/products/270053/ |
| INFR | iShares Global Infrastructure UCITS ETF | https://www.ishares.com/uk/individual/en/products/251872/ |
| AGGG | iShares Core Global Aggregate Bond UCITS ETF (GBP Hedged) | https://www.ishares.com/uk/individual/en/products/291772/ |
| IGLS | iShares UK Gilts 0-5yr UCITS ETF | https://www.ishares.com/uk/individual/en/products/251818/ |
| SGLN | iShares Physical Gold ETC | https://www.ishares.com/uk/individual/en/products/258441/ |

Platform eligibility was cross-checked against:
- Vanguard UK ISA product list: https://www.vanguard.co.uk/personal/product/list/etf
- Hargreaves Lansdown ETF screener: https://www.hl.co.uk/shares/etfs
- Trading 212 ISA-eligible instruments list: https://www.trading212.com/instruments

---

## 4. Long-Horizon Portfolio Theory

1. **Dimson, E., Marsh, P., and Staunton, M. (2024).** *Credit Suisse / UBS Global Investment Returns Yearbook 2024.* — 123-year (1900–2023) annualised real returns: world equities 5.1%, world bonds 1.7%, UK equities 5.4%. https://www.ubs.com/global/en/investment-bank/in-focus/2024/global-investment-returns-yearbook.html
2. **Vanguard (2024).** "Vanguard Economic and Market Outlook 2025." — 10-year nominal return CMAs: global ex-US equities 7.3–9.3%, US equities 2.8–4.8%, global aggregate bonds 4.3–5.3%. https://corporate.vanguard.com/content/corporatesite/us/en/corp/vemo/vanguard-economic-market-outlook.html
3. **Cederburg, S., Pflueger, C., O'Doherty, M. (2023).** "Beyond the Status Quo: A Critical Assessment of Lifecycle Investment Advice." *NBER Working Paper 31616*. — Empirical case for sustained equity heavy allocations over 40+ year horizons.
4. **Bengen, W. P. (1994).** "Determining Withdrawal Rates Using Historical Data." *Journal of Financial Planning*. — Foundational lifecycle / sequence-of-returns paper.
5. **Vanguard Research (2019).** "Best Practices for Portfolio Rebalancing." — Threshold vs calendar rebalancing analysis; recommends annual with a 5% absolute drift band. https://www.vanguard.com/pdf/ISGPORE.pdf

---

## 5. Current Macro Views (as at May 2026)

1. **IMF World Economic Outlook (April 2026 update).** Global growth projection of 3.2%; UK at 1.4%; India at 6.5%; US at 1.9%. https://www.imf.org/en/Publications/WEO
2. **Bank of England Monetary Policy Report (May 2026).** UK Bank Rate at 4.00% after gradual easing cycle; CPI expected to converge to 2% target by Q4 2027. https://www.bankofengland.co.uk/monetary-policy-report
3. **MSCI Emerging Markets vs Developed Markets valuation gap (Q1 2026).** EM 12-month forward P/E of ~12.4x vs DM ~18.7x — a ~33% discount, roughly one standard deviation wide of the 20-year average.
4. **AQR Capital Management (2025).** "Value vs Growth: The Case for a Tilt." — Quantifies the value-premium opportunity given still-elevated growth multiples.
5. **Research Affiliates Asset Allocation Interactive (2026).** 10-year forecasts (real, USD): EM equity 6.8%, EAFE 4.9%, US large cap 1.3%. https://www.researchaffiliates.com/asset-allocation-interactive

These inform the five investor views documented in `portfolio/views.md`.

---

## 6. Historical Return Data

1. **MSCI Index Performance (monthly returns, 1995–2025).** MSCI World, MSCI EM, MSCI USA, MSCI UK, MSCI World Small Cap, MSCI World Quality / Value / Momentum. https://www.msci.com/end-of-day-data-search
2. **FTSE Russell — FTSE 100 and FTSE All-Share total return history.** https://www.ftserussell.com/products/indices/uk
3. **Bloomberg Barclays Global Aggregate (GBP-hedged) total return series.** Used for the global bond covariance estimate.
4. **Bank of England — Gilt yield curve historical data.** https://www.bankofengland.co.uk/statistics/yield-curves — Underpins UK short-gilt return and vol estimates.
5. **LBMA — Gold spot price history (GBP).** https://www.lbma.org.uk/prices-and-data — For the gold/commodity correlation block.
6. **S&P Dow Jones Indices — S&P Global Infrastructure Index Factsheet.** https://www.spglobal.com/spdji/en/indices/equity/sp-global-infrastructure-index/

Long-run estimates (annualised, GBP terms) applied in `bl_model.py` are summarised in `portfolio/universe.md` and were sanity-checked against the DMS Yearbook plus Vanguard CMAs.

---

## 7. UK Platforms, ISA Rules, Tax

1. **HMRC ISA Manual.** https://www.gov.uk/guidance/individual-savings-accounts-isa-manager-guidance — £20,000 annual subscription limit (2026/27).
2. **FSCS Investment Protection.** https://www.fscs.org.uk/what-we-cover/investments/ — £85,000 per person per authorised firm.
3. **US-Ireland Double Taxation Treaty.** Reduces US withholding tax on dividends paid to Irish-domiciled funds from 30% to 15%. https://www.irs.gov/pub/irs-trty/ireland.pdf
4. **Platform fee comparisons (Money to the Masses, Apr 2026).** https://moneytothemasses.com/best-of/best-stocks-and-shares-isa
