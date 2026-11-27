import numpy as np
import pandas as pd

from app.ml import patterns as P
from app.quant.lab.factors import Wide


def _wide(paths: dict[str, np.ndarray]):
    n = len(next(iter(paths.values())))
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.DataFrame(paths, index=idx)
    qv = pd.DataFrame(1e7, index=idx, columns=close.columns)
    return Wide(close=close, high=close * 1.01, low=close * 0.99, quote_volume=qv, taker_buy_quote=qv / 2,
                funding=pd.DataFrame(np.nan, index=idx, columns=close.columns))


def test_shape_is_the_same_for_the_same_chart_at_any_price():
    rng = np.random.default_rng(1)
    walk = np.exp(np.cumsum(rng.normal(0, 0.03, 80)))
    w = _wide({"AAAUSDT": 100 * walk, "BBBUSDT": 0.002 * walk})   # same shape, prices 50,000x apart
    U = pd.DataFrame(True, index=w.close.index, columns=w.close.columns)
    U.iloc[:40] = False
    V, valid, _ = P.shapes(w, U)
    assert valid.all() and V.shape[1] == P.L + P.LV + 1
    a, b = V[0::2], V[1::2]                                          # rows alternate AAA, BBB per day
    assert np.allclose(a, b, atol=1e-4)


def test_pattern_names_read_the_shape():
    up = np.linspace(-1.5, 1.5, P.L)
    assert P.name_pattern(up, 3.0, 0.0).startswith("Uptrend")
    v = np.concatenate([np.linspace(1, -1.5, P.L // 2), np.linspace(-1.5, 1, P.L - P.L // 2)])
    assert P.name_pattern(v, 0.2, 0.6).startswith("V-shaped recovery") and "volume surging" in P.name_pattern(v, 0.2, 0.6)


def test_out_of_sample_features_only_use_earlier_years(monkeypatch):
    seen = []

    def fake_fit(Vtr, Ytr, seed=0):
        seen.append(len(Vtr))
        raise RuntimeError("stop after recording the training size")

    monkeypatch.setattr(P, "_fit", fake_fit)
    dates = pd.DatetimeIndex(pd.date_range("2019-01-01", "2023-12-31", freq="D").repeat(30))
    n = len(dates)
    V = np.zeros((n, 5), dtype=np.float32)
    Y = {k: np.zeros(n) for k in P.OUTCOMES}
    try:
        P.oos_features(V, np.ones(n, bool), Y, dates, years=(2022,), purge=15)
    except RuntimeError:
        pass
    # the first fold (2021) may only learn from charts dated before 2020-12-17
    assert seen and seen[0] == int((dates < pd.Timestamp("2021-01-01") - pd.Timedelta(days=15)).sum())
