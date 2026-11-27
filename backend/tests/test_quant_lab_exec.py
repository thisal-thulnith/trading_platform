"""Quant lab execution model tests (synthetic data only, no network): next-open fills, maker fill rule, no-trade
band, calendar-anchored rebalancing and the promoted live book."""
import json

import numpy as np
import pandas as pd
import pytest

from app.quant.lab import data as qd
from app.quant.lab import portfolio as qp
from app.quant.lab import retest as rt
from app.quant.lab.factors import FACTORS, build_wide, universe
from tests.test_quant_lab import synth_panel


def _wide(seed=0, n_sym=30, n_days=400):
    panel, fund = synth_panel(n_sym=n_sym, n_days=n_days, seed=seed)
    # make opens differ from the previous close so the two execution models are distinguishable
    rng = np.random.default_rng(seed + 100)
    panel["open"] = panel["open"] * np.exp(rng.normal(0, 0.01, len(panel)))
    panel["high"] = panel[["open", "high", "close"]].max(axis=1) * 1.01
    panel["low"] = panel[["open", "low", "close"]].min(axis=1) * 0.99
    return panel, fund


def test_legacy_config_keys_and_default_execution_unchanged():
    d = qp.PortfolioConfig().to_dict()
    assert set(d) == {"k", "side", "rebalance", "weighting", "select", "vol_target", "vol_target_ls", "max_gross_long",
                      "max_gross_short", "coin_cap", "aum", "max_adv_participation", "turnover_limit", "dust",
                      "cost_mult", "lag", "cov_window"}      # old trial keys stay valid
    m = qp.PortfolioConfig(order_type="maker", execution="next_open", band=0.02).to_dict()
    assert m["maker_fee"] == 0.0002 and m["band"] == 0.02 and qp.PortfolioConfig(**m) == qp.PortfolioConfig(
        order_type="maker", execution="next_open", band=0.02)


def test_next_open_returns_are_open_to_open_and_ignore_the_signal_close():
    panel, fund = _wide()
    w = build_wide(panel, fund)
    u = universe(w, 90, 5e6)
    mkt = qp.prepare_market(w, u, fill="next_open")
    o = w.open
    expect = (o.shift(-2) / o.shift(-1) - 1).to_numpy()
    T = len(w.dates)
    ok = np.isfinite(expect[: T - 2])
    assert np.allclose(mkt.r_next[: T - 2][ok], expect[: T - 2][ok])
    assert np.allclose(np.nan_to_num(mkt.fill_px[: T - 1]), np.nan_to_num(o.shift(-1).to_numpy()[: T - 1]))

    # perturb every close (keeping opens): a book whose weights do not depend on closes must earn exactly the same
    rng = np.random.default_rng(1)
    S = np.where(u.to_numpy(), rng.normal(size=u.shape), np.nan)
    cfg = qp.PortfolioConfig(k=5, side="long", rebalance=1, weighting="equal", vol_target=None, coin_cap=1.0,
                             turnover_limit=None, max_adv_participation=1e9, execution="next_open")
    base = qp.simulate(S, mkt, cfg, start=w.dates[120])
    p2 = panel.copy()
    p2["close"] = p2["close"] * rng.uniform(0.5, 1.5, len(p2))
    w2 = build_wide(p2, fund)
    w2.high, w2.low = w.high, w.low
    mkt2 = qp.prepare_market(w2, u, fill="next_open")
    moved = qp.simulate(S, mkt2, cfg, start=w.dates[120])
    assert np.allclose(base.returns.to_numpy(), moved.returns.to_numpy())
    # ... while the legacy same-close book does move with the closes
    cl = dict(cfg.__dict__, execution="close")
    a = qp.simulate(S, qp.prepare_market(w, u), qp.PortfolioConfig(**cl), start=w.dates[120])
    b = qp.simulate(S, qp.prepare_market(w2, u), qp.PortfolioConfig(**cl), start=w.dates[120])
    assert not np.allclose(a.returns.to_numpy(), b.returns.to_numpy())
    with pytest.raises(ValueError):
        qp.simulate(S, mkt, qp.PortfolioConfig(**cl))     # a config must run on a market built for its fill


def test_next_open_pnl_matches_a_hand_computed_trade():
    panel, fund = _wide(seed=3, n_sym=20)
    w = build_wide(panel, fund)
    u = universe(w, 90, 0)
    mkt = qp.prepare_market(w, u, fill="next_open")
    t = 200
    j = 4
    S = np.full(u.shape, np.nan)
    S[t, j] = 1.0
    cfg = qp.PortfolioConfig(k=1, side="long", rebalance=10**6, weighting="equal", vol_target=None, coin_cap=1.0,
                             turnover_limit=None, max_adv_participation=1e9, execution="next_open", cost_mult=0.0)
    res = qp.simulate(S, mkt, cfg, start=w.dates[t], end=w.dates[t + 1])
    o = w.open.iloc[:, j]
    assert res.returns.iloc[0] == pytest.approx(o.iloc[t + 2] / o.iloc[t + 1] - 1)   # bought at the D+1 open


def test_close_marked_fills_trade_at_the_fill_price_and_mark_at_closes():
    panel, fund = _wide(seed=3, n_sym=20)
    w = build_wide(panel, fund)
    u = universe(w, 90, 0)
    t, j = 200, 4
    S = np.full(u.shape, np.nan)
    S[t, j] = 1.0
    cfg = qp.PortfolioConfig(k=1, side="long", rebalance=10**6, weighting="equal", vol_target=None, coin_cap=1.0,
                             turnover_limit=None, max_adv_participation=1e9, execution="next_vwap", cost_mult=0.0)
    mkt = qp.prepare_market(w, u, fill="next_vwap", mark="close")
    res = qp.simulate(S, mkt, cfg, start=w.dates[t], end=w.dates[t + 2])
    c = w.close.iloc[:, j]
    vw = (w.quote_volume / w.volume).iloc[:, j]
    assert res.returns.iloc[0] == pytest.approx(c.iloc[t + 1] / vw.iloc[t + 1] - 1)   # bought at the D+1 VWAP
    assert res.returns.iloc[1] == pytest.approx(c.iloc[t + 2] / c.iloc[t + 1] - 1)    # then marked at closes
    with pytest.raises(ValueError):
        qp.prepare_market(w, u, fill="close", mark="close")


def test_maker_fill_rule():
    panel, fund = _wide(seed=2, n_sym=10)
    w = build_wide(panel, fund)
    mkt = qp.prepare_market(w, universe(w, 90, 0), fill="next_open")
    t = 150
    L = mkt.fill_px[t].copy()
    mkt.low_next[t] = L * (1 - 0.0010)          # traded exactly 10 bp through a buy limit
    mkt.high_next[t] = L * (1 + 0.0009)         # only 9 bp above a sell limit
    dw = np.zeros(len(L))
    dw[0], dw[1], dw[2] = 0.1, -0.1, 0.0
    ok = qp.maker_fills(mkt, t, dw, 10.0)
    assert ok[0] and not ok[1] and ok[2]
    assert not qp.maker_fills(mkt, t, dw, 11.0)[0]
    mkt.low_next[t] = L * (1 - 0.0009)
    assert not qp.maker_fills(mkt, t, dw, 10.0)[0]

    # in a book: unfilled orders are skipped (the slot stays empty) and filled ones pay the maker fee
    u = universe(w, 90, 0)
    rng = np.random.default_rng(0)
    S = np.where(u.to_numpy(), rng.normal(size=u.shape), np.nan)
    mk = qp.prepare_market(w, u, fill="next_open")
    mk.low_next[:] = np.inf     # no buy ever fills
    cfg = qp.PortfolioConfig(k=3, side="long", rebalance=1, vol_target=None, execution="next_open",
                             order_type="maker", turnover_limit=None)
    res = qp.simulate(S, mk, cfg, start=w.dates[120])
    assert res.exposure["long"].max() == 0 and res.costs.sum() == 0 and res.filled.sum() == 0
    mk2 = qp.prepare_market(w, u, fill="next_open")
    mk2.low_next[:] = 0.0
    mk2.high_next[:] = np.inf   # everything fills
    res2 = qp.simulate(S, mk2, cfg, start=w.dates[120])
    taker = qp.simulate(S, mk2, qp.PortfolioConfig(**dict(cfg.__dict__, order_type="taker")), start=w.dates[120])
    assert res2.filled.sum() == res2.orders.sum() > 0
    assert np.allclose(res2.gross.to_numpy(), taker.gross.to_numpy())       # same fills, same price
    assert res2.costs.sum() == pytest.approx(res2.turnover.sum() * 0.0002)
    assert res2.costs.sum() < taker.costs.sum()


def test_no_trade_band_cuts_turnover_and_still_exits():
    panel, fund = _wide(seed=4, n_sym=40, n_days=500)
    w = build_wide(panel, fund)
    u = universe(w, 90, 5e6)
    mkt = qp.prepare_market(w, u, fill="next_open")
    rng = np.random.default_rng(3)
    kw = dict(k=5, side="long_short", rebalance=1, execution="next_open", turnover_limit=None)
    # a stable ranking: turnover is only resizing (inverse vol, vol target), which the band suppresses
    fixed = np.where(u.to_numpy(), np.arange(u.shape[1], dtype=float)[None, :], np.nan)
    free = qp.simulate(fixed, mkt, qp.PortfolioConfig(**kw), start=w.dates[150])
    band = qp.simulate(fixed, mkt, qp.PortfolioConfig(**kw, band=0.02), start=w.dates[150])
    assert band.turnover.iloc[1:].mean() < 0.5 * free.turnover.iloc[1:].mean()
    # a churning ranking: the band never adds turnover, and names leaving the target are always closed
    S = np.where(u.to_numpy(), rng.normal(size=u.shape), np.nan)
    free = qp.simulate(S, mkt, qp.PortfolioConfig(**kw), start=w.dates[150])
    band = qp.simulate(S, mkt, qp.PortfolioConfig(**kw, band=0.02), start=w.dates[150])
    assert band.turnover.mean() <= free.turnover.mean()
    assert band.n_positions.max() <= 10
    huge = qp.simulate(S, mkt, qp.PortfolioConfig(**kw, band=10.0), start=w.dates[150])
    assert huge.exposure.sum(axis=1).max() == 0     # a band wider than any weight never opens a position


def test_anchored_schedule_and_delay():
    panel, fund = _wide(seed=5)
    w = build_wide(panel, fund)
    u = universe(w, 90, 5e6)
    mkt = qp.prepare_market(w, u, fill="next_open")
    rng = np.random.default_rng(0)
    S = np.where(u.to_numpy(), rng.normal(size=u.shape), np.nan)
    cfg = qp.PortfolioConfig(k=5, side="long", rebalance=7, execution="next_open", anchor="2021-01-03")
    res = qp.simulate(S, mkt, cfg, start=w.dates[100])
    traded = res.orders[res.orders > 0].index - pd.Timedelta(days=1)    # returns are labelled by the next day
    assert len(traded) > 5 and all((d - pd.Timestamp("2021-01-03")).days % 7 == 0 for d in traded)
    lag = qp.simulate(S, mkt, qp.PortfolioConfig(**dict(cfg.__dict__, lag=1)), start=w.dates[100])
    assert not np.allclose(res.returns.to_numpy(), lag.returns.to_numpy())


def test_block_bootstrap_sharpe_diff():
    rng = np.random.default_rng(0)
    ix = pd.date_range("2021-01-01", periods=1500)
    a = pd.Series(rng.normal(0.003, 0.01, 1500), ix)
    b = pd.Series(rng.normal(0.0, 0.01, 1500), ix)
    assert rt.block_bootstrap_sharpe_diff(a, b, n=300)["p_one_sided"] < 0.05
    assert rt.block_bootstrap_sharpe_diff(b, a, n=300)["p_one_sided"] > 0.5


def test_live_ranking_promoted_book_uses_schedule_band_and_execution(tmp_path, monkeypatch):
    from app.quant.lab import live

    panel, fund = _wide(seed=6, n_sym=40, n_days=500)
    monkeypatch.setattr(qd, "load_panel", lambda *a, **k: panel)
    monkeypatch.setattr(qd, "load_funding_daily", lambda *a, **k: fund)
    monkeypatch.setattr(live, "ROOT", tmp_path)
    coef = {f: 0.0 for f in FACTORS} | {"mom_7": 1.0}
    last = pd.Timestamp(panel["date"].max())
    for anchor_shift, today in ((0, True), (3, False)):
        cfg = qp.PortfolioConfig(k=3, side="long_short", rebalance=7, execution="next_open", order_type="maker",
                                 band=0.02, turnover_limit=None, anchor=str((last - pd.Timedelta(days=anchor_shift)).date()))
        (tmp_path / "model.json").write_text(json.dumps({
            "model": "ridge", "version": "exec-1", "horizon_days": 3, "trained_through": "2022-01-01",
            "factor_list": FACTORS, "coef": coef, "intercept": 0.0, "portfolio": cfg.to_dict()}))
        r = live.live_ranking()
        p = r["portfolio"]
        assert r["promoted"] is True
        assert p["rebalance_days"] == 7 and p["no_trade_band"] == 0.02 and p["execution"] == "next_open"
        assert p["order_type"] == "maker" and p["rebalance_today"] is today
        assert p["positions"] <= 6 and p["gross_long"] > 0
        if today:
            assert p["next_rebalance"] == str(last.date())
            book = {x["symbol"]: x["weight"] * (1 if x["side"] != "short" else -1) for x in r["rows"]}
            # every order is either larger than the band or closes a position completely
            assert p["orders"] and all(abs(o["delta_weight"]) > 0.02 or abs(book[o["symbol"]]) < 1e-9
                                       for o in p["orders"])
        else:
            assert p["orders"] == [] and p["next_rebalance"] == str((last + pd.Timedelta(days=4)).date())
        wsum = sum(x["weight"] for x in r["rows"] if x["side"] == "long")
        assert wsum == pytest.approx(p["gross_long"], abs=1e-3)
