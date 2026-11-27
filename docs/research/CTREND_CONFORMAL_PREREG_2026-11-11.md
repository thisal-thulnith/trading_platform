# Pre-registered additions: CTREND factor and conformal abstention (2026-11-11)

Status: **fixed before either addition was run on real data.** Written after the execution re-test
(docs/research/EXECUTION_PREREG_2026-11-11.md) finished; its results are the only new numbers known: the selected
configuration is **A|taker|band0** (LightGBM h = 3, daily, long–short, k = 5, next-open taker fills, no band), net
Sharpe 1.46, DSR p = 0.246 with N = 156, not promoted. The CTREND and conformal code was checked only on synthetic data
(tests/test_quant_lab_ctrend_conformal.py). After this file was written it was not edited; results are appended to
docs/QUANT_PLAN.md and the full record is in `backend/data/quant_lab/ctrend_conformal_report.json` and `trials.jsonl`.

Code: `backend/app/quant/lab/ctrend.py`, `conformal.py`, `retest_b.py`
(`uv run python -m app.quant.lab.retest ctrend_conformal`).

Common to both: the base book is A|taker|band0 exactly as in the execution re-test (next-open taker fills, calendar
schedule anchored 2021-01-01, lab-2 weights, caps, vol target and turnover limit). Stresses of every new book: costs
×2, one more day of delay, and fills at the D+1 VWAP **marked at the close** (the corrected convention from the
execution re-test; VWAP-to-VWAP marks smooth returns). Stresses are logged as `stress`, not as trials.

## B1 — CTREND (Fieberg et al., JFQA 2025)
**Indicators (20), each from data dated ≤ D:** close / SMA(n) − 1 for n ∈ {3, 5, 10, 20, 50, 100, 200}; log(3-day mean
quote volume / n-day mean) for n ∈ {10, 20, 50, 100, 200}; RSI(14, Wilder); stochastic %K(14) and %D(3); CCI(20)
(rolling std instead of mean absolute deviation); MACD(12, 26) / close; MACD histogram (signal 9) / close; Bollinger
%B(20, 2σ); Chaikin money flow(20).

**Aggregation:** each indicator is ranked across the universe on D (centred rank, missing = 0). An elastic net
(scikit-learn `ElasticNetCV`, l1_ratio 0.5, 30 alphas, alpha chosen by a 5-fold time-ordered split inside the training
rows) maps them to the centred cross-sectional rank of the forward **7-day** return. Refit **monthly** on an expanding
window from 2020-01-01 (first fit on 2019 data); the fit used in month m is trained on rows dated ≤ (first day of m)
− 8 days, so every training label is realised before the month starts; ≥ 1,000 training rows required. CTREND on D is
the prediction from D's indicator ranks. Its sign is +1 by construction (no in-sample orientation).

**Reported:** the same IC report as the other factors (IC at 1/3/7 days with Newey–West t, by year, by BTC regime,
decay, rank autocorrelation, quintile long–short daily and weekly after costs), on the walk-forward span (2020-01 →)
and on the lab's OOS window (2021-01 →); the elastic net's coefficients over time.

**Factor pass test (TECH_PLAN 2.2):** on 2021-01-01 → end, rank IC with 7-day returns has NW t > 3, **and** the
weekly-rebalanced top-minus-bottom quintile (factor-report method: equal weights, taker fee + liquidity slippage, no
funding) has a positive net mean return in ≥ 4 of the 5 full years 2021–2025.

**Model-ladder test:** CTREND is added as a 19th input to each rung (composite — through the same training-dates IC
screen —, ridge, LightGBM) at h = 3 and h = 7, with the identical quarterly walk-forward from 2021-01-01. For each of
the 6 rung × horizon pairs, "CTREND improves the rung" iff the daily OOS rank-IC difference (with − without) has mean
> 0 and paired NW t ≥ 1.65 (the ladder's own rule). These 6 comparisons are logged as `ladder_ic`.

**Portfolio trial (1):** A|taker|band0 with the LightGBM h = 3 predictions that include CTREND.

## B2 — Conformal abstention
For every (D, coin) with a lab-2 LightGBM h = 3 walk-forward prediction:
- point prediction p = the coin's centred cross-sectional rank of the model score on D (in [−0.5, 0.5]);
- outcome y = the centred cross-sectional rank of its forward 3-day return; residual e = y − p (known at D + 3);
- local scale σ_i(D) = root mean square of the coin's own residuals on rows dated in [D − 3 − 60, D − 3 − 1], at least
  20 of them, otherwise the cross-sectional median σ on D;
- score s = |e| / σ_i(row date); q(D) = the ⌈(n + 1)(1 − α)⌉-th smallest score among the n rows dated in
  [D − 3 − 90, D − 3 − 1] (rolling split conformal on past residuals only; ≥ 100 rows);
- interval [p − q(D) σ_i(D), p + q(D) σ_i(D)].

**Abstention rule:** a coin may be held long only if its interval lies entirely above the neutral rank 0, short only if
entirely below; otherwise the book abstains on it (no position). The top-k longs / bottom-k shorts are then chosen
among the eligible coins (fewer positions when fewer are eligible; weights, caps and vol target as usual).

**A priori expectation (stated before running):** with an OOS rank IC of about 0.14, the residual of a predicted rank
has a standard deviation of about √(2 · (1/12) · (1 − 0.14)) ≈ 0.38, so a 90% interval has a half-width of roughly
0.6 — wider than the whole half-range of ranks. A 90% interval can therefore almost never exclude the neutral rank,
and a 90% abstaining book would be (almost) empty. So the books use **α = 0.25 and α = 0.5** (two portfolio trials);
coverage is reported at α ∈ {0.1, 0.25, 0.5}.

**Coverage test (TECH_PLAN 3.2):** the 90% intervals (α = 0.1) cover 90% ± 3% of OOS rows (all rows from the first
date with a calibrated q). Coverage at α = 0.25 and 0.5 reported against 75% / 50%.

**Abstention test:** each abstaining book vs the same configuration without abstention over the same window (from the
first calibrated date). "Abstention beats" iff the abstaining book's net Sharpe is higher with a one-sided paired
circular block bootstrap p < 0.05 (10-day blocks, 2,000 resamples, seed 0).

## Trials, gate and promotion
New `strategy` trials: 1 (CTREND book) + 2 (conformal books) = **3**, so N becomes 159 if nothing else is logged.
Each of the three books faces the gate of the execution re-test: G1 DSR p < 0.05 with the updated N and V[SR]; G2 PBO
< 0.3 by CSCV over the 15 configurations of this re-test round (step A's 12 + these 3) on their common window; G3 net
Sharpe above the equal-weight universe over the same window with next-open fills; G4 costs ×2 PSR ≥ 0.95; G5 VWAP fill
(close-marked) PSR ≥ 0.95.

If one or more pass, the one with the lowest DSR p is promoted — written to model.json only together with live
support for its inputs (the CTREND fit / the conformal residual history) so that `live_ranking()` reproduces it. If
none passes, nothing is promoted and model.json is not written. Step B never changes step A's result.
