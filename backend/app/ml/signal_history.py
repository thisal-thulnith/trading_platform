"""Signal history: what the platform said on any past day, and how it turned out.

Two honest sources, kept apart:
- live: the signals actually saved that day by the signal test (frozen when given, graded on live candles), from
  2026-11-23 on
- record: the machine's ranking that day from its out-of-sample record (unified_oos.npz: each day predicted by models
  that never saw it), 2021 to the last training, with the result each coin's plan actually made (R after fees; open
  if the 14 days are not over yet). It is what the machine WOULD have said that day, not a saved message.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db import session_factory
from app.ml import style as sty
from app.models import SignalTest

TOP = 10
_cache: dict = {}


def _snap_dir():
    from app.ml import forecast as fc

    d = fc.MODELS / "daily_rankings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_snapshot() -> str | None:
    """Keep today's ranking (top 10 + the pick) with its levels, so the day can be looked up later. Called after every
    daily re-score; the out-of-sample record only reaches the last training, this fills the days after it."""
    import json

    from app.ml import unified as un

    td = un.today()
    if not td:
        return None
    uc = un.candidates()
    picks = {p["symbol"] for p in (uc.get("picks") or [])} if uc.get("available") else set()
    rows = []
    for r in un.ranked()[:TOP]:
        row = td["rows"][r["symbol"]]
        side, stop_atr, r_mult = un._parse(r["plan"])
        rows.append({"rank": r["rank"], "symbol": r["symbol"], "side": side, "plan": r["plan"], "expected_r": r["pred_r"],
                     "pick": r["symbol"] in picks, "clear": bool(r["pred_r"] > 0 or r["symbol"] in picks),
                     "close": row["close"], "atr": row["atr"], "stop_atr": stop_atr, "r_mult": r_mult, "date": row["date"]})
    out = {"as_of": td["as_of"], "of": len(td["rows"]), "rows": rows, "saved_at": datetime.now(UTC).isoformat()}
    (_snap_dir() / f"{td['as_of']}.json").write_text(json.dumps(out))
    return td["as_of"]


async def _from_snapshot(on: date) -> list[dict]:
    """A saved day, graded on the 1h candles since its signal time (stop first if a candle touches both)."""
    import json

    from app.ml import live_status

    f = _snap_dir() / f"{on.isoformat()}.json"
    if not f.exists():
        return []
    snap = json.loads(f.read_text())
    out = []
    for r in snap["rows"]:
        d = 1 if r["side"] == "LONG" else -1
        entry, risk = r["close"], r["stop_atr"] * r["atr"]
        stop, target = entry - d * risk, entry + d * r["r_mult"] * risk
        since = int((pd.Timestamp(r["date"]) + pd.Timedelta(days=1)).timestamp())
        bars = await live_status.bars_1h(r["symbol"])
        st = live_status.judge(r["side"], entry, stop, target, bars, since)
        after = [b for b in bars if b["time"] >= since]
        now_r = (d * (after[-1]["close"] - entry) / risk) if after and risk else None
        res = r["r_mult"] if st["state"] == "target_hit" else -1.0 if st["state"] == "stop_hit" else None
        out.append({"rank": r["rank"], "symbol": r["symbol"], "side": r["side"], "plan": r["plan"], "pick": r["pick"],
                    "clear": r["clear"], "expected_r": r["expected_r"], "result_r": res,
                    "outcome": "won" if res and res > 0 else "lost" if res is not None else None,
                    "now_r": None if now_r is None else round(now_r, 2), "of": snap["of"], "source": "saved"})
    return out


def _record() -> dict | None:
    from app.ml import unified as un

    rep = un.report()
    if not un.OOS.exists() or not rep or not rep.get("rule"):
        return None
    m = un.OOS.stat().st_mtime
    if _cache.get("m") != m:
        z = np.load(un.OOS, allow_pickle=True)
        dates = pd.to_datetime(z["date"])
        P, R, atr = z["P"].astype(np.float64), z["R"].astype(np.float64), z["atr_pct"].astype(np.float64)
        S = un._scores(P, dates, rep["rule"]["score"]).astype(np.float64)
        S[~(atr <= un.MAX_ATR_PCT)] = np.nan
        ok = np.isfinite(S).any(axis=1)
        j = np.where(ok, np.nanargmax(np.where(np.isfinite(S), S, -np.inf), axis=1), 0)
        idx = np.arange(len(S))
        df = pd.DataFrame({"date": dates.normalize(), "symbol": z["symbol"].astype(str), "plan": j,
                           "score": np.where(ok, S[idx, j], np.nan), "pred": P[idx, j], "real": R[idx, j]})[ok]
        df["rank"] = df.groupby("date")["score"].rank(ascending=False, method="first").astype(int)
        _cache.update(m=m, df=df.sort_values(["date", "rank"]))
    return _cache


def available() -> dict:
    rec = _record()
    if rec is None:
        return {"record_from": None, "record_to": None}
    d = rec["df"]["date"]
    return {"record_from": str(d.min().date()), "record_to": str(d.max().date())}


async def day(on: date) -> dict:
    """Everything the platform said on `on` (UTC), with outcomes."""
    from app.ml.signal_test import _public

    start = datetime(on.year, on.month, on.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    async with session_factory()() as db:
        rows = (await db.execute(select(SignalTest).where(SignalTest.created_at >= start.replace(tzinfo=None),
                                                         SignalTest.created_at < end.replace(tzinfo=None))
                                 .order_by(SignalTest.created_at))).scalars().all()
    live = [_public(r) for r in rows]
    rec = _record()
    machine = []
    if rec is not None:
        x = rec["df"][rec["df"]["date"] == pd.Timestamp(on)]
        for r in x.head(TOP).itertuples():
            side, stop_atr, r_mult = sty.PLANS[r.plan]
            done = np.isfinite(r.real)
            machine.append({"rank": int(r.rank), "symbol": r.symbol, "side": side, "plan": sty.PIDS[r.plan],
                            "pick": int(r.rank) == 1, "clear": bool(r.pred > 0 or r.rank == 1),
                            "expected_r": round(float(r.pred), 3), "result_r": round(float(r.real), 3) if done else None,
                            "outcome": None if not done else "won" if r.real > 0 else "lost",
                            "of": int((rec["df"]["date"] == pd.Timestamp(on)).sum())})
    if not machine:   # after the last training: the day's saved ranking, graded live
        machine = await _from_snapshot(on)
    return {"date": on.isoformat(), "live": live, "machine": machine, **available(),
            "note": "Live = saved that day and graded on live prices. Machine record = what the machine would have "
                    "ranked that day (out of sample), and what each plan actually made; only #1 is a trade."}
