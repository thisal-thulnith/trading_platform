from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.analysis.schools import SCHOOLS, dow, fractals, po3, quasimodo, run_schools, school_vote, zigzag
from app.analysis.walkthrough import build_walkthrough
from tests.conftest import make_ohlcv
from tests.test_walkthrough import _snap


def _path(prices, step_ms=3_600_000, t0=1_767_225_600_000):
    """Candles that walk linearly through the given swing prices (10 bars per leg)."""
    closes = np.concatenate([np.linspace(a, b, 10, endpoint=False) for a, b in zip(prices, prices[1:])] + [[prices[-1]]])
    o = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({"open_time": t0 + np.arange(len(closes)) * step_ms, "open": o,
                         "high": np.maximum(o, closes) * 1.001, "low": np.minimum(o, closes) * 0.999,
                         "close": closes, "volume": np.full(len(closes), 100.0)})


def test_every_school_returns_a_valid_step():
    df = make_ohlcv(500, seed=7, drift=0.001)
    steps = run_schools(df, "1h", datetime(2026, 1, 1, 9, tzinfo=UTC))
    assert [s["key"] for s in steps] == SCHOOLS
    lo, hi = df["low"].min(), df["high"].max()
    for s in steps:
        assert s["verdict"] in (-1, 0, 1) and s["observation"] and s["plain"]
        for o in s["objects"]:
            for k in ("price1", "price2"):
                if o.get(k) is not None and o["kind"] not in ("trendline",):  # Gann angles project beyond the range
                    assert lo * 0.5 < o[k] < hi * 1.5, (s["key"], o)
    v = school_vote(steps)
    assert len(v["bull"]) + len(v["bear"]) + len(v["neutral"]) == len(SCHOOLS)


def test_zigzag_and_fractals_find_the_swings():
    df = _path([100, 120, 110, 130, 115, 140])
    zz = zigzag(df, 2.0)
    assert [k for _, k, _ in zz] == ["low", "high", "low", "high", "low", "high"]
    assert abs(zz[3][2] - 130 * 1.001) < 0.5
    highs = [p for _, k, p in fractals(df, 2) if k == "high"]
    assert len(highs) >= 2 and max(highs) > 129


def test_dow_uptrend_and_break():
    up = dow(_path([100, 120, 110, 130, 118, 140, 126, 150, 135, 160, 145, 170]))
    assert up["verdict"] == 1 and "uptrend" in up["observation"]
    down = dow(_path([170, 150, 160, 140, 152, 130, 144, 120, 135, 110, 125, 100]))
    assert down["verdict"] == -1


def test_quasimodo_bearish():
    # left shoulder 130, low 115, head 140 sweeps it, lower low 105 breaks structure, price returns toward 130
    s = quasimodo(_path([100, 130, 115, 140, 105, 126]))
    assert s["verdict"] == -1 and "Bearish Quasimodo" in s["observation"]
    assert any(o["id"] == "school_qm_level" and abs(o["price1"] - 130 * 1.001) < 0.5 for o in s["objects"])


def test_po3_bullish_after_asia_low_sweep():
    t0 = int(datetime(2026, 1, 2, tzinfo=UTC).timestamp() * 1000) - 24 * 3_600_000
    prev = [100.0] * 24
    asia = [100, 101, 100.5, 101, 100.6, 100.8, 100.7]  # 00-07 UTC range ~100-101
    london = [99.0, 98.5, 100.5, 102, 103]  # sweeps the Asia low, then rallies above the daily open
    closes = np.array(prev + asia + london, dtype=float)
    o = np.r_[closes[0], closes[:-1]]
    df = pd.DataFrame({"open_time": t0 + np.arange(len(closes)) * 3_600_000, "open": o, "high": np.maximum(o, closes),
                       "low": np.minimum(o, closes), "close": closes, "volume": 1.0})
    s = po3(df, "1h", datetime(2026, 1, 2, 12, 30, tzinfo=UTC))
    assert s["verdict"] == 1 and "Bullish Power of 3" in s["observation"]
    assert any("killzone" in b for b in s["bullets"])


def test_war_room_with_council_and_scout():
    from app.agents.room import AGENTS, SCHOOL_AGENTS, build_room

    snap = _snap(3, 0.002)
    df = make_ohlcv(500, seed=3, drift=0.002)
    schools = run_schools(df, "1h")
    scout = {"rows": [{"symbol": "TESTUSDT", "score": 80, "direction": "bullish", "reasons": ["a", "b"]},
                      {"symbol": "XUSDT", "score": 40, "direction": "neutral", "reasons": ["c"]}],
             "session": {"session": "London", "killzone": None, "utc": "09:00"}}
    room = build_room(build_walkthrough(snap, {}), snap, schools, scout)
    speakers = {e["agent"] for e in room["events"] if e["type"] == "say"}
    assert {a["id"] for a in SCHOOL_AGENTS} <= speakers and "scout" in speakers
    assert {e["agent"] for e in room["events"]} <= {a["id"] for a in AGENTS}
    assert room["events"][0]["agent"] == "chief" and room["council"]["direction"] in ("bullish", "bearish", "neutral")
    assert room["events"][-1]["agent"] == "chief"


def test_agents_use_the_toolbar_like_a_person():
    from app.agents.room import TOOL_FOR, build_room

    snap = _snap(3, 0.002)
    room = build_room(build_walkthrough(snap, {}), snap, run_schools(make_ohlcv(500, seed=3, drift=0.002), "1h"))
    ev = room["events"]
    for i, e in enumerate(ev):
        if e["type"] == "draw":  # every drawing is preceded by picking its tool and pressing at the start point
            assert ev[i - 1]["type"] == "move" and ev[i - 1]["agent"] == e["agent"]
            assert ev[i - 2] == {**ev[i - 2], "type": "click", "target": f"tool:{TOOL_FOR[e['object']['kind']]}"}
    inds = [e for e in ev if e["type"] == "indicator"]
    assert {e["id"] for e in inds} >= {"ema", "rsi", "macd"}
    for e in inds:
        j = ev.index(e)
        assert ev[j - 1]["target"] == f"ind:{e['id']}" and ev[j - 2]["target"] == "indicators"
