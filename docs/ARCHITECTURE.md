# Architecture

## Principles
1. **Deterministic code computes every number**: prices, indicators, structure detections, R:R, sizing.
2. **ML gives probabilities only after validation.** Models that fail purged walk-forward evaluation against
   baselines, after costs, are registered as `rejected` and never served.
3. **LLMs explain and orchestrate.** They read structured evidence through tools and change the chart only
   through strictly-typed, server-validated actions. They never invent values or execute code.
4. **Closed candles only** for analysis (no repainting). Every detection carries its confirmation time.
5. **Append-only signal history.** Levels freeze at confirmation, transitions are events, and outcomes are
   written once.
6. **Abstain by default.** The decision is `NO_TRADE` unless a confirmed setup passes every validation gate.

## Runtime (single process, event-driven)

```
Binance spot WS (klines, 5 TFs × 20 symbols)      Binance USDⓈ-M WS /market (mark price, liquidations)
          │  REST backfill + gap repair + resync after reconnect          │
          ▼                                                               ▼
   candles table ◄── upsert (idempotent, validated) ── MarketHub (in-memory live state, freshness)
          │                                               ▲   ▲
   bus: candle_closed                       derivatives poller (60s)  order-book poller (60s)  news RSS (5m)
          ▼
   Engine.scan(symbol, tf)  ── only for scan TFs (15m/1h/4h), max 4 concurrent
     ├─ load closed candles + HTF frames + derivatives + book + news + ML (accepted only)
     ├─ build_snapshot(): indicators · SMC · Elliott · price action · regime · evidence · confluence
     ├─ evaluate_strategies() → candidates (forming / pre_signal / confirmed)
     ├─ validate() → gates + counter-arguments + quality score
     ├─ decide() → LONG / SHORT / NO_TRADE with reasons
     └─ signals.manager.process_scan() → Signal + SignalEvent rows, notifications
   outcome tracking: each closed candle of a signal's timeframe advances TradeSim (same rules as backtester)
   paper loop (2s): stops/targets, funding at funding times, equity points
   health loop (30s): stale feeds → alerts; DATA_UNAVAILABLE after 6h without data
   housekeeping (1h): prune unreferenced snapshots, rolling strategy stats → degradation suspension
          │
   bus → WebSocket /ws → browser (candles, signals, notifications, scans, liquidations)
```

There is no LLM call in the engine loop (cost control). The analyst chat, the agent review and the Chief Analyst
narrative call the LLM on demand only, and fall back to deterministic text when no key is configured.

## Multi-agent graph (LangGraph, `app/agents/graph.py`)
Stage 1 runs in parallel: market_data, technical, smc, elliott, derivatives, ml_forecast, news, regime.
After a join, stage 2 runs in parallel: strategy, risk, validation, counter_argument, chart_annotation,
portfolio, research. The final node is chief_analyst (LLM narrative with a template fallback).
Each agent returns a Pydantic `AgentReport` (status, summary, findings with provenance, data, timing). Agents
time out after 30s and fail independently.

## Module map (`backend/app`)
| Module | Responsibility |
|---|---|
| `data/` | Binance REST client (weight budget, retry, failover to data-api.binance.vision), WS streams, backfill, universe ranking, hub |
| `analysis/indicators/` | Registry of 41 indicators (trend, momentum, volatility, volume/flow), each tested for look-ahead |
| `analysis/price_action.py` | Fractal pivots, S/R clustering, trendlines, breakouts/retests, candlestick patterns, Fibonacci, compression |
| `analysis/smc.py` | BOS/CHoCH/MSS (internal/external), OBs, breakers, mitigation blocks, FVG/IFVG, voids, EQH/EQL, liquidity, sweeps, premium/discount, OTE, inducement, sessions, PDH/PDL/PWH/PWL |
| `analysis/elliott.py` | Rule-filtered wave enumeration (impulse, diagonal, zigzag, flat, triangle), alternates, invalidation |
| `analysis/microstructure.py` | Book imbalance/walls/slippage, funding z, OI change, basis, squeeze flags, liquidation-band estimates |
| `analysis/regime.py` | Transparent rule-based regime classifier |
| `analysis/evidence.py` | Family-capped confluence aggregation (no double counting) |
| `analysis/annotations.py` | Structured, explainable chart objects with deterministic ids |
| `strategies/` | 8 strategies with full specs; validation and counter-argument review |
| `signals/` | TradeSim (shared with the backtester) and the lifecycle manager |
| `risk/` | Risk-based sizing and Binance isolated-margin liquidation formula |
| `backtest/` | Event-driven backtester, cost stress scenarios, portfolio constraints, metrics (PSR, CVaR, bootstrap CI) |
| `ml/` | Causal features, triple-barrier labels, purged walk-forward, LightGBM vs baselines, isotonic calibration, registry, inference with TreeSHAP and PSI drift |
| `news/` | RSS collection, clustering and corroboration, coin extraction, event typing, lexicon sentiment, impact |
| `portfolio/` | Paper accounts: sizing, fees, funding, exits, metrics, correlation risk |
| `agents/` | LangGraph agents and the Claude layer (narrative and tool-using analyst chat) |
| `api/` | FastAPI routers (market, user, chat) and the WebSocket |

## Database schema (SQLAlchemy, `app/models.py`)
`candles` (PK symbol, market, timeframe, open_time) · `derivatives_snapshots` · `universe_snapshots` · `users` ·
`watchlists` · `chart_layouts` · `drawings` (origin ai/user, parent_id → AI annotation id) · `analysis_snapshots`
(immutable, versioned) · `signals` · `signal_events` (append-only) · `notifications` (dedupe_key unique) ·
`paper_accounts` · `paper_positions` · `equity_points` · `news_items` · `backtest_runs` (config hash) ·
`model_records` · `audit_logs`.

SQLite (WAL) for local dev; Postgres/TimescaleDB in production (`docker-compose.yml`).

## Scaling notes
- Bus: the in-process pub/sub has drop-oldest backpressure. To run several API replicas, move the bus to Redis
  pub/sub and keep exactly one engine process.
- Scans: about 40–80 ms each. 20 symbols × 3 timeframes takes well under 10 s of CPU per hour, at a 4-scan
  concurrency limit.
- Rate limits: client-side weight budgets of 5000/min spot and 1800/min futures (exchange limits are 6000 and
  2400).
