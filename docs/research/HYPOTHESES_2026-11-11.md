# Pre-registered hypotheses: daily-5 picks (2026-11-11)

Status: **fixed before any hypothesis below was evaluated.** No result was looked at while writing this file. The
only numbers known at the time are the ones already in docs/STRATEGIES.md ("Daily 5 — historical simulation and
evolution"). After this file was written it was not edited. The results are in docs/STRATEGIES.md and
docs/DAILY_PICKS.md ("Hypothesis round 2026-11-11"), and the full record is in
`backend/data/research/hypotheses_2026-11-11.jsonl`.

Code: `backend/app/research/hypotheses.py` runs this document. The filters are in `selector.py` (optional params,
off by default). The context features are computed in `context.py` and added to the rows by `meta_label.setup_row` /
`live_setup_features` (the `context=` argument).

## Common definitions

**Data.** The engine setups cached by `app/ml/meta_label.py` are used and never regenerated. They are labelled by
`label_setups`: edge_tp2r, which is a limit at the zone edge, the strategy stop and one +2R target, through TradeSim
with a 0.05% taker fee and 2 bps slippage on each side. Unfilled setups (expired or invalidated) are candidates too:
an unfilled pick earns 0R and blocks its coin and slot until it expires. These are the same rules as `simulate.py`.

**Book ("daily-5 book").** `simulate.run_book`:
- Each UTC day, the picker chooses at most 5 of the setups created that day.
- At most 5 positions or pending orders are open at once, one per coin.
- Skipped picks are not back-filled.
- Each trade risks 1% of equity.

Every hypothesis uses the same neutral picker, `selector.NEUTRAL_PARAMS` plus that hypothesis's filter:
- no scoring weights and no quality floor;
- the earliest eligible setups of the day first (`order="time"`);
- one setup per coin and no cap per side.

The neutral picker with no filter is the **control**. It is reported but is not a hypothesis.

**Metric.** The expectancy of the book: mean R per filled trade after costs. Its 95% CI is a circular block
bootstrap over calendar days with 10-day blocks (`simulate.block_bootstrap_ci`).

**Benchmark.** The **take-all** book (`simulate.take_all_picker`) on the same dataset and window. It takes every
setup in time order under the same book rules.

**Test statistic.** A one-sided, paired, day-block bootstrap of
Δ = E[R](hypothesis book) − E[R](take-all book), on the same window:
- The same resampled days are used for both books.
- Circular 10-day blocks, 10,000 resamples, seed 0.
- p = (1 + #{Δ* ≤ 0}) / (1 + 10,000).

**Multiple testing.** Holm–Bonferroni at family-wise α = 0.05 over every hypothesis evaluated out of sample (H1–H9;
H9 is dropped from the family only if it has no component). If H9 equals a single hypothesis, it still counts, which
is conservative.

**Context features.** These are known at setup time. Setup time t is the close of the setup bar. A daily bar is
usable only after it closes: at t, the last usable day is floor(t/1d) − 1. If that day is missing, the feature is
NaN. **A filter whose feature is NaN rejects the setup** (fail-closed). The context universe is the dataset's coins
plus BTCUSDT.
- `btc_trend`:
  - +1 when BTC's last completed daily close is above both its 50-day and 200-day simple averages;
  - −1 when it is below both;
  - 0 otherwise.

  Source: data/history/1d/BTCUSDT.csv.
- `xs_mom_pct`: the mid-rank percentile (0..1) of the coin's 28-day return (close[D]/close[D−28] − 1) among the
  universe coins that have one at t. At least 3 coins are needed. Source: data/history/1d.
- `funding_pct_90d`: the mid-rank percentile of the last settled funding print among the coin's prints of the
  previous 90 days. At least 30 prints are needed. Source: data/history/funding.
- `pending_long` / `pending_short`: the number of the coin's earlier engine setups on that side, created strictly
  before t, whose limit is still waiting at t: not filled, not invalidated and not expired. Waiting follows the
  TradeSim rules on the edge entry, using only the bars closed by t.
- The direction-aware versions are in `meta_label.CONTEXT_FEATURES`:
  - `btc_trend_dir` = btc_trend × dir;
  - `xs_mom_dir_pct` = pct for longs and 1 − pct for shorts;
  - `funding_pct_dir` = pct for longs and 1 − pct for shorts;
  - `pending_same_side`.

## Data splits

| Set | What | Role |
|---|---|---|
| 4h TRAIN | 12 cached coins. Setups before the evolution cut (first 60% of the span, so **< 2025-03-28**), **purged**: the trade or order must resolve before the cut. | All development: the H4 strategy list, the H9 composition, the TRAIN check (G6). |
| 4h OOS | The same 12 coins, setups ≥ 2025-03-28 to 2026-11-02. | The single primary test, the Holm family and the stress tests. |
| 1h OOS | The 19 cached 1h coins, after the 1h 60% cut (≥ 2026-04-09). H4 uses a list recomputed on the 1h TRAIN window. | Independent check (G7). |
| Fresh: newcoins | 4h, the 23 coins in data/history/4h that are **not** in the 4h cache, over the same period as the cache (whole period, no split). Setups generated for this round (`hypotheses gen-holdout`). | Never seen by anyone. Replication check (G8). |
| Fresh: early | 4h, the 12 cached coins **before** the cache's first setup bar (2020-01 to 2022-11; whole period). Generated for this round. | Never seen by anyone. Replication check (G8). |

Prior exposure, stated honestly:
- **H1** (the stacked-unfilled lead) was found after looking at the 4h TRAIN and OOS windows and at 1h, where it
  did not hold. Its 4h OOS and 1h numbers are therefore **not independent evidence**. Only the two fresh sets are a
  clean test of H1.
- The earlier work also showed that on 4h TRAIN, setups against the engine's regime did better than aligned ones.
  That is related to H2 but is not the same rule: the engine's regime is not the BTC daily trend.
- Nothing else here was looked at before.

## Hypotheses

Each hypothesis is NEUTRAL_PARAMS plus exactly the rule below.

| ID | Rule (exact) | Selector params |
|---|---|---|
| H1 | Stacked unfilled: keep a setup only if ≥ 2 earlier same-coin, same-side setups are still pending at its setup time. | `min_pending_same_side: 2` |
| H2 | BTC daily trend: longs only when btc_trend = +1, shorts only when btc_trend = −1. Nothing when BTC is mixed (0) or unknown. | `btc_trend_filter: true` |
| H3 | Cross-sectional momentum: longs only when the coin's 28-day return is in the universe's top tercile (xs_mom_pct ≥ 2/3), shorts only in the bottom tercile (xs_mom_pct ≤ 1/3). | `min_xs_mom_dir_pct: 0.6667` |
| H4 | Strategy subset chosen on TRAIN: keep the strategies whose filled, resolved TRAIN setups (all candidates, not the book) have mean R > 0 and n ≥ 50. 4h list from 4h TRAIN (also used on the fresh sets); 1h list from 1h TRAIN. If the list is empty, the book makes no trades and the hypothesis fails. | `strategy_allow: [...]` |
| H5 | Funding crowding: skip longs when funding_pct_90d ≥ 0.9, skip shorts when funding_pct_90d ≤ 0.1 (funding_pct_dir ≥ 0.9). | `max_funding_dir_pct: 0.9` |
| H6 | Stop distance band: keep 1 ≤ stop_atr ≤ 3 (stop distance from the edge entry in ATR(14) of the setup timeframe). | `stop_atr_band: [1, 3]` |
| H7 | Exit: one take-profit at +1.5R instead of +2R. Same edge entry, stop, expiry, time stop and costs; re-labelled with TradeSim from the cached setups (`simulate.label_with_targets`). Book: NEUTRAL with no filter. | targets (1.5,) |
| H8 | Exit: half at +1R, then the stop moves to break-even (the fill), and the other half at +2R. TradeSim's partial rule; re-labelled the same way. Book: NEUTRAL with no filter. | targets (1.0, 2.0) |
| H9 | Combination: the AND of every filter H1–H6 that passes on TRAIN, plus the exit among H7/H8 that passes on TRAIN (the one with the higher TRAIN expectancy if both pass). Fixed from TRAIN results only, before any OOS evaluation. | composed |

**TRAIN pass** (decides H9's components; nothing else is chosen on TRAIN except the H4 list):
- the 4h TRAIN book has n ≥ 100 trades;
- its E[R] is above take-all's TRAIN E[R];
- the paired-bootstrap one-sided p is below 0.10.

## Pass criteria (all must hold to promote; fixed now)

| | Criterion |
|---|---|
| G1 | 4h OOS: Holm-adjusted p < 0.05 for Δ > 0 vs take-all |
| G2 | 4h OOS: ≥ 100 filled trades in the book |
| G3 | 4h OOS: the block-bootstrap 95% CI of the book's E[R] has lower bound > 0 |
| G4 | 4h OOS with fees and slippage ×2 (re-labelled): book E[R] > 0 |
| G5 | 4h OOS without the book's best calendar month: E[R] > 0 |
| G6 | 4h TRAIN: Δ > 0 (same sign as claimed) |
| G7 | 1h OOS: Δ vs 1h take-all ≥ 0 (holds, or at least does not reverse) |
| G8 | Fresh sets: Δ vs that set's take-all > 0 on **both** newcoins and early |

**Relation to `evolution.decide`.** The gate is evolution.decide plus extra checks:
- evolution.decide is called with the hypothesis's 4h OOS book, the DEFAULT_PARAMS OOS book and the take-all OOS
  book. That covers: it differs from the default, it beats the default and take-all, n ≥ 50, CI low > 0, costs ×2 > 0
  and without the best month > 0.
- Its "other seed" criterion is meaningless here because nothing is searched. G7 AND G8 (replication on 1h and on
  fresh data) take its place.
- G1, G2 and G6 are required on top.

**Promotion.**
- **Filter rule (H1–H6, or H9 without an exit) passes:** NEUTRAL_PARAMS plus that filter is written to
  `data/research/selector_4h.json` with `"status": "promoted"` and run_id `hyp-2026-11-11-<H>`, so
  `promoted_params("4h")` returns source `promoted:hyp-2026-11-11-<H>`.
- **Several pass:** H9 if it passes, otherwise the one with the smallest Holm-adjusted p.
- **Exit hypothesis (H7, H8, or an H9 that includes an exit) passes:** it cannot be expressed through the selector.
  It is recorded as passed, with a recommendation for the exit-plan owners, and is **not** promoted here.
- **Nothing passes:** nothing is promoted, and `selector_4h.json` is not written.

Also reported for every hypothesis, as information only: the TRAIN numbers, the OOS CI, costs ×2, drop-best-month,
1h OOS, both fresh sets, and the control (NEUTRAL with no filter) next to take-all on each set.
