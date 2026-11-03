import pytest

from app.risk.sizing import RiskProfile, liquidation_price_isolated, rr, size_position


def test_risk_amount_matches_profile():
    p = RiskProfile(equity=1000, risk_per_trade_pct=1.0, max_leverage=10, taker_fee=0.0, slippage_bps=0)
    r = size_position(p, "BTCUSDT", "LONG", 100_000, 98_500)
    assert r["ok"]
    assert r["risk_amount"] == pytest.approx(10.0, rel=1e-6)
    assert r["qty"] == pytest.approx(10 / 1500)


def test_fees_reduce_size():
    a = size_position(RiskProfile(1000, taker_fee=0.0, slippage_bps=0, max_leverage=10), "X", "LONG", 100, 98)
    b = size_position(RiskProfile(1000, max_leverage=10), "X", "LONG", 100, 98)
    assert b["qty"] < a["qty"]
    assert b["risk_amount"] == pytest.approx(10.0, rel=1e-6)


def test_leverage_cap_and_never_all_in():
    p = RiskProfile(1000, risk_per_trade_pct=2, max_leverage=1, market="spot")
    r = size_position(p, "X", "LONG", 100, 99.9)  # tiny stop would imply huge size
    assert r["notional"] <= 1000 + 1e-9
    assert r["notes"]


def test_wrong_side_stop_rejected():
    assert not size_position(RiskProfile(1000), "X", "LONG", 100, 101)["ok"]


def test_liquidation_formula_long_10x():
    # LP = (EP/lev - EP)/(MMR - 1) = EP (1 - 1/lev)/(1 - MMR)
    lp = liquidation_price_isolated("LONG", 100.0, 10, 0.004)
    assert lp == pytest.approx(100 * 0.9 / 0.996)
    sp = liquidation_price_isolated("SHORT", 100.0, 10, 0.004)
    assert sp == pytest.approx(100 * 1.1 / 1.004)


def test_rr_net_of_costs():
    assert rr(100, 99, 102, fee=0, slip_bps=0) == pytest.approx(2.0)
    assert rr(100, 99, 102) < 2.0


def test_portfolio_budget():
    p = RiskProfile(1000, risk_per_trade_pct=2, max_portfolio_risk_pct=5)
    assert not size_position(p, "X", "LONG", 100, 98, open_risk_pct=5)["ok"]
    r = size_position(p, "X", "LONG", 100, 98, open_risk_pct=4)
    assert r["risk_pct"] == pytest.approx(1.0, rel=1e-3)
