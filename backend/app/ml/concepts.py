"""Human trading concepts as numbers, for every coin and day since 2017, so the same model that reads momentum and
volatility can also read market structure, liquidity and price action, and LEARN how much each concept is worth from
history instead of a person deciding it.

Concepts (daily candles, all causal: a value on day D uses candles dated <= D only):
- Market structure (Dow / SMC): swing highs and lows (3-bar fractals, confirmed 3 days later, never earlier), higher
  highs and higher lows vs lower highs and lower lows, break of structure (BOS) and change of character (CHoCH), days
  since the last break, distance to the last swing high and low in ATR, premium / discount inside the swing range.
- Liquidity (ICT): sweeps of the 20-day low or high that close back inside, equal highs / equal lows (resting stops),
  open fair value gaps (3-candle imbalances not yet traded through) and the distance to the nearest one, displacement
  (the largest 3-day move in ATR over 10 days).
- Wyckoff: the 30-day range width in ATR, springs and upthrusts (a break of the range that closes back inside), volume
  drying up.
- Value: distance to the 20- and 60-day VWAP in ATR.
- Price action: candle body and wicks, engulfing and inside bars.
- Trend: moving-average stack (10 > 20 > 50) and the 20-day average's slope; strength against BTC over 7 and 28 days.
- Round numbers (Osler, JF 2003: take-profit orders cluster at round numbers, stop-losses just beyond them): distance
  to the nearest round price in ATR.
- Confluence: how many of the classic long / short conditions agree today (a desk's checklist, as a number).

Each one is only a feature: the model decides from 2017-2026 data whether it predicts anything.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N_SWING = 3
CONCEPT_DOC = {
    "ms_struct": "structure: +1 higher high, +1 higher low, -1 lower high, -1 lower low (-2..+2)",
    "ms_bos_state": "+1 if the last break of structure was up (close above the last swing high), -1 if down",
    "ms_days_since_bos": "days since the last break of structure",
    "ms_choch_5": "change of character in the last 5 days: +1 bullish, -1 bearish, 0 none",
    "ms_dist_sh_atr": "distance from the close up to the last swing high, in ATR",
    "ms_dist_sl_atr": "distance from the last swing low up to the close, in ATR",
    "ms_pd_pos": "premium / discount: where the close sits between the last swing low (0) and high (1)",
    "liq_sweep_low_5": "sweeps of the 20-day low that closed back above it, last 5 days",
    "liq_sweep_high_5": "sweeps of the 20-day high that closed back below it, last 5 days",
    "liq_eq_highs_20": "days in 20 whose high sits within 0.25 ATR of the 20-day high (resting buy stops)",
    "liq_eq_lows_20": "days in 20 whose low sits within 0.25 ATR of the 20-day low (resting sell stops)",
    "fvg_bull_open": "bullish fair value gaps of the last 10 days not yet traded through",
    "fvg_bear_open": "bearish fair value gaps of the last 10 days not yet traded through",
    "fvg_bull_dist_atr": "distance from the close to the nearest open bullish gap, in ATR",
    "fvg_bear_dist_atr": "distance from the close to the nearest open bearish gap, in ATR",
    "disp_up_10": "largest 3-day rise in ATR over the last 10 days (displacement)",
    "disp_dn_10": "largest 3-day fall in ATR over the last 10 days",
    "wy_range_30_atr": "30-day high-low range in ATR (a tight range after a trend = possible accumulation)",
    "wy_spring_5": "springs in 5 days: a break of the 30-day low that closed back inside",
    "wy_upthrust_5": "upthrusts in 5 days: a break of the 30-day high that closed back inside",
    "wy_vol_dry": "log(10-day / 60-day average volume): volume drying up",
    "vwap_20_atr": "close minus the 20-day VWAP, in ATR",
    "vwap_60_atr": "close minus the 60-day VWAP, in ATR",
    "pa_body": "candle body / range (+ up, - down)",
    "pa_upper_wick": "upper wick / range",
    "pa_lower_wick": "lower wick / range",
    "pa_engulf": "+1 bullish engulfing, -1 bearish engulfing",
    "pa_inside": "1 for an inside bar",
    "tr_ma_stack": "+1 when the 10 > 20 > 50-day averages, -1 when 10 < 20 < 50",
    "tr_ma20_slope": "5-day change of the 20-day average",
    "rs_btc_7": "7-day return minus BTC's",
    "rs_btc_28": "28-day return minus BTC's",
    "round_dist_atr": "distance to the nearest round price (1, 2, 2.5, 5 x 10^k), in ATR",
    "conf_long": "classic long checklist: bullish structure, last BOS up, discount, recent low sweep, open bullish gap, "
                 "above the 20-day VWAP, bullish average stack (0-7)",
    "conf_short": "the mirror short checklist (0-7)",
}
CONCEPTS = list(CONCEPT_DOC)


def _ffill_prev(ev: pd.DataFrame) -> pd.DataFrame:
    """For an events frame (value on the day it happened, NaN otherwise): the event BEFORE the latest one, as of each day."""
    return ev.apply(lambda s: s.dropna().shift(1).reindex(s.index).ffill())


def _days_since(ev: pd.DataFrame) -> pd.DataFrame:
    n = len(ev)
    idx = np.where(ev.to_numpy(dtype=bool), np.arange(n)[:, None], np.nan)
    last = pd.DataFrame(idx, index=ev.index, columns=ev.columns).ffill()
    return np.arange(n)[:, None] - last


def _round_dist(c: pd.DataFrame) -> pd.DataFrame:
    p = c.to_numpy(dtype=np.float64)
    with np.errstate(all="ignore"):
        mag = 10.0 ** np.floor(np.log10(p))
        best = np.full(p.shape, np.inf)
        for m in (1.0, 2.0, 2.5, 5.0, 10.0):
            for scale in (0.1, 1.0):
                best = np.minimum(best, np.abs(p - m * scale * mag))
    return pd.DataFrame(np.where(np.isfinite(p), best, np.nan), index=c.index, columns=c.columns)


def concepts(w, atr: pd.DataFrame) -> dict[str, pd.DataFrame]:
    c, h, qv = w.close, w.high, w.quote_volume
    lo = w.low if w.low is not None else c
    o = w.open if w.open is not None else c.shift(1)
    f: dict[str, pd.DataFrame] = {}
    a = atr.where(atr > 0)

    # --- market structure: 3-bar fractals, confirmed N days after the swing (no look-ahead)
    win = 2 * N_SWING + 1
    sh_ev = h.shift(N_SWING).where(h.shift(N_SWING) == h.rolling(win, min_periods=win).max())
    sl_ev = lo.shift(N_SWING).where(lo.shift(N_SWING) == lo.rolling(win, min_periods=win).min())
    sh, sl = sh_ev.ffill(), sl_ev.ffill()
    psh, psl = _ffill_prev(sh_ev), _ffill_prev(sl_ev)
    f["ms_struct"] = np.sign(sh - psh) + np.sign(sl - psl)
    up_b = (c > sh) & (c.shift(1) <= sh.shift(1))
    dn_b = (c < sl) & (c.shift(1) >= sl.shift(1))
    state = pd.DataFrame(np.where(up_b, 1.0, np.where(dn_b, -1.0, np.nan)), index=c.index, columns=c.columns).ffill()
    f["ms_bos_state"] = state
    f["ms_days_since_bos"] = _days_since(up_b | dn_b).clip(upper=120)
    flip = (state != state.shift(1)) & state.shift(1).notna() & state.notna()
    f["ms_choch_5"] = state.where(_days_since(flip) <= 5, 0.0).where(state.notna())
    f["ms_dist_sh_atr"] = (sh - c) / a
    f["ms_dist_sl_atr"] = (c - sl) / a
    f["ms_pd_pos"] = ((c - sl) / (sh - sl)).where(sh > sl).clip(-0.5, 1.5)

    # --- liquidity
    lo20, hi20 = lo.rolling(20, min_periods=15).min().shift(1), h.rolling(20, min_periods=15).max().shift(1)
    f["liq_sweep_low_5"] = ((lo < lo20) & (c > lo20)).astype(float).rolling(5, min_periods=1).sum()
    f["liq_sweep_high_5"] = ((h > hi20) & (c < hi20)).astype(float).rolling(5, min_periods=1).sum()
    mx20, mn20 = h.rolling(20, min_periods=15).max(), lo.rolling(20, min_periods=15).min()
    eqh = sum(((mx20 - h.shift(k)) <= 0.25 * a).astype(float) for k in range(20))
    eql = sum(((lo.shift(k) - mn20) <= 0.25 * a).astype(float) for k in range(20))
    f["liq_eq_highs_20"], f["liq_eq_lows_20"] = eqh, eql

    # --- fair value gaps of the last 10 days still open today
    bull_n = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    bear_n = bull_n.copy()
    bull_d = pd.DataFrame(np.inf, index=c.index, columns=c.columns)
    bear_d = bull_d.copy()
    for k in range(10):
        top_b, bot_b = lo.shift(k), h.shift(k + 2)              # bullish gap made on day t-k: low > high two days before
        gap_b = top_b > bot_b
        top_s, bot_s = lo.shift(k + 2), h.shift(k)              # bearish gap: high < low two days before
        gap_s = bot_s < top_s
        if k == 0:
            open_b, open_s = gap_b, gap_s
        else:
            open_b = gap_b & (lo.rolling(k, min_periods=1).min() > bot_b)
            open_s = gap_s & (h.rolling(k, min_periods=1).max() < top_s)
        bull_n += open_b.astype(float)
        bear_n += open_s.astype(float)
        bull_d = bull_d.where(~open_b | (bull_d <= ((c - top_b) / a).abs()), ((c - top_b) / a).abs())
        bear_d = bear_d.where(~open_s | (bear_d <= ((bot_s - c) / a).abs()), ((bot_s - c) / a).abs())
    f["fvg_bull_open"], f["fvg_bear_open"] = bull_n, bear_n
    f["fvg_bull_dist_atr"] = bull_d.where(np.isfinite(bull_d))
    f["fvg_bear_dist_atr"] = bear_d.where(np.isfinite(bear_d))

    m3 = (c - c.shift(3)) / a.shift(3)
    f["disp_up_10"] = m3.rolling(10, min_periods=5).max()
    f["disp_dn_10"] = m3.rolling(10, min_periods=5).min()

    # --- Wyckoff
    lo30, hi30 = lo.rolling(30, min_periods=20).min().shift(1), h.rolling(30, min_periods=20).max().shift(1)
    f["wy_range_30_atr"] = (h.rolling(30, min_periods=20).max() - lo.rolling(30, min_periods=20).min()) / a
    f["wy_spring_5"] = ((lo < lo30) & (c > lo30)).astype(float).rolling(5, min_periods=1).sum()
    f["wy_upthrust_5"] = ((h > hi30) & (c < hi30)).astype(float).rolling(5, min_periods=1).sum()
    q = qv.where(qv > 0)
    f["wy_vol_dry"] = np.log(q.rolling(10, min_periods=7).mean() / q.rolling(60, min_periods=40).mean())

    # --- value
    tp = (h + lo + c) / 3
    for n in (20, 60):
        vw = (tp * q).rolling(n, min_periods=int(n * 0.7)).sum() / q.rolling(n, min_periods=int(n * 0.7)).sum()
        f[f"vwap_{n}_atr"] = (c - vw) / a

    # --- price action
    rng = (h - lo).where(h > lo)
    f["pa_body"] = (c - o) / rng
    f["pa_upper_wick"] = (h - np.maximum(o, c)) / rng
    f["pa_lower_wick"] = (np.minimum(o, c) - lo) / rng
    c1, o1 = c.shift(1), o.shift(1)
    bull_e = (c > o) & (c1 < o1) & (o <= c1) & (c >= o1)
    bear_e = (c < o) & (c1 > o1) & (o >= c1) & (c <= o1)
    f["pa_engulf"] = bull_e.astype(float) - bear_e.astype(float)
    f["pa_inside"] = ((h < h.shift(1)) & (lo > lo.shift(1))).astype(float)

    # --- trend and relative strength
    m10, m20, m50 = (c.rolling(n, min_periods=int(n * 0.7)).mean() for n in (10, 20, 50))
    f["tr_ma_stack"] = ((m10 > m20) & (m20 > m50)).astype(float) - ((m10 < m20) & (m20 < m50)).astype(float)
    f["tr_ma20_slope"] = m20 / m20.shift(5) - 1
    if "BTCUSDT" in c.columns:
        btc = c["BTCUSDT"]
        for n in (7, 28):
            f[f"rs_btc_{n}"] = (c / c.shift(n) - 1).sub(btc / btc.shift(n) - 1, axis=0)
    f["round_dist_atr"] = _round_dist(c) / a

    # --- a desk's checklist as a number
    vw20 = f["vwap_20_atr"]
    long_c = [f["ms_struct"] > 0, f["ms_bos_state"] > 0, f["ms_pd_pos"] < 0.5, f["liq_sweep_low_5"] > 0,
              f["fvg_bull_open"] > 0, vw20 > 0, f["tr_ma_stack"] > 0]
    short_c = [f["ms_struct"] < 0, f["ms_bos_state"] < 0, f["ms_pd_pos"] > 0.5, f["liq_sweep_high_5"] > 0,
               f["fvg_bear_open"] > 0, vw20 < 0, f["tr_ma_stack"] < 0]
    f["conf_long"] = sum(x.astype(float) for x in long_c)
    f["conf_short"] = sum(x.astype(float) for x in short_c)

    known = c.notna()
    return {k: v.replace([np.inf, -np.inf], np.nan).where(known).astype(np.float32) for k, v in f.items() if k in CONCEPT_DOC}
