# Trading Concepts: Implementation Definitions

Last verified: 2026-11-01. Evidence grades (A to D) follow [RESEARCH.md §0](RESEARCH.md#0-evidence-grading-scale).

**Conventions used throughout**
- Bars are indexed `i = 0..n-1`, oldest first, and use only **closed** bars. `o, h, l, c, v` are open, high, low, close, volume.
- `ATR_k[i]` is the Wilder ATR over k bars (default k = 14) computed through bar i. It is causal.
- Every detected object has `formed_at` (the bar where its geometry lives) and `confirmed_at` (the first bar at which it is knowable). **Signals, backtests, and ML features must key on `confirmed_at`.**
- All tolerances are fractions of ATR so they work across assets and timeframes. Every constant below is a user-tunable parameter. The listed defaults are conventions, not optimized values.

ICT/SMC terms have no single authoritative specification, and vendors differ. The definitions below are *our* deterministic operationalization. Treat them as testable hypotheses.

---

## 1. Market structure

### 1.1 Swing pivots (fractals) — Grade C (as a signal); deterministic tool
Parameters `L` (left bars) and `R` (right bars). Default `L = R = 2` (Williams fractal), or `L = R = 5` for "major" swings.

- **Swing high at i** if `h[i] > max(h[i-L .. i-1])` and `h[i] >= max(h[i+1 .. i+R])`.
- **Swing low at i** if `l[i] < min(l[i-L .. i-1])` and `l[i] <= min(l[i+1 .. i+R])`.
- Ties: use strict `>` on the left and `>=` on the right so a flat top yields one pivot (the first bar).
- `confirmed_at = i + R`. **Lag of R bars. Never place a pivot on bar i in real time.**
- Optional alternation filter: if two consecutive swing highs occur without an intervening swing low, keep the higher (for lows, the lower). This gives a clean H-L-H-L sequence.
- Alternative: **ZigZag** with threshold `θ` (e.g., `θ = 2·ATR` or x%). A new pivot is confirmed when price reverses θ from the running extreme. `confirmed_at` = the bar where the reversal threshold is crossed.

### 1.2 HH / HL / LH / LL — Grade C
Compare each confirmed swing with the previous swing of the same type:
- Swing high `H_k`: **HH** if `H_k > H_{k-1} + ε`, **LH** if `H_k < H_{k-1} − ε`, otherwise **EQH** (equal high, §2.6).
- Swing low `L_k`: **HL** if `L_k > L_{k-1} + ε`, **LL** if `L_k < L_{k-1} − ε`, otherwise **EQL**.
- `ε = 0.1·ATR` by default.
- **Trend state:** bullish if the latest high is HH and the latest low is HL; bearish if LH and LL; otherwise range/transition.

### 1.3 Break of Structure (BOS) — Grade C
Track `state ∈ {bull, bear}` and the reference levels: `last_swing_high` and `last_swing_low` (confirmed).
- **Bullish BOS:** `state == bull` and a bar closes above `last_swing_high` (close-based by default; a wick-based variant is a parameter). This is trend continuation.
- **Bearish BOS:** `state == bear` and close < `last_swing_low`.
- `confirmed_at` = the breaking bar. Each swing level can be broken only once.
- Note the subtle lag: the swing being broken must itself already be confirmed (at its `i+R`) before the break bar.

### 1.4 Change of Character (CHoCH) — Grade C
The first break **against** the current state:
- `state == bear` and close > `last_swing_high` (specifically the most recent lower high) gives a **bullish CHoCH**; set `state = bull`.
- `state == bull` and close < `last_swing_low` (the most recent higher low) gives a **bearish CHoCH**; set `state = bear`.
- Initialization: `state` is undefined until the first break. The first break is labeled BOS in its direction.

### 1.5 Market Structure Shift (MSS) — Grade C/D
ICT usage: a CHoCH that occurs **after a liquidity sweep** (§2.8) and **with displacement** (§2.10). Operationalization:
`MSS = CHoCH at bar j` AND `∃ sweep in the opposite direction within the last W bars (default W = 20)` AND `displacement candle within [sweep, j]`.

### 1.6 Internal vs. external structure — Grade C
Run two pivot detectors:
- **External (swing) structure:** large `L = R` (e.g., 5 to 10) or ZigZag θ = 3·ATR. These define the dealing range.
- **Internal structure:** small `L = R` (e.g., 1 to 2), computed *inside* the current external range.
- BOS/CHoCH are computed separately per layer. Internal CHoCH against the external trend is labeled "pullback," not reversal, until external structure breaks.

### 1.7 Dealing range, premium / discount / equilibrium — Grade D/C
Given the active external range `[RL, RH]` (last confirmed external swing low and high):
- `EQ = (RH + RL) / 2`.
- **Premium:** price > EQ. **Discount:** price < EQ.
- Normalized position: `p = (price − RL)/(RH − RL) ∈ [0,1]`.

### 1.8 Optimal Trade Entry (OTE) — Grade D
For a bullish leg from swing low `A` to swing high `B`, the retracement level `r` maps to the price `B − r·(B − A)`. **OTE zone** = `r ∈ [0.62, 0.79]`, with 0.705 as the midpoint (commonly cited). Fibonacci levels in general: 0.236, 0.382, 0.5, 0.618, 0.786. Bearish legs are mirrored. No credible evidence supports special significance of these ratios.

---

## 2. Price-action objects (SMC/ICT)

### 2.1 Displacement — Grade C
Bar i is a **displacement candle** if:
`|c[i] − o[i]| ≥ k_body · ATR_14[i−1]` (default `k_body = 1.5`) AND `|c[i] − o[i]| / (h[i] − l[i]) ≥ 0.6` (body-to-range ratio, i.e., small wicks).
Multi-bar variant: the cumulative directional move over ≤ 3 same-direction bars ≥ `2·ATR`. Use `ATR[i−1]` so the bar does not inflate its own threshold.

### 2.2 Fair Value Gap (FVG) — Grade C
Three-candle pattern on bars `i−2, i−1, i`:
- **Bullish FVG:** `l[i] > h[i−2]`. Zone = `[h[i−2], l[i]]`.
- **Bearish FVG:** `h[i] < l[i−2]`. Zone = `[h[i], l[i−2]]`.
- `confirmed_at = i`. Min size filter: `gap ≥ 0.1·ATR` (parameter). Optionally require bar `i−1` to be displacement.
- **Fill/mitigation tracking:** partial fill when price trades into the zone; full fill when a close beyond the far edge occurs (or a wick beyond it, under the wick variant). Store `fill_pct = penetration / gap`. Consequent encroachment (CE) is the zone midpoint.

### 2.3 Inverse FVG (IFVG) — Grade D
An FVG that is fully invalidated by a **close** through its far edge flips polarity. A bullish FVG closed below becomes a bearish IFVG (expected resistance), and vice versa. `confirmed_at` = the invalidating close bar.

### 2.4 Liquidity void — Grade D
A run of consecutive same-direction FVGs or displacement bars (≥ 2) whose union forms one contiguous low-volume price span. Implementation: merge overlapping or adjacent FVG zones created within `m` bars (default 3) into a void.

### 2.5 Order block (OB) — Grade D (as usually stated) / C (as defined here)
**Bullish OB:** the **last bearish candle** (`c < o`) before a bullish displacement leg that produces a **bullish BOS or CHoCH**.
Algorithm, on each bullish BOS/CHoCH at bar j:
1. Let `s` be the bar of the swing low that started the leg (lowest low between the broken swing high's bar and j).
2. Search backward from the first displacement bar in `[s, j]` (inclusive of s) for the most recent bar with `c < o`. Call it bar b.
3. Zone = `[l[b], h[b]]` (variant: `[l[b], o[b]]` "body OB").
4. Require at least one displacement bar (§2.1) in `(b, j]`. Optional: require an FVG in the leg.
Bearish OB mirrors this (last bullish candle before a bearish displacement that breaks structure). `confirmed_at = j` (the BOS bar), **not b**.
State: `active` → `mitigated` (first trade back into the zone after `confirmed_at`) → `invalidated` (close beyond the far edge).

### 2.6 Breaker block — Grade D
An OB that is **invalidated** (close through its far edge), where that invalidation produces a structure break in the opposite direction. The zone flips polarity. A failed bullish OB becomes a bearish breaker. ICT variants also require that the OB's leg swept liquidity first (a bearish breaker forms after a higher high sweeps an old high, then price breaks below the last low).

### 2.7 Mitigation block — Grade D
Like a breaker, but the failed leg **did not** make a new extreme (no sweep). For example, in a bearish context, a lower high forms, then price breaks the prior low. The last bullish candle before that down-leg is the bearish mitigation block. Implementation: same as breaker, but require that the preceding swing is LH (bearish) or HL (bullish) rather than HH/LL.

### 2.8 Equal highs / lows; buy-side and sell-side liquidity — Grade C (concept B- per Osler 2003)
- **Equal highs (EQH):** two or more confirmed swing highs within `|H_a − H_b| ≤ tol`, `tol = 0.1·ATR` (default; 0.05 to 0.2 typical), separated by ≥ 3 bars, with no close above `max(H)` in between. The level is `max(H_a, H_b)`.
- **Equal lows (EQL):** mirrored.
- **Buy-side liquidity (BSL):** resting buy-stops presumed above swing highs, EQH, PDH/PWH, and round numbers.
- **Sell-side liquidity (SSL):** presumed below swing lows, EQL, PDL/PWL.
- Each level stores its `strength` (touch count, age, timeframe).

### 2.9 Liquidity sweep (stop run) — Grade C
For a BSL level `X`, a **sweep** occurs at bar i if `h[i] > X + δ` and `c[i] < X` (wick beyond, close back inside). `δ ≥ 0` (default 0 or 0.05·ATR).
- Multi-bar variant: price trades above X and closes back below X within `m ≤ 3` bars.
- SSL sweep is mirrored (`l[i] < X − δ` and `c[i] > X`).
- A close beyond X without a return is a **break** (run), not a sweep.

### 2.10 Inducement — Grade D
A minor (internal) swing point forming within a pullback, ahead of a higher-timeframe POI (OB/FVG), whose sweep precedes the reaction from the POI. Implementation: an internal-structure swing low (bullish context) located between current price and an active bullish OB/FVG, swept (§2.9) before price taps the POI. Highly discretionary.

---

## 3. Time-based levels

### 3.1 Sessions and killzones — Grade C (intraday volatility seasonality itself is well documented, B)
Two different conventions exist. **Store all timestamps in UTC; compute sessions with a timezone-aware library (DST matters).**

**(a) FX market sessions (broad).** Typical definitions in local time are Tokyo 09:00–18:00 JST, London 08:00–17:00 London time, and New York 08:00–17:00 New York time. In UTC:

| Session | UTC, northern summer (BST/EDT) | UTC, northern winter (GMT/EST) |
|---|---|---|
| Asia (Tokyo, no DST) | 00:00–09:00 (often simplified to 00:00–08:00) | 00:00–09:00 |
| London | 07:00–16:00 | 08:00–17:00 |
| New York | 12:00–21:00 | 13:00–22:00 |

The commonly quoted "Asia 00:00–08:00, London 07:00–16:00, NY 12:00–21:00 UTC" is the **summer-time** mapping. Implement in local time and convert, rather than hard-coding UTC. Overlap London/NY = 12:00–16:00 UTC in summer.

**(b) ICT killzones (narrow, defined in New York time).** Sources vary by about an hour. The most common:
- Asian KZ: 20:00–00:00 NY time
- London KZ: 02:00–05:00 NY time
- New York KZ: 07:00–10:00 NY time (some use 08:00–11:00)
- London close KZ: 10:00–12:00 NY time

**Crypto caveat:** crypto trades 24/7. These sessions matter only through the participation of TradFi-hours traders. Volatility and volume peaks around the US equity open (13:30/14:30 UTC) and funding times (00/08/16 UTC on Binance) are measurable. Verify empirically per asset.

### 3.2 PDH / PDL / PWH / PWL / PMH / PML — Grade C
- Previous day high/low: max/min of `h`/`l` over the prior **UTC** calendar day (00:00–24:00 UTC, the Binance 1d kline convention). Variant: the NY-midnight day.
- Previous week: prior ISO week, Monday 00:00 UTC to Monday 00:00 UTC (Binance 1w klines open Monday 00:00 UTC).
- Previous month: prior calendar month, UTC.
- Also: daily open, weekly open, and the midnight NY open (ICT).
- `confirmed_at` = the period's close. These levels act as BSL/SSL for sweep detection.

---

## 4. Elliott Wave — Grade D

Source: Frost & Prechter, *Elliott Wave Principle* (Wiley; 10th ed. 2005). Input is an alternating pivot sequence `P0..P5` (impulse) from ZigZag. A labeling is **valid** only if all hard rules hold.

### 4.1 Impulse (motive) wave, bullish (mirror for bearish), waves W1 = P0→P1, W2 = P1→P2, ...
**Hard rules**
1. W2 never retraces more than 100% of W1: `P2 > P0`.
2. W3 is never the shortest of W1, W3, W5 (price length): `len3 > min(len1, len5)`, where `len1 = P1−P0`, `len3 = P3−P2`, `len5 = P5−P4`.
3. W4 does not enter W1's price territory: `P4 > P1` (**except** in diagonal triangles).
4. Also implied: W3 must travel beyond the end of W1 (`P3 > P1`), and W2 and W4 are corrective (opposite direction).

**Guidelines (soft; use for scoring, not validation)**
- W2 retraces 50% to 61.8% of W1. W4 retraces 23.6% to 38.2% of W3.
- W3 ≈ 1.618 × W1 (often the extended wave). W5 ≈ W1, or 0.618 × (W1+W3).
- Alternation: if W2 is sharp (zigzag), W4 tends to be sideways (flat/triangle), and vice versa.
- Channeling: W5 often ends near a parallel through P3, drawn from the P2–P4 line.
- Volume usually peaks in W3 (in equities).

**Diagonals (leading or ending)**: 5 waves in a wedge, with W4 overlapping W1 allowed. Ending diagonal subwaves are 3-3-3-3-3. Hard rules 1 and 2 still apply.

### 4.2 Corrective patterns
- **Zigzag (5-3-5), A-B-C:** B retraces < 100% of A (typically 38 to 79%). C goes beyond the end of A. C ≈ A (or 0.618/1.618 × A).
- **Flat (3-3-5):** B retraces ≥ ~90% of A (regular ≈ 100%; expanded: B > 100% of A, typically 1.236 to 1.382 × A, and C ends beyond A's end; running: B > 100% but C fails to reach A's end).
- **Triangle (3-3-3-3-3), A-B-C-D-E:** contracting is most common; each leg is shorter than the previous (`|E| < |D| < |C| < |B| < |A|`), with trendlines A–C and B–D converging. Occurs in the W4 or B position, never as W2 per the standard rules.
- Combinations (double/triple three, W-X-Y): many degrees of freedom, so we do not auto-label them.

**Implementation note:** enumerate candidate labelings over the last N ZigZag pivots, filter by hard rules, score by guideline fit (e.g., a sum of log-distances to ideal ratios), and show the top-k with an explicit "speculative / non-unique" banner. Re-labeling is expected as new pivots confirm.

---

## 5. Derivatives formulas (Binance USDⓈ-M perpetuals)

### 5.1 Premium index and funding rate — mechanics (A as a definition); signal grade B/B-
Source: Binance FAQ "Introduction to Binance Futures Funding Rates".

- **Impact Margin Notional:** `IMN = 200 USDT / initial_margin_rate_at_max_leverage` (e.g., 20x → IMR 5% → IMN 4,000 USDT). Impact bid/ask = the average fill price for IMN notional on each side of the book.
- **Premium index** (sampled every few seconds, every 5 s per Binance docs; verify):
  `P = [max(0, ImpactBid − Index) − max(0, Index − ImpactAsk)] / Index`
- **Average premium** `P̄`: time-weighted average over the funding interval, weighted increasingly toward recent samples (Binance FAQ; exact weighting unverified here).
- **Interest rate:** `I = 0.03%/day`, i.e., 0.01% per 8 h (0% for some contracts, such as ETHBTC). Binance may adjust it.
- **Funding rate:**
  `F = [P̄ + clamp(I − P̄, −0.05%, +0.05%)] / (8 / N)`, where N = interval hours (8, 4, or 1).
  Consequence: when `P̄ ∈ [I − 0.05%, I + 0.05%] = [−0.04%, +0.06%]` per 8 h, `F = I = 0.01%`.
- **Cap/floor:** for major symbols (BTC, ETH, BNB, SOL, etc.) `F_capped = clamp(F, −0.75·MMR, +0.75·MMR)`, where MMR is the maintenance margin rate of the first bracket. Others are capped at ±2%. Binance can widen these in extreme conditions.
- **Interval:** 00:00/08:00/16:00 UTC by default. Since 2025-05-02, a contract can switch to a 1 h interval when settlement hits the cap or floor; since 2026-01-02, it can revert from 1 h to 4 h after 16 consecutive cycles with `|F| ≤ 0.025%`. **Read `fundingIntervalHours` from `GET /fapi/v1/fundingInfo` rather than assuming 8 h.**
- **Payment:** `funding_payment = position_notional (at mark price) × F`. Positive F means longs pay shorts.
- **Live predicted rate:** `GET /fapi/v1/premiumIndex` returns `lastFundingRate` (the current predicted rate), `markPrice`, `indexPrice`, and `nextFundingTime`. WS: `<symbol>@markPrice` (on the `/market` route).
- **Normalization for comparison:** `F_annualized ≈ F × (24/N) × 365`. Always normalize per hour across exchanges with different intervals.

### 5.2 Basis — B+
- **Perp basis (spot-relative):** `basis = (Mark or Last_perp − Index or Spot) / Spot`. Prefer mark vs. index to avoid last-trade noise.
- **Dated futures annualized basis:** `basis_ann = (F_t/S_t − 1) × 365 / days_to_expiry` (simple), or `ln(F/S) × 365/τ` (continuous).
- Binance `/futures/data/basis` provides history, but **only the latest 30 days**.

### 5.3 Open interest change — C (as signal)
- `ΔOI_% = (OI_t − OI_{t−k}) / OI_{t−k}`. Use OI in **notional** (`sumOpenInterestValue`) when comparing across price moves, and in contracts (`sumOpenInterest`) to see position count changes.
- Quadrant heuristic (practitioner, grade C): price↑ OI↑ = new longs; price↑ OI↓ = short covering; price↓ OI↑ = new shorts; price↓ OI↓ = long liquidation/closing.
- **Data limit:** `GET /futures/data/openInterestHist` returns only the **latest 1 month** (verified: 31 daily rows). Snapshot `GET /fapi/v1/openInterest` ourselves every 1 to 5 min to build history. Bybit v5 OI history goes back to 2020 (verified), so it serves as a backfill source.

### 5.4 Maintenance margin
`MM = notional × MMR − cum` (cum = "maintenance amount"), where `notional = |Q| × MarkPrice`. MMR and cum come from the leverage bracket that contains the position notional: `GET /fapi/v1/leverageBracket` (signed endpoint, fields `maintMarginRatio`, `cum`). The cum amount makes MM continuous across bracket boundaries.

### 5.5 Liquidation price — A (as exchange definition)
Source: Binance FAQ "How to Calculate Liquidation Price of USDⓈ-M Futures Contracts". The formula is published as an image; we reproduced it below and **verified it numerically against both worked examples on that page** (BTCUSDT 26,316.89 and ETHUSDT 1,153.26 match to the cent).

General (one-way + hedge mode, cross):
```
LP = ( WB − TMM1 + UPNL1 + cumB + cumL + cumS
       − Side1BOTH·Position1BOTH·EP1BOTH − Position1LONG·EP1LONG + Position1SHORT·EP1SHORT )
     / ( Position1BOTH·MMR_B + Position1LONG·MMR_L + Position1SHORT·MMR_S
         − Side1BOTH·Position1BOTH − Position1LONG + Position1SHORT )
```
- WB = wallet balance (cross: crossWalletBalance; isolated: the position's isolatedWalletBalance, i.e., its isolated margin).
- TMM1 / UPNL1 = maintenance margin / unrealized PnL of all *other* positions (**0 in isolated mode**).
- cumB/L/S = maintenance amounts. Side1BOTH = +1 long, −1 short. Positions are absolute sizes; EP = entry price.

**Isolated, one-way mode (the common case):**
```
LP = (WB + cum − s·Q·EP) / (Q·MMR − s·Q)        s = +1 long, −1 short; Q = |size|
```
Long sanity form: `LP = (Q·EP − WB − cum) / (Q·(1 − MMR))`.

Caveats: (1) MMR and cum depend on the bracket at *liquidation* notional, so iterate: compute LP, recompute the bracket at `Q·LP`, and repeat until stable (usually 1 to 2 iterations). (2) Liquidation triggers on **mark price**, not last price. (3) Fees, funding accruals, and the liquidation clearance fee shift the real outcome. Present the result as an **estimate** and show distance-to-liquidation in % and in ATR multiples.

### 5.6 Mark price
Binance mark price = median(Price1, Price2, Price3), where Price1 = index × (1 + latest funding rate × time-until-funding/interval), Price2 = index + moving average of (basis), and Price3 = the last price (per Binance mark price FAQ; exact component definitions change, so treat them as unverified and consume `markPrice` from the API rather than recomputing it).

---

## References
- Binance funding rate FAQ: https://www.binance.com/en/support/faq/introduction-to-binance-futures-funding-rates-360033525031
- Binance liquidation price FAQ (USDⓈ-M): https://www.binance.com/en/support/faq/how-to-calculate-liquidation-price-of-usd%E2%93%A2-m-futures-contracts-b3c689c1f50a44cabb3a84e663b81d93
- Binance USDⓈ-M market data REST (premiumIndex, fundingInfo, openInterestHist): https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api
- Binance Open Interest Statistics (1-month limit): https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics
- Binance leverage brackets: https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Notional-and-Leverage-Brackets
- Frost & Prechter, *Elliott Wave Principle: Key to Market Behavior*, Wiley.
- Williams, B. (1995) *Trading Chaos* (fractal definition).
- Osler, C. (2003) Currency orders and exchange rate dynamics, JF: https://doi.org/10.1111/1540-6261.00594
- ICT killzone conventions (practitioner sources; vary): https://www.luxalgo.com/library/concept/killzones/ , https://www.tradingview.com/scripts/ictkillzone/
- LuxAlgo Smart Money Concepts (reference implementation, practitioner): https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-SMC-LuxAlgo/
- smartmoneyconcepts Python package (open-source reference): https://github.com/joshyattridge/smart-money-concepts
- Wilder (1978) *New Concepts in Technical Trading Systems* (ATR).
