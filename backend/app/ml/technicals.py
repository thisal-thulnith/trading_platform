"""Technical-indicator features for the forecast models, CTREND-style (Fieberg, Liedtke, Poddig, Walker & Zaremba,
"A Trend Factor for the Cross-Section of Cryptocurrency Returns", JFQA 2025): many classic chart indicators over several
windows, each also ranked across the day's coins, so the model can learn which ones matter and in which mix, instead of
a human choosing one rule.

Every value on day D uses candles dated <= D only. Nothing here looks ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_WINDOWS = (3, 5, 10, 20, 100)       # 50 and 200 are already features (dist_ma50 / dist_ma200)
VOL_WINDOWS = (5, 20, 50)
STOCH_WINDOWS = (14, 30)

TECH_DOC = {
    **{f"p_ma_{n}": f"close / {n}-day average - 1" for n in MA_WINDOWS},
    **{f"v_ma_{n}": f"log(today's quote volume / its {n}-day average)" for n in VOL_WINDOWS},
    **{f"stoch_{n}": f"where the close sits in the {n}-day high-low range (0 = low, 1 = high)" for n in STOCH_WINDOWS},
    "rsi_14": "RSI 14 (Wilder)",
    "macd": "MACD (EMA12 - EMA26) / close",
    "macd_hist": "MACD minus its 9-day signal line, / close",
    "bb_pctb_20": "Bollinger %B: (close - 20-day average) / (2 x 20-day stdev)",
    "bb_width_20": "Bollinger width: 4 x 20-day stdev / 20-day average",
    "cci_20": "CCI 20 on the typical price",
    "cmf_20": "Chaikin money flow 20: volume-weighted close location in the day's range",
    "max_ret_30": "largest one-day return in 30 days (lottery effect)",
    "skew_30": "skewness of daily returns over 30 days",
    "ret_3": "3-day return",
    "down_days_14": "share of down days in 14",
}
TECH = list(TECH_DOC)


def technicals(w, U: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The indicators, raw and ranked within each day's universe (prefix r_)."""
    c, h, qv = w.close, w.high, w.quote_volume
    lo = getattr(w, "low", None)
    if lo is None:
        lo = c
    ret = c / c.shift(1) - 1
    f: dict[str, pd.DataFrame] = {}
    for n in MA_WINDOWS:
        f[f"p_ma_{n}"] = c / c.rolling(n, min_periods=max(2, int(n * 0.7))).mean() - 1
    lv = np.log(qv.where(qv > 0))
    for n in VOL_WINDOWS:
        f[f"v_ma_{n}"] = lv - np.log(qv.rolling(n, min_periods=max(2, int(n * 0.7))).mean().where(lambda x: x > 0))
    for n in STOCH_WINDOWS:
        hh, ll = h.rolling(n, min_periods=int(n * 0.7)).max(), lo.rolling(n, min_periods=int(n * 0.7)).min()
        f[f"stoch_{n}"] = ((c - ll) / (hh - ll)).where(hh > ll)
    d = c.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    rs = up.ewm(alpha=1 / 14, min_periods=10).mean() / dn.ewm(alpha=1 / 14, min_periods=10).mean()
    f["rsi_14"] = 100 - 100 / (1 + rs)
    macd = c.ewm(span=12, min_periods=10).mean() - c.ewm(span=26, min_periods=20).mean()
    f["macd"] = macd / c
    f["macd_hist"] = (macd - macd.ewm(span=9, min_periods=7).mean()) / c
    m20, s20 = c.rolling(20, min_periods=14).mean(), c.rolling(20, min_periods=14).std()
    f["bb_pctb_20"] = ((c - m20) / (2 * s20)).where(s20 > 0)
    f["bb_width_20"] = (4 * s20 / m20).where(m20 > 0)
    tp = (h + lo + c) / 3
    tm = tp.rolling(20, min_periods=14).mean()
    md = (tp - tm).abs().rolling(20, min_periods=14).mean()
    f["cci_20"] = ((tp - tm) / (0.015 * md)).where(md > 0)
    rng = (h - lo).where(h > lo)
    mfv = (((c - lo) - (h - c)) / rng) * qv
    f["cmf_20"] = mfv.rolling(20, min_periods=14).sum() / qv.rolling(20, min_periods=14).sum()
    f["max_ret_30"] = ret.rolling(30, min_periods=20).max()
    f["skew_30"] = ret.rolling(30, min_periods=20).skew()
    f["ret_3"] = c / c.shift(3) - 1
    f["down_days_14"] = (ret < 0).astype(float).where(ret.notna()).rolling(14, min_periods=10).mean()
    out: dict[str, pd.DataFrame] = {}
    for k, v in f.items():
        v = v.replace([np.inf, -np.inf], np.nan)
        out[k] = v
        out[f"r_{k}"] = v.where(U).rank(axis=1, pct=True)
    return out
