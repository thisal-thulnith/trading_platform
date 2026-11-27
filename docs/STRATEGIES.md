# Strategies

All strategies are deterministic functions of an analysis snapshot built from **closed candles only**. Each
exposes entry, exit, invalidation, stop and target rules, suitable regimes, required data and an evidence grade
(from RESEARCH.md). Live specs: `GET /api/strategies`.

| id | Family | Regimes | Entry trigger | Stop | Evidence |
|---|---|---|---|---|---|
| trend_pullback | trend following | strong/weak bull/bear | Pullback into the EMA20–EMA50 band, then a close back beyond EMA20 with RSI > 50 (long) | Below the 5-bar extreme − 0.3 ATR | B |
| smc_ob_retest | SMC continuation | trends, range | First tap of an unmitigated OB in discount (long) / premium (short), then a reaction close or internal CHoCH | Beyond the OB ± 0.25 ATR | C |
| liquidity_sweep_reversal | reversal | range, weak trends, high vol | Sweep (wick through resting liquidity, close back inside), then an internal structure shift within 10 bars | Beyond the sweep extreme ± 0.2 ATR | C |
| breakout_retest | breakout | breakout, trends, low vol | Close beyond the 25-bar range on RVOL > 1.5, then a retest that holds | Other side of the level − 0.6 ATR | B |
| fvg_continuation | SMC continuation | trends | Retrace into an open FVG in the trend direction, then a close back out | Beyond the FVG ± 0.3 ATR | C |
| vwap_reversion | mean reversion | range, low vol | Close back inside the 2σ session-VWAP band after a close outside, with RSI stretched | Beyond the excursion extreme | B |
| volatility_breakout | volatility | low vol, range, breakout | BB-inside-KC squeeze released with a close beyond the band and RVOL > 1.5 | Opposite side of the squeeze range | B |
| funding_extreme_fade | derivatives | weak trends, range, high vol | Funding \|z\| > 2.5 with rising OI, then structure shifts against the crowded side | Beyond the last 10-bar extreme | B (live only: not enough funding history to backtest) |

Targets default to 1.5R / 2.5R / 4R. TP1 snaps to the nearest resting liquidity level between 1R and 4R.

## Stages and validation
`forming` (context met, part of the setup present) → `pre_signal` (setup complete, waiting for the trigger) →
`confirmed`. Confirmation additionally requires **every** validation gate:
net R:R to TP1 ≥ 1.2 after fees and slippage · stop 0.25–6 ATR · data fresh (≤ 2 bars + 2 min) · regime not
`liquidity_stress` / `news_instability` · confluence bias aligned (≥ 0.1) · ≥ 3 independent evidence families
supporting · ≤ 2 opposing families · validated ML model (if one exists) not contradicting · spread ≤ 10 bps ·
forward-test expectancy not negative over ≥ 30 signals · **research gate**: latest backtest on that
timeframe must not be statistically negative (95% CI upper bound < 0 with n ≥ 30 ⇒ suspended).
A failed gate turns a confirmation into an explicit **NO TRADE** with the reasons listed.

## Execution model (identical for live tracking and backtests — `app/signals/simulator.py`)
- Limit entry in the zone: a long fills at `min(open, entry_high)` once low ≤ entry_high, plus slippage.
- A stop touched before the fill means INVALIDATED. No fill within `expiry_bars` means EXPIRED.
- If stop and target both fall inside one candle, **the stop is assumed first**.
- Exits are 1/3 at each TP. After TP1 the stop moves to breakeven. A time stop applies at 3× the expected holding period.
- Costs: 0.05% taker fee per fill and 2 bps slippage on entry and stop exits (Binance USDⓈ-M VIP0, not verified
  per account). Funding is not applied in spot-data backtests.

## Backtest results (simulated — not real trading)
Configuration: Binance spot candles, fixed priors (no parameter optimisation), 1% risk per trade, max 5
concurrent positions, fill-or-expire limit entries. **R is measured against planned risk** (entry-zone midpoint
to stop). Runs made before the 2026-11-01 simulator fix were marked `invalidated`; the numbers below are the
corrected re-runs.

### 1h · BTC, ETH, SOL, XRP, BNB · 2026-03-08 → 2026-11-01 (1,014 trades)
| Strategy | n | Expectancy (R) | 95% CI | Win % | Gate |
|---|---|---|---|---|---|
| fvg_continuation | 299 | −0.65 | [−0.98, −0.31] | 21% | **suspended** |
| trend_pullback | 267 | −0.20 | [−0.37, −0.02] | 34% | **suspended** |
| volatility_breakout | 159 | −0.07 | [−0.24, +0.11] | 40% | inconclusive |
| breakout_retest | 144 | −0.29 | [−0.62, +0.06] | 28% | inconclusive |
| liquidity_sweep_reversal | 74 | **+0.30** | [+0.03, +0.58] | 61% | inconclusive (lower bound < +0.05R; 6 strategies tested) |
| smc_ob_retest | 71 | −0.53 | [−0.91, −0.10] | 23% | **suspended** |
| **All** | **1,014** | **−0.31** | **[−0.44, −0.18]** | 31% | |

Cost stress: base −0.31R · fees ×2 −0.49R · slippage ×3 −0.44R · combined stress −0.75R.
In-sample −0.22R (n=702) · out-of-sample −0.51R (n=312).

### 4h · BTC, ETH, SOL, XRP, BNB · 2025-08-08 → 2026-11-01 (505 trades, 15 months)
| Strategy | n | Expectancy (R) | 95% CI | Win % | Gate |
|---|---|---|---|---|---|
| trend_pullback | 187 | −0.03 | [−0.23, +0.18] | 39% | inconclusive |
| fvg_continuation | 142 | −0.41 | [−0.84, +0.07] | 21% | inconclusive |
| breakout_retest | 73 | +0.22 | [−0.32, +0.75] | 37% | inconclusive |
| volatility_breakout | 72 | −0.03 | [−0.30, +0.26] | 44% | inconclusive |
| liquidity_sweep_reversal | 21 | −0.03 | [−0.54, +0.54] | 43% | insufficient |
| smc_ob_retest | 10 | −0.20 | — | 30% | insufficient |
| **All** | **505** | **−0.11** | **[−0.27, +0.07]** | 34% | |

Cost stress: base −0.11R · fees ×2 −0.18R · slippage ×3 −0.18R · stress −0.30R.
In-sample **+0.08R** (n=344) · out-of-sample **−0.51R** (n=161). The edge seen in-sample did not survive out
of sample.

### 15m
Corrected run `9b224642` (2026-09-03 → 2026-11-01, top-20 universe):

| Strategy | n | E[R] | 95% CI | Win | Gate |
|---|---|---|---|---|---|
| fvg_continuation | 310 | −0.56 | [−0.95, −0.14] | 25% | suspended |
| trend_pullback | 179 | −0.09 | [−0.32, +0.15] | 38% | inconclusive |
| breakout_retest | 156 | −0.90 | [−1.22, −0.56] | 19% | suspended |
| volatility_breakout | 104 | −0.27 | [−0.48, −0.07] | 33% | suspended |
| smc_ob_retest | 71 | −0.76 | [−1.17, −0.29] | 21% | suspended |
| liquidity_sweep_reversal | 36 | +0.12 | [−0.27, +0.53] | 42% | inconclusive |
| **All** | **856** | **−0.48** | **[−0.65, −0.29]** | 28% | |

Cost stress: base −0.48R · fees ×2 −0.87R · slippage ×3 −0.77R · stress −1.47R. In-sample −0.46R (n=612) ·
out-of-sample −0.52R (n=244). 15m is the worst timeframe: costs are large relative to the stop distance, and average
losses exceed 1R (−1.8R) because fills often land at the far edge of wide entry zones (R is measured on planned risk).

**Conclusion:** after correcting the simulator, the default strategy set still has **no demonstrated edge**:
negative on 1h and 15m with statistical confidence, and flat to negative on 4h, with out-of-sample collapse. SMC
continuation entries (FVG, OB retest) are the worst performers. Liquidity-sweep reversal on 1h is the only
candidate worth further, pre-registered study. The platform abstains far more often than it signals, and it
suspends statistically losing strategy/timeframe pairs automatically.

## Exit-profile study

Tool: `uv run python -m app.maintenance.exit_study gen|study --tf 4h|1h` (`app/maintenance/exit_study.py`).
Setups are generated once (one full live snapshot per bar, no look-ahead) for 19 symbols with ≥5,000 hourly bars
(2025-05 → 2026-11), cached, then re-simulated under 12 exit profiles × 5 filters × 2 strategy selections.
The split is by time: first 70% in-sample (IS), last 30% out-of-sample (OOS).

### Why: the "95% win rate" question
The win rate rises as the take-profit shrinks, but expectancy does not: at TP = 0.1R the win rate is about 87% and
the strategy loses money. The goal is positive expectancy after costs, not the highest possible win rate.

### 4h — 19 symbols, 9,819 setups, split 2026-05-29
- **The pre-registered rule (highest IS expectancy) failed OOS:** the strategy-native exits with quality≥50 and
  IS-positive strategies scored IS +0.19R and OOS −0.10R. The baseline (strategy-native exits) scored IS +0.14R and
  OOS **−0.15R [−0.28, −0.02]**.
- **Consistent in both halves:** `edge_tp1r_q50` (one limit at the zone edge, a single TP at +1R, quality≥50):

| | n | Win | E[R] | 95% CI |
|---|---|---|---|---|
| IS | 1,060 | 57% | +0.069 | [+0.01, +0.12] |
| OOS | 429 | 59% | **+0.107** | [+0.01, +0.20] |

- Neighbouring settings agree in direction (TP 0.75R OOS +0.08R at 65% win; TP 1.5R +0.08R), so the result
  is not a single lucky point. It was, however, identified after seeing OOS numbers, so it was **pre-registered and
  tested once on 1h** (`data/setups/PREREGISTRATION.md`) before adoption beyond 4h.
- **Adopted for live 4h signals** (`AIT_EXIT_PROFILES`, default `{"4h": "edge_tp1r_q50"}`) as an experimental
  profile; the forward test and research gate keep watching it.

### 1h replication (pre-registered) — 19 symbols, 34,871 setups, split 2026-05-23
| | n | Win | E[R] | 95% CI |
|---|---|---|---|---|
| `edge_tp1r_q50` IS | 3,648 | 59% | +0.020 | [−0.01, +0.05] |
| `edge_tp1r_q50` OOS | 1,554 | 59% | +0.017 | [−0.03, +0.06] |
| strategy-native exits OOS | 4,236 | 33% | −0.121 | [−0.19, −0.05] |

**Not replicated:** the profile lifts 1h from clearly losing to break-even, but not to a demonstrable edge, so per the
pre-registration it is **not adopted on 1h**. The 1h pick ranked highest on IS failed again OOS (+0.13R → −0.32R).
Implications: 4h is the only timeframe with positive evidence, and it should be treated as experimental (one
discovery sample, a weak replication on a correlated timeframe). Typical 4h trade: stop ≈ 1.9% away, so the
+1R target ≈ a 1.9% move, held about 20 bars (≈ 3 days).

## Schools of thought

`app/analysis/schools.py` turns ten classic methods into rules: Wyckoff, Dow, Gann, Market Profile, Harmonics, ICT Power of 3 / sessions, NeoWave (simplified Neely rules), Hurst cycles, Quasimodo and Bill Williams fractals. `app/maintenance/school_study.py` tests whether each method's direction call (+1/−1) matches the forward return:
- data: 19 symbols, sampled every 3 bars on 4h (20-bar horizon) and every 6 bars on 1h (24-bar horizon);
- split: 70/30 by time;
- confidence intervals widened for overlapping windows;
- a method counts as "useful" only if the 95% CI lower bound is above 0 in **both** halves, because 11 hypotheses are tested per timeframe.

| 4h, out-of-sample | n | hit rate | baseline | mean signed fwd return |
|---|---|---|---|---|
| Wyckoff | 1107 | 50% | 52% | −0.11% |
| Dow | 2691 | 44% | 52% | −0.60% |
| Gann | 4270 | 50% | 52% | +0.22% |
| Market Profile | 1783 | 47% | 52% | +0.08% |
| Harmonics | 51 | 59% | 52% | +0.93% (CI ±4.4%) |
| ICT PO3 | 739 | 53% | 52% | +0.14% |
| NeoWave | 19 | 5% | 52% | −4.35% |
| Hurst | 4065 | 54% | 52% | +0.50% (CI −0.08…+1.07) |
| Quasimodo | 1582 | 51% | 52% | +0.09% |
| Fractals | 792 | 48% | 52% | +0.20% |
| Council vote (≥2 net) | 2333 | 49% | 52% | +0.21% |

No method qualifies as useful on 4h or 1h.
- On 1h, Market Profile and the council vote were positive out-of-sample, but negative or not significant in-sample.
- **Decision:** in the War Room, the schools advise (veto or half size) but never trigger trades.
- The War Room shows each agent's own out-of-sample record next to its opinion.

## Daily 5 — historical simulation and evolution (2026-11-11)

Code: `backend/app/research/` — `selector.py` (the one scoring/picking function, live and historical),
`simulate.py` (`uv run python -m app.research.simulate --tf 4h`), `evolution.py`
(`uv run python -m app.research.evolution --tf 4h`, run log in `data/research/evolution_{tf}.jsonl`, promoted params
in `data/research/selector_{tf}.json`). Data: the engine setups cached by `app/ml/meta_label.py` (never regenerated),
labelled by `label_setups` (edge_tp2r: limit at the zone edge, strategy stop, one +2R target, 0.05% taker fee + 2 bps
slippage).

**Method.** Score = documented linear rule over setup features (quality, HTF agreement, room to the nearest opposing
level, stop-distance band, regime alignment, confluence count, limit distance, funding, per-strategy bias). Each UTC
day: the top 5 eligible setups created that day, at most one per coin and 3 per side. Book: max 5 open positions or
pending orders, one per coin, skipped picks are not back-filled, 1% of equity risked per trade. Unfilled setups are
candidates too (you cannot know live whether a limit fills); an unfilled pick earns 0R and blocks its slot until
expiry. Expectancy CIs: circular block bootstrap over calendar days (10-day blocks). Random-5 = mean of 200 draws.

### Full history, DEFAULT_PARAMS

4h: 12 coins, 2022-11-04 to 2026-11-02, 11,232 candidate setups (8.1 per day).

| Book | Trades | /day | Win | +2R hit | E[R] | 95% CI | Total R | CAGR @1% | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| Selected 5 | 1,941 | 1.33 | 33.5% | 32.8% | −0.072 | [−0.143, −0.004] | −140R | −35% | 84% |
| Take all | 2,659 | 1.82 | 36.4% | 35.7% | +0.021 | [−0.047, +0.087] | +55R | +3% | 50% |
| Random 5 | 2,520 | 1.73 | 34.9% | 34.2% | −0.025 | p5–p95 [−0.053, +0.001] | −62R | −22% | 74% |

1h: 19 coins, 2025-05-22 to 2026-11-11, 11,900 candidates (22 per day).

| Book | Trades | /day | Win | +2R hit | E[R] | 95% CI | Total R | CAGR @1% | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| Selected 5 | 1,363 | 2.53 | 29.5% | 28.6% | −0.223 | [−0.309, −0.140] | −304R | −89% | 96% |
| Take all | 3,239 | 6.01 | 33.4% | 31.7% | −0.110 | [−0.198, −0.024] | −357R | −94% | 99% |
| Random 5 | 1,830 | 3.40 | 32.2% | 30.3% | −0.147 | p5–p95 [−0.182, −0.106] | −269R | −86% | 95% |

The default selector is **worse than random** on both timeframes (its expectancy beats 0 of 200 random draws). Its
priors (prefer high quality, HTF agreement and regime alignment) point the wrong way in this data; on the 4h train
window, setups against the regime averaged +0.29R and aligned ones +0.00R. The defaults were left as written (priors)
rather than flipped after seeing the results.

### Evolution, walk-forward (24 candidates × 8 generations, seed 7, ~15 s per run)

Train = first 60% of the span (purged: trades must resolve before the cut); OOS = after the cut, never seen by the
search. Objective on train: E[R] − 0.5·(trade shortfall vs half of take-all) − 0.2·max drawdown.

4h, OOS 2025-03-28 to 2026-11-02:

| Book | Trades | Win | +2R hit | E[R] | 95% CI | Total R | CAGR @1% | Max DD |
|---|---|---|---|---|---|---|---|---|
| Evolved (train-best) | 773 | 36.9% | 36.5% | +0.039 | [−0.081, +0.157] | +30R | +11% | 35% |
| DEFAULT_PARAMS | 779 | 32.1% | 31.7% | −0.116 | [−0.228, +0.006] | −90R | −47% | 68% |
| Take all | 1,078 | 36.0% | 35.4% | +0.006 | [−0.103, +0.114] | +7R | −7% | 50% |
| Random 5 | 999 | 34.2% | 33.7% | −0.047 | p5–p95 [−0.081, −0.010] | −47R | −32% | 63% |

Stress on the evolved set: fees and slippage ×2 → −0.032R; without its best month (2025-07, +29R) → +0.001R; the
seed-8 re-run's train-best → −0.084R OOS. Over seeds 1–5 the train-best scored +0.08 to +0.22R on train and −0.11 to
+0.01R OOS: the search fits noise. (Take-all is +0.006R per trade yet −7%/yr because five overlapping,
correlated positions compound losses faster than wins.)

1h, OOS 2026-04-09 to 2026-11-11: evolved −0.134R [−0.284, +0.015] (575 trades), DEFAULT −0.149R, take all
+0.007R [−0.110, +0.124] (1,254), random 5 −0.067R. Seeds 1–5: −0.06 to −0.10R OOS.

**Decision: nothing promoted on either timeframe.** 4h failed: CI lower bound > 0, costs ×2, and the other-seed check.
1h also failed: beating take-all and dropping the best month. `promoted_params()` returns DEFAULT_PARAMS with
`source: "default"`. No selector rule tested here beats taking setups in time order out of sample, and that book is
itself ~0R after costs. "Today's 5" is a way to limit and size exposure, not a demonstrated edge.

### Look-ahead trap: "filled trades only"

If only setups that later fill are candidates, the same 4h books look far better. Take-all goes to +0.199R
[+0.134, +0.266] on 3,290 trades, and selected goes to +0.092R. The cause is hindsight: knowing that a pending limit
will never fill frees its slot. Filled setups that arrived while an earlier same-side setup on the same coin was still
unfilled (and never would fill) averaged +0.40R (n = 2,496), against −0.12R for the rest.

A causal version is the number of the coin's earlier same-side setups still untouched at setup time. On 4h, ≥ 2 such
setups gave +0.09 / +0.30R on train (2 / ≥3; n = 453 / 307) and +0.27 / +0.19R OOS (n = 324 / 175), against about 0R
with none. It did not hold on 1h. This was found after looking at OOS data, so it is a hypothesis to pre-register and
forward-test, not a result. It is not in the selector.

**Caveats:**
- Survivorship: these are 12 and 19 of today's liquid coins.
- Positions overlap and are correlated across coins: five open positions behave like about 2–3 independent bets, and
  the day-block bootstrap captures only part of this.
- Within a day the selector sees all of that day's setups (when they appear, not their outcomes). The live job sees
  only what is on the board when it runs.
- The labels use edge_tp2r, while live 4h uses edge_tp1r_q50.
- Backtest snapshots lack order-book and derivatives data.
- Only one walk-forward split was used, and the 1h OOS is about 7 months.

## Hypothesis round 2026-11-11 (pre-registered)

The hypotheses, splits and pass criteria were written down before anything was evaluated:
`docs/research/HYPOTHESES_2026-11-11.md`. That file was not changed afterwards.
- Code: `backend/app/research/hypotheses.py` (`uv run python -m app.research.hypotheses run`).
- Record: `backend/data/research/hypotheses_2026-11-11.jsonl`. It holds the TRAIN-stage registration, written
  before the OOS evaluation, and then the result.
- Filters: optional params in `selector.py`, off by default.
- Context features: `app/research/context.py` and `meta_label.setup_row(..., context=)` / `live_setup_features(...,
  context=)`.

**Setup.** Every hypothesis is the same neutral daily-5 book plus one rule. The neutral book uses no scoring, takes
the earliest setups of the day first, at most 5 per day and one per coin, and uses the same book rules as `simulate.py`
(unfilled setups are candidates). Every hypothesis is compared with the take-all book on the same window.
- Statistics: paired 10-day block bootstrap of Δ = E[R] − E[R](take-all), 10,000 resamples, one-sided p.
  Holm–Bonferroni across H1–H9.
- 4h TRAIN: before 2025-03-28 (purged). 4h OOS: 2025-03-28 to 2026-11-02.
- 1h OOS: from 2026-04-09.
- Two fresh 4h sets were generated for this round only:
  - **newcoins**: the 23 coins of data/history/4h that are not in the cache, 2022-11 to 2026-11, 21,086 candidates;
  - **early**: the cached coins, 2020-02 to 2022-11, 6,964 candidates.

  They are in `data/research/holdout_setups/`, not in the canonical cache.

**Fixed on TRAIN before OOS.**
- H4 kept breakout_retest and fvg_continuation. On 1h TRAIN the same rule kept liquidity_sweep_reversal and
  smc_ob_retest, a completely different list.
- Only H2 and H4 passed TRAIN, so H9 = H2 AND H4.

### Results (E[R] per filled trade after costs; Δ vs take-all on the same window)

Baselines:

| Window | Take-all E[R] (n) | Control: neutral 5, no filter |
|---|---|---|
| 4h OOS | +0.006 (1,078) | +0.065, Δ +0.059 |
| 4h OOS costs ×2 | −0.067 | −0.008 |
| 1h OOS | +0.007 (1,254) | +0.002 |
| newcoins | −0.031 (3,510) | +0.003 |
| early | +0.003 (1,823) | +0.048 |

The 4h OOS DEFAULT selector was −0.116.

| ID | Rule | TRAIN E (Δ) | 4h OOS n | 4h OOS E[R] [95% CI] | Δ | p | Holm p | costs ×2 | w/o best month | 1h OOS Δ | newcoins Δ | early Δ | Pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | ≥2 earlier same-side setups still unfilled | +0.021 (−0.010) | 227 | **+0.306** [+0.089, +0.521] | +0.300 | 0.004 | **0.031** | +0.236 | +0.262 | −0.005 | **+0.180** | +0.021 | fail (G6, G7) |
| H2 | BTC daily trend direction only | +0.125 (+0.095) | 530 | +0.070 [−0.122, +0.262] | +0.063 | 0.200 | 0.705 | +0.002 | +0.028 | +0.119 | +0.003 | +0.024 | fail |
| H3 | 28d cross-sectional momentum tercile | +0.024 (−0.006) | 568 | +0.102 [−0.040, +0.250] | +0.095 | 0.036 | 0.245 | +0.031 | +0.074 | +0.008 | +0.036 | −0.026 | fail |
| H4 | strategies positive on TRAIN | +0.165 (+0.135) | 755 | +0.036 [−0.088, +0.160] | +0.029 | 0.243 | 0.705 | −0.045 | +0.002 | −0.028 | +0.109 | +0.063 | fail |
| H5 | skip crowded funding (90d decile) | +0.057 (+0.027) | 912 | +0.060 [−0.057, +0.181] | +0.054 | 0.068 | 0.342 | −0.013 | +0.030 | −0.002 | −0.010 | +0.060 | fail |
| H6 | stop 1–3 ATR | +0.000 (−0.030) | 578 | +0.053 [−0.100, +0.206] | +0.046 | 0.213 | 0.705 | +0.006 | +0.007 | −0.008 | −0.017 | +0.050 | fail |
| H7 | exit: one +1.5R target | +0.042 (+0.011) | 1,051 | +0.097 [+0.002, +0.187] | +0.091 | 0.002 | **0.018** | +0.024 | +0.076 | −0.023 | +0.024 | +0.072 | fail (G7) |
| H8 | exit: ½ at +1R, BE, ½ at +2R | +0.032 (+0.002) | 1,023 | +0.067 [−0.017, +0.152] | +0.061 | 0.035 | 0.245 | −0.008 | +0.048 | −0.025 | +0.036 | +0.062 | fail |
| H9 | H2 AND H4 | +0.172 (+0.141) | 399 | +0.078 [−0.113, +0.260] | +0.072 | 0.176 | 0.705 | +0.003 | +0.049 | +0.089 | +0.096 | +0.039 | fail |

Gates: G1 Holm p < 0.05, G2 n ≥ 100, G3 CI low > 0, G4 costs ×2 > 0, G5 without the best month > 0, G6 TRAIN Δ > 0,
G7 1h Δ ≥ 0, G8 Δ > 0 on both fresh sets. Each hypothesis also went through `evolution.decide`; its other-seed
criterion is replaced by G7 and G8.

**Decision: nothing promoted.** `data/research/selector_4h.json` was not written, and `promoted_params("4h")` still
returns DEFAULT_PARAMS with source "default".

**What the numbers say:**
- **H1 (stacked unfilled) is the only rule that held up on data nobody had seen.**
  - On the 23 new coins: +0.149R [+0.044, +0.258], n = 997, Δ +0.18, p = 0.001.
  - On 2020–22: +0.024R, Δ +0.021. That is flat, and below the neutral control (+0.048).
  - It failed two pre-registered gates: its TRAIN book was not better than take-all (Δ −0.010), and 1h was flat
    (Δ −0.005, n = 113, CI ±0.3R, so almost no information).
  - Its 4h OOS number (+0.306R) is not independent evidence, because the lead was found on that window.
  - Verdict: a real candidate, not a proven edge. It goes to a forward test (see below), not to live ranking.
- **H7 (+1.5R target) passed the 4h OOS test and every stress test**, and was positive on both fresh sets
  (+0.024 / +0.072). It reversed slightly on 1h (Δ −0.023), and the edge is small: +0.024R at costs ×2. This agrees
  with the earlier exit-profile study: on 4h, a closer target is better than +2R. An exit cannot be promoted through
  the selector anyway. If the plan owners want to act on it, it is a candidate for the daily-picks target (today the
  first level beyond 2R).
- **The rules chosen on TRAIN decayed out of sample.**
  - H4 (strategy subset) went from +0.165R on TRAIN to +0.036R OOS, and −0.045R at costs ×2. The 1h TRAIN chose a
    disjoint set of strategies. It is noise.
  - H2 (BTC trend) went from +0.125R to +0.070R, with a CI that includes −0.12R.
- H3, H5 and H6 are noise: none survived Holm, and each reverses on at least one check.
- **The neutral control beat take-all on three of four sets** (4h OOS +0.059, p = 0.014; newcoins +0.034; early
  +0.045; 1h −0.005). Taking the first ≤5 setups of each day, one per coin, is a little better than taking every
  setup as it comes. That was not a pre-registered hypothesis, so it is a lead and not a result.
- Costs ×2 push every book except H1 to about 0R or below. Apart from H1, nothing here is a profitable strategy
  after realistic costs. These are differences of a few hundredths of R around zero.

**Next step (not done here).** The engine setups that arrive from now on are new data for H1, H7 and the control.
Pre-register them as they are (no re-tuning) for a forward test of at least 3–6 months and 200+ trades. The sandbox
can log, per pick, the `pending_same_side` count and whether the pick would have passed H1.

**Caveats:**
- The fresh sets reuse the same engine, the same strategies and the same 4h timeframe. They are independent in coins
  and years, not in method.
- The newcoins list is today's survivors (EOS, MANA and others are included, but delisted coins are not).
- The early set is mostly 2020–22, a different market (the 2021 bull and the 2022 crash). APT has no setups there.
- The 1h checks are about 7 months long and have low power.
- Context features need data the live job does not fetch today: daily closes of the universe, funding, and the
  coin's recent engine setups (see DAILY_PICKS.md).
- data/history/1d ends on 2026-11-01. Setups after 2026-11-02 get NaN for the BTC trend and momentum, and are
  rejected by H2, H3 and H9 (fail-closed). This touches only the last days of the 1h window.
