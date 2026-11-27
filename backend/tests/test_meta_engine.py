"""Meta-labeling on the unified engine: the walk-forward never trains on rows that overlap the year it predicts."""
import numpy as np
import pandas as pd

from app.ml import meta_engine as ml


def _fake(n_days=1300):
    d = pd.date_range("2021-01-01", periods=n_days, freq="D")
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"date": d, "real": rng.normal(0.1, 1.5, n_days), "year": d.year})
    for f in ml.FEATURES:
        df[f] = rng.normal(size=n_days)
    return df


def test_walk_forward_purges_and_starts_after_first_year(monkeypatch):
    seen = []

    class Spy:
        def fit(self, X, y):
            seen.append(len(X))
            return self

        def predict_proba(self, X):
            return np.tile([0.5, 0.5], (len(X), 1))

    monkeypatch.setattr(ml, "_models", lambda: {"spy": lambda: Spy()})
    out = ml.walk_forward(_fake())
    assert out.loc[out["year"] == 2021, "p_spy"].isna().all()          # the first year only trains
    assert out.loc[out["year"] == 2022, "p_spy"].notna().all()
    # the 2022 model saw 2021 minus the 15-day purge: Jan 1 2021 .. Dec 16 2021 = 350 days
    assert seen[0] == 350


def test_weights():
    df = pd.DataFrame({"p_m": [0.2, 0.5, 0.9]})
    assert ml._weights(df, {"kind": "all", "model": None}, 0.4).tolist() == [1, 1, 1]
    assert ml._weights(df, {"kind": "skip_below", "model": "m", "q": 0.3}, 0.4).tolist() == [0, 1, 1]
    assert np.allclose(ml._weights(df, {"kind": "size_by_p", "model": "m"}, 0.4), [0.5, 1.25, 2.0])
