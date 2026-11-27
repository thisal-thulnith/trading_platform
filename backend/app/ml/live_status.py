"""Is a signal still valid right now? Signals are made once a day (the machine) or a few times a day (the 4h setups),
but the price keeps moving: by the time you look, the target or the stop may already have been hit, or the price may
have run so far that entering now is a different, worse trade. This checks every signal against the 1h candles since
it was given, so no page says "take it" for a trade that is already over.

States (checked in this order; a candle touching both the stop and the target counts as the stop, as in the backtest):
  stop_hit     the stop was touched after the signal: it failed, skip it
  target_hit   the take profit was touched: it already worked, too late, don't chase
  ran_away     the price already went half the way or more to the target: entering now is a worse trade, skip or wait
  valid        neither level touched and the price is still near the entry
"""
from __future__ import annotations

import asyncio
import time

from app.logging import log

CACHE_S = 30
RAN_AWAY = 0.5                 # share of the way from the entry to the target
_cache: dict[str, tuple[float, list[dict]]] = {}
_sem = asyncio.Semaphore(6)


async def bars_1h(symbol: str) -> list[dict]:
    """The last ~500 closed-or-live 1h candles (cached 30 seconds per coin)."""
    hit = _cache.get(symbol)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    from app.api.market import candles

    async with _sem:
        try:
            bars = (await candles(symbol, "1h", 500, None))["candles"]
        except Exception as e:  # noqa: BLE001 - no candles: the check says unknown, never blocks a page
            log.warning("live_status_candles_failed", symbol=symbol, error=str(e))
            bars = []
    _cache[symbol] = (time.time(), bars)
    return bars


def judge(side: str, entry: float, stop: float, target: float, bars: list[dict], since_s: int,
          price: float | None = None) -> dict:
    """Pure: the state of one signal from the candles that opened at or after `since_s`."""
    d = 1 if side == "LONG" else -1
    after = [b for b in bars if b["time"] >= since_s]
    if not bars:
        return {"state": "unknown", "text": "Live check unavailable (no recent candles)."}
    for b in after:
        if (b["low"] <= stop) if d == 1 else (b["high"] >= stop):
            return {"state": "stop_hit", "at": b["time"],
                    "text": "The stop was already hit after this signal: it failed. Skip it."}
        if (b["high"] >= target) if d == 1 else (b["low"] <= target):
            return {"state": "target_hit", "at": b["time"],
                    "text": "The take profit was already hit after this signal: it worked, but it's too late now. Don't chase it."}
    px = price if price is not None else (after[-1]["close"] if after else bars[-1]["close"])
    way = d * (px - entry) / abs(target - entry) if target != entry else 0.0
    if way >= RAN_AWAY:
        return {"state": "ran_away", "progress": round(way, 2),
                "text": f"The price already went {way * 100:.0f}% of the way to the take profit: entering now is a worse "
                        f"trade. Skip it or wait for a pull-back."}
    return {"state": "valid", "progress": round(way, 2),
            "text": "Still valid: neither the stop nor the take profit has been touched since the signal."}


def unified_levels(plan: dict) -> tuple[float, float, float, int] | None:
    """The machine's signal as tested: entered at the model's daily close, levels from that close; given when that
    daily candle closed (the next day 00:00 UTC)."""
    from app.ml import unified as un

    row = (un.today() or {}).get("rows", {}).get(plan.get("symbol"))
    if not row or not plan.get("plan_id"):
        return None
    side, stop_atr, r_mult = un._parse(plan["plan_id"])
    d = 1 if side == "LONG" else -1
    close, atr = float(row["close"]), float(row["atr"])
    import pandas as pd

    since = int((pd.Timestamp(row["date"]) + pd.Timedelta(days=1)).timestamp())
    return close, close - d * stop_atr * atr, close + d * r_mult * stop_atr * atr, since


async def for_unified(plan: dict) -> dict | None:
    lv = unified_levels(plan)
    if lv is None:
        return None
    entry, stop, target, since = lv
    st = judge(plan["side"], entry, stop, target, await bars_1h(plan["symbol"]), since, float(plan.get("price") or entry))
    return {**st, "since": since, "entry": entry, "stop": float(f"{stop:.6g}"), "target": float(f"{target:.6g}")}


async def apply(plan: dict) -> dict:
    """Attach the live state to a unified plan; a signal that is over (or ran away) can't say TRADE."""
    if not plan.get("covered") or plan.get("engine") != "unified":
        return plan
    st = await for_unified(plan)
    if not st:
        return plan
    plan["live_state"] = st
    if st["state"] in ("stop_hit", "target_hit", "ran_away"):
        plan["status"] = "watch"
        plan["gates"] = [*plan.get("gates", []), st["text"]]
        plan["fast_gates"] = [*plan.get("fast_gates", []), st["text"]]
    return plan
