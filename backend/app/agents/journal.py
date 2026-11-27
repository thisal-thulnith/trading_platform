"""Desk journal: how the desk learns from its own calls.

Every finished research session with a valid plan is recorded (symbol, timeframe, the decision, the plan's side, entry,
stop, target and reward/risk, the two validator scores, AI or rules). Every 5 minutes `grade()` walks the closed candles
after each open entry and writes the outcome: target first (+reward/risk R), stop first (-1R; a candle that touches both
counts as the stop), or expired after the timeframe's holding window (the R at the last close).

Plans of WAIT decisions are graded too, as counterfactuals: they tell whether waiting saved money or missed a winner.
`stats()` summarises the record — overall, per coin and timeframe, taken vs waited, by research grade — and the agents
read it through the `desk_record` tool, so the next call is made knowing how calls like it worked out.

Each row also keeps the Head Trader's confidence and every AI lead's view and confidence (`leads`), so
`app/agents/calibration.py` can check whether the desk's confidence means what it says and which leads read the market
well. Rows written before that simply lack those fields.
"""
import json
import time
import uuid
from pathlib import Path

import numpy as np

from app.logging import log

PATH = Path("data/desk_journal.jsonl")
MAX_BARS = {"15m": 96, "1h": 72, "4h": 42, "1d": 30}
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
LEADS = ("researcher", "technician", "flow", "fundamental", "quant", "skeptic", "risk", "head")


def _load() -> list[dict]:
    if not PATH.exists():
        return []
    out = []
    for line in PATH.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _save(rows: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(PATH)


def rows() -> list[dict]:
    """Every journal row, oldest first (read-only view for calibration and reports)."""
    return _load()


def _num(x) -> float | None:
    return round(float(x), 3) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _leads(s: dict) -> dict:
    """Each AI lead's final view and confidence; leads without a view are left out."""
    out = {}
    for a in s.get("agents") or []:
        if a.get("id") in LEADS and a.get("view") in ("bullish", "bearish", "neutral"):
            out[a["id"]] = {"view": a["view"], "confidence": _num(a.get("confidence"))}
    return out


def record(s: dict) -> dict | None:
    """Add one desk call. One entry per coin, timeframe and side per candle, so re-running the desk does not inflate
    the record."""
    p, d, v = s.get("plan") or {}, s.get("decision") or {}, s.get("validation") or {}
    if not p.get("ok") or "stop" not in p:
        return None
    now = int(time.time() * 1000)
    bucket = now // TF_MS.get(s["tf"], 3_600_000)
    rows = _load()
    if any(r["symbol"] == p["symbol"] and r["tf"] == s["tf"] and r["side"] == p["side"] and r["bucket"] == bucket
           for r in rows):
        return None
    row = {"id": uuid.uuid4().hex[:10], "ts": now, "bucket": bucket, "symbol": p["symbol"], "tf": s["tf"],
           "decision": d.get("action", "WAIT"), "side": p["side"], "entry": p["entry"], "stop": p["stop"],
           "target": p["target"], "rr": p.get("rr_net"), "brain": s.get("brain"),
           "research_score": v.get("score"), "evidence_score": v.get("evidence_score"), "gate": v.get("gate"),
           "confidence": _num(d.get("confidence")),
           # what the Head Trader called before any guard turned it into WAIT
           "head_action": p["side"] if d.get("guard") else d.get("action", "WAIT"), "leads": _leads(s),
           "status": "open", "outcome": None, "r": None, "closed_ts": None}
    rows.append(row)
    _save(rows)
    return row


def outcome(row: dict, bars: list[dict]) -> dict | None:
    """First touch of the stop or the target in the closed candles after the call (bars: dicts with open_time, high,
    low, close), or expiry after the holding window. None while still open."""
    d = 1 if row["side"] == "LONG" else -1
    risk = abs(row["entry"] - row["stop"])
    after = [b for b in bars if b["open_time"] >= row["ts"] - TF_MS.get(row["tf"], 0) + 1][: MAX_BARS.get(row["tf"], 72)]
    for b in after:
        hit_stop = (b["low"] <= row["stop"]) if d == 1 else (b["high"] >= row["stop"])
        hit_tgt = (b["high"] >= row["target"]) if d == 1 else (b["low"] <= row["target"])
        if hit_stop:  # both in one candle: assume the worse came first
            return {"outcome": "stop", "r": -1.0, "closed_ts": b["open_time"]}
        if hit_tgt:
            return {"outcome": "target", "r": round(float(row.get("rr") or abs(row["target"] - row["entry"]) / risk), 2),
                    "closed_ts": b["open_time"]}
    if len(after) >= MAX_BARS.get(row["tf"], 72):
        last = after[-1]["close"]
        return {"outcome": "expired", "r": round((last - row["entry"]) * d / risk, 2) if risk else 0.0,
                "closed_ts": after[-1]["open_time"]}
    return None


async def grade() -> int:
    """Grade the open entries from the stored closed candles. Returns how many were closed."""
    from app.data.hub import now_ms
    from app.data.store import closed_only, load_candles
    from app.db import session_factory

    rows = _load()
    todo = [r for r in rows if r["status"] == "open"]
    if not todo:
        return 0
    done = 0
    async with session_factory()() as db:
        for r in todo:
            try:
                df = closed_only(await load_candles(db, r["symbol"], r["tf"], limit=MAX_BARS.get(r["tf"], 72) + 5,
                                                    start_ms=r["ts"] - TF_MS.get(r["tf"], 0)), r["tf"], now_ms())
            except Exception as e:  # noqa: BLE001 - one coin's data must not stop the grading
                log.warning("journal_grade_failed", symbol=r["symbol"], error=str(e))
                continue
            res = outcome(r, df[["open_time", "high", "low", "close"]].to_dict("records") if len(df) else [])
            if res:
                r.update(status="closed", **res)
                done += 1
    if done:
        _save(rows)
    return done


def _summ(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["status"] == "closed"]
    rs = np.array([r["r"] for r in closed], dtype=float) if closed else np.array([])
    return {"calls": len(rows), "graded": len(closed), "open": len(rows) - len(closed),
            "target_first": sum(r["outcome"] == "target" for r in closed),
            "stop_first": sum(r["outcome"] == "stop" for r in closed),
            "expired": sum(r["outcome"] == "expired" for r in closed),
            "win_rate": round(float((rs > 0).mean()), 3) if len(rs) else None,
            "avg_r": round(float(rs.mean()), 2) if len(rs) else None}


def stats(symbol: str | None = None, tf: str | None = None) -> dict:
    rows = _load()
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol]
    if tf:
        rows = [r for r in rows if r["tf"] == tf]
    taken = [r for r in rows if r["decision"] in ("LONG", "SHORT")]
    waited = [r for r in rows if r["decision"] == "WAIT"]
    good = [r for r in rows if (r.get("evidence_score") or 0) >= 70]
    weak = [r for r in rows if (r.get("evidence_score") or 0) < 70]
    out = {"scope": f"{symbol or 'all coins'} · {tf or 'all timeframes'}", "all": _summ(rows), "taken": _summ(taken),
           "waited_counterfactual": _summ(waited), "evidence_70_plus": _summ(good), "evidence_below_70": _summ(weak),
           "recent": [{k: r[k] for k in ("symbol", "tf", "decision", "side", "outcome", "r")} for r in rows[-6:]]}
    n = out["all"]["graded"]
    out["note"] = ("No graded calls yet: the record starts with the first call whose stop or target is reached."
                   if not n else f"{n} graded calls; judge the desk on 30+." if n < 30 else f"{n} graded calls.")
    return out
