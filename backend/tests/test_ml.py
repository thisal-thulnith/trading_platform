import numpy as np

from app.ml.cv import purged_walk_forward
from app.ml.features import build_features, triple_barrier
from app.ml.train import make_dataset, research
from tests.conftest import make_ohlcv


def test_features_are_causal():
    df = make_ohlcv(700, seed=2)
    full = build_features(df)
    part = build_features(df.iloc[:500].reset_index(drop=True))
    a, b = full.iloc[:500], part
    for c in b.columns:
        x, y = a[c].to_numpy(), b[c].to_numpy()
        m = ~np.isnan(x) & ~np.isnan(y)
        np.testing.assert_allclose(x[m], y[m], rtol=1e-8, atol=1e-10, err_msg=c)


def test_triple_barrier_labels_use_future_only_within_horizon():
    df = make_ohlcv(300, seed=3)
    lab = triple_barrier(df, horizon=10)
    resolved = lab.dropna(subset=["label"])
    assert ((resolved["t1"] - resolved.index) <= 10).all()
    assert ((resolved["t1"] - resolved.index) >= 1).all()
    assert lab["label"].iloc[-10:].isna().all()


def test_purged_walk_forward_no_overlap():
    times = np.arange(1000) * 10
    t1 = times + 50
    for tr, te in purged_walk_forward(times, t1, 4, embargo=5):
        assert t1[tr].max() < times[te].min()
        assert times[tr].max() < times[te].min()


def test_research_rejects_noise():
    frames = {"AAA": make_ohlcv(2500, seed=11), "BBB": make_ohlcv(2500, seed=12)}
    res = research(make_dataset(frames, 12, btc_key="AAA"), 12, cost=0.0014, n_splits=3)
    # a random walk has no predictable structure: the acceptance gate must reject it
    assert res["status"] == "rejected"
