
from app.analysis.elliott import _impulse, analyze_elliott
from app.analysis.price_action import Pivot
from tests.conftest import make_ohlcv


def piv(prices):
    kinds = ["low", "high"] * 4
    return [Pivot(i * 10, i * 10 + 3, i * 3_600_000, p, kinds[i]) for i, p in enumerate(prices)]


def test_textbook_impulse_valid():
    # 0=100, 1=110 (w1=10), 2=104 (62%), 3=120 (w3=16 =1.6x), 4=115 (31%), 5=125 (w5=10)
    r = _impulse(piv([100, 110, 104, 120, 115, 125]), True, False)
    assert r is not None and r["pattern"] == "impulse" and r["complete"]
    assert r["score"] > 0.8
    assert r["invalidation"] == 125


def test_rule1_violation_rejected():
    assert _impulse(piv([100, 110, 99, 120, 115, 125]), True, False) is None


def test_rule2_violation_rejected():
    # w1=10, w3=6 (shortest), w5=12
    assert _impulse(piv([100, 110, 105, 111, 108.5, 120.5]), True, False) is None


def test_overlap_becomes_diagonal_or_rejected():
    r = _impulse(piv([100, 110, 104, 120, 109, 125]), True, False)
    assert r is None or r["pattern"] == "leading_diagonal"


def test_in_progress_wave4_invalidation():
    r = _impulse(piv([100, 110, 104, 120, 115]), True, True)
    assert r["current_wave"] == "5" and r["invalidation"] == 115


def test_analyze_elliott_runs_on_trend():
    df = make_ohlcv(500, seed=5, drift=0.002)
    out = analyze_elliott(df)
    assert "counts" in out
    for c in out["counts"]:
        assert c["confidence_calibrated"] is False
        assert 0 <= c["score"] <= 1
