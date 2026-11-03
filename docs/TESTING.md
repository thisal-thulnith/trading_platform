# Testing

```bash
cd backend && uv run pytest -q                       # 145 tests, ~25 s
AIT_TEST_DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/db \
  uv run pytest -q tests/test_signal_lifecycle.py tests/test_paper.py tests/test_backfill.py   # same tests on Postgres
cd frontend && npx tsc --noEmit && npm run lint && npm run build
```

| Area | Tests | What is verified |
|---|---|---|
| Indicators | `test_indicators.py` (88) | **Look-ahead test for every registered indicator** (values at bar *i* don't change when future bars are appended), output shapes, cross-validation against the independent `ta` library (RSI, MACD, BB, ATR, CCI, %R, OBV, CMF, MFI, Stoch, ADX, ROC, AO, UO, Donchian), parameter validation, volume-profile value area, CVD = taker data |
| SMC | `test_smc.py` | Pivot confirmation delay, zigzag alternation, FVG geometry, BOS→CHoCH sequence, **structure events never repaint** across truncation points, FVG creation never repaints |
| Elliott | `test_elliott.py` | Hard rules R1/R2/R3 reject invalid counts; textbook impulse scores high; in-progress invalidation levels |
| Analyzer | `test_analyzer.py` | End-to-end snapshot is reproducible (byte-identical JSON), unique annotation ids, family capping prevents double counting |
| Strategies | `test_strategies.py` | All strategies run on varied data; level consistency; complete specs; stale data blocks confirmation |
| Risk | `test_risk.py` | Risk amount equals the profile, fees shrink size, leverage/notional caps, wrong-side stops, Binance isolated liquidation formula, net R:R, portfolio risk budget |
| Simulator | `test_simulator.py` | Full target path, stop-first on ambiguous bars, breakeven after TP1, invalidation before entry, expiry, costs, short mirror |
| Backtest | `test_backtest.py` | HTF resampling drops incomplete buckets, metrics, end-to-end run, stress costs never improve results |
| ML | `test_ml.py` | **Features are causal**, triple-barrier labels resolve within the horizon, purged walk-forward has no overlap, **the acceptance gate rejects a random walk** |
| Signal lifecycle (DB) | `test_signal_lifecycle.py` | FORMING→PRE_SIGNAL→(blocked)→CONFIRMED→ACTIVE→TARGET_HIT; levels frozen after confirmation; append-only events; vanished setups invalidated; notification dedupe; first-seen-confirmed transition |
| Paper trading (DB) | `test_paper.py` | Risk-sized fills, stop exits, funding direction, take-profit accounting, max positions |
| Ingestion | `test_backfill.py` | Backfill extends history backwards and leaves no gaps (mocked HTTP) |
| Walkthrough | `test_walkthrough.py` | All 15 concept steps present, focus objects reference real chart annotations, synthesis confidence, works without HTF data |
| News | `test_news.py` | Coin extraction, event typing, sentiment sign, impact rule, title similarity |

## Live verification performed (2026-11-01)
- All public endpoints, both WebSocket paths (spot and the futures `/market` route) and the RSS feeds were
  verified against real services.
- Engine run: backfill of 20 symbols × 5 timeframes took 18 s. Streams connected, 0 gaps and 0 rejected
  candles, 60 scans per cycle, and signals with notifications were created.
- API smoke tests: auth, onboarding, risk-sized paper position, drawings auth scoping, chat sweep highlight and
  price scenario, 16-agent review in 15 ms.
- Browser: production build screenshots of the terminal, signal detail, radar, research and backtest pages,
  with no console errors.

## Not yet automated
Frontend unit/E2E tests (the Playwright screenshot script lives outside the repo), WebSocket reconnection
under fault injection, load tests, and notification delivery to real Telegram/SMTP endpoints.
