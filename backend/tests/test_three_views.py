"""Machine, simulation and agents kept apart: the pieces that join them."""
from datetime import UTC, datetime, timedelta

import numpy as np

from app.api.agent import _final
from app.ml import signal_test as st
from app.ml import simulate as sm


def test_monte_carlo_skips_leverage_liquidated_before_stop():
    r = np.array([-1.0, 3.0] * 50)
    rows = sm.monte_carlo(r, stop_pct=7.0, margin=100.0, leverages=(1, 20))
    one, twenty = rows
    assert not one["liquidated_before_stop"] and one["risk_per_trade"] == 7.0
    assert twenty == {"leverage": 20, "liquidated_before_stop": True}   # 20x liquidates at ~4.5%: no number shown
    # average +1R a trade, 20 trades, $7 a R -> about +$140 in the middle
    assert 80 <= one["median"] <= 200


def test_monte_carlo_needs_a_sample():
    assert sm.monte_carlo(np.array([1.0, -1.0]), 5.0) == []


def test_stance():
    v = {"action": "SHORT"}
    assert st.stance("SHORT", v) == "agree"
    assert st.stance("LONG", v) == "against"
    assert st.stance("LONG", {"action": "WAIT"}) == "wait"
    assert st.stance("LONG", None) == "none"


def test_agent_verdict_counts_only_without_hindsight():
    now = datetime.now(UTC)
    v = {"at": now.isoformat()}
    assert st._fresh(v, now + timedelta(hours=2), "waiting")          # given before the signal
    assert st._fresh(v, now - timedelta(hours=2), "waiting")          # after, but the trade had not filled
    assert not st._fresh(v, now - timedelta(hours=2), "open")         # after the fill: hindsight
    assert not st._fresh(v, now + timedelta(hours=30), "waiting")     # too old


def test_final_reading():
    plan = {"side": "SHORT", "status": "trade"}
    assert _final(plan, None)["stance"] == "none"
    assert _final(plan, {"action": "SHORT"})["tone"] == "good"
    w = _final(plan, {"action": "WAIT", "conditions": ["rejects below 1.0"]})
    assert w["stance"] == "wait" and "rejects below 1.0" in w["text"]
    assert _final(plan, {"action": "LONG"})["tone"] == "bad"


def test_agent_veto_needs_30_and_a_clear_gain():
    def g(n, avg):
        return {"swing": {"finished": n, "avg_r": avg, "sum_r": None if avg is None else n * avg}}
    early = st.agent_veto_check({"agents_agree": g(10, 0.5), "agents_wait": g(5, -0.5), "agents_against": g(0, None),
                                 "unified": g(20, 0.1)})
    assert not early["decided"] and not early["veto_on"] and early["have"] == 15
    passed = st.agent_veto_check({"agents_agree": g(20, 0.4), "agents_wait": g(8, -0.6), "agents_against": g(2, -1.0),
                                  "unified": g(40, 0.15)})
    assert passed["decided"] and passed["veto_on"]
    small = st.agent_veto_check({"agents_agree": g(20, 0.2), "agents_wait": g(10, -0.2), "agents_against": g(0, None),
                                 "unified": g(40, 0.15)})
    assert small["decided"] and not small["veto_on"]   # agreed beats alone by only 0.05R


def test_tight_option_held_14_days_for_the_unified_engine():
    t0 = 1_700_000_000
    bars = [{"time": t0 + i * 3600, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0} for i in range(24 * 7)]
    sig = {"created_s": t0, "side": "LONG", "entry": 100.0, "entry_type": "market", "stop": 90.0, "tp1": 130.0,
           "tp2": None, "fast_stop": 98.0, "fast_tp": 106.0}
    now = t0 + 24 * 7 * 3600
    old = st.grade(sig, bars, now)                       # the old fixed plan: a 5-day tight option
    new = st.grade({**sig, "fast_days": 14}, bars, now)  # the unified engine: 14 days
    assert old["fast_state"] == "closed" and new["fast_state"] == "open"
