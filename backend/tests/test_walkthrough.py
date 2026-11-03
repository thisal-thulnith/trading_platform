from app.analysis.analyzer import build_snapshot
from app.analysis.walkthrough import build_walkthrough
from app.strategies.evaluate import enrich, evaluate_strategies, strip_runtime
from tests.conftest import make_ohlcv


def _snap(seed, drift, step=3_600_000):
    df = make_ohlcv(500, seed=seed, drift=drift, step_ms=step)
    s = enrich(build_snapshot("TESTUSDT", "1h", df), df)
    s["candidates"] = evaluate_strategies(s, now_ms=s["as_of_close_time"] + 1000)
    s["decision"] = {"action": "NO_TRADE", "reasons": ["test"]}
    return strip_runtime(s)


def test_walkthrough_covers_all_concepts_and_is_consistent():
    snap = _snap(3, 0.002)
    htf = {"4h": _snap(4, 0.002, 4 * 3_600_000), "1d": _snap(5, 0.002, 86_400_000)}
    w = build_walkthrough(snap, htf)
    keys = [s["key"] for s in w["steps"]]
    assert keys == ["big_picture", "regime", "structure", "levels", "liquidity", "zones", "imbalance", "premium_discount",
                    "elliott", "momentum", "derivatives", "news", "scenarios", "plan", "verdict"]
    ann_ids = {a["id"] for a in snap["annotations"]}
    for s in w["steps"]:
        assert s["plain"] and s["observation"]
        assert s["verdict"] in (-1, 0, 1)
        assert set(s["focus_ids"]) <= ann_ids | {""}, s["key"]  # focus only references real chart objects
    assert w["synthesis"]["confidence"] in ("low", "medium", "high")
    sc = next(s for s in w["steps"] if s["key"] == "scenarios")
    for o in sc["objects"]:
        assert o["price1"] > 0


def test_walkthrough_without_htf():
    w = build_walkthrough(_snap(7, -0.001), {})
    assert w["steps"][0]["verdict"] == 0


def test_custom_weights_follow_the_only_weighted_concept():
    from app.analysis.walkthrough import synthesize

    snap = _snap(3, 0.002)
    steps = [{"key": "structure", "title": "S", "verdict": -1, "observation": "x"},
             {"key": "momentum", "title": "M", "verdict": 1, "observation": "y"}]
    assert synthesize(steps, snap, {}, {"structure": 1.0, "momentum": 0.0})["direction"] == "bearish"
    assert synthesize(steps, snap, {}, {"structure": 0.0, "momentum": 1.0})["direction"] == "bullish"


def test_war_room_timeline_is_grounded():
    from app.agents.room import AGENTS, build_room

    snap = _snap(3, 0.002)
    htf = {"4h": _snap(4, 0.002, 4 * 3_600_000)}
    room = build_room(build_walkthrough(snap, htf), snap)
    ids = {a["id"] for a in AGENTS}
    ts = [e["t"] for e in room["events"]]
    assert ts == sorted(ts) and room["duration_ms"] == ts[-1]
    assert {e["agent"] for e in room["events"]} <= ids
    assert room["events"][-1]["agent"] == "chief" and room["events"][-1]["type"] == "say"
    for e in room["events"]:
        if e["type"] == "move":
            assert e["price"] > 0 and e["time"] > 0
        if e["type"] == "draw":
            assert e["object"]["id"].startswith("room_")
