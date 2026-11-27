"""Signal test: a forward test of "Today's signal" on live prices.

Every signal is saved at the moment it is given, with its levels frozen (entry, stop, TP1, TP2 and the fast plan's
stop and target), then graded on the live Binance 1h candles that come AFTER it, with the backtest's rules:
- market entry: filled at the saved price; limit entry: filled when a candle reaches it within 48 hours, else "not filled"
- main plan: one stop and one take profit for the whole position (stop -> -1R, take profit -> +the plan's R), up to 14
  days; the old fixed plan (23-24 Nov rows only) closed half at TP1 and moved the stop to entry
- fast (the tight-stop option): one stop, one target; 14 days for the unified engine (its plans are 14-day plans),
  5 days for the old fixed plan
- a candle touching both the stop and a target counts as the stop (we cannot know which came first), and on the candle
  that fills a limit only the stop is checked
- fees 0.1% round trip, as in the backtest

What is saved: each day the platform's top LONG and top SHORT (whatever their status: "trade" or "watch only"), plus
any plan the user adds. Nothing is ever placed on an exchange: this is a paper record.

Accuracy is reported as the share of finished signals that made money, the average R (1R = the loss at the stop) and
the dollars at the safe leverage for the saved margin, next to what the backtest expected for the same signals.
"""
from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import session_factory
from app.logging import log
from app.ml import signal_plan as sp
from app.ml import style as sty
from app.models import SignalTest

FILL_HOURS = 48
MILESTONE = 10
AGENT_FRESH_H = 24   # an agent verdict counts for a signal only if it was given within 24h BEFORE or after the signal
#                      while the trade had not filled yet (no hindsight: the agents must not see the outcome forming)
HOUR = 3600
_last = {"t": 0.0}


# ------------------------------------------------------------------------------------------- grading (pure)

def _walk(bars: list[dict], i0: int, fill_s: int, e: float, stop: float, tp1: float, tp2: float | None, side: str,
          hours: int, now_s: int, fill_bar_stop_only: bool) -> dict:
    """Follow one filled trade over the 1h candles from index i0. Returns state open|closed, outcome, R (after fees),
    the time it closed, and the R it would have now (for open trades)."""
    d = 1 if side == "LONG" else -1
    sd = abs(e - stop)
    fee_r = sp.FEE * e / sd
    half = tp2 is not None
    end_s = fill_s + hours * HOUR
    banked, phase, last = 0.0, 0, e
    cur_stop = stop
    for k in range(i0, len(bars)):
        b = bars[k]
        if b["time"] >= end_s:
            break
        hi, lo, last = b["high"], b["low"], b["close"]
        adverse = (lo <= cur_stop) if d == 1 else (hi >= cur_stop)
        only_stop = fill_bar_stop_only and k == i0
        if phase == 0:
            if adverse:
                return {"state": "closed", "outcome": "stop", "r": -1.0 - fee_r, "closed_s": b["time"]}
            if only_stop:
                continue
            if (hi >= tp1) if d == 1 else (lo <= tp1):
                if not half:
                    return {"state": "closed", "outcome": "target", "r": abs(tp1 - e) / sd - fee_r, "closed_s": b["time"]}
                banked, phase, cur_stop = 0.5 * abs(tp1 - e) / sd, 1, e
                if (lo <= e) if d == 1 else (hi >= e):
                    return {"state": "closed", "outcome": "tp1_then_entry", "r": banked - fee_r, "closed_s": b["time"]}
                if (hi >= tp2) if d == 1 else (lo <= tp2):
                    return {"state": "closed", "outcome": "tp2", "r": banked + 0.5 * abs(tp2 - e) / sd - fee_r,
                            "closed_s": b["time"]}
        else:
            if adverse:
                return {"state": "closed", "outcome": "tp1_then_entry", "r": banked - fee_r, "closed_s": b["time"]}
            if (hi >= tp2) if d == 1 else (lo <= tp2):
                return {"state": "closed", "outcome": "tp2", "r": banked + 0.5 * abs(tp2 - e) / sd - fee_r,
                        "closed_s": b["time"]}
    left = 0.5 if phase == 1 else 1.0
    r_now = banked + left * d * (last - e) / sd - fee_r
    window_done = any(b["time"] >= end_s for b in bars) or now_s >= end_s + HOUR
    if window_done:
        return {"state": "closed", "outcome": "timeout", "r": r_now, "closed_s": end_s}
    return {"state": "open", "outcome": "tp1_hit" if phase == 1 else None, "r": r_now, "closed_s": None}


def grade(sig: dict, bars: list[dict], now_s: int) -> dict:
    """Grade one saved signal (dict with created_s, entry_type, entry, side, stop, tp1, tp2, fast_stop, fast_tp) on the
    1h candles ({time (open, seconds), high, low, close}). Pure: the same candles always give the same answer."""
    t0, side, e = sig["created_s"], sig["side"], sig["entry"]
    d = 1 if side == "LONG" else -1
    after = [b for b in bars if b["time"] >= t0]      # only candles that opened after the signal was given
    out: dict = {"last_price": (after or bars)[-1]["close"] if bars else None}   # for display only
    fill_i, fill_s = None, None
    if sig["entry_type"] == "market":
        fill_i, fill_s = 0, t0
    else:
        for i, b in enumerate(after):
            if b["time"] >= t0 + FILL_HOURS * HOUR:
                break
            if (b["low"] <= e) if d == 1 else (b["high"] >= e):
                fill_i, fill_s = i, b["time"]
                break
    if fill_i is None:
        expired = now_s >= t0 + FILL_HOURS * HOUR
        st = "not_filled" if expired else "waiting"
        return {**out, "state": st, "fast_state": st, "filled_s": None,
                "swing": None, "fast": None}
    limit = sig["entry_type"] == "limit"
    if not after:
        return {**out, "state": "open", "fast_state": "open", "filled_s": fill_s,
                "swing": {"state": "open", "outcome": None, "r": -sp.FEE * e / abs(e - sig["stop"]), "closed_s": None},
                "fast": {"state": "open", "outcome": None, "r": -sp.FEE * e / abs(e - sig["fast_stop"]), "closed_s": None}}
    swing = _walk(after, fill_i, fill_s, e, sig["stop"], sig["tp1"], sig["tp2"], side, sp.PLANS["swing"]["days"] * 24,
                  now_s, limit)
    fast_days = sig.get("fast_days") or sp.PLANS["fast"]["days"]
    fast = _walk(after, fill_i, fill_s, e, sig["fast_stop"], sig["fast_tp"], None, side, fast_days * 24, now_s, limit)
    return {**out, "state": swing["state"], "fast_state": fast["state"], "filled_s": fill_s, "swing": swing, "fast": fast}


# ------------------------------------------------------------------------------------------- saving signals

def _row_from_plan(plan: dict, source: str, key: str) -> SignalTest:
    sw, fa = plan["plans"]["swing"], plan["plans"]["fast"]
    exp = lambda p: {k: p["tested"]["holdout"].get(k) for k in ("trades", "win_rate", "avg_r")} | {  # noqa: E731
        "verdict": p["tested"]["verdict"]}
    return SignalTest(
        dedupe_key=key, source=source, as_of=plan["as_of"], symbol=plan["symbol"], side=plan["side"],
        status=plan["status"], gates=plan["gates"], entry_type=plan["entry_type"], entry=sw["entry"], stop=sw["stop"],
        tp1=sw["tp1"], tp2=sw["tp2"] if sw["tp2"] is not None else sw["tp1"], fast_stop=fa["stop"], fast_tp=fa["tp1"],
        margin=plan["margin"],
        safe_lev=float(max(1, min(sw["leverage"]["safe_max"], 20))),
        fast_safe_lev=float(max(1, min(fa["leverage"]["safe_max"], 20))),
        payload={"engine": plan.get("engine", "fixed"), "plan_id": plan.get("plan_id"),
                 "single_target": sw["tp2"] is None, "predicted_r": sw.get("predicted_r"),
                 "expected": exp(sw), "fast_expected": exp(fa), "fast_status": "trade" if not plan["fast_gates"] else "watch",
                 "score": plan["score"], "rank": plan["rank"], "of": plan["of"], "tier": plan["tier"],
                 "price_at_signal": plan["price"], "model_close": plan["model_close"], "market": plan["market"],
                 "notes": plan["notes"], "stop_pct": sw["leverage"]["stop_pct"],
                 "fast_stop_pct": fa["leverage"]["stop_pct"]})


# ------------------------------------------------------------------------------------------- the AI agents' verdicts
# The machine (unified engine) and the AI agents (research desk) analyse separately; their calls are kept apart and
# joined only here, so each can be scored on its own and together: machine alone, machine when the agents agreed,
# machine when the agents said wait or the other side.

def _verdicts_path():
    from app.ml import forecast as fc

    return fc.MODELS / "agent_verdicts.json"


def verdicts() -> dict:
    p = _verdicts_path()
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def verdict_from_session(s: dict) -> dict | None:
    """The desk's final call in a small, stable shape."""
    d, p = s.get("decision") or {}, s.get("plan") or {}
    if not d.get("action"):
        return None
    return {"action": d["action"], "side": d.get("plan_side") or p.get("side"),
            "confidence": d.get("confidence_calibrated", d.get("confidence")), "summary": (d.get("summary") or "")[:700],
            "conditions": d.get("conditions") or [], "invalidation": d.get("invalidation"), "by": d.get("by"),
            "entry": p.get("entry"), "stop": p.get("stop"), "target": p.get("target"), "tf": s.get("tf"),
            "session": s.get("id"), "model": s.get("model"), "at": datetime.now(UTC).isoformat()}


def stance(machine_side: str | None, v: dict | None) -> str:
    """agree | wait | against | none"""
    if not v or not machine_side:
        return "none"
    if v["action"] == machine_side:
        return "agree"
    if v["action"] in ("LONG", "SHORT"):
        return "against"
    return "wait"


def _fresh(v: dict, created: datetime, state: str) -> bool:
    at = datetime.fromisoformat(v["at"])
    created = created if created.tzinfo else created.replace(tzinfo=UTC)
    # within 24h of the signal, and either before it or while the trade had not filled (no hindsight)
    return abs(at - created) <= timedelta(hours=AGENT_FRESH_H) and (at <= created or state == "waiting")


async def save_agent_verdict(s: dict) -> dict | None:
    """Called when a desk session finishes: keep the coin's latest verdict, and attach it to that coin's engine signals
    when it came in time (see AGENT_FRESH_H)."""
    v = verdict_from_session(s)
    if not v:
        return None
    sym = s.get("symbol")
    allv = verdicts()
    allv[sym] = v
    p = _verdicts_path()
    p.write_text(json.dumps(allv))
    async with session_factory()() as db:
        rows = (await db.execute(select(SignalTest).where(SignalTest.symbol == sym, SignalTest.source == "auto")))
        for row in rows.scalars().all():
            if (row.payload or {}).get("agents") is None and _fresh(v, row.created_at, row.state):
                row.payload = {**(row.payload or {}), "agents": v, "agent_stance": stance(row.side, v)}
        await db.commit()
    log.info("agent_verdict_saved", symbol=sym, action=v["action"])
    return v


async def _live(symbol: str) -> dict | None:
    from app.api.agent import _live as live

    return await live(symbol)


def _plan(symbol: str, side: str | None, margin: float, live: dict) -> dict:
    """The unified engine's plan when it covers the coin, else the fixed plan."""
    from app.ml import unified as un

    p = un.plan_for(symbol, side, margin, live)
    return p if p.get("covered") else sp.plan_for(symbol, side, margin, live)


async def record_today(margin: float = 100.0) -> list[str]:
    """Save the unified engine's picks once per model day (its top-k; the top one even on a no-trade day, so the test
    keeps counting). Falls back to the fixed plan's top LONG and SHORT. Needs a live price (no stale entries)."""
    from app.ml import unified as un

    uc = un.candidates()
    if uc.get("available"):
        chosen = uc["picks"] or uc["top"][:1]
        saved = []
        async with session_factory()() as db:
            rows = (un.today() or {}).get("rows", {})
            for x in chosen:
                # key on the coin's own model day (some coins lag the newest day), and never stack a second copy of a
                # trade that is still waiting or open: one idea counts once
                day = rows.get(x["symbol"], {}).get("date") or uc["as_of"]
                key = f"unified:{day}:{x['symbol']}"
                if (await db.execute(select(SignalTest.id).where(SignalTest.dedupe_key == key))).first():
                    continue
                if (await db.execute(select(SignalTest.id).where(
                        SignalTest.source == "auto", SignalTest.symbol == x["symbol"],
                        SignalTest.state.in_(("waiting", "open"))))).first():
                    continue
                live = await _live(x["symbol"])
                if not live:
                    continue
                plan = un.plan_for(x["symbol"], None, margin, live)
                if not plan.get("covered"):
                    continue
                row = _row_from_plan(plan, "auto", key)
                v = verdicts().get(x["symbol"])
                if v and _fresh(v, datetime.now(UTC), "waiting"):
                    row.payload = {**row.payload, "agents": v, "agent_stance": stance(row.side, v)}
                db.add(row)
                saved.append(f"{x['symbol']} {plan['plan_id']}")
            await db.commit()
        if saved:
            log.info("signal_test_recorded", engine="unified", signals=saved)
        return saved
    cand = sp.candidates()
    if not cand.get("available") or not cand.get("as_of"):
        return []
    saved = []
    async with session_factory()() as db:
        for side in ("LONG", "SHORT"):
            key = f"auto:{cand['as_of']}:{side}"
            if (await db.execute(select(SignalTest.id).where(SignalTest.dedupe_key == key))).first():
                continue
            top = cand["sides"][side]["top"]
            if not top:
                continue
            sym = top[0]["symbol"]
            live = await _live(sym)
            if not live:
                continue
            plan = sp.plan_for(sym, side, margin, live)
            if not plan.get("covered"):
                continue
            db.add(_row_from_plan(plan, "auto", key))
            saved.append(f"{sym} {side}")
        await db.commit()
    if saved:
        log.info("signal_test_recorded", signals=saved)
    return saved


async def add(symbol: str, side: str | None, margin: float) -> dict:
    """The user adds a plan to the test (once per coin, side and day)."""
    live = await _live(symbol)
    if not live:
        return {"ok": False, "note": "No live price for this coin right now."}
    plan = _plan(symbol, side, margin, live)
    if not plan.get("covered"):
        return {"ok": False, "note": plan.get("note") or "This coin is not covered by the models."}
    key = f"user:{datetime.now(UTC).date()}:{plan['symbol']}:{plan['side']}"
    async with session_factory()() as db:
        if (await db.execute(select(SignalTest.id).where(SignalTest.dedupe_key == key))).first():
            return {"ok": False, "note": f"{plan['symbol']} {plan['side']} is already in the test today."}
        row = _row_from_plan(plan, "user", key)
        db.add(row)
        await db.commit()
        return {"ok": True, "id": row.id}


async def remove(sid: str) -> bool:
    """Only signals the user added can be removed: the platform's own signals stay, wins and losses alike."""
    async with session_factory()() as db:
        row = await db.get(SignalTest, sid)
        if row is None or row.source != "user":
            return False
        await db.delete(row)
        await db.commit()
        return True


# ------------------------------------------------------------------------------------------- updating

def _ts(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int((dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp())


def _dt(s: int | None) -> datetime | None:
    return datetime.fromtimestamp(s, UTC) if s is not None else None


async def update() -> int:
    """Grade every signal that is not finished yet on the latest 1h candles."""
    from app.api.market import candles

    now_s = int(time.time())
    n = 0
    async with session_factory()() as db:
        rows = (await db.execute(select(SignalTest).where(
            (SignalTest.state.in_(("waiting", "open"))) | (SignalTest.fast_state.in_(("waiting", "open")))))).scalars().all()
        bars_by: dict[str, list[dict]] = {}
        for row in rows:
            if row.symbol not in bars_by:
                try:
                    bars_by[row.symbol] = (await candles(row.symbol, "1h", 500, None))["candles"]
                except Exception as e:  # noqa: BLE001
                    log.warning("signal_test_candles_failed", symbol=row.symbol, error=str(e))
                    bars_by[row.symbol] = []
            bars = bars_by[row.symbol]
            if not bars:
                continue
            g = grade({"created_s": _ts(row.created_at), "side": row.side, "entry": row.entry, "entry_type": row.entry_type,
                       "stop": row.stop, "tp1": row.tp1,
                       "tp2": None if (row.payload or {}).get("single_target") else row.tp2, "fast_stop": row.fast_stop,
                       "fast_tp": row.fast_tp,
                       # the unified engine's tight option is a 14-day plan like its main plan; the old fixed plan used 5
                       "fast_days": sty.HOLD if (row.payload or {}).get("engine") == "unified" else None}, bars, now_s)
            row.last_price = g["last_price"]
            row.updated_at = datetime.now(UTC)
            row.filled_at = _dt(g["filled_s"])
            if row.state in ("waiting", "open"):
                row.state = g["state"]
                if g["swing"]:
                    row.outcome, row.r = g["swing"]["outcome"], round(g["swing"]["r"], 4)
                    row.closed_at = _dt(g["swing"]["closed_s"])
                elif g["state"] == "not_filled":
                    row.outcome, row.closed_at = "not_filled", datetime.now(UTC)
            if row.fast_state in ("waiting", "open"):
                row.fast_state = g["fast_state"]
                if g["fast"]:
                    row.fast_outcome, row.fast_r = g["fast"]["outcome"], round(g["fast"]["r"], 4)
                elif g["fast_state"] == "not_filled":
                    row.fast_outcome = "not_filled"
            n += 1
        await db.commit()
    return n


async def tick(every_s: int = 600) -> None:
    """Called by the background loop: save today's signals, then grade the open ones (at most every 10 minutes)."""
    if time.time() - _last["t"] < every_s:
        return
    _last["t"] = time.time()
    await record_today()
    await update()


# ------------------------------------------------------------------------------------------- the report

def _wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float] | None:
    """90% range for a win rate from k wins in n trades (Wilson): how far luck alone can move a small sample."""
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, mid - half), 3), round(min(1.0, mid + half), 3)


def _usd(r: float | None, margin: float, lev: float, stop_pct: float | None) -> float | None:
    if r is None or not stop_pct:
        return None
    return round(r * margin * lev * stop_pct / 100, 2)


def _group(items: list[dict], plan: str) -> dict:
    """Stats over finished signals for one plan (swing | fast)."""
    rk, ok, ek = ("r", "outcome", "expected") if plan == "swing" else ("fast_r", "fast_outcome", "fast_expected")
    sk = "state" if plan == "swing" else "fast_state"
    done = [x for x in items if x[sk] == "closed" and x[rk] is not None]
    openn = [x for x in items if x[sk] == "open"]
    out: dict = {"finished": len(done), "open": len(openn), "waiting": sum(x[sk] == "waiting" for x in items),
                 "not_filled": sum(x[sk] == "not_filled" for x in items)}
    if not done:
        return out
    rs = [x[rk] for x in done]
    wins = sum(r > 0 for r in rs)
    usd_key = "usd" if plan == "swing" else "fast_usd"
    oc: dict[str, int] = {}
    for x in done:
        oc[x[ok]] = oc.get(x[ok], 0) + 1
    exp_w = [x["payload"][ek]["win_rate"] for x in done if (x["payload"].get(ek) or {}).get("win_rate") is not None]
    exp_r = [x["payload"][ek]["avg_r"] for x in done if (x["payload"].get(ek) or {}).get("avg_r") is not None]
    out |= {"wins": wins, "losses": len(rs) - wins, "win_rate": round(wins / len(rs), 3),
            "win_range_90": _wilson(wins, len(rs)), "avg_r": round(sum(rs) / len(rs), 3), "sum_r": round(sum(rs), 2),
            "usd": round(sum(x[usd_key] or 0 for x in done), 2), "outcomes": oc,
            "expected_win_rate": round(sum(exp_w) / len(exp_w), 3) if exp_w else None,
            "expected_avg_r": round(sum(exp_r) / len(exp_r), 3) if exp_r else None}
    return out


def _verdict(g: dict) -> dict:
    n = g.get("finished", 0)
    if n < MILESTONE:
        return {"ready": False, "text": f"{n} of {MILESTONE} signals finished. {MILESTONE - n} more to go before the "
                                         f"first score."}
    lo, hi = g["win_range_90"]
    exp_r = g.get("expected_avg_r")
    if g["avg_r"] > 0 and (exp_r is None or g["avg_r"] >= exp_r - 0.05):
        word = "working"
    elif g["avg_r"] > 0:
        word = "making money, but less than the backtest"
    else:
        word = "losing money so far"
    return {"ready": True, "word": word,
            "text": (f"After {n} finished signals: {round(g['win_rate'] * 100)}% made money, average {g['avg_r']:+.2f}R "
                     f"a trade ({g['sum_r']:+.1f}R in total). The backtest expected "
                     f"{'' if g.get('expected_win_rate') is None else str(round(g['expected_win_rate'] * 100)) + '% and '}"
                     f"{'' if exp_r is None else f'{exp_r:+.2f}R'}. With only {n} trades the true win rate could be "
                     f"anywhere from {round(lo * 100)}% to {round(hi * 100)}% (90% range): keep counting.")}


def _public(row: SignalTest) -> dict:
    d = {c: getattr(row, c) for c in ("id", "source", "as_of", "symbol", "side", "status", "gates", "entry_type", "entry",
                                      "stop", "tp1", "tp2", "fast_stop", "fast_tp", "margin", "safe_lev", "fast_safe_lev",
                                      "payload", "state", "outcome", "r", "fast_state", "fast_outcome", "fast_r",
                                      "last_price")}
    for c in ("created_at", "filled_at", "closed_at", "updated_at"):
        v = getattr(row, c)
        d[c] = (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat() if v else None   # SQLite drops the zone
    p = row.payload or {}
    d["usd"] = _usd(row.r, row.margin, row.safe_lev, p.get("stop_pct"))
    d["fast_usd"] = _usd(row.fast_r, row.margin, row.fast_safe_lev, p.get("fast_stop_pct"))
    if row.closed_at and row.filled_at:
        d["hours"] = round((_ts(row.closed_at) - _ts(row.filled_at)) / HOUR, 1)
    return d


VETO_NEEDED = 30        # pre-registered in docs/ML_RESEARCH.md §14, before any data
VETO_MARGIN_R = 0.15


def agent_veto_check(stats: dict) -> dict:
    """The pre-registered test of the AI agents as a veto: after 30 finished machine signals that had an agent verdict,
    the agents' WAIT / opposite call becomes a veto only if 'agents agreed' beats 'machine alone' by 0.15R a trade and
    the signals the agents said wait/against on lost money. Until then the machine decides and the agents only advise."""
    ag, wt, ac = (stats.get(k, {}).get("swing", {}) for k in ("agents_agree", "agents_wait", "agents_against"))
    have = sum(g.get("finished", 0) for g in (ag, wt, ac))
    out = {"needed": VETO_NEEDED, "have": have, "margin_r": VETO_MARGIN_R, "decided": have >= VETO_NEEDED, "veto_on": False}
    if not out["decided"]:
        out["text"] = (f"{have} of {VETO_NEEDED} machine signals with an agent verdict have finished. Until "
                       f"{VETO_NEEDED}, the machine decides and the agents only advise.")
        return out
    alone = stats.get("unified", {}).get("swing", {}).get("avg_r")
    skip_n = wt.get("finished", 0) + ac.get("finished", 0)
    skip_r = ((wt.get("sum_r") or 0) + (ac.get("sum_r") or 0)) / skip_n if skip_n else None
    out |= {"agree_avg_r": ag.get("avg_r"), "machine_avg_r": alone, "skipped_avg_r": None if skip_r is None else round(skip_r, 3)}
    out["veto_on"] = bool(ag.get("avg_r") is not None and alone is not None and ag["avg_r"] >= alone + VETO_MARGIN_R
                          and skip_r is not None and skip_r < 0)
    out["text"] = ("Test passed: the agents' WAIT or opposite call now counts as a veto." if out["veto_on"] else
                   "Test not passed: the agents did not add enough, so they stay a second opinion.")
    return out


async def report() -> dict:
    async with session_factory()() as db:
        rows = (await db.execute(select(SignalTest).order_by(SignalTest.created_at.desc()))).scalars().all()
    items = [_public(r) for r in rows]
    groups = {
        "all": [x for x in items],
        "platform": [x for x in items if x["source"] == "auto"],
        "trade": [x for x in items if x["source"] == "auto" and x["status"] == "trade"],
        "watch": [x for x in items if x["source"] == "auto" and x["status"] == "watch"],
        "long": [x for x in items if x["source"] == "auto" and x["side"] == "LONG"],
        "short": [x for x in items if x["source"] == "auto" and x["side"] == "SHORT"],
        "yours": [x for x in items if x["source"] == "user"],
        "unified": [x for x in items if x["source"] == "auto" and (x["payload"] or {}).get("engine") == "unified"],
        "fixed": [x for x in items if x["source"] == "auto" and (x["payload"] or {}).get("engine") != "unified"],
    }
    eng = groups["unified"]
    for st in ("agree", "wait", "against", "none"):
        groups[f"agents_{st}"] = [x for x in eng if ((x["payload"] or {}).get("agent_stance") or "none") == st]
    stats = {k: {"swing": _group(v, "swing"), "fast": _group(v, "fast"), "signals": len(v)} for k, v in groups.items()}
    main = "unified" if stats["unified"]["signals"] else "platform"
    veto = agent_veto_check(stats)
    from app.agents import journal

    try:
        agents_own = journal.stats()
    except Exception:  # noqa: BLE001 - the agents' record must never break the report
        agents_own = None
    return {"milestone": MILESTONE, "stats": stats, "main_group": main, "verdict": _verdict(stats[main]["swing"]),
            "agents_own": agents_own, "agent_veto": veto,
            "verdict_all": _verdict(stats["all"]["swing"]), "signals": items,
            "updated": max((x["updated_at"] for x in items if x["updated_at"]), default=None),
            "rules": __doc__.split("\n\n")[1]}
