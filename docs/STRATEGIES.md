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
