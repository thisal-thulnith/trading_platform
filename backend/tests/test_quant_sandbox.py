import pandas as pd
import pytest

from app.signal import quant_sandbox as qs


def _panel(rows):
    return pd.DataFrame(rows, columns=["date", "symbol", "open", "close"])


def test_targets_fill_at_the_next_open_and_mark_to_the_close():
    p = _panel([("2026-01-01", "A", 100, 100), ("2026-01-02", "A", 110, 121), ("2026-01-03", "A", 121, 133.1)])
    def rank(d):
        return {"rows": [{"symbol": "A", "weight": 1.0, "side": "long"}], "portfolio": {"rebalance_days": 7}, "promoted": False}

    st = {"start_equity": 1000.0, "equity": 1000.0, "weights": {}, "pending": None, "last_date": "2026-01-01",
          "last_rebalance": None, "history": [], "trades": [], "costs_paid": 0.0, "started": "2026-01-01"}
    st = qs.step_panel(st, p[p.date <= "2026-01-01"], rank)
    assert st["pending"]["weights"] == {"A": 1.0} and st["equity"] == 1000.0     # decided at the close, not yet filled
    st = qs.step_panel(st, p, rank)
    cost = 1.0 * (qs.FEE + qs.SLIP) * 1000
    # filled at the 2nd day's OPEN (110), so the gap from 100 to 110 is not earned; then 110→121 and 121→133.1
    assert st["equity"] == pytest.approx((1000 - cost) * 1.10 * 1.10, abs=0.02)
    assert st["trades"][0]["date"] == "2026-01-02" and st["last_rebalance"] == "2026-01-02"


def test_a_calendar_book_rebalances_only_on_its_own_days():
    p = _panel([("2026-01-01", "A", 100, 100), ("2026-01-02", "A", 100, 100), ("2026-01-03", "A", 100, 100)])
    today = {"flag": False}

    def rank(d):
        return {"rows": [{"symbol": "A", "weight": 0.5, "side": "short"}],
                "portfolio": {"rebalance_days": 7, "rebalance_today": today["flag"]}}

    st = {"start_equity": 1000.0, "equity": 1000.0, "weights": {}, "pending": None, "last_date": "2026-01-01",
          "last_rebalance": None, "history": [], "trades": [], "costs_paid": 0.0, "started": "2026-01-01"}
    st = qs.step_panel(st, p[p.date <= "2026-01-01"], rank)
    assert st["pending"]["weights"] == {"A": -0.5}                  # first day: take the current book (short = negative)
    st = qs.step_panel(st, p[p.date <= "2026-01-02"], rank)
    assert st["pending"] is None and st["weights"] == {"A": -0.5}   # filled; not its rebalance day, so no new order
    today["flag"] = True
    st = qs.step_panel(st, p, rank)
    assert st["pending"] is not None                                 # its calendar says rebalance
