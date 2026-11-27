import numpy as np
import pandas as pd

from app.ml import concepts as cc
from app.quant.lab.factors import Wide


def _wide(n=200, seed=0, cols=("BTCUSDT", "AAAUSDT", "BBBUSDT")):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.03, (n, len(cols))), axis=0)), index=idx, columns=cols)
    op = close.shift(1).fillna(close.iloc[0])
    hi = np.maximum(op, close) * (1 + rng.uniform(0, 0.02, close.shape))
    lo = np.minimum(op, close) * (1 - rng.uniform(0, 0.02, close.shape))
    qv = pd.DataFrame(rng.uniform(1e6, 5e6, close.shape), index=idx, columns=cols)
    return Wide(close=close, high=hi, quote_volume=qv, taker_buy_quote=qv / 2, funding=close * np.nan, open=op, low=lo)


def _atr(w):
    return (w.high - w.low).rolling(14, min_periods=10).mean()


def test_no_concept_looks_ahead():
    w = _wide()
    base = cc.concepts(w, _atr(w))
    T = 150
    w2 = _wide()
    rng = np.random.default_rng(9)
    for fr in (w2.close, w2.high, w2.low, w2.open, w2.quote_volume):
        fr.iloc[T + 1:] = fr.iloc[T + 1:] * rng.uniform(0.5, 1.5, fr.iloc[T + 1:].shape)
    w2.high.iloc[T + 1:] = np.maximum(w2.high.iloc[T + 1:], np.maximum(w2.close.iloc[T + 1:], w2.open.iloc[T + 1:]))
    w2.low.iloc[T + 1:] = np.minimum(w2.low.iloc[T + 1:], np.minimum(w2.close.iloc[T + 1:], w2.open.iloc[T + 1:]))
    moved = cc.concepts(w2, _atr(w2))
    assert set(base) == set(cc.CONCEPTS)
    for k in cc.CONCEPTS:
        a, b = base[k].iloc[: T + 1].to_numpy(), moved[k].iloc[: T + 1].to_numpy()
        assert np.allclose(np.nan_to_num(a, nan=-999), np.nan_to_num(b, nan=-999)), k


def _one(closes, highs=None, lows=None):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    c = pd.DataFrame({"X": closes}, index=idx, dtype=float)
    h = pd.DataFrame({"X": highs if highs is not None else [x + 0.5 for x in closes]}, index=idx, dtype=float)
    lo = pd.DataFrame({"X": lows if lows is not None else [x - 0.5 for x in closes]}, index=idx, dtype=float)
    w = Wide(close=c, high=h, quote_volume=c * 0 + 1e6, taker_buy_quote=c * 0, funding=c * np.nan, open=c.shift(1), low=lo)
    return cc.concepts(w, pd.DataFrame({"X": [1.0] * len(closes)}, index=idx))


def test_break_of_structure_and_swing_levels():
    # a swing high at 110 on day 5, a pullback, then a close above it on day 12 = bullish BOS
    closes = [100, 102, 104, 106, 108, 110, 107, 105, 104, 105, 107, 109, 112, 113]
    f = _one(closes)
    assert np.isnan(f["ms_bos_state"]["X"].iloc[11])
    assert f["ms_bos_state"]["X"].iloc[12] == 1 and f["ms_days_since_bos"]["X"].iloc[13] == 1
    assert abs(f["ms_dist_sh_atr"]["X"].iloc[10] - (110.5 - 107)) < 1e-5     # swing high 110.5 confirmed on day 8


def test_fair_value_gap_and_sweep():
    # day 3 low (105) is above day 1 high (101.5): a bullish gap, open while price stays above 101.5
    closes = [100] * 25 + [101, 103, 106, 107]
    highs = [x + 0.5 for x in closes]
    lows = [x - 0.5 for x in closes]
    lows[27] = 105.0
    f = _one(closes, highs, lows)
    assert f["fvg_bull_open"]["X"].iloc[27] >= 1 and f["fvg_bull_open"]["X"].iloc[28] >= 1
    # a sweep: the low pokes under the 20-day low and the close is back above it
    closes2 = [100] * 30
    lows2 = [99.5] * 30
    lows2[29] = 98.0
    f2 = _one(closes2, None, lows2)
    assert f2["liq_sweep_low_5"]["X"].iloc[29] == 1 and f2["wy_spring_5"]["X"].iloc[29] == 1
