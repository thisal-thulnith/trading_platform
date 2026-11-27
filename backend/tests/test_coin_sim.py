# ruff: noqa: E741  (o, h, l, c: open, high, low, close)
import math

import numpy as np
import pandas as pd

from app.sim import engine as E
from app.sim.engine import START, SimConfig


def _flat(n=10, px=100.0):
    o = np.full(n, px)
    return o.copy(), o * 1.01, o * 0.99, o.copy()


def _run(o, h, l, c, F, fund=None, cfg=None, cost=0.0, atr=1.0):  # noqa: E741
    n = len(c)
    return E.run_coin(o, h, l, c, np.ones(n, bool), np.full(n, cost), np.full(n, np.nan) if fund is None else fund,
                      np.full(n, atr), np.asarray(F, float), 0, n - 1, cfg or SimConfig(stop_atr=None))


def test_fills_at_next_open_never_the_signal_close():
    o, h, l, c = _flat(6)
    o[2], c[1] = 110.0, 100.0
    F = [0, 0.5, 0.5, 0.5, 0.5, 0.5]
    r = _run(o, h, l, c, F)
    t = r["trades"][0]
    assert t["entry_t"] == 2 and t["entry_px"] == 110.0


def test_signal_exit_and_costs():
    o, h, l, c = _flat(8)
    F = [0.5, 0.5, 0.5, -0.05, 0, 0, 0, 0]
    r = _run(o, h, l, c, F, cfg=SimConfig(stop_atr=None, shorts=False), cost=0.001)
    t = r["trades"][0]
    assert t["why"] == "signal" and t["exit_t"] == 4
    assert math.isclose(r["eq"][-1], START - 2 * START * 0.001, rel_tol=1e-6)  # fee on entry and exit notional


def test_stop_fills_at_stop_and_blocks_reentry_until_rearmed():
    n = 10
    o, h, l, c = _flat(n)
    l[3] = 90.0  # 3 x ATR(2) = 6 below entry 100 -> stop 94
    F = [0.5] * n
    r = _run(o, h, l, c, F, cfg=SimConfig(stop_atr=3.0, shorts=False), atr=2.0)
    assert r["trades"][0]["why"] == "stop" and math.isclose(r["trades"][0]["exit_px"], 94.0)
    assert len(r["trades"]) == 1  # F never fell back through exit, so no new long


def test_one_x_long_is_spot_no_funding_short_pays_funding():
    o, h, l, c = _flat(6)
    fund = np.full(6, 0.001)
    long = _run(o, h, l, c, [0.5] * 6, fund=fund)
    assert long["funding"] == 0.0
    short = _run(o, h, l, c, [-0.5] * 6, fund=fund)
    assert short["funding"] < 0  # positive funding: shorts receive


def test_leveraged_gap_through_liquidation_ends_the_run():
    n = 8
    o, h, l, c = _flat(n)
    o[4], h[4], l[4], c[4] = 60.0, 61.0, 55.0, 58.0  # gaps far below a 3x long's liquidation (~67)
    r = _run(o, h, l, c, [0.5] * n, fund=np.zeros(n), cfg=SimConfig(stop_atr=3.0, leverage=3.0), atr=1.0)
    assert r["ruined"] and r["trades"][-1]["why"] == "liquidation"
    assert np.all(r["eq"][4:] == 0)


def test_stop_never_beyond_liquidation():
    n = 8
    o, h, l, c = _flat(n)
    l[4] = 72.0  # 3x long from 100: liquidation ~67; a 3 x ATR(20) stop (40) is capped at 0.8 x 33 -> ~73.6
    r = _run(o, h, l, c, [0.5] * n, fund=np.zeros(n), cfg=SimConfig(stop_atr=3.0, leverage=3.0), atr=20.0)
    assert not r["ruined"] and r["trades"][0]["why"] == "stop"


def test_shorts_need_a_perp():
    o, h, l, c = _flat(6)
    r = _run(o, h, l, c, [-0.5] * 6)  # no funding history -> no perp -> no short
    assert r["trades"] == []


def test_fuse_matches_live_fusion():
    from app.signal.fusion import fuse as live_fuse

    comps = {"momentum": 0.4, "regime": -0.6, "quant_factors": 0.8, "carry": float("nan")}
    w = {"momentum": 0.1, "regime": 0.15, "quant_factors": 0.3, "carry": 0.2}
    got = E.fuse({k: np.array([[v]]) for k, v in comps.items()}, w)[0, 0]
    want = live_fuse({k: {"s": v, "why": ""} for k, v in comps.items() if not math.isnan(v)}, w)["score"]
    assert math.isclose(got, want, abs_tol=1e-4)


def test_null_test_separates_skill_from_luck():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.02, 800)
    cost = np.zeros(800)
    perfect = np.r_[np.sign(r[1:]), 0.0]  # position at t knows the return of t+1
    assert E.null_test(perfect, r, cost, 0, 799, 200, 0)["p"] < 0.02
    rand = np.sign(rng.normal(size=800))
    assert E.null_test(rand, r, cost, 0, 799, 200, 0)["p"] > 0.02


def test_benjamini_hochberg():
    assert E.bh([0.001, 0.2, 0.03, None, 0.9], q=0.10) == [True, False, True, False, False]
    assert E.bh([0.5, 0.6]) == [False, False]


def test_point_in_time_signs_ignore_later_data():
    idx = pd.date_range("2019-01-01", "2021-12-31")
    ic = pd.DataFrame({"a": np.where(idx < pd.Timestamp("2020-06-01"), 0.1, -0.1)}, index=idx)
    seen = []

    def screen(x):
        seen.append(x.index.max())
        return {"a": 1 if x["a"].mean() > 0 else -1}

    s = E.pit_signs(ic, [2019, 2020, 2021], screen)
    assert s[2019] == {} and s[2020] == {"a": 1}  # 2020 cannot see the sign change in June 2020
    assert all(d < pd.Timestamp(f"{y}-01-01") for d, y in zip(seen, [2020, 2021], strict=True))


def test_monte_carlo_bands_ordered():
    mc = E.monte_carlo(np.random.default_rng(0).normal(0.001, 0.02, 500), 200, 120, 0)
    b = mc["bands"]
    assert all(b["p5"][i] <= b["p50"][i] <= b["p95"][i] for i in range(len(b["p5"])))
    assert 0 <= mc["p_loss"] <= 100


def test_dca_bot_no_take_profit_on_a_safety_order_day():
    from app.sim.benchmarks import dca_coin

    n = 6
    o = np.array([100.0, 100, 100, 100, 100, 100])
    h = np.array([100.0, 101, 110, 100, 100, 100])
    l = np.array([100.0, 99, 90, 99, 99, 99])  # day 2: safety orders fill at the low AND the high reaches the TP
    c = o.copy()
    r = dca_coin(o, h, l, c, np.ones(n, bool), np.zeros(n), 0, n - 1)
    assert not any(t["why"] == "take profit" and t["exit_t"] == 2 for t in r["trades"])


def test_dca_bot_counts_stuck_cycles_as_open_not_won():
    from app.sim.benchmarks import dca_coin

    n = 40
    c = np.linspace(100, 40, n)  # a steady slide: safety orders fill, the take profit never comes
    r = dca_coin(c, c * 1.001, c * 0.999, c, np.ones(n, bool), np.zeros(n), 0, n - 1)
    assert E.trade_stats(r["trades"])["trades"] == 0 and r["trades"][-1]["why"] == "open"
    assert r["eq"][-1] < START * 0.7


def test_trend_signals_follow_a_clean_trend():
    from app.sim.benchmarks import ema_cross, supertrend

    up = np.exp(np.linspace(0, 1, 200))[:, None] * 100
    assert ema_cross(pd.DataFrame(up))[-1, 0] == 1.0
    assert supertrend(up * 1.01, up * 0.99, up)[-1, 0] == 1.0
    down = up[::-1]
    assert ema_cross(pd.DataFrame(down))[-1, 0] == -1.0


def test_take_profit_fills_at_target_and_stop_wins_a_shared_candle():
    n = 6
    o, h, l, c = _flat(n)
    h[2] = 104.0
    r = _run(o, h, l, c, [0.5, 0.5, 0, 0, 0, 0], cfg=SimConfig(stop_atr=None, take_profit=0.03, shorts=False))
    assert r["trades"][0]["why"] == "take profit" and math.isclose(r["trades"][0]["exit_px"], 103.0)
    o, h, l, c = _flat(n)
    h[2], l[2] = 104.0, 90.0  # reaches both: counted as stopped
    r = _run(o, h, l, c, [0.5, 0.5, 0, 0, 0, 0], cfg=SimConfig(stop_atr=3.0, take_profit=0.03, shorts=False), atr=2.0)
    assert r["trades"][0]["why"] == "stop"


def test_partial_profit_takes_half_and_moves_stop_to_entry():
    n = 8
    o, h, l, c = _flat(n)
    h[2] = 107.0  # +1R with ATR 2 x 3 = 6 -> partial at 106
    l[4] = 99.5   # back through the entry: the rest stops at 100 (break-even)
    r = _run(o, h, l, c, [0.5] * n, cfg=SimConfig(stop_atr=3.0, partial_r=1.0, shorts=False), atr=2.0)
    t = r["trades"][0]
    assert t["why"] == "stop" and t.get("partial") and math.isclose(t["exit_px"], 100.0)
    assert t["ret"] > 0  # half banked at +6%, half flat
