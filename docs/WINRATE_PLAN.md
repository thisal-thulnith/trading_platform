# Plan: a 90–95% win rate without hiding losses

Written 2026-11-12, from measurements on the 591-coin panel (2021-01-01 → 2026-11-11). Scripts: the simulator
(`app/sim/`), `docs/COMPETITORS.md` §4, and the funding-carry test below.

## 1. What a 95% win rate means

Win rate p, average win W, average loss L. A strategy makes money only if p·W > (1−p)·L. The win rate needed just to
break even is **p\* = L / (W + L)**:

| Average win : average loss | Win rate needed to break even |
|---|---|
| 1 : 1 | 50% |
| 1 : 3 | 75% |
| 1 : 10 | 91% |
| 1 : 19 | 95% |

So a 95% win rate is only good if the losses are less than 19 times the wins. Three ways products reach it:

1. **Tight take profit, far or no stop**: measured on our signal, +2% TP and no stop gives a 90% win rate and −100%.
2. **Not counting losers** (DCA bots): open losing cycles never close; 98.9% "wins", −36% overall.
3. **Not predicting at all** — earning a structural premium (funding, spreads, rebates) where most days genuinely pay
   and the risk is a rare, bounded event. This is the only honest route to 90%+, and the one this plan builds.

Directional prediction cannot get there: the best published crypto direction models reach ~52–58% accuracy; our signal
calls the next day right 50.1% of the time; no rule-based strategy in our test beat random timing on any coin after
correcting for testing 541 coins. More training does not change this — it changes the honest target to ~55–65% at a
payoff of 1.2–2 : 1.

## 2. Measured: delta-neutral funding carry (long spot + short perp, 1x, half the capital each leg)

Fees on both legs at every change, plus 0.1% per round trip for the spot–perp basis (perp prices are not in the panel
yet, so basis risk is estimated, not measured).

| Version | Return | Per year | Max DD | Days won | Weeks won | Months won | Worst month |
|---|---|---|---|---|---|---|---|
| BTC + ETH, hold until 7-day funding < 0 | +32.9% | 5.0% | **0.45%** | **90.6%** | **82.1%** | **84.1%** | −0.21% |
| BTC + ETH, rebalance weekly | +29.2% | 4.5% | 1.2% | 88.9% | 79.2% | 74.6% | −0.34% |
| 10 liquid coins, enter > 0.05%/day, hold until < 0 | +33.7% | 5.1% | 1.3% | 83.3% | 71.9% | 71.7% | −0.58% |
| Top-10 funding coins, rotated weekly | −24.0% | −4.6% | 35.9% | 78.4% | 16.6% | 16.9% | −4.0% |

By year (BTC + ETH, hold): 2021 +17.5%, 2022 +0.1%, 2023 +3.7%, 2024 +6.2%, 2025 +1.8%, 2026 +0.8%.

Read: the win rate is real (no hidden open losses: the position is hedged, the PnL is the funding received minus costs).
The return is small and has fallen since 2021 — in 2025–26 it earned less than cash. Chasing the highest-funding alts
loses to trading costs and basis. **High win rate, low return, low risk**: a "steady" product, not a "growth" one.

## 3. The plan — three tracks

### Track A — structural strategies (where 90%+ is honest)
| Step | What | Technology | Pass test |
|---|---|---|---|
| A1 | Perp price history for every perp since 2019 (Binance futures klines, data.binance.vision) | extend `app/quant/lab/data.py` | basis series for 357 perps |
| A2 | Carry backtest with the real basis, funding-interval changes, borrow and margin rules | `app/sim/carry.py` | ≥ 85% of weeks positive, max DD < 3%, return > cash after costs, out of sample from 2024 |
| A3 | Cross-exchange funding spread (short perp where funding is highest, long where lowest) | record Binance/Bybit/OKX funding and prices daily (we already read them live) | spread net of 4 legs' fees > 0 in ≥ 80% of weeks, forward 3 months |
| A4 | Quarterly futures cash-and-carry (lock the basis to expiry) | futures curve recorder | locked yield > cash + costs at entry |
| A5 | Market making / maker rebates on the 20 perps we record | queue-position simulator on the order-book recorder (`app/quant/lob/`), inventory limits | positive after adverse selection in paper trading for 1 month |
| A6 | Carry paper book in the sandbox, then real execution with two-leg orders | execution engine: both legs or neither, withdrawal-disabled API keys, exchange-risk limits per venue | 3 months forward, matching the backtest within its range |

Risks that cap the win rate: funding flipping negative for weeks (2022), exchange failure (FTX), auto-deleveraging,
basis blowouts in crashes, a stablecoin depeg. The plan sizes per exchange and shows them on the product page.

### Track B — directional signals: higher precision, honest target 55–65% at 1.2–2 : 1
| Step | What | Technology | Pass test |
|---|---|---|---|
| B1 | Stronger-signal threshold (entry 0.20: +140%, Sharpe 0.57 vs 0.51 in-sample) | simulator | pre-registered, holds on 2025–26 out of sample |
| B2 | Partial profits: half at +1R, stop to break-even, trail the rest | engine exit profile | win rate +10–20 points with average trade not lower |
| B3 | Trend component (EMA 20/50 long/short was best of 14) | fusion component with a literature prior | pre-registered (chosen after seeing the table), out of sample |
| B4 | Train on every coin, 4h and 1h (today: 19 coins for setups) | generate setups for the 591-coin panel on the Spark GPU box | more data, same CPCV / deflated-Sharpe gates |
| B5 | Order-book features (OFI, depth imbalance, microprice) for entry timing | LOB recorder, ≥ 1 month of data first | better fill price / win rate on the same signals, forward |
| B6 | Meta-labeling + conformal abstention: trade only when the model is confident | LightGBM + conformal (built), new features from B4–B5 | precision ≥ 60% at ≥ 1.2 : 1 out of sample |
| B7 | Foundation model forecasts (Kronos / Chronos) | needs your approval to install the model code | pre-registered test vs the current signal |
| B8 | LLM desk graded: weight only from its own graded calls | renew the AI key; journal grading (built) | 200+ graded calls, calibration within 5 points |

### Track C — product: show the right numbers
- Every strategy shows win rate **next to** average win, average loss, profit per trade, return and drawdown — and open
  losing positions count. The DCA example is the explanation users see.
- Two books: **Steady** (Track A: high win rate, low return, hedged) and **Growth** (Track B: ~45–60% win rate, bigger
  winners). The user picks by temperament; the budget-and-risk settings already exist.
- Every pick logged before the fact, never deleted, after fees — the thing no competitor offers.

## 4. What not to do
No martingale or averaging down without a stop, no tightening take profit to raise the win rate, no counting only
closed trades, no annualising a few weeks, no "AI accuracy" claims without a graded forward record (SEC/CFTC cases).

## Status (2026-11-13)
A1 done (perp history). A2 done: pre-registered carry fails with separate margin; unified-margin BTC + ETH meets the bar
post-hoc → forward test (A6 paper) running. B1 forward test running; B2 (partial profit) failed. See
`docs/research/WINRATE_PREREG_2026-11-13.md`.

## 5. Order
A1 → A2 → A6 (paper) first: it is the only route to a real 90%+ and needs no new model. In parallel B1 + B2 (cheap,
simulator-only), then B4 → B6 on the GPU box. Each step ships only if it passes its test; failures are logged like the
others.
