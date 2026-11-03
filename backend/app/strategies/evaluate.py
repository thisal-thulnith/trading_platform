"""Run all strategies on a snapshot, then validate (risk/quality gates + counter-argument review)."""
import time

import numpy as np
import pandas as pd

from app.analysis.indicators import compute
from app.data.timeframes import tf_ms
from app.risk.sizing import rr
from app.strategies.library import STRATEGIES

ABSTAIN_REGIMES = {"liquidity_stress", "news_instability"}
MIN_RR_TP1 = 2.0  # live record: 36 closed trades at the old 1.2 floor lost -35.5R
MAX_COST_SHARE = 0.2  # fees + slippage may use at most 20 % of the risk (old stops of 0.07 % lost ~1.5R each)
MIN_SUPPORTING_FAMILIES = 3


def enrich(snap: dict, df: pd.DataFrame, n: int = 60) -> dict:
    """Attach runtime-only series used by strategies (keys prefixed with _ are not persisted)."""
    tail = df.tail(n)
    snap["_recent"] = {k: tail[c].tolist() for k, c in
                       (("time", "open_time"), ("open", "open"), ("high", "high"), ("low", "low"),
                        ("close", "close"), ("volume", "volume"))}
    vw = compute("vwap", df)
    snap["_vwap"] = {"vwap": float(vw["vwap"][-1]), "upper": float(vw["upper"][-1]), "lower": float(vw["lower"][-1]),
                     "band_hist": {True: vw["lower"][-4:-1].tolist(), False: vw["upper"][-4:-1].tolist()}}
    sq = compute("squeeze", df)["squeeze_on"]
    snap["_squeeze_hist"] = [int(x) if np.isfinite(x) else 0 for x in sq[-12:]]
    return snap


def strip_runtime(snap: dict) -> dict:
    return {k: v for k, v in snap.items() if not k.startswith("_")}


def validate(snap: dict, cand: dict, now_ms: int | None = None, fee: float = 0.0005, slip_bps: float = 2.0,
             strategy_stats: dict | None = None) -> dict:
    """Deterministic validation + counter-argument review. Returns {passed, checks, counter_arguments, score}."""
    now_ms = now_ms or int(time.time() * 1000)
    s = 1 if cand["direction"] == "LONG" else -1
    ind = snap["indicators"]
    a = ind["atr14"] or 0
    entry = (cand["entry_low"] + cand["entry_high"]) / 2
    stop = cand["stop"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    tps = cand["targets"]
    r1 = rr(entry, stop, tps[0], fee, slip_bps) if tps else 0
    chk("reward/risk to TP1 ≥ %.1f (net of costs)" % MIN_RR_TP1, r1 >= MIN_RR_TP1, f"{r1:.2f}")
    cost = entry * (2 * fee + 2 * slip_bps / 1e4)
    share = cost / abs(entry - stop) if entry != stop else 1.0
    chk("stop wide enough for costs (costs ≤ %d%% of risk)" % (MAX_COST_SHARE * 100), share <= MAX_COST_SHARE,
        f"costs {share * 100:.0f}% of risk, stop {abs(entry - stop) / entry * 100:.2f}% away")
    sd = abs(entry - stop) / a if a else 0
    chk("stop distance 0.25–6 ATR", 0.25 <= sd <= 6, f"{sd:.2f} ATR")
    chk("stop on correct side", s * (entry - stop) > 0, f"entry {entry:.6g} stop {stop:.6g}")
    age = now_ms - snap["as_of_close_time"]
    chk("data fresh", age <= 2 * tf_ms(snap["timeframe"]) + 120_000, f"last close {age / 60000:.1f} min ago")
    regime = snap["regime"]["regime"]
    chk("regime not abstain", regime not in ABSTAIN_REGIMES, regime)
    conf = snap["confluence"]
    aligned = conf["bias"] * s
    chk("confluence aligned with direction", aligned >= 0.1, f"bias {conf['bias']:+.2f}")
    fam = conf["families"]
    support = [f for f, v in fam.items() if v["score"] * s > 0.15]
    oppose = [f for f, v in fam.items() if v["score"] * s < -0.15]
    chk(f"≥{MIN_SUPPORTING_FAMILIES} independent evidence families support", len(support) >= MIN_SUPPORTING_FAMILIES,
        ", ".join(support) or "none")
    ml = snap.get("ml")
    if ml and ml.get("accepted"):
        p = ml["p_up"] if s > 0 else 1 - ml["p_up"]
        chk("validated ML model does not contradict", p >= 0.5, f"P(direction)={p:.2f}")
    book = snap.get("orderbook") or {}
    if book.get("available"):
        chk("spread acceptable", book.get("spread_bps", 0) <= 10, f"{book.get('spread_bps', 0):.2f} bps")
    if strategy_stats and strategy_stats.get("n", 0) >= 30:
        chk("strategy not degraded (rolling expectancy ≥ 0)", strategy_stats.get("expectancy_r", 0) >= 0,
            f"{strategy_stats.get('expectancy_r', 0):+.2f}R over {strategy_stats['n']} trades")

    # Counter-argument review: enumerate the strongest reasons the trade may fail.
    counter = []
    for f in oppose:
        items = [e for e in snap["evidence"] if e["family"] == f and e["direction"] == -s]
        counter.append(f"{f}: " + "; ".join(f"{e['name']} ({e['detail']})" for e in items[:2]))
    for k, v in (snap.get("mtf") or {}).items():
        if v == -s:
            counter.append(f"{k} trend opposes the trade")
    rsi = ind.get("rsi14") or 50
    if (s > 0 and rsi > 75) or (s < 0 and rsi < 25):
        counter.append(f"momentum stretched (RSI {rsi:.0f}) — late entry risk")
    smc = snap.get("smc") or {}
    want = "sell_side_liquidity" if s > 0 else "buy_side_liquidity"
    near_opp = [lq for lq in smc.get("liquidity", []) if lq["type"] != want and lq["status"] == "resting"
                and 0 < s * (lq["price"] - entry) < abs(entry - stop)]
    if near_opp:
        counter.append(f"opposing liquidity within 1R at {near_opp[0]['price']:.6g} may cap the move")
    d = snap.get("derivatives") or {}
    if d.get("funding_extreme") and ((d.get("funding_rate") or 0) * s > 0):
        counter.append("funding extreme on the same side (crowded)")
    if regime in ("high_volatility",):
        counter.append("high-volatility regime: stops more likely to be hit by noise")
    chk("counter-arguments limited (≤2 opposing families)", len(oppose) <= 2, f"{len(oppose)} opposing")

    passed = all(c["passed"] for c in checks)
    score = (max(aligned, 0) * 40 + min(r1, 4) / 4 * 25 + min(len(support), 6) / 6 * 25
             - len(oppose) * 5 - len(counter) * 2)
    return {"passed": passed, "checks": checks, "counter_arguments": counter, "rr_tp1": r1,
            "rr_tp2": rr(entry, stop, tps[1], fee, slip_bps) if len(tps) > 1 else None,
            "rr_tp3": rr(entry, stop, tps[2], fee, slip_bps) if len(tps) > 2 else None,
            "supporting_families": support, "opposing_families": oppose,
            "quality_score": round(max(0.0, min(100.0, score)), 1)}


def evaluate_strategies(snap: dict, now_ms: int | None = None, only: list[str] | None = None,
                        stats: dict | None = None) -> list[dict]:
    out = []
    for sid, strat in STRATEGIES.items():
        if only and sid not in only:
            continue
        try:
            cands = strat.evaluate(snap)
        except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as e:
            out.append({"strategy": sid, "error": f"{type(e).__name__}: {e}"})
            continue
        for c in cands:
            if c.stage == "none":
                continue
            d = c.d()
            d["validation"] = validate(snap, d, now_ms, strategy_stats=(stats or {}).get(sid))
            out.append(d)
    return out
