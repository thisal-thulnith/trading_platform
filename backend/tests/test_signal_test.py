from app.ml import signal_plan as sp
from app.ml import signal_test as st

H = 3600
T0 = 1_000_000 * H          # signal given exactly at an hour open
SIG = {"created_s": T0, "side": "LONG", "entry": 100.0, "entry_type": "market", "stop": 97.0, "tp1": 103.0,
       "tp2": 106.0, "fast_stop": 99.0, "fast_tp": 103.0}
FEE_R = sp.FEE * 100 / 3


def bars(*hlc, start=T0):
    return [{"time": start + i * H, "high": h, "low": lo, "close": c} for i, (h, lo, c) in enumerate(hlc)]


def test_candles_before_the_signal_are_ignored_and_stop_is_minus_one_r():
    b = bars((101, 90, 95), start=T0 - H) + bars((100.5, 96.5, 97))       # the dip to 90 happened before the signal
    g = st.grade(SIG, b, T0 + 2 * H)
    assert g["swing"]["outcome"] == "stop" and abs(g["swing"]["r"] - (-1 - FEE_R)) < 1e-9
    assert g["fast"]["outcome"] == "stop"


def test_tp1_then_entry_tp2_and_same_candle_counts_as_stop():
    g = st.grade(SIG, bars((103.5, 100.5, 103), (101, 99.8, 100)), T0 + 3 * H)
    assert g["swing"]["outcome"] == "tp1_then_entry" and abs(g["swing"]["r"] - (0.5 - FEE_R)) < 1e-9
    assert g["fast"]["outcome"] == "target" and abs(g["fast"]["r"] - (3.0 - sp.FEE * 100)) < 1e-9
    g = st.grade(SIG, bars((103.5, 100.5, 103), (106.5, 102, 106)), T0 + 3 * H)
    assert g["swing"]["outcome"] == "tp2" and abs(g["swing"]["r"] - (1.5 - FEE_R)) < 1e-9
    g = st.grade(SIG, bars((104, 96, 100)), T0 + 2 * H)
    assert g["swing"]["outcome"] == "stop"


def test_open_trade_is_marked_and_timeout_closes_at_the_last_candle():
    g = st.grade(SIG, bars((101, 99.5, 101.5)), T0 + H)
    assert g["state"] == "open" and abs(g["swing"]["r"] - (0.5 - FEE_R)) < 1e-9
    flat = bars(*[(100.5, 99.5, 100.6)] * (14 * 24 + 1))
    g = st.grade(SIG, flat, T0 + 15 * 24 * H)
    assert g["state"] == "closed" and g["swing"]["outcome"] == "timeout"
    assert g["fast"]["outcome"] == "timeout"


def test_limit_entry_waits_fills_and_expires():
    lim = {**SIG, "entry_type": "limit"}                                 # a long limit at 100, given at 102
    assert st.grade(lim, bars((102.5, 101, 102)), T0 + H)["state"] == "waiting"
    g = st.grade(lim, bars(*[(103, 101, 102)] * 49), T0 + 49 * H)
    assert g["state"] == "not_filled" and g["swing"] is None
    g = st.grade(lim, bars((102, 99.9, 100.2), (103.5, 100.1, 103)), T0 + 2 * H)   # filled, then TP1
    assert g["filled_s"] == T0 and g["state"] == "open" and g["swing"]["outcome"] == "tp1_hit"
    g = st.grade(lim, bars((104, 96, 98)), T0 + H)                       # fill candle also hits the stop
    assert g["swing"]["outcome"] == "stop"


def test_short_mirrors_long():
    s = {**SIG, "side": "SHORT", "stop": 103.0, "tp1": 97.0, "tp2": 94.0, "fast_stop": 101.0, "fast_tp": 97.0}
    g = st.grade(s, bars((99.5, 96.5, 97), (98, 93.5, 94)), T0 + 3 * H)
    assert g["swing"]["outcome"] == "tp2" and g["fast"]["outcome"] == "target"


def test_report_maths():
    assert st._wilson(6, 10) == (0.352, 0.806)
    assert st._usd(-1.0, 100, 5, 10.0) == -50.0
    items = [{"state": "closed", "r": 1.0, "outcome": "tp2", "fast_state": "closed", "fast_r": -1.0, "fast_outcome": "stop",
              "usd": 10.0, "fast_usd": -5.0, "payload": {"expected": {"win_rate": 0.58, "avg_r": 0.1}}}] * 10
    g = st._group(items, "swing")
    assert g["finished"] == 10 and g["win_rate"] == 1.0 and g["usd"] == 100.0 and g["expected_avg_r"] == 0.1
    assert st._verdict(g)["ready"] and not st._verdict({**g, "finished": 4})["ready"]
