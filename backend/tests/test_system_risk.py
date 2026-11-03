import numpy as np
import pandas as pd

from app.risk.portfolio import RiskRules, run, summary
from app.strategies import daily_trend as dt


def _coin(n=320, drift=0.004, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, 0.03, n)))
    t = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"t": t, "open": o, "high": np.maximum(o, close) * 1.01, "low": np.minimum(o, close) * 0.99,
                         "close": close})


def _frames(btc_drift=0.004, n_coins=8):
    btc = _coin(drift=btc_drift, seed=99)
    ok = dt.btc_filter(btc)
    return {f"C{i}USDT": dt.prepare(_coin(seed=i), ok) for i in range(n_coins)}


def test_every_trade_risks_about_one_percent_and_limits_hold():
    frames = _frames()
    rules = RiskRules()
    res = run(frames, rules, pd.Timestamp("2024-08-01", tz="UTC"))
    assert res.trades, "an uptrending market must produce trades"
    for t in res.trades:
        assert t["risk_usd"] <= 0.011 * 200  # 1 % of equity (equity stays far below $200 here)
        assert t["r"] >= -3  # a loss is about 1R; only a gap through the stop can cost more
    assert max(e["open"] for e in res.equity) <= rules.max_positions
    s = summary(res, rules)
    assert s["trades"] == len(res.trades) and "max_drawdown_pct" in s


def test_bear_market_filter_keeps_the_account_in_cash():
    frames = _frames(btc_drift=-0.004)
    res = run(frames, RiskRules(), pd.Timestamp("2024-08-01", tz="UTC"))
    assert not res.trades and res.equity[-1]["equity"] == 100.0


def test_plan_has_two_r_target_and_refuses_tight_stops():
    p = dt.plan(100.0, 2.0)
    assert abs(p["target"] - 100 - 2 * (100 - p["stop"])) < 1e-9 and p["stop"] == 95.0
    assert dt.plan(100.0, 0.2) is None  # 0.5 % stop: costs would eat the trade


def test_loss_guard_pauses_new_entries():
    rules = RiskRules(pause_window=3, pause_below_r=10.0)  # impossible bar: pauses after the first 3 trades
    res = run(_frames(), rules, pd.Timestamp("2024-08-01", tz="UTC"))
    assert len(res.trades) <= 3 + rules.max_positions
    assert any("paused" in s["why"] for s in res.skipped)


def test_risk_modes_scale_risk_and_limits():
    from app.risk.portfolio import RISK_MODES, rules_for
    c, g, a = (rules_for(m) for m in ("conservative", "growth", "aggressive"))
    assert c.risk_pct < g.risk_pct < a.risk_pct and set(RISK_MODES) == {"conservative", "growth", "aggressive"}
    for r in (c, g, a):
        assert r.max_open_risk_pct >= r.risk_pct and r.max_position_pct <= 50


def test_system_desk_agents_speak_from_account_numbers():
    from app.agents import system_desk
    frames = _frames()
    frames["BTCUSDT"] = frames["C0USDT"]
    acct = {"market": {"btc_above_200d": True, "btc_close": float(frames["BTCUSDT"].close.iloc[-1])},
            "account": {"equity_usd": 100.0, "open_risk_usd": 0.0, "drawdown_pct": 0.0, "paused": False, "trades": 0},
            "rules": RiskRules().d() | {"risk_now_pct": 1.0}, "open_positions": [], "watchlist": [],
            "next_orders": [{"symbol": "C1USDT", "momentum_60d_pct": 12.0, "size_usd": 9.0, "stop": 90.0,
                             "stop_pct": 10.0, "target_2r": 120.0, "risk_usd": 1.0}]}
    msgs = system_desk.brief(acct, frames, "conservative")
    assert [m["agent"] for m in msgs] == ["regime", "scout", "risk", "leverage", "auditor", "manager"]
    assert "BUY C1" in msgs[-1]["text"] and "$1.00" in msgs[-1]["text"]
    assert "Spot account" in msgs[3]["text"]


def test_system_desk_leverage_agent_caps_the_ticket_and_the_manager_quotes_it():
    from app.agents import system_desk
    from app.risk import futures as fut
    frames = _frames()
    frames["BTCUSDT"] = frames["C0USDT"]
    order = {"symbol": "C1USDT", "close": 100.0, "momentum_60d_pct": 12.0, "size_usd": 9.0, "stop": 90.0,
             "stop_pct": 10.0, "target_2r": 120.0, "risk_usd": 1.0}
    acct = {"market": {"btc_above_200d": True, "btc_close": float(frames["BTCUSDT"].close.iloc[-1])},
            "account": {"equity_usd": 100.0, "open_risk_usd": 0.0, "drawdown_pct": 0.0, "paused": False, "trades": 0},
            "rules": RiskRules().d() | {"risk_now_pct": 1.0}, "open_positions": [], "watchlist": [],
            "next_orders": [order], "venue": "futures",
            "futures": {"leverage_requested": 20, "open": [],
                        "tickets": [fut.ticket(order, 20, fut.Filters(0.01, 0.01, 0.01, 5.0))]}}
    msgs = system_desk.brief(acct, frames, "growth")
    lev = next(m for m in msgs if m["agent"] == "leverage")
    assert lev["level"] == "warn" and "6x" in lev["text"] and "asked 20x" in lev["text"]
    assert "LONG C1" in msgs[-1]["text"] and "6x isolated" in msgs[-1]["text"] and "0.09 coins" in msgs[-1]["text"]


def test_system_desk_speaks_from_the_top_list_with_carry_data_present():
    from app.agents import system_desk
    frames = _frames()
    frames["BTCUSDT"] = frames["C0USDT"]
    acct = {"market": {"btc_above_200d": True, "btc_close": float(frames["BTCUSDT"].close.iloc[-1])},
            "account": {"equity_usd": 100.0, "open_risk_usd": 0.0, "drawdown_pct": 0.0, "paused": False, "trades": 0},
            "rules": RiskRules().d() | {"risk_now_pct": 1.0}, "open_positions": [], "watchlist": [], "next_orders": [],
            "carry": {"top_7d_apr": [{"symbol": "C1USDT", "apr_pct": 12.0}], "enter_apr_pct": 10.0, "worth_it": ["C1USDT"],
                      "account_apr_pct": 1.2, "research": None},
            "top_list": [{"symbol": "C2USDT", "reason": "2.0% below its 20-day high; 60-day +5%."},
                         {"symbol": "C3USDT", "reason": "4.0% below its 20-day high."}],
            "budget_plan": {"budget_usd": 100, "typical_position_usd": 8.0, "coins_tradeable": 7, "coins_total": 8,
                            "recommendation": {"switch": False, "text": "fits"}}}
    msgs = {m["agent"]: m for m in system_desk.brief(acct, frames, "conservative")}
    assert "Top list (analysed): C2 — 2.0% below" in msgs["scout"]["text"]
    assert "Watch: C2, C3" in msgs["manager"]["text"] and "C1 +12.0%" in msgs["carry"]["text"]
    assert "7 of 8 coins big enough" in msgs["risk"]["text"]


def test_pause_expires_so_the_account_can_trade_again():
    rules = RiskRules(pause_window=3, pause_below_r=10.0, pause_days=30)
    res = run(_frames(), rules, pd.Timestamp("2024-08-01", tz="UTC"))
    paused_days = [s["day"] for s in res.skipped if "paused" in s["why"]]
    assert paused_days and len(res.trades) > 3  # trades resume after each 30-day pause
