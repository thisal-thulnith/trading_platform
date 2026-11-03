# Roadmap

## Done (v0.1)
Data foundation, 41 indicators, SMC, Elliott, microstructure, regime detection, 8 strategies, validation and
counter-argument review, signal lifecycle, notifications, paper trading, event-driven backtesting with stress
scenarios, ML pipeline with a strict acceptance gate, 15-agent LangGraph review, Claude analyst chat with
validated chart tools, TradingView-like terminal with editable AI annotations, replay, research lab, Docker.

## Next (priority order, evidence-driven)
1. **Strategy research.** Current backtests show negative expectancy for trend_pullback and fvg_continuation;
   these are automatically suspended. Ideas to investigate (pre-registered, walk-forward, deflated Sharpe):
   time-series momentum on 1d/4h with volatility targeting (strongest literature support); funding/basis carry;
   breakout_retest with a larger sample; wider targets with trailing exits (backtest MFE ≈ 1.5R suggests exits
   are part of the problem).
2. **ML.** Fix calibration (Platt + per-regime base rates), remove spurious calendar features (`dow` was the top
   feature, a red flag), non-overlapping evaluation samples, meta-labelling of strategy signals instead of raw
   direction prediction.
3. **Data.** Bybit/OKX failover clients, Bybit OI history backfill (Binance keeps 30 days), our own long-term
   snapshot archive, data.binance.vision bulk import for multi-year history, 1m streams for finer outcome
   tracking.
4. **Regime.** Train an HMM/GMM regime model and accept it only if it improves strategy selection out-of-sample.
5. **Infrastructure.** Redis bus, Alembic migrations, CI pipeline (tests, lint, build, dependency audit),
   Prometheus metrics, frontend E2E tests.
6. **Product.** Channel drawing tool, multi-chart layouts, per-user alert thresholds UI, Telegram linking flow,
   on-chain metrics.
