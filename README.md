# ConfluenceAI — Crypto Market Intelligence Platform

An explainable, continuously-running crypto market research and decision-support system. It watches a
dynamically ranked universe of about 20 liquid USDT markets on Binance, 24/7. It computes technical, Smart
Money Concepts, Elliott Wave, derivatives and order-flow analysis on closed candles, and runs a forming →
pre-signal → confirmed signal lifecycle with validation gates and a counter-argument review. Every signal is
tracked to its outcome, with immutable evidence. The interactive chart lets you inspect, edit and save AI
annotations without ever overwriting the original AI evidence.

> **Decision support, not financial advice.** The platform never places orders. All performance shown is
> simulated (backtests) or hypothetical (paper / forward tests).

## Quick start (local, no Docker)

Requirements: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
# backend (SQLite by default, data in backend/data/)
cd backend
uv sync
uv run uvicorn app.main:app --port 8100        # starts API + 24/7 engine (backfill takes ~20 s)

# frontend
cd ../frontend
npm install
echo 'NEXT_PUBLIC_API_URL=http://localhost:8100' > .env.local
npm run dev -- -p 3100                          # open http://localhost:3100
```

The first registered account becomes the admin.

## Docker (Postgres/TimescaleDB)

```bash
cp .env.example .env    # set POSTGRES_PASSWORD and AIT_JWT_SECRET
docker compose up -d --build
```

## Useful commands

| Task | Command |
|---|---|
| Run all backend tests | `cd backend && uv run pytest -q` |
| Run DB tests on Postgres | `AIT_TEST_DATABASE_URL=postgresql+asyncpg://… uv run pytest -q tests/test_signal_lifecycle.py tests/test_paper.py` |
| Train / evaluate an ML model | `uv run python -m app.ml.train --tf 1h --horizon 24 --days 540` |
| Lint | `uv run ruff check app` · `cd frontend && npm run lint` |

## Documentation

| Document | Contents |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | What works, verified results, known gaps, next steps (**start here when resuming**) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data flow, module map, database schema |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Research knowledge base with evidence grades |
| [docs/TRADING_CONCEPTS.md](docs/TRADING_CONCEPTS.md) | Mathematical definitions of every detection |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Provider comparison, limits, licensing, failover |
| [docs/ML_RESEARCH.md](docs/ML_RESEARCH.md) | ML research plan and acceptance policy |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | Strategy specs, execution model, backtest results |
| [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md) | REST + WebSocket API |
| [docs/SECURITY.md](docs/SECURITY.md) · [docs/TESTING.md](docs/TESTING.md) · [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md) · [docs/ROADMAP.md](docs/ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) | |

Charts use TradingView Lightweight Charts™ (Apache-2.0, attribution logo shown on the chart).
# trading_platform
