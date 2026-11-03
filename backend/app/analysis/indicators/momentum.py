import numpy as np
import pandas as pd

from app.analysis.indicators.core import Output, Param, ema, highest, indicator, lowest, rma, sma

P = Param


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = rma(d.clip(lower=0), n)
    loss = rma((-d).clip(lower=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


@indicator("rsi", "Relative Strength Index", "momentum", [P("length", 14, 2)], [Output("rsi", "separate")],
           family="oscillator_momentum", levels=[30, 50, 70])
def _rsi(df, length):
    return {"rsi": rsi(df["close"], length)}


@indicator("stochrsi", "Stochastic RSI", "momentum",
           [P("rsi_length", 14, 2), P("stoch_length", 14, 2), P("k", 3, 1), P("d", 3, 1)],
           [Output("k", "separate"), Output("d", "separate")], family="oscillator_momentum", levels=[20, 80])
def _stochrsi(df, rsi_length, stoch_length, k, d):
    r = rsi(df["close"], rsi_length)
    lo, hi = lowest(r, stoch_length), highest(r, stoch_length)
    st = 100 * (r - lo) / (hi - lo).replace(0, np.nan)
    kk = sma(st, k)
    return {"k": kk, "d": sma(kk, d)}


@indicator("stoch", "Stochastic Oscillator", "momentum", [P("k", 14, 1), P("smooth", 3, 1), P("d", 3, 1)],
           [Output("k", "separate"), Output("d", "separate")], family="oscillator_momentum", levels=[20, 80])
def _stoch(df, k, smooth, d):
    lo, hi = lowest(df["low"], k), highest(df["high"], k)
    raw = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    kk = sma(raw, smooth)
    return {"k": kk, "d": sma(kk, d)}


@indicator("cci", "Commodity Channel Index", "momentum", [P("length", 20, 2)], [Output("cci", "separate")],
           family="oscillator_momentum", levels=[-100, 100])
def _cci(df, length):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    m = sma(tp, length)
    md = tp.rolling(length, min_periods=length).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)
    return {"cci": (tp - m) / (0.015 * md.replace(0, np.nan))}


@indicator("willr", "Williams %R", "momentum", [P("length", 14, 1)], [Output("willr", "separate")],
           family="oscillator_momentum", levels=[-80, -20])
def _willr(df, length):
    hi, lo = highest(df["high"], length), lowest(df["low"], length)
    return {"willr": -100 * (hi - df["close"]) / (hi - lo).replace(0, np.nan)}


@indicator("roc", "Rate of Change", "momentum", [P("length", 10, 1)], [Output("roc", "separate")],
           family="return_momentum", levels=[0])
def _roc(df, length):
    return {"roc": 100 * (df["close"] / df["close"].shift(length) - 1)}


@indicator("mom", "Momentum", "momentum", [P("length", 10, 1)], [Output("mom", "separate")],
           family="return_momentum", levels=[0])
def _mom(df, length):
    return {"mom": df["close"] - df["close"].shift(length)}


@indicator("tsi", "True Strength Index", "momentum", [P("long", 25, 1), P("short", 13, 1), P("signal", 13, 1)],
           [Output("tsi", "separate"), Output("signal", "separate")], family="return_momentum", levels=[0])
def _tsi(df, long, short, signal):
    d = df["close"].diff()
    num = ema(ema(d, long), short)
    den = ema(ema(d.abs(), long), short)
    t = 100 * num / den.replace(0, np.nan)
    return {"tsi": t, "signal": ema(t, signal)}


@indicator("uo", "Ultimate Oscillator", "momentum", [P("fast", 7, 1), P("mid", 14, 1), P("slow", 28, 1)],
           [Output("uo", "separate")], family="oscillator_momentum", levels=[30, 70])
def _uo(df, fast, mid, slow):
    pc = df["close"].shift(1)
    bp = df["close"] - pd.concat([df["low"], pc], axis=1).min(axis=1)
    tr = pd.concat([df["high"], pc], axis=1).max(axis=1) - pd.concat([df["low"], pc], axis=1).min(axis=1)

    def avg(n):
        return bp.rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum().replace(0, np.nan)

    return {"uo": 100 * (4 * avg(fast) + 2 * avg(mid) + avg(slow)) / 7}


@indicator("ao", "Awesome Oscillator", "momentum", [P("fast", 5, 1), P("slow", 34, 1)],
           [Output("ao", "separate", "histogram")], family="trend_momentum", levels=[0])
def _ao(df, fast, slow):
    hl2 = (df["high"] + df["low"]) / 2
    return {"ao": sma(hl2, fast) - sma(hl2, slow)}


@indicator("ppo", "Percentage Price Oscillator", "momentum", [P("fast", 12, 1), P("slow", 26, 1), P("signal", 9, 1)],
           [Output("ppo", "separate"), Output("signal", "separate"), Output("hist", "separate", "histogram")],
           family="trend_momentum", levels=[0])
def _ppo(df, fast, slow, signal):
    s = ema(df["close"], slow)
    p = 100 * (ema(df["close"], fast) - s) / s
    sg = ema(p, signal)
    return {"ppo": p, "signal": sg, "hist": p - sg}
