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
