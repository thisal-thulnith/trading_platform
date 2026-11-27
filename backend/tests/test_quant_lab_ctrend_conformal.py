"""CTREND and conformal abstention tests (synthetic data only, no network)."""
import numpy as np
import pandas as pd
import pytest

from app.quant.lab import conformal as qc
from app.quant.lab import ctrend as ct
from app.quant.lab.factors import build_wide, universe
from tests.test_quant_lab import synth_panel


def test_ctrend_indicators_are_point_in_time():
    panel, fund = synth_panel(n_sym=12, n_days=400, seed=1)
    w = build_wide(panel, fund)
    full = ct.indicators(w)
    cut = w.dates[250]
    part = ct.indicators(w.truncate(cut))
    assert set(full) == set(ct.INDICATORS) and len(ct.INDICATORS) == 20
    for k in ct.INDICATORS:
        a, b = full[k].loc[:cut], part[k]
        assert np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True), k


def test_ctrend_walk_forward_uses_no_future_data():
    panel, fund = synth_panel(n_sym=25, n_days=520, seed=2)
    w = build_wide(panel, fund)
    u = universe(w, 60, 0)
    kw = dict(start="2021-07-01", min_rows=500)
    base, fits = ct.walk_forward(w, u, **kw)
    assert fits and all(f["n_train"] >= 500 for f in fits)
    assert base.loc[: "2021-06-30"].isna().all().all() and base.loc["2021-07-01":].notna().any().any()
    # shock every price after X: CTREND before X must not move (neither indicators nor the fitted nets)
    X = pd.Timestamp("2022-02-15")
    p2 = panel.copy()
    after = pd.to_datetime(p2["date"]) >= X
    rng = np.random.default_rng(0)
    shock = np.exp(rng.normal(0, 0.3, after.sum()))
    for c in ("open", "high", "low", "close"):
        p2.loc[after, c] = p2.loc[after, c] * shock
    p2.loc[after, "quote_volume"] *= rng.uniform(0.1, 10, after.sum())
    w2 = build_wide(p2, fund)
    moved, _ = ct.walk_forward(w2, universe(w2, 60, 0), **kw)
    before = base.index < X
    assert np.allclose(base[before].to_numpy(float), moved[before].to_numpy(float), equal_nan=True)
    assert not np.allclose(base[~before].to_numpy(float), moved[~before].to_numpy(float), equal_nan=True)


def test_ctrend_learns_a_planted_trend_signal():
    # returns follow the 20-day MA ratio: the elastic net must find a positive weight on it out of sample
    rng = np.random.default_rng(5)
    n_sym, n_days = 40, 600
    dates = pd.date_range("2021-01-01", periods=n_days)
    px = np.full((n_days, n_sym), 10.0)
    for t in range(1, n_days):
        lo = max(0, t - 20)
        ma = px[lo:t].mean(axis=0)
        drift = 0.02 * np.tanh((px[t - 1] / ma - 1) * 10)
        px[t] = px[t - 1] * np.exp(drift + rng.normal(0, 0.03, n_sym))
    rows = []
    for t in range(n_days):
        for i in range(n_sym):
            c = px[t, i]
            rows.append((dates[t].strftime("%Y-%m-%d"), f"C{i:02d}USDT", c, c * 1.01, c * 0.99, c, 1e6 / c, 1e7, 100,
                         5e5 / c, 5e6))
    from app.quant.lab import data as qd
    panel = pd.DataFrame(rows, columns=qd.COLUMNS)
    w = build_wide(panel, pd.DataFrame(columns=["date", "symbol", "funding"]))
    u = universe(w, 30, 0)
    cf, fits = ct.walk_forward(w, u, start="2021-06-01", min_rows=1000)
    fwd = w.close.shift(-7) / w.close - 1
    ic = cf.rank(axis=1).corrwith(fwd.where(cf.notna()).rank(axis=1), axis=1).dropna()
    assert ic.mean() > 0.1
    assert np.mean([f["coef"]["ma_20"] + f["coef"]["ma_10"] + f["coef"]["ma_50"] for f in fits]) > 0


def _synthetic_ranks(T=500, N=60, rho=0.3, seed=0, hetero=True):
    rng = np.random.default_rng(seed)
    z = rng.normal(size=(T, N))
    scale = np.where(np.arange(N) % 2 == 0, 0.5, 1.5) if hetero else np.ones(N)
    y_raw = rho * z + rng.normal(size=(T, N)) * scale
    P = qc.centred_rank(z)
    Y = qc.centred_rank(y_raw)
    return P, Y


@pytest.mark.parametrize("alpha", [0.1, 0.5])
def test_conformal_coverage_on_synthetic_data(alpha):
    P, Y = _synthetic_ranks()
    iv = qc.intervals(P, Y, h=3, alphas=(alpha,))
    lo, hi = iv["lower"][alpha], iv["upper"][alpha]
    cov = qc.coverage(Y, lo, hi)
    assert cov["rows"] > 10000
    assert cov["coverage"] == pytest.approx(1 - alpha, abs=0.02)
    # the local scale is larger for the noisier half of the coins
    sig = iv["sigma"][200:]
    assert np.nanmean(sig[:, 1::2]) > np.nanmean(sig[:, ::2])


def test_conformal_is_point_in_time_and_abstains():
    P, Y = _synthetic_ranks(seed=1)
    iv = qc.intervals(P, Y, h=3, alphas=(0.5,))
    Y2 = Y.copy()
    Y2[300:] = np.random.default_rng(9).permutation(Y2[300:].ravel()).reshape(Y2[300:].shape)
    iv2 = qc.intervals(P, Y2, h=3, alphas=(0.5,))
    # intervals on rows <= 303 only use outcomes of rows <= 299 (dated <= t - h - 1)
    assert np.allclose(iv["lower"][0.5][:304], iv2["lower"][0.5][:304], equal_nan=True)
    assert not np.allclose(iv["lower"][0.5][320:], iv2["lower"][0.5][320:], equal_nan=True)
    lo, hi = iv["lower"][0.5], iv["upper"][0.5]
    long_ok, short_ok = qc.abstention_masks(lo, hi)
    assert not (long_ok & short_ok).any()
    assert (P[long_ok] > 0).all() and (P[short_ok] < 0).all()
    ok = np.isfinite(lo)
    assert 0 < long_ok[ok].mean() < 0.5             # abstains on most coins but not all
    assert qc.conformal_quantile(np.arange(1, 11, dtype=float), 0.1) == 10.0
    assert qc.conformal_quantile(np.arange(1, 10, dtype=float), 0.05) == np.inf
