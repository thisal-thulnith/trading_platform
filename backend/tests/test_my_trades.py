from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.models import MyTrade
from app.portfolio import budget
from app.portfolio import my_trades as mt
from tests.test_system_risk import _frames


def test_budget_plan_counts_tradeable_coins_and_recommends():
    frames = _frames(n_coins=8)
    p = budget.plan(100, "conservative", "futures", 20, frames, {}, None)
    assert p["risk_per_trade_usd"] == 1.0 and p["coins_total"] == 8 and p["max_positions"] == 4
    assert p["coins_tradeable"] == 8 and p["typical_leverage"] <= 20 and not p["recommendation"]["switch"]
    tiny = budget.plan(20, "conservative", "futures", 20, frames, {}, None)  # $0.20 risk: every ticket under $5
    assert tiny["coins_tradeable"] == 0 and tiny["recommendation"]["switch"]
    rec = tiny["recommendation"]
    assert rec["mode"] != "conservative" or (rec["budget_needed_usd"] or 0) > 20
    assert all(r["budget_needed_usd"] >= 20 for r in tiny["too_small"])


def _trade(**kw) -> MyTrade:
    base = {"symbol": "SOLUSDT", "venue": "spot", "side": "LONG", "leverage": 1.0, "entry": 100.0, "qty": 1.0,
            "stop": 90.0, "target": 120.0, "risk_usd": 10.0, "budget_usd": 100.0, "mode": "growth",
            "partial_done": False, "status": "open", "source": "manual",
            "opened_at": datetime(2024, 6, 1, 12, tzinfo=UTC)}
    return MyTrade(**(base | kw))


def _frame(lows=None, highs=None, n=10, start="2024-05-25"):
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    f = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 2.0}, index=idx)
    for day, v in (lows or {}).items():
        f.loc[pd.Timestamp(day, tz="UTC"), "low"] = v
    for day, v in (highs or {}).items():
        f.loc[pd.Timestamp(day, tz="UTC"), "high"] = v
    return f


def test_plan_state_reads_only_the_candles_since_the_entry():
    t = _trade()
    assert mt.plan_state(t, None)["action"] == "hold"
    # a dip below the stop BEFORE the entry day does not count; one after it does
    assert mt.plan_state(t, _frame(lows={"2024-05-28": 80.0}))["action"] == "hold"
    s = mt.plan_state(t, _frame(lows={"2024-06-02": 89.0}))
    assert s["action"] == "close" and s["level"] == "warn" and "touched" in s["text"]
    s = mt.plan_state(t, _frame(highs={"2024-06-02": 121.0}))
    assert s["action"] == "partial" and "sell a third" in s["text"]
    # after the partial the stop sits at entry and trails 3 ATR below the close
    t2 = _trade(partial_done=True, partial_price=120.0, partial_qty=1 / 3, partial_at=datetime(2024, 6, 2, tzinfo=UTC),
                stop=100.0)
    f = _frame(n=12)
    f["low"], f["high"], f["close"] = 101.0, 112.0, 110.0  # never back to the entry-level stop; trail = 110 - 6 = 104
    s = mt.plan_state(t2, f)
    assert s["action"] == "raise_stop" and abs(s["recommended_stop"] - 104.0) < 1e-9
    # the same frame with one dip to the entry-level stop after the partial: close
    f.loc[f.index[-2], "low"] = 100.0
    assert mt.plan_state(t2, f)["action"] == "close"
    assert mt.plan_state(_trade(), _frame(n=70))["action"] == "close"  # 60-day limit


def test_card_marks_to_market_like_binance():
    t = _trade(venue="futures", leverage=3.0, qty=0.3, risk_usd=3.0)
    c = mt.card(t, 110.0, mt.plan_state(t, None))
    assert c["margin_usd"] == 10.0 and c["liquidation"] < 90.0 and c["margin_mode"] == "Isolated"
    assert c["pnl_usd"] == pytest.approx(3.0 - 0.3 * 100 * 0.0005, abs=0.01) and c["roe_pct"] > 29 and c["r"] > 0.9
    assert c["pnl_pct"] == 10.0 and c["open_risk_usd"] == 3.0 and c["action"] == "hold"


async def test_add_partial_close_and_stats(db):
    t = await mt.add(db, None, {"symbol": "solusdt", "venue": "futures", "leverage": 3, "entry": 100.0, "qty": 0.3,
                                "stop": 90.0, "budget_usd": 100, "mode": "growth", "source": "system"})
    assert t.symbol == "SOLUSDT" and t.target == 120.0 and t.risk_usd == pytest.approx(3.0 + 0.3 * 190 * 0.0005)
    await mt.partial(db, t, 120.0)
    assert t.partial_done and t.stop == 100.0 and t.partial_qty == pytest.approx(0.1)
    await mt.close(db, t, 130.0, "trail")
    # banked 0.1 * 20 - fee, then 0.2 * 30 - fees: about $8 on $3 risk
    assert t.status == "closed" and t.pnl_usd == pytest.approx(7.97, abs=0.05) and t.r_multiple > 2.5
    with pytest.raises(ValueError):
        await mt.close(db, t, 1.0)
    trades = await mt.list_trades(db, None)
    assert len(trades) == 1 and await mt.get(db, "someone-else", t.id) is None
    s = mt.stats([], trades)
    assert s["closed"] == 1 and s["win_rate"] == 1.0 and s["realized_usd"] == pytest.approx(7.97, abs=0.05)
    for bad in ({"symbol": "X", "entry": 100, "qty": 1, "stop": 100}, {"symbol": "X", "entry": 100, "qty": 0, "stop": 90},
                {"symbol": "X", "venue": "margin", "entry": 100, "qty": 1, "stop": 90}):
        with pytest.raises(ValueError):
            await mt.add(db, None, bad)


def test_coach_flags_leverage_and_risk_above_the_plan():
    frames = _frames(n_coins=4)
    plan = budget.plan(100, "growth", "futures", 3, frames, {}, None)
    risky = _trade(venue="futures", leverage=20.0, risk_usd=8.0, mode="growth")
    msgs = mt.coach([], [risky], mt.stats([], []), plan, None, "growth")
    assert [m["agent"] for m in msgs] == ["planner", "manager", "coach"]
    coach = msgs[-1]
    assert coach["level"] == "warn" and "20x is above the safe" in coach["text"] and "risked $8.00" in coach["text"]
    assert "No open trades" in msgs[1]["text"] and "$2.0 per trade" in msgs[0]["text"]
    assert np.isfinite(plan["typical_position_usd"])
