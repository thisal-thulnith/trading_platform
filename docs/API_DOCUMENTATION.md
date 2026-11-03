# API

Base URL: `http://localhost:8100`. Interactive OpenAPI docs are served at `/docs` (Swagger) and `/redoc`.
Auth: `Authorization: Bearer <JWT>` from `/api/auth/login` or `/api/auth/register`. Endpoints marked 🔒 require it.

## Market & analysis (public, read-only)
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness |
| GET | `/api/system/health` | Providers, streams, freshness, data quality, task failures |
| GET | `/api/markets` | Universe with price, 24h change, volume, funding, radar summary |
| GET | `/api/universe/history` | Universe ranking snapshots |
| GET | `/api/candles/{symbol}?tf=&limit=&end=` | OHLCV (closed candles plus the forming one). Any of the 12 timeframes; backfills on demand |
| GET | `/api/indicators` | Indicator registry (params, outputs, panes, levels) |
| GET | `/api/indicators/{id}/{symbol}?tf=&params=k=v,k2=v2` | Indicator series |
| GET | `/api/volume_profile/{symbol}?tf=&bars=&bins=` | Volume profile with POC/VAH/VAL |
| GET | `/api/analysis/{symbol}?tf=&fresh=` | Latest deterministic snapshot (cached ≤ 1 bar) |
| POST | `/api/analysis/{symbol}/run?tf=` | Create a new immutable, versioned snapshot |
| GET | `/api/analysis/{symbol}/history?tf=` | Snapshot versions |
| GET | `/api/snapshots/{id}` · `/api/snapshots/{a}/compare/{b}` | Snapshot · diff of two versions |
| GET | `/api/replay/{symbol}?tf=&at=<unix s>` | Analysis using only candles closed before `at` |
| GET | `/api/radar?tf=` | Opportunity radar |
| GET | `/api/signals?state=&symbol=&active=&limit=` · `/api/signals/{id}` | Signals and their full event timeline |
| GET | `/api/performance` | Forward-test statistics (by strategy/regime/symbol/timeframe, quality calibration) |
| GET | `/api/strategies` | Strategy specs, live stats, research-gate status |
| GET | `/api/models` | Model registry with walk-forward folds, criteria, reliability |
| GET | `/api/news?coin=&impact=` | News intelligence and Fear & Greed |
| GET | `/api/orderbook/{symbol}` · `/api/liquidations/{symbol}` | Latest book snapshot · recent liquidation prints |
| GET | `/api/methodology` | Performance methodology |
| GET | `/api/agents/{symbol}?tf=` | 15-agent structured review (portfolio-aware if authenticated) |
| POST | `/api/chat` | Analyst chat `{message, symbol, timeframe, history}` → `{text, actions[], mode}`. Rate-limited |

## User 🔒
| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` · `/api/auth/login` | `{email, password}` → `{token, user}` (rate-limited 10/min/IP) |
| GET | `/api/auth/me` | Current user |
| GET/POST/PATCH/DELETE | `/api/drawings` | User drawings. `parent_id` = the AI annotation being overridden |
| GET/PUT | `/api/layouts` · `/api/watchlists` | Saved chart layouts / watchlists |
| POST | `/api/portfolio/onboarding` | Capital and risk profile (`reset: true` resets the account) |
| GET | `/api/portfolio` | Paper portfolio summary and metrics |
| POST | `/api/portfolio/positions` · `/api/portfolio/positions/{id}/close` | Open (risk-sized) / close a paper position |
| POST | `/api/portfolio/sizing` | Transparent position-size calculation (public; uses your profile when logged in) |
| POST | `/api/signals/{id}/close` | Admin: manually close a platform signal |
| GET | `/api/notifications` | Broadcast and user notifications |
| POST/GET | `/api/backtests` · `/api/backtests/{id}` | Run (background, max 2 concurrent) / fetch backtests |

## WebSocket `/ws`
Send `{"subscribe": ["BTCUSDT:1h", "ETHUSDT:5m"]}` to receive `candle` events for those keys. `signal`,
`notification`, `scan` and `liquidation` events are always delivered. Every message has the shape `{topic, data}`.

## Chat chart actions (validated server-side)
`highlight` / `hide` (annotation must exist in a snapshot fetched in the same conversation) · `draw` (`hline`,
`trendline`, `position`; prices must be within 0.3×–3× of the current price; scenario levels must be
consistent) · `timeframe`.
