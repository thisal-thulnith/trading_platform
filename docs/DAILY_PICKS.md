# Today's 5 trades — how the product works

The product is one page: every day, the five best trade setups across the market, each validated and sized for the
trader's budget, with the method's live paper record and its backtest next to them. Everything below runs in code;
the AI desk reviews the picks when an AI key is set and never invents a number.

## The daily pipeline (`backend/app/agents/daily.py`, `GET /api/agent/daily`)
1. **Pool** — one price call screens every USDT coin; the most liquid and most active are analysed in depth
   (`scout.py`). Only coins with a real engine setup on the 4h chart continue.
2. **Readiness gate** (`readiness.py`) — a 0-100 score from the setup, both timeframes, the backtest, room to the
   target, the regime and the trend rank. Hard blocks (stale data, a side against both timeframes, a suspended
   strategy) remove the coin.
3. **Ranking** — ready setups come before forming ones, then by score. The selector (`app/research/selector.py`, the
   same function the historical simulation and the evolution loop use, fed the same features) ranks live picks **only
   once the evolution loop has promoted a version that beat the baselines on unseen data**. As of 2026-11-11 none has:
   the default selector lost to five random picks (4h: −0.072R vs −0.025R random vs +0.021R taking every setup), so
   live picks are ranked by the readiness score and the page says so.
4. **Plan and validation** — stop beyond structure, target at the first level beyond 2R after costs (`plan.levels`);
   the validators' data, trade, market and execution checks (`validators.py`). A critical failure drops the pick.
5. **Capacity** — a pick the order book cannot absorb (under $25,000 within 10 bps, a quarter of the resting size) is
   dropped: thin coins move against a crowd.
6. **Sizing for the budget** — the risk style the evidence supports (1% of the budget at risk, 2% only for a ready
   setup scoring 85+ with the higher timeframe agreeing), then quantity, the lowest safe leverage, margin, liquidation,
   the dollars at the stop and the target, and the Binance orders.

Picks are made once per UTC day (the background loop makes them after midnight; `refresh=true` makes them again). A
ready pick says "Take now"; a forming one says "Wait for trigger" and names the trigger.

## The sandbox — backtest to execution (`daily.sandbox`, `GET /api/agent/sandbox`)
Every published pick is recorded at the price it was published and graded on the closed candles after it (target
first, stop first, or expiry after 42 candles), then compounded on a $10,000 paper account at the pick's risk. This is
the method's live record; it sits next to the backtest of the same method so drift between the two is visible.

## The research loop (`backend/app/research/`)
- **Backtesting engine** — `app/backtest/` replays the live engine code on history; `app/ml/meta_label.py` caches its
  setups with their outcomes after fees and slippage.
- **Historical simulation** (`simulate.py`) — replays the daily-5 method day by day on those setups with position limits
  and risk sizing; compares it with taking every setup and with five random setups.
- **Evolution loop** (`evolution.py`, 7 steps) — seed, evaluate on a training window, select, mutate, validate on a
  later window it never saw, stress test, and promote only if it beats both baselines out of sample. The live selector
  uses the promoted parameters, or the defaults when nothing has passed.

## What the backtest says today (2026-11-11, 4h, 12 coins, Nov 2022 – Nov 2026)
| Method | Trades | Win | Avg R | Per year at 1% risk | Max DD |
|---|---|---|---|---|---|
| Selected 5 (default selector) | 1,941 | 33% | −0.072 | −35% | 84% |
| Every setup | 2,659 | 36% | +0.021 | +3% | 50% |
| 5 at random | 2,520 | 35% | −0.025 | −22% | 74% |

No ranking method has a proven edge yet, and taking every setup is about breakeven after costs. The daily picks are
candidates to study, not proven signals; the sandbox's live record decides. Details: docs/STRATEGIES.md (last section).

## Hypothesis round 2026-11-11 (pre-registered): nothing promoted

Nine rules were registered in `docs/research/HYPOTHESES_2026-11-11.md` before any of them was evaluated:
- stacked unfilled setups;
- the BTC daily trend;
- 28-day cross-sectional momentum;
- a strategy subset chosen on TRAIN;
- a funding-crowding filter;
- a 1–3 ATR stop band;
- a +1.5R exit;
- a 1R partial with break-even;
- their TRAIN-chosen combination.

Each was then tested once out of sample on 4h, with a Holm correction against take-all, costs ×2, the best month
dropped, 1h, and two freshly generated 4h sets: 23 coins never cached, and 2020–22.

**None passed every pre-registered gate, so the live selector stays unpromoted.** `promoted_params("4h")` returns
source "default", and the picks are still ranked by the readiness score. The full table is in docs/STRATEGIES.md
("Hypothesis round 2026-11-11").

| Rule | 4h OOS E[R] (take-all +0.006) | Holm p | New coins Δ | 2020–22 Δ | 1h Δ | Why it failed |
|---|---|---|---|---|---|---|
| H1 ≥2 same-side setups still unfilled | +0.306 (n 227) | 0.031 | +0.180 | +0.021 | −0.005 | TRAIN book no better; 1h flat |
| H7 single +1.5R target | +0.097 (n 1,051) | 0.018 | +0.024 | +0.072 | −0.023 | slightly worse on 1h; an exit, not a selector rule |
| everything else | +0.04 to +0.10 | ≥ 0.25 | mixed | mixed | mixed | not significant after Holm; most ≤ 0 at costs ×2 |

Plainly:
- No ranking or filter rule has demonstrated an edge.
- H1 is the one lead worth a forward test. It replicated on 23 coins nobody had looked at, but not on 1h or 2020–22.
- A +1.5R target looks slightly better than +2R on 4h.
- Treat both as candidates, not signals.

### How the live job must call the selector, if a filter is ever promoted

Nothing changes for current callers:
- `live_setup_features(df, cand, snap, tf, btc=btc)` and `score_setup(feats, pp)` behave exactly as before.
- The new arguments are optional, and every new selector param is off in DEFAULT_PARAMS.

A promoted filter rule would set `order="time"` and one or more filters. The filters need context features, and
**they fail closed**: without context, every setup is rejected. The caller must then do this:

```python
from app.ml.meta_label import live_setup_features
from app.research.context import DailyPanel, live_context, load_daily, recent_engine_setups
from app.research.selector import promoted_params, selector_verdict

UNIVERSE = ["AAVEUSDT", "ADAUSDT", "ALGOUSDT", "APTUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT",
            "ETHUSDT", "LINKUSDT", "SOLUSDT", "XRPUSDT"]          # the 4h backtest universe (+ BTCUSDT always)
panel = DailyPanel(load_daily(UNIVERSE))   # once per run; or {sym: DataFrame[open_time, close]} of fresh 1d candles.
                                           # It must contain yesterday's completed daily bar, or btc_trend/xs_mom are NaN.
# per candidate (df: closed candles of the setup tf; >= ~2,700 bars for HTF parity with the backtest)
recent = recent_engine_setups(df, symbol, tf)          # the coin's engine setups on the previous 14 bars (H1)
ctx = live_context(symbol, df, tf, panel=panel, funding=funding_df, recent_setups=recent)
feats = live_setup_features(df, cand, snap, tf, btc=btc, funding=funding_df, context=ctx)
pp = promoted_params(tf)
if str(pp["source"]).startswith("promoted"):
    v = selector_verdict(feats, pp)        # {"score", "eligible", "failed_filters"}
    if not v["eligible"]:
        drop(candidate)                    # what pick_daily does historically; score_setup alone only sinks it
```

`funding_df` is the settled perp funding prints, a DataFrame with `time` and `rate` covering at least 90 days. Only
the funding filter needs it.

Known live/backtest gaps:
- `recent_engine_setups` de-duplicates anchors only inside its 14-bar replay, so it can count a few more pending
  setups than the history did.
- With the 600 bars the daily job loads today, the higher-timeframe context of those replays is shorter than in
  the backtest.
- data/history/1d is not refreshed by the live job.
