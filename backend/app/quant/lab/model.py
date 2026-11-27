"""Phase 3a — model ladder on cross-sectional factor ranks, walk-forward and CPCV prediction generators.

Ladder (each rung kept only if it beats the simpler one out of sample, see validate.py):
  (a) composite — equal-weight sum of signed cross-sectional z-scores (of ranks) of the factors that passed the
      pre-specified IC screen (research.screen) on the TRAINING dates only (purged by the target horizon);
  (b) ridge     — Ridge(alpha=1000) on all factor ranks (centred, missing = 0);
  (c) lgbm      — LightGBM regressor on all factor ranks (small, heavily regularised, fixed hyper-parameters).
Target: cross-sectional rank (centred) of the forward h-day return, h in {3, 7}.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from app.quant.lab.factors import FACTORS, Wide, forward_returns
from app.quant.lab.research import screen

RIDGE_ALPHA = 1000.0
LGBM_PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=1000, subsample=0.7,
                   subsample_freq=1, colsample_bytree=0.7, reg_lambda=10.0, random_state=0, n_jobs=8, verbose=-1)
MODELS = ("composite", "ridge", "lgbm")


@dataclass
class Dataset:
    dates: pd.DatetimeIndex
    symbols: pd.Index
    t_idx: np.ndarray
    s_idx: np.ndarray
    X: np.ndarray                  # n x F centred ranks in [-0.5, 0.5], missing = 0
    y: dict[int, np.ndarray]       # centred rank of the forward h-day return (NaN at the end of the sample)
    fwd: dict[int, np.ndarray]     # raw forward returns (for evaluation)
    factor_names: list[str]

    @property
    def row_dates(self) -> pd.DatetimeIndex:
        return self.dates[self.t_idx]

    def to_matrix(self, pred: np.ndarray) -> np.ndarray:
        S = np.full((len(self.dates), len(self.symbols)), np.nan)
        S[self.t_idx, self.s_idx] = pred
        return S


def build_dataset(w: Wide, facs: dict[str, pd.DataFrame], univ: pd.DataFrame,
                  horizons=(3, 7), names: list[str] | None = None) -> Dataset:
    names = names or FACTORS
    U = univ.to_numpy()
    t_idx, s_idx = np.nonzero(U)
    X = np.zeros((len(t_idx), len(names)), dtype=np.float32)
    for j, f in enumerate(names):
        r = facs[f].where(univ).rank(axis=1, pct=True).to_numpy()
        n = np.isfinite(np.where(U, facs[f].to_numpy(), np.nan)).sum(axis=1, keepdims=True)
        # centre: mean pct-rank of n names is (n+1)/(2n)
        c = r - (n + 1) / (2 * np.maximum(n, 1))
        X[:, j] = np.nan_to_num(c[t_idx, s_idx], nan=0.0)
    y, fwd = {}, {}
    for h in horizons:
        fr = forward_returns(w, h).where(univ)
        rk = fr.rank(axis=1, pct=True).to_numpy()
        n = np.isfinite(fr.to_numpy()).sum(axis=1, keepdims=True)
        c = rk - (n + 1) / (2 * np.maximum(n, 1))
        y[h] = c[t_idx, s_idx]
        fwd[h] = fr.to_numpy()[t_idx, s_idx]
    return Dataset(dates=w.dates, symbols=w.symbols, t_idx=t_idx, s_idx=s_idx, X=X, y=y, fwd=fwd,
                   factor_names=list(names))


# --- models -----------------------------------------------------------------------------------------------------
def fit(ds: Dataset, model: str, h: int, train_rows: np.ndarray, ic_table: pd.DataFrame | None = None,
        train_dates: pd.DatetimeIndex | None = None) -> dict:
    """Fit one rung on ``train_rows`` (boolean mask over dataset rows). Returns a serialisable state."""
    if model == "composite":
        signs = screen(ic_table, train_dates, h)
        return {"model": "composite", "h": h, "signs": signs}
    rows = train_rows & np.isfinite(ds.y[h])
    X, y = ds.X[rows], ds.y[h][rows]
    if model == "ridge":
        from sklearn.linear_model import Ridge
        m = Ridge(alpha=RIDGE_ALPHA).fit(X, y)
        return {"model": "ridge", "h": h, "coef": dict(zip(ds.factor_names, map(float, m.coef_))),
                "intercept": float(m.intercept_)}
    if model == "lgbm":
        import lightgbm as lgb
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = lgb.LGBMRegressor(**LGBM_PARAMS).fit(X, y)
        return {"model": "lgbm", "h": h, "booster": m.booster_}
    raise ValueError(model)


def predict(state: dict, X: np.ndarray, names: list[str]) -> np.ndarray:
    if state["model"] == "composite":
        if not state["signs"]:
            return np.full(len(X), np.nan)
        cols = [names.index(f) for f in state["signs"]]
        sg = np.array([state["signs"][f] for f in state["signs"]], dtype=float)
        # z-score of a uniform rank: (rank - 0.5) * sqrt(12); equal weights
        return (X[:, cols] * np.sqrt(12)) @ sg / len(cols)
    if state["model"] == "ridge":
        coef = np.array([state["coef"].get(f, 0.0) for f in names])
        return X @ coef + state["intercept"]
    if state["model"] == "lgbm":
        return state["booster"].predict(X)
    raise ValueError(state["model"])


def purge_mask(row_dates: pd.DatetimeIndex, test_ranges: list[tuple], h: int, embargo: int) -> np.ndarray:
    """True for rows whose label window [D, D+h] does not touch a test range and that are outside the embargo."""
    ok = np.ones(len(row_dates), dtype=bool)
    for a, b in test_ranges:
        lo = a - pd.Timedelta(days=h)
        hi = b + pd.Timedelta(days=embargo)
        ok &= ~((row_dates >= lo) & (row_dates <= hi))
    return ok


def walk_forward(ds: Dataset, model: str, h: int, ic_table: pd.DataFrame | None = None,
                 start: str = "2021-01-01", freq: str = "QS") -> tuple[np.ndarray, list[dict]]:
    """Expanding-window walk-forward, refit each quarter, training labels purged before the test block."""
    rd = ds.row_dates
    end = rd.max()
    starts = pd.date_range(start, end, freq=freq)
    pred = np.full(len(rd), np.nan)
    folds = []
    for i, a in enumerate(starts):
        b = starts[i + 1] - pd.Timedelta(days=1) if i + 1 < len(starts) else end
        test = (rd >= a) & (rd <= b)
        train = (rd <= a - pd.Timedelta(days=h + 1))
        tdates = ds.dates[(ds.dates <= a - pd.Timedelta(days=h + 1))]
        st = fit(ds, model, h, train, ic_table, tdates)
        pred[test] = predict(st, ds.X[test], ds.factor_names)
        folds.append(_fold_info(st, a, b, int(train.sum())))
    return pred, folds


def _fold_info(st: dict, a, b, n_train: int) -> dict:
    info = {"test_start": str(a.date()), "test_end": str(pd.Timestamp(b).date()), "n_train_rows": n_train}
    if st["model"] == "composite":
        info["factors"] = st["signs"]
    elif st["model"] == "ridge":
        info["coef"] = {k: round(v, 5) for k, v in st["coef"].items()}
    return info


def cpcv_groups(ds: Dataset, h: int, n_groups: int = 10, min_names: int = 10) -> list[tuple]:
    rd = ds.row_dates
    cnt = pd.Series(1, index=rd).groupby(level=0).size()
    valid = cnt[cnt >= min_names].index
    lab = pd.Series(np.isfinite(ds.y[h]), index=rd).groupby(level=0).any()
    valid = valid[lab.reindex(valid).fillna(False).to_numpy()]
    chunks = np.array_split(valid, n_groups)
    return [(c[0], c[-1]) for c in chunks]


def cpcv(ds: Dataset, model: str, h: int, ic_table: pd.DataFrame | None = None, n_groups: int = 10, k: int = 2,
         embargo: int = 14) -> tuple[list[np.ndarray], list[tuple]]:
    """Combinatorial purged CV (Lopez de Prado): returns one prediction vector per backtest path."""
    groups = cpcv_groups(ds, h, n_groups)
    rd = ds.row_dates
    splits = list(combinations(range(n_groups), k))
    n_paths = len(splits) * k // n_groups
    preds_by_split = {}
    for sp in splits:
        ranges = [groups[g] for g in sp]
        train = purge_mask(rd, ranges, h, embargo) & np.isfinite(ds.y[h])
        tdates = ds.dates[purge_mask(ds.dates, ranges, h, embargo)]
        st = fit(ds, model, h, train, ic_table, tdates)
        test = np.zeros(len(rd), dtype=bool)
        for a, b in ranges:
            test |= (rd >= a) & (rd <= b)
        p = np.full(len(rd), np.nan)
        p[test] = predict(st, ds.X[test], ds.factor_names)
        preds_by_split[sp] = p
    paths = [np.full(len(rd), np.nan) for _ in range(n_paths)]
    for g in range(n_groups):
        a, b = groups[g]
        m = (rd >= a) & (rd <= b)
        for j, sp in enumerate([s for s in splits if g in s]):
            paths[j][m] = preds_by_split[sp][m]
    return paths, groups


def daily_ic(ds: Dataset, pred: np.ndarray, h: int, min_names: int = 10) -> pd.Series:
    """Daily Spearman IC between predictions and forward h-day returns over the dataset rows."""
    df = pd.DataFrame({"t": ds.t_idx, "p": pred, "f": ds.fwd[h]}).dropna()
    df["rp"] = df.groupby("t")["p"].rank()
    df["rf"] = df.groupby("t")["f"].rank()
    g = df.groupby("t")
    n = g.size()
    ic = g.apply(lambda x: np.corrcoef(x["rp"], x["rf"])[0, 1] if len(x) >= min_names and x["rp"].std() > 0
                 else np.nan, include_groups=False)
    ic = ic[n >= min_names]
    ic.index = ds.dates[ic.index.to_numpy()]
    return ic.dropna()
