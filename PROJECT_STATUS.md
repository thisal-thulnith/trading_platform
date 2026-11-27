# Project status — resume here

_Last updated: 2026-11-01_

## TL;DR
A working, integrated platform: live Binance data → deterministic analysis → strategies → validation →
signal lifecycle → notifications / paper trading / outcome tracking, plus backtesting, an ML research pipeline,
a multi-agent review, an LLM analyst chat, and a TradingView-style terminal with editable AI annotations.
**145 backend tests pass** (DB tests also pass on TimescaleDB), the frontend production build passes, and it
has been verified live against real exchange data with browser screenshots.

**Research verdict so far:** the default strategies **lose after costs** in backtests and the first ML model was
**rejected**. The platform therefore mostly outputs NO TRADE and auto-suspends losing strategies. That is the
honest state. No edge is claimed.

## How to run
```bash
cd backend && uv run uvicorn app.main:app --port 8100     # API + 24/7 engine (SQLite in backend/data)
cd frontend && npm run dev -- -p 3100                      # UI at http://localhost:3100 (NEXT_PUBLIC_API_URL in .env.local)
cd backend && uv run pytest -q                             # tests
```
Port 8000 was already used by another app on the dev machine, so the project uses **8100** (API) and **3100** (web).

## New in 0.21 (2026-11-11)
- Named leads with specialist teams (Atlas, Vega, Tide, Ledger, Raven, Sentinel, Verity, Apex; 20 specialists) and a
  Fundamentals team with live CoinGecko/DefiLlama data. Tests pass with a scripted LLM; **not yet run end to end with a
  real model** because the OpenAI key is expired — renew it and run one analysis to check prompts, cost and timing.

## New in 0.15 (verified live, 2026-11-10)
- Home is the **Research desk** (`frontend/src/components/desk/ResearchDesk.tsx`, `backend/app/agents/research_desk.py`): research → analysis → debate → plan → decision, real tools, plan numbers from `app/risk/plan.py`.
- Add-trade form gets the full plan from the agents (`POST /api/agent/plan`). Shorts supported in My trades (futures only).
- Unused pages archived in `frontend/archive/` (see its README to restore one).
- **The OpenAI key in backend/.env has expired** (2026-11-10): agents run rule-based until the user renews it.

## New in 0.2 (verified in browser)
- Guided step-by-step analysis with chart focus mode, synthesis, scenarios and confidence (`app/analysis/walkthrough.py`, `frontend/src/components/terminal/Walkthrough.tsx`).
- Learn page (`frontend/src/app/learn/`).
- Live P&L % for signals and positions (`frontend/src/components/ui/Pnl.tsx`, `signals/manager.py::pnl_fields`).
- Simulator R-basis bug fixed. Outcomes corrected via `app/maintenance/recompute_outcomes.py` (audit events). Backtests re-run (see STRATEGIES.md).

## Completed (verified)
| Area | Status | Evidence |
|---|---|---|
| Research KB | ✅ | docs/RESEARCH.md, TRADING_CONCEPTS.md, DATA_SOURCES.md, ML_RESEARCH.md (with citations and evidence grades) plus empirical findings |
| Data ingestion | ✅ | Spot kline WS (100 streams), futures `/market` WS (mark price, liquidations), REST backfill with backward extension and gap repair, weight budgets, retries, fallback host, resync on reconnect, freshness in the UI |
| Universe | ✅ | Ranks by market cap (CoinGecko), volume, perp availability, spread; filters non-TRADING pairs (TON was in BREAK) and stablecoins/wrapped tokens; refreshes every 6h |
| Derivatives | ✅ | Funding, OI (own snapshot history), L/S, taker ratio, basis, squeeze flags, liquidation prints, estimated liquidation bands (labelled as estimates) |
| Indicators | ✅ | 41 indicators, registry, look-ahead tests for all of them, cross-checked against the `ta` library |
| SMC | ✅ | BOS/CHoCH/MSS (internal/external), OB/breaker/mitigation, FVG/IFVG, voids, EQH/EQL, liquidity and sweeps, premium/discount, OTE, inducement, sessions, PDH/PDL/PWH/PWL. Repaint tests |
| Elliott | ✅ | Rule-filtered counts at 3 scales, alternates, invalidation, projections, uncalibrated scores |
| Regime | ✅ (rule-based) | 10 regimes, used for strategy suitability and abstention |
| Strategies + validation | ✅ | 8 strategies, family-capped confluence, 11+ validation gates, counter-argument review, NO-TRADE engine, research gate |
| Signal lifecycle | ✅ | FORMING→PRE_SIGNAL→CONFIRMED→ACTIVE→outcomes; append-only events; frozen levels; live fills observed (TRX short went ACTIVE) |
| Notifications | ✅ | In-app, browser, Telegram, SMTP (server-side config), deduplicated, linked to signal/snapshot |
| Paper trading | ✅ | Onboarding risk profile, risk-based sizing, fees, funding, stops/TPs, drawdown pause, correlation risk |
| Backtesting | ✅ | Event-driven using the live engine code, cost stress, portfolio constraints, IS/OOS, breakdowns, PSR, bootstrap CIs, config hash |
| ML | ✅ pipeline / ❌ no accepted model | Causal features, triple-barrier labels, purged walk-forward, LightGBM vs prior/LR baselines, isotonic calibration, reliability diagram, TreeSHAP, PSI drift, registry |
| Agents | ✅ | LangGraph, 15 agents + chief, typed reports, timeouts, deterministic fallback |
| LLM chat | ✅ (needs `AIT_ANTHROPIC_API_KEY` for LLM mode) | Strict tools, validated chart actions, server-side refusal fallback; deterministic mode works without a key |
| Frontend | ✅ | Terminal (chart, 9 drawing tools, drag-edit, undo/redo, AI layers, indicators/panes, volume profile, screenshot, layouts, replay), radar, signals and timeline, snapshots and diff, portfolio, backtest lab, research lab, news, health, settings, methodology |
| Auth/security | ✅ | bcrypt, JWT, rate limits, ownership checks, audit log, prod secret guard |
| Deployment | ✅ (files) | Dockerfiles + docker-compose (TimescaleDB). Compose stack not yet run end to end |

## Live backtest / research results
See docs/STRATEGIES.md and docs/RESEARCH.md § Empirical findings. Gate status (auto-refreshes):
suspended (statistically losing): fvg_continuation (1h, 15m), trend_pullback (1h), smc_ob_retest (15m),
breakout_retest (15m), volatility_breakout (15m). Everything else is inconclusive or experimental.
Corrected backtests: 1h −0.31R (n=1014), 4h −0.11R (n=505), 15m −0.48R (n=856).
Every-trade $100 tracker (`/follow`, `GET /api/follow_all`): replays every filled live signal at fixed % risk.

## 0.3.0 additions
- War Room (`/room`), Analysis Board (`/board`), Every-trade $100 tracker (`/follow`), OpenAI provider.
- Exit-profile study: `edge_tp1r_q50` is live on 4h (IS +0.07R, OOS +0.11R). The pre-registered replication on 1h
  was **not** significant (+0.02R), so 1h keeps strategy-native exits. Cached setups are in `backend/data/setups/`;
  re-run the study with `uv run python -m app.maintenance.exit_study study --tf 4h`.

## Known gaps / next steps (priority order)
1. **Find a real edge** (see ROADMAP §1): time-series momentum and carry strategies, exit redesign (MFE ≈ 1.5R),
   larger samples for breakout_retest and liquidity_sweep_reversal, deflated Sharpe across all tested variants.
2. **ML v2:** better calibration, drop calendar features, non-overlapping evaluation, meta-labelling of strategy
   signals.
3. **Failover providers:** Bybit/OKX clients are not implemented (only Binance with a fallback host).
   Bybit OI history backfill.
4. **Ops:** run `docker compose up` end to end; Alembic migrations (currently `create_all`); Redis bus for
   multi-replica; CI pipeline; frontend E2E tests; WebSocket fault-injection tests; load test.
5. **UI:** channel tool; per-user alert thresholds; Telegram linking flow; anchored-VWAP anchor picker (the
   param exists, but the anchor is set manually in the indicator dialog).
6. **Not implemented from the original brief:** pairs/stat-arb strategy, HMM regime model, deep-learning
   forecasters (documented as a research track), on-chain data.

## Gotchas for the next session
- The Binance futures WS **must** use `wss://fstream.binance.com/market/stream`; the un-routed URL is silent.
- SQLite stores naive datetimes. Always `.replace(tzinfo=UTC)` before `.timestamp()`.
- Restarting the backend kills running backtests (they run in-process threads).
- `research_gate` uses the newest completed backtest per strategy/timeframe with n ≥ 10.
- The dev database is `backend/data/aitrade.db`. Model artifacts are in `backend/data/models/`.
