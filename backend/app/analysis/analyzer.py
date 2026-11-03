"""Deterministic market analysis: builds an immutable, reproducible analysis snapshot.

LLMs never compute anything here. Every number is derived from stored market data by code in this package.
"""
import hashlib
import json
import time

import numpy as np
import pandas as pd

from app.analysis.annotations import build_annotations
from app.analysis.elliott import analyze_elliott
from app.analysis.evidence import Evidence, aggregate
from app.analysis.indicators import compute
from app.analysis.indicators.core import atr as _atr
from app.analysis.price_action import (
    breakouts,
    candlestick_patterns,
    compression,
    fibonacci,
    find_pivots,
    support_resistance,
    trendlines,
)
from app.analysis.regime import classify_regime
from app.analysis.smc import analyze_smc
from app.data.timeframes import tf_ms

ENGINE_VERSION = "1.0.0"


def data_hash(df: pd.DataFrame) -> str:
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    return hashlib.sha256(df[cols].to_numpy().tobytes()).hexdigest()


def _last(a) -> float | None:
    v = float(np.asarray(a)[-1])
    return v if np.isfinite(v) else None


def indicator_summary(df: pd.DataFrame) -> dict:
    c = df["close"].to_numpy()
    out = {
        "close": float(c[-1]),
        "ema20": _last(compute("ema", df, length=20)["ema"]),
        "ema50": _last(compute("ema", df, length=50)["ema"]),
        "ema200": _last(compute("ema", df, length=200)["ema"]) if len(df) > 200 else None,
        "rsi14": _last(compute("rsi", df)["rsi"]),
        "stochrsi_k": _last(compute("stochrsi", df)["k"]),
        "macd_hist": _last(compute("macd", df)["hist"]),
        "adx": _last(compute("adx", df)["adx"]),
        "atr14": _last(compute("atr", df)["atr"]),
        "bb_percent_b": _last(compute("bbands", df)["percent_b"]),
        "supertrend_dir": _last(compute("supertrend", df)["direction"]),
        "cmf20": _last(compute("cmf", df)["cmf"]),
        "rvol20": _last(compute("rvol", df)["rvol"]),
        "taker_imbalance14": _last(compute("taker_imbalance", df)["imbalance"]),
        "vwap": _last(compute("vwap", df)["vwap"]),
        "squeeze_on": _last(compute("squeeze", df)["squeeze_on"]),
        "hv20": _last(compute("hv", df)["hv"]),
    }
    return out


def trend_bias(df: pd.DataFrame) -> int:
    """Simple, robust trend state used for multi-timeframe alignment: +1/-1/0."""
    if len(df) < 60:
        return 0
    c = df["close"]
    e20 = compute("ema", df, length=20)["ema"][-1]
    e50 = compute("ema", df, length=50)["ema"][-1]
    if c.iloc[-1] > e20 > e50:
        return 1
    if c.iloc[-1] < e20 < e50:
        return -1
    return 0


def gather_evidence(ind: dict, smc: dict, regime: dict, mtf: dict, deriv: dict | None, book: dict | None,
                    elliott: dict, news: list[dict], ml: dict | None, pa: dict) -> list[Evidence]:
    ev: list[Evidence] = []
    c = ind["close"]
    # --- trend family
    if ind["ema50"] and ind["ema200"]:
        d = 1 if ind["ema50"] > ind["ema200"] else -1
        ev.append(Evidence("trend", "EMA50 vs EMA200", d, 0.6, f"EMA50 {ind['ema50']:.6g} "
                           f"{'>' if d > 0 else '<'} EMA200 {ind['ema200']:.6g}", "derived"))
    if ind["ema20"] and ind["ema50"]:
        d = 1 if c > ind["ema20"] > ind["ema50"] else -1 if c < ind["ema20"] < ind["ema50"] else 0
        ev.append(Evidence("trend", "Price/EMA20/EMA50 stack", d, 0.7, "stacked" if d else "mixed", "derived"))
    if ind["supertrend_dir"] is not None:
        ev.append(Evidence("trend", "Supertrend(10,3)", int(ind["supertrend_dir"]), 0.5,
                           "uptrend" if ind["supertrend_dir"] > 0 else "downtrend", "derived"))
    # --- momentum family (collapsed: oscillators are highly correlated)
    if ind["rsi14"] is not None:
        r = ind["rsi14"]
        d = 1 if r > 55 else -1 if r < 45 else 0
        ev.append(Evidence("momentum", "RSI(14)", d, min(abs(r - 50) / 25, 1), f"RSI {r:.1f}", "derived"))
        if r > 75 or r < 25:
            ev.append(Evidence("momentum", "RSI extreme", -1 if r > 75 else 1, 0.4,
                               f"RSI {r:.1f} stretched (mean-reversion risk)", "derived"))
    if ind["macd_hist"] is not None:
        ev.append(Evidence("momentum", "MACD histogram", 1 if ind["macd_hist"] > 0 else -1, 0.4,
                           f"hist {ind['macd_hist']:.4g}", "derived"))
    # --- structure family
    te = smc.get("trend_external")
    if te in ("bullish", "bearish"):
        ev.append(Evidence("structure", "External market structure", 1 if te == "bullish" else -1, 0.8,
                           f"last external break was {te}", "derived"))
    ti = smc.get("trend_internal")
    if ti in ("bullish", "bearish"):
        ev.append(Evidence("structure", "Internal structure", 1 if ti == "bullish" else -1, 0.4,
                           f"internal structure {ti}", "derived"))
    for e in (smc.get("structure") or [])[-3:]:
        if e.get("mss"):
            ev.append(Evidence("structure", "Market structure shift", 1 if e["direction"] == "bull" else -1, 0.7,
                               f"CHoCH with displacement at {e['level']:.6g}", "derived", e["id"]))
    pdz = smc.get("premium_discount")
    if pdz:
        d = 1 if pdz["zone"] == "discount" else -1
        ev.append(Evidence("structure", "Premium/discount", d, 0.3, f"price at {pdz['position_pct']:.0f}% of range "
                           f"({pdz['zone']})", "derived", pdz["id"]))
    price = c
    atr_now = ind["atr14"] or 0
    for ob in (smc.get("order_blocks") or []) + (smc.get("breakers") or []):
        if ob["status"] == "invalidated" or not atr_now:
            continue
        dist = (price - ob["top"]) if ob["direction"] == "bullish" else (ob["bottom"] - price)
        if -0.2 * atr_now <= dist <= 1.0 * atr_now:
            ev.append(Evidence("structure", f"Price at {ob['direction']} {ob['type'].replace('_', ' ')}",
                               1 if ob["direction"] == "bullish" else -1, 0.6,
                               f"zone {ob['bottom']:.6g}–{ob['top']:.6g}", "derived", ob["id"]))
    # --- liquidity family
    for s in (smc.get("sweeps") or [])[-3:]:
        ev.append(Evidence("liquidity", f"Liquidity {s['kind']} of {s['source']}",
                           1 if s["bias"] == "bullish" else -1, 0.7 if s["stop_hunt_candidate"] else 0.4,
                           f"{s['side']}-side level {s['price']:.6g}", "inferred", s["id"]))
    # --- volume/flow
    if ind["cmf20"] is not None:
        ev.append(Evidence("volume_flow", "CMF(20)", 1 if ind["cmf20"] > 0.05 else -1 if ind["cmf20"] < -0.05 else 0,
                           min(abs(ind["cmf20"]) * 4, 1), f"CMF {ind['cmf20']:.3f}", "derived"))
    if ind["taker_imbalance14"] is not None:
        ti_ = ind["taker_imbalance14"]
        ev.append(Evidence("volume_flow", "Taker imbalance (14 bars)", 1 if ti_ > 0.03 else -1 if ti_ < -0.03 else 0,
                           min(abs(ti_) * 8, 1), f"{ti_:+.3f} (venue taker volume)", "observed"))
    if book and book.get("available"):
        im = book.get("imbalance_0.5pct", 0)
        ev.append(Evidence("volume_flow", "Order book imbalance ±0.5%", 1 if im > 0.15 else -1 if im < -0.15 else 0,
                           min(abs(im), 1) * 0.5, f"{im:+.2f} (snapshot; books are easily spoofed)", "observed"))
    # --- derivatives
    if deriv and deriv.get("available"):
        if deriv.get("funding_extreme"):
            fr = deriv.get("funding_rate") or 0
            ev.append(Evidence("derivatives", "Funding extreme (crowding)", -1 if fr > 0 else 1, 0.5,
                               f"funding {fr * 100:.4f}% / z {deriv.get('funding_z', 0) or 0:.1f}", "observed"))
        for flag in deriv.get("squeeze_flags", []):
            ev.append(Evidence("derivatives", flag.replace("_", " "), 1 if flag.startswith("short") else -1, 0.5,
                               "funding/OI/price configuration", "inferred"))
        q = deriv.get("oi_price_regime")
        if q:
            d = 1 if "new longs" in q["label"] else -1 if "new shorts" in q["label"] else 0
            ev.append(Evidence("derivatives", "OI/price regime", d, 0.3, q["label"], "inferred"))
    # --- multi-timeframe alignment
    for htf, b in mtf.items():
        if b != 0:
            ev.append(Evidence("mtf_alignment", f"{htf} trend", b, 0.7, "bullish" if b > 0 else "bearish", "derived"))
    # --- elliott (low weight)
    counts = elliott.get("counts", [])
    if counts:
        p = counts[0]
        cw = str(p.get("current_wave", ""))
        d = 0
        if p["pattern"] == "impulse" and cw in ("4", "5"):
            d = 1 if p["direction"] == "bullish" else -1
        elif "correction expected" in cw:
            d = -1 if p["direction"] == "bullish" else 1
        ev.append(Evidence("elliott", f"Primary count: {p['pattern']} ({cw})", d, 0.3 * p["score"],
                           p["explanation"], "inferred", p["id"]))
    # --- news
    for n in news[:5]:
        if n.get("sentiment") and abs(n["sentiment"]) >= 0.3 and n.get("credibility", 0) >= 0.6:
            ev.append(Evidence("news", n["title"][:80], 1 if n["sentiment"] > 0 else -1,
                               min(abs(n["sentiment"]), 1) * 0.5, f"{n['source']} ({n['event_type']})", "inferred"))
    # --- ML (only accepted models)
    if ml and ml.get("accepted"):
        p_up = ml["p_up"]
        ev.append(Evidence("ml", f"{ml['model']} P(up {ml['horizon']})", 1 if p_up > 0.55 else -1 if p_up < 0.45 else 0,
                           min(abs(p_up - 0.5) * 4, 1), f"p_up={p_up:.2f} (model {ml['version']})", "model"))
    # --- context: regime and patterns (non-directional context items)
    ev.append(Evidence("context", "Regime", 0, 0, f"{regime['regime']}: {'; '.join(regime.get('reasons', []))}"))
    for pat in pa.get("patterns", [])[-3:]:
        if pat["bias"]:
            ev.append(Evidence("momentum", f"Candle: {pat['pattern']}", pat["bias"], 0.2, "last bars", "derived",
                               pat["id"]))
    return ev


def build_snapshot(symbol: str, tf: str, df: pd.DataFrame, htf: dict[str, pd.DataFrame] | None = None,
                   deriv: dict | None = None, book: dict | None = None, news: list[dict] | None = None,
                   ml: dict | None = None, news_risk: bool = False, light: bool = False) -> dict:
    """df must contain closed candles only (see store.closed_only).
    light=True skips Elliott enumeration and chart annotations (used by the backtester)."""
    if len(df) < 120:
        raise ValueError(f"need >= 120 closed candles, got {len(df)}")
    step = tf_ms(tf)
    ind = indicator_summary(df)
    smc = analyze_smc(df, step)
    ew = {"counts": []} if light else analyze_elliott(df)
    regime = classify_regime(df, book, news_risk)
    a = _atr(df, 14).to_numpy()
    piv = find_pivots(df, 5, 5)
    levels = support_resistance(df, piv, float(a[-1]))
    ext = find_pivots(df, 8, 8)
    fib = fibonacci(ext[-2], ext[-1]) if len(ext) >= 2 and ext[-2].kind != ext[-1].kind else None
    pa = {"levels": levels, "trendlines": trendlines(df, piv, float(a[-1])),
          "breakouts": breakouts(df, levels, a), "patterns": candlestick_patterns(df), "fib": fib,
          "compression": compression(df)}
    mtf = {k: trend_bias(v) for k, v in (htf or {}).items() if len(v) >= 60}
    evidence = gather_evidence(ind, smc, regime, mtf, deriv, book, ew, news or [], ml, pa)
    conf = aggregate(evidence)
    last_t = int(df["open_time"].iloc[-1])
    snap = {
        "symbol": symbol, "timeframe": tf, "engine_version": ENGINE_VERSION,
        "as_of": last_t, "as_of_close_time": last_t + step, "generated_at": int(time.time() * 1000),
        "data_hash": data_hash(df), "bars_used": len(df),
        "price": ind["close"], "indicators": ind, "regime": regime, "smc": smc, "elliott": ew,
        "price_action": pa, "mtf": mtf, "derivatives": deriv, "orderbook": book,
        "evidence": [e.d() for e in evidence], "confluence": conf, "ml": ml,
    }
    snap["annotations"] = [] if light else build_annotations(snap, tf)
    return snap


def jsonable(obj):
    """Make numpy/NaN safe for JSON/DB storage."""
    return json.loads(json.dumps(obj, default=_default, allow_nan=True).replace("NaN", "null")
                      .replace("-Infinity", "null").replace("Infinity", "null"))


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))
