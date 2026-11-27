"""A signal that is already over must never say 'take it'."""
from app.ml import live_status as ls

T0 = 1_700_000_000


def bars(*hl):
    return [{"time": T0 + i * 3600, "open": 0, "high": h, "low": low, "close": (h + low) / 2} for i, (h, low) in enumerate(hl)]


def test_states_in_order():
    # SHORT from 100, stop 107, target 79
    assert ls.judge("SHORT", 100, 107, 79, bars((101, 99), (102, 97)), T0, 98)["state"] == "valid"
    assert ls.judge("SHORT", 100, 107, 79, bars((101, 99), (108, 99)), T0)["state"] == "stop_hit"
    assert ls.judge("SHORT", 100, 107, 79, bars((101, 90), (95, 78)), T0)["state"] == "target_hit"
    assert ls.judge("SHORT", 100, 107, 79, bars((101, 90)), T0, 89)["state"] == "ran_away"     # 52% of the way
    # a candle touching both counts as the stop (as in the backtest)
    assert ls.judge("LONG", 100, 95, 115, bars((116, 94)), T0)["state"] == "stop_hit"


def test_only_candles_after_the_signal_count():
    before = [{"time": T0 - 3600, "open": 0, "high": 120, "low": 90, "close": 100}]
    assert ls.judge("LONG", 100, 95, 115, before + bars((101, 99)), T0, 100)["state"] == "valid"
    assert ls.judge("LONG", 100, 95, 115, [], T0)["state"] == "unknown"
