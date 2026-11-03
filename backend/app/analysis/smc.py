"""Smart Money Concepts engine — deterministic, sequential (bar-by-bar) and look-ahead free.

These are *descriptive* pattern detections. Labels such as "order block" describe price geometry; they do
not verify institutional intent. Precise definitions: docs/TRADING_CONCEPTS.md §SMC.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.indicators.core import atr as _atr
from app.analysis.price_action import Pivot, find_pivots, stable_id

DISPLACEMENT_BODY_ATR = 1.5
DISPLACEMENT_BODY_RATIO = 0.6
FVG_MIN_ATR = 0.1
EQ_TOL_ATR = 0.1
OB_SEARCH_BARS = 6
SESSIONS_UTC = {"asia": (0, 8), "london": (7, 16), "new_york": (12, 21)}  # approximations, DST ignored


@dataclass
class Ctx:
    o: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray
    t: np.ndarray
    atr: np.ndarray
    n: int


def _ctx(df: pd.DataFrame) -> Ctx:
    return Ctx(*(df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")),
               df["open_time"].to_numpy(dtype=np.int64), _atr(df, 14).to_numpy(), len(df))


def is_displacement(x: Ctx, i: int) -> bool:
    body, rng = abs(x.c[i] - x.o[i]), x.h[i] - x.low[i]
    return bool(np.isfinite(x.atr[i]) and rng > 0 and body >= DISPLACEMENT_BODY_ATR * x.atr[i]
                and body / rng >= DISPLACEMENT_BODY_RATIO)


# ------------------------------------------------------------------ structure

def market_structure(x: Ctx, pivots: list[Pivot], scope: str) -> dict:
    piv = sorted(pivots, key=lambda p: p.confirmed_idx)
    trend, pi = 0, 0
    last_high: Pivot | None = None
    last_low: Pivot | None = None
    high_broken = low_broken = True
    events = []
    for t in range(x.n):
        while pi < len(piv) and piv[pi].confirmed_idx <= t:
            p = piv[pi]
            if p.kind == "high":
                last_high, high_broken = p, False
            else:
                last_low, low_broken = p, False
            pi += 1
        for direction in ("bull", "bear"):
            sw = last_high if direction == "bull" else last_low
            broken = high_broken if direction == "bull" else low_broken
            if sw is None or broken:
                continue
            crossed = x.c[t] > sw.price if direction == "bull" else x.c[t] < sw.price
            if not crossed:
                continue
            want = 1 if direction == "bull" else -1
            kind = "CHoCH" if trend == -want else "BOS"
            disp = is_displacement(x, t) or (t >= 1 and is_displacement(x, t - 1))
            fvg = t >= 2 and ((x.low[t] > x.h[t - 2]) if want == 1 else (x.h[t] < x.low[t - 2]))
            events.append({
                "id": stable_id(scope, kind, direction, sw.time, int(x.t[t])),
                "type": kind, "mss": kind == "CHoCH" and (disp or fvg), "direction": direction,
                "scope": scope, "level": sw.price, "swing_time": sw.time, "swing_idx": sw.idx,
                "break_time": int(x.t[t]), "break_idx": t, "displacement": disp,
            })
            trend = want
            if direction == "bull":
                high_broken = True
            else:
                low_broken = True
    return {"trend": {1: "bullish", -1: "bearish", 0: "undetermined"}[trend], "events": events}


# ------------------------------------------------------------------ order blocks / breakers

def order_blocks(x: Ctx, events: list[dict], max_keep: int = 12) -> list[dict]:
    """Order block = last opposite-colour candle at/just before the extreme of the leg that broke structure.
    Bullish OB: last bearish candle (close<open) within OB_SEARCH_BARS bars up to the lowest low between the
    broken swing and the break bar. Zone = that candle's [low, high]."""
    obs = []
    for e in events:
        s, t = e["swing_idx"], e["break_idx"]
        bull = e["direction"] == "bull"
        seg = x.low[s : t + 1] if bull else x.h[s : t + 1]
        ext = s + int(np.argmin(seg) if bull else np.argmax(seg))
        ob_i = None
        for k in range(ext, max(ext - OB_SEARCH_BARS, -1), -1):
            if (bull and x.c[k] < x.o[k]) or (not bull and x.c[k] > x.o[k]):
                ob_i = k
                break
        if ob_i is None:
            ob_i = ext
        top, bot = float(x.h[ob_i]), float(x.low[ob_i])
        ob = {
            "id": stable_id("ob", e["direction"], int(x.t[ob_i]), e["break_time"]),
            "type": "order_block", "direction": "bullish" if bull else "bearish",
            "top": top, "bottom": bot, "time": int(x.t[ob_i]), "formed_time": e["break_time"],
            "candle_idx": ob_i, "from_event": e["id"], "event_type": e["type"], "scope": e["scope"],
            "status": "active", "touched_time": None, "invalidated_time": None,
        }
        # forward tracking from bar after formation
        extreme_after = -np.inf if bull else np.inf
        for j in range(t + 1, x.n):
            if bull:
                if x.c[j] < bot:
                    ob["status"], ob["invalidated_time"] = "invalidated", int(x.t[j])
                    took_ext = extreme_after > x.h[t]
                    obs.append(_flip(ob, x, j, "breaker" if took_ext else "mitigation_block"))
                    break
                if x.low[j] <= top and ob["touched_time"] is None:
                    ob["status"], ob["touched_time"] = "mitigated", int(x.t[j])
                extreme_after = max(extreme_after, x.h[j])
            else:
                if x.c[j] > top:
                    ob["status"], ob["invalidated_time"] = "invalidated", int(x.t[j])
                    took_ext = extreme_after < x.low[t]
                    obs.append(_flip(ob, x, j, "breaker" if took_ext else "mitigation_block"))
                    break
                if x.h[j] >= bot and ob["touched_time"] is None:
                    ob["status"], ob["touched_time"] = "mitigated", int(x.t[j])
                extreme_after = min(extreme_after, x.low[j])
        obs.append(ob)
    # de-duplicate overlapping same-direction OBs from the same candle
    uniq: dict[tuple, dict] = {}
    for ob in obs:
        uniq.setdefault((ob["type"], ob["direction"], ob["time"]), ob)
    out = sorted(uniq.values(), key=lambda o: o["formed_time"])
    return out[-max_keep * 2 :]


def _flip(ob: dict, x: Ctx, j: int, kind: str) -> dict:
    """A failed OB flips polarity. Breaker: price first extended beyond the break candle (liquidity taken)
    before failing. Mitigation block: failed without making that new extreme."""
    new_dir = "bearish" if ob["direction"] == "bullish" else "bullish"
    br = {**ob, "id": stable_id(kind, ob["id"]), "type": kind, "direction": new_dir,
          "formed_time": int(x.t[j]), "status": "active", "touched_time": None, "invalidated_time": None,
          "from_ob": ob["id"]}
    for k in range(j + 1, x.n):
        if new_dir == "bearish":
            if x.c[k] > br["top"]:
                br["status"], br["invalidated_time"] = "invalidated", int(x.t[k])
                break
            if x.h[k] >= br["bottom"] and br["touched_time"] is None:
                br["status"], br["touched_time"] = "mitigated", int(x.t[k])
        else:
            if x.c[k] < br["bottom"]:
                br["status"], br["invalidated_time"] = "invalidated", int(x.t[k])
                break
            if x.low[k] <= br["top"] and br["touched_time"] is None:
                br["status"], br["touched_time"] = "mitigated", int(x.t[k])
    return br


# ------------------------------------------------------------------ imbalances

def fair_value_gaps(x: Ctx, max_keep: int = 20) -> list[dict]:
    """Bullish FVG at bar i: low[i] > high[i-2]; zone [high[i-2], low[i]]. Bearish mirror.
    Status: open -> partially_filled -> filled; a close beyond the far side converts it into an
    inverse FVG (IFVG) of opposite polarity."""
    out = []
    for i in range(2, x.n):
        a = x.atr[i]
        if not np.isfinite(a):
            continue
        for bull in (True, False):
            if bull and x.low[i] > x.h[i - 2] and x.low[i] - x.h[i - 2] >= FVG_MIN_ATR * a:
                top, bot = float(x.low[i]), float(x.h[i - 2])
            elif not bull and x.h[i] < x.low[i - 2] and x.low[i - 2] - x.h[i] >= FVG_MIN_ATR * a:
                top, bot = float(x.low[i - 2]), float(x.h[i])
            else:
                continue
            g = {"id": stable_id("fvg", "bull" if bull else "bear", int(x.t[i])), "type": "fvg",
                 "direction": "bullish" if bull else "bearish", "top": top, "bottom": bot,
                 "time": int(x.t[i - 1]), "formed_time": int(x.t[i]), "status": "open", "fill_pct": 0.0,
                 "filled_time": None, "inverted_time": None, "size_atr": (top - bot) / a}
            ifvg = None
            for j in range(i + 1, x.n):
                if ifvg is None:
                    pen = (top - x.low[j]) if bull else (x.h[j] - bot)
                    if pen > 0:
                        g["fill_pct"] = max(g["fill_pct"], min(pen / (top - bot), 1.0))
                        g["status"] = "filled" if g["fill_pct"] >= 1.0 else "partially_filled"
                        if g["status"] == "filled" and g["filled_time"] is None:
                            g["filled_time"] = int(x.t[j])
                    if (bull and x.c[j] < bot) or (not bull and x.c[j] > top):
                        g["status"], g["inverted_time"] = "inverted", int(x.t[j])
                        ifvg = {**g, "id": stable_id("ifvg", g["id"]), "type": "ifvg",
                                "direction": "bearish" if bull else "bullish", "formed_time": int(x.t[j]),
                                "status": "active", "from_fvg": g["id"]}
                else:
                    if (bull and x.c[j] > top) or (not bull and x.c[j] < bot):
                        ifvg["status"], ifvg["invalidated_time"] = "invalidated", int(x.t[j])
                        break
            out.append(g)
            if ifvg:
                out.append(ifvg)
    out.sort(key=lambda g: g["formed_time"])
    return out[-max_keep:]


def liquidity_voids(x: Ctx, max_keep: int = 8) -> list[dict]:
    """Large one-sided candle (range >= 2.5*ATR, body/range >= 0.8): thin trading through the body."""
    out = []
    for i in range(x.n):
        a = x.atr[i]
        rng = x.h[i] - x.low[i]
        if not np.isfinite(a) or rng <= 0:
            continue
        body = abs(x.c[i] - x.o[i])
        if rng >= 2.5 * a and body / rng >= 0.8:
            top, bot = float(max(x.o[i], x.c[i])), float(min(x.o[i], x.c[i]))
            bull = x.c[i] > x.o[i]
            filled = None
            for j in range(i + 1, x.n):
                if (bull and x.low[j] <= bot) or (not bull and x.h[j] >= top):
                    filled = int(x.t[j])
                    break
            out.append({"id": stable_id("void", int(x.t[i])), "type": "liquidity_void",
                        "direction": "bullish" if bull else "bearish", "top": top, "bottom": bot,
                        "time": int(x.t[i]), "status": "filled" if filled else "open", "filled_time": filled})
    return out[-max_keep:]


def displacement_candles(x: Ctx, lookback: int = 50) -> list[dict]:
    return [{"id": stable_id("disp", int(x.t[i])), "time": int(x.t[i]),
             "direction": "bullish" if x.c[i] > x.o[i] else "bearish",
             "body_atr": float(abs(x.c[i] - x.o[i]) / x.atr[i]), "price": float(x.c[i])}
            for i in range(max(0, x.n - lookback), x.n) if is_displacement(x, i)]


# ------------------------------------------------------------------ liquidity

def _sweep_scan(x: Ctx, level: float, side: str, start: int) -> dict | None:
    """First bar after `start` that trades through `level`. Sweep = wick through + close back on original
    side (rejection). Run = close through (liquidity taken, continuation)."""
    for j in range(start, x.n):
        if side == "buy" and x.h[j] > level:
            return {"time": int(x.t[j]), "idx": j, "kind": "sweep" if x.c[j] < level else "run"}
        if side == "sell" and x.low[j] < level:
            return {"time": int(x.t[j]), "idx": j, "kind": "sweep" if x.c[j] > level else "run"}
    return None


def equal_levels(x: Ctx, pivots: list[Pivot]) -> list[dict]:
    out = []
    for kind in ("high", "low"):
        ps = [p for p in pivots if p.kind == kind]
        for a, b in zip(ps, ps[1:]):
            tol = EQ_TOL_ATR * x.atr[b.idx] if np.isfinite(x.atr[b.idx]) else 0
            if tol <= 0 or abs(a.price - b.price) > tol or b.idx - a.idx < 3:
                continue
            between = x.h[a.idx + 1 : b.idx] if kind == "high" else x.low[a.idx + 1 : b.idx]
            lvl = max(a.price, b.price) if kind == "high" else min(a.price, b.price)
            if len(between) and ((kind == "high" and between.max() > lvl) or (kind == "low" and between.min() < lvl)):
                continue
            side = "buy" if kind == "high" else "sell"
            sw = _sweep_scan(x, lvl, side, b.confirmed_idx + 1)
            out.append({"id": stable_id("eq", kind, a.time, b.time), "type": "equal_highs" if kind == "high"
                        else "equal_lows", "side": side, "price": lvl, "time1": a.time, "time2": b.time,
                        "status": "swept" if sw else "resting", "swept": sw})
    return out


def liquidity_levels(x: Ctx, ext_pivots: list[Pivot], extra: list[dict]) -> tuple[list[dict], list[dict]]:
    """Buy-side liquidity rests above swing highs, sell-side below swing lows (resting stops are inferred,
    not observed). Returns (levels, sweep events)."""
    levels, sweeps = [], []
    cands = [{"price": p.price, "side": "buy" if p.kind == "high" else "sell", "time": p.time,
              "start": p.confirmed_idx + 1, "source": f"swing_{p.kind}"} for p in ext_pivots[-16:]] + extra
    for c in cands:
        sw = _sweep_scan(x, c["price"], c["side"], c["start"])
        lid = stable_id("liq", c["source"], c["time"], round(c["price"], 8))
        levels.append({"id": lid, "type": "buy_side_liquidity" if c["side"] == "buy" else "sell_side_liquidity",
                       "price": c["price"], "time": c["time"], "source": c["source"],
                       "status": ("swept" if sw["kind"] == "sweep" else "taken") if sw else "resting"})
        if sw:
            reversal = False
            j = sw["idx"]
            for k in range(j, min(j + 4, x.n)):
                if is_displacement(x, k) and ((c["side"] == "buy" and x.c[k] < x.o[k])
                                              or (c["side"] == "sell" and x.c[k] > x.o[k])):
                    reversal = True
                    break
            sweeps.append({"id": stable_id("sweep", lid, sw["time"]), "level_id": lid, "price": c["price"],
                           "side": c["side"], "time": sw["time"], "kind": sw["kind"], "source": c["source"],
                           "bias": ("bearish" if c["side"] == "buy" else "bullish") if sw["kind"] == "sweep"
                           else ("bullish" if c["side"] == "buy" else "bearish"),
                           "stop_hunt_candidate": sw["kind"] == "sweep" and reversal})
    return levels, sorted(sweeps, key=lambda s: s["time"])[-15:]


def reference_levels(df: pd.DataFrame, tf_ms: int) -> list[dict]:
    """Previous-day/week high & low and recent session highs/lows from intraday data (UTC)."""
    out: list[dict] = []
    if len(df) < 3:
        return out
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    last_day = t.iloc[-1].floor("D")
    if tf_ms <= 4 * 3_600_000:
        prev = df[(t >= last_day - pd.Timedelta(days=1)) & (t < last_day)]
        if len(prev):
            out += [{"source": "PDH", "price": float(prev["high"].max()), "side": "buy"},
                    {"source": "PDL", "price": float(prev["low"].min()), "side": "sell"}]
        wk = last_day - pd.Timedelta(days=int(last_day.weekday()))
        pw = df[(t >= wk - pd.Timedelta(days=7)) & (t < wk)]
        if len(pw) and (pw["open_time"].iloc[0] <= int((wk - pd.Timedelta(days=7)).timestamp() * 1000) + tf_ms):
            out += [{"source": "PWH", "price": float(pw["high"].max()), "side": "buy"},
                    {"source": "PWL", "price": float(pw["low"].min()), "side": "sell"}]
    if tf_ms <= 3_600_000:
        hours = t.dt.hour
        for name, (a, b) in SESSIONS_UTC.items():
            for day_off in (0, 1):
                d = last_day - pd.Timedelta(days=day_off)
                m = (t >= d) & (t < d + pd.Timedelta(days=1)) & (hours >= a) & (hours < b)
                if m.sum() == 0:
                    continue
                complete = t.iloc[-1] >= d + pd.Timedelta(hours=b)
                tag = f"{name}_{'today' if day_off == 0 else 'prev'}"
                if day_off == 0 and not complete:
                    tag = f"{name}_live"
                out += [{"source": f"{tag}_high", "price": float(df.loc[m, "high"].max()), "side": "buy"},
                        {"source": f"{tag}_low", "price": float(df.loc[m, "low"].min()), "side": "sell"}]
    for o in out:
        o["time"] = int(df["open_time"].iloc[-1])
        # level only "exists" from the end of its window; scan for sweeps only on the last day
        o["start"] = max(len(df) - int(86_400_000 // tf_ms), 0)
    return out


def current_session(ts_ms: int) -> list[str]:
    h = pd.Timestamp(ts_ms, unit="ms", tz="UTC").hour
    return [k for k, (a, b) in SESSIONS_UTC.items() if a <= h < b] or ["off_hours"]


# ------------------------------------------------------------------ ranges

def premium_discount(x: Ctx, ext_pivots: list[Pivot], trend: str) -> dict | None:
    highs = [p for p in ext_pivots if p.kind == "high"]
    lows = [p for p in ext_pivots if p.kind == "low"]
    if not highs or not lows:
        return None
    hi, lo = highs[-1], lows[-1]
    top, bot = max(hi.price, x.h[min(hi.idx, lo.idx):].max()), min(lo.price, x.low[min(hi.idx, lo.idx):].min())
    eq = (top + bot) / 2
    price = float(x.c[-1])
    leg_up = lo.idx < hi.idx
    rng = top - bot
    ote = ({"top": top - 0.62 * rng, "bottom": top - 0.79 * rng, "direction": "bullish"} if leg_up
           else {"top": bot + 0.79 * rng, "bottom": bot + 0.62 * rng, "direction": "bearish"})
    return {"id": stable_id("pd", hi.time, lo.time), "range_high": float(top), "range_low": float(bot),
            "equilibrium": float(eq), "time1": min(hi.time, lo.time), "zone": "premium" if price > eq else "discount",
            "position_pct": float((price - bot) / rng * 100) if rng > 0 else 50.0, "ote": ote,
            "leg": "up" if leg_up else "down"}


def inducement(x: Ctx, int_pivots: list[Pivot], ext_events: list[dict], obs: list[dict]) -> dict | None:
    if not ext_events:
        return None
    last = ext_events[-1]
    bull = last["direction"] == "bull"
    cands = [p for p in int_pivots if p.idx > last["break_idx"] and p.kind == ("low" if bull else "high")]
    if not cands:
        return None
    p = cands[0]
    active = [o for o in obs if o["status"] != "invalidated" and o["direction"] == ("bullish" if bull else "bearish")]
    if active:
        ob = active[-1]
        if (bull and p.price <= ob["top"]) or (not bull and p.price >= ob["bottom"]):
            return None
    sw = _sweep_scan(x, p.price, "sell" if bull else "buy", p.confirmed_idx + 1)
    return {"id": stable_id("idm", p.time), "type": "inducement", "price": p.price, "time": p.time,
            "direction": "bullish" if bull else "bearish", "status": "swept" if sw else "resting",
            "swept_time": sw["time"] if sw else None}


# ------------------------------------------------------------------ entry point

def analyze_smc(df: pd.DataFrame, tf_ms: int, internal_len: int = 3, external_len: int = 8) -> dict:
    if len(df) < max(30, 2 * external_len + 5):
        return {"error": "insufficient data", "bars": len(df)}
    x = _ctx(df)
    ip = find_pivots(df, internal_len, internal_len)
    ep = find_pivots(df, external_len, external_len)
    ext = market_structure(x, ep, "external")
    intl = market_structure(x, ip, "internal")
    obs = order_blocks(x, ext["events"] + [e for e in intl["events"] if e["displacement"]])
    refs = reference_levels(df, tf_ms)
    eqs = equal_levels(x, ip)
    levels, sweeps = liquidity_levels(x, ep, refs)
    pd_ = premium_discount(x, ep, ext["trend"])
    return {
        "trend_external": ext["trend"], "trend_internal": intl["trend"],
        "structure": (ext["events"][-12:] + intl["events"][-12:]),
        "swings": [p.d() for p in ep[-30:]],
        "internal_swings": [p.d() for p in ip[-40:]],
        "order_blocks": [o for o in obs if o["type"] == "order_block"],
        "breakers": [o for o in obs if o["type"] == "breaker"],
        "mitigation_blocks": [o for o in obs if o["type"] == "mitigation_block"],
        "fvgs": fair_value_gaps(x), "liquidity_voids": liquidity_voids(x),
        "displacement": displacement_candles(x), "equal_levels": eqs[-10:],
        "liquidity": levels, "sweeps": sweeps, "premium_discount": pd_,
        "inducement": inducement(x, ip, ext["events"], obs),
        "session": current_session(int(x.t[-1])),
        "params": {"internal_len": internal_len, "external_len": external_len,
                   "displacement_body_atr": DISPLACEMENT_BODY_ATR, "fvg_min_atr": FVG_MIN_ATR,
                   "eq_tol_atr": EQ_TOL_ATR},
    }
