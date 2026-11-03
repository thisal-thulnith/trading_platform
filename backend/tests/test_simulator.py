import pytest

from app.signals.simulator import TradeSim


def sim(**kw):
    base = dict(direction="LONG", entry_low=99, entry_high=100, stop=98, targets=[102, 104, 106], fee=0, slip_bps=0)
    base.update(kw)
    return TradeSim(**base)


def test_full_target_path():
    t = sim()
    t.on_bar(1, 101, 101.5, 99.8, 100.5)  # fill at 100
    assert t.state == "ACTIVE" and t.fill == 100
    t.on_bar(2, 100.5, 106.5, 100.2, 106)
    assert t.outcome == "TARGET_HIT"
    assert t.realized_r == pytest.approx((2 + 4 + 6) / 3 / 1.5)  # R unit = planned risk |99.5 - 98|


def test_stop_first_on_ambiguous_bar():
    t = sim()
    t.on_bar(1, 101, 101, 99.9, 100)
    t.on_bar(2, 100, 103, 97, 100)  # both TP1 and stop inside bar
    assert t.outcome == "STOP_HIT" and t.realized_r == pytest.approx(-2 / 1.5)  # filled at zone top: worse than plan


def test_breakeven_after_tp1():
    t = sim()
    t.on_bar(1, 101, 101, 99.9, 100)
    t.on_bar(2, 100, 102.5, 100.1, 102)
    t.on_bar(3, 102, 102.2, 99.5, 99.8)
    assert t.outcome == "TP1_THEN_STOP"
    assert t.realized_r == pytest.approx((2 / 3) / 1.5)


def test_invalidated_before_entry():
    t = sim(entry_low=99.5, entry_high=100, stop=99.6)
    t.on_bar(1, 101, 101, 100.5, 100.8)
    assert t.outcome is None
    t2 = sim(direction="SHORT", entry_low=100, entry_high=101, stop=103, targets=[98, 96, 94])
    t2.on_bar(1, 99, 99.5, 98, 99)
    t2.on_bar(2, 99, 103.5, 98.5, 103)  # reaches zone and stop same bar -> fill then stop
    assert t2.outcome == "STOP_HIT"


def test_expiry():
    t = sim(expiry_bars=2)
    t.on_bar(1, 105, 106, 104, 105)
    t.on_bar(2, 105, 106, 104, 105)
    assert t.outcome == "EXPIRED" and t.fill is None


def test_fees_reduce_r():
    a, b = sim(), sim(fee=0.001, slip_bps=5)
    for x in (a, b):
        x.on_bar(1, 101, 101, 99.9, 100)
        x.on_bar(2, 100, 107, 100.1, 106)
    assert b.realized_r < a.realized_r


def test_short_side_mirror():
    t = sim(direction="SHORT", entry_low=100, entry_high=101, stop=102, targets=[98, 96, 94])
    t.on_bar(1, 99, 100.2, 98.9, 99.5)
    assert t.state == "ACTIVE" and t.fill == 100
    t.on_bar(2, 99.5, 99.6, 93, 94)
    assert t.outcome == "TARGET_HIT" and t.realized_r == pytest.approx((2 + 4 + 6) / 3 / 1.5)


def test_gap_fill_near_stop_keeps_r_bounded():
    # long zone 99-100, stop 98: price gaps down and opens at 98.05 (inside reach, just above stop) then stops out
    t = sim()
    t.on_bar(1, 98.05, 98.3, 97.5, 97.8)
    assert t.outcome == "STOP_HIT"
    assert -1.5 < t.realized_r < 0  # measured against planned risk (1.5), not the 0.05 actual distance


def test_open_beyond_stop_is_invalidated_not_filled():
    t = sim()
    t.on_bar(1, 97.5, 98.5, 97.0, 98.2)
    assert t.outcome == "INVALIDATED" and t.fill is None
