"""Causal feature engineering. Every feature at row t uses data with open_time <= t only (tested)."""
import numpy as np
import pandas as pd

from app.analysis.indicators import compute
from app.analysis.price_action import find_pivots
from app.analysis.smc import _ctx, market_structure

FEATURE_VERSION = "f1"


def structure_state(df: pd.DataFrame, L: int = 5) -> np.ndarray:
    """+1/-1 external structure trend at each bar from sequential BOS/CHoCH events (causal by construction)."""
    ev = market_structure(_ctx(df), find_pivots(df, L, L), "x")["events"]
    out = np.zeros(len(df))
    cur = 0
    j = 0
    for i in range(len(df)):
        while j < len(ev) and ev[j]["break_idx"] <= i:
            cur = 1 if ev[j]["direction"] == "bull" else -1
            j += 1
        out[i] = cur
    return out


def build_features(df: pd.DataFrame, btc: pd.DataFrame | None = None) -> pd.DataFrame:
    c = df["close"]
    a = compute("atr", df)["atr"]
    f = pd.DataFrame(index=df.index)
    lr = np.log(c / c.shift(1))
    for k in (1, 3, 6, 12, 24, 72):
        f[f"ret_{k}"] = np.log(c / c.shift(k))
    f["vol_24"] = lr.rolling(24).std()
    f["vol_72"] = lr.rolling(72).std()
    f["vol_ratio"] = f["vol_24"] / f["vol_72"]
    f["atr_pct"] = a / c
    f["parkinson"] = compute("rvol_realized", df)["rv"]
    f["rsi14"] = compute("rsi", df)["rsi"]
    f["stochrsi_k"] = compute("stochrsi", df)["k"]
    m = compute("macd", df)
    f["macd_hist_atr"] = m["hist"] / a
    adx = compute("adx", df)
    f["adx"] = adx["adx"]
    f["di_diff"] = adx["plus_di"] - adx["minus_di"]
    bb = compute("bbands", df)
    f["bb_pctb"] = bb["percent_b"]
    f["bb_width"] = bb["width"]
    f["chop"] = compute("chop", df)["chop"]
    f["cmf"] = compute("cmf", df)["cmf"]
    f["rvol"] = compute("rvol", df)["rvol"]
    f["taker_imb"] = compute("taker_imbalance", df)["imbalance"]
    for n in (20, 50, 200):
        e = compute("ema", df, length=n)["ema"]
        f[f"dist_ema{n}_atr"] = (c - e) / a
    dc = compute("donchian", df, length=55)
    f["donchian_pos"] = (c - dc["lower"]) / (dc["upper"] - dc["lower"])
    f["supertrend"] = compute("supertrend", df)["direction"]
    f["squeeze"] = compute("squeeze", df)["squeeze_on"]
    f["structure"] = structure_state(df)
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    f["hour_sin"] = np.sin(2 * np.pi * t.dt.hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * t.dt.hour / 24)
    f["dow"] = t.dt.day_of_week
    if btc is not None and len(btc):
        b = btc.set_index("open_time")["close"].reindex(df["open_time"]).ffill()
        b = pd.Series(b.to_numpy(), index=df.index)
        f["btc_ret_6"] = np.log(b / b.shift(6))
        f["btc_ret_24"] = np.log(b / b.shift(24))
        f["rel_strength_24"] = f["ret_24"] - f["btc_ret_24"]
    return f.replace([np.inf, -np.inf], np.nan)


def triple_barrier(df: pd.DataFrame, horizon: int, k: float = 1.5, vol_window: int = 24) -> pd.DataFrame:
    """López de Prado triple-barrier labels. Barriers at ±k×σ (σ = rolling std of log returns × sqrt(horizon)).
    label=1 upper first, 0 lower first, NaN if vertical barrier (time-out) or same-bar ambiguity.
    Also returns fwd_ret (log return to horizon) and t1 (index where label resolved) for purging."""
    c, h, lo = (df[k_].to_numpy() for k_ in ("close", "high", "low"))
    sig = (np.log(df["close"]).diff().rolling(vol_window).std() * np.sqrt(horizon)).to_numpy()
    n = len(df)
    lab = np.full(n, np.nan)
    t1 = np.full(n, np.nan)
    fwd = np.full(n, np.nan)
    for i in range(n - horizon):
        if not np.isfinite(sig[i]):
            continue
        up, dn = c[i] * np.exp(k * sig[i]), c[i] * np.exp(-k * sig[i])
        fwd[i] = np.log(c[i + horizon] / c[i])
        t1[i] = i + horizon
        for j in range(i + 1, i + horizon + 1):
            hu, hd = h[j] >= up, lo[j] <= dn
            if hu and hd:
                t1[i] = j
                break
            if hu or hd:
                lab[i] = 1.0 if hu else 0.0
                t1[i] = j
                break
    return pd.DataFrame({"label": lab, "t1": t1, "fwd_ret": fwd, "barrier_sigma": sig}, index=df.index)
