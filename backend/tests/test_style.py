import numpy as np
import pandas as pd

from app.ml import style as st


def _frames(highs, lows, closes, entry=100.0):
    idx = pd.date_range("2024-01-01", periods=len(closes) + 1 + st.HOLD, freq="D")
    pad = [np.nan] * st.HOLD
    c = pd.DataFrame({"X": [entry, *closes, *pad]}, index=idx)
    h = pd.DataFrame({"X": [entry, *highs, *pad]}, index=idx)
    lo = pd.DataFrame({"X": [entry, *lows, *pad]}, index=idx)
    atr = pd.DataFrame({"X": [2.0] * len(idx)}, index=idx)
    return c, h, lo, atr


def r_of(plan, *hlc):
    c, h, lo, atr = _frames(*hlc)
    return float(st.plan_returns(c, h, lo, atr)[plan]["X"].iloc[0])


def test_stop_target_and_same_day_rule():
    fee = lambda s: st.FEE * 100 / (s * 2)  # noqa: E731
    # LONG 1.5 ATR stop (3), 2R target (+6): the target is hit on day 2
    hi, lo, cl = [101, 106.5] + [100] * 12, [99, 101] + [99] * 12, [100, 106] + [100] * 12
    assert abs(r_of("LONG_1.5ATR_2R", hi, lo, cl) - (2 - fee(1.5))) < 1e-9
    # the same path for a SHORT with the same stop: stopped on day 2 (high 106.5 >= 103)
    assert abs(r_of("SHORT_1.5ATR_2R", hi, lo, cl) - (-1 - fee(1.5))) < 1e-9
    # one day touching both the stop (97) and the target (106) counts as the stop
    assert r_of("LONG_1.5ATR_2R", [106.5] + [100] * 13, [96] + [100] * 13, [100] * 14) < -1


def test_timeout_marks_to_the_close_and_unknown_future_is_nan():
    hi, lo, cl = [100.5] * 14, [99.5] * 14, [101.0] * 14
    assert abs(r_of("LONG_2.5ATR_3R", hi, lo, cl) - (1 / 5 - st.FEE * 100 / 5)) < 1e-9
    c, h, lo2, atr = _frames(hi, lo, cl)
    R = st.plan_returns(c, h, lo2, atr)["LONG_2.5ATR_3R"]["X"]
    assert R.iloc[-st.HOLD:].isna().all()    # the last 14 days have no known future yet


def test_policy_takes_the_best_coin_and_plan_each_day_above_the_bar():
    dates = pd.to_datetime(["2024-01-01"] * 3 + ["2024-01-02"] * 2)
    P = np.array([[0.1, 0.3], [0.2, 0.0], [np.nan, np.nan], [0.01, 0.02], [0.0, 0.04]], dtype=np.float32)
    R = np.array([[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]], dtype=float)
    pk = st.policy(P, R, dates, None)
    assert list(pk["real"]) == [2, 10]
    assert list(st.policy(P, R, dates, 0.1)["real"]) == [2]
