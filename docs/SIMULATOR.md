# Coin simulator — replay the signal on every coin, test it, simulate it

Code: `backend/app/sim/engine.py` (pure rules), `backend/app/sim/service.py` (data, jobs, cache), `backend/app/api/sim.py`,
page `/simulator` (also linked as "Simulate" from the Machine bar). Tests: `backend/tests/test_coin_sim.py`.

## What it replays
The quant lab's survivorship-free daily panel: 591 Binance USDT coins since 2019 (delisted coins included). Each day, for
every coin, the systematic part of the final signal (`docs/SIGNAL.md`) is rebuilt from data dated that day or earlier:
momentum, carry (funding), BTC regime, order flow (taker CVD over 4 days) and the factor composite, whose factor signs are
re-chosen every 1 January from earlier data only. Fundamentals, chart setups and the AI desk have no history for every
coin and are left out. Weights: `live` (today's evidence weights; the composite earned its 0.3 on this same history, so
partly in-sample) or `prior` (literature priors only).

## Trading rules (one $10,000 account per coin)
Decided at the close, filled at the next open. Long at F ≥ entry, short at F ≤ −entry (shorts and leverage only where a
perp existed), out when F falls back through `exit` or the coin leaves the tradeable universe or delists. Stop at
k × ATR(14), always before the liquidation price; no re-entry on the stopped side until F re-arms. A 1x long is spot (no
funding, no liquidation); perps pay or receive daily funding and use isolated margin at 0.5% maintenance — a gap through
liquidation ends the run. Fee + slippage by liquidity bucket on every fill.

## Tests
- **Random timing**: the coin's own positions shifted to random dates (circular shift, 200 times), mean daily return after
  costs. p = share of shifted books doing as well.
- **Every coin**: Benjamini-Hochberg at a 10% false-discovery rate across all tested coins, and the count of p < 0.05 next
  to the number luck alone gives.
- **Monte Carlo**: 1,000 one-year futures by 10-day block bootstrap of the simulated daily returns — chance of a loss, of a
  30% drawdown, of losing 95%.

## First results (2021-01-01 → 2026-11-11, 541 tradeable coins, entry ±0.10, 3 × ATR stop)
| Setup | Pooled book | Sharpe | Hold every coin | p < 0.05 (luck) | Pass FDR | Liquidated |
|---|---|---|---|---|---|---|
| live weights, 1x, long+short | +104% | 0.51 | +90%, Sharpe 0.57 | 33 (26) | 0 | 0 |
| prior weights only | −60% | −0.44 | | 23 (27) | 0 | 0 |
| live, 3x | −93% | 0.12 | | 36 (26) | 0 | 57 |
| live, long only | −71% | 0.12 | | 14 (25) | 0 | 0 |

Read: per coin, the signal's timing is not distinguishable from luck; the pooled book roughly matches holding every coin
with a lower drawdown only in some years, and only with the (partly in-sample) live weights. Leverage destroys it. It
"beats buy-and-hold" on most coins mainly because most coins fell. This agrees with the quant lab: the composite ranks coins
against each other but does not time a single coin. The simulator is the tool to test any change to the signal on every
coin before it reaches users; it refreshes itself daily after the lab's data update.
