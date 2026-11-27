import numpy as np
import pandas as pd

from app.sim import carry
from app.sim.engine import START, Market


def _market(n=30, fund=0.001, perp_drift=0.0):
    dates = pd.date_range("2024-01-01", periods=n)
    syms = pd.Index(["BTCUSDT"])
    s = np.linspace(100, 130, n)[:, None]  # spot trends up: a hedged book must not care
    p = s * (1 + perp_drift * np.arange(n)[:, None])  # basis widening when perp_drift > 0
    m = Market(dates=dates, symbols=syms, o=s.copy(), h=s * 1.01, l=s * 0.99, c=s.copy(), univ=np.ones((n, 1), bool),
               cost=np.zeros((n, 1)), fund=np.full((n, 1), fund), atr=np.ones((n, 1)), comps={}, signs={})
    return m, (p.copy(), p * 1.01, p.copy())


def _cfg():
    return carry.CarryConfig(start="2024-01-01")


def test_hedged_book_earns_funding_and_ignores_price():
    m, perp = _market()
    r = carry.run(_cfg(), m, perp)
    e = r["eq"]
    assert e[-1] > START and r["totals"]["funding"] > 0
    assert abs(r["totals"]["basis"]) < 1e-6  # spot and perp moved together: no basis PnL
    assert all(np.diff(e[~np.isnan(e)][7:]) >= -1e-9)  # once in, every day is a win


def test_widening_basis_costs_the_short_perp_leg():
    m, perp = _market(fund=0.0002, perp_drift=0.002)  # perp runs away from spot
    r = carry.run(_cfg(), m, perp)
    assert r["eq"][-1] < START  # the measured basis loss beats the funding: the old "basis = 0" shortcut would miss this


def test_no_entry_when_funding_is_negative():
    m, perp = _market(fund=-0.0005)
    r = carry.run(_cfg(), m, perp)
    assert r["trades"] == [] and r["open"] == [] and np.nanmax(np.abs(r["eq"] - START)) < 1e-9
