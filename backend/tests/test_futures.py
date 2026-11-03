from app.risk import futures as fut


def _order(**kw):
    return {"symbol": "SOLUSDT", "close": 100.0, "stop": 90.0, "target_2r": 120.0, "size_usd": 50.0, "risk_usd": 5.0,
            "stop_pct": 10.0} | kw


def test_liquidation_price_and_safe_leverage():
    assert abs(fut.liquidation_price(100, 20, "XUSDT") - 96.0) < 1e-9  # 1/20 - 1 % MMR = 4 % below entry
    assert abs(fut.liquidation_price(100, 20, "BTCUSDT") - 95.4) < 1e-9
    assert fut.safe_leverage(0.166, "XUSDT") == 3  # the system's typical 2.5-ATR stop
    assert fut.safe_leverage(0.02, "XUSDT") == 25
    assert fut.safe_leverage(0.001, "BTCUSDT") == fut.MAX_LEVERAGE
    assert fut.safe_leverage(5.0, "XUSDT") == 1
    # the liquidation of a safe ticket is always at least the buffer times the stop distance away
    for stop in (0.015, 0.05, 0.1, 0.2):
        lev = fut.safe_leverage(stop, "XUSDT")
        assert fut.liq_distance(lev, "XUSDT") >= fut.LIQ_BUFFER * stop - 1e-9 or lev == 1


def test_round_step_rounds_down_to_the_exchange_step():
    assert fut.round_step(0.123456, 0.001) == 0.123
    assert fut.round_step(7.9, 1) == 7
    assert fut.round_step(100.07, 0.1) == 100.0
    assert fut.round_step(0.5, 0.01) == 0.5


def test_ticket_caps_leverage_so_the_stop_comes_before_liquidation():
    t = fut.ticket(_order(), 20, fut.Filters(0.01, 0.01, 0.01, 5.0), funding_apr_pct=10.0)
    assert (t["leverage_set"], t["leverage_requested"], t["leverage_safe_max"]) == (6, 20, 6)
    assert t["liquidation"] < t["stop"]["trigger"] < t["entry_ref"] < t["take_profit"]["trigger"]
    assert t["warnings"] and "liquidated" in t["warnings"][0] and "capped at 6x" in t["warnings"][0]
    assert t["qty"] == 0.5 and t["notional_usd"] == 50.0 and t["margin_usd"] == round(50 / 6, 2)
    assert t["take_profit"]["qty"] == 0.16 and t["take_profit"]["reduce_only"] and t["stop"]["reduce_only"]
    assert t["stop"]["type"] == "STOP_MARKET" and t["take_profit"]["type"] == "TAKE_PROFIT_MARKET"
    assert t["funding_usd_per_day"] > 0 and t["fees_usd_round_trip"] == 0.05 and t["refused"] is None


def test_ticket_accepts_safe_leverage_and_refuses_orders_under_the_exchange_minimum():
    t = fut.ticket(_order(), 3, fut.Filters(0.01, 0.01, 0.01, 5.0))
    assert t["leverage_set"] == 3 and not t["warnings"] and t["refused"] is None and t["funding_usd_per_day"] is None
    small = fut.ticket(_order(symbol="BTCUSDT", close=100_000.0, stop=90_000.0, target_2r=120_000.0, size_usd=30.0), 3,
                       None)
    assert small["refused"] and "50 USDT minimum" in small["refused"]  # default BTCUSDT filters
    tiny = fut.ticket(_order(size_usd=20.0), 3, fut.Filters(0.1, 0.1, 0.01, 5.0))
    assert tiny["refused"] is None and tiny["qty"] == 0.2 and tiny["take_profit"] is None  # a third of 0.2 < the 0.1 step


def test_open_position_view_judges_leverage_from_the_entry_stop_not_todays_price():
    # INJ-like: entry 8.5453, stop 6.5615 (2.5 ATR = 23 % at entry), now 16 % under water
    v = fut.position_view("INJUSDT", 8.5453, 6.5615, 8.5453 - 6.5615, 0.5, 7.185, 20)
    assert v["leverage_set"] == 2 and v["leverage_safe_max"] == 2
    assert v["liquidation"] < 6.5615 < 7.185 and v["liquidation_below_stop"]
    # after the 2R partial the stop sits at entry; the cap still comes from the original stop distance
    w = fut.position_view("INJUSDT", 8.5453, 8.5453, 8.5453 - 6.5615, 0.5, 11.0, 20)
    assert w["leverage_set"] == 2 and w["liquidation_below_stop"] and w["margin_usd"] == round(0.5 * 11.0 / 2, 2)


def test_venue_rules_describe_the_costs():
    v = fut.venue_rules()
    assert v["taker_fee_pct"] == 0.05 and v["max_leverage"] == 50 and v["mmr_pct"]["BTCUSDT"] == 0.4
