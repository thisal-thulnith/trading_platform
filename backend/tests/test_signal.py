import pytest

from app.signal import evidence, fusion

W = {"momentum": 0.3, "carry": 0.25, "regime": 0.15, "order_flow": 0.05, "fundamentals": 0.05, "setup": 0.0, "ai_desk": 0.0}


def test_weight_shrinks_from_the_prior_toward_the_measured_edge():
    assert evidence.weight(0.3, None, None) == 0.3                        # nothing measured: the literature prior
    assert evidence.weight(0.3, -1.9, 48) == pytest.approx(0.3 * 52 / 100, abs=1e-3)  # failing edge: only shrinks the prior
    assert evidence.weight(0.0, 0.6, 2659) < 0.01                          # an insignificant edge earns nothing
    assert evidence.weight(0.0, 3.5, 500) > 0.85                          # a strong, well-sampled edge earns full weight


def test_fusion_takes_waits_and_vetoes():
    bearish = fusion.components({"rank": {"score": 15, "trend": "down", "rank": 100, "of": 117}, "funding_ann_pct": 40,
                                 "btc_trend": "bear", "cvd_pct": -3, "setup_side": "SHORT", "setup_stage": "confirmed"})
    f = fusion.fuse(bearish, W, "SHORT", "ready")
    assert f["direction"] == "bearish" and f["decision"] == "take" and f["agreement"] > 0.9
    assert fusion.fuse(bearish, W, "SHORT", "forming")["decision"] == "wait"       # no trigger yet
    assert fusion.fuse(bearish, W, "LONG", "ready")["decision"] == "veto"          # the evidence points the other way
    assert f["contributions"][0]["component"] == "momentum"                        # explains itself, biggest first


def test_unproven_components_do_not_move_the_signal():
    comps = fusion.components({"setup_side": "LONG", "setup_stage": "confirmed", "desk": {"action": "LONG", "confidence": 0.9}})
    f = fusion.fuse(comps, W, "LONG", "ready")
    assert f["score"] == 0 and f["decision"] == "wait" and f["coverage"] == 0       # setup and desk have no earned weight
