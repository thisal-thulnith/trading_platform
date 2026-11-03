# Changelog

## 0.14.0
- **Signal cards** (`app/portfolio/signals.py`, page `/system`): the system's candidates for the next daily open as Binance-style cards — live setups (entry, stop, 2R target, quantity, margin at the capped leverage, liquidation, risk, this coin's own breakout odds) with a "Select this trade" button that opens the fill form and turns the card into a running trade on the same page; live setups the risk engine skipped, with the reason; and the coins nearest a breakout, marked "no entry yet" with the level that must close above. Your running trades (live P&L, next action, partial/close) show on the system page too.
- **Top list, always analysed**: the ranked top list no longer waits for the AI desk — the rules rank the coins (live setups, nearest breakout, momentum) and every row carries its analysis card and a plain-English reason (`desk_tools.reason`); the Scout agent refines it when the AI is available. The rule-based Scout and Manager now speak from this list.
- **Margin-based entry**: the trade form takes the margin (or the amount on spot) and the leverage, Binance-style, and derives the quantity; changing the leverage keeps the margin and changes the size.
- **One clear AI notice**: pages carry the LLM status (`llm` in `/api/system` and `/api/my/portfolio`, from `app/agents/llm.status()`); when the provider fails (e.g. OpenAI "no credits remaining") the desk shows one banner with the reason instead of a failure line under every agent, and the briefing/desk retry automatically.

## 0.13.0
- **Intelligent desk** (`app/agents/toolkit.py`, `desk_tools.py`, `intel_desk.py`; `GET /api/system/desk`, `GET /api/my/desk`): every desk agent is now an LLM agent with tool use (OpenAI function calling or Claude tool use, whichever key is set). Tools: market regime, ranked coin universe (the researched 35 and the quant brain's ~120 coins with its walk-forward proof), per-coin analysis, research facts (system, leverage, win rate, carry, shorts), account state, budget plan, futures ticket, live funding and the user's portfolio. The agents run as one sequential conversation (each sees the conclusions before it) in the background, cached for an hour; the page shows each agent's analysis, the tools it called and its confidence, with the deterministic facts underneath as the audit line. Guardrails: every number must come from a tool result; agents cannot place orders or change risk; without an LLM key the rule-based agents carry the page.
- **Scout's top list — already analysed**: the Scout ranks up to 8 coins (live setups, near-breakouts, trend rank, momentum, crowding and each coin's own breakout record since 2020) and the page shows them with the full analysis card and the Scout's reason.
- **Portfolio desk** (Planner, Position Manager, Coach) runs on the same framework with the user's cards as a tool. The account view is cached for two minutes so the page, the desk and the portfolio share one replay.

## 0.12.0
- **Budget planner** (`app/portfolio/budget.py`): the user types their own budget and the agents turn it into the settings they invest with — risk per trade in $, max trades and total risk, typical position, margin and safe leverage, which coins are too small at the exchange minimum, the mode or budget that fixes it, and what a year like 2020-now / 2024 / 2025-26 would have done to that budget. Shown on `/system` (the Risk Officer reads it) and `/portfolio`.
- **My trades** (`app/portfolio/my_trades.py`, table `my_trades`, `GET /api/my/portfolio`, `POST /api/my/trades`, `/partial`, `/close`, `DELETE`): "I took this trade" on any ticket records the trade the user actually placed. The portfolio page shows it as a Binance-style card (contract, Isolated N×, size, entry, mark, liquidation, margin, stop, TP, unrealised P&L, ROE, R, days) marked to the live price, with the next action from the daily candles (stop touched, 2R reached — sell a third and move the stop to entry, raise the trailing stop, 60-day limit). The user confirms the partial and the close with the real fills; closed cards keep the realised P&L, % and R. Login optional (trades belong to the installation without a token, to the user with one).
- **Portfolio desk**: Planner, Position Manager and Coach (your record against the research; flags leverage above the safe cap, risk above the plan and open risk above the limit). The old auto-follow paper account moves below, collapsed.

## 0.11.0
- **Leverage study** (`app/maintenance/leverage_study.py`, results in `data/history/leverage_study.json`): the system's own 197 growth-mode trades 2020-now re-run at 1-50x. Stops sit a median 16.6 % below entry and the median winner first fell 6.4 % before it paid. At 20x, 86 % of trades and 68 % of winners are liquidated before paying, the average falls from +0.64R to +0.17R, and $100 traded at 20x with 20 % margin per trade ends at $4 (worst drop 97 %). At 3x: 7 % liquidated, no winner lost. Every stop tight enough for 20x (fixed 0.5-3 %, daily and 4-hour candles) lost money in every period; the "1 % move" trade (0.5 % stop on 4h) averaged -0.67 to -0.86R.
- **Win rate vs payoff**: same signal and stop, fixed targets 0.25R-3R. 0.25R wins 74-83 % and earns about 0 after costs (-0.08R in 2024); 2R wins 38-48 % for +0.13 to +0.42R; 3R wins 32-42 % for +0.21 to +0.61R, the most. No setting reaches 90 %.
- **Binance Futures venue** (`app/risk/futures.py`; `GET /api/system?venue=futures&leverage=20`): the same trades as USDⓈ-M tickets — isolated margin, quantity on the exchange lot step, STOP_MARKET and TAKE_PROFIT_MARKET reduce-only legs, margin, liquidation price, funding per day — with the leverage capped at the largest value whose liquidation is at least 1.5x the stop distance away. Live lot/tick/minimum filters from the public exchange-info endpoint (BTC 50 USDT, ETH 20, most others 5). Manual placement only; nothing is sent to Binance.
- **Leverage agent** joins the desk (seven agents); the Auditor now reports the win-rate study. Honest notes carry the leverage and win-rate facts. The `/system` page gets a venue switch, a leverage selector, ticket and how-to cards, and the two research tables.

## 0.10.0
- **Bigger winners:** a third (not half) is sold at 2R and the rest trails. Chosen on 2020-24 only: growth mode $100 → $526 vs $419. Same result on the 2025-26 test, because no coin trended.
- **Fix:** the loss guard could pause the account forever (it lifted only after new trades closed, and none could open). It now lasts 30 days. Conservative 2020-now: $206 (was stuck at $183).
- **Tested, not adopted:** wider trails (4-5 ATR), 120-day holds, market-breadth gates (30-50 %); none was better on 2020-24 and 2025-26 together.
- **Carry research** (`app/maintenance/funding_history.py`, `carry_study.py`): spot + short-perp hedge on the highest-funding coins. About +8 %/yr in 2020-23, +5.7 % in 2024 and +0.7 % since 2025, with drawdowns under 3 %. Funding on BTC/ETH fell from about 30 %/yr (2021) to about 3 % (2026).
- **Carry agent** joins the system desk with live 7-day funding for 35 coins and says when the hedge beats its costs. Notes on the page now read their numbers from the research files.

## 0.9.0
- **Risk modes** (`RISK_MODES` in `app/risk/portfolio.py`; `GET /api/system?mode=`): conservative 1 %, growth 2 %, aggressive 3 % per trade, each with its own position and total-risk limits. Research on $100, Jan 2020 to now: conservative $194 (+10 %/yr, worst drop 15 %), growth $419 (+23 %/yr, 18 %), aggressive $374 (+21 %/yr, 28 %). On the 2025-26 test: -1 %, -10 %, -17 %.
- **Tested and rejected** (they failed validation): bear-market shorts (2024 validation about -0.6R per trade), other breakout lengths (no robust gain), and win-streak adaptive sizing (on $100 it shrinks orders below the $5 minimum and stops trading).
- **System desk** (`app/agents/system_desk.py`): five specialist agents (Regime with breadth, Scout, Risk Officer, Auditor checking the live win rate against the research with a Wilson range, Manager) built on computed numbers. Optional LLM briefing that may only rephrase them.

## 0.8.0
- **Why the live signals lost (-35.5R over 36 closed trades):** stops were often 0.07–0.3 % away, so fees and slippage alone cost more than the risk and an average stop-out lost 1.5R; targets started at 1.2R. Validation now requires reward/risk >= 2.0 net of costs and costs <= 20 % of the risk, and every scanned timeframe uses the new `edge_tp2r` exit (single 2R target).
- **Edge study** (`app/maintenance/edge_study.py`, history from `app/maintenance/history.py`): 6.5 years of public 4h/1d candles for 35 coins; 384 breakout/pullback/squeeze configurations at >= 1:2 with 0.15 %/side costs, pre-registered train 2020-23 / valid 2024 / test 2025-26. Result: 4h setups mostly follow the market's direction (no stable edge); passing train + validation is not enough (average test result -0.06R). The daily 20-day breakout with a BTC 200-day filter is the one family positive in all three periods.
- **Daily trend breakout** (`app/strategies/daily_trend.py`) and **system risk engine** (`app/risk/portfolio.py`): 1 % risk per trade, at most 4 positions / 4 % open risk / 30 % per coin, no leverage, $5 order minimum, drawdown brakes at 10 % and 20 %, and a loss guard that pauses entries. Proof on $100: 2020-23 $100 → $220 (worst drop 12 %); 2024 $99; 2025-26 test $99 (worst drop 9 %).
- **System account** (`GET /api/system`, page `/system`): the platform's own $100 paper account with next orders, open positions, the year's trades, rules and the research proof. Paper only.

## 0.7.0
- **Quant brain** (`app/quant/brain.py`): cross-sectional trend score for the ~120 most liquid coins (8 signals, ranked and averaged), market regime (BTC 50/200-day, volatility percentile, breadth), funding/basis crowding for every perp, and a weekly walk-forward proof net of costs with per-quintile up-rates. `GET /api/quant`.
- **Forecast Ledger** (`app/quant/ledger.py`, table `forecasts`): every agent, the quant model and the team state P(price higher after 12 candles); forecasts are graded every 5 minutes, scored (Brier, skill, calibration slope, bias) and turned into shrunk trust weights. `GET /api/ledger`.
- **Setup odds** (`app/quant/odds.py`): "X of 10 similar setups won" from completed backtests and live signals, with Wilson ranges. `GET /api/odds`.
- **Forecast hub** (`app/agents/forecast_hub.py`): team probability = weighted average of calibrated forecasts; attached to every session signal (forecast, odds, quant rank, regime, case against) for both the LLM and rule-based brains.
- **Quant agent** joins the Desk and reports regime, rank, crowding, odds and the honest walk-forward result; specialists now give a scored `p_up`.
- Background intelligence loop (quant refresh + ledger grading every 5 minutes). 167 tests pass.

## 0.6.0
- **AI agent team** (`app/agents/live_room.py`): Chief (an LLM) reads the scan of every coin and the time of day, picks the coin, and chooses which specialists fit the market and what to ask them. Specialists are LLMs with method knowledge (SMC, ICT, Wyckoff, Elliott/NeoWave, Gann, Market Profile, Harmonics, Hurst, Quasimodo, Bill Williams, Dow, momentum, derivatives). They decide what to draw and which indicators to add, and reply to each other. Chief then runs a debate on the biggest disagreement, Skeptic attacks the leading view, Guard sizes the risk, and Chief decides.
  - Guardrails: measured data only; drawings and indicators are allow-listed; the LLM cannot trade an unvalidated setup, and Guard overrides it if it tries.
  - Sessions stream: `POST /api/room/session`, `GET /api/room/session/{id}?after=n`.
  - Without a key, the rule-based team runs and the UI says so.
- **One app:** the Desk (`/`) merges War Room and Simple signals: coin list with signals, the agents' chart, signal and discussion.
  - Moved: the manual terminal to `/terminal`; expert pages to the **Advanced** menu.
  - `/room` and `/simple` redirect to `/`.
- **Human-like mouse:** curved Bézier paths, Fitts's-law duration, ease in/out, small overshoot, idle drift, and "thinking…" bubbles while an agent's LLM call runs.
- Tests: `tests/test_live_room.py` (fake LLM: invented objects and agents dropped, forced trades overridden).

## 0.5.0
- **All coins:** `GET /api/coins` lists every tradeable USDT pair on Binance spot (about 500; stablecoins and leveraged tokens removed). Scout (`app/agents/scout.py`) screens them all, then deep-analyses every coin with at least $2M daily volume in a background job (progress via `GET /api/room/scan`, cached 5 min). Coins outside the streamed universe are refreshed over REST before analysis. Any coin can be opened in the War Room.
- **Human-like agents:** cursors float over the whole page. Agents click the real toolbar tools, press at the start point and drag drawings out. They open the Indicators menu and add real indicators: Atlas EMA 50/200, Pulse RSI + MACD, Session daily VWAP, Chaos Awesome Oscillator, Auction the volume profile. Scout clicks rows in its scan list. Added `ChartControl` and `indicatorPreset` to `TradingChart` (the War Room never changes the user's saved layout), and `data-agent-target` on toolbar and menu items.
- **Simple signals page** (`/simple`): BUY / SELL / WAIT for every analysed coin in plain words, with entry, stop and target, what it means for a $100 account, and a link to watch the agents.
- War Room shows a signal card with a "Skip to signal" button; blocked setups are explained in beginner words.
- Fix: production now builds into `.next-prod`, so running `next dev` in the same folder no longer breaks the site (CSS/JS returned HTTP 400).

## 0.4.0
- War Room council: 10 new agents — Composite (Wyckoff), Dow, Angle (Gann), Auction (Market Profile), Fib (Harmonics), Session (ICT Power of 3 + killzones), Neo (NeoWave), Cycle (Hurst), QM (Quasimodo), Chaos (Bill Williams). Each draws its evidence, gives a verdict and quotes its measured out-of-sample record.
- Scout agent: `GET /api/room/scan?tf=` ranks every coin in the universe; `GET /api/room/AUTO?tf=` scans and runs the room on the top pick. Added the War Room "Let Scout pick the coin" toggle and a scan panel.
- `app/analysis/schools.py` (rule-based analysers) and `app/maintenance/school_study.py` (IS/OOS track record). Result: no school has a proven edge on its own, so the council can only veto or reduce size.
- Added 8× playback, the Learn lesson "The ten classic schools" and 6 new tests.

## 0.3.0 — 2026-11-01
### Added
- **Agent War Room** (`/room`, `GET /api/room/{symbol}?tf=`): ten specialist agents (higher timeframe, structure,
  liquidity, order blocks/FVG, Elliott, momentum, futures/news, devil's advocate, risk manager, head trader), each
  with its own cursor on the chart. They point at and draw their evidence, talk, reply to and disagree with each
  other, and end with the head trader's call. Every message comes from the deterministic analysis
  (`app/agents/room.py`), so nothing is invented. Playback at 1×, 2× or 4×.
- **Analysis Board** (`/board`): drag-and-drop view of the agent's reasoning. It has a concept library, an ordered
  reasoning chain with a trust slider per concept, a live conclusion (same arithmetic as `walkthrough.synthesize`,
  whose weights and rules are now returned by `/api/walkthrough`), click-to-show-on-chart, and an animated "Replay
  agent thinking".
- **OpenAI provider** (`AIT_OPENAI_API_KEY`, `AIT_OPENAI_MODEL`, `AIT_LLM_PROVIDER=auto|anthropic|openai`) for chat
  (function calling with the same strict tools), agent narration and walkthrough commentary. Keys stay server-side.
- **Exit profiles** (`app/strategies/exits.py`), shared by live signals, the backtester (`BacktestConfig.exit_profile`)
  and research. `edge_tp1r_q50` is adopted on 4h after the exit-profile study.
- **Exit-profile study tool** (`app/maintenance/exit_study.py`): cached setups for 19 symbols, profile × filter grid,
  IS/OOS split and a pre-registration file.
- Learn: "The Analysis Board" and "Why a 95% win rate is a trap".

## 0.2.0 — 2026-11-01
### Added
- **Every-trade $100 tracker** (`/follow`, `GET /api/follow_all?capital=&risk_pct=`): a hypothetical account
  that took every filled signal at fixed-fractional risk, compounding in time order, with banked vs live P&L, a
  per-trade $ and % of account, an equity curve, drawdown and the peak leverage this would have needed.
- **Guided step-by-step analysis** (`GET /api/walkthrough/{symbol}`; "▶ Step-by-step analysis" in the terminal).
  It runs top-down over higher timeframes and 15 concept steps (big picture, regime, structure, levels, liquidity,
  zones, FVGs, premium/discount, Elliott, momentum, derivatives, news, scenarios, plan, verdict). Each step has a
  beginner explanation, a live observation, a verdict and a **chart focus mode** that shows only that step's
  objects. A weighted synthesis (price action first, indicators last) produces a story, bull/bear scenarios with
  triggers, targets and invalidations, and a confidence label. Optional Claude commentary. Also available to the
  chat as the `explain_step_by_step` tool.
- **Learn page** (`/learn`): platform guide plus 30+ lessons on trading A→Z, concept diagrams, a searchable
  glossary and "see it on the live chart" links.
- **Live P&L %** on every trade: active signals tick in real time (% and R), closed signals show the realised
  net %, signals awaiting entry show the distance to entry, paper positions show P&L % and ROE %, and chart
  position boxes show "now ±x%".

### Fixed
- **Simulator R basis (affects live outcomes and backtests).** R was divided by the *actual* fill-to-stop
  distance. When price gapped through the entry zone and filled next to the stop, R exploded (e.g. −23.7R on a
  normal stop-out). R is now measured against the planned risk (zone midpoint to stop), and a bar that opens
  beyond the stop no longer fills (INVALIDATED). All 7 resolved signals were re-simulated, with append-only
  audit-correction events. All pre-fix backtest runs were marked `invalidated` and re-run.

## 0.1.0 — 2026-11-01
First integrated release: research knowledge base, live data engine, analysis engines (indicators, SMC,
Elliott, microstructure, regime), strategies with validation, signal lifecycle with notifications, paper
trading, backtesting, ML pipeline with acceptance gate, multi-agent review, LLM analyst chat, interactive chart
terminal, research lab, Docker deployment, 138 automated tests.

### Fixed during verification
- News recency used fetch time instead of publication time, which made every market `news_instability` on
  start-up.
- Backfill did not extend history backwards for symbols that already had recent candles.
- A first-seen confirmed signal logged a redundant CONFIRMED→CONFIRMED event.
- AI chart objects were decluttered (nearest liquidity, recent active OBs and FVGs, recent structure only).
