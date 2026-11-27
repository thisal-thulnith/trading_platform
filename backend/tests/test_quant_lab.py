"""Quant lab tests on synthetic data only (no network)."""
import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from app.quant.lab import data as qd
from app.quant.lab import model as qm
from app.quant.lab import portfolio as qp
from app.quant.lab import validate as qv
from app.quant.lab.factors import (
    FACTORS,
    build_wide,
    compute_factors,
    cost_rate,
    forward_returns,
    newey_west_t,
    rank_ic,
    universe,
)


def synth_panel(n_sym: int = 30, n_days: int = 400, seed: int = 0, delist: dict | None = None,
                late: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-01", periods=n_days, freq="D")
    rows, frows = [], []
    for i in range(n_sym):
        sym = "BTCUSDT" if i == 0 else f"C{i:02d}USDT"
        r = rng.normal(0, 0.03, n_days)
        close = 10 * np.exp(np.cumsum(r))
        qv_ = rng.lognormal(np.log(2e7 if i % 5 else 2e6), 0.3, n_days)
        lo = (late or {}).get(sym, 0)
        hi = (delist or {}).get(sym, n_days)
        for t in range(lo, hi):
            o = close[t - 1] if t else close[t]
            rows.append((dates[t].strftime("%Y-%m-%d"), sym, o, max(o, close[t]) * 1.01, min(o, close[t]) * 0.99,
                         close[t], qv_[t] / close[t], qv_[t], 1000, qv_[t] / close[t] * 0.5, qv_[t] * rng.uniform(.4, .6)))
            if i % 2 == 0:
                frows.append((dates[t].strftime("%Y-%m-%d"), sym, rng.normal(0.0003, 0.0002)))
    panel = pd.DataFrame(rows, columns=qd.COLUMNS)
    fund = pd.DataFrame(frows, columns=["date", "symbol", "funding"])
    return panel, fund


def _eq(a: pd.Series, b: pd.Series) -> bool:
    return bool(np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True, rtol=1e-9, atol=1e-12))


# --- data -------------------------------------------------------------------------------------------------------
def test_exclusions():
    assert qd.exclusion_reason("BTCUSDT") is None
    assert qd.exclusion_reason("JUPUSDT") is None          # ends in "UP" but is not a leveraged token
    assert qd.exclusion_reason("BTCUPUSDT") == "leveraged_token"
    assert qd.exclusion_reason("ETHBEARUSDT") == "leveraged_token"
    assert qd.exclusion_reason("USDCUSDT") == "stablecoin_or_fiat"
    assert qd.exclusion_reason("WBTCUSDT") == "wrapped_or_pegged"
    assert qd.exclusion_reason("BTCBUSD") == "not_usdt_quote"
    assert qd.perp_base("1000PEPEUSDT") == "PEPE" and qd.perp_base("BTCUSDT") == "BTC"


def test_kline_zip_parses_ms_and_us_timestamps():
    import io
    import zipfile

    def z(line: str) -> bytes:
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as f:
            f.writestr("x.csv", line)
        return b.getvalue()

    ms = "1583020800000,1,2,0.5,1.5,10,1583107199999,15,7,4,6,0\n"
    us = "1740787200000000,1,2,0.5,1.5,10,1740873599999999,15,7,4,6,0\n"
    assert qd.parse_kline_zip(z(ms))["date"].iloc[0] == "2020-03-01"
    d = qd.parse_kline_zip(z(us))
    assert d["date"].iloc[0] == "2025-03-01" and d["taker_buy_quote"].iloc[0] == 6


# --- point in time ----------------------------------------------------------------------------------------------
def test_factors_are_point_in_time():
    panel, fund = synth_panel()
    w = build_wide(panel, fund)
    full = compute_factors(w)
    for cut in ("2021-06-15", "2021-11-30"):
        trunc = compute_factors(build_wide(panel[panel["date"] <= cut], fund[fund["date"] <= cut]))
        for f in FACTORS:
            assert _eq(full[f].loc[cut], trunc[f].loc[cut].reindex(full[f].columns)), f


def test_future_shock_does_not_move_past_factors():
    panel, fund = synth_panel(seed=3)
    w = build_wide(panel, fund)
    base = compute_factors(w)
    shocked = panel.copy()
    m = shocked["date"] > "2021-08-01"
    for c in ("open", "high", "low", "close"):
        shocked.loc[m, c] *= 5.0
    shocked.loc[m, "quote_volume"] *= 0.01
    s = compute_factors(build_wide(shocked, fund))
    for f in FACTORS:
        assert _eq(base[f].loc[:"2021-08-01"].stack(), s[f].loc[:"2021-08-01"].stack()), f


# --- universe / returns ------------------------------------------------------------------------------------------
def test_universe_filter_history_volume_and_delisting():
    panel, fund = synth_panel(delist={"C01USDT": 200}, late={"C02USDT": 300})
    w = build_wide(panel, fund)
    u = universe(w, min_history=90, min_adv=5e6)
    d = w.dates
    assert not u["C05USDT"].any()                       # ~$2M ADV: never tradeable
    assert not u.loc[d[:89]].any().any()                # nobody has 90 days of history yet
    assert u.loc[d[100], "C01USDT"] and not u.loc[d[250], "C01USDT"]   # delisted after day 200
    assert not u.loc[d[350], "C02USDT"] and u.loc[d[395], "C02USDT"]   # listed on day 300


def test_forward_return_exits_at_last_close_on_delisting():
    panel, fund = synth_panel(delist={"C01USDT": 200})
    w = build_wide(panel, fund)
    f7 = forward_returns(w, 7)
    last = w.close["C01USDT"].dropna()
    t = w.dates[196]
    assert f7.loc[t, "C01USDT"] == pytest.approx(last.iloc[-1] / w.close.loc[t, "C01USDT"] - 1)


# --- IC ---------------------------------------------------------------------------------------------------------
def test_rank_ic_matches_spearman_and_extremes():
    panel, fund = synth_panel()
    w = build_wide(panel, fund)
    u = universe(w, 90, 0)
    fwd = forward_returns(w, 3)
    assert rank_ic(fwd, fwd, u).dropna().round(10).eq(1).all()
    assert rank_ic(-fwd, fwd, u).dropna().round(10).eq(-1).all()
    f = compute_factors(w)["mom_28"]
    ic = rank_ic(f, fwd, u)
    t = ic.dropna().index[10]
    m = u.loc[t] & f.loc[t].notna() & fwd.loc[t].notna()
    assert ic[t] == pytest.approx(spearmanr(f.loc[t][m], fwd.loc[t][m]).statistic)
    assert abs(ic.mean()) < 0.05                          # random walk: no predictability


def test_newey_west_t_close_to_iid_t():
    x = np.random.default_rng(1).normal(0.1, 1, 5000)
    t_iid = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert newey_west_t(x, 5) == pytest.approx(t_iid, rel=0.1)


# --- portfolio --------------------------------------------------------------------------------------------------
def test_cap_normalize():
    w = qp._cap_normalize(np.array([10.0, 1, 1, 1]), np.full(4, 0.3), 1.0)
    assert w.max() <= 0.3 + 1e-12 and w.sum() == pytest.approx(1.0)
    assert w[0] == pytest.approx(0.3) and np.allclose(w[1:], 0.7 / 3)
    w2 = qp._cap_normalize(np.array([1.0, 1.0]), np.full(2, 0.3), 1.0)
    assert w2.sum() == pytest.approx(0.6)                 # cannot reach 1 under caps -> cash


def test_target_weights_topk_invvol_voltarget_and_shorts():
    panel, fund = synth_panel(n_sym=40)
    w = build_wide(panel, fund)
    u = universe(w, 90, 5e6)
    mkt = qp.prepare_market(w, u)
    t = 300
    score = np.where(u.iloc[t], np.arange(len(w.symbols), dtype=float), np.nan)
    cfg = qp.PortfolioConfig(k=5, vol_target=None, coin_cap=1.0)
    wt = qp.target_weights(score, t, mkt, cfg)
    longs = np.flatnonzero(wt > 0)
    elig = np.flatnonzero(u.iloc[t].to_numpy())
    assert set(longs) == set(elig[np.argsort(-score[elig])[:5]])
    assert wt.sum() == pytest.approx(1.0)
    inv = 1 / mkt.vol[t, longs]
    assert np.allclose(wt[longs] / wt[longs].sum(), inv / inv.sum())
    # vol target: scaled-down book has the target ex-ante vol
    cfg_v = qp.PortfolioConfig(k=5, vol_target=0.10, coin_cap=1.0)
    wv = qp.target_weights(score, t, mkt, cfg_v)
    nz = np.flatnonzero(wv)
    cov = np.cov(np.nan_to_num(mkt.ret[t - 59: t + 1, nz]), rowvar=False) * 365
    assert np.sqrt(wv[nz] @ cov @ wv[nz]) == pytest.approx(0.10, rel=1e-6)
    # coin cap
    wc = qp.target_weights(score, t, mkt, qp.PortfolioConfig(k=5, vol_target=None, coin_cap=0.15))
    assert wc.max() <= 0.15 + 1e-12
    # long-short: shorts only where a perp exists (even-indexed synthetic symbols have funding)
    ws = qp.target_weights(score, t, mkt, qp.PortfolioConfig(k=5, side="long_short", vol_target=None, vol_target_ls=0.0,
                                                             coin_cap=1.0))
    shorts = np.flatnonzero(ws < 0)
    assert len(shorts) == 5 and mkt.has_perp[t, shorts].all()
    assert -ws[shorts].sum() == pytest.approx(1.0)


def test_simulate_turnover_limit_costs_and_funding():
    panel, fund = synth_panel(n_sym=40, seed=5)
    w = build_wide(panel, fund)
    u = universe(w, 90, 5e6)
    mkt = qp.prepare_market(w, u)
    rng = np.random.default_rng(0)
    S = np.where(u.to_numpy(), rng.normal(size=u.shape), np.nan)
    cfg = qp.PortfolioConfig(k=5, rebalance=1, turnover_limit=0.3, vol_target=None, dust=0.0)
    res = qp.simulate(S, mkt, cfg, start=w.dates[100])
    assert res.turnover.iloc[1:].max() <= 0.3 + 1e-9
    assert res.n_positions.max() > 5                      # a pure turnover limit leaves a tail of old positions
    dusted = qp.simulate(S, mkt, qp.PortfolioConfig(k=5, rebalance=1, turnover_limit=0.3, vol_target=None,
                                                    dust=0.05), start=w.dates[100])
    assert dusted.n_positions.mean() < res.n_positions.mean()
    free = qp.simulate(S, mkt, qp.PortfolioConfig(k=5, rebalance=1, turnover_limit=None, vol_target=None,
                                                  cost_mult=0.0), start=w.dates[100])
    assert free.costs.sum() == 0 and res.costs.sum() > 0
    cr = cost_rate(w)
    assert cr.min().min() >= 0.0005 + 0.0002 - 1e-12
    ls = qp.simulate(S, mkt, qp.PortfolioConfig(k=5, side="long_short", rebalance=7, vol_target=None),
                     start=w.dates[100])
    assert ls.funding.sum() > 0      # synthetic funding is positive on average -> shorts earn it
    assert (ls.exposure["short"] > 0).any()


# --- validation statistics -----------------------------------------------------------------------------------------
def test_pbo_and_dsr_on_pure_noise():
    rng = np.random.default_rng(42)
    M = rng.normal(0, 0.01, (1500, 60))
    p = qv.pbo_cscv(M, 16)
    assert p["pbo"] > 0.3
    srs = M.mean(axis=0) / M.std(axis=0, ddof=1)
    best = int(np.argmax(srs))
    d = qv.deflated_sharpe(pd.Series(M[:, best]), 60, float(srs.var(ddof=1)))
    assert d["p_value"] > 0.05                      # the best of 60 noise strategies is not significant
    assert d["psr_vs_0"] > d["dsr"]                 # deflation lowers the confidence


def test_pbo_and_dsr_with_a_real_edge():
    rng = np.random.default_rng(7)
    M = rng.normal(0, 0.01, (1500, 30))
    M[:, 3] += 0.003                                # one genuinely skilled strategy (daily SR ~0.3)
    p = qv.pbo_cscv(M, 16)
    assert p["pbo"] < 0.1
    srs = M.mean(axis=0) / M.std(axis=0, ddof=1)
    d = qv.deflated_sharpe(pd.Series(M[:, 3]), 30, float(srs.var(ddof=1)))
    assert d["p_value"] < 0.05


def test_expected_max_sharpe_grows_with_trials():
    assert qv.expected_max_sr(0.001, 1) == 0
    assert qv.expected_max_sr(0.001, 10) < qv.expected_max_sr(0.001, 100) < qv.expected_max_sr(0.001, 1000)


def test_promotion_gate():
    good = {"sharpe": 1.5, "ew_sharpe": 0.8, "dsr_p": 0.01, "pbo": 0.1, "psr_costs_x2": 0.99}
    assert qv.promotion_gate(good)[0]
    for k, bad in (("dsr_p", 0.2), ("pbo", 0.5), ("sharpe", 0.5), ("psr_costs_x2", 0.6)):
        ok, checks = qv.promotion_gate({**good, k: bad})
        assert not ok and not all(checks.values())


# --- model ------------------------------------------------------------------------------------------------------
def test_walk_forward_purges_training_labels():
    panel, fund = synth_panel(n_sym=30, n_days=500)
    w = build_wide(panel, fund)
    u = universe(w, 90, 0)
    facs = compute_factors(w)
    ds = qm.build_dataset(w, facs, u, horizons=(7,))
    rd = ds.row_dates
    tests = [(pd.Timestamp("2021-10-01"), pd.Timestamp("2021-10-31"))]
    ok = qm.purge_mask(rd, tests, 7, 14)
    assert not ok[(rd >= "2021-09-24") & (rd <= "2021-11-14")].any()
    assert ok[rd < "2021-09-24"].all() and ok[rd > "2021-11-14"].all()
    pred, folds = qm.walk_forward(ds, "ridge", 7, start="2021-10-01")
    assert np.isnan(pred[rd < "2021-10-01"]).all() and np.isfinite(pred[rd >= "2021-10-01"]).all()
    assert all(f["n_train_rows"] > 0 for f in folds)
    # the composite on a pure random walk should mostly find nothing to trade
    ic7 = pd.DataFrame({f: rank_ic(facs[f], forward_returns(w, 7), u) for f in FACTORS})
    paths, groups = qm.cpcv(ds, "composite", 7, ic7, n_groups=5, k=2)
    assert len(paths) == 4 and len(groups) == 5


# --- live outputs -----------------------------------------------------------------------------------------------
def test_live_ranking_promoted_and_unpromoted(tmp_path, monkeypatch):
    import json

    from app.quant.lab import live

    panel, fund = synth_panel(n_sym=40, n_days=500)
    monkeypatch.setattr(qd, "load_panel", lambda *a, **k: panel)
    monkeypatch.setattr(qd, "load_funding_daily", lambda *a, **k: fund)
    monkeypatch.setattr(live, "ROOT", tmp_path)
    (tmp_path / "factor_report.json").write_text(json.dumps({"screen_passed": {"rvol_30": -1, "mom_28": 1}}))
    r = live.live_ranking()
    assert r["promoted"] is False and "no model is promoted" in r["note"]
    assert r["universe_size"] == len(r["rows"]) > 10
    assert [x["rank"] for x in r["rows"]] == list(range(1, len(r["rows"]) + 1))
    scores = [x["score"] for x in r["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert 0 < r["portfolio"]["gross_long"] <= 1.0 + 1e-9 and r["portfolio"]["positions"] <= 10
    assert set(r["rows"][0]["factors"]) == set(FACTORS)
    coef = {f: 0.0 for f in FACTORS} | {"mom_7": 1.0}
    (tmp_path / "model.json").write_text(json.dumps({
        "model": "ridge", "version": "t", "horizon_days": 7, "trained_through": "2022-01-01", "factor_list": FACTORS,
        "coef": coef, "intercept": 0.0, "portfolio": qp.PortfolioConfig(k=3).to_dict()}))
    p = live.live_ranking()
    assert p["promoted"] is True and p["portfolio"]["positions"] <= 3
    top = max(p["rows"], key=lambda x: x["factors"]["mom_7"] if x["factors"]["mom_7"] is not None else -9)
    assert top["rank"] == 1                               # ridge on mom_7 rank only -> highest 7-day return first
    rep = live.latest_report()
    assert rep["available"] and rep["promoted_model"]["model"] == "ridge"
