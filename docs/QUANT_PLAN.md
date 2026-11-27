# Computer-side analysis — the quant plan

The platform today reads charts the way a person does: structure, order blocks, fair value gaps, Elliott waves, Wyckoff,
candle patterns, then the AI desk reasons about them. That is the **human side**. Measured honestly, it has no edge
after costs: on 4h the engine's setups average about +0.02R, the daily-5 selection lost to five random picks, and the
ML filter and the evolution loop promoted nothing (docs/ML_RESEARCH.md §11, docs/STRATEGIES.md, docs/DAILY_PICKS.md).

The **computer side** works differently: every signal is a number computed the same way for every coin every day,
every number is tested on data it never saw, and coins are ranked against each other and traded as a portfolio. This is
how systematic (quant) funds work, and it is where the published evidence for crypto actually is.

## 1. What the evidence supports (and how strongly)

| Signal family | What it is | Evidence in crypto | Horizon | Strength |
|---|---|---|---|---|
| Cross-sectional momentum | Rank coins by past 1–4 week return; favour the top, avoid or short the bottom | One of three factors that explain crypto returns (market, size, momentum) — Liu, Tsyvinski & Wu, *Journal of Finance* 2022 | days–weeks | strong, published |
| Time-series momentum (trend) | Each coin's own trend (7/28/90-day return sign), sized by its volatility | Strong in Liu & Tsyvinski and Han et al.; weak or negative in Grobys & Sapkota; concentrated in bull markets; volatility scaling reduces crashes | weeks | moderate, period-sensitive |
| Carry / basis | Funding rate and futures–spot gap | Averages above 10% a year, sometimes 40–60%; high carry predicts crashes — Schmeling, Schrimpf & Todorov, BIS 2023. Basis reported as the strongest single daily predictor among 134 perp signals (secondary source) | daily | strong for carry harvesting and as a crash warning |
| Size | Small coins vs large | In the JF 2022 factor model, but small coins are thin: capacity and manipulation risk | weeks | real but hard to trade at scale |
| Order flow imbalance | Taker buys vs sells, book imbalance | Predicts very short horizons on Binance perps (seconds–minutes), small effect (IC 0.01–0.05), one study finds it fails under simple rules | seconds–hours | weak for swing trades; useful for entry timing |
| Positioning | Open interest vs price, liquidations, long/short ratio | Practitioner use; little peer-reviewed evidence | hours–days | hypothesis |
| Market regime | BTC trend and volatility, breadth, stablecoin supply | Used as a filter: momentum works mainly in bull regimes | weeks | supporting role |

What this means: the most reliable computer-side edge is **ranking many coins by momentum and carry and holding a
diversified, volatility-sized portfolio** — not finding one perfect pattern on one chart. Expected edges are small
(information coefficients of 0.02–0.05 are normal for good signals); the goal is a positive expectancy after costs,
proven out of sample, not "fully accurate".

## 2. The pipeline

**Data (point in time, survivorship-free)** → **factors** → **model** → **portfolio** → **validation** → **sandbox** →
**Today's 5**.

1. **Data.** A daily panel of every Binance USDT pair since 2019 *including delisted ones* (Binance's public bulk
   archive), with taker volume (kline column 9), funding history, and market cap. Today's research uses 12–35 coins that
   are still big, which flatters every backtest (survivorship bias). Open interest history is short on the API, so the
   platform must start recording it daily now.
2. **Factor library.** Each factor computed only from data known at the time: momentum 7/14/28/90d, volatility-adjusted
   momentum, 1-day reversal, funding (level and change), basis, OI change, taker imbalance, volume trend, distance from
   the 20-day high, BTC beta, correlation to BTC, regime flags. The platform's quant brain already ranks ~120 coins on 8
   trend signals; it becomes the first part of this library.
3. **Factor research report.** For every factor: rank IC with the next 1/3/7-day return, by year and by regime; IC decay
   by horizon; turnover; costs. A factor that does not show a stable IC is dropped before any model sees it.
4. **Model.** Three levels, each only kept if it beats the one below out of sample: (a) an equal-weight z-score
   composite of the surviving factors (the robust baseline); (b) ridge regression on factor ranks; (c) gradient
   boosting for non-linear effects, with the existing meta-labeling as a final filter. The model predicts each coin's
   next-period return *rank*, not a chart pattern.
5. **Portfolio.** Every day: the top-ranked coins long (and, on futures, the bottom-ranked short), each position sized to
   the same volatility, a cap per coin and per sector, a correlation limit, minimum liquidity and capacity, and a turnover
   limit so costs do not eat the edge. Funding paid or received is part of the return.
6. **Validation — what "accurate" means here.**
   - Walk-forward and combinatorial purged cross-validation (no overlap between training and test, with an embargo).
   - The deflated Sharpe ratio (does the best result beat what luck would produce after N tries?) and the probability
     of backtest overfitting (does the in-sample winner fall below the median out of sample?) — Bailey & López de Prado.
   - Every hypothesis written down before testing; a correction for testing many at once (Holm).
   - Stress tests: costs ×2, without the best month, by regime and by year, delisted coins included.
7. **Sandbox forward test.** 60–90 days of live paper trading of the exact portfolio, compared every day with what the
   backtest expected. If live diverges, it does not ship.
8. **Product.** The quant composite chooses Today's 5. The pattern engine, the readiness gate and the AI desk move to
   the *explanation* layer: why this coin, what the chart shows, where the invalidation is, how to size it. The computer
   decides; the desk explains and checks.

## 3. A second, lower-risk product: carry

The BIS evidence on carry suggests a separate offering: hold spot and short the perpetual (delta-neutral) when funding
is high, collect the funding, exit when it normalises; and use extreme carry as a crash warning for the directional
picks. It needs exchange fee tiers, funding history and margin rules modelled exactly before anyone relies on it.

## 4. Roadmap

| Phase | Deliverable | Pass to continue |
|---|---|---|
| 1. Data | Point-in-time daily panel of all Binance USDT pairs incl. delisted; funding history; daily OI recording | panel reproduces Binance's own closes; no future data in any row |
| 2. Factors | Factor library + research report (IC by horizon, year, regime; decay; turnover) | at least 3 factors with stable positive IC after costs |
| 3. Model + portfolio | Composite → ridge → boosting; vol-targeted portfolio backtest with costs and funding | out-of-sample Sharpe significant after deflation; PBO low; beats equal-weight market and BTC buy-and-hold on risk-adjusted terms |
| 4. Sandbox | 60–90 days live paper trading of the exact portfolio | live within the backtest's expected range |
| 5. Product switch | Today's 5 chosen by the quant model; desk and AI explain and check | the sandbox record holds |
| 6. Carry | Delta-neutral funding product | net of fees and margin, positive in stress periods |

## 5. What already exists to build on
- The quant brain: a cross-sectional trend rank of ~120 coins with its measured next-week hit rate.
- The backtester, the ML pipeline (purged walk-forward, calibration, acceptance rules) and the research loop
  (simulation, evolution, promotion gate).
- Market data from Binance, Bybit, OKX and Deribit (order flow, funding across exchanges, options), fundamentals
  (supply, TVL, fees) and the validators.
- The sandbox and the daily picks pipeline, which the quant model plugs into without changing the product.

## Sources
- Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency", Journal of Finance 77(2), 2022 — https://econpapers.repec.org/RePEc:bla:jfinan:v:77:y:2022:i:2:p:1133-1177
- Schmeling, Schrimpf & Todorov, "Crypto carry", BIS Working Paper 1087 — https://www.bis.org/publ/work1087.htm
- Volatility-managed crypto momentum (2025) — https://link.springer.com/article/10.1007/s11408-025-00474-9
- Liu & Tsyvinski, "Risks and Returns of Cryptocurrency", NBER w24877 — https://www.nber.org/system/files/working_papers/w24877/w24877.pdf
- Bailey, Borwein, López de Prado & Zhu, "The Probability of Backtest Overfitting" — https://papers.ssrn.com/abstract=2326253
- Bieganowski & Ślepaczuk, "Explainable Patterns in Cryptocurrency Microstructure" (preprint) — https://arxiv.org/abs/2602.00776

## Quant lab results (phases 1–3, run 2026-11-11)

Code: `backend/app/quant/lab/` (`data.py`, `factors.py`, `research.py`, `model.py`, `portfolio.py`, `validate.py`,
`live.py`), tests in `backend/tests/test_quant_lab.py` (17 synthetic tests, no network). Outputs in
`backend/data/quant_lab/` (git-ignored, 84 MB): `panel.csv.gz`, `funding_daily.csv.gz`, `symbols.csv`, `manifest.json`,
`factor_report.{json,md}`, `validation_report.json`, `trials.jsonl`. pyarrow is not installed, so storage is gzip CSV.

Commands: `uv run python -m app.quant.lab.data --update` (incremental; a no-op refresh takes ~70 s, the first full
download took ~23 min and ~56k requests), `uv run python -m app.quant.lab.research` (~35 s),
`uv run python -m app.quant.lab.validate` (~155 s).

### Data (phase 1) — passes
- Source: Binance's public archive (data.binance.vision), monthly 1d klines for every `*USDT` spot pair in the archive
  plus the daily files for the current month; USDT-M funding from the monthly `fundingRate` archive plus the live
  `/fapi/v1/fundingRate` endpoint for the current month. Archive timestamps switch from ms to µs in 2025 (handled).
- Panel 2019-01-01 → 2026-11-10: **791,988 rows, 591 symbols, of which 199 are delisted**. Funding: 518,785 daily
  rows for 471 symbols (the archive starts 2020-01).
- Excluded: 84 archive pairs (stablecoins and fiat incl. UST/USTC, leveraged UP/DOWN/BULL/BEAR tokens, wrapped and
  pegged assets such as WBTC, WBETH, BETH, BNSOL, PAXG, XAUT, WNXM), plus 105 tokenized stocks/ETFs that Binance
  started listing on 2026-06-11 as `{TICKER}B` (NVDAB, TSLAB, SPYB, …), plus KGST and RWUSD (fiat-pegged, found by the
  constant-price check).
- Seven redenominations were back-adjusted like stock splits (COCOS ×1000, DREP ×100, SUN/QUICK ÷1000, BNX ÷100,
  BTCST/STRAX ÷10). Seven tickers that were relisted after a gap of more than 7 days were split into separate
  episodes (LUNA 2020–22 vs LUNA 2.0, FTT, CVC, KEY, NBT, STRAX, VIDT), so no return is computed across a relisting.
- Validation: the last 20 daily closes of BTC, ETH, SOL, BNB, XRP and DOGE match `api.binance.com` exactly (max relative
  difference 0, quote volume within 5e-10). No duplicate rows, no inconsistent OHLC rows. The remaining extreme daily
  moves are real (the LUNA collapse, the OM crash, DOGE in January 2021).

**Universe** (listed, ≥ 90 days of history, 30-day average quote volume ≥ $5M): 545 distinct symbols were ever
tradeable, and **188 of them (34%) were later delisted**. That share is the survivorship bias the old 12–35-coin
research had.

| year | min | median | max names per day |
|---|---|---|---|
| 2019 | 10 | 13 | 19 |
| 2020 | 12 | 25 | 48 |
| 2021 | 46 | 174 | 220 |
| 2022 | 50 | 133 | 211 |
| 2023 | 43 | 86 | 184 |
| 2024 | 106 | 144 | 289 |
| 2025 | 60 | 121 | 265 |
| 2026 | 47 | 64 | 93 |

### Factors (phase 2) — 8 of 18 factors pass
Full-sample (2019–2026) rank IC is the daily Spearman correlation over the universe, with Newey–West t-statistics.
L/S is the top-minus-bottom quintile spread, oriented by the sign of the 7-day IC (an in-sample orientation), after a
0.05% taker fee plus slippage by liquidity bucket (0.02% for ≥ $100M ADV, 0.05% for $20–100M, 0.10% for $5–20M,
0.25% below $5M), per side, without funding. Size is proxied by log 30-day quote volume because Binance publishes no
market-cap history.

| factor | IC1 | IC3 | IC7 | t(IC7) | IC7 BTC>200d | IC7 BTC<200d | L/S weekly net Sharpe | passes |
|---|---|---|---|---|---|---|---|---|
| mom_7 | −0.040 | −0.034 | −0.032 | −4.1 | −0.030 | −0.034 | −0.21 | no |
| mom_14 | −0.044 | −0.036 | −0.035 | −4.0 | −0.029 | −0.045 | −0.44 | no |
| mom_28 | −0.050 | −0.043 | −0.043 | −4.3 | −0.028 | −0.064 | −0.47 | no |
| mom_90 | −0.045 | −0.042 | −0.047 | −4.6 | −0.039 | −0.066 | +0.11 | yes |
| vadj_mom_28 | −0.050 | −0.044 | −0.043 | −4.4 | −0.026 | −0.072 | −0.37 | no |
| rev_1 | +0.035 | +0.022 | +0.003 | +0.7 | −0.000 | +0.006 | −0.65 | no |
| rev_5 | +0.043 | +0.028 | +0.026 | +3.7 | +0.025 | +0.028 | −0.42 | no |
| rvol_30 | −0.100 | −0.109 | −0.133 | −13.9 | −0.137 | −0.122 | +0.85 | yes |
| dist_high_20 | +0.039 | +0.052 | +0.072 | +8.0 | +0.083 | +0.054 | +0.55 | yes |
| dist_high_90 | +0.022 | +0.032 | +0.047 | +4.6 | +0.069 | +0.007 | +0.34 | yes |
| vol_trend | −0.033 | −0.033 | −0.028 | −3.5 | −0.024 | −0.028 | +0.08 | yes |
| taker_imb_7 | −0.006 | +0.000 | +0.007 | +0.9 | +0.004 | −0.004 | −0.26 | no |
| amihud_30 | −0.069 | −0.074 | −0.090 | −9.2 | −0.083 | −0.086 | +0.31 | yes |
| beta_btc_60 | +0.002 | +0.002 | +0.002 | +0.1 | −0.011 | +0.020 | +0.10 | no |
| corr_btc_60 | +0.093 | +0.105 | +0.128 | +13.8 | +0.124 | +0.132 | +1.31 | yes |
| funding_7 | +0.012 | +0.006 | +0.004 | +0.6 | −0.012 | +0.028 | −0.64 | no |
| funding_chg_7 | −0.000 | −0.009 | −0.015 | −2.5 | −0.018 | −0.011 | +0.73 | yes |
| size | +0.026 | +0.027 | +0.033 | +3.7 | +0.026 | +0.023 | −0.38 | no |

What this shows:
- **Momentum is reversed in this universe.** Past 1–13-week winners underperform over the next 1–7 days, in every
  year except 2020 and 2025. Short-horizon momentum also fails after costs in both directions because it turns over
  too fast. This contradicts the weekly-momentum evidence in §1, which comes from market-cap-weighted universes and
  longer holding periods.
- **The strongest effects are "quality/safety" effects:** low volatility, high correlation with BTC, liquidity (low
  Amihud) and being close to the recent high. Their IC is large (0.09–0.13), partly because rank IC measures the
  median: high-volatility, illiquid coins have a negative median return (lottery-like, and many are on the way to
  delisting). Their mean-based quintile spreads are much smaller (net Sharpe 0.3–1.3).
- Funding level does not rank coins. A rise in funding is mildly bearish.
- IC decays slowly for rvol, corr, amihud and dist_high (still 0.05–0.07 at a 20-day lag) and fast for the
  reversal and volume-trend factors. Full tables: `data/quant_lab/factor_report.md`.

### Model ladder (phase 3, walk-forward OOS 2021-01-01 → 2026-11-10)
Each rung is refit every quarter on an expanding window, with training labels purged by the horizon. The composite's
factors are screened on the training dates only (|NW t| ≥ 2 and the same sign in ≥ 60% of years). A rung is kept
only if the paired NW t-statistic of its daily OOS IC improvement is ≥ 1.65.

| h | rung | OOS IC | t | vs simpler | kept | best grid Sharpe (net) |
|---|---|---|---|---|---|---|
| 3d | composite | 0.096 | 15.9 | – | yes | |
| 3d | ridge | 0.125 | 22.7 | +0.029 (t 8.5) | yes | |
| 3d | LightGBM | 0.140 | 28.7 | +0.015 (t 6.9) | yes | |
| 7d | composite | 0.114 | 12.6 | – | yes | 0.29 (all composite configs) |
| 7d | ridge | 0.143 | 17.0 | +0.030 (t 6.4) | yes | 0.52 (all ridge configs) |
| 7d | LightGBM | 0.157 | 20.8 | +0.014 (t 4.5) | yes | 1.45 (all LightGBM configs) |

LightGBM wins the ladder at both horizons. The IC gains are statistically clear, but much of the IC is the
median/lottery effect described above. What can actually be traded is better measured by the portfolio Sharpe.

### Portfolio vs baselines (OOS 2021-01 → 2026-11, net of costs; shorts pay or earn funding)
Grid: 3 rungs × h {3, 7} × rebalance {daily, weekly} × {long only, long–short} × k {5, 10, 20} = 72 configurations.
All use inverse-volatility weights, a 25% per-coin cap, a capacity cap of 2% of ADV at $1M AUM, a volatility target
(50% long-only, 30% long–short), turnover limits (0.4 per day for daily rebalancing, 1.0 for weekly), and longs in
spot, shorts in perps. The selected configuration is the best OOS Sharpe among each horizon's ladder winner.

| strategy | Sharpe | ann. return | ann. vol | CAGR | max DD |
|---|---|---|---|---|---|
| **selected: LightGBM h=3, daily, long–short, k=5** | **1.45** | 38.6% | 26.7% | 41.9% | −24.3% |
| best long-only (LightGBM h=7, daily, k=5) | 0.74 | 35.1% | – | – | −66.0% |
| equal-weight universe | 0.08 | 6.8% | 84.8% | −26.0% | −98.4% |
| BTC buy-and-hold | 0.56 | 31.8% | 56.8% | 17.0% | −76.6% |
| 20-day trend (EW, coins with a positive 20-day return) | −0.21 | −18.2% | 85.7% | −42.7% | −99.7% |

The selected strategy's costs are 17.6% a year (turnover 0.40 per day), and its shorts paid 18.3% a year of funding:
bottom-ranked coins carry heavily negative funding, so shorting them is expensive. Its average gross is 0.31 long
and 0.30 short, with 14 positions.

Stress tests of the selected strategy:

| test | Sharpe |
|---|---|
| base | 1.45 |
| costs ×2 | 0.79 (PSR > 0: 0.969) |
| no costs | 2.11 |
| **1-day execution lag** | **0.40** |
| without the best month (2021-02, +23%) | 1.33 |
| BTC above / below its 200-day average | 2.08 / 0.63 |
| by year 2021 / 22 / 23 / 24 / 25 / 26 | 1.16 / 1.64 / 0.30 / 2.92 / 1.72 / 1.05 |

### Validation
- **CPCV** (10 groups, 2 test groups, purge equal to the horizon, 14-day embargo → 9 paths, run at the selected
  portfolio configuration):

  | rung | path Sharpes | mean | positive paths |
  |---|---|---|---|
  | composite | −0.64 … −0.43 | −0.49 | 0/9 |
  | ridge | −0.07 … 0.09 | 0.04 | 6/9 |
  | LightGBM | 0.53 … 0.98 | 0.82 | 9/9 |

- **Deflated Sharpe** (Bailey & López de Prado 2014): daily SR 0.0759 (1.45 annualised), T = 2,139 days, skew −1.26,
  kurtosis 10.2. N = **144** configurations from `trials.jsonl`: this run's 72 plus the 72 of the first run (lab-1,
  before the tokenized-stock exclusion and the dust-position rule). Across trials, V[SR] = 4.3e-4. Expected maximum
  SR under no skill = 1.06 annualised. **DSR = 0.817, p = 0.183 → not significant.** With only this run's 72 trials
  the first run gave p = 0.131, also not significant.
- **PBO** (CSCV, 16 blocks, 12,870 splits, 72 configurations): **0.154**. The probability that the in-sample winner
  loses out of sample is 8.5%. The degradation slope (OOS on IS Sharpe) is −0.90.

### Decision: nothing is promoted
| gate | result |
|---|---|
| DSR p < 0.05 | **fail** (0.183) |
| PBO < 0.3 | pass (0.154) |
| beats the equal-weight universe after costs | pass (1.45 vs 0.08) |
| survives costs ×2 (PSR ≥ 0.95) | pass (0.969) |

`model.json` was not written. `live_ranking()` therefore returns the equal-weight composite of the 14 factors that
pass the IC screen on the full history, with `promoted: false` and a note saying the ranking is unvalidated.

Honest reading:
- The LightGBM long–short book is the first computer-side result in this project that is positive in every CPCV path
  and every OOS year, and it is not obviously overfitted (PBO 0.15). After deflating for the 144 configurations
  tried, however, its Sharpe is not distinguishable from the best of 144 lucky draws.
- Its edge is fragile in two ways:
  - **One day of execution delay removes about 70% of it** (Sharpe 1.45 → 0.40). Part of the signal is a 1-day
    reversal that has to be traded at the same close the signal is computed on.
  - About half of its gross edge goes on costs and short funding.
- Long-only results, which are what Today's 5 needs, are weak: the best is Sharpe 0.74 with a 66% drawdown, close to
  BTC buy-and-hold (0.56, −77%).
- What does look robust is the factor evidence: avoid high-volatility, illiquid, low-correlation coins and coins far
  below their highs. It works better as a filter or a risk overlay for the picks than as a stand-alone alpha.

Suggested next steps (each new attempt is logged and raises N):
- A pre-registered narrower test: LightGBM h=3, long–short, weekly rather than daily rebalancing, with the 1-day lag
  built into the base case.
- Record open interest daily, as phase 1 planned.
- Use the factor screen as a universe filter for Today's 5, and paper-trade it in the sandbox (phase 4) rather than
  promoting anything now.

Caveats:
- The 2019–2020 universe is small (10–48 names), and the funding archive starts in 2020-01.
- Size is proxied by ADV, because there is no market-cap history.
- A delisted coin is assumed to exit at its last traded close.
- Execution at the signal close is optimistic, as the 1-day-lag stress test shows.
- Shorts are only allowed where a perp existed. Borrow limits and liquidation risk are not modelled.
- The tokenized-stock rule uses the "B" suffix plus a listing date on or after 2026-06-01, and needs review if Binance
  changes its naming.

## Quant lab round 2: execution re-test, CTREND, conformal abstention (run 2026-11-11)

Two pre-registrations, each written before its run and not edited afterwards:
docs/research/EXECUTION_PREREG_2026-11-11.md (step A) and docs/research/CTREND_CONFORMAL_PREREG_2026-11-11.md
(step B). Code: `portfolio.py` (next-day fills, maker fill rule, no-trade band, calendar-anchored schedule,
close-marked accounting, `live_book`), `retest.py`, `retest_b.py`, `ctrend.py`, `conformal.py`; tests in
`tests/test_quant_lab_exec.py` (9) and `tests/test_quant_lab_ctrend_conformal.py` (6). Commands:
`uv run python -m app.quant.lab.retest exec` (~150 s, of which CPCV ~90 s), `... retest exec_recheck` (~6 s once the
walk-forward predictions are cached in `data/quant_lab/wf_preds.npz`), `... retest ctrend_conformal` (~120 s).
Outputs: `exec_retest_report.json`, `ctrend_conformal_report.json`, `ctrend_wf.csv.gz`, `ctrend_fits.json`.
The legacy same-close path still reproduces lab-2 exactly (selected book Sharpe 1.449).

**Result: nothing is promoted. N = 159 strategy configurations are now logged** (144 + 12 + 3). Every new book fails
only the deflated-Sharpe test; adding configurations raises the bar (expected best-of-N Sharpe: 1.06 at N = 144, 1.16
at N = 156, 1.21 at N = 159, because the new configurations are good ones and V[SR] rose from 4.3e-4 to 5.1e-4).

### Step A — next-open fills, maker orders, no-trade band, weekly books
A data fact found while writing the pre-registration: Binance's daily candle of D+1 opens one millisecond after D
closes, so |open(D+1)/close(D) − 1| has a median of 3.3 bp (36% exactly equal). **"Fill at the next open" is,
in a 24/7 market, practically the signal close.** It removes the logical look-ahead but not the latency. The
pre-registration therefore added gate G5: the book must also survive fills at the VWAP of D+1.

OOS 2021-01-02 → 2026-11-09, net of costs and funding; decisions at the close of D, filled at the open of D+1.

| config | Sharpe | CAGR | max DD | turnover/day | costs/yr | funding/yr | maker fill rate | costs ×2 | +1 day delay | VWAP fill* | maker 25 bp |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **A taker, no band (selected)** | **1.46** | 42.5% | −23.5% | 0.396 | 17.6% | −18.3% | – | 0.80 | 0.41 | 1.45 | – |
| A taker, band 0.02 | 1.43 | 41.2% | −22.6% | 0.384 | 17.2% | −17.6% | – | 0.79 | 0.41 | 1.49 | – |
| A maker, no band | 1.20 | 32.5% | −25.4% | 0.388 | 2.8% | −17.9% | 97.8% | 1.09 | 0.16 | 2.02 | 0.47 |
| A maker, band 0.02 | 1.17 | 31.6% | −25.6% | 0.376 | 2.8% | −17.3% | 97.8% | 1.07 | 0.17 | 2.05 | 0.46 |
| B taker, no band | 0.75 | 21.3% | −55.0% | 0.113 | 5.1% | −11.3% | – | 0.60 | 1.44 | 0.91 | – |
| B taker, band 0.02 | 0.74 | 21.0% | −55.7% | 0.111 | 4.9% | −11.1% | – | 0.59 | 1.40 | 0.90 | – |
| B maker, no band | 0.76 | 21.2% | −55.7% | 0.111 | 0.8% | −10.8% | 97.4% | 0.73 | 1.35 | 1.04 | 0.62 |
| B maker, band 0.02 | 0.75 | 20.9% | −56.6% | 0.108 | 0.8% | −10.7% | 97.5% | 0.72 | 1.33 | 1.03 | 0.62 |
| C taker, no band | 1.06 | 30.7% | −40.0% | 0.139 | 6.5% | −10.4% | – | 0.84 | 1.17 | 1.37 | – |
| C taker, band 0.02 | 0.97 | 27.8% | −41.2% | 0.134 | 6.3% | −9.6% | – | 0.76 | 1.04 | 1.27 | – |
| C maker, no band | 1.02 | 28.9% | −44.5% | 0.136 | 1.0% | −10.3% | 97.3% | 0.99 | 1.11 | 1.56 | 0.74 |
| C maker, band 0.02 | 0.92 | 25.4% | −45.3% | 0.131 | 1.0% | −9.5% | 97.4% | 0.89 | 0.98 | 1.46 | 0.64 |

Books: A = LightGBM h 3, daily, k 5 (lab-2's selected book); B = the same, weekly; C = LightGBM h 7, weekly, k 10. All
long–short. Same books at the signal close (lab-2): A 1.45, B 0.73, C 1.06. Baselines with next-open fills:
equal-weight universe 0.08, BTC buy-and-hold 0.57.
\* VWAP fill, marked at closes (old holdings earn close D → VWAP D+1, new holdings VWAP D+1 → close D+1). The first
run marked positions VWAP-to-VWAP, which smooths daily returns and overstates Sharpe (A 1.59, A maker 2.23, C 1.54);
this was caught after the run, corrected (`retest exec_recheck`, logged as stress) and G5 re-evaluated on the corrected
number. Re-marking the next-open books at closes changes nothing (A 1.46). A maker order at the VWAP is assumed to
fill in full at the maker fee with no adverse selection, so the "maker" VWAP column is optimistic.

Sharpe by year and BTC regime:

| config | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | BTC > 200d | BTC < 200d |
|---|---|---|---|---|---|---|---|---|
| A taker | 1.15 | 1.63 | 0.31 | 2.93 | 1.75 | 1.07 | 2.10 | 0.63 |
| A maker | 0.73 | 1.57 | −0.11 | 2.53 | 1.50 | 1.09 | 1.77 | 0.45 |
| B taker | 0.74 | 1.22 | −1.80 | 1.13 | 1.82 | 1.71 | 0.55 | 0.97 |
| C taker | 1.22 | 1.30 | −1.13 | 1.41 | 2.14 | 1.87 | 1.22 | 0.88 |
| C maker | 1.35 | 1.43 | −1.39 | 1.35 | 1.90 | 1.95 | 1.08 | 0.95 |

(Band variants track their no-band book within ±0.2 in every year; full table in `exec_retest_report.json`.)

Gate for the selected configuration (A taker, no band):

| gate | result |
|---|---|
| G1 DSR p < 0.05 (N = 156, V[SR] = 5.1e-4, SR0 = 1.16 annualised) | **fail: p = 0.246** |
| G2 PBO < 0.3 (CSCV over the 12) | pass: 0.131 |
| G3 beats the equal-weight universe | pass: 1.46 vs 0.08 |
| G4 costs ×2 PSR ≥ 0.95 | pass: 0.971 (Sharpe 0.80) |
| G5 VWAP fill PSR ≥ 0.95 | pass: 0.999 (Sharpe 1.45) |

CPCV (9 paths, selected model and execution): Sharpe 0.54 … 0.99, mean 0.82, 9/9 positive.

What step A shows:
- **Next-open ≈ same-close (1.46 vs 1.45)**, as the data fact predicted. The real timing question is latency. A fill
  spread over the next 24 hours (VWAP) keeps the edge (1.45), but a full extra day of delay removes most of it (0.41).
  So the edge lives in roughly the first day after the signal, not in the first minutes. That is good news for
  execution: the book does not need a sub-second fill at the close.
- **The maker variant is worse for the daily book (1.20 vs 1.46)**, even though costs fall from 17.6% to 2.8% a year.
  Only 2.2% of orders go unfilled, but they are the ones where the price runs away from the limit (buys that never
  trade 10 bp below the open). For a book with a reversal component, those are its best trades. The gross return
  falls by about 22 points a year, more than the 15 points saved on costs. With a 25 bp trade-through rule the maker
  book falls to 0.47. For the weekly books the maker variant is roughly neutral (B) or slightly worse (C). Maker
  execution only pays if missed fills are chased (a hybrid). That needs the recorded order books (TECH_PLAN 1.2)
  before it can be tested honestly.
- **The no-trade band does almost nothing.** Turnover falls by only 3% (0.396 → 0.384): it comes from names entering
  and leaving the top/bottom k, not from resizing. Net Sharpe is flat or slightly lower. TECH_PLAN 4.2's pass test
  (at least 40% less turnover, higher net Sharpe) fails. Rank hysteresis (hold a name until it leaves the top 2k) is
  the obvious next candidate, as a new logged trial.
- **Weekly books are not more delay-robust; their results depend on which weekday they trade.** B's Sharpe goes from
  0.75 to 1.44 with one day of delay, and C's from 1.06 to 1.17. These changes are noise from the schedule phase,
  not signal: for a weekly book, a one-day delay mostly means trading on a different weekday. Read weekly Sharpes as
  ±0.5.

### Step B — CTREND
CTREND: 20 technical indicators (price relative to its 3–200-day moving averages, volume ratios, RSI, stochastics,
CCI, MACD, Bollinger %B, Chaikin money flow). Each is ranked across the universe and combined by an elastic net. The
net is refit monthly on past data only (83 fits from 2020-01). It keeps about 10 of the 20 indicators; the largest
average weights go to the 200-day volume ratio, Chaikin money flow, stochastic %D, the 10-day moving-average ratio
and the MACD histogram.

| span | IC1 | IC3 | IC7 | t(IC7) | IC7 BTC>200d | IC7 BTC<200d | L/S weekly net SR | L/S daily net SR | rank AC |
|---|---|---|---|---|---|---|---|---|---|
| 2020-01 → (walk-forward) | +0.094 | +0.093 | +0.098 | 12.1 | +0.087 | +0.112 | 1.07 | 1.56 | 0.92 |
| 2021-01 → (lab OOS) | +0.095 | +0.093 | +0.097 | 11.8 | +0.085 | +0.112 | 1.08 | 1.61 | 0.92 |

IC7 by year: 2020 0.117, 2021 0.102, 2022 0.102, 2023 0.104, 2024 0.093, 2025 0.065, 2026 0.120. Weekly quintile L/S
net return by year: 2021 +97%, 2022 +25%, 2023 +15%, 2024 +32%, 2025 +59%, 2026 +92% (annualised, no funding).
**Factor pass test: passes** (t 11.8 > 3; positive in 5 of 5 years). Stand-alone, CTREND is among the
strongest factors in the lab. Its weekly L/S Sharpe of 1.08 is second only to corr_btc_60's 1.31, and that 1.31 is
oriented in-sample, while CTREND is fully walk-forward.

But it adds nothing to the models that already see the other 18 factors:

| rung | OOS IC without | with CTREND | diff | paired NW t | improves |
|---|---|---|---|---|---|
| composite h3 | 0.0964 | 0.0999 | +0.0035 | 4.57 | yes |
| ridge h3 | 0.1253 | 0.1266 | +0.0014 | 1.37 | no |
| LightGBM h3 | 0.1404 | 0.1408 | +0.0003 | 0.39 | no |
| composite h7 | 0.1135 | 0.1160 | +0.0025 | 1.83 | yes |
| ridge h7 | 0.1430 | 0.1437 | +0.0008 | 0.69 | no |
| LightGBM h7 | 0.1573 | 0.1559 | −0.0014 | −1.30 | no |

The book with CTREND (A taker + LightGBM h3 incl. CTREND) has Sharpe 1.52, CAGR 45.7%, max DD −24.6%, costs
17.8%/yr, costs ×2 0.87, +1 day delay 0.80, VWAP 1.69. By year: 0.84 / 1.37 / 0.50 / 3.40 / 2.03 / 1.15. BTC above
/ below its 200-day average: 2.41 / 0.57. The +0.06 Sharpe gain over the base book is within noise: the model's IC
is unchanged (t 0.39). Gate: DSR p = 0.246 (N = 159) → fail. PBO, costs ×2 and VWAP pass.

### Step B — conformal abstention
Rolling split-conformal intervals around each coin's predicted rank are calibrated on the last 90 days of realised
residuals and scaled by the coin's own recent residual size. Coverage on 268,573 OOS rows (2021-01-29 →):

| nominal | coverage | by year (2021…2026) | coins whose interval excludes the neutral rank (long / short) |
|---|---|---|---|
| 90% | **90.0%** | 89.9 – 90.1% | 0.24% / 0.15% |
| 75% | 75.1% | 74.9 – 75.2% | 5.5% / 4.2% |
| 50% | 50.2% | 49.7 – 50.4% | 26.0% / 22.6% |

The coverage test passes (90% ± 3%, and stable every year). As predicted before the run, though, a 90% interval
almost never excludes the neutral rank (half-width ≈ 1.76 × σ, σ ≈ 0.36), so abstaining at 90% would empty the book.

| book (same window, from 2021-01-29) | Sharpe | CAGR | max DD | positions | Δ Sharpe vs no abstention | bootstrap p |
|---|---|---|---|---|---|---|
| A taker, no abstention | 1.47 | – | – | 14.0 | – | – |
| abstain at α = 0.5 | 1.49 | 43.5% | −23.2% | 14.1 | +0.02 | 0.34 |
| abstain at α = 0.25 | 0.67 | 16.8% | −35.9% | 13.2 | −0.79 | 0.98 |

**Abstention does not raise the traded Sharpe.** At α = 0.5 it changes almost nothing, because the top/bottom-5 names
almost always have intervals that already exclude 0. At α = 0.25 it swaps the most extreme predictions for less
extreme ones whose coins happen to have small recent residuals, and the book gets much worse. Gates: α = 0.5 DSR
p = 0.259 → fail; α = 0.25 fails DSR, costs ×2 (PSR 0.54) and VWAP (0.94). PBO over the round's 15 configurations:
0.199.

### Decision and what follows
| book | Sharpe | DSR p (N) | PBO | costs ×2 PSR | VWAP PSR | promoted |
|---|---|---|---|---|---|---|
| A taker, next open (step A) | 1.46 | 0.246 (156) | 0.131 | 0.971 | 0.999 | no |
| A + CTREND | 1.52 | 0.246 (159) | 0.199 | 0.977 | 1.000 | no |
| A + conformal α 0.5 | 1.49 | 0.259 (159) | 0.199 | 0.974 | 0.999 | no |
| A + conformal α 0.25 | 0.67 | 0.889 (159) | 0.199 | 0.544 | 0.943 | no |

`model.json` was not written; `live_ranking()` still returns `promoted: false` (the unvalidated composite). If a
book is promoted later through `retest.promote`, `live_ranking()` replays it with the same engine: calendar schedule,
no-trade band, next-open/maker settings. Its `portfolio` field then exposes `execution`, `order_type`,
`no_trade_band`, `rebalance_anchor`, `rebalance_today`, `next_rebalance`, `orders` and `maker`. `latest_report()` now
includes a `retests` block with both decisions. `validate.run` (lab-2) now retires only a model.json that it wrote
itself.

Honest reading:
- The lab-2 book survives realistic timing. Next-open, VWAP-of-the-next-day and close-marked accounting all give
  about 1.45, and CPCV is 9/9 positive. It is not a same-close artefact; it is a "trade within the next day" edge.
  What it cannot survive is a full day of delay or a passive-only fill rule.
- The binding constraint is now purely statistical: about 150 configurations have been tried on one 5.9-year OOS
  window. A Sharpe of about 1.5 with skew −1.3 is not enough at that N (it would need about 1.8). More variants on the
  same window can only make that worse. The way forward is new data the book has not seen: the forward sandbox run
  under the exact next-open execution, and a holdout of coins or periods.
- CTREND is a good factor, but it is redundant once LightGBM sees the volatility, liquidity and distance-from-high
  factors. It is worth using where the composite is used (the unpromoted live ranking and the picks' risk overlay),
  where it adds IC with t 4.6.
- The weekly books have large schedule-phase noise. Any future weekly test should average over the 7 phases.

Caveats: fee levels follow the lab's existing model (0.05% taker, 0.02% maker as instructed). Binance spot at VIP 0
charges 0.10% maker and taker (0.075% with BNB), so the spot (long) leg's costs are understated for a small account.
The maker fill rule uses daily ranges, not queue position or the order book. The VWAP fill assumes the book's size is
small relative to the day's volume (it is capped at 2% of ADV). Funding is attributed to the fill day, as in lab-2.
Delisted coins exit at their last close.
