# Forecast models — what the platform's own trained models predict, and how well

Code: `backend/app/ml/forecast.py` · tests: `backend/tests/test_forecast.py` · API: `GET /api/agent/forecast?symbol=`,
`GET /api/agent/forecast/report` · UI: "Trained models" on the Research desk (no AI key needed) · agents: `model_forecast`.

## Data and method
- 284,517 coin-days from the survivorship-free daily panel (591 Binance USDT coins since 2019, delisted included),
  inside the tradeable universe (90+ days of history, $5M+ average daily volume), 48 features from data dated on or
  before the day: the 18 lab factors and their ranks in the day's universe, ATR, short and long volatility, the last
  7 days' range, distance from the 50/200-day averages, and the market (BTC trend and volatility, breadth).
- Walk-forward by year: each test year (2022-2026) is predicted by models trained only on earlier years, ending 15 days
  before it. Four candidates per target: a volatility-only formula (the baseline), a regularised linear model,
  LightGBM, and a blend. The candidate is **chosen on 2022-2024** and **reported on 2025-2026**, which played no part in
  the choice. Probabilities are isotonic-calibrated on the pooled out-of-sample predictions.

## Training data (0.38)
- **Full history since August 2017** (`app/ml/history.py`, `panel_full.csv.gz`): the lab panel (2019+) plus the 2017-18
  monthly archive files — the 21 USDT pairs of that time and 154 ALT/BTC pairs converted to USD with the day's BTC
  close (real USDT days win). 850,718 coin-days, 733 coins (delisted included), 54,026 days before 2019, so the 2018
  crash and bear market are learned too. The lab panel itself is unchanged (it feeds the lab, simulator and books).
- Walk-forward test years 2020-2026; candidates chosen on **2020-2024** (five years instead of three), reported on
  unseen 2025-2026.

## Results on 2025-2026 (unseen)
| Tier | Model | Chosen | Score | Status |
|---|---|---|---|---|
| core | Up in 7 days | blend | AUC 0.579 | accepted |
| core | Beats the market | blend | AUC 0.604 | accepted |
| core | Expected 7-day range | linear_patterns | R² 0.27 | accepted |
| core | Drop risk | blend | AUC 0.650 | accepted |
| core | Long hits target | blend | AUC 0.671 | accepted |
| core | Short hits target | volatility_only | AUC 0.652 | accepted |
| wide | Up in 7 days | blend | AUC 0.547 | weak |
| wide | Beats the market | blend | AUC 0.578 | accepted |
| wide | Expected 7-day range | blend | R² 0.21 | accepted |
| wide | Drop risk | volatility_only | AUC 0.660 | accepted |
| wide | Long hits target | blend | AUC 0.647 | accepted |
| wide | Short hits target | volatility_only | AUC 0.655 | accepted |

Core = coins with $5M+ a day (more accurate); wide = $1M+ (thinner coins, labelled less accurate on the page).

## Chart patterns (`app/ml/patterns.py`, `docs` here)
Every coin's daily chart since 2017 as a scale-free 30-day shape (close path standardised, 10 days of relative volume,
trend strength): 606,386 charts. Analogs = the 50 most similar past charts across all coins; the library = 40
learned patterns (k-means), named from their average shape. Walk-forward: each test year uses only charts from before
it. Scores on 2020-2026 (unseen at each step):

| Outcome | Analogs AUC | Pattern AUC | Analogs top / bottom tenth vs usual |
|---|---|---|---|
| direction_7d | 0.513 | 0.510 | 47% / 44% vs 45% |
| long_tp_first | 0.563 | 0.555 | 28% / 16% vs 21% |
| short_tp_first | 0.538 | 0.539 | 17% / 12% vs 15% |
| drop_risk_7d | 0.557 | 0.535 | 25% / 15% vs 20% |

Chart shape alone carries a small, real signal for target-first and drop risk, almost none for direction. Added to the
forecast models as features (out of sample by year) they changed the unseen-year scores by at most 0.004 — shapes mostly
repeat what volatility and trend already say — so they are shown as their own analysis (the desk's "Learned chart
patterns" card and the agents' `chart_patterns` tool) rather than claimed as an accuracy gain.

## What this means
- Real but modest edges: AUC 0.58-0.67 sorts better from worse situations clearly better than chance; it is not a high
  win rate on any single trade. Volatility does most of the work for drop risk and shorts, and the page says so.
- Accuracy is never shown as "% right" on rare outcomes; every forecast is shown against its usual rate.

## Upkeep
Daily: the lab update adds a day, `history.refresh()` rebuilds the full panel, patterns and forecasts are re-scored.
Monthly: patterns, then both forecast tiers, are retrained. Manual: `uv run python -m app.ml.history` (downloads),
`uv run python -m app.ml.patterns train`, `uv run python -m app.ml.forecast train`.
