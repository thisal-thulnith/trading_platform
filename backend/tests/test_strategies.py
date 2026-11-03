from app.analysis.analyzer import build_snapshot
from app.strategies.evaluate import enrich, evaluate_strategies, strip_runtime
from app.strategies.library import STRATEGIES
from tests.conftest import make_ohlcv


def test_all_strategies_run_and_produce_consistent_levels():
    seen_stages = set()
    for seed in range(8):
        df = make_ohlcv(500, seed=seed, drift=0.0015 if seed % 2 else -0.0015)
        snap = enrich(build_snapshot("TEST", "1h", df), df)
        res = evaluate_strategies(snap, now_ms=snap["as_of_close_time"] + 1000)
        for r in res:
            assert "error" not in r, r
            seen_stages.add(r["stage"])
            s = 1 if r["direction"] == "LONG" else -1
            assert r["entry_low"] <= r["entry_high"]
            assert len(r["targets"]) == 3
            v = r["validation"]
            assert isinstance(v["passed"], bool) and v["checks"]
            if v["passed"]:
                assert s * (r["targets"][0] - r["entry_high" if s > 0 else "entry_low"]) > 0
        assert not any(k.startswith("_") for k in strip_runtime(snap))
    assert seen_stages & {"forming", "pre_signal", "confirmed"}


def test_specs_complete():
    for sid, s in STRATEGIES.items():
        d = s.spec.d()
        for k in ("entry", "exit", "invalidation", "stop_rule", "target_rule", "regimes", "required_data"):
            assert d[k], f"{sid}.{k}"


def test_stale_data_fails_validation():
    df = make_ohlcv(500, seed=1, drift=0.002)
    snap = enrich(build_snapshot("TEST", "1h", df), df)
    res = evaluate_strategies(snap, now_ms=snap["as_of_close_time"] + 10 * 3_600_000)
    for r in res:
        fresh = next(c for c in r["validation"]["checks"] if c["name"] == "data fresh")
        assert not fresh["passed"] and not r["validation"]["passed"]
