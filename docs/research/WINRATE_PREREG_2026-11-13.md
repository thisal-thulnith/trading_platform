# Pre-registration — win-rate round (2026-11-13)

Written before running the tests below. Simulator: `app/sim/` (every coin, 2021-01-01 → 2026-11-11, live weights, long +
short, 3 × ATR stop, next-open fills, costs and funding as in `docs/SIMULATOR.md`). Nothing below may be changed after
seeing its result; results are added under "Results" with the date.

## P1 — partial profit, stop to break-even (Track B2, never run before)
Rule fixed in advance: when price reaches entry ± 1R (R = the initial stop distance, 3 × ATR(14)), close 50% of the
position there and move the stop on the rest to the entry price. Same-day order: the stop is checked first, then the
partial (conservative). Everything else unchanged.
Pass (all three, versus the same run without the partial):
1. win rate of closed trades up by ≥ 10 points;
2. average trade (after costs) not lower;
3. pooled Sharpe not lower, both 2021→ and 2022→.

## P2 — stronger-signal threshold (Track B1)
Entry ±0.20 instead of ±0.10. **Contaminated**: the 2021–2026 grid that suggested it (2026-11-12) already covers every
year and coin available, so no clean historical holdout exists. Historical result is reported as descriptive only
(per year: does 0.20 beat 0.10 on pooled return in at least 4 of 6 years?). The deciding test is **forward**: from
2026-11-13 both thresholds are recorded daily by the all-coins simulator on new data only; decision after 180 days:
0.20 is adopted if its forward pooled Sharpe exceeds 0.10's and its average trade is not lower.

## P3 — funding carry with the measured basis (Track A2)
Rule fixed in advance: long spot + short perp, 1x, half the capital each leg; hold BTC and ETH while their 7-day mean
funding > 0.01%/day, exit when it falls to ≤ 0; spot and perp fills at the daily open after the decision; fees +
slippage on both legs; PnL = spot leg + perp leg (so the basis is measured, not assumed) + funding received.
Pass (2024-01-01 → 2026-11-11 as the out-of-sample period; 2021–2023 reported for context):
1. ≥ 85% of weeks with a non-zero result positive;
2. max drawdown < 3%;
3. annual return above 4% (a cash-like yield), after costs.
The liquid-alt version (10 coins, enter > 0.05%/day, hold until ≤ 0, liquid bucket only) is evaluated the same way and
reported separately.
Data-validity guard added before the first run (after inspecting the basis, not the results): a coin is not entered on a
day its perp close is more than 2% from its spot close (same ticker, different asset; e.g. RAY, SC, DGB, FTT show >20%
gaps for years).

## Results (2026-11-13)

**P1 partial profit — FAIL.** Win rate 47.4% → 49.4% (+2.0 points, needed +10); average trade +0.40% → −0.13% (lower);
pooled Sharpe 0.51 → 0.33 from 2021 (lower), 0.05 → 0.10 from 2022. At 3 × ATR, +1R is rarely reached. Not adopted.

**P2 entry 0.20 — descriptive check passes, decision pending.** Pooled return beat entry 0.10 in 4 of 6 years (2021,
2022, 2023, 2026; not 2024, 2025); whole period +140% vs +104%, Sharpe 0.57 vs 0.51, average trade +0.65% vs +0.40%.
Forward test running from 2026-11-13 (`data/sim/forward.json`, `GET /api/sim/forward`); decision after 180 days.

**P3 funding carry with the measured basis — FAIL (both books).** Out of sample 2024-01-01 → 2026-11-12:

| Book | Weeks won | Max DD | Per year | Verdict |
|---|---|---|---|---|
| BTC + ETH | 85.8% ✓ | 3.63% ✗ | 2.0% ✗ | fail |
| 10 liquid alts | 80.0% ✗ | 8.61% ✗ | 4.3% ✓ | fail |

Cause: the 1x short perp on separate (isolated) margin was liquidated in rallies (7 times on BTC + ETH, 43 on alts)
while the spot leg was sold later at the close; the measured basis cost the majors book $1,206 of $4,483 funding.

**Exploratory, post-hoc (not a pass):** with unified margin (the spot leg collateralises the short, as on Binance
Portfolio Margin), no liquidations: BTC + ETH out of sample 86.6% of weeks, 84.8% of months, max DD 1.46%, 4.4% a year
— it would meet the bar; alts 84.0% of weeks (just under), 1.82% DD, 6.2% a year. Because the variant was chosen after
seeing the result, it counts only if it meets the same bar forward: paper books from 2026-11-13
(`data/sim/carry_forward.json`), judged after 180 days.
