# Pre-registered execution re-test of the quant lab's best book (2026-11-11)

Status: **fixed before any configuration below was run on real data.** The only numbers known when this was written
are the lab-2 results in docs/QUANT_PLAN.md ("Quant lab results"): the 72-configuration grid executed at the signal
close, its selected book (LightGBM, h = 3, daily, long–short, k = 5, Sharpe 1.45, 1-day-lag Sharpe 0.40, costs
17.6%/yr), DSR p = 0.183 with N = 144, PBO 0.154. The new execution code was checked only (a) on synthetic data
(tests/test_quant_lab_exec.py) and (b) by confirming that the legacy same-close path still reproduces lab-2 exactly
(Sharpe 1.449 for the selected book). After this file was written it was not edited; results are appended to
docs/QUANT_PLAN.md and the full record is in `backend/data/quant_lab/exec_retest_report.json` and `trials.jsonl`.

Code: `backend/app/quant/lab/portfolio.py` (execution models, maker fills, no-trade band, calendar schedule),
`backend/app/quant/lab/retest.py` (`uv run python -m app.quant.lab.retest exec`).

## A data fact checked before writing (a property of the panel, not a result)
On Binance the daily candle of D+1 opens at 00:00:00 UTC, one millisecond after the D candle closes. Over the
791,391 consecutive candle pairs in the panel, |open(D+1) / close(D) − 1| has median 3.3 bp, 90th percentile 20 bp,
99th percentile 61 bp; 36% are exactly equal. **So "fill at the next open" is, in a 24/7 market, almost the same price
as "fill at the signal close"**; it removes the look-ahead of trading on the very print the signal uses, but it does
not model the minutes-to-hours a real system needs to compute, decide and work the orders. That is why this test adds
an execution-realism condition (G5 below) that fills at the VWAP of day D+1 instead. For the maker rule: the day's
low trades ≥ 10 bp below the open on 97.9% of candles and the high ≥ 10 bp above it on 97.0%.

## Execution model (all configurations)
- **Timing.** Scores use data through the close of D. Orders are filled at the **open of D+1** (never at the close
  of D); the position earns open D+1 → open D+2. Funding: a short receives/pays the funding settled on D+1 (as in
  lab-2). A coin with no candle on D+1 cannot be bought; an existing position in it exits at its last close.
- **Taker** (current model): 0.05% fee + slippage by 30-day ADV bucket (0.02% ≥ $100M, 0.05% $20–100M, 0.10%
  $5–20M, 0.25% below), per side, on every unit traded.
- **Maker:** a limit order at the D+1 open price L. A buy fills only if the D+1 low ≤ L × (1 − 10 bp); a sell (incl.
  covering a short is a buy) only if the D+1 high ≥ L × (1 + 10 bp). A filled order executes at L and pays **0.02%**
  per side, no slippage. An unfilled order is cancelled at the end of D+1: nothing is traded in that coin (a new slot
  stays empty; an exit or resize is retried at the next rebalance). Exits of coins without a D+1 candle are forced at
  the taker rate. All-or-nothing per order (no partial fills).
- **No-trade band** b: at a rebalance, a holding is changed only if |target weight − current weight| > b. Names that
  leave the target book (target 0) are always closed in full (otherwise small stale positions accumulate outside the
  model's selection); the lab's existing dust rule (exiting positions below 0.01 are closed outright) stays.
  Pre-registered value: **b = 0.02** (about half of a typical position: the lab-2 selected book averaged 0.61 gross
  over 14 names ≈ 0.044 each). b = 0 is the control.
- **Schedule.** Rebalancing is anchored to the calendar: decisions on days with (D − 2021-01-01) mod rebalance = 0, so
  a live book rebalances on the same days as its backtest. Turnover limits as lab-2 (0.4 per day for daily, 1.0 per
  rebalance for weekly), vol target 30% for long–short, the same weights, caps and capacity filter as lab-2.
- Models: the lab-2 walk-forward LightGBM predictions (quarterly expanding refits, labels purged by the horizon),
  unchanged. OOS window 2021-01-01 → end of panel (2026-11-10).

## Configurations (12, all long–short LightGBM; chosen from the lab-2 grid's best rungs, not searched anew)
| book | horizon | rebalance | k | why |
|---|---|---|---|---|
| A | 3 days | daily | 5 | lab-2's selected book |
| B | 3 days | weekly | 5 | the same book, weekly (QUANT_PLAN's suggested next test: less exposed to delay) |
| C | 7 days | weekly | 10 | the best weekly configuration in the lab-2 grid; the 7-day model |

× order type {taker, maker} × band {0, 0.02} = **12 configurations**. Each is appended to
`data/quant_lab/trials.jsonl` as a `strategy` trial (version `exec-1`), so N becomes 156 if no other trials are
logged meanwhile.

Stress variants of each configuration (reported, logged as kind `stress`; they are never selection candidates, the
same treatment lab-2 gave its costs ×2 and lag stresses, so they do not enter N): costs ×2; one more day of delay
(signal of D filled at the open of D+2); fill at the VWAP of D+1 (quote volume / base volume, clipped to the day's
range; a maker order is then assumed worked passively to the VWAP and pays the maker fee); for maker configurations
also a 25 bp trade-through rule.

Reported for every configuration: Sharpe, ann. return, CAGR, max drawdown, turnover, costs, funding, maker fill rate,
Sharpe by year and by BTC regime (above/below its 200-day average), the stresses above, and the same book at the
signal close (lab-2) for reference.

## Selection and gate (fixed now)
**Selected configuration** = the highest OOS net Sharpe among the 12. It is promoted only if all hold:

| | criterion |
|---|---|
| G1 | deflated Sharpe p < 0.05, with N = every distinct `strategy` configuration in trials.jsonl after this run, V[SR] across them |
| G2 | PBO < 0.3 (CSCV, 16 blocks, over the 12 configurations' daily OOS returns) |
| G3 | net Sharpe > the equal-weight universe's Sharpe over the same window under the same next-open execution |
| G4 | costs ×2: PSR(SR > 0) ≥ 0.95 |
| G5 | **added for this test:** fill at the D+1 VWAP: PSR(SR > 0) ≥ 0.95 |

G1–G4 are the lab's existing gate. G5 is added because of the data fact above: without it, "next open" would let
the book keep the same-price execution whose fragility (1-day-lag Sharpe 0.40) is the reason for this test. CPCV
(10 groups, 2 test groups, purge = horizon, 14-day embargo) of the selected model traded with the selected execution
settings is reported, not gated (as in lab-2).

**If the selected configuration passes:** `data/quant_lab/model.json` is written through the lab's promotion path
(`validate._write_model`, version `exec-1`) with the full execution settings (next-open fills, order type, band,
rebalance anchor); `live_ranking()` then replays the book with the same engine and returns `promoted: true`, weights
after today's orders, and the settings in its `portfolio` field. **If it fails:** nothing is promoted and model.json
is not written. No other configuration is tried in this round; any later variant is a new logged trial.
