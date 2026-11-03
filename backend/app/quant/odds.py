"""Setup odds: "in X of 10 similar past setups, price hit the target before the stop".

Evidence comes from two honest sources, never from an opinion:
- simulated trades in completed backtests (walk-forward, fees and slippage included), and
- live paper signals that reached a final outcome.
A setup is "similar" when strategy, direction and timeframe match; the regime is used when it has enough cases.
The interval is a Wilson 90% interval, so small samples show wide ranges instead of false precision.
"""
import math
import time
from collections import defaultdict

from sqlalchemy import select

from app.db import session_factory
from app.models import BacktestRun, Signal

MIN_N = 15
_cache: dict = {"t": 0.0, "rows": []}


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


async def _rows() -> list[dict]:
    if time.time() - _cache["t"] < 600:
        return _cache["rows"]
    rows: list[dict] = []
    async with session_factory()() as db:
        runs = (await db.execute(select(BacktestRun).where(BacktestRun.status == "done"))).scalars().all()
        seen = set()
        for r in sorted(runs, key=lambda r: r.created_at, reverse=True):
            tf = (r.config or {}).get("timeframe")
            for t in ((r.results or {}).get("scenarios", {}).get("base", {}).get("trade_list") or []):
                if not t.get("filled") or t.get("r") is None:
                    continue
                key = (t["symbol"], t["strategy"], t.get("entry_time"), tf)
                if key in seen:  # the same simulated trade appears in overlapping runs
                    continue
                seen.add(key)
                rows.append({"src": "backtest", "tf": tf, "strategy": t["strategy"], "direction": t["direction"],
                             "regime": t.get("regime"), "r": t["r"]})
        sigs = (await db.execute(select(Signal).where(Signal.r_multiple.is_not(None)))).scalars().all()
        for s in sigs:
            rows.append({"src": "live", "tf": s.timeframe, "strategy": s.strategy, "direction": s.direction,
                         "regime": (s.payload or {}).get("regime"), "r": s.r_multiple})
    _cache.update(t=time.time(), rows=rows)
    return rows


def _summ(rs: list[dict], basis: str) -> dict:
    n = len(rs)
    k = sum(1 for r in rs if r["r"] > 0)
    lo, hi = wilson(k, n)
    avg_r = sum(r["r"] for r in rs) / n if n else None
    return {"n": n, "wins": k, "win_rate": round(k / n, 3) if n else None, "lo": round(lo, 3), "hi": round(hi, 3),
            "of10": round(10 * k / n) if n else None, "lo10": round(10 * lo), "hi10": round(10 * hi),
            "avg_r": round(avg_r, 2) if avg_r is not None else None, "basis": basis,
            "live_n": sum(1 for r in rs if r["src"] == "live")}


async def setup_odds(strategy: str, direction: str, tf: str, regime: str | None = None) -> dict | None:
    rows = [r for r in await _rows() if r["strategy"] == strategy and r["direction"] == direction]
    exact = [r for r in rows if r["tf"] == tf]
    if regime:
        rg = [r for r in exact if r.get("regime") == regime]
        if len(rg) >= MIN_N:
            return _summ(rg, f"{strategy.replace('_', ' ')} {direction} on {tf} in a {regime} market")
    if len(exact) >= MIN_N:
        return _summ(exact, f"{strategy.replace('_', ' ')} {direction} on {tf}")
    if len(rows) >= MIN_N:
        return _summ(rows, f"{strategy.replace('_', ' ')} {direction}, all timeframes")
    return _summ(rows, "too few past cases") if rows else None


async def table() -> list[dict]:
    g: dict[tuple, list] = defaultdict(list)
    for r in await _rows():
        g[(r["strategy"], r["direction"], r["tf"])].append(r)
    out = [{"strategy": s, "direction": d, "tf": tf, **_summ(rs, "")} for (s, d, tf), rs in g.items()]
    return sorted(out, key=lambda x: (-(x["n"] >= MIN_N), -(x["avg_r"] or -9)))
