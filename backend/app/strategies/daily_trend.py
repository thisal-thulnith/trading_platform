"""Daily trend breakout — the only setup that passed train, validation AND the untouched 2025-26 test at >= 1:2.

Rule (fixed before the test period was looked at; evidence in data/history/edge_study_1d.json, docs/STRATEGIES.md):
  signal   daily close above the highest high of the previous 20 days
  filter   BTC's last finished daily close is above its 200-day average (no longs in a bear market)
  entry    next day's open
  stop     entry - 2.5 x ATR(14)          (stop at least 1.5 % away so costs stay small next to the risk)
  exit     sell a third at +2R (the 1:2), move the stop to entry, trail the rest 3 x ATR below the close; 60-day
           limit. (A third, not half, was chosen on 2020-24 only: $100 -> $526 vs $419 at growth risk; the 2025-26
           test was about the same either way, -0.6 %, because no coin trended.)
Long only, so it works on a spot account with no leverage and no liquidation risk.
"""
import numpy as np
import pandas as pd

LOOKBACK = 20
STOP_ATR = 2.5
TRAIL_ATR = 3.0
RR = 2.0
PARTIAL = 1 / 3  # share sold at the 2R target
MAX_HOLD_DAYS = 60
MIN_STOP_PCT = 0.015
BTC_MA = 200
RANK_DAYS = 60  # when more coins trigger than the risk budget allows, take the strongest 60-day performers first


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def btc_filter(btc: pd.DataFrame) -> pd.Series:
    """True on days whose close is above the 200-day average, indexed by day (open time)."""
    b = btc.set_index("t").close
    return (b > b.rolling(BTC_MA).mean()).rename("btc_ok")


def prepare(df: pd.DataFrame, btc_ok: pd.Series, lookback: int = LOOKBACK) -> pd.DataFrame:
    """Per-coin daily frame with everything the strategy needs. `df` needs t, open, high, low, close (one row per day).
    Every column on row i uses only data up to and including day i's close."""
    d = df.set_index("t")[["open", "high", "low", "close"]].astype(float).copy()
    d["atr"] = atr(d)
    d["hh"] = d.high.rolling(lookback).max().shift()
    d["btc_ok"] = btc_ok.reindex(d.index).ffill().fillna(False).astype(bool)
    d["rank"] = d.close / d.close.shift(RANK_DAYS) - 1
    d["signal"] = (d.close > d.hh) & d.btc_ok
    d["stop_dist"] = STOP_ATR * d.atr
    return d


def plan(entry: float, atr_now: float) -> dict | None:
    """Entry plan for a fill at `entry`: stop, 2R target, or None when the stop would be too tight for the costs."""
    stop = entry - STOP_ATR * atr_now
    risk = entry - stop
    if not np.isfinite(risk) or risk <= 0 or risk / entry < MIN_STOP_PCT:
        return None
    return {"stop": stop, "target": entry + RR * risk, "risk_per_unit": risk}
