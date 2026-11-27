# Trading-technology plan — the machine side, layer by layer

The platform has two ways of looking at a market. The **human side** reads charts the way a trader does — structure,
order blocks, fair value gaps, Elliott, Wyckoff — and an AI desk argues about it. The **machine side** measures every
coin the same way, every day, and keeps only what predicts out of sample. Our own research (docs/STRATEGIES.md,
docs/QUANT_PLAN.md) shows the human-side setups have no edge after costs, while the machine side has found real but
not yet tradable structure. This plan builds the machine side into the engine and keeps the human side as the
explanation a person can read, check and run.

Every item has a pass test. Nothing reaches a live pick until it passes, and every attempt is logged so the
overfitting tests stay honest (each extra try makes the deflated-Sharpe bar higher).

## Where we stand (2026-11-11)
| Layer | Built | Measured result |
|---|---|---|
| Data | survivorship-free daily panel, 591 coins (199 delisted), funding history | matches Binance's live closes |
| Signals | 18 factors, IC report | 8 rank coins (low vol, BTC correlation, liquidity, distance from highs…); momentum reversed; funding level flat |
| Models | composite → ridge → LightGBM, walk-forward | LightGBM rank IC 0.157 OOS (t 20.8) |
| Portfolio | vol-targeted long-short, costs and funding | Sharpe 1.45 OOS, but costs 17.6%/yr and Sharpe 0.40 with one day of delay |
| Validation | CPCV, deflated Sharpe, PBO, trial log, pre-registration | not promoted: deflated-Sharpe p = 0.18 |
| Fusion | evidence-weighted final signal, take / wait / veto | live on Today's picks |
| AI | 8-lead TradingAgents-style desk, fact-checked | no graded record yet (weight 0) |

The two problems to solve are clear: **execution** (costs and the one-day delay eat the edge) and **proof** (deflated
Sharpe). Most of the plan targets those two.

## Layer 1 — Data
1. ✅ Daily panel incl. delisted coins; funding.
2. **Record order books and trades** (top 20 levels every 10 s, aggregated trades) for the ~50 coins in the universe.
   Public history is short, so recording must start now. *Pass:* 30 days stored with no gap over 1 minute.
3. **Hourly panel** from the same archive (for intraday execution and features). *Pass:* closes match the daily panel.

## Layer 2 — Signals (alpha)
| # | Technique | Research | Pass test |
|---|---|---|---|
| 2.1 | ✅ Factor library + IC screen | Liu, Tsyvinski & Wu (JF 2022) | IC stable across years |
| 2.2 | **CTREND**: an ML-combined trend factor built from many technical indicators across the whole universe | Fieberg et al., JFQA 2025 (3,000+ coins) | rank IC t > 3 in walk-forward, positive after costs in ≥ 4 of 5 years |
| 2.3 | **LLM alpha mining**: the AI writes formula factors, a backtest loop scores and refines them | Alpha-GPT (2023), LLM + MCTS (Tsinghua 2025), Chain-of-Alpha (2025) | a mined factor must add IC to the existing composite on a holdout it never saw, after counting every formula tried in the deflation |
| 2.4 | **Order-flow and book features**: order-flow imbalance, depth imbalance, microprice | DeepLOB (2018); "better inputs matter more than deeper nets" (2025); Bieganowski & Ślepaczuk (2026) | predicts the next 1–60 minutes out of sample; used for entry timing, not direction |
| 2.5 | **Foundation-model forecasts** (Kronos, NeurIPS 2025) | gains self-reported; general models mixed on crypto | pre-registered: rank IC > 0 (p < 0.05) at 7 days and better than the factor composite — *needs your approval to install* |
| 2.6 | **News sentiment scored by the LLM** as a factor | FinGPT; sentiment long-short studies | rank IC t > 2 on a holdout; else it stays explanation only |

## Layer 3 — Models
1. ✅ Ridge and LightGBM on factor ranks; walk-forward refits.
2. **Conformal prediction** around each coin's predicted rank: a calibrated interval, so the system can say "too
   uncertain — no trade" instead of always ranking. *Pass:* intervals cover 90% ± 3% out of sample; abstaining raises
   the Sharpe of what is traded.
3. **Regime-aware weighting** (BTC trend, volatility state): one model, regime as an input, not separate models.
   *Pass:* beats the single model out of sample in both regimes.
4. **Online refit** weekly with a drift monitor: alert when live IC falls below its backtest range for 4 weeks.

## Layer 4 — Portfolio
1. ✅ Inverse-volatility weights, a volatility target, per-coin caps, turnover limit.
2. **Cost-aware rebalancing**: trade only when the expected gain beats the cost (a no-trade band) — the direct fix
   for 17.6%/yr of costs. *Pass:* same IC, at least 40% less turnover, higher net Sharpe.
3. **Hierarchical risk parity** across coin clusters (crypto moves in correlated groups). *Pass:* lower drawdown at
   the same return out of sample.
4. Capacity per position from the live order book (✅ built for picks).

## Layer 5 — Execution (the biggest lever)
1. **Realistic timing**: signal at the close, trade at the next open — re-test the LightGBM book this way once,
   pre-registered. *Pass:* the deflated Sharpe gate.
2. **Maker execution**: limit orders at the touch instead of market orders (0.02% instead of 0.05% per side), with the
   fill rate modelled from the recorded order books. *Pass:* net Sharpe up after missed fills are counted.
3. **TWAP/VWAP slicing** for larger sizes; later **reinforcement-learning execution** (RL-Exec, 2025) once book data
   exists. *Pass:* beats TWAP on replayed books.

## Layer 6 — Validation (already strong; keep it)
✅ CPCV, deflated Sharpe, PBO, trial ledger, pre-registered hypotheses, holdout coins and years, the sandbox forward test.
Add: a **live drift monitor** (layer 3.4) and a **monthly evidence report** that refreshes every weight in the fusion.

## Layer 7 — AI layer
1. ✅ The research desk (8 leads, 20 specialists) explains and checks; it earns signal weight only from its graded record.
2. LLM alpha mining (2.3) and sentiment scoring (2.6) — the AI used as a *researcher*, never as an unchecked trader.
3. Deep reinforcement-learning allocation: low priority. Recent crypto studies (SAC/DDPG 2025) still struggle to
   beat simple benchmarks out of sample.

## Layer 8 — Product: machine view and human view
✅ This turn: a **machine-view bar** for every coin (the fused signal, the factor ranks that matter, order flow,
funding across exchanges, regime, which models are accepted), next to the human view (chart concepts and the AI desk
the user can run). The machine decides; the human view explains, and the user can check both.

## Order of work
| Step | What | Why first | Status |
|---|---|---|---|
| 1 | Next-open re-test + maker costs + no-trade band (5.1, 5.2, 4.2) | the edge exists; costs and timing are what kill it | **done 2026-11-11, not promoted** (see below) |
| 2 | Start recording order books (1.2) | everything short-horizon needs it, and it cannot be bought back later | not in this workstream (app/quant/lob) |
| 3 | CTREND factor (2.2) and conformal abstention (3.2) | strongest published evidence; abstention raises the quality of what is traded | **done 2026-11-11, not promoted** (see below) |
| 4 | LLM alpha mining (2.3) with strict trial accounting | uses the AI where it helps: generating ideas, checked by the lab | open |
| 5 | Order-flow timing (2.4), then RL execution (5.3) | needs recorded books | open |
| 6 | Kronos (2.5) | after your approval | open |

### Progress 2026-11-11 (steps 1 and 3)
Pre-registered in docs/research/EXECUTION_PREREG_2026-11-11.md and CTREND_CONFORMAL_PREREG_2026-11-11.md; results
in docs/QUANT_PLAN.md ("Quant lab round 2"). Trial log: N = 159 strategy configurations.
- **5.1 Realistic timing — tested, gate failed on deflated Sharpe only.** Fill at the next open: Sharpe 1.46, about
  the same as at the signal close, because a Binance daily open is one millisecond after the previous close. Fill at
  the next day's VWAP: 1.45. One extra day of delay: 0.41. CPCV 9/9 positive. DSR p = 0.246 at N = 156; PBO, costs ×2,
  beats-EW and VWAP all pass.
- **5.2 Maker execution — fails.** At 0.02% with a 10 bp trade-through fill rule, the daily book drops to 1.20.
  97.8% of orders fill, but the 2.2% that are missed are the runaway winners. With a 25 bp rule: 0.47. Retest only as
  a hybrid (chase unfilled orders), with fill rates from the recorded order books (1.2).
- **4.2 No-trade band — fails** its pass test: turnover −3%, not −40%. The turnover comes from names entering and
  leaving the top/bottom k. Next candidate: rank hysteresis.
- **2.2 CTREND — the factor passes** (walk-forward IC7 0.097, t 11.8; weekly L/S net positive in 5/5 years). It adds
  IC to the composite (t 4.6) but **not to ridge or LightGBM** (t 0.4 at h 3). Its book: 1.52, DSR p 0.246 → not
  promoted. Use it in the composite / risk overlay.
- **3.2 Conformal — coverage passes** (90.0% for nominal 90%, every year within 0.2 pt). **Abstention fails:** at 90%
  the interval never excludes the neutral rank. α = 0.5 gives +0.02 Sharpe (p 0.34). α = 0.25 gives −0.79.
- Engine: next-open, VWAP and maker fills, band, calendar schedule and live-book replay are in
  `app/quant/lab/portfolio.py`. If a book is promoted, `live_ranking()` reproduces its schedule and band.
- Implication for the order of work: more variants on the same 2021–2026 window cannot pass the deflated-Sharpe test.
  Proof has to come from data the book has not seen: the forward sandbox under next-open fills, and holdouts of coins
  or periods. Order-book recording (step 2) is now also the blocker for any maker/hybrid execution test.

## Sources
Cakici, Shahzad, Będowska-Sójka & Zaremba, "Machine learning and the cross-section of cryptocurrency returns" (IRFA 2024) —
https://open.icm.edu.pl/items/7566337e-2e7e-40e7-86da-413d57c313b0 · Fieberg et al., "A trend factor for the cross-section of
cryptocurrency returns" (JFQA 2025) — https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178 ·
Alpha-GPT — https://arxiv.org/html/2308.00016v2 · LLM + MCTS factor mining — https://arxiv.org/abs/2505.11122 · Chain-of-Alpha —
https://arxiv.org/abs/2508.06312v2 · Crypto DRL portfolio (2025) — https://arxiv.org/abs/2511.20678 · Crypto LOB features (2025) —
https://arxiv.org/abs/2506.05764 · RL execution on crypto books (2025) — https://arxiv.org/html/2511.07434v1 · plus the sources in
docs/QUANT_PLAN.md and docs/COMPETITORS.md.

## Layer 1.2 as built — order-book and trade recorder (2026-11-11)
`backend/app/quant/lob/` records Binance USDⓈ-M perps: the top 20 book levels every 10 s and every aggregated trade,
for the 20 most liquid perps by 24h quote volume (refreshed daily at 00:05 UTC, with hysteresis) or an explicit list.
- **Streams (checked live):** `<sym>@depth20@100ms` is served only on `wss://fstream.binance.com/public/stream`;
  `<sym>@aggTrade` only on `/market/stream`. Two combined connections. The 100 ms book is held in memory and sampled on
  the 10-s wall clock; a symbol whose book is older than 3 s is filled from REST `/fapi/v1/depth?limit=20` (`src=r`).
  Aggregate trade ids are consecutive, so any jump (dropped frame, reconnect, Binance's 24 h disconnect) is backfilled
  from REST `/fapi/v1/aggTrades?fromId=`; ids that cannot be recovered are counted as a trade gap.
- **Trades are stored per second, not per trade.** Measured: ~425 aggregated trades/s across the top 20 (hype listings
  such as ZEC and ARIA trade 50–70/s each). Raw trades compress to ~7 B each, which still comes to ~8 GB/month, so
  the recorder keeps per-second rows (count, taker-buy count, buy and sell size, VWAP, high, low, last, last id) at about
  17–28 B each. `trade_mode="raw"` stays available for a few symbols.
- **Storage:** `backend/data/lob/{book|trades_1s}/{SYMBOL}/{YYYY-MM-DD}/{HH}.csv.gz`, appended every minute as a gzip
  member (a crash loses at most a minute), rewritten as one level-9 member after the hour closes; a per-day manifest has
  row counts. Measured cost: book ~0.057 MB per symbol-hour, trades ~0.08 MB, so **~2.0 GB per 30 days for 20 symbols**
  (~5 GB for 50 symbols, which needs a bigger cap or fewer symbols).
- **Budget:** 3 GB cap, 35-day age limit (oldest hours deleted first), and a 2 GB free-disk floor for the machine.
  At the cap, writes are refused and status says so (or, with `evict_to_fit`, the oldest hours are deleted instead).
- **Health:** any book gap over 60 s per symbol is logged and counted (restarts included, using the manifest), as are
  unrecovered trade ids. `status()` reports rows today, the last row per symbol, gaps, REST fallbacks and backfills, and
  disk use.
- **Features** (`features.py`): per-minute order-flow imbalance (Cont–Kukanov–Stoikov, level 1 and multi-level),
  depth imbalance at 5/10/20 levels, microprice, spread and signed trade volume, the inputs for 2.4 entry timing.
  OFI between 10-s snapshots is a coarse version of event-level OFI.
- **Pass test unchanged:** 30 days stored with no gap over a minute, which `status()["gaps_today"]` and the manifest
  can check.
