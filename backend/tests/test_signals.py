from app.agents import desk_tools
from app.portfolio import signals
from app.risk import futures as fut
from tests.test_intel_desk import _acct
from tests.test_system_risk import _frames


def _setup(monkeypatch):
    monkeypatch.setattr(desk_tools, "breakout_records", lambda: {"C1USDT": {"trades": 9, "win_rate": 0.44, "avg_r": 0.3,
                                                                           "best_r": 4.0, "since": "2020-05-01", "last_3": []}})
    frames = _frames(n_coins=5)
    frames["BTCUSDT"] = frames["C0USDT"]
    return frames, _acct()


def test_reason_and_top_list(monkeypatch):
    frames, acct = _setup(monkeypatch)
    cards = desk_tools.analysis_cards(acct, frames, {"C1USDT": 15.0})
    top = signals.top_list(cards, 3)
    assert [t["rank"] for t in top] == [1, 2, 3] and all(t["reason"].endswith(".") for t in top)
    r = desk_tools.reason(cards["C1USDT"])
    assert "crowded: funding +15%/yr" in r and "record 9 breakouts, 44% won, +0.30R" in r and "safe leverage" in r
    assert desk_tools.reason({}) == "no analysis available."


def test_signal_cards_mark_live_skipped_near_and_carry_the_ticket(monkeypatch):
    frames, acct = _setup(monkeypatch)
    order = {"symbol": "C1USDT", "close": 100.0, "stop": 90.0, "target_2r": 120.0, "size_usd": 10.0, "risk_usd": 1.0,
             "stop_pct": 10.0, "momentum_60d_pct": 5.0, "action": "BUY at next daily open"}
    acct["next_orders"] = [order]
    acct["watchlist"] = [{"symbol": "C2USDT", "close": 50.0, "momentum_60d_pct": 2.0, "action": "skip",
                          "why": "position limit reached"},
                         {"symbol": "C3USDT", "close": 97.0, "breakout_level": 100.0, "distance_pct": 3.1, "action": "watch"}]
    cards = desk_tools.analysis_cards(acct, frames, {})
    out = signals.signal_cards(acct, cards)
    assert [s["state"] for s in out] == ["live", "skipped", "near"]
    live = out[0]
    assert live["selectable"] and live["qty"] == 0.1 and live["margin_usd"] == 10.0 and live["leverage"] == 1
    assert live["safe_leverage"] == 6 and live["liquidation"] is None and live["reason"] and live["rule"]
    assert not out[1]["selectable"] and "position limit" in out[1]["why"]
    assert not out[2]["selectable"] and "daily close above 100" in out[2]["why"] and out[2]["distance_pct"] == 3.1
    # futures: the ticket's capped leverage, margin, liquidation and warning flow into the card
    acct["venue"] = "futures"
    acct["futures"] = {"leverage_requested": 20, "open": [],
                       "tickets": [fut.ticket(order, 20, fut.Filters(0.01, 0.01, 0.01, 5.0))]}
    live = signals.signal_cards(acct, cards)[0]
    assert live["leverage"] == 6 and live["liquidation"] and live["margin_usd"] == round(10 / 6, 2) and live["warnings"]
    # a refused ticket is not selectable
    acct["futures"]["tickets"] = [fut.ticket(order | {"size_usd": 3.0}, 3, fut.Filters(0.01, 0.01, 0.01, 5.0))]
    live = signals.signal_cards(acct, cards)[0]
    assert not live["selectable"] and "minimum" in live["why"]


def test_blocked_cards_when_btc_is_below_its_200_day_average(monkeypatch):
    frames, acct = _setup(monkeypatch)
    acct["market"]["btc_above_200d"] = False
    acct["watchlist"] = [{"symbol": "C3USDT", "close": 97.0, "breakout_level": 100.0, "distance_pct": 3.1,
                          "action": "watch (blocked while BTC is below its 200-day average)"}]
    cards = desk_tools.analysis_cards(acct, frames, {})
    out = signals.signal_cards(acct, cards)
    assert out[0]["state"] == "blocked" and "BTC is below" in out[0]["why"] and cards["C3USDT"]["verdict"].startswith("blocked")
