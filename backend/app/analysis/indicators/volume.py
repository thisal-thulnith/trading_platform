import numpy as np
import pandas as pd

from app.analysis.indicators.core import Output, Param, indicator, sma

P = Param


@indicator("obv", "On-Balance Volume", "volume", [], [Output("obv", "separate")], family="volume_flow")
def _obv(df):
    sign = np.sign(df["close"].diff().fillna(0))
    return {"obv": (sign * df["volume"]).cumsum()}


@indicator("mfi", "Money Flow Index", "volume", [P("length", 14, 1)], [Output("mfi", "separate")],
           family="oscillator_momentum", levels=[20, 80])
def _mfi(df, length):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["volume"]
    d = tp.diff()
    pos = mf.where(d > 0, 0.0).rolling(length, min_periods=length).sum()
    neg = mf.where(d < 0, 0.0).rolling(length, min_periods=length).sum()
    out = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    return {"mfi": out.where(neg != 0, 100.0).where(pos.notna())}


def _clv(df):
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng).fillna(0)


@indicator("cmf", "Chaikin Money Flow", "volume", [P("length", 20, 1)], [Output("cmf", "separate")],
           family="volume_flow", levels=[0])
def _cmf(df, length):
    mfv = _clv(df) * df["volume"]
    return {"cmf": mfv.rolling(length, min_periods=length).sum()
            / df["volume"].rolling(length, min_periods=length).sum().replace(0, np.nan)}


@indicator("ad", "Accumulation/Distribution", "volume", [], [Output("ad", "separate")], family="volume_flow")
def _ad(df):
    return {"ad": (_clv(df) * df["volume"]).cumsum()}


@indicator("rvol", "Relative Volume", "volume", [P("length", 20, 1)], [Output("rvol", "separate", "histogram")],
           family="volume_activity", levels=[1, 2])
def _rvol(df, length):
    return {"rvol": df["volume"] / sma(df["volume"], length).shift(1)}


def _delta(df) -> pd.Series:
    """Per-bar volume delta = taker buy base - taker sell base. Directly observed from Binance kline field 9."""
    if "taker_buy_base" not in df:
        return pd.Series(np.nan, index=df.index)
    return 2 * df["taker_buy_base"] - df["volume"]


@indicator("cvd", "Cumulative Volume Delta", "volume", [P("anchor", "day", kind="str")],
           [Output("delta", "separate", "histogram"), Output("cvd", "separate")], family="order_flow",
           description="Exchange-reported taker buy vs sell volume per bar (Binance kline taker_buy_base). "
                       "Observed data for this venue only, not market-wide flow.")
def _cvd(df, anchor):
    d = _delta(df)
    if anchor == "none" or "open_time" not in df:
        return {"delta": d, "cvd": d.cumsum()}
    key = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.floor("D")
    return {"delta": d, "cvd": d.groupby(key).cumsum()}


@indicator("taker_imbalance", "Taker Imbalance", "volume", [P("length", 14, 1)],
           [Output("imbalance", "separate")], family="order_flow", levels=[0],
           description="Rolling (buy - sell) / total taker volume in [-1, 1]")
def _ti(df, length):
    d = _delta(df).rolling(length, min_periods=length).sum()
    v = df["volume"].rolling(length, min_periods=length).sum().replace(0, np.nan)
    return {"imbalance": d / v}


def volume_profile(df: pd.DataFrame, bins: int = 48, value_area: float = 0.70) -> dict:
    """Volume-at-price histogram over the window. Each bar's volume is spread uniformly across its range
    (an approximation: true volume-at-price requires tick data). Returns POC/VAH/VAL."""
    if df.empty:
        return {"bins": [], "poc": None, "vah": None, "val": None}
    lo, hi = float(df["low"].min()), float(df["high"].max())
    if hi <= lo:
        return {"bins": [], "poc": lo, "vah": hi, "val": lo}
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    buy = np.zeros(bins)
    has_taker = "taker_buy_base" in df
    for h, low, v, tb in zip(df["high"], df["low"], df["volume"],
                             df["taker_buy_base"] if has_taker else np.zeros(len(df))):
        if v <= 0:
            continue
        span = max(h - low, 1e-12)
        ov = np.clip(np.minimum(edges[1:], h) - np.maximum(edges[:-1], low), 0, None)
        if ov.sum() == 0:
            idx = min(np.searchsorted(edges, low, side="right") - 1, bins - 1)
            ov[idx] = span
        w = ov / ov.sum()
        vol += w * v
        buy += w * tb
    poc_i = int(np.argmax(vol))
    total = vol.sum()
    inc = {poc_i}
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < value_area * total and (lo_i > 0 or hi_i < bins - 1):
        down = vol[lo_i - 1] if lo_i > 0 else -1
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= down:
            hi_i += 1
            acc += vol[hi_i]
            inc.add(hi_i)
        else:
            lo_i -= 1
            acc += vol[lo_i]
            inc.add(lo_i)
    mids = (edges[:-1] + edges[1:]) / 2
    return {
        "bins": [{"price_low": float(edges[i]), "price_high": float(edges[i + 1]), "volume": float(vol[i]),
                  "buy_volume": float(buy[i]), "in_value_area": i in inc} for i in range(bins)],
        "poc": float(mids[poc_i]), "vah": float(edges[hi_i + 1]), "val": float(edges[lo_i]),
        "method": "uniform-range distribution of OHLCV bar volume (approximation)",
    }
