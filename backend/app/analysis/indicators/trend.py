import numpy as np
import pandas as pd

from app.analysis.indicators.core import (
    Output,
    Param,
    atr,
    ema,
    highest,
    indicator,
    lowest,
    rma,
    sma,
    true_range,
    wma,
)

P = Param


def _src(df, source: str) -> pd.Series:
    if source == "hl2":
        return (df["high"] + df["low"]) / 2
    if source == "hlc3":
        return (df["high"] + df["low"] + df["close"]) / 3
    if source == "ohlc4":
        return (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    return df[source]


@indicator("sma", "Simple Moving Average", "trend", [P("length", 20, 1, 1000), P("source", "close", kind="str")],
           [Output("sma")], family="moving_average")
def _sma(df, length, source):
    return {"sma": sma(_src(df, source), length)}


@indicator("ema", "Exponential Moving Average", "trend", [P("length", 21, 1, 1000), P("source", "close", kind="str")],
           [Output("ema")], family="moving_average")
def _ema(df, length, source):
    return {"ema": ema(_src(df, source), length)}


@indicator("wma", "Weighted Moving Average", "trend", [P("length", 20, 1, 1000)], [Output("wma")],
           family="moving_average")
def _wma(df, length):
    return {"wma": wma(df["close"], length)}


@indicator("hma", "Hull Moving Average", "trend", [P("length", 21, 2, 1000)], [Output("hma")],
           family="moving_average", description="WMA(2*WMA(n/2) - WMA(n), sqrt(n))")
def _hma(df, length):
    half, sq = max(int(length / 2), 1), max(int(np.sqrt(length)), 1)
    return {"hma": wma(2 * wma(df["close"], half) - wma(df["close"], length), sq)}


@indicator("vwma", "Volume Weighted Moving Average", "trend", [P("length", 20, 1, 1000)], [Output("vwma")],
           family="moving_average")
def _vwma(df, length):
    pv = (df["close"] * df["volume"]).rolling(length, min_periods=length).sum()
    v = df["volume"].rolling(length, min_periods=length).sum()
    return {"vwma": pv / v.replace(0, np.nan)}


def _session_key(df, anchor: str) -> pd.Series:
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    if anchor == "week":
        return (t - pd.to_timedelta(t.dt.weekday, unit="D")).dt.floor("D")
    if anchor == "month":
        return t.dt.to_period("M").astype(str)
    return t.dt.floor("D")


@indicator("vwap", "VWAP (session-anchored)", "trend",
           [P("anchor", "day", kind="str"), P("bands", 2.0, 0, 5, "float")],
           [Output("vwap", color="#f5a623"), Output("upper", style="band_upper"), Output("lower", style="band_lower")],
           family="vwap", description="Resets at UTC day/week/month boundary; bands = k * volume-weighted stdev")
def _vwap(df, anchor, bands):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    key = _session_key(df, anchor)
    v = df["volume"]
    cpv = (tp * v).groupby(key).cumsum()
    cv = v.groupby(key).cumsum().replace(0, np.nan)
    vw = cpv / cv
    cpv2 = (tp * tp * v).groupby(key).cumsum()
    var = (cpv2 / cv - vw * vw).clip(lower=0)
    sd = np.sqrt(var)
    return {"vwap": vw, "upper": vw + bands * sd, "lower": vw - bands * sd}


@indicator("avwap", "Anchored VWAP", "trend", [P("anchor_time", 0, kind="time")], [Output("avwap", color="#e056fd")],
           family="vwap", description="VWAP accumulated from a user-selected anchor candle (epoch seconds)")
def _avwap(df, anchor_time):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    anchor_ms = int(anchor_time) * 1000
    mask = df["open_time"] >= anchor_ms if anchor_ms else pd.Series(True, index=df.index)
    v = df["volume"].where(mask, 0.0)
    cv = v.cumsum().replace(0, np.nan)
    out = (tp * v).cumsum() / cv
    return {"avwap": out.where(mask)}


@indicator("macd", "MACD", "trend", [P("fast", 12, 1), P("slow", 26, 1), P("signal", 9, 1)],
           [Output("macd", "separate"), Output("signal", "separate"), Output("hist", "separate", "histogram")],
           family="trend_momentum", levels=[0])
def _macd(df, fast, slow, signal):
    m = ema(df["close"], fast) - ema(df["close"], slow)
    s = ema(m, signal)
    return {"macd": m, "signal": s, "hist": m - s}


def dmi(df, n: int = 14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    plus_dm.iloc[0] = minus_dm.iloc[0] = np.nan
    tr = true_range(df)
    tr.iloc[0] = np.nan
    atr_ = rma(tr, n)
    pdi = 100 * rma(plus_dm, n) / atr_
    mdi = 100 * rma(minus_dm, n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pdi, mdi, rma(dx, n)


@indicator("adx", "ADX / DMI", "trend", [P("length", 14, 1)],
           [Output("adx", "separate"), Output("plus_di", "separate"), Output("minus_di", "separate")],
           family="trend_strength", levels=[20, 25])
def _adx(df, length):
    pdi, mdi, adx = dmi(df, length)
    return {"adx": adx, "plus_di": pdi, "minus_di": mdi}


@indicator("ichimoku", "Ichimoku Cloud", "trend",
           [P("tenkan", 9, 1), P("kijun", 26, 1), P("senkou", 52, 1)],
           [Output("tenkan"), Output("kijun"), Output("span_a", style="band_upper"),
            Output("span_b", style="band_lower")],
           family="ichimoku",
           description="Spans are shifted forward by kijun bars for display; values here are the *plotted* "
                       "spans at each bar (computed from data kijun bars ago), so they are look-ahead safe. "
                       "Chikou is omitted because it plots current close in the past (not usable as a feature).")
def _ichimoku(df, tenkan, kijun, senkou):
    h, low = df["high"], df["low"]
    t = (highest(h, tenkan) + lowest(low, tenkan)) / 2
    k = (highest(h, kijun) + lowest(low, kijun)) / 2
    a = ((t + k) / 2).shift(kijun)
    b = ((highest(h, senkou) + lowest(low, senkou)) / 2).shift(kijun)
    return {"tenkan": t, "kijun": k, "span_a": a, "span_b": b}


@indicator("supertrend", "Supertrend", "trend", [P("length", 10, 1), P("mult", 3.0, 0.1, 20, "float")],
           [Output("supertrend"), Output("direction", "separate")], family="trend_following")
def _supertrend(df, length, mult):
    a = atr(df, length).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    c = df["close"].to_numpy()
    n = len(c)
    up, dn, st, d = (np.full(n, np.nan) for _ in range(4))
    for i in range(n):
        if np.isnan(a[i]):
            continue
        bu, bd = hl2[i] + mult * a[i], hl2[i] - mult * a[i]
        if i == 0 or np.isnan(up[i - 1]):
            up[i], dn[i], d[i] = bu, bd, 1
        else:
            up[i] = bu if (bu < up[i - 1] or c[i - 1] > up[i - 1]) else up[i - 1]
            dn[i] = bd if (bd > dn[i - 1] or c[i - 1] < dn[i - 1]) else dn[i - 1]
            if d[i - 1] == 1:
                d[i] = -1 if c[i] < dn[i] else 1
            else:
                d[i] = 1 if c[i] > up[i] else -1
        st[i] = dn[i] if d[i] == 1 else up[i]
    return {"supertrend": st, "direction": d}


@indicator("psar", "Parabolic SAR", "trend",
           [P("start", 0.02, 0.001, 1, "float"), P("step", 0.02, 0.001, 1, "float"), P("max", 0.2, 0.01, 1, "float")],
           [Output("psar", style="dots")], family="trend_following")
def _psar(df, start, step, max):
    h, low = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(h)
    out = np.full(n, np.nan)
    if n < 2:
        return {"psar": out}
    bull = h[1] + low[1] >= h[0] + low[0]
    af = start
    ep = h[0] if bull else low[0]
    sar = low[0] if bull else h[0]
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < sar:
                bull, sar, ep, af = False, ep, low[i], start
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max)
        else:
            sar = np.maximum(sar, np.maximum(h[i - 1], h[i - 2] if i >= 2 else h[i - 1]))
            if h[i] > sar:
                bull, sar, ep, af = True, ep, h[i], start
            elif low[i] < ep:
                ep, af = low[i], min(af + step, max)
        out[i] = sar
    return {"psar": out}


@indicator("aroon", "Aroon", "trend", [P("length", 25, 1)],
           [Output("up", "separate"), Output("down", "separate")], family="trend_strength", levels=[30, 70])
def _aroon(df, length):
    hh = df["high"].rolling(length + 1, min_periods=length + 1).apply(lambda a: np.argmax(a), raw=True)
    ll = df["low"].rolling(length + 1, min_periods=length + 1).apply(lambda a: np.argmin(a), raw=True)
    return {"up": 100 * hh / length, "down": 100 * ll / length}


@indicator("donchian", "Donchian Channels", "trend", [P("length", 20, 1)],
           [Output("upper", style="band_upper"), Output("middle"), Output("lower", style="band_lower")],
           family="channel_breakout")
def _donchian(df, length):
    u, lo = highest(df["high"], length), lowest(df["low"], length)
    return {"upper": u, "middle": (u + lo) / 2, "lower": lo}


@indicator("linreg", "Linear Regression Channel", "trend", [P("length", 100, 5), P("mult", 2.0, 0, 10, "float")],
           [Output("mid"), Output("upper", style="band_upper"), Output("lower", style="band_lower"),
            Output("slope", "separate")],
           family="regression",
           description="Rolling OLS fit over the last `length` closes; values are the fit's end-point at each bar")
def _linreg(df, length, mult):
    c = df["close"].to_numpy(dtype=float)
    n = len(c)
    mid, sd, slope = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    x = np.arange(length, dtype=float)
    xm = x.mean()
    sxx = ((x - xm) ** 2).sum()
    for i in range(length - 1, n):
        y = c[i - length + 1 : i + 1]
        b = ((x - xm) * (y - y.mean())).sum() / sxx
        a = y.mean() - b * xm
        fit = a + b * x
        mid[i], slope[i], sd[i] = fit[-1], b, np.sqrt(((y - fit) ** 2).mean())
    return {"mid": mid, "upper": mid + mult * sd, "lower": mid - mult * sd, "slope": slope}
