"""CTREND — a cross-sectional trend factor that combines many technical indicators with an elastic net.

After Fieberg, Liedtke, Metko & Zaremba / Poddig ("A trend factor for the cross-section of cryptocurrency returns",
JFQA 2025): price moving-average ratios over several windows, momentum oscillators and volume indicators are each
ranked across the universe, and an elastic net estimated on PAST data only maps them to the next week's
cross-sectional return rank. The factor value on D is the model's prediction from indicators known at the close of D.

Point in time: every indicator on D uses rows dated <= D (rolling windows / EWMs, no shift(-n)). The elastic net used
on dates in month m is fitted on rows dated <= (first day of m) - h - 1, so every training label (forward h-day
return) is realised before the month starts. Refit monthly on an expanding window; alpha is chosen by ElasticNetCV
with a time-ordered split inside the training rows only.

Indicator notes: CCI uses the rolling standard deviation of the typical price instead of the mean absolute deviation
(a rank transform follows, so only the ordering matters); volume indicators use quote volume (USD).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from app.quant.lab import model as qm
from app.quant.lab.factors import Wide

MA_WINDOWS = (3, 5, 10, 20, 50, 100, 200)
VOL_WINDOWS = (10, 20, 50, 100, 200)
H = 7                     # target: next-week cross-sectional return rank (the paper's weekly horizon)
START = "2020-01-01"      # first monthly refit (trained on 2019 data)
MIN_TRAIN_ROWS = 1000
L1_RATIO = 0.5


def indicators(w: Wide) -> dict[str, pd.DataFrame]:
    """20 technical indicators per (date, coin), each from data dated <= D."""
    c, h, qv = w.close, w.high, w.quote_volume
    lo = w.low if w.low is not None else c
    out: dict[str, pd.DataFrame] = {}
    for n in MA_WINDOWS:                                           # price relative to its moving averages
        out[f"ma_{n}"] = c / c.rolling(n, min_periods=max(2, int(n * 0.8))).mean() - 1
    v3 = qv.rolling(3, min_periods=2).mean()
    for n in VOL_WINDOWS:                                          # short-term volume relative to longer averages
        out[f"vma_{n}"] = np.log(v3 / qv.rolling(n, min_periods=int(n * 0.8)).mean())
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    out["rsi_14"] = 100 - 100 / (1 + up / dn.where(dn > 0))
    hh, ll = h.rolling(14, min_periods=10).max(), lo.rolling(14, min_periods=10).min()
    k = (c - ll) / (hh - ll).where(hh > ll)
    out["stoch_k_14"] = k
    out["stoch_d_3"] = k.rolling(3, min_periods=2).mean()
    tp = (h + lo + c) / 3
    out["cci_20"] = (tp - tp.rolling(20, min_periods=15).mean()) / (0.015 * tp.rolling(20, min_periods=15).std())
    e12 = c.ewm(span=12, min_periods=12, adjust=False).mean()
    e26 = c.ewm(span=26, min_periods=26, adjust=False).mean()
    macd = e12 - e26
    out["macd"] = macd / c
    out["macd_hist"] = (macd - macd.ewm(span=9, min_periods=9, adjust=False).mean()) / c
    m20, s20 = c.rolling(20, min_periods=15).mean(), c.rolling(20, min_periods=15).std()
    out["boll_b_20"] = (c - (m20 - 2 * s20)) / (4 * s20).where(s20 > 0)
    mfm = ((c - lo) - (h - c)) / (h - lo).where(h > lo)
    out["cmf_20"] = (mfm * qv).rolling(20, min_periods=15).sum() / qv.rolling(20, min_periods=15).sum()
    return {k_: v.replace([np.inf, -np.inf], np.nan) for k_, v in out.items()}


INDICATORS = ([f"ma_{n}" for n in MA_WINDOWS] + [f"vma_{n}" for n in VOL_WINDOWS]
              + ["rsi_14", "stoch_k_14", "stoch_d_3", "cci_20", "macd", "macd_hist", "boll_b_20", "cmf_20"])


def fit_enet(X: np.ndarray, y: np.ndarray, seed: int = 0):
    from sklearn.linear_model import ElasticNetCV
    from sklearn.model_selection import TimeSeriesSplit
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ElasticNetCV(l1_ratio=L1_RATIO, alphas=30, cv=TimeSeriesSplit(5), max_iter=5000,
                            random_state=seed, selection="cyclic").fit(X, y)


def walk_forward(w: Wide, univ: pd.DataFrame, h: int = H, start: str = START,
                 min_rows: int = MIN_TRAIN_ROWS) -> tuple[pd.DataFrame, list[dict]]:
    """CTREND as a dates x symbols frame (NaN outside the universe and before the first fit) + per-fit coefficients."""
    ind = indicators(w)
    ds = qm.build_dataset(w, ind, univ, horizons=(h,), names=INDICATORS)
    rd = ds.row_dates
    pred = np.full(len(rd), np.nan)
    fits = []
    months = pd.date_range(start, rd.max(), freq="MS")
    for i, a in enumerate(months):
        b = months[i + 1] - pd.Timedelta(days=1) if i + 1 < len(months) else rd.max()
        test = (rd >= a) & (rd <= b)
        train = (rd <= a - pd.Timedelta(days=h + 1)) & np.isfinite(ds.y[h])
        if train.sum() < min_rows or not test.any():
            continue
        m = fit_enet(ds.X[train], ds.y[h][train])
        pred[test] = m.predict(ds.X[test])
        fits.append({"month": str(a.date()), "n_train": int(train.sum()), "alpha": float(m.alpha_),
                     "coef": {f: round(float(v), 5) for f, v in zip(INDICATORS, m.coef_)},
                     "nonzero": int((m.coef_ != 0).sum())})
    return pd.DataFrame(ds.to_matrix(pred), index=w.dates, columns=w.symbols), fits
