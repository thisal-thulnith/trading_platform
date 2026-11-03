from app.signals.simulator import TradeSim
from app.strategies.exits import apply_exit_profile


def _cand(direction="LONG", q=60.0):
    if direction == "LONG":
        return {"direction": "LONG", "entry_low": 99.0, "entry_high": 101.0, "stop": 98.0, "targets": [104, 106, 110],
                "validation": {"passed": True, "quality_score": q, "checks": []}}
    return {"direction": "SHORT", "entry_low": 99.0, "entry_high": 101.0, "stop": 102.0, "targets": [96, 94, 90],
            "validation": {"passed": True, "quality_score": q, "checks": []}}


def test_default_profile_is_identity():
    c = _cand()
    assert apply_exit_profile(c, None) is c
    assert apply_exit_profile(c, "default") is c


def test_edge_tp1r_long_and_short():
    c = apply_exit_profile(_cand("LONG"), "edge_tp1r_q50")
    assert c["entry_low"] == c["entry_high"] == 101.0 and c["targets"] == [104.0] * 3  # risk 3 -> +1R = 104
    s = apply_exit_profile(_cand("SHORT"), "edge_tp1r_q50")
    assert s["entry_low"] == s["entry_high"] == 99.0 and s["targets"] == [96.0] * 3
    assert c["validation"]["passed"]


def test_quality_floor_blocks_confirmation_without_mutating_input():
    orig = _cand(q=40)
    c = apply_exit_profile(orig, "edge_tp1r_q50")
    assert not c["validation"]["passed"]
    assert orig["validation"]["passed"] and orig["entry_low"] == 99.0


def test_repeated_targets_close_whole_position_at_one_r():
    c = apply_exit_profile(_cand(), "edge_tp1r_q50")
    sim = TradeSim("LONG", c["entry_low"], c["entry_high"], c["stop"], c["targets"], fee=0.0, slip_bps=0.0)
    sim.on_bar(1, 102, 102, 100.5, 101)   # fills at 101
    sim.on_bar(2, 101, 104.5, 100.8, 104)  # hits +1R
    assert sim.outcome == "TARGET_HIT" and abs(sim.realized_r - 1.0) < 1e-9
