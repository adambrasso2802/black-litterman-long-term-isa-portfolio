# Black-Litterman Investor Views

These five views translate the macro research in `research/macro_views_research.md` into the matrices that feed the BL posterior. Views are encoded over the 11-asset universe of `portfolio/universe.md` in the order:

`[VWRP, VUSA, VUKE, VFEM, WLDS, IWVL, IWQU, INFR, AGGG, IGLS, SGLN]`

Each row of `P` is a portfolio of assets whose expected return (in excess of risk-free) the view pins to the corresponding entry of `Q`. Relative views sum to zero. The diagonal of `Ω` encodes the view's uncertainty (smaller = more confident).

---

## View 1 — Emerging markets > developed-ex-US equities by **+2.0% p.a.**

**Economic rationale.** EM trades at a 12.4x forward P/E vs ~18.7x for MSCI World ex-US — a 33% discount, roughly one standard deviation wider than the 20-year average. India / SE Asia demographics, a stabilising RMB, and easing US-rate headwinds underpin a partial mean reversion. The 2.0% spread sits below the Research Affiliates 10-year forecast (RA puts EM 6.8% vs EAFE 4.9%, a 1.9% gap, before any USD repricing).

**Confidence:** Medium  →  Ω₁ = 0.0015

**P row:** long VFEM (+1.0), short VWRP (−1.0) (VWRP is used as the global-ex-US proxy here for simplicity; the EM weight inside VWRP is small enough that the spread is dominated by the directional EM tilt).

**Q:** +0.020

---

## View 2 — Value > Growth (global) by **+1.5% p.a.**

**Economic rationale.** Russell 1000 Value vs Growth P/B spread sits at the 92nd historical percentile. The 5-year mean-reverting value premium under such conditions has averaged ~3% p.a. historically (Asness et al., 2015). We discount this to 1.5% to reflect the structural argument that intangibles-heavy growth firms warrant some persistent premium.

**Confidence:** Medium  →  Ω₂ = 0.0015

**P row:** long IWVL (+1.0), short VWRP (−1.0) (using the world index as the "neutral" benchmark — a positive Q here means IWVL beats the cap-weighted world).

**Q:** +0.015

---

## View 3 — UK equities > eurozone equities by **+1.0% p.a.**

**Economic rationale.** UK forward P/E of 11.5x vs eurozone ~14.5x. Higher dividend yield, sterling at ~10% PPP discount to USD, fading domestic political risk. View kept modest because the UK has carried a persistent governance / growth discount for several years.

**Confidence:** Low–medium  →  Ω₃ = 0.0030

**P row:** long VUKE (+1.0), short VWRP (−1.0). (We use VWRP again as the cap-weighted neutral; the eurozone is a sub-component of VWRP.)

**Q:** +0.010

---

## View 4 — Global infrastructure > global aggregate bonds by **+3.0% p.a.**

**Economic rationale.** Listed infra cash-yields (~6%) exceed nominal bond yields (~4%) and embed an inflation pass-through; bonds do not. Long-cycle infra has produced ~3% of excess return over global bonds historically (Bitsch, Buchner & Kaserer, 2010; S&P GIIC research).

**Confidence:** Medium  →  Ω₄ = 0.0015

**P row:** long INFR (+1.0), short AGGG (−1.0).

**Q:** +0.030

---

## View 5 — Quality factor > MSCI World by **+0.5% p.a.**

**Economic rationale.** Quality has delivered ~150 bps of historical outperformance with lower drawdowns. We discount to 50 bps because the factor itself is now somewhat crowded and trades at a valuation premium. Including it gives the BL system a defensive equity tilt that complements the value position.

**Confidence:** Low–medium  →  Ω₅ = 0.0030

**P row:** long IWQU (+1.0), short VWRP (−1.0).

**Q:** +0.005

---

## Encoded matrices

`P` (5 × 11):

```
            VWRP  VUSA  VUKE  VFEM  WLDS  IWVL  IWQU  INFR  AGGG  IGLS  SGLN
View 1:     -1     0     0    +1     0     0     0     0     0     0     0
View 2:     -1     0     0     0     0    +1     0     0     0     0     0
View 3:     -1     0    +1     0     0     0     0     0     0     0     0
View 4:      0     0     0     0     0     0     0    +1    -1     0     0
View 5:     -1     0     0     0     0     0    +1     0     0     0     0
```

`Q` (5 × 1): `[0.020, 0.015, 0.010, 0.030, 0.005]`

`Ω` (5 × 5 diagonal): `diag(0.0015, 0.0015, 0.0030, 0.0015, 0.0030)`

`τ` = 0.05.

These values are reproduced verbatim in `portfolio/bl_model.py` (the `CONFIG['views']` block) and the file is the single source of truth for any future tuning.
