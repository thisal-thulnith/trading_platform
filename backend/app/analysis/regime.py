"""Rule-based market regime classifier (transparent baseline). A statistical regime model (HMM on returns/
volatility) is trained in app/ml and can override when it is validated."""
import numpy as np
import pandas as pd

from app.analysis.indicators.core import atr, ema
from app.analysis.indicators.trend import dmi
from app.analysis.indicators.volatility import _chop

REGIMES = ["strong_bull", "weak_bull", "strong_bear", "weak_bear", "range", "high_volatility", "low_volatility",
           "breakout", "liquidity_stress", "news_instability"]

# Which strategy families each regime historically tends to suit (prior; replaced by measured performance)
REGIME_STRATEGY_PRIOR = {
    "strong_bull": ["trend_following", "breakout_retest", "smc_continuation"],
    "weak_bull": ["pullback", "smc_continuation", "trend_following"],
    "strong_bear": ["trend_following", "breakout_retest", "smc_continuation"],
    "weak_bear": ["pullback", "smc_continuation", "trend_following"],
    "range": ["mean_reversion", "liquidity_sweep_reversal", "vwap_reversion"],
    "high_volatility": ["abstain", "liquidity_sweep_reversal"],
    "low_volatility": ["volatility_breakout"],
    "breakout": ["volatility_breakout", "breakout_retest"],
    "liquidity_stress": ["abstain"],
    "news_instability": ["abstain"],
}


def classify_regime(df: pd.DataFrame, book: dict | None = None, news_risk: bool = False) -> dict:
    if len(df) < 120:
        return {"regime": "undetermined", "reasons": ["insufficient history"], "features": {}}
    c = df["close"]
    pdi, mdi, adx = dmi(df, 14)
    e50, e200 = ema(c, 50), ema(c, min(200, len(df) - 1))
    a = atr(df, 14)
    chop = _chop(df, 14)["chop"]
    ret = np.log(c / c.shift(1))
    vol = ret.rolling(20).std()
    vol_pct = float((vol.tail(250) < vol.iloc[-1]).mean() * 100)
    slope50 = float((e50.iloc[-1] - e50.iloc[-11]) / a.iloc[-1]) if np.isfinite(a.iloc[-1]) else 0.0
    f = {
        "adx": float(adx.iloc[-1]), "plus_di": float(pdi.iloc[-1]), "minus_di": float(mdi.iloc[-1]),
        "ema50_slope_atr_per_10": slope50, "price_vs_ema200_pct": float((c.iloc[-1] / e200.iloc[-1] - 1) * 100),
        "chop": float(chop.iloc[-1]), "vol_percentile": vol_pct,
        "range_20_atr": float((df["high"].tail(20).max() - df["low"].tail(20).min()) / a.iloc[-1]),
    }
    hi20 = df["high"].iloc[-21:-1].max()
    lo20 = df["low"].iloc[-21:-1].min()
    breakout = c.iloc[-1] > hi20 or c.iloc[-1] < lo20
    reasons = []
    secondary = []
    if book and book.get("available") and (book.get("spread_bps", 0) > 15 or book.get("slippage_buy_bps") is None):
        regime = "liquidity_stress"
        reasons.append(f"spread {book.get('spread_bps', 0):.1f} bps / thin visible depth")
    elif news_risk:
        regime = "news_instability"
        reasons.append("high-impact news within the last hours")
    elif vol_pct >= 95:
        regime = "high_volatility"
        reasons.append(f"20-bar realised vol at {vol_pct:.0f}th percentile of last 250 bars")
    elif breakout and f["range_20_atr"] < 8 and vol_pct > 40:
        regime = "breakout"
        reasons.append("close outside prior 20-bar range")
    elif f["adx"] >= 25 and abs(slope50) > 0.5:
        bull = f["plus_di"] > f["minus_di"] and slope50 > 0
        regime = "strong_bull" if bull else "strong_bear"
        reasons.append(f"ADX {f['adx']:.1f} ≥ 25 with EMA50 slope {slope50:+.2f} ATR/10 bars")
    elif f["chop"] > 61.8 or f["adx"] < 18:
        regime = "range"
        reasons.append(f"Choppiness {f['chop']:.1f} / ADX {f['adx']:.1f} indicates non-trending")
    else:
        bull = slope50 > 0 and f["plus_di"] >= f["minus_di"]
        regime = "weak_bull" if bull else "weak_bear"
        reasons.append(f"moderate trend: ADX {f['adx']:.1f}, EMA50 slope {slope50:+.2f}")
    if vol_pct <= 10:
        secondary.append("low_volatility")
    if vol_pct >= 90 and regime != "high_volatility":
        secondary.append("high_volatility")
    return {"regime": regime, "secondary": secondary, "reasons": reasons, "features": f,
            "suited_strategies": REGIME_STRATEGY_PRIOR.get(regime, []), "method": "rule_based_v1"}
