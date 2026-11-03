import numpy as np

from app.analysis.indicators.core import (
    Output,
    Param,
    atr,
    ema,
    highest,
    indicator,
    lowest,
    sma,
    stdev,
    true_range,
)

P = Param

# Crypto trades 24/7: annualisation uses 365 days.
def _bars_per_year(df) -> float:
    if "open_time" in df and len(df) > 1:
        step = float(np.median(np.diff(df["open_time"].to_numpy()[-50:])))
        if step > 0:
            return 365 * 86_400_000 / step
    return 365.0


@indicator("atr", "Average True Range", "volatility", [P("length", 14, 1)], [Output("atr", "separate")],
           family="range_volatility")
def _atr(df, length):
    return {"atr": atr(df, length)}


@indicator("bbands", "Bollinger Bands", "volatility", [P("length", 20, 2), P("mult", 2.0, 0.1, 10, "float")],
           [Output("upper", style="band_upper"), Output("basis"), Output("lower", style="band_lower"),
            Output("width", "separate"), Output("percent_b", "separate")], family="band_volatility")
def _bb(df, length, mult):
    b = sma(df["close"], length)
    sd = stdev(df["close"], length)
    u, lo = b + mult * sd, b - mult * sd
    return {"upper": u, "basis": b, "lower": lo, "width": (u - lo) / b,
            "percent_b": (df["close"] - lo) / (u - lo).replace(0, np.nan)}


@indicator("keltner", "Keltner Channels", "volatility",
           [P("length", 20, 1), P("mult", 2.0, 0.1, 10, "float"), P("atr_length", 10, 1)],
           [Output("upper", style="band_upper"), Output("basis"), Output("lower", style="band_lower")],
           family="band_volatility")
def _kc(df, length, mult, atr_length):
    b = ema(df["close"], length)
    a = atr(df, atr_length)
    return {"upper": b + mult * a, "basis": b, "lower": b - mult * a}


@indicator("stdev", "Standard Deviation", "volatility", [P("length", 20, 2)], [Output("stdev", "separate")],
           family="return_volatility")
def _stdev(df, length):
    return {"stdev": stdev(df["close"], length)}


@indicator("hv", "Historical Volatility (annualised %)", "volatility", [P("length", 20, 2)],
           [Output("hv", "separate")], family="return_volatility")
def _hv(df, length):
    r = np.log(df["close"] / df["close"].shift(1))
    return {"hv": 100 * r.rolling(length, min_periods=length).std() * np.sqrt(_bars_per_year(df))}


@indicator("rvol_realized", "Realized Volatility (Parkinson, annualised %)", "volatility", [P("length", 20, 2)],
           [Output("rv", "separate")], family="return_volatility",
           description="Parkinson range-based estimator: sqrt(mean(ln(H/L)^2)/(4 ln2))")
def _rv(df, length):
    x = np.log(df["high"] / df["low"]) ** 2
    rv = np.sqrt(x.rolling(length, min_periods=length).mean() / (4 * np.log(2)))
    return {"rv": 100 * rv * np.sqrt(_bars_per_year(df))}


@indicator("chop", "Choppiness Index", "volatility", [P("length", 14, 2)], [Output("chop", "separate")],
           family="trend_strength", levels=[38.2, 61.8])
def _chop(df, length):
    s = true_range(df).rolling(length, min_periods=length).sum()
    rng = (highest(df["high"], length) - lowest(df["low"], length)).replace(0, np.nan)
    return {"chop": 100 * np.log10(s / rng) / np.log10(length)}


@indicator("squeeze", "Volatility Contraction (BB inside KC)", "volatility",
           [P("length", 20, 2), P("bb_mult", 2.0, 0.1, 10, "float"), P("kc_mult", 1.5, 0.1, 10, "float")],
           [Output("squeeze_on", "separate", "histogram"), Output("bbw_pct", "separate")],
           family="band_volatility",
           description="squeeze_on=1 when Bollinger Bands are inside Keltner Channels; bbw_pct = percentile of "
                       "BB width over trailing 120 bars (low = contraction, high = expansion)")
def _squeeze(df, length, bb_mult, kc_mult):
    b = sma(df["close"], length)
    sd = stdev(df["close"], length)
    a = atr(df, length)
    k = ema(df["close"], length)
    on = ((b + bb_mult * sd) < (k + kc_mult * a)) & ((b - bb_mult * sd) > (k - kc_mult * a))
    w = (2 * bb_mult * sd) / b
    pct = w.rolling(120, min_periods=30).apply(lambda a: (a[:-1] < a[-1]).mean() * 100 if len(a) > 1 else np.nan,
                                                raw=True)
    return {"squeeze_on": on.astype(float).where(w.notna()), "bbw_pct": pct}
