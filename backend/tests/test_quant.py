import numpy as np
import pandas as pd

from app.quant import ledger
from app.quant.brain import regime, score_to_prob, trend_score, walk_forward
from app.quant.odds import wilson


def _panel(n_days=300, n_coins=40, seed=1, persist=0.0):
    """Synthetic daily closes; with persist > 0 each coin has a fixed drift, so trend ranks should predict."""
    rng = np.random.default_rng(seed)
    drift = np.linspace(-0.004, 0.004, n_coins) * persist
    rets = rng.normal(0, 0.03, (n_days, n_coins)) + drift
    idx = pd.date_range("2025-01-01", periods=n_days, freq="D")
    cols = [f"C{i}USDT" for i in range(n_coins - 1)] + ["BTCUSDT"]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=cols)
    qvol = pd.DataFrame(rng.uniform(1e6, 2e6, (n_days, n_coins)), index=idx, columns=cols)
    return close, qvol


def test_trend_score_ranks_persistent_winners_high_and_walk_forward_finds_it():
    close, qvol = _panel(persist=1.0)
    s = trend_score(close, qvol)
    last = s.iloc[-1].dropna()
    assert last.between(0, 1).all()
    assert last["C38USDT"] > last["C0USDT"]  # strongest drift beats weakest drift
    wf = walk_forward(close, s)
    assert wf["weeks"] > 20 and wf["long_short"]["mean_week_pct"] > 0
    assert wf["quintiles"][4]["up_rate"] > wf["quintiles"][0]["up_rate"]
    p, n = score_to_prob(0.95, wf["quintiles"])
    assert p == wf["quintiles"][4]["up_rate"] and n > 0


def test_walk_forward_on_noise_shows_no_edge():
    close, qvol = _panel(persist=0.0, seed=4)
    wf = walk_forward(close, trend_score(close, qvol))
    assert abs(wf["long_short"]["t_stat"]) < 3  # honest: random data must not look like an edge


def test_regime_reads_btc_trend_and_breadth():
    close, _ = _panel(persist=1.0)
    r = regime(close)
    assert r["btc_trend"] in ("bull", "bear", "mixed") and 0 <= r["breadth_pct"] <= 100 and r["plain"]


def test_ledger_scores_reward_skill_and_shrink_small_samples():
    good = [(0.7, 1)] * 40 + [(0.3, 0)] * 40
    bad = [(0.7, 0)] * 40 + [(0.3, 1)] * 40
    g, b = ledger._score(good, 0.5), ledger._score(bad, 0.5)
    assert g["skill"] > 0 > b["skill"] and g["weight"] > 1 > b["weight"]
    tiny = ledger._score([(0.9, 1)] * 3, 0.5)
    assert tiny["weight"] < g["weight"]  # three lucky calls can't outweigh eighty good ones


def test_combine_is_weighted_calibrated_average():
    fc = [{"source": "a", "p_up": 0.8}, {"source": "b", "p_up": 0.3}]
    eq = ledger.combine(fc, {})
    assert abs(eq["p_up"] - (0.5 + ((0.8 - 0.5) * 0.6 + (0.3 - 0.5) * 0.6) / 2)) < 1e-6
    trusted_b = ledger.combine(fc, {"b": {"weight": 3.0, "slope": 1.0}})
    assert trusted_b["p_up"] < eq["p_up"]
    assert ledger.verdict_to_p("bullish", 70) == 0.7 and ledger.verdict_to_p(-1, 60) == 0.4


def test_wilson_interval_is_wide_for_small_samples():
    lo_s, hi_s = wilson(3, 5)
    lo_b, hi_b = wilson(300, 500)
    assert hi_s - lo_s > hi_b - lo_b and lo_b < 0.6 < hi_b
