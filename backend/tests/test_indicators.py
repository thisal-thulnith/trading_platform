import numpy as np
import pandas as pd
import pytest
import ta

from app.analysis.indicators import REGISTRY, compute
from app.analysis.indicators.core import ema, rma, sma, wma
from app.analysis.indicators.volume import volume_profile
from tests.conftest import make_ohlcv


@pytest.mark.parametrize("ind_id", sorted(REGISTRY))
def test_no_lookahead(ind_id):
    """Value at bar i must not change when future bars are appended."""
    df = make_ohlcv(400)
    full = compute(ind_id, df)
    cut = 300
    part = compute(ind_id, df.iloc[:cut].reset_index(drop=True))
    for k in full:
        a, b = full[k][:cut], part[k]
        both = ~np.isnan(a) & ~np.isnan(b)
        assert np.array_equal(np.isnan(a), np.isnan(b)), f"{ind_id}.{k} warm-up differs"
        np.testing.assert_allclose(a[both], b[both], rtol=1e-9, atol=1e-9, err_msg=f"{ind_id}.{k}")


@pytest.mark.parametrize("ind_id", sorted(REGISTRY))
def test_outputs_shape_and_finite_tail(ind_id):
    df = make_ohlcv(400)
    out = compute(ind_id, df)
    meta = REGISTRY[ind_id].meta()
    assert set(out) == {o["name"] for o in meta["outputs"]}
    for k, v in out.items():
        assert len(v) == len(df)
        if ind_id != "avwap":
            assert np.isfinite(v[-1]), f"{ind_id}.{k} last value not finite"


def test_basic_ma_values():
    x = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert sma(x, 3).tolist()[2:] == [2, 3, 4]
    assert wma(x, 3).iloc[-1] == pytest.approx((3 * 1 + 4 * 2 + 5 * 3) / 6)
    e = ema(x, 3)
    assert np.isnan(e.iloc[1]) and e.iloc[2] == 2.0 and e.iloc[3] == pytest.approx(0.5 * 4 + 0.5 * 2)
    r = rma(x, 2)
    assert r.iloc[1] == 1.5 and r.iloc[2] == pytest.approx(0.5 * 3 + 0.5 * 1.5)


def _tail_close(a, b, n=150, rtol=1e-3, atol=1e-6):
    np.testing.assert_allclose(np.asarray(a)[-n:], np.asarray(b)[-n:], rtol=rtol, atol=atol)


def test_against_ta_library(ohlcv):
    df = ohlcv
    c, h, low, v = df["close"], df["high"], df["low"], df["volume"]
    _tail_close(compute("rsi", df)["rsi"], ta.momentum.RSIIndicator(c, 14).rsi())
    m = compute("macd", df)
    _tail_close(m["macd"], ta.trend.MACD(c).macd())
    bb = compute("bbands", df)
    _tail_close(bb["upper"], ta.volatility.BollingerBands(c, 20, 2).bollinger_hband())
    _tail_close(compute("atr", df)["atr"], ta.volatility.AverageTrueRange(h, low, c, 14).average_true_range())
    _tail_close(compute("cci", df)["cci"], ta.trend.CCIIndicator(h, low, c, 20).cci())
    _tail_close(compute("willr", df)["willr"], ta.momentum.WilliamsRIndicator(h, low, c, 14).williams_r())
    # OBV level depends on the seed convention; increments must match.
    _tail_close(np.diff(compute("obv", df)["obv"]),
                np.diff(ta.volume.OnBalanceVolumeIndicator(c, v).on_balance_volume()))
    _tail_close(compute("cmf", df)["cmf"], ta.volume.ChaikinMoneyFlowIndicator(h, low, c, v, 20).chaikin_money_flow())
    _tail_close(compute("mfi", df)["mfi"], ta.volume.MFIIndicator(h, low, c, v, 14).money_flow_index(), rtol=1e-2)
    st = compute("stoch", df)
    _tail_close(st["k"], ta.momentum.StochasticOscillator(h, low, c, 14, 3).stoch().rolling(3).mean())
    adx = compute("adx", df)
    _tail_close(adx["adx"], ta.trend.ADXIndicator(h, low, c, 14).adx(), rtol=2e-2)
    _tail_close(compute("roc", df)["roc"], ta.momentum.ROCIndicator(c, 10).roc())
    _tail_close(compute("ao", df)["ao"], ta.momentum.AwesomeOscillatorIndicator(h, low).awesome_oscillator())
    _tail_close(compute("uo", df)["uo"], ta.momentum.UltimateOscillator(h, low, c).ultimate_oscillator())
    dc = compute("donchian", df)
    _tail_close(dc["upper"], ta.volatility.DonchianChannel(h, low, c, 20).donchian_channel_hband())


def test_supertrend_flips_with_trend():
    up = make_ohlcv(300, drift=0.004)
    d = compute("supertrend", up)["direction"]
    assert np.nanmean(d[-100:]) > 0.5
    dn = make_ohlcv(300, drift=-0.004)
    assert np.nanmean(compute("supertrend", dn)["direction"][-100:]) < -0.5


def test_param_validation(ohlcv):
    with pytest.raises(ValueError):
        compute("rsi", ohlcv, length=1)


def test_volume_profile_value_area(ohlcv):
    vp = volume_profile(ohlcv.tail(200), bins=30)
    vols = np.array([b["volume"] for b in vp["bins"]])
    assert vp["val"] <= vp["poc"] <= vp["vah"]
    in_va = sum(b["volume"] for b in vp["bins"] if b["in_value_area"])
    assert in_va >= 0.70 * vols.sum() - 1e-6
    assert vols.sum() == pytest.approx(ohlcv.tail(200)["volume"].sum(), rel=1e-6)


def test_cvd_uses_taker_volume(ohlcv):
    out = compute("cvd", ohlcv, anchor="none")
    np.testing.assert_allclose(out["delta"], 2 * ohlcv["taker_buy_base"] - ohlcv["volume"])
