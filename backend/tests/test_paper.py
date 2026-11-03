import pytest

from app.models import PaperAccount, User
from app.portfolio.paper import apply_funding, check_exits, open_position, summary


async def make_acct(db, capital=1000.0):
    u = User(email="t@example.com", password_hash="x")
    db.add(u)
    await db.flush()
    a = PaperAccount(user_id=u.id, starting_capital=capital, cash=capital,
                     risk={"taker_fee": 0.0, "slippage_bps": 0.0, "max_leverage": 5})
    db.add(a)
    await db.commit()
    return a


async def test_risk_sized_trade_and_stop(db):
    a = await make_acct(db)
    r = await open_position(db, a, "BTCUSDT", "LONG", 100.0, 98.0, 104.0, {"BTCUSDT": 100.0})
    assert r["ok"] and r["sizing"]["risk_amount"] == pytest.approx(10.0)
    await check_exits(db, a, {"BTCUSDT": 97.99})
    s = await summary(db, a, {"BTCUSDT": 97.99})
    assert s["trades"] == 1
    assert s["equity"] == pytest.approx(990.0, abs=0.01)
    assert s["closed_positions"][0]["close_reason"] == "stop"


async def test_take_profit_and_funding(db):
    a = await make_acct(db)
    await open_position(db, a, "ETHUSDT", "LONG", 100.0, 98.0, 104.0, {"ETHUSDT": 100.0})
    await apply_funding(db, a, "ETHUSDT", 0.001, 100.0)  # long pays
    await check_exits(db, a, {"ETHUSDT": 104.0})
    s = await summary(db, a, {})
    assert s["realized_pnl"] == pytest.approx(5 * 4 - 0.5, rel=1e-6)  # qty 5, +4/unit, funding -0.5
    assert s["equity"] == pytest.approx(1019.5)


async def test_max_positions_enforced(db):
    a = await make_acct(db)
    a.risk = {**a.risk, "max_open_positions": 1}
    await db.commit()
    assert (await open_position(db, a, "BTCUSDT", "LONG", 100, 98, None, {}))["ok"]
    r = await open_position(db, a, "ETHUSDT", "LONG", 100, 98, None, {})
    assert not r["ok"] and "max open positions" in r["reason"]
