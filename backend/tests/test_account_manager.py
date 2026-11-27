"""The Risk manager picks the risk mode from evidence; the sizing follows the dollar risk and the stop."""
from app.portfolio import manager as m


def acct(dd=0.0, streak=0):
    return {"drawdown_pct": dd, "loss_streak": streak}


def test_starts_conservative_until_the_live_record_proves_itself():
    r = m.choose_mode({"finished": 3, "avg_r": 0.5}, acct(), "agree", "trade", False)
    assert r["mode"] == "conservative" and r["risk_pct"] == 1.0 and r["trade"]


def test_growth_needs_10_live_signals_and_aggressive_needs_30_plus_the_agents():
    g = m.choose_mode({"finished": 12, "avg_r": 0.15}, acct(), "none", "trade", False)
    assert g["mode"] == "growth" and g["risk_pct"] == 2.0
    not_aggr = m.choose_mode({"finished": 40, "avg_r": 0.3}, acct(), "agree", "trade", False)  # veto test not passed
    assert not_aggr["mode"] == "growth"
    a = m.choose_mode({"finished": 40, "avg_r": 0.3}, acct(), "agree", "trade", True)
    assert a["mode"] == "aggressive" and a["risk_pct"] == 3.0


def test_steps_down_and_pauses():
    s = m.choose_mode({"finished": 40, "avg_r": 0.3}, acct(streak=3), "agree", "trade", True)
    assert s["mode"] == "conservative" and s["risk_pct"] == 0.5
    w = m.choose_mode({"finished": 5, "avg_r": 0.1}, acct(), "wait", "trade", False)
    assert w["risk_pct"] == 0.5 and w["trade"]
    assert not m.choose_mode({"finished": 5, "avg_r": 0.1}, acct(), "against", "trade", False)["trade"]
    assert not m.choose_mode({"finished": 5, "avg_r": 0.1}, acct(), "none", "watch", False)["trade"]
    assert not m.choose_mode({"finished": 5, "avg_r": 0.1}, acct(dd=25), "none", "trade", False)["trade"]


def test_size_from_dollar_risk_and_stop():
    # $1,000 account, 1% risk = $10; stop 5% away -> position ~ $10 / 0.051 = $196; margin limit 30% = $300 -> 1x
    s = m.size_trade(1000, 1.0, "conservative", "SHORT", 100.0, 105.0, 10)
    assert s["ok"] and s["leverage"] == 1 and abs(s["notional_usd"] - 196.08) < 0.1 and s["risk_usd"] == 10.0
    assert s["liquidation"] > 105.0          # a short's liquidation sits above the stop
    # a tight 1% stop needs a big position: $10 / 0.011 = $909 -> margin limit $300 -> 4x
    t = m.size_trade(1000, 1.0, "conservative", "LONG", 100.0, 99.0, 20)
    assert t["leverage"] == 4 and t["margin_usd"] <= 300 and t["liquidation"] < 99.0


def test_size_respects_safe_max_and_open_risk():
    s = m.size_trade(1000, 1.0, "conservative", "LONG", 100.0, 99.0, 2)   # safe max 2x: shrink instead
    assert s["leverage"] == 2 and s["margin_usd"] <= 300.01 and s["risk_usd"] < 10 and s["note"]
    full = m.size_trade(1000, 1.0, "conservative", "LONG", 100.0, 95.0, 10, open_risk_usd=40.0)  # 4% limit used
    assert not full["ok"]


def test_main_plan_trades_are_managed_as_tested():
    from datetime import UTC, datetime

    import numpy as np
    import pandas as pd

    from app.models import MyTrade
    from app.portfolio import my_trades as mt

    t = MyTrade(symbol="X", venue="futures", side="SHORT", leverage=1, entry=100.0, qty=1, stop=107.0, target=79.0,
                risk_usd=7, budget_usd=1000, mode="conservative", source="planner", opened_at=datetime(2026, 1, 1, tzinfo=UTC),
                status="open", partial_done=False)
    idx = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    calm = pd.DataFrame({"open": 100.0, "high": 103.0, "low": 95.0, "close": 99.0, "atr": np.full(5, 4.0)}, index=idx)
    s = mt.plan_state(t, calm)
    assert s["action"] == "hold" and "day 5 of 14" in s["text"] and "Don't move the stop" in s["text"]
    hit = calm.assign(low=[95.0, 90.0, 85.0, 80.0, 78.5])
    assert mt.plan_state(t, hit)["action"] == "close" and "Take profit" in mt.plan_state(t, hit)["text"]
    long_hold = pd.DataFrame({"open": 100.0, "high": 103.0, "low": 95.0, "close": 99.0, "atr": 4.0},
                             index=pd.date_range("2026-01-01", periods=15, freq="D", tz="UTC"))
    assert "14-day limit" in mt.plan_state(t, long_hold)["text"]


def test_pre_trade_check_verdicts():
    plan = {"symbol": "XUSDT", "covered": True, "engine": "unified", "clear": True, "side": "SHORT", "status": "trade",
            "rank": 1, "market": {"trend": "down"}, "live_state": {"state": "valid"}}
    acct = {"drawdown_pct": 0.0, "loss_streak": 0, "equity": 1000.0, "open_risk_usd": 0.0}
    risk = {"mode": "conservative"}
    good = m.assess("SHORT", plan, acct, risk, [], {"label": "High", "win_rate": 0.51, "avg_r": 0.45}, 1.0, None)
    assert good["verdict"] == "take"
    against = m.assess("LONG", plan, acct, risk, [], None, 1.0, None)        # the main signal says SHORT; long in a falling market
    assert against["verdict"] == "avoid" and any("other way" in x["text"] for x in against["reasons"])
    held = m.assess("SHORT", plan, acct, risk, [{"symbol": "XUSDT"}], None, 1.0, None)
    assert held["verdict"] == "avoid"
    past_stop = m.assess("SHORT", plan, acct, risk, [], None, 1.2, {"entry": 1.0, "stop": 1.1, "target": 0.7})
    assert past_stop["verdict"] == "avoid"
    no_acct = m.assess("SHORT", {**plan, "status": "watch"}, None, None, [], {"label": "Medium", "win_rate": 0.4, "avg_r": 0.05}, 1.0, None)
    assert no_acct["verdict"] == "careful"
