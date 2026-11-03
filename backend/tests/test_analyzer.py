import json

from app.analysis.analyzer import build_snapshot, jsonable
from tests.conftest import make_ohlcv


def test_snapshot_end_to_end_and_reproducible():
    df = make_ohlcv(600, seed=21, drift=0.001)
    htf = {"4h": make_ohlcv(300, seed=22, step_ms=4 * 3_600_000)}
    s1 = build_snapshot("BTCUSDT", "1h", df, htf)
    s2 = build_snapshot("BTCUSDT", "1h", df, htf)
    for k in ("generated_at",):
        s1.pop(k), s2.pop(k)
    assert json.dumps(jsonable(s1), sort_keys=True) == json.dumps(jsonable(s2), sort_keys=True)
    assert s1["confluence"]["direction"] in ("bullish", "bearish", "neutral")
    assert s1["annotations"], "expected chart annotations"
    ids = [a["id"] for a in s1["annotations"]]
    assert len(ids) == len(set(ids)), "annotation ids must be unique"
    for a in s1["annotations"]:
        assert a["origin"] == "ai" and "explain" in a


def test_family_capping_prevents_double_counting():
    from app.analysis.evidence import Evidence, aggregate
    many = [Evidence("momentum", f"osc{i}", 1, 1.0, "") for i in range(10)]
    one_trend = [Evidence("trend", "t", -1, 1.0, "")]
    agg = aggregate(many + one_trend)
    # 10 agreeing oscillators must not overwhelm a single opposing higher-weight family
    assert agg["bias"] < 0.0
