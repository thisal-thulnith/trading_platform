# The final signal — how every part of the platform becomes one decision

Code: `backend/app/signal/fusion.py` (the fusion), `backend/app/signal/evidence.py` (the weights),
`GET /api/agent/signal?symbol=`, `GET /api/agent/evidence`. Used by Today's 5 (`app/agents/daily.py`).

## 1. Components (each scored −1 bearish … +1 bullish)
| Component | Source | Score |
|---|---|---|
| Momentum | quant brain: trend score among ~120 coins | (score − 50) / 50 |
| Carry | funding per year | −tanh(funding / 60%) — crowded longs crash more often |
| Regime | BTC trend | bull +0.6, bear −0.6 |
| Order flow | taker CVD over 24 candles | tanh(CVD % / 5) |
| Fundamentals | FDV / market cap | −0.5 at ≥ 2x, −0.2 at ≥ 1.5x |
| Chart setup | engine setup and stage | ±1 confirmed, ±0.6 pre-signal, ±0.3 forming |
| AI desk | the desk's decision and confidence | ± confidence |
| Quant model | the quant lab's promoted model | rank scaled to ±1 |

## 2. Weights — earned, not assumed
`weight = prior × (1 − n/(n+52)) + strength × n/(n+52)`, `strength = clip((t − 1)/2, 0, 1)`.
The prior comes from published research (momentum 0.30, carry 0.25, regime 0.15, order flow 0.05, fundamentals 0.05;
chart setups and the AI desk 0). It shrinks toward what this platform measured as the sample grows; an edge needs
t > 1 to start counting. A failing edge loses weight but its sign is never flipped. Models (selector, ML meta-label,
AI forecast, quant model) count only after passing their acceptance test.

On 2026-11-11, after the quant lab ran on a survivorship-free panel (591 coins, 199 delisted, 2019–2026):
- momentum measured *reversed* (28-day rank IC −0.043, t −4.3): weight ~0;
- funding level does not rank coins (IC +0.004, t 0.6): carry weight ~0;
- the lab's factor composite (low volatility, BTC correlation, liquidity, distance from highs…) ranks coins out of sample
  (rank IC +0.114, t 12.6, every year since 2022) — but as a median effect: its own portfolio lost after costs in
  cross-validation and the best model failed the deflated-Sharpe test, so its weight is capped at 0.3;
- chart setups (+0.021R, t 0.6) and the AI desk (no graded record) earn 0; regime, order flow and fundamentals keep their
  small literature priors until measured.
The composite is relative: a top-ranked coin is expected to beat the others, so shorting it is on the wrong side of the
evidence and is vetoed even in a falling market.

## 3. Fusion and decision
`F = Σ wᵢsᵢ / Σ wᵢ` over components with data; also coverage (share of the evidence weight with data) and agreement
(share of the weight on F's side). For a setup with a side:
- **take** — the readiness gate says ready, F leans the setup's way by ≥ 0.05 and ≥ 55% of the weight agrees;
- **wait** — not opposed, but the trigger, the lean or the agreement is missing;
- **veto** — F points the other way by ≥ 0.10: the setup is dropped from Today's 5.

A waiting pick enters the paper book only when the trigger fires *and* the fused signal then says take. The page shows
each pick's signal strength, agreement and the full breakdown (score, weight and reason per component).

## 4. What this does and does not promise
The fusion uses only evidence and is explicit about it. Today most of the weight rests on literature priors because the
platform's own measurements are short or negative; as the quant lab, the research loop and the sandbox add measured
results, the weights move automatically. It does not make the signal "accurate"; it makes it as accurate as the evidence
allows, and shows why.
