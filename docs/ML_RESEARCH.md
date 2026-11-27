# ML Research Plan

Last updated: 2026-11-01. Read with [RESEARCH.md](RESEARCH.md) §2.6, §2.12, §5.

**Prior expectation:** crypto returns at 1h to 1d horizons are close to unpredictable after costs. Realistic out-of-sample directional AUC is 0.51 to 0.55. Volatility is much more predictable (R² 0.3 to 0.6 for realized-vol forecasts is typical). The goal is a calibrated, honestly evaluated probability that adds information over simple baselines. If nothing passes the acceptance gate, the platform ships rules-based signals with event-study statistics and **no ML score**.

---

## 1. Prediction targets

| Target | Definition | Use | Priority |
|---|---|---|---|
| **Triple-barrier label** | From entry at close of bar t: upper barrier `+u·σ_t`, lower `−d·σ_t` (σ_t = EWMA vol of log returns, or ATR/price), vertical barrier at `t+H`. Label = +1 (upper hit first), −1 (lower first), 0 or sign(return) at vertical. Use bar highs and lows to detect touches; **if both are touched within one bar, label it ambiguous and drop it, or resolve with lower-timeframe data** | Primary directional classifier; aligns with stop/target UX | P0 |
| **Meta-label** | Given a primary rule signal (e.g., TSMOM long, BOS long), predict P(triple-barrier hit is profitable) | Filter/size rule signals | P1 |
| **Forward return sign** | `sign(log(c[t+H]/c[t]) − cost)` | Baseline target, easy to explain | P0 |
| **Forward realized volatility** | `RV_{t→t+H} = sqrt(Σ r²)` on intrabar returns, log-transformed | Sizing, stop width, regime | P0 (most reliably learnable) |
| Forward return quantiles | 10/50/90% quantiles of `r_{t→t+H}` | Intervals in UI | P2 |

**Horizons:** 4h and 1d bars, horizons `H ∈ {6 bars of 4h (1 day), 1d, 3d, 7d}`. The 1h horizon is optional; anything shorter is not worth it given costs and latency. Barrier multiples to start with: `u = d ∈ {1.0, 1.5, 2.0}·σ`. Each combination tried counts toward the trial count.

**Universe:** point-in-time top-N by trailing 30-day quote volume on Binance USDⓈ-M (N = 20 to 50), rebuilt monthly using only data before the rebuild date, and including later-delisted symbols.

---

## 2. Features (strict as-of alignment)

Every feature row at decision time `t` (the close of bar t) may use only data with `available_at ≤ t`. Implement with `pandas.merge_asof(direction="backward")` on `available_at`, and add a unit test that shuffles future rows to confirm features do not change.

| Group | Features | As-of notes |
|---|---|---|
| Returns / momentum | log returns over 1, 3, 6, 12, 24, 72, 168, 720 bars; vol-scaled TSMOM `r_k/σ_k`; distance from 20/50/200 MA in ATR units; MA slopes | Closed bars only |
| Volatility | Parkinson, Garman-Klass, Yang-Zhang over several windows; ATR%; vol-of-vol; HAR components (1d, 1w, 1m RV); vol percentile vs. 1y | |
| Range / structure | position in N-bar range; distance to PDH/PDL/PWH/PWL in ATR; days since last BOS/CHoCH; trend state from pivots (TRADING_CONCEPTS §1); active FVG/OB count and distance | **Use `confirmed_at`, never `formed_at`** |
| Volume / flow | volume z-score; taker buy ratio (from kline `takerBuyBaseVolume`); CVD slope from aggTrades; OBV slope | Kline taker volume is available at bar close |
| Derivatives | funding (last settled + predicted), funding z-score vs. 30d, cumulative 7d funding; basis (mark vs. index); ΔOI over 1h/4h/24h (notional); OI/market-cap; long/short ratio; liquidation notional (long vs. short) | Funding settles at interval end; the predicted rate is a snapshot, so use the snapshot as of t. **OI history from self-collected snapshots (Binance gives only 30 days); Bybit backfill introduces a venue mismatch, so flag it** |
| Cross-asset | BTC return and vol (for alts), ETH/BTC ratio, beta to BTC (rolling), BTC dominance (CoinGecko, daily, lagged 1 day) | Align daily data to the next bar |
| Calendar | hour-of-day, day-of-week (cyclic encodings), session flags, hours to next funding | Deterministic |
| Regime | vol-regime bucket; HMM **filtered** state probabilities (refit walk-forward) | Never use smoothed probabilities |
| Sentiment (P2) | Fear & Greed (daily, lag 1 day), news count and sentiment per coin over 24h by `first_seen_at` | LLM-scored sentiment is subject to pretraining look-ahead before the model cutoff |

**Transforms:** rank or rolling z-scores computed with trailing windows only. Fractional differentiation (d ≈ 0.3 to 0.5) for price-level features. No global scalers. Fit scalers inside each training fold.

---

## 3. Validation protocol

1. **Purged k-fold with embargo** (López de Prado 2018, ch. 7) for model selection: k = 5 to 6 contiguous folds. Purge training samples whose label interval `[t, t1]` overlaps the test fold. Embargo `h = max(H, 1% of samples)` after each test fold. Implement from scratch or use `mlfinlab`-style code; check licensing (the mlfinlab package went commercial, so we write our own ~50-line `PurgedKFold`).
2. **Combinatorial purged CV (CPCV)** for estimating PBO and the distribution of Sharpe across paths (Bailey et al. 2017).
3. **Walk-forward (anchored or rolling) for the final estimate:** train on `[start, T]`, test `(T+embargo, T+Δ]`, roll monthly. This mimics production retraining. **Final holdout:** the most recent 6 to 12 months, untouched until the end and evaluated once.
4. **Trial registry:** log every configuration (features, target, hyperparameters, seed) with metrics to MLflow or a table. N_trials feeds the deflated Sharpe ratio.
5. **Costs in evaluation:** taker fee 0.05% per side on perps (Binance VIP0; verify), plus a slippage model (half-spread + k·σ·sqrt(size/ADV)), plus actual funding paid or received over the holding period.
6. **Report per fold, per year, per regime, per symbol bucket.** Aggregates hide failure.

---

## 4. Baselines (every model must beat all of these)

| Baseline | Definition |
|---|---|
| Always-flat | Zero exposure (Sharpe 0, zero cost). Strategies must clear it after costs |
| Buy & hold | Long the asset (or the equal-weight universe) |
| Climatology / base rate | Predict the training-set class frequency (probabilistic baseline for Brier/log loss) |
| Momentum sign | `sign(r_{t−k..t})` for k ∈ {1d, 7d, 30d}; vol-scaled TSMOM |
| Logistic regression | L2-regularized on ~10 standardized features (momentum, vol, funding) |
| HAR-RV | For volatility targets (Corsi 2009) |
| Persistence | For vol: `RV_{t+H} = RV_t` |

---

## 5. Candidate models (ranked by expected value-for-effort on CPU)

1. **LightGBM** (classification for triple-barrier/sign; regression for log-RV). Strong defaults: `num_leaves 15–63`, `min_data_in_leaf ≥ 200`, `feature_fraction 0.5–0.8`, `bagging`, `learning_rate 0.02–0.05`, early stopping on a purged validation fold. Use monotone constraints where theory is clear. Use sample weights by label uniqueness (LdP ch. 4). Tune with Optuna (≤ 100 trials, all registered).
2. **Regularized linear / logistic + interactions.** Often within noise of GBDT on returns; more stable and interpretable.
3. **CatBoost / XGBoost:** alternatives if LightGBM overfits; similar expected performance.
4. **HAR / GARCH-family and LightGBM for vol** (combine as features).
5. **Research track (GPU recommended):** DLinear/NLinear (Zeng et al. 2023), the baseline any deep model must beat; then PatchTST, N-BEATS, TFT for multi-horizon vol and quantiles. Use `neuralforecast` (Nixtla, Apache-2.0) or `pytorch-forecasting`. Expect no directional gain; possible gain on volatility.
6. **Not recommended:** RL agents, LSTM price-level forecasting (a classic leakage-plus-persistence illusion), zero-shot foundation models for returns, and LLMs as predictors.

---

## 6. Calibration and probabilistic evaluation

- Calibrate on a **time-ordered calibration slice** inside each training window (never on the test fold). Use isotonic regression when there are ≥ ~1000 calibration samples, otherwise Platt/sigmoid (scikit-learn `CalibratedClassifierCV` with a pre-fit model and a manual time split; never its default shuffled CV).
- **Metrics:** log loss, Brier score, and Brier skill score vs. climatology; ECE plus **reliability diagrams** (10 to 15 quantile bins) per fold; AUC as secondary. For quantiles: pinball loss and interval coverage.
- **Conformal intervals** for vol and return quantiles, using adaptive conformal inference (Gibbs & Candès 2021) to keep coverage under drift.
- UI shows calibrated probability with an interval or bin reliability, never raw model scores.

---

## 7. Acceptance criteria (all must pass before a model is shown to users)

1. **Beats every baseline out of sample after costs** on the walk-forward periods, in **≥ 70% of folds**, and on the final holdout.
2. **Probabilistic skill:** Brier skill score > 0 vs. climatology on the holdout, with a bootstrap 95% CI that excludes 0 (block bootstrap; block ≥ horizon). Calibration ECE ≤ 0.03.
3. **Deflated Sharpe ratio:** DSR probability > 0.95 (i.e., the Sharpe of the derived strategy exceeds the expected maximum Sharpe under N_trials at 95%), using the observed skew/kurtosis (Bailey & López de Prado 2014). At minimum, DSR > 0 is required to show it as "experimental".
4. **PBO < 0.2** via CSCV.
5. **Stability:** performance not driven by one symbol, year, or regime (drop-one analyses). Feature importances broadly stable across folds. Neighbouring hyperparameters perform similarly.
6. **Leakage tests pass:** shuffled-future test; a "random label" test giving AUC ≈ 0.5; and performance does not rise implausibly at short horizons (AUC > 0.6 at 1h is a red flag for leakage).
7. **Reproducibility:** pinned data snapshot hash, code commit, seed; retrain reproduces metrics within tolerance.

Failing models stay in the research registry. Do not "fix" them by tuning on the holdout.

---

## 8. Explainability: SHAP caveats

- TreeSHAP (Lundberg & Lee 2017; Lundberg et al. 2020) gives fast, exact attributions for trees. Show the top-k feature contributions per prediction as "what drove this score".
- **Caveats:** SHAP explains the *model*, not the market, and is not causal. With correlated features (many momentum windows), credit is split arbitrarily and the interventional and observational variants disagree (Kumar et al. 2020). Attributions on a near-noise model are noise. Aggregate over folds and show stability. Use grouped attributions (e.g., "momentum group", "derivatives group") in the UI.
- Prefer permutation importance on purged validation folds (or MDA, LdP ch. 8) for feature selection, not in-sample SHAP.

---

## 9. Drift monitoring and retraining

- **Population Stability Index** per feature, weekly, comparing the live distribution to the training reference (10 quantile bins from training): `PSI = Σ (a_i − e_i)·ln(a_i/e_i)`. Industry heuristics: < 0.1 stable, 0.1 to 0.25 moderate, > 0.25 significant (these are conventions, not statistical tests). Also track prediction-distribution PSI and label-rate drift.
- **Performance monitoring:** rolling Brier/log loss and a rolling reliability diagram on matured labels (labels mature after H). Alert if the rolling 30-day Brier skill drops below 0 or ECE exceeds 0.05.
- **Retraining cadence:** walk-forward monthly retrain (matches validation). Weekly recalibration only, if the calibration set is large enough. Do an emergency retrain or **auto-disable** on a drift alert, falling back to rules-based display. Every retrain passes the acceptance gate on the latest walk-forward fold before promotion (champion/challenger).
- Model registry with versions. Each user-visible score records its model version.

---

## 10. Compute requirements

| Task | Hardware | Time (estimate) |
|---|---|---|
| Feature build, 50 symbols × 6 yrs × 1h bars (~2.6M rows) | 4 to 8 vCPU, 16 GB RAM, Polars/pandas | Minutes |
| LightGBM single fit (2.6M × 150) | 8 vCPU | 1 to 5 min |
| Purged 6-fold × 100 Optuna trials | 8 to 16 vCPU | 2 to 8 h (parallelizable) |
| CPCV (e.g., N=10 groups, k=2: 45 splits) | 16 vCPU | Hours |
| Monthly walk-forward retrain + calibration | 4 to 8 vCPU | < 30 min |
| Online inference (per bar close, all symbols) | 1 vCPU | < 1 s |
| Deep models (PatchTST/TFT), research | 1 consumer/cloud GPU (≥ 16 GB) | Hours per configuration |
| Storage for features/labels | Parquet, partitioned by symbol/month | ~1 to 5 GB |

---

## References
- López de Prado, M. (2018) *Advances in Financial Machine Learning*, Wiley (triple-barrier ch. 3; sample weights ch. 4; purged CV ch. 7; feature importance ch. 8).
- Bailey & López de Prado (2014) The Deflated Sharpe Ratio: https://ssrn.com/abstract=2460551
- Bailey, Borwein, López de Prado, Zhu (2017) The Probability of Backtest Overfitting: https://ssrn.com/abstract=2326253
- Ke et al. (2017) LightGBM: https://papers.nips.cc/paper/2017/hash/6449f44a102fde848669bdd9eb6b76fa-Abstract.html
- Zeng et al. (2023) Are Transformers Effective for Time Series Forecasting?: https://arxiv.org/abs/2205.13504
- Nie et al. (2023) PatchTST: https://arxiv.org/abs/2211.14730
- Lim et al. (2021) Temporal Fusion Transformers: https://arxiv.org/abs/1912.09363
- Oreshkin et al. (2020) N-BEATS: https://arxiv.org/abs/1905.10437
- Corsi (2009) HAR-RV: https://doi.org/10.1093/jjfinec/nbp001
- Gu, Kelly, Xiu (2020) Empirical Asset Pricing via Machine Learning: https://doi.org/10.1093/rfs/hhaa009
- Niculescu-Mizil & Caruana (2005) Predicting good probabilities: https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf
- Guo et al. (2017) On Calibration of Modern Neural Networks: https://arxiv.org/abs/1706.04599
- Gneiting & Raftery (2007) Strictly Proper Scoring Rules: https://doi.org/10.1198/016214506000001437
- Gibbs & Candès (2021) Adaptive Conformal Inference: https://arxiv.org/abs/2106.00170
- Lundberg & Lee (2017) SHAP: https://arxiv.org/abs/1705.07874
- Lundberg et al. (2020) TreeSHAP: https://doi.org/10.1038/s42256-019-0138-9
- Kumar et al. (2020) Problems with Shapley-value-based explanations: https://arxiv.org/abs/2002.11097
- Yurdakul (2018) Statistical properties of the Population Stability Index: https://scholarworks.wmich.edu/dissertations/3208/
- scikit-learn probability calibration: https://scikit-learn.org/stable/modules/calibration.html
- Nixtla neuralforecast: https://github.com/Nixtla/neuralforecast

---

## 11. Results: meta-label filter on engine setups (2026-11-11)

Code: `backend/app/ml/meta_label.py`. Run: `uv run python -m app.ml.meta_label --tf 4h --days 1460 --symbols ...`.

**What it is.** A secondary model (López de Prado, AFML §3.6) on top of the rule engine. The primary "model" is the engine itself: every confirmed and validated setup that `app.backtest.engine.generate_setups` produces, which is the same code path as live, regenerated with the current strategy code. The live exit profile `edge_tp2r` is applied (single limit at the zone edge, the strategy's stop, one take-profit at +2R). The label is 1 if `TradeSim` (the backtester's simulator, with 0.05% taker fee and 2 bps slippage) hits +2R before the stop, and 0 on a stop or time exit. Unfilled setups are dropped. Features are the f1 set (without taker imbalance, which the long CSV history lacks), direction-aligned copies of them, and setup-level features: strategy, direction, regime, validation quality, confluence bias, supporting/opposing evidence families, stop distance in ATR, distance from price to the limit, R to the nearest opposing S/R level, higher-timeframe trend agreement, last and 3-day mean perp funding (signed per side), and hour/day. The model is LightGBM with isotonic calibration on the last 20% of each training window. It is compared with the training base rate and an L2 logistic regression, using purged walk-forward CV (5 folds, 24-bar embargo). The take/skip threshold for test fold k is chosen on folds < k only, so fold 0 is validation only.

**Acceptance policy.** These are the `train.py` criteria adapted to a filter, plus the §7.2 ECE gate:
- log loss beats the base rate on ≥ 70% of folds;
- mean AUC ≥ 0.52 and above the logistic baseline;
- the filtered trades number ≥ 30, have a bootstrap 95% CI lower bound of expectancy > 0, and beat taking every setup;
- ECE ≤ 0.03.

DSR, PBO, drop-one stability and a final holdout (§7.3 to §7.5) were not run.

### 4h: 12 symbols, Nov 2022 to Nov 2026. REJECTED

The symbols are BTC, ETH, SOL, XRP, BNB, DOGE, LINK, AVAX, AAVE, ADA, ALGO and APT, from the `data/history/4h` CSVs. These are survivors, and the set was scaled down from 35 because setup generation takes about 11 min per symbol. The run produced 11,252 engine setups and 8,316 filled and resolved trades, with a base rate of 35.9% hitting +2R first.

Out of sample, folds 1 to 4 (Nov 2024 to Nov 2026; fold 0, May to Nov 2024, is validation only), R after costs:

| | Trades | +2R hit | Win (R>0) | Expectancy | 95% CI (iid bootstrap) | Total R |
|---|---|---|---|---|---|---|
| All setups | 4,107 | 35.5% | 36.3% | +0.017R | [−0.027, +0.061] | +69.1R |
| Meta filter (LightGBM, p ≥ 0.278) | 3,709 | 36.7% | 37.2% | +0.043R | [−0.004, +0.088] | +160.7R |
| Logistic filter (baseline) | 3,578 | 37.0% | 37.3% | +0.048R | [+0.003, +0.096] | +171.4R |

Per fold (meta filter vs all setups): +0.122R vs +0.077R, +0.161R vs +0.145R, −0.080R vs −0.106R, and −0.017R vs −0.038R. The filter helps a little in every fold, but it drops only about 10% of setups, and it cannot turn the losing periods (folds 3 and 4, Oct 2025 onward) positive.

Mean AUC was 0.549 for LightGBM and 0.547 for logistic regression. Calibrated log loss beat the base rate in only 1 of 5 folds. Pooled OOS ECE was 0.044. The AUC with permuted labels was 0.488, so there is no sign of leakage.

Failed criteria:
- log loss vs the base rate (20% of folds, 70% needed);
- expectancy CI lower bound > 0;
- ECE ≤ 0.03.

The logistic baseline does as well as LightGBM, or slightly better, as a filter. Whatever ranking signal exists is weak and roughly linear. No model was saved, and `score()` returns `accepted_model: false`.

### 1h: 19 symbols, 540 days (May 2025 to Nov 2026). REJECTED

8,886 filled trades from all 19 symbols in the candles table; 33.3% reached +2R first. Run 20261111144210.

| OOS folds 1 to 4 | Trades | +2R hit | Win (R>0) | Expectancy | 95% CI | Total R |
|---|---|---|---|---|---|---|
| All setups | 3,968 | 33.9% | 36.2% | −0.029R | [−0.073, +0.014] | −116.0R |
| Meta filter (LightGBM) | 2,132 | 37.8% | 38.7% | +0.053R | [−0.009, +0.116] | +113.9R |
| Logistic filter | 1,001 | 35.7% | 36.1% | −0.028R | [−0.113, +0.063] | −28.4R |

Per fold (filter vs all setups): +0.085R on 8 kept trades vs −0.166R; +0.187R vs +0.080R; −0.004R vs −0.021R;
+0.031R vs +0.004R. Mean AUC was 0.522 for LightGBM, **below** logistic regression's 0.530; log loss beat the base rate in
1 of 5 folds (20%); ECE was 0.036; the shuffled-label control gave 0.504 (and 0.551 in fold 0, above the real model there).
The filter's +114R swing looks large but its CI includes zero, the model barely separates winners from losers, and fold 1
kept only 8 trades — it is not trustworthy. Rejected: no model file, `score()` returns `p: null`.

### Reading

- The engine's setups have a small edge over random (AUC about 0.55) for "will this reach 2R", but not one that is calibrated or economically significant after costs. This matches the prior at the top of this document.
- Neither timeframe passes (4h and the full 1h run), so the desk shows no meta-label probability. `score()` returns `p: null` together with the failed criteria.

**Caveats:**
- Setups are labelled independently, so the engine's one-position-per-symbol/strategy rule is not applied and overlapping trades are double-counted.
- The CIs use an iid bootstrap over correlated trades and are optimistic.
- Isotonic calibration is fitted on slices of 200 to 1,500 samples, below the ≥ 1,000 rule of thumb in §6 for the smaller folds.
- Backtest snapshots have no order book or derivatives data, unlike live.

## 12. Upgrade research: what serious systems do, and what it bought us (2026-11-24)

### What the literature and current systems say
- **Cross-sectional ML on many technical signals works in crypto, modestly.** Fieberg, Liedtke, Poddig, Walker & Zaremba,
  "A Trend Factor for the Cross-Section of Cryptocurrency Returns" (JFQA, 2025): combining many classic indicators
  (price vs moving averages over several windows, volume, oscillators) with ML predicts returns across 3,000+ coins and
  survives 53,920 robustness variants.
- **Model complexity adds little.** Cakici, Shahzad, Będowska-Sójka & Zaremba (IRFA, 2024): many ML models predict the
  crypto cross-section, the edge sits mostly in the long leg, and fancier models add little over simple ones. This
  matches our own result that the blend of a linear model and LightGBM is usually chosen.
- **Multi-agent LLM desks** (TradingAgents, Xiao et al., 2024-25): analysts by specialty (fundamental, sentiment,
  technical), a bull/bear debate, a trader and a risk team. Their reported gains are backtests, not live results. The
  platform's desk already has this shape; what it lacked was a lead who owns the computational evidence.
- **Perpetual-futures data**: deviations of perps from fair value are large in crypto and are predicted by past returns
  (He, Manela, Ross & von Wachter, "Fundamentals of Perpetual Futures"); open interest is misquoted on some exchanges
  (Giagkiozis & Said, Ledger 2024), so it is used with care. Funding is already a feature.

### What was tested (app/ml/upgrade_eval.py, data/models/upgrade_eval.json)
Same walk-forward, same 295,117 liquid-coin rows. Added 21 CTREND-style indicators (app/ml/technicals.py: price vs
3/5/10/20/100-day averages, volume vs 5/20/50-day averages, stochastic 14/30, RSI 14, MACD and histogram, Bollinger %B
and width, CCI 20, Chaikin money flow, the largest daily return in 30 days, 30-day skew, 3-day return, share of down
days), each raw and ranked within the day (98 features instead of 56), and LightGBM averaged over three seeds.

AUC, chosen on 2020-24, reported on 2025-26:

| Target | Today's best (2020-24 → 2025-26) | Best with the upgrade | Gain 2020-24 | Gain 2025-26 |
|---|---|---|---|---|
| Long hits target first | blend 0.614 → 0.671 | blend + bagged LightGBM 0.619 → 0.674 | +0.005 | +0.003 |
| Short hits target first | blend 0.679 → 0.646 | blend with indicators 0.681 → 0.651 | +0.002 | +0.005 |
| 2-ATR drop risk | blend 0.659 → 0.650 | blend with indicators 0.664 → 0.657 | +0.005 | +0.006 |
| Up in 7 days | blend 0.547 → 0.579 | blend with indicators 0.554 → 0.575 | +0.006 | −0.004 |
| Beats the median coin | blend 0.566 → 0.604 | blend + bagged LightGBM 0.570 → 0.601 | +0.003 | −0.003 |

Reading: the indicators win on the selection years for every target, so they were adopted as candidates. On the
unseen years the gain is small (+0.3 to +0.6 AUC points on the three trade targets, a hair worse on the two direction
targets). This is what the literature predicts: the momentum, volatility and pattern features already carry most of
what classic indicators know. No change here moves a signal anywhere near a 90% win rate.

## 13. The unified engine: every method in one model (2026-11-24)

### Why one model, not opinions side by side
Running a "concept" analysis and a "machine" analysis in parallel and then comparing them leaves the hardest part,
how much each one is worth, to a human. The industry answer is to put every signal into one learner and let it weigh
them by their measured value:
- **Alpha combination.** Grinold & Kahn (Active Portfolio Management): combine signals by their measured information
  content, not by story. A tree model trained on all of them does this weighing per coin and per market state.
- **Concepts are features, not rules.** Lo, Mamaysky & Wang (JF 2000) found that classic chart patterns carry some
  information when measured systematically. Osler (JF 2003) showed why support, resistance and round numbers matter:
  take-profit orders cluster at round numbers and stop-losses just beyond them, so trends tend to reverse at those
  levels and accelerate once they break. Jiang, Kelly & Xiu (JF 2023) showed a model reading chart images predicts
  returns. None of these say a hand-written rule is enough; they say the information is real when it is measured.
- **Meta-labeling** (López de Prado, AFML 2018) splits the side decision from the trade/size decision. Practitioners
  report it is "not a silver bullet": it adds little on top of an already good model. Here the plan models already
  predict the R of each trade directly, so the trade / no-trade decision is a small rule on top of them.
- **Selection bias.** Bailey & López de Prado (deflated Sharpe ratio, 2014; probability of backtest overfitting,
  2015/2017): the best of many backtests is inflated. So the decision layer tries only 8 rules, picks one on
  2020-24, reports all 8 and gives a bootstrap range for the unseen years.

### Architecture (app/ml/unified.py)
1. Features for every coin and day since 2017: market and factors (quant lab), 21 technical indicators
   (technicals.py), 35 human trading concepts (concepts.py: market structure with BOS/CHoCH, premium/discount,
   liquidity sweeps, equal highs/lows, fair value gaps, displacement, Wyckoff springs/upthrusts, VWAP, candles,
   average stack, strength vs BTC, round numbers, long/short checklists), and learned chart patterns (patterns.py).
   All causal; tests/test_concepts.py changes every candle after a day and checks that nothing up to it moves.
2. 18 plan models (style.py): one LightGBM per plan (side x stop 0.75/1.5/2.5 ATR x target 1.5/2/3R, 14 days)
   predicting the realised R after fees. Walk-forward by year, 15-day purge.
3. Decision: score = predicted R (raw) or R relative to the same plan on the other coins (edge); top 1 or top 3
   coins a day; with or without a bar (the median top score of the past year). Coins with ATR above 25% are never
   picked. Chosen on 2020-24 with at least 300 trades.
4. Output: coin, side, plan, levels at the live price, the leverage table, the 0.75-ATR option for 10-20x, and why:
   TreeSHAP contributions of the chosen plan's model summed by concept group. Today's signal, the Signal test and
   Cipher's `tested_signal` tool all read this one engine; the daily refresh re-scores it and the monthly retrain
   relearns it.

### Results (2026-11-24, data to 2026-11-23, 295k liquid coin-days)
**Concepts test: rejected.** Same decision rule, same rows:

| | 2020-24 | 2025-26 (unseen) |
|---|---|---|
| Plan models with the 35 concept features | +0.170R / trade (1,461) | +0.047R (678) |
| Plan models without them | +0.199R | +0.080R |

The concepts lowered the result in both periods: momentum, volatility, ranges and the learned chart patterns already
carry what structure, liquidity and Wyckoff measure, and 35 more inputs add noise. concepts.py stays (tested, causal,
documented) and `unified.USE_CONCEPTS = False`; the human-style reading stays with the AI agents (Vega's team), where
it is an argument to weigh, not a model input.

**Decision rule** (8 tried, chosen on 2020-24 with 300+ trades): score = a coin's predicted R minus the same plan's
median on the other coins that day ("edge"), the single best coin a day, no bar.

| Rule | 2020-24 | 2025-26 |
|---|---|---|
| edge, top 1, no bar (**chosen**) | +0.199R (1,461) | **+0.080R (678), 90% range −0.064 to +0.234** |
| edge, top 3, no bar | +0.160R | +0.140R |
| edge, top 1, bar | +0.112R | +0.150R |
| edge, top 3, bar | +0.081R | +0.274R |
| raw, top 1, no bar | +0.072R | +0.210R |
| raw, top 3, no bar | +0.054R | +0.252R |
| raw, top 1, bar | −0.005R | +0.220R |
| raw, top 3, bar | +0.010R | +0.349R |

Every rule made money on the unseen years, and the ranking flipped: the rule that was best on 2020-24 is the weakest on
2025-26. The chosen rule was NOT changed after seeing this (that would be choosing on the unseen years); the
live Signal test now records its picks. A random liquid coin with the same plan on the same days: −0.035R / −0.058R.
By year: 2021 +0.14, 2022 +0.22, 2023 +0.27, 2024 +0.17, 2025 +0.11, 2026 +0.05. Shorts carried 2025-26 (+0.455R,
51% in profit, 172 trades); longs −0.047R (506). Paper account at 0.5% risk a day, booked at entry: 2025-26 +14.6% a
year, worst drop −20.1%, Sharpe 1.04 (2020-24: +42.1% a year, −24.0%, 2.41). Verdict: tested edge, small, its range
includes zero, and fading in 2026.

## 14. How to combine the machine, other methods and the AI agents (2026-11-26)

The question: the platform has one trained model (the unified engine), several other systems (the 4h setup screener,
the rule-based system account, the pattern scanner, the factor ranking) and the AI agents. What is the right way to
combine them so the signal gets better and not just more complicated?

What the research says, and what we did with each:

1. **One model that sees every input beats several models voting.** Stacking every feature into one learner lets it
   weigh them against each other on the same trades; a vote of separate systems double-counts what they share (most of
   them read the same price trend). This is what the unified engine is (§13). The other systems stay in the Lab, shown
   separately, and never change the main signal.
2. **Meta-labeling** (Lopez de Prado, AFML ch. 3): a second model learns, from the primary model's own past trades, when
   its picks fail, and skips or shrinks them. Tested here (`app/ml/meta_engine.py`, `data/models/meta_engine_report.json`):
   2,139 out-of-sample picks (2021-26); 15 features known on the pick day (the pick's scores, the gap to the next coin,
   how crowded the top is, plan, volatility, BTC trend, the other side's forecast, and the engine's own record from
   trades already closed); logistic and a small LightGBM; walk-forward by year with a 15-day purge; 10 policies (skip
   below 0.30 / 0.34 / 0.38 / 0.42, or size by the chance of profit) chosen on 2022-24 (2021 only trains).
   **Result: rejected.** The meta models barely separate winners from losers (AUC 0.52-0.53). The policy chosen on
   2022-24 (logistic, skip below 0.30) lifted 2022-24 from Sharpe 2.67 to 2.97, then on unseen 2025-26 fell from Sharpe
   1.04 to 0.48 (+0.039R a trade against +0.080R; total +22R against +54R; 90% range of the difference −80R..+8R).
   Same lesson as §11: the engine's picks carry no learnable "this one will fail" signal beyond what the engine already
   uses. Not added. Re-run after a year of new data: `.venv/bin/python -m app.ml.meta_engine`.
3. **AI agents (LLMs) cannot be backtested honestly.** A language model has read the history it would be tested on, so
   any backtest of it leaks the future (the known weakness of published LLM-trading papers). The only fair test is
   forward: the agents give their verdict before the trade fills, and the Signal test scores machine alone against
   machine-when-agents-agreed / said wait / disagreed (0.43.0). **Pre-registered rule, written before any data:** after
   30 finished machine signals that had an agent verdict, the agents' WAIT or opposite call becomes a veto only if the
   "agents agreed" group beats "machine alone" by at least 0.15R a trade and the "agents said wait/against" group is
   negative; otherwise they stay a second opinion. Until then the machine decides and the agents only advise.
4. **What we did not do:** average the 8 decision rules, or switch to the rule that did best on 2025-26. All 8 were
   positive on the unseen years, but choosing or blending them now would use those years to choose; the rule stays the
   one chosen on 2020-24 (§13).

Reading: the current design (one model, a rule chosen on old years, a simulation of its own past picks, and the agents
as a forward-scored second opinion with a pre-registered veto test) is the research-supported way. The levers left are
more and newer data (the monthly retrain) and the forward record, not more layers on top.
