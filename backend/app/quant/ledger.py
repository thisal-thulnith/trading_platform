"""Forecast Ledger: every forecaster (each agent, the quant model, the team) states P(price higher after H candles).
Each forecast is resolved against the real close, scored (Brier), calibrated and turned into a trust weight.

This is how the system becomes smarter over time without anyone deciding: forecasters that are right more often
than the base rate earn weight; those that are not fade toward zero. Weights start equal and need about 30 graded
forecasts before they move much (shrinkage), so a lucky streak can't take over.
"""
import time
from collections import defaultdict

from sqlalchemy import select

from app.data.timeframes import tf_ms
from app.db import session_factory
from app.logging import log
from app.models import Forecast

HORIZON = 12        # candles
PRIOR_N = 30        # graded forecasts before the record outweighs the prior
PRIOR_SLOPE = 0.6   # untested forecasters are assumed overconfident (LLM stated confidence is)
BUCKETS = [0, 0.2, 0.35, 0.45, 0.55, 0.65, 0.8, 1.0001]


def verdict_to_p(verdict, confidence: float | None = None) -> float:
    """Rule-based agents give a direction + confidence; express it as P(up)."""
    v = {"bullish": 1, "bearish": -1, "neutral": 0}.get(verdict, verdict) if isinstance(verdict, str) else (verdict or 0)
    c = min(max((confidence if confidence is not None else 55) / 100, 0.5), 0.9)
    return round(0.5 + v * (c - 0.5), 3)


async def record(session_id: str, symbol: str, tf: str, ref_price: float, items: list[dict],
                 regime: str | None = None) -> int:
    """items: [{source, p_up, brain}] — one row each; duplicates per (session, source) are ignored."""
    if not ref_price or not items:
        return 0
    now = int(time.time() * 1000)
    due = now + HORIZON * tf_ms(tf)
    async with session_factory()() as db:
        have = set((await db.execute(select(Forecast.source).where(Forecast.session_id == session_id))).scalars())
        n = 0
        for it in items:
            p = it.get("p_up")
            if p is None or it["source"] in have:
                continue
            db.add(Forecast(created_ms=now, session_id=session_id, source=it["source"], brain=it.get("brain", "rules"),
                            symbol=symbol, timeframe=tf, regime=regime, p_up=float(min(max(p, 0.01), 0.99)),
                            ref_price=ref_price, horizon_bars=HORIZON, due_ms=due))
            have.add(it["source"])
            n += 1
        await db.commit()
    return n


async def resolve(client, limit: int = 300) -> int:
    """Fill outcomes for forecasts whose horizon has passed, using the closed candle that contains due_ms."""
    now = int(time.time() * 1000)
    async with session_factory()() as db:
        rows = (await db.execute(select(Forecast).where(Forecast.outcome.is_(None), Forecast.due_ms <= now)
                                 .limit(limit))).scalars().all()
        groups: dict[tuple, list[Forecast]] = defaultdict(list)
        for f in rows:
            groups[(f.symbol, f.timeframe, f.due_ms // tf_ms(f.timeframe))].append(f)
        done = 0
        for (sym, tf, _), fs in groups.items():
            step = tf_ms(tf)
            due = fs[0].due_ms
            if due + step > now:  # the candle containing due_ms has not closed yet
                continue
            try:
                ks = await client.klines(sym, tf, "spot", limit=3, start_ms=due - step)
            except Exception as e:  # noqa: BLE001
                log.warning("ledger_resolve_failed", symbol=sym, error=str(e))
                continue
            k = next((x for x in ks if x["open_time"] <= due < x["open_time"] + step), None)
            if not k:
                continue
            for f in fs:
                f.exit_price = k["close"]
                f.outcome = int(k["close"] > f.ref_price)
                f.resolved_ms = now
                done += 1
        await db.commit()
    if done:
        log.info("ledger_resolved", n=done)
    return done


def _score(rows: list[tuple[float, int]], base: float) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    brier = sum((p - o) ** 2 for p, o in rows) / n
    ref = sum((base - o) ** 2 for _, o in rows) / n or 1e-9
    skill = 1 - brier / ref
    hits = sum(1 for p, o in rows if (p > 0.5 and o == 1) or (p < 0.5 and o == 0))
    calls = sum(1 for p, _ in rows if p != 0.5)
    sxy = sum((p - 0.5) * (o - 0.5) for p, o in rows)
    sxx = sum((p - 0.5) ** 2 for p, _ in rows)
    lam = PRIOR_N * 0.01
    slope = min(max((sxy + lam * PRIOR_SLOPE) / (sxx + lam), 0.0), 1.2)
    w_raw = max(0.0, 1 + 4 * skill)
    weight = (PRIOR_N * 1.0 + n * w_raw) / (PRIOR_N + n)
    mean_p = sum(p for p, _ in rows) / n
    return {"n": n, "brier": round(brier, 4), "brier_ref": round(ref, 4), "skill": round(skill, 3),
            "hit_rate": round(hits / calls, 3) if calls else None, "calls": calls,
            "bias": round(mean_p - base, 3), "slope": round(slope, 3), "weight": round(weight, 3)}


def _calibration(rows: list[tuple[float, int]]) -> list[dict]:
    out = []
    for lo, hi in zip(BUCKETS, BUCKETS[1:]):
        b = [(p, o) for p, o in rows if lo <= p < hi]
        if b:
            out.append({"lo": lo, "hi": min(hi, 1), "n": len(b), "mean_p": round(sum(p for p, _ in b) / len(b), 3),
                        "freq": round(sum(o for _, o in b) / len(b), 3)})
    return out


async def scores() -> dict:
    async with session_factory()() as db:
        rows = (await db.execute(select(Forecast))).scalars().all()
    resolved = [f for f in rows if f.outcome is not None]
    base = sum(f.outcome for f in resolved) / len(resolved) if resolved else 0.5
    by: dict[str, list] = defaultdict(list)
    for f in resolved:
        by[f.source].append((f.p_up, f.outcome))
    open_by: dict[str, int] = defaultdict(int)
    for f in rows:
        if f.outcome is None:
            open_by[f.source] += 1
    sources = sorted(set(by) | set(open_by))
    table = [{"source": s, **_score(by.get(s, []), base), "open": open_by.get(s, 0)} for s in sources]
    table.sort(key=lambda r: (-(r.get("skill") if r.get("n") else -9), r["source"]))
    team = by.get("team", [])
    return {"total": len(rows), "resolved": len(resolved), "open": len(rows) - len(resolved),
            "base_rate_up": round(base, 3), "horizon_candles": HORIZON, "sources": table,
            "calibration": {"team": _calibration(team), "all": _calibration([x for v in by.values() for x in v])},
            "recent": [{"source": f.source, "symbol": f.symbol, "tf": f.timeframe, "p_up": f.p_up,
                        "outcome": f.outcome, "created_ms": f.created_ms, "due_ms": f.due_ms}
                       for f in sorted(rows, key=lambda f: -f.created_ms)[:40]]}


_cache: dict = {"t": 0.0, "w": {}}


async def weights() -> dict[str, dict]:
    """{source: {weight, slope}} from the record (cached 2 min)."""
    if time.time() - _cache["t"] < 120:
        return _cache["w"]
    s = await scores()
    _cache["w"] = {r["source"]: {"weight": r.get("weight", 1.0), "slope": r.get("slope", PRIOR_SLOPE),
                                 "n": r.get("n", 0), "skill": r.get("skill")} for r in s["sources"]}
    _cache["t"] = time.time()
    return _cache["w"]


def combine(forecasts: list[dict], w: dict[str, dict]) -> dict:
    """Team probability = weighted average of calibrated probabilities (maths, not the loudest argument).

    Each raw p is shrunk toward 50% by the forecaster's measured calibration slope (untested: 0.6), then averaged
    with its earned weight. Returns the team p, each forecaster's contribution, and how much they agree.
    """
    parts = []
    for f in forecasts:
        if f.get("p_up") is None:
            continue
        info = w.get(f["source"], {})
        slope = info.get("slope", PRIOR_SLOPE)
        weight = info.get("weight", 1.0)
        p_cal = 0.5 + (f["p_up"] - 0.5) * slope
        parts.append({"source": f["source"], "p_raw": round(f["p_up"], 3), "p_cal": round(p_cal, 3),
                      "weight": round(weight, 3), "n": info.get("n", 0)})
    tw = sum(p["weight"] for p in parts)
    if not parts or tw <= 0:
        return {"p_up": 0.5, "parts": parts, "agreement": None}
    p = 0.5 + sum(x["weight"] * (x["p_cal"] - 0.5) for x in parts) / tw
    ups = sum(1 for x in parts if x["p_cal"] > 0.52)
    downs = sum(1 for x in parts if x["p_cal"] < 0.48)
    return {"p_up": round(p, 3), "parts": parts, "agreement": round(max(ups, downs) / len(parts), 2),
            "ups": ups, "downs": downs, "flat": len(parts) - ups - downs}
