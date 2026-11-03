"""Deterministic price-action primitives. Definitions are documented in docs/TRADING_CONCEPTS.md.

No look-ahead: a fractal pivot at bar i with `right` bars is only *known* at bar i+right; every pivot
carries `confirmed_idx` and consumers must filter on it.
"""
import hashlib
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from app.analysis.indicators.core import atr as _atr


def stable_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


@dataclass
class Pivot:
    idx: int
    confirmed_idx: int
    time: int  # ms
    price: float
    kind: str  # high | low
    label: str = ""  # HH | HL | LH | LL | H | L

    def d(self) -> dict:
        return asdict(self)


def find_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[Pivot]:
    """Fractal pivots: high[i] strictly greater than `left` previous highs and >= `right` following highs."""
    h, low, t = df["high"].to_numpy(), df["low"].to_numpy(), df["open_time"].to_numpy()
    n = len(h)
    out: list[Pivot] = []
    for i in range(left, n - right):
        hw_l, hw_r = h[i - left : i], h[i + 1 : i + right + 1]
        if h[i] > hw_l.max() and h[i] >= hw_r.max():
            out.append(Pivot(i, i + right, int(t[i]), float(h[i]), "high"))
        lw_l, lw_r = low[i - left : i], low[i + 1 : i + right + 1]
        if low[i] < lw_l.min() and low[i] <= lw_r.min():
            out.append(Pivot(i, i + right, int(t[i]), float(low[i]), "low"))
    out.sort(key=lambda p: (p.idx, p.kind))
    # label HH/HL/LH/LL relative to previous same-kind pivot
    last: dict[str, Pivot | None] = {"high": None, "low": None}
    for p in out:
        prev = last[p.kind]
        if prev is None:
            p.label = "H" if p.kind == "high" else "L"
        elif p.kind == "high":
            p.label = "HH" if p.price > prev.price else "LH"
        else:
            p.label = "HL" if p.price > prev.price else "LL"
        last[p.kind] = p
    return out


def zigzag(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[Pivot]:
    """Alternating high/low pivot sequence (keeps the more extreme of consecutive same-kind pivots)."""
    seq: list[Pivot] = []
    for p in find_pivots(df, left, right):
        if seq and seq[-1].kind == p.kind:
            if (p.kind == "high" and p.price >= seq[-1].price) or (p.kind == "low" and p.price <= seq[-1].price):
                seq[-1] = p
            continue
        seq.append(p)
    return seq


def support_resistance(df: pd.DataFrame, pivots: list[Pivot], atr_now: float, max_levels: int = 8) -> list[dict]:
    """Cluster pivot prices within 0.5*ATR; strength = touches, recency-weighted."""
    if not pivots or not np.isfinite(atr_now) or atr_now <= 0:
        return []
    tol = 0.5 * atr_now
    n = len(df)
    clusters: list[dict] = []
    for p in sorted(pivots, key=lambda x: x.price):
        if clusters and p.price - clusters[-1]["prices"][-1] <= tol:
            clusters[-1]["prices"].append(p.price)
            clusters[-1]["pivots"].append(p)
        else:
            clusters.append({"prices": [p.price], "pivots": [p]})
    price = float(df["close"].iloc[-1])
    levels = []
    for c in clusters:
        lvl = float(np.mean(c["prices"]))
        touches = len(c["pivots"])
        recency = max(p.idx for p in c["pivots"]) / max(n - 1, 1)
        strength = touches * (0.5 + 0.5 * recency)
        levels.append({
            "id": stable_id("sr", round(lvl, 8), touches, min(p.time for p in c["pivots"])),
            "price": lvl, "low": lvl - 0.25 * atr_now, "high": lvl + 0.25 * atr_now,
            "touches": touches, "strength": round(strength, 3),
            "type": "resistance" if lvl > price else "support",
            "first_time": min(p.time for p in c["pivots"]), "last_time": max(p.time for p in c["pivots"]),
        })
    levels.sort(key=lambda x: -x["strength"])
    return levels[:max_levels]


def trendlines(df: pd.DataFrame, pivots: list[Pivot], atr_now: float) -> list[dict]:
    """Rising support from last two higher lows / falling resistance from last two lower highs, valid only
    if no close crossed the line between the anchors and the last bar (by more than 0.1*ATR)."""
    c = df["close"].to_numpy()
    t = df["open_time"].to_numpy()
    n = len(c)
    out = []
    for kind, cond in (("low", lambda a, b: b.price > a.price), ("high", lambda a, b: b.price < a.price)):
        ps = [p for p in pivots if p.kind == kind]
        if len(ps) < 2:
            continue
        a, b = ps[-2], ps[-1]
        if not cond(a, b):
            continue
        slope = (b.price - a.price) / (b.idx - a.idx)
        line = a.price + slope * (np.arange(a.idx, n) - a.idx)
        seg = c[a.idx :]
        broken_at = None
        viol = (seg < line - 0.1 * atr_now) if kind == "low" else (seg > line + 0.1 * atr_now)
        idxs = np.where(viol)[0]
        if len(idxs):
            broken_at = int(a.idx + idxs[0])
            if broken_at <= b.idx:
                continue
        out.append({
            "id": stable_id("tl", kind, a.time, b.time),
            "kind": "rising_support" if kind == "low" else "falling_resistance",
            "time1": a.time, "price1": a.price, "time2": b.time, "price2": b.price,
            "slope_per_bar": slope, "value_now": float(line[-1]),
            "end_time": int(t[-1]), "status": "broken" if broken_at is not None else "intact",
            "broken_time": int(t[broken_at]) if broken_at is not None else None,
        })
    return out


def breakouts(df: pd.DataFrame, levels: list[dict], atr_series: np.ndarray, lookback: int = 30) -> list[dict]:
    """Classify recent interactions with S/R levels: breakout, failed breakout (close back inside within 3
    bars), retest (after breakout, wick revisits level within 0.2*ATR and closes on breakout side)."""
    c, h, low, t = (df[k].to_numpy() for k in ("close", "high", "low", "open_time"))
    n = len(c)
    events = []
    for lvl in levels:
        L = lvl["price"]
        for i in range(max(1, n - lookback), n):
            a = atr_series[i]
            if not np.isfinite(a):
                continue
            up = c[i - 1] <= L and c[i] > L + 0.1 * a
            dn = c[i - 1] >= L and c[i] < L - 0.1 * a
            if not (up or dn):
                continue
            direction = "up" if up else "down"
            status = "breakout"
            for j in range(i + 1, min(i + 4, n)):
                if (up and c[j] < L) or (dn and c[j] > L):
                    status = "failed_breakout"
                    break
            if status == "breakout":
                for j in range(i + 1, n):
                    if up and low[j] <= L + 0.2 * atr_series[j] and c[j] > L:
                        status = "retest"
                        break
                    if dn and h[j] >= L - 0.2 * atr_series[j] and c[j] < L:
                        status = "retest"
                        break
            events.append({"id": stable_id("bo", lvl["id"], t[i]), "level": L, "time": int(t[i]),
                           "direction": direction, "status": status})
    return events[-10:]


def candlestick_patterns(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    o, h, low, c, t = (df[k].to_numpy() for k in ("open", "high", "low", "close", "open_time"))
    a = _atr(df, 14).to_numpy()
    n = len(c)
    out = []
    for i in range(max(2, n - lookback), n):
        body, rng = abs(c[i] - o[i]), h[i] - low[i]
        if rng <= 0 or not np.isfinite(a[i]):
            continue
        up_w, lo_w = h[i] - max(o[i], c[i]), min(o[i], c[i]) - low[i]
        pb, pc_ = abs(c[i - 1] - o[i - 1]), c[i - 1]
        pats = []
        if c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= pc_ and body > pb:
            pats.append(("bullish_engulfing", 1))
        if c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= pc_ and body > pb:
            pats.append(("bearish_engulfing", -1))
        if lo_w >= 2 * body and up_w <= 0.3 * rng and rng > 0.5 * a[i]:
            pats.append(("hammer", 1))
        if up_w >= 2 * body and lo_w <= 0.3 * rng and rng > 0.5 * a[i]:
            pats.append(("shooting_star", -1))
        if body <= 0.1 * rng:
            pats.append(("doji", 0))
        if h[i] < h[i - 1] and low[i] > low[i - 1]:
            pats.append(("inside_bar", 0))
        b2 = abs(c[i - 2] - o[i - 2])
        if (c[i - 2] < o[i - 2] and b2 > 0.6 * a[i] and pb < 0.3 * b2 and c[i] > o[i]
                and c[i] > (o[i - 2] + c[i - 2]) / 2):
            pats.append(("morning_star", 1))
        if (c[i - 2] > o[i - 2] and b2 > 0.6 * a[i] and pb < 0.3 * b2 and c[i] < o[i]
                and c[i] < (o[i - 2] + c[i - 2]) / 2):
            pats.append(("evening_star", -1))
        for name, bias in pats:
            out.append({"id": stable_id("cdl", name, t[i]), "pattern": name, "bias": bias, "time": int(t[i]),
                        "price": float(h[i] if bias < 0 else low[i])})
    return out


FIB_RETRACEMENTS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 1.0]
FIB_EXTENSIONS = [1.272, 1.618, 2.0, 2.618]


def fibonacci(swing_from: Pivot, swing_to: Pivot) -> dict:
    """Retracements of the leg from->to, and extensions beyond `to`."""
    a, b = swing_from.price, swing_to.price
    d = b - a
    return {
        "id": stable_id("fib", swing_from.time, swing_to.time),
        "time1": swing_from.time, "price1": a, "time2": swing_to.time, "price2": b,
        "direction": "up" if d > 0 else "down",
        "retracements": {str(r): b - r * d for r in FIB_RETRACEMENTS},
        "extensions": {str(e): a + e * d for e in FIB_EXTENSIONS},
    }


def compression(df: pd.DataFrame) -> dict:
    """Volatility contraction: ATR(14)/ATR(100) and 20-bar range vs 100-bar median range."""
    a14, a100 = _atr(df, 14), _atr(df, 100)
    ratio = float((a14 / a100).iloc[-1]) if len(df) > 100 else float("nan")
    rng20 = float(df["high"].tail(20).max() - df["low"].tail(20).min())
    med = float((df["high"] - df["low"]).rolling(20).sum().tail(100).median()) if len(df) > 120 else float("nan")
    return {"atr_ratio": ratio, "range20": rng20, "compressed": bool(np.isfinite(ratio) and ratio < 0.75),
            "expanding": bool(np.isfinite(ratio) and ratio > 1.3), "range20_vs_typical": rng20 / med if med else None}
