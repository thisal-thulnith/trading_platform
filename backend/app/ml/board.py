"""Signal board: every liquid coin scored by the machine (the unified engine), checked against the other methods
(the trend factor ranking and the 4h chart setups), with a confidence level that says how signals like it actually did.
The AI agents are NOT run here: they run only on the coin the user selects (they cost AI credits).

Confidence, honestly: the engine trades only its #1 coin a day, but its out-of-sample record (unified_oos.npz) has a
score for every coin every day. Ranking those days the same way and reading what each rank actually made gives the
record of "a signal ranked like this one": by rank bucket and side, on the unseen years 2025-26 (all years shown too).
  High    similar signals made at least +0.10R a trade on 2025-26
  Medium  they made between 0 and +0.10R
  Low     they lost money
It describes the past of signals like this one; it is not a probability for this trade. Part of it is the market:
in 2025-26 the market fell, so even low-ranked shorts made money; "vs the rest" (this rank minus ranks 11+ on the
same side) is the machine's own part.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from app.logging import log
from app.ml import forecast as fc
from app.ml import style as sty

BUCKETS = [(1, 1, "#1"), (2, 3, "#2–3"), (4, 10, "#4–10"), (11, 10_000, "#11+")]
MIN_N = 60
_cache: dict = {}


def _stats(r: np.ndarray) -> dict:
    r = r[np.isfinite(r)]
    if not len(r):
        return {"n": 0}
    return {"n": int(len(r)), "win_rate": round(float((r > 0).mean()), 3), "avg_r": round(float(r.mean()), 3)}


def calibration() -> dict | None:
    """What signals at each rank (and side) made, out of sample. Cached until the record changes."""
    from app.ml import unified as un

    rep = un.report()
    if not un.OOS.exists() or not rep or not rep.get("rule"):
        return None
    m = un.OOS.stat().st_mtime
    if _cache.get("m") == m:
        return _cache["cal"]
    z = np.load(un.OOS, allow_pickle=True)
    dates = pd.to_datetime(z["date"])
    P, R, atr = z["P"].astype(np.float64), z["R"].astype(np.float64), z["atr_pct"].astype(np.float64)
    S = un._scores(P, dates, rep["rule"]["score"]).astype(np.float64)
    S[~(atr <= un.MAX_ATR_PCT)] = np.nan
    ok = np.isfinite(S).any(axis=1)
    j = np.where(ok, np.nanargmax(np.where(np.isfinite(S), S, -np.inf), axis=1), 0)
    df = pd.DataFrame({"date": dates, "score": np.where(ok, S[np.arange(len(S)), j], np.nan),
                       "real": R[np.arange(len(R)), j], "side": [sty.PLANS[k][0] for k in j]})[ok]
    df["rank"] = df.groupby("date")["score"].rank(ascending=False, method="first")
    df["unseen"] = df["date"].dt.year.isin(fc.HOLDOUT_YEARS)
    out = {"buckets": [], "what": "Signals ranked like this, out of sample: 2025-26 never used to choose anything."}
    for lo, hi, label in BUCKETS:
        b = df[(df["rank"] >= lo) & (df["rank"] <= hi)]
        u = b[b["unseen"]]
        out["buckets"].append({"label": label, "lo": lo, "hi": hi, "unseen": _stats(u["real"].to_numpy()),
                               "all_years": _stats(b["real"].to_numpy()),
                               "by_side": {s: _stats(u.loc[u["side"] == s, "real"].to_numpy()) for s in ("LONG", "SHORT")}})
    _cache.update(m=m, cal=out)
    return out


def confidence(rank: int, side: str, cal: dict | None) -> dict:
    if not cal:
        return {"label": "Unknown", "basis": "no record yet"}
    b = next(x for x in cal["buckets"] if x["lo"] <= rank <= x["hi"])
    s = b["by_side"].get(side) or {}
    st, basis = (s, f"{side.lower()}s ranked {b['label']}") if s.get("n", 0) >= MIN_N else (b["unseen"], f"signals ranked {b['label']}")
    avg = st.get("avg_r")
    label = "Unknown" if avg is None else "High" if avg >= 0.10 else "Medium" if avg >= 0 else "Low"
    # what the machine's ranking added over the rest of the coins on the same side (the market's own part removed)
    rest = (cal["buckets"][-1]["by_side"].get(side) or {}).get("avg_r")
    vs_rest = None if avg is None or rest is None else round(avg - rest, 3)
    return {"label": label, "win_rate": st.get("win_rate"), "avg_r": avg, "n": st.get("n", 0), "basis": basis,
            "vs_rest_r": vs_rest}


def _trend_view(trend: str | None) -> str | None:
    t = (trend or "").lower()
    return "LONG" if "up" in t else "SHORT" if "down" in t else None


async def board(prices: dict[str, float]) -> dict:
    """Every coin the engine scores today, best plan first, with levels at the live price, the other methods' view,
    the agents' latest verdict (if one was asked for) and the confidence level."""
    from app.agents.scout import JOBS
    from app.ml import signal_test
    from app.ml import unified as un
    from app.quant import brain

    td = un.today()
    if not td:
        return {"available": False, "note": "The machine has not scored today yet."}
    cal = calibration()
    uc = un.candidates()
    picks = {p["symbol"] for p in (uc.get("picks") or [])} if uc.get("available") else set()
    try:
        q = await brain.get()
        trend = {r["symbol"]: r for r in (q or {}).get("ranks", [])}
    except Exception:  # noqa: BLE001 - the factor view is optional
        trend = {}
    setups = {r["symbol"]: r for r in (((JOBS.get("4h") or {}).get("result") or {}).get("rows") or [])}
    try:   # the 4h setups page's own picks (the same list the user sees on Lab → 4h setups) take precedence
        from app.agents import daily

        for p in (await daily.today("4h", 1000.0, False)).get("picks", []):
            setups[p["symbol"]] = {"best": {"direction": p["side"], "action": (p.get("signal") or {}).get("decision")}}
    except Exception as e:  # noqa: BLE001 - optional view
        log.warning("board_setups_failed", error=str(e))
    verdicts = signal_test.verdicts()
    from app.ml import live_status

    syms = [r["symbol"] for r in un.ranked()]
    bars = dict(zip(syms, await asyncio.gather(*(live_status.bars_1h(s) for s in syms)), strict=True))
    rows = []
    for r in un.ranked():
        sym = r["symbol"]
        row = td["rows"][sym]
        side, stop_atr, r_mult = un._parse(r["plan"])
        d = 1 if side == "LONG" else -1
        bl = bars.get(sym) or []
        # the same price and the same entry rule as the signal page (unified.plan_for): one signal, one set of levels
        price = float(bl[-1]["close"]) if bl else float(prices.get(sym) or row["close"])
        atr = row["atr"]
        entry, entry_type = un.entry_for(row["close"], atr, side, price)
        stop, target = entry - d * stop_atr * atr, entry + d * r_mult * stop_atr * atr
        tr = trend.get(sym) or {}
        st = setups.get(sym) or {}
        setup_side = (st.get("best") or {}).get("direction") or {"bullish": "LONG", "bearish": "SHORT"}.get(st.get("direction") or "")
        views = {"machine": side, "trend": _trend_view(tr.get("trend")), "setup_4h": setup_side}
        v = verdicts.get(sym)
        agents = None
        if v:
            age_h = (pd.Timestamp.now("UTC") - pd.Timestamp(v["at"])).total_seconds() / 3600
            if age_h <= signal_test.AGENT_FRESH_H:
                agents = {"action": v["action"], "confidence": v.get("confidence"), "age_hours": round(age_h, 1)}
        others = [x for k, x in views.items() if k != "machine" and x]
        clear = bool(r["pred_r"] > 0 or sym in picks)   # same rule as unified.plan_for: no side claimed otherwise
        since_s = int((pd.Timestamp(row["date"]) + pd.Timedelta(days=1)).timestamp())
        c0 = float(row["close"])
        live_state = live_status.judge(side, c0, c0 - d * stop_atr * atr, c0 + d * r_mult * stop_atr * atr,
                                       bars.get(sym) or [], since_s, price) if clear else None
        since = d * (price - row["close"])
        rows.append({
            "rank": r["rank"], "symbol": sym, "side": side, "plan": r["plan"], "pick": sym in picks, "clear": clear,
            # live result since the model's daily close, as if the signal had been taken there (display only)
            "live_pct": round(d * (price / row["close"] - 1) * 100, 2) if row["close"] else None,
            "live_r": round(since / (stop_atr * atr), 3) if atr else None,
            "expected_r": r["pred_r"], "score": r["score"], "price": price, "model_close": row["close"],
            "move_since_model_pct": round((price / row["close"] - 1) * 100, 2) if row["close"] else None,
            "entry": float(f"{entry:.6g}"), "entry_type": entry_type, "stop": float(f"{stop:.6g}"), "target": float(f"{target:.6g}"),
            "stop_pct": round((stop / entry - 1) * 100, 2), "target_pct": round((target / entry - 1) * 100, 2),
            "atr_pct": row["atr_pct"], "confidence": confidence(r["rank"], side, cal),
            "trend": {"view": views["trend"], "label": tr.get("trend"), "rank": tr.get("rank"), "of": tr.get("of")},
            "setup_4h": {"view": setup_side, "action": (st.get("best") or {}).get("action")},
            "live_state": live_state,
            "agree": sum(1 for x in others if x == side), "disagree": sum(1 for x in others if x != side),
            "checked": len(others), "agents": agents})
    return {"available": True, "as_of": td["as_of"], "market": td.get("market"), "rows": rows, "calibration": cal,
            "setups_ready": bool(setups), "note": __doc__.split("\n\n")[1].strip()}
