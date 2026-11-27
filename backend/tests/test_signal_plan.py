import numpy as np

from app.ml import signal_plan as sp

SWING = sp.PLANS["swing"]


def _series(highs, lows, closes, entry=100.0):
    """One coin; day 0 is the entry close, ATR is 2 (stop 3 away, TP1 +3, TP2 +6)."""
    c = np.array([[entry], *[[x] for x in closes]], dtype=float)
    h = np.array([[entry], *[[x] for x in highs]], dtype=float)
    lo = np.array([[entry], *[[x] for x in lows]], dtype=float)
    atr = np.full_like(c, 2.0)
    return c, h, lo, atr


def _r(highs, lows, closes, side="LONG", plan=SWING):
    c, h, lo, atr = _series(highs, lows, closes)
    return sp.simulate(c, h, lo, atr, 0, 0, side, plan)


def test_stop_first_and_both_same_day_counts_as_stop():
    r, oc, k = _r([101, 101], [96.5, 99], [98, 100])
    assert oc == "stop" and k == 1 and abs(r - (-1 - sp.FEE * 100 / 3)) < 1e-9
    r, oc, _ = _r([104, 101], [96, 99], [100, 100])           # touches TP1 and the stop on one day
    assert oc == "stop"


def test_tp1_then_back_to_entry_and_tp2():
    fee = sp.FEE * 100 / 3
    r, oc, _ = _r([103.5, 101, 101], [100.5, 99.5, 99], [103, 100, 100])
    assert oc == "tp1_then_entry" and abs(r - (0.5 - fee)) < 1e-9
    r, oc, _ = _r([103.5, 106.5], [100.5, 102], [103, 106])
    assert oc == "tp2" and abs(r - (0.5 + 1.0 - fee)) < 1e-9


def test_short_mirrors_long_and_timeout_marks_to_close():
    r, oc, _ = _r([99.5, 98], [96.5, 93.5], [97, 94], side="SHORT")
    assert oc == "tp2"
    flat = [100.5] * 14
    r, oc, k = _r(flat, [99.5] * 14, [101.5] * 14)
    assert oc == "timeout" and k == 14 and abs(r - (1.5 / 3 - sp.FEE * 100 / 3)) < 1e-9


def test_future_unknown_is_open_not_a_result():
    r, oc, _ = _r([101], [99], [100])
    assert oc == "open" and np.isnan(r)


def test_short_recipe_swaps_targets_and_flips_the_rest():
    cols = dict(sp._side_cols("everything", "SHORT"))
    assert cols["pred_short_tp_first"] == 1 and cols["pred_long_tp_first"] == -1
    assert cols["analog_short_tp_first"] == 1 and cols["pred_direction_7d"] == -1 and cols["pred_drop_risk_7d"] == 1


def test_leverage_table_flags_liquidation_before_the_stop():
    t = sp.leverage_table(100.0, 87.0, 113.0, 126.0, "LONG", "AAVEUSDT", 100.0)   # stop 13% away
    rows = {r["leverage"]: r for r in t["rows"]}
    assert rows[5]["status"] in ("safe", "tight") and rows[5]["liquidation"] < 87
    assert rows[10]["status"] == "liquidated_before_stop" and rows[10]["stop_usd"] == -100
    assert rows[3]["stop_usd"] < 0 < rows[3]["tp1_usd"] < rows[3]["plan_usd"] < rows[3]["tp2_usd"]
