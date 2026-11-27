"""Rolling split-conformal intervals around the model's predicted cross-sectional rank, and abstention masks.

For every (D, coin) with a model prediction:
- point prediction p = the coin's centred cross-sectional rank of the model score on D (in [-0.5, 0.5]);
- outcome y = the centred cross-sectional rank of its forward h-day return (model.build_dataset's target);
- residual e = y - p, known only at D + h;
- local scale sigma_i(D) = root mean square of the coin's own residuals over rows dated in [D - h - SIGMA_DAYS, D - h - 1]
  (at least SIGMA_MIN of them; otherwise the cross-sectional median scale on D);
- nonconformity score s = |e| / sigma_i(row date);
- q(D) = the ceil((n + 1)(1 - alpha))-th smallest score among the n rows dated in [D - h - CALIB_DAYS, D - h - 1],
  i.e. the calibration set is the most recent CALIB_DAYS days whose outcomes are fully realised by D (rolling split
  conformal; past residuals only);
- interval [p - q(D) sigma_i(D), p + q(D) sigma_i(D)].
A coin may be held long only if the interval lies entirely above the neutral rank 0, short only if entirely below;
otherwise the book abstains on it.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CALIB_DAYS = 90
SIGMA_DAYS = 60
SIGMA_MIN = 20


def centred_rank(S: np.ndarray) -> np.ndarray:
    """Row-wise centred percentile rank of a dates x symbols score matrix (NaN stays NaN)."""
    df = pd.DataFrame(S)
    r = df.rank(axis=1, pct=True).to_numpy()
    n = np.isfinite(S).sum(axis=1, keepdims=True)
    return r - (n + 1) / (2 * np.maximum(n, 1))


def conformal_quantile(scores: np.ndarray, alpha: float) -> float:
    s = np.sort(scores[np.isfinite(scores)])
    n = len(s)
    if n == 0:
        return float("nan")
    k = math.ceil((n + 1) * (1 - alpha))
    return float(s[k - 1]) if k <= n else float("inf")


def intervals(P: np.ndarray, Y: np.ndarray, h: int, alphas=(0.1,), calib_days: int = CALIB_DAYS,
              sigma_days: int = SIGMA_DAYS, sigma_min: int = SIGMA_MIN) -> dict:
    """Rolling conformal intervals for dates x symbols matrices of predicted ranks P and realised ranks Y.

    Returns {"sigma": T x N, "q": {alpha: T}, "lower"/"upper": {alpha: T x N}}. Row t uses only Y of rows dated
    <= t - h - 1."""
    T, N = P.shape
    E = Y - P                                       # NaN where either is missing
    E2 = pd.DataFrame(E**2)
    cnt = E2.notna().rolling(sigma_days, min_periods=1).sum()
    ms = E2.rolling(sigma_days, min_periods=1).mean()
    sig = np.sqrt(ms.where(cnt >= sigma_min)).shift(h + 1)       # residuals realised by t
    med = sig.median(axis=1)
    sig = sig.apply(lambda col: col.fillna(med)).to_numpy()
    sig = np.where(np.isfinite(sig) & (sig > 0), sig, np.nan)
    score = np.abs(E) / sig
    out = {"sigma": sig, "q": {}, "lower": {}, "upper": {}}
    for a in alphas:
        q = np.full(T, np.nan)
        for t in range(T):
            lo, hi = t - h - calib_days, t - h - 1
            if hi < 0:
                continue
            block = score[max(lo, 0): hi + 1]
            if np.isfinite(block).sum() >= 100:
                q[t] = conformal_quantile(block.ravel(), a)
        out["q"][a] = q
        out["lower"][a] = P - q[:, None] * sig
        out["upper"][a] = P + q[:, None] * sig
    return out


def coverage(Y: np.ndarray, lower: np.ndarray, upper: np.ndarray, dates: pd.DatetimeIndex | None = None) -> dict:
    """Share of rows whose realised rank falls inside its interval (overall and by year)."""
    ok = np.isfinite(Y) & np.isfinite(lower) & np.isfinite(upper)
    hit = (Y >= lower) & (Y <= upper)
    out = {"rows": int(ok.sum()), "coverage": round(float(hit[ok].mean()), 4) if ok.any() else None}
    if dates is not None and ok.any():
        out["by_year"] = {int(y): round(float(hit[dates.year == y][ok[dates.year == y]].mean()), 4)
                          for y in sorted(set(dates.year)) if ok[dates.year == y].any()}
    return out


def abstention_masks(lower: np.ndarray, upper: np.ndarray, neutral: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(long_ok, short_ok): the interval lies entirely above / below the neutral rank."""
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(lower, nan=-np.inf) > neutral, np.nan_to_num(upper, nan=np.inf) < neutral
