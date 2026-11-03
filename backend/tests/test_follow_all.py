from datetime import UTC, datetime, timedelta

from app.portfolio.follow_all import FollowTrade, simulate_follow_all

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _t(i, state, r=None, entered=0, closed=None, direction="LONG", symbol="X"):
    return FollowTrade(id=str(i), symbol=symbol, timeframe="1h", strategy="s", direction=direction, state=state,
                       entered_at=T0 + timedelta(hours=entered),
                       closed_at=T0 + timedelta(hours=closed) if closed is not None else None,
                       fill=100.0, stop=98.0, entry_low=99.0, entry_high=101.0, r_multiple=r)


def test_compounds_closed_trades_in_time_order():
    res = simulate_follow_all([_t(1, "STOP_HIT", -1.0, 0, 1), _t(2, "TARGET_HIT", 2.0, 2, 3)], {}, 100, 1)
    # -1% of 100 = -1, then +2R on 1% of 99 = +1.98
    assert abs(res["realized_pnl"] - 0.98) < 1e-9
    assert res["wins"] == 1 and res["losses"] == 1
    assert res["max_drawdown_pct"] > 0


def test_open_trade_marked_to_market_and_sized_on_known_equity():
    res = simulate_follow_all([_t(1, "STOP_HIT", -1.0, 0, 5), _t(2, "ACTIVE", entered=1)], {"X": 102.0}, 100, 1)
    open_row = next(r for r in res["trades"] if r["status"] == "OPEN")
    assert abs(open_row["risk_usd"] - 1.0) < 1e-9  # loss closed later -> not known at entry
    # +2 price on 2 planned risk = +1R minus entry fee (0.05/2 R)
    assert abs(open_row["r"] - (1 - 0.025)) < 1e-9
    assert abs(res["unrealized_pnl"] - open_row["pnl_usd"]) < 1e-12
    assert abs(res["equity"] - (100 - 1 + open_row["pnl_usd"])) < 1e-9


def test_unfilled_and_unresolved_are_skipped():
    res = simulate_follow_all([_t(1, "EXPIRED", None, 0, 1)], {}, 100, 1)
    assert res["trades_total"] == 0 and res["equity"] == 100


def test_same_bar_entry_and_stop():
    res = simulate_follow_all([_t(1, "STOP_HIT", -1.5, 3, 3)], {}, 100, 1)
    assert abs(res["equity"] - 98.5) < 1e-9
