# Research Knowledge Base: AI Crypto Trading Decision-Support

Status: living document. Last verified: 2026-11-01. Companion docs: [TRADING_CONCEPTS.md](TRADING_CONCEPTS.md), [DATA_SOURCES.md](DATA_SOURCES.md), [ML_RESEARCH.md](ML_RESEARCH.md).

The platform is **decision support**. It does not execute trades or promise returns. Every signal shown to a user should carry an evidence grade, a calibrated probability or confidence, and a note on what data it was built from.

---

## 0. Evidence grading scale

| Grade | Meaning |
|---|---|
| **A** | Strong evidence: peer-reviewed, replicated, and holds out of sample across assets or periods. Effect sizes still decay after publication. |
| **B** | Mixed or conditional: real but regime-dependent, sensitive to costs, or supported mainly by in-sample studies. |
| **C** | Weak or anecdotal: practitioner lore, few rigorous tests, or tests that mostly fail after costs and multiple-testing correction. |
| **D** | No credible evidence, or untestable as usually stated (subjective labeling, no falsifiable rule). |

A technique graded C or D can still be *displayed* (users expect SMC and Elliott annotations) but should be labeled as such and **must not feed a model target or headline score without passing the ML acceptance criteria** in ML_RESEARCH.md.

---

## 1. Executive summary

1. **Trend and time-series momentum are the best-supported directional signals**, in traditional futures (Moskowitz, Ooi & Pedersen 2012) and in crypto, where Liu & Tsyvinski (2021) find strong time-series momentum and Liu, Tsyvinski & Wu (2022) find a three-factor model (market, size, momentum) explains much of the cross-section. Grade **A-/B+** for crypto, given the short sample and heavy regime dependence.
2. **Perpetual-futures carry (funding/basis) is a real, documented premium** (Schmeling, Schrimpf & Todorov 2023, "Crypto carry"; He, Manela, Ross & von Wachter 2022). Funding is predictable in the short term, but crashes cluster when carry is high. Grade **B+**.
3. **Order-book imbalance predicts very short-horizon price changes** (Cont, Kukanov & Stoikov 2014; replicated for crypto). The effect lasts seconds to minutes and is mostly eaten by costs for non-HFT users. Grade **A** for the statistical relationship, **C** as a retail trading edge.
4. **SMC/ICT concepts and Elliott Wave have essentially no peer-reviewed support.** Parts of them can be defined objectively (pivots, FVG, sweeps of prior highs and lows) and tested; as packaged methodologies they are grade **D**, and their objectively testable parts are **C** until our own tests say otherwise.
5. **On financial returns, ML gains are small, and simple baselines are hard to beat.** Gradient-boosted trees on carefully aligned features are the best value-for-effort. Deep forecasters (PatchTST, TFT, N-BEATS) are research-grade. Zeng et al. (2023) showed a one-layer linear model matches or beats many Transformers on long-horizon forecasting benchmarks.
6. **Multi-agent LLM trading systems (TradingAgents, FinMem) are unvalidated.** Their backtests are short and exposed to look-ahead from LLM pretraining, so they cannot support trading claims. Use LLMs to summarize and explain, not as the source of alpha. Grade **D** for alpha, **B** for summarization.
7. **The biggest risk is self-deception through backtest overfitting.** Mandatory controls: purged and embargoed CV, deflated Sharpe ratio, a log of every trial run, realistic costs and funding, and point-in-time data.

---

## 2. Findings by area

### 2.1 Trading strategies and quant methods
- **Time-series momentum / trend following.** Each asset's own trailing 12-month return predicts its next-month return across 58 futures and forwards (Moskowitz, Ooi & Pedersen 2012). In crypto, Liu & Tsyvinski (2021) find time-series momentum at 1 to 8 week horizons, and Liu, Tsyvinski & Wu (2022) document cross-sectional size and momentum factors. Caveats: the sample is short (mostly 2014 onward), and coin selection carries survivorship bias. Post-2022 performance has been weaker and more episodic (practitioner reports; unverified in peer review). **A- (traditional), B+ (crypto).**
- **Cross-sectional momentum among coins.** Present in Liu-Tsyvinski-Wu, but sensitive to liquidity screens and microcap inclusion. **B.**
- **Short-term reversal.** Daily and intraday reversal is documented in equities. Crypto evidence is mixed and very cost-sensitive. **C/B-.**
- **Classic technical rules (MA crossovers, breakouts).** Brock, Lakonishok & LeBaron (1992) reported profitability; Sullivan, Timmermann & White (1999) showed that much of it disappears after a data-snooping correction (White's Reality Check). Crypto: Hudson & Urquhart (2021) find some rule predictability; Detzel et al. (2021) give theory and evidence that MA rules work for assets with hard-to-value fundamentals such as BTC. MA rules are effectively trend proxies, so they are not independent evidence. **B (as trend proxies), C (as standalone pattern rules).**
- **Chart patterns (head-and-shoulders, etc.).** Lo, Mamaysky & Wang (2000) algorithmically detected patterns and found modest *distributional* information, not robust profitability. **C.**

### 2.2 Market structure / Smart Money Concepts (SMC / ICT)
- Concepts include BOS, CHoCH, order blocks, FVGs, liquidity sweeps, premium/discount, OTE, and killzones. TRADING_CONCEPTS.md gives the precise definitions we use.
- **Evidence:** We found no peer-reviewed study validating SMC/ICT as a methodology. Material is almost entirely YouTube, courses, and TradingView scripts. The terminology repackages older ideas: support and resistance, swing structure (Dow theory), stop-hunting/liquidity, and gaps. Several components are reasonable *hypotheses* that can be tested once defined objectively:
  - Swing-high/low structure and breaks give a trend-state label, which overlaps with momentum (**C**, possibly B by proxy).
  - Stop clustering near round numbers and prior extremes is documented for FX by Osler (2003, 2005). This lends indirect support to "liquidity pools beyond equal highs/lows" (**B-** as a concept, **C** as a trading signal).
  - Price gaps / FVG "fill" tendencies: no rigorous crypto evidence (**C/D**).
  - Order blocks: the definition is discretionary in most sources (**D** as usually stated, **C** once our definition is fixed).
- **Implementation stance:** Detect deterministically with parameters exposed (pivot N, ATR multipliers). Display as annotations. Measure forward-return statistics with honest multiple-testing correction before any concept is promoted into the scoring engine.

### 2.3 Elliott Wave
- Hard rules (W2 never retraces past the W1 start; W3 is never the shortest impulse wave; W4 does not overlap W1 except in diagonals) are falsifiable *constraints on a labeling*. The labeling itself is not unique: many valid counts exist for any series, and practitioners revise counts after the fact.
- **Evidence:** We found no credible peer-reviewed out-of-sample evidence of predictive value. Fibonacci-ratio claims (W3 = 1.618 × W1, etc.) are guidelines that ad-hoc counts tend to "confirm" through hindsight. **D.**
- **Implementation stance:** We may offer an *automated, rule-constrained* count (zigzag pivots, then enumerate labelings that satisfy the hard rules, then rank by guideline fit) as a visualization with an explicit "speculative" badge. Never use it as a model target.

### 2.4 Order flow and microstructure
- **Order-flow imbalance (OFI):** Cont, Kukanov & Stoikov (2014) show that price changes over short intervals are approximately linear in OFI, with slope inversely related to market depth. R² is high at 10-second horizons for US equities. Kolm, Turiel & Westray (2023) show deep models on multi-level OFI forecast short-horizon returns, but alpha decays within seconds to minutes. Silantyev (2019) finds that trade-flow imbalance explains contemporaneous BTC price changes on BitMEX better than book imbalance does.
- **Practical relevance:** Contemporaneous explanatory power is not the same as tradable predictive power at our latency (cloud REST/WS, 100 ms to seconds). Useful as a **context feature** (pressure, absorption, liquidity) and for slippage estimation; not as a standalone retail signal. **A** (statistical), **C** (retail edge).
- **Cumulative volume delta (CVD) from aggTrades** and **liquidation feeds** are cheap to compute from public WebSocket data. They are informative for context. Evidence for predictive edge: **C**.
- **Footprint / volume profile / VWAP:** VWAP is an execution benchmark (Berkowitz, Logue & Noser 1988). "Point of control as magnet" is practitioner lore: **C**.

### 2.5 Indicators and signal processing
- RSI, MACD, Bollinger Bands, ATR, ADX, Stochastics, Ichimoku: deterministic transforms of price. Their information is largely already contained in momentum, volatility, and mean-reversion features. Use them as **features** for ML, not as validated signals. **C** standalone.
- **Volatility estimators (Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang)** are well-founded and more efficient than close-to-close estimation. **A** (as estimators).
- **Volatility forecasting with GARCH / HAR-RV:** volatility is far more predictable than direction. HAR-RV (Corsi 2009) is a strong, simple baseline. **A** for vol forecasting.
- **Filtering (Kalman, wavelets, Hilbert/"cycle" indicators):** centered/two-sided filters are a classic look-ahead trap. Use causal filters only. Ehlers-style cycle indicators: **C**.
- **Fractional differentiation** (López de Prado 2018) preserves memory while achieving stationarity. Useful for features; modest evidence. **B-.**

### 2.6 ML / DL for financial time series
- **Tabular GBDT (LightGBM/XGBoost)** on engineered features is the practical default. Gu, Kelly & Xiu (2020) found trees and neural nets add value over linear models in equity cross-sections, with small absolute R² (under 1% monthly). Grinsztajn et al. (2022) show trees still beat deep nets on typical tabular data. **B+.**
- **Deep forecasters:** N-BEATS (Oreshkin et al. 2020), TFT (Lim et al. 2021), PatchTST (Nie et al. 2023). These are strong on M4 and electricity/traffic benchmarks, whose series have seasonality that returns lack. Zeng et al. (2023), "Are Transformers Effective for Time Series Forecasting?", show DLinear/NLinear match or beat Transformer models on long-term benchmarks. On near-random-walk return series, expect little gain over baselines. **C** for return prediction, **B** for volatility/volume.
- **Time-series foundation models (Chronos, TimesFM, Moirai):** zero-shot point forecasts on prices reduce to near-persistence. No credible evidence of tradable return prediction. **D/C.**
- **Reinforcement learning for trading:** results are highly sensitive to simulator assumptions and seeds. **C/D.**
- **Labeling:** triple-barrier and meta-labeling (López de Prado 2018) give path-aware labels and a principled way to size bets. Methodologically sound; edge depends on the primary signal. **B.**
- See ML_RESEARCH.md for the full plan.

### 2.7 Regime detection
- **Markov-switching / HMM** (Hamilton 1989; Ang & Bekaert 2002): standard for volatility and bull/bear regimes. Regimes are well identified *in sample*. Real-time (filtered, not smoothed) probabilities lag, and smoothed probabilities are a look-ahead trap.
- **Statistical jump models** (Nystrup et al. 2020, penalized state switching) give more persistent, less flickery regimes and have shown out-of-sample risk reduction for equity allocation (Shu, Yu & Mulvey 2024).
- **Simple alternatives:** realized-vol percentile buckets, trend filters (price vs. 200-day MA), and ADX are often as useful as HMMs for conditioning.
- Grade: **B** for volatility-regime identification and risk scaling; **C** for using regime calls to time direction.

### 2.8 Statistical arbitrage / pairs in crypto
- Pairs trading is classically documented (Gatev, Goetzmann & Rouwenhorst 2006) but has declined over time. Crypto studies (e.g., Fil & Kristoufek 2020) report profitability at high frequency that falls sharply at daily frequency and after costs.
- Cointegration among major coins is unstable, because the market factor dominates and correlations rise in selloffs.
- Execution-heavy (two legs, borrow/funding). Better suited to exchange arbitrage desks than to a decision-support UI. **C/B-.** Low priority.

### 2.9 Perpetual futures: funding and basis
- **Mechanics** (TRADING_CONCEPTS.md §4): funding transfers between longs and shorts every 8h (dynamic 4h/1h on some Binance contracts), anchoring the perp to the index. Binance formula: `F = P̄ + clamp(I − P̄, −0.05%, +0.05%)`, with I = 0.01% per 8h by default.
- **Evidence:** He, Manela, Ross & von Wachter (2022) derive no-arbitrage perp pricing and document large, persistent deviations with sizable arbitrage returns early on, declining over time. Schmeling, Schrimpf & Todorov (2023, BIS WP 1087) find crypto carry (futures basis) is large and time-varying, driven by trend-chasing demand for leverage. High carry predicts liquidation crashes and lower subsequent spot returns.
- **Signals:** extreme positive funding plus rising OI is a crowding/leverage warning (contrarian at extremes, **B-**). Funding as a carry harvest (delta-neutral) is **B+**, but exchange and counterparty risk is real (FTX 2022).
- **OI change plus price change quadrants** (long buildup, short covering, etc.) are practitioner heuristics. **C.**

### 2.10 News, sentiment, on-chain
- Text sentiment predicts equity returns modestly (Tetlock 2007). For crypto, Twitter sentiment shows some short-horizon predictive power for some coins (Kraaijeveld & De Smedt 2020). Results are fragile and data access is now restricted (DATA_SOURCES.md).
- **Event detection** (listings, delistings, hacks, regulatory actions, ETF flows, FOMC) is more robust than generic sentiment scores. Binance listing announcements historically produced large short-term moves. That is well known, so speed matters and the edge is not retail-accessible.
- **Fear & Greed Index (alternative.me):** a composite of vol, momentum, social, and dominance. No peer-reviewed validation. **C**. Display only.
- **On-chain metrics (MVRV, SOPR, exchange flows, active addresses):** Liu & Tsyvinski (2021) find network-adoption measures relate to returns. Many popular on-chain "indicators" are fit on a handful of BTC cycles (n ≈ 4), so **C**. Good on-chain data is mostly paid (Glassnode, CryptoQuant).
- **LLM-derived sentiment look-ahead:** an LLM pretrained on later text can "know" outcomes (Glasserman & Lin 2023; Sarkar & Vafa 2024). Backtests of LLM sentiment before the model's training cutoff are contaminated.

### 2.11 Multi-agent LLM finance architectures
- **TradingAgents** (Xiao et al. 2024, arXiv 2412.20138): analyst, researcher (bull/bear debate), trader, and risk-manager agents. Reported backtests cover roughly 3 months on a handful of US stocks.
- **FinMem** (Yu et al. 2023, arXiv 2311.13743): layered memory plus character profile. Backtests are similarly short, on single stocks.
- **Limits:** (1) very short evaluation windows, with no significance or multiple-testing control; (2) pretraining look-ahead, because evaluation periods often fall inside the model's training cutoff; (3) non-determinism and prompt sensitivity; (4) cost and latency; (5) hallucinated numbers.
- **Our use:** LLMs as *explainers and summarizers* on top of deterministic, tested signals: news digest, "why is this flagged", structured reasoning over computed features. Never use LLM output as the probability source. Always ground on tool-computed numbers and cite inputs. **B** for summarization utility, **D** for alpha.

### 2.12 Probabilistic forecasting and calibration
- Present **probabilities and intervals**, not point targets. Evaluate with proper scoring rules (Gneiting & Raftery 2007): log loss and Brier score for classification, CRPS and pinball loss for quantiles.
- Tree ensembles and boosted models are often miscalibrated. Isotonic regression and Platt scaling (Niculescu-Mizil & Caruana 2005) fix much of it. Recalibrate on a time-ordered holdout only.
- **Conformal prediction** gives distribution-free intervals. Under non-stationarity use adaptive conformal inference (Gibbs & Candès 2021). **A** (method validity), and it can be implemented cheaply.

### 2.13 Portfolio construction and risk
- **Volatility targeting:** scaling exposure by inverse forecast volatility stabilizes risk. Moreira & Muir (2017) report improved Sharpe; Cederburg et al. (2020) find the gains mostly do not survive out of sample for many factors. Harvey et al. (2018) show it reliably reduces tail risk for risk assets. In crypto, where vol clusters strongly, vol targeting is primarily a **risk** tool. **A** (risk control), **B** (return enhancement).
- **Kelly criterion:** maximizes long-run growth *if* the edge and the distribution are known. With estimated edges, full Kelly badly overbets (MacLean, Thorp & Ziemba 2011). Use fractional Kelly (≤ 0.25 to 0.5) on *shrunk* edge estimates, capped by a max-risk-per-trade rule. Show Kelly only as an upper bound.
- **Position-sizing display:** risk-per-trade = account × r%. Size = risk / |entry − stop|. Include fees, funding, and a liquidation-distance check (TRADING_CONCEPTS.md §4.5).
- **Correlation:** crypto assets are highly correlated (BTC beta). "Diversification" across alts is limited, especially in drawdowns.

### 2.14 Backtesting pitfalls
See §5 for the full list. The key references are Bailey, Borwein, López de Prado & Zhu (2014, "Pseudo-mathematics and financial charlatanism"), Bailey & López de Prado (2014, deflated Sharpe ratio), Bailey et al. (2017, probability of backtest overfitting), and Harvey, Liu & Zhu (2016, t > 3 hurdle).

### 2.15 Charting libraries
| Library | License | Notes |
|---|---|---|
| **TradingView Lightweight Charts** | Apache-2.0 | Canvas, ~45 KB, fast, supports custom series and primitives (draw zones, FVG boxes). Attribution: the NOTICE file and a link to tradingview.com are required; the `attributionLogo` chart option satisfies the link requirement. **Recommended.** |
| TradingView Advanced Charts / Charting Library | Proprietary, free on application for public non-commercial-ish use; licence agreement required | Full drawing tools and indicators. Not open source. |
| Apache ECharts | Apache-2.0 | General-purpose; candlesticks fine; heavier. |
| Plotly.js | MIT | Good for research and reliability diagrams; slow for live tick data. |
| uPlot | MIT | Extremely fast time-series; no native financial primitives. |
| Highcharts Stock | Commercial licence for commercial use | Avoid unless licensed. |

---

## 3. Evidence-grade summary table

| Technique | Grade | Primary evidence | Use in platform |
|---|---|---|---|
| Time-series momentum / trend | A- (B+ crypto) | Moskowitz et al. 2012; Liu & Tsyvinski 2021 | Core feature and signal |
| Cross-sectional coin momentum, size | B | Liu, Tsyvinski & Wu 2022 | Screener/ranking |
| Volatility forecasting (HAR, GARCH) | A | Corsi 2009; extensive literature | Risk sizing, stops, bands |
| Volatility targeting (risk control) | A | Harvey et al. 2018 | Position sizing |
| Vol targeting (Sharpe enhancement) | B | Moreira & Muir 2017 vs. Cederburg et al. 2020 | Do not market as alpha |
| Funding/basis carry | B+ | Schmeling et al. 2023; He et al. 2022 | Derivatives dashboard, crowding flag |
| Funding extremes as contrarian | B- | Schmeling et al. 2023 (crash predictability) | Warning flag |
| OI/price quadrants | C | Practitioner | Display |
| Order-flow imbalance (short horizon) | A stat / C retail | Cont et al. 2014; Silantyev 2019 | Context feature |
| CVD, liquidations | C | Practitioner | Display/features |
| Classic TA rules (MA cross, breakout) | B as trend proxy / C standalone | Brock et al. 1992; Sullivan et al. 1999 | Features |
| Oscillators (RSI, MACD, etc.) | C | Mostly in-sample | Features only |
| Chart patterns | C | Lo, Mamaysky & Wang 2000 | Optional display |
| SMC/ICT (BOS, CHoCH, OB, FVG, OTE) | D packaged / C objectively defined | None peer-reviewed; Osler 2003 for stop clustering | Annotations with badge; test before scoring |
| Liquidity sweeps of prior highs/lows | C | Osler 2003/2005 (FX stops) | Annotation, testable feature |
| Killzones/sessions | C (seasonality in crypto vol is real, B) | Intraday vol seasonality literature | Vol/session context |
| Elliott Wave | D | None credible | Speculative overlay only |
| Fibonacci retracements | D/C | None credible | Display |
| HMM / jump-model regimes | B (vol) / C (direction) | Hamilton 1989; Nystrup et al. 2020; Shu et al. 2024 | Conditioning, risk |
| Pairs/stat-arb in crypto | C/B- | Gatev et al. 2006; Fil & Kristoufek 2020 | Low priority |
| GBDT on engineered features | B+ | Gu, Kelly & Xiu 2020; Grinsztajn et al. 2022 | Primary ML |
| Deep TS models (PatchTST, TFT, N-BEATS) | C returns / B vol | Zeng et al. 2023 | Research track |
| TS foundation models (zero-shot returns) | D/C | No credible finance evidence | Do not use for signals |
| RL trading agents | C/D | Fragile | Do not use |
| Triple-barrier / meta-labeling | B | López de Prado 2018 | Labeling |
| Calibration (isotonic/Platt), conformal | A (method) | Niculescu-Mizil & Caruana 2005; Gibbs & Candès 2021 | Mandatory |
| Generic news/social sentiment | C | Tetlock 2007; Kraaijeveld & De Smedt 2020 | Context |
| Event detection (listings, hacks, macro) | B- | Event-study literature | Alerts |
| Fear & Greed Index | C | None peer-reviewed | Display |
| On-chain cycle indicators (MVRV etc.) | C | Few cycles; Liu & Tsyvinski 2021 (adoption) | Optional |
| Multi-agent LLM trading | D (alpha) / B (summaries) | TradingAgents, FinMem (short backtests) | Explainer only |
| Full Kelly sizing | D in practice | MacLean, Thorp & Ziemba 2011 | Fractional, capped only |

---

## 4. Implementation priorities

Ordered by evidence × user value ÷ effort.

1. **Data foundation (P0):** point-in-time OHLCV (Binance spot + USDⓈ-M), funding, mark/index price, OI snapshots (collect our own: Binance OI history is only ~30 days), and an event timestamp on every row (`event_time`, `ingest_time`).
2. **Backtest/eval harness (P0), built before any model:** cost model (fees, spread, slippage, funding), purged/embargoed CV, walk-forward, trial registry, deflated Sharpe, PBO.
3. **Trend/momentum and volatility (P0):** multi-horizon TSMOM signals, HAR-RV vol forecast, vol-targeted size suggestions, ATR-based stops.
4. **Derivatives dashboard (P1):** funding (current, predicted, history percentiles), basis, OI change, liquidation-price calculator, crowding flags.
5. **Deterministic structure annotations (P1):** pivots, HH/HL/LH/LL, BOS/CHoCH, FVG, sweeps, PDH/PDL, sessions. All parameterized, all badged with an evidence grade, with an "event study" view showing historical forward returns after each event, with confidence intervals and after costs.
6. **Baseline ML (P1):** LightGBM on triple-barrier labels with calibrated probabilities, gated by the acceptance criteria.
7. **Regime layer (P2):** vol-percentile regimes, then HMM/jump model (filtered probabilities only).
8. **News/event pipeline (P2):** RSS ingestion, dedup, entity tagging (coin tickers), LLM summaries with source links. Event alerts for listings/delistings.
9. **LLM explainer (P2):** a tool-grounded narrative of the computed state. No free-form price predictions.
10. **Research track (P3):** PatchTST/N-BEATS for vol; Elliott auto-count overlay; pairs screener.

---

## 5. Known sources of look-ahead bias and overfitting

**Look-ahead / leakage**
1. **Using the forming candle.** Indicators computed on a bar that has not closed. Only use closed bars (`k.x == true` in Binance kline WS) for signals.
2. **Pivot confirmation lag.** A fractal pivot with right-side N bars is only known N bars later. Structure events (BOS/CHoCH) must be timestamped at *confirmation*, not at the pivot bar.
3. **Smoothed vs. filtered regimes.** HMM smoothed probabilities (forward-backward) use future data. Use forward-filtered probabilities only.
4. **Two-sided filters and normalization.** Centered moving averages, full-sample z-scores, scalers fit on all data, and full-sample PCA all leak.
5. **Label overlap.** Triple-barrier labels span future bars, so CV without purging and embargo leaks (López de Prado 2018, ch. 7).
6. **Timestamp misalignment.** Binance kline open time vs. close time; funding timestamp vs. settlement; news `published` vs. our `ingested` time (RSS `pubDate` can be backdated or edited). Join on `available_at`.
7. **Revised data.** OI and long/short ratios can be restated. Macro data gets revised. Store vintages.
8. **Survivorship.** The current exchangeInfo symbol list excludes delisted coins. Universe selection must be point in time (data.binance.vision keeps delisted symbols' archives; also keep our own listing log).
9. **LLM pretraining contamination** (§2.10).
10. **Tuning on the test set.** Every look at the holdout is a trial.

**Overfitting / multiple testing**
- Track **number of trials N** for every strategy family. Report the **deflated Sharpe ratio** (Bailey & López de Prado 2014) and **probability of backtest overfitting** via CSCV (Bailey et al. 2017).
- Use a t-stat hurdle around 3 for new factors (Harvey, Liu & Zhu 2016).
- Prefer few-parameter models. Check parameter stability: plateaus, not peaks.
- Include **realistic costs**: Binance VIP0 fees (spot 0.10%, futures 0.02% maker / 0.05% taker at time of writing; verify via `/api/v3/account/commission` or the fee page), spread, slippage scaled by size/depth, and funding for perps.
- Watch for non-stationarity: crypto 2017, 2020-21, and 2024-25 regimes differ. Report results per regime and per year.

---

## 6. Expected computational requirements

| Workload | Estimate | Notes |
|---|---|---|
| OHLCV storage | 1m bars ≈ 0.5M rows/symbol/year; 100 symbols × 6 yrs ≈ 300M rows. ~15 to 30 GB raw in Postgres, roughly 5 to 10x smaller with Timescale columnstore compression | Estimate; depends on columns |
| aggTrades / depth | Orders of magnitude larger (BTCUSDT aggTrades: millions per day). Store only for a few symbols, or aggregate to bars on ingest | |
| Live ingest | 1 small VM (2 vCPU / 4 GB) handles WS for ~200 symbols' klines + markPrice + liquidations | Binance WS 1024 streams/conn |
| Indicators / structure detection | Vectorized numpy/pandas or Polars: milliseconds per symbol-timeframe for 10k bars | Incremental update on bar close |
| LightGBM training | 1M rows × 100 features: under 1 to 5 minutes on 8 CPU cores. Full purged CV × hyperparameter search (~100 trials): about 1 to 3 hours CPU | Optuna, early stopping |
| Deep models (PatchTST/TFT) | GPU recommended. Hours per model on a single consumer GPU for multi-symbol hourly data; CPU is impractical for search | Research only |
| HMM / jump models | Seconds on CPU (hmmlearn) | |
| LLM summarization | External API; cost scales with articles. Cache by article hash | Rate-limit and budget caps |
| Backtests | Vectorized: seconds. Event-driven with fills: minutes per symbol-year | |

---

## 7. Regulatory, licensing and exchange restrictions

*Not legal advice. Have counsel review before launch.*

- **Decision support vs. advice:** Signals and "recommendations" may count as investment advice or regulated activity in some jurisdictions (e.g., US investment-adviser rules, UK FCA financial promotions rules for cryptoassets since Oct 2023, EU MiCA since Dec 2024). Present as information/education, avoid personalized recommendations, show risk warnings, and do not present hypothetical performance without disclosures.
- **Binance.com geo-restrictions:** Binance.com does not serve US persons. US users are directed to Binance.US (a separate entity with its own API at `api.binance.us`, its own symbol set, and state-level restrictions). **Binance APIs enforce this by server IP:** requests from US IPs, including many US cloud regions, get **HTTP 451** "Service unavailable from a restricted location" (widely reported, e.g., ccxt issues #15872/#15917). Do not circumvent via proxies. Deploy ingestion in a permitted region, or use alternative sources for US users. Other jurisdictions have restrictions or have seen exits (e.g., Canada 2023, UK onboarding limits, Ontario). These change frequently, so check Binance's Terms "Eligibility" section.
- **Binance API / data terms:** API use is subject to the Binance Terms of Use, and Binance may restrict or terminate API access at any time without notice. Bulk data from data.binance.vision is subject to the **Binance Vision Terms of Use** (supplemental to the main terms; derived outputs covered; no use of Binance marks). Redistribution of raw market data to end users at scale may need permission. Display derived analytics and attribute the source.
- **CoinGecko Demo (free) plan:** requires attribution ("Data provided by CoinGecko" with a link per their attribution guide). The pricing page lists 100 calls/min and 10,000 calls/month for Demo (the docs page also says 100/min; older sources said 30/min). Demo is positioned for testing and exploration; commercial use is listed for paid plans. Use a paid plan for production commercial use.
- **TradingView Lightweight Charts:** Apache-2.0. Must keep LICENSE/NOTICE, credit TradingView as the product creator, and link to tradingview.com (`attributionLogo: true` satisfies this). Do not use TradingView trademarks to imply endorsement.
- **TimescaleDB:** two editions. The **Apache-2.0 edition** has core hypertables only. The **Community Edition under the Timescale/Tiger Data License (TSL)** adds columnstore/compression, continuous aggregates, and retention policies. TSL is free to self-host (including inside a SaaS product), but **you may not offer TimescaleDB itself as a DBaaS**. Our use (an internal DB behind an app) is fine under TSL. Managed Postgres providers (RDS, Cloud SQL) typically ship only the Apache edition, so compression and continuous aggregates are unavailable there.
- **CCXT:** MIT. Its use does not change the underlying exchange terms.
- **News content:** RSS feeds permit headline, snippet, and link display for personal or aggregation use under each publisher's terms. Do not republish full articles. Store title, summary, URL, and timestamp, and link out. LLM summaries of full article text may raise copyright issues, so summarize from feed snippets or with permission.
- **X (Twitter) and Reddit:** X API access is paid, with frequently changing tiers and pricing (verify current pricing). Scraping violates X's ToS. Reddit's Data API terms (2023) require an agreement for commercial use and have rate limits on the free tier. Treat both as out of scope for the MVP.

---

## References

Momentum, factors, TA
- Moskowitz, Ooi, Pedersen (2012) Time series momentum, JFE: https://doi.org/10.1016/j.jfineco.2011.11.003
- Liu & Tsyvinski (2021) Risks and Returns of Cryptocurrency, RFS: https://doi.org/10.1093/rfs/hhaa113
- Liu, Tsyvinski, Wu (2022) Common Risk Factors in Cryptocurrency, JF: https://doi.org/10.1111/jofi.13119
- Brock, Lakonishok, LeBaron (1992) Simple technical trading rules, JF: https://doi.org/10.1111/j.1540-6261.1992.tb04681.x
- Sullivan, Timmermann, White (1999) Data-snooping, technical trading rule performance, and the bootstrap, JF: https://doi.org/10.1111/0022-1082.00163
- Lo, Mamaysky, Wang (2000) Foundations of Technical Analysis, JF: https://doi.org/10.1111/0022-1082.00265
- Hudson & Urquhart (2021) Technical trading and cryptocurrencies, Annals of OR: https://doi.org/10.1007/s10479-019-03357-1
- Detzel et al. (2021) Learning and predictability via technical analysis, Financial Management: https://doi.org/10.1111/fima.12310
- Osler (2003) Currency orders and exchange rate dynamics, JF: https://doi.org/10.1111/1540-6261.00594

Microstructure
- Cont, Kukanov, Stoikov (2014) The price impact of order book events, J. Financial Econometrics: https://doi.org/10.1093/jjfinec/nbt003 (arXiv: https://arxiv.org/abs/1011.6402)
- Kolm, Turiel, Westray (2023) Deep order flow imbalance, Mathematical Finance: https://doi.org/10.1111/mafi.12413
- Silantyev (2019) Order flow analysis of cryptocurrency markets, Digital Finance: https://doi.org/10.1007/s42521-019-00007-w

Derivatives / carry
- He, Manela, Ross, von Wachter (2022) Fundamentals of Perpetual Futures: https://arxiv.org/abs/2212.06888
- Schmeling, Schrimpf, Todorov (2023) Crypto carry, BIS WP 1087: https://www.bis.org/publ/work1087.htm
- Binance funding rate FAQ: https://www.binance.com/en/support/faq/introduction-to-binance-futures-funding-rates-360033525031

ML / forecasting
- López de Prado (2018) Advances in Financial Machine Learning, Wiley.
- Bailey & López de Prado (2014) The Deflated Sharpe Ratio: https://ssrn.com/abstract=2460551
- Bailey, Borwein, López de Prado, Zhu (2017) The Probability of Backtest Overfitting: https://ssrn.com/abstract=2326253
- Bailey, Borwein, López de Prado, Zhu (2014) Pseudo-Mathematics and Financial Charlatanism, Notices AMS: https://www.ams.org/notices/201405/rnoti-p458.pdf
- Harvey, Liu, Zhu (2016) ...and the Cross-Section of Expected Returns, RFS: https://doi.org/10.1093/rfs/hhv059
- Gu, Kelly, Xiu (2020) Empirical Asset Pricing via Machine Learning, RFS: https://doi.org/10.1093/rfs/hhaa009
- Grinsztajn, Oyallon, Varoquaux (2022) Why do tree-based models still outperform deep learning on tabular data?: https://arxiv.org/abs/2207.08815
- Zeng et al. (2023) Are Transformers Effective for Time Series Forecasting? (AAAI): https://arxiv.org/abs/2205.13504
- Nie et al. (2023) PatchTST, ICLR: https://arxiv.org/abs/2211.14730
- Lim et al. (2021) Temporal Fusion Transformers, IJF: https://arxiv.org/abs/1912.09363
- Oreshkin et al. (2020) N-BEATS, ICLR: https://arxiv.org/abs/1905.10437
- Corsi (2009) A Simple Approximate Long-Memory Model of Realized Volatility: https://doi.org/10.1093/jjfinec/nbp001
- Gneiting & Raftery (2007) Strictly Proper Scoring Rules: https://doi.org/10.1198/016214506000001437
- Niculescu-Mizil & Caruana (2005) Predicting good probabilities with supervised learning: https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf
- Gibbs & Candès (2021) Adaptive Conformal Inference Under Distribution Shift: https://arxiv.org/abs/2106.00170

Regimes, pairs, risk
- Hamilton (1989) Econometrica: https://doi.org/10.2307/1912559
- Nystrup, Lindström, Madsen (2020) Learning hidden Markov models with persistent states by penalizing jumps: https://doi.org/10.1016/j.eswa.2020.113307
- Shu, Yu, Mulvey (2024) Downside risk reduction using regime-switching signals: a statistical jump model approach: https://arxiv.org/abs/2402.05272
- Gatev, Goetzmann, Rouwenhorst (2006) Pairs Trading, RFS: https://doi.org/10.1093/rfs/hhj020
- Fil & Kristoufek (2020) Pairs trading in cryptocurrency markets, IEEE Access: https://doi.org/10.1109/ACCESS.2020.3024619
- Moreira & Muir (2017) Volatility-Managed Portfolios, JF: https://doi.org/10.1111/jofi.12513
- Cederburg, O'Doherty, Wang, Yan (2020) On the performance of volatility-managed portfolios, JFE: https://doi.org/10.1016/j.jfineco.2020.04.015
- Harvey et al. (2018) The Impact of Volatility Targeting: https://ssrn.com/abstract=3175538
- MacLean, Thorp, Ziemba (2011) The Kelly Capital Growth Investment Criterion, World Scientific.

Sentiment / LLM
- Tetlock (2007) Giving Content to Investor Sentiment, JF: https://doi.org/10.1111/j.1540-6261.2007.01232.x
- Kraaijeveld & De Smedt (2020) The predictive power of public Twitter sentiment for forecasting cryptocurrency prices: https://doi.org/10.1016/j.intfin.2020.101188
- Xiao et al. (2024) TradingAgents: https://arxiv.org/abs/2412.20138
- Yu et al. (2023) FinMem: https://arxiv.org/abs/2311.13743
- Glasserman & Lin (2023) Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis: https://arxiv.org/abs/2309.17322
- Sarkar & Vafa (2024) Lookahead Bias in Pretrained Language Models: https://ssrn.com/abstract=4754678

Licensing / restrictions
- Lightweight Charts (Apache-2.0, attribution): https://github.com/tradingview/lightweight-charts
- TimescaleDB editions: https://www.tigerdata.com/docs/about/latest/timescaledb-editions
- CoinGecko API pricing / attribution: https://www.coingecko.com/en/api/pricing
- Binance Vision Terms of Use: https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf
- Binance Terms of Use: https://www.binance.com/en/terms
- ccxt issue (HTTP 451 restricted location): https://github.com/ccxt/ccxt/issues/15872

---

## Empirical findings from this platform (2026-11-01)
These results come from the platform's own backtester and ML pipeline on Binance data. They are simulated,
not live, but they are reproducible from the stored configuration hashes.

1. **The default rule-based strategy set has negative expectancy after costs** (corrected simulator). 1h, 5
   majors, 8 months: 1,014 trades, −0.31R (95% CI −0.44 to −0.18). 4h, 5 majors, 15 months: 505 trades, −0.11R
   (CI −0.27 to +0.07), with in-sample +0.08R collapsing to −0.51R out-of-sample. Results get worse
   monotonically as costs rise. See STRATEGIES.md.
2. **SMC-style continuation entries performed worst.** FVG continuation scored −0.65R on 1h (n=299) and
   −0.41R on 4h (n=142). OB retest scored −0.53R on 1h (n=71). This agrees with the grade-C/D literature
   assessment above: geometric "institutional" zones did not carry exploitable information in this test.
3. **Liquidity-sweep reversal was the only positive strategy on 1h** (+0.30R, n=74, CI +0.03 to +0.58), but
   flat on 4h (n=21). Given six
   strategies tested, it is treated as inconclusive and needs out-of-sample confirmation through forward
   testing.
4. **ML:** a pooled LightGBM triple-barrier model (19 symbols, 81k labelled samples, 24h horizon) reached a mean
   OOS AUC of 0.542 against 0.514 for logistic regression. But it failed to beat the class-prior log loss on
   4 of 5 folds (poor calibration under regime shifts), and its top feature was day-of-week, a likely spurious
   pattern. Rejected. The thresholded economic test was positive (+0.10% per trade after 0.14% costs), but its
   samples overlap (hourly entries, 24h horizon), so its CI is optimistic.
5. **Implication for the product:** abstention is the default behaviour, not a bug. Statistically losing
   strategy/timeframe pairs are suspended automatically (`research_gate`). Promising directions are listed in
   ROADMAP.md, starting with time-series momentum and carry, which have the strongest external evidence.

## Leverage and win rate (2026-11, `app/maintenance/leverage_study.py`)

Questions fixed before looking: what does leverage do to the validated daily-trend edge, and what does a high win
rate cost? Data: the system account's own 197 growth-mode trades (2020-now) and the raw 1d / 4h breakout signals.
Costs: Binance USD-M taker 0.05 % + slippage 0.05 % per side. Liquidation of an isolated long ≈ 1/L − MMR below entry
(lowest tier: BTC 0.4 %, ETH 0.5 %, others 1 %).

**Liquidation.** Stops sit a median 16.6 % below entry; the median winner first fell 6.4 % before it paid.

| Leverage | Liquidated at | Trades liquidated | Winners liquidated | Avg per trade | $100 at 2 % risk |
|---|---|---|---|---|---|
| 1x | — | 0 % | 0 % | +0.64R | $957 |
| 3x | −32 % | 7 % | 0 % | +0.62R | $871 |
| 5x | −19 % | 35 % | 13 % | +0.46R | $482 |
| 10x | −9 % | 70 % | 35 % | +0.45R | $476 |
| 20x | −4 % | 86 % | 68 % | +0.17R | $170 |
| 50x | −1 % | 93 % | 85 % | +0.08R | $131 |

Traded as most 20x traders do (20 % of the account as margin on every trade), $100 ended at $4 with a 97 % drawdown.
A +20 % trade (+10R at 2 % risk) happened 4 times in 197 trades; the best was +22.7R.

**Win rate vs payoff** (1d breakout + BTC filter, stop 2.5 ATR, fixed targets; train / valid / test):

| Target | Win rate | Avg per trade |
|---|---|---|
| 0.25R | 83 % / 74 % / 83 % | +0.02 / −0.08 / +0.03 |
| 0.5R | 73 % / 58 % / 71 % | +0.08 / −0.13 / +0.05 |
| 1R | 61 % / 48 % / 56 % | +0.21 / −0.06 / +0.12 |
| 2R | 48 % / 38 % / 42 % | +0.42 / +0.13 / +0.16 |
| 3R | 42 % / 32 % / 37 % | +0.61 / +0.25 / +0.21 |

No target reaches 90 %. The highest win rate earns about nothing after costs; the lowest earns the most.

**Stops tight enough for 20x** (target 2R): 1d fixed 0.5 / 1 / 2 / 3 % stops averaged −1.1 / −0.8 / −0.5 / −0.3R in
training and stayed negative in 2024 and 2025-26; 4h fixed stops averaged −0.86 / −0.49 / −0.23 / −0.11R in training
and stayed negative. The "1 % move at 20x" trade (0.5 % stop, 1 % target on 4h candles) won 14-20 % and averaged
−0.67 to −0.86R per trade. Costs alone are 40 % of the risk at a 0.5 % stop.

**Decision.** The futures venue caps leverage per trade so the liquidation is at least 1.5x the stop distance away
(about 3x for a typical stop). Tight-stop, high-leverage and high-win-rate variants were not adopted.
