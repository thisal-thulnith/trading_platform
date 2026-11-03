import asyncio
import json

from app.agents import live_room
from app.analysis.schools import run_schools
from app.analysis.walkthrough import build_walkthrough
from tests.conftest import make_ohlcv
from tests.test_walkthrough import _snap


def _fake_llm(calls: list):
    async def complete(system, user, max_tokens=900, json_mode=False):
        calls.append((system, user))
        task = user.split("TASK:")[-1]
        data = json.loads(user.split("DATA:\n", 1)[1].split("\n\nTASK:")[0])
        if "Choose the ONE coin" in task:
            return json.dumps({"symbol": "TESTUSDT", "say": "Test coin has the cleanest setup.", "also_considered": ["NOPE"]})
        if "Plan the analysis" in task:
            return json.dumps({"say": "Range market: liquidity, Wyckoff and profile first.",
                               "ask": [{"id": "hunter", "question": "Where is liquidity?"},
                                       {"id": "composite", "question": "Accumulation?"},
                                       {"id": "made_up_agent", "question": "x"}, {"id": "sage", "question": "Structure?"}]})
        if "chart_objects_you_can_draw" in data:
            ids = [o["id"] for o in data["chart_objects_you_can_draw"]]
            verdict = "bearish" if "Wyckoff" in system else "bullish"
            return json.dumps({"say": "My read from the evidence.", "verdict": verdict, "confidence": 60,
                               "draw": ids[:1] + ["invented_object"], "indicators": ["rsi", "not_an_indicator"],
                               "reply_to": None})
        if "most important disagreement" in task:
            return json.dumps({"say": "Wyckoff vs liquidity — answer each other.", "bull_id": "hunter", "bear_id": "composite"})
        if "Respond directly" in task:
            return json.dumps({"say": "I partly agree.", "verdict": "neutral"})
        if "Attack the leading view" in task:
            return json.dumps({"say": "Base rates say most of these fail."})
        if "final call" in task:  # tries to trade even if nothing is tradeable
            return json.dumps({"decision": "TRADE", "index": 0, "size": "full", "say": "Let's take it."})
        return None
    return complete


def test_live_session_is_grounded_and_guarded(monkeypatch):
    snap = _snap(3, 0.002)
    for c in snap["candidates"]:  # make sure nothing is tradeable: the LLM must not be able to force a trade
        if "validation" in c:
            c["validation"]["passed"] = False
    walk = build_walkthrough(snap, {})
    schools = run_schools(make_ohlcv(500, seed=3, drift=0.002), "1h")
    calls = []
    monkeypatch.setattr(live_room, "complete", _fake_llm(calls))

    async def walk_fn(sym, tf):
        return walk, snap

    async def schools_fn(sym, tf):
        return schools

    async def scan_fn(tf, timeout=120):
        return {"rows": [{"symbol": "TESTUSDT", "signal": {"action": "WAIT"}, "reasons": ["x"], "direction": "bullish",
                          "council": {"direction": "neutral"}, "agree": True, "regime": "range", "score": 50}],
                "all_coins": 500}

    import app.agents.scout as scout
    import app.api.chat as chat
    from app.quant import brain, ledger, odds

    qd = {"coins": 2, "regime": {"plain": "BTC is in a mixed trend.", "btc_trend": "mixed"},
          "proof": {"weeks": 10, "long_short": {"total_pct": -1.0, "t_stat": -0.2}},
          "ranks": [{"symbol": "TESTUSDT", "rank": 1, "of": 2, "score": 90.0, "trend": "strong up", "mom_7d_pct": 3.0,
                     "mom_28d_pct": 9.0, "derivs": None, "funding_note": None}]}
    booked = []

    async def qget(refresh=False):
        return qd

    async def no_scores():
        return {"base_rate_up": 0.5}

    async def no_weights():
        return {}

    async def fake_record(sid, symbol, tf, ref, items, regime=None):
        booked.extend(items)
        return len(items)

    async def fake_odds(*a, **k):
        return {"n": 40, "wins": 12, "of10": 3, "avg_r": -0.4}

    monkeypatch.setattr(brain, "get", qget)
    monkeypatch.setattr(brain, "peek", lambda: qd)
    monkeypatch.setattr(brain, "coin", lambda s: qd["ranks"][0] if s == "TESTUSDT" else None)
    monkeypatch.setattr(ledger, "scores", no_scores)
    monkeypatch.setattr(ledger, "weights", no_weights)
    monkeypatch.setattr(ledger, "record", fake_record)
    monkeypatch.setattr(odds, "setup_odds", fake_odds)
    monkeypatch.setattr(chat, "_walk", walk_fn)
    monkeypatch.setattr(chat, "_schools", schools_fn)
    monkeypatch.setattr(scout, "wait_scan", scan_fn)
    live_room.SESSIONS["t1"] = {"id": "t1", "status": "running", "events": []}
    asyncio.run(live_room.run_live("t1", None, "1h"))
    s = live_room.SESSIONS["t1"]
    assert s["status"] == "done" and s["symbol"] == "TESTUSDT"
    ev = s["events"]
    ts = [e["t"] for e in ev]
    assert ts == sorted(ts)
    speakers = [e["agent"] for e in ev if e["type"] == "say"]
    assert {"hunter", "composite", "sage", "skeptic", "chief"} <= set(speakers)
    assert "made_up_agent" not in {e["agent"] for e in ev}
    drawn = [e["object"]["id"] for e in ev if e["type"] == "draw"]
    assert drawn and not any("invented_object" in d for d in drawn)
    assert {e["id"] for e in ev if e["type"] == "indicator"} == {"rsi"}
    assert not any(e.get("target") == "scan:NOPE" for e in ev)
    # a disagreement produced a debate with replies
    assert any(e["type"] == "say" and e.get("reply_to") == "hunter" for e in ev)
    # the LLM asked to TRADE an unvalidated setup: Guard overrides
    assert s["signal"]["action"] == "WAIT" and "Guard overrode" in ev[-1]["text"]
    # every prompt carries the no-invented-numbers rule
    assert all("Never invent" in sys for sys, _ in calls)
    # the quant brain speaks, the team forecast is maths over every forecaster, and all of it is booked for grading
    assert "quant" in speakers
    f = s["signal"]["forecast"]
    assert 0 < f["p_up"] < 1 and {p["source"] for p in f["parts"]} >= {"hunter", "composite", "sage", "quant"}
    assert s["signal"]["odds"]["of10"] == 3 and s["signal"]["booked"] == len(booked)
    assert {"team", "quant", "hunter"} <= {b["source"] for b in booked}
