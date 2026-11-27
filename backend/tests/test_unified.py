import numpy as np
import pandas as pd

from app.ml import unified as un


def _toy():
    dates = pd.DatetimeIndex(["2024-01-01"] * 3 + ["2024-01-02"] * 3)
    P = np.full((6, 18), -0.5)
    P[0, 3], P[1, 5], P[2, 1] = 0.4, 0.3, 0.9      # day 1: row 2 is best but too volatile
    P[3, 0], P[4, 10], P[5, 2] = 0.1, 0.2, 0.05
    R = np.arange(6 * 18, dtype=float).reshape(6, 18)
    atr_pct = np.array([5, 6, 60, 5, 5, 5], dtype=float)
    return P, R, dates, atr_pct


def test_decide_takes_top_k_per_day_and_skips_extreme_volatility():
    P, R, dates, atr_pct = _toy()
    pk = un.decide(P, R, dates, atr_pct, {"score": "raw", "k": 1, "bar": False})
    assert list(pk["row"]) == [0, 4] and list(pk["plan"]) == [3, 10]
    assert list(pk["real"]) == [R[0, 3], R[4, 10]]
    pk3 = un.decide(P, R, dates, atr_pct, {"score": "raw", "k": 3, "bar": False})
    assert sorted(pk3[pk3["date"] == pd.Timestamp("2024-01-01")]["row"]) == [0, 1]     # the 60%-ATR coin never


def test_edge_score_is_relative_to_the_same_plan_on_other_coins():
    P, R, dates, atr_pct = _toy()
    S = un._scores(P, dates, "edge")
    assert abs(S[0, 3] - (0.4 - (-0.5))) < 1e-9 and abs(S[0, 0]) < 1e-9


def test_groups_and_descriptions():
    assert un.group_of("ms_struct") == "Market structure (SMC)" and un.group_of("r_mom_28") == "Trend & momentum"
    assert un.group_of("analog_long_tp_first") == "Learned chart patterns"
    assert "rank" in un.describe("r_rsi_14") and "fair value" in un.describe("fvg_bull_open")
    assert un._parse("SHORT_0.75ATR_3R") == ("SHORT", 0.75, 3.0)
