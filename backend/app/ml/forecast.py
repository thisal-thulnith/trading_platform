"""Forecast models: trained LightGBM models on the survivorship-free daily panel (591 Binance USDT coins since 2019),
each predicting one thing a trader needs, each with its accuracy measured on years it never saw.

Targets (all from closes/highs/lows strictly after day D; features strictly from data dated <= D):
  direction_7d     P(close D+7 > close D)
  outperform_7d    P(the coin's 7-day return beats the median coin's that week)
  move_7d          expected 7-day high-to-low range as % of the price (regression on its log)
  drop_risk_7d     P(the low falls 2 ATR below the close within 7 days)
  long_tp_first    P(a long with stop -1.5 ATR and target +3 ATR (2R) reaches the target first within 14 days)
  short_tp_first   the same for a short

Validation: expanding walk-forward by calendar year (test 2022 ... 2026); every training set ends 15 days before its
test year (labels look up to 14 days ahead, so nothing overlaps). Four candidates per target — a volatility-only
formula (the baseline every model must beat), regularised linear models on core features (+ the learned chart
patterns, + CTREND-style technical indicators), LightGBM averaged over three seeds on every feature, and blends of a
linear model with LightGBM (see app/ml/upgrade_eval.py for the test that added the indicators and the seeds). The candidate is CHOSEN on 2022-2024 only and REPORTED on 2025-2026, years that played no part in the
choice. Classifiers are judged by AUC, Brier skill against the base rate, AUC in every test year, and lift (the hit
rate of the top and bottom tenth of forecasts); the move model by R². Calibration: isotonic, fitted on the pooled
out-of-sample predictions. Status: "accepted", "weak" (shown, labelled weak) or "rejected" (not shown as a signal),
and every model says whether it beats the volatility-only baseline. Nothing here places orders.

Train:   uv run python -m app.ml.forecast train      (≈ 5-10 minutes on 8 cores)
Predict: uv run python -m app.ml.forecast predict    (seconds; the backend also refreshes it daily)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.logging import log

MODELS = Path("data/models")
TODAY = MODELS / "forecast_today.json"
# two tiers: liquid coins get the more accurate "core" models; thinner coins the "wide" ones, labelled less accurate
TIERS = {"core": 5e6, "wide": 1e6}


def _report_path(tier: str) -> Path:
    return MODELS / f"forecast_report_{tier}.json"


def _oos_path(tier: str) -> Path:
    return MODELS / f"forecast_oos_{tier}.npz"


def _artifact_path(tier: str) -> Path:
    return MODELS / f"forecast_models_{tier}.joblib"
TEST_YEARS = (2020, 2021, 2022, 2023, 2024, 2025, 2026)   # with the full history since Aug 2017
PURGE_DAYS = 15
STOP_ATR, TARGET_ATR, TP_DAYS = 1.5, 3.0, 14
DROP_ATR = 2.0

TARGETS = {
    "direction_7d": {"kind": "binary", "label": "Up in 7 days", "what": "chance the price is higher 7 days from now"},
    "outperform_7d": {"kind": "binary", "label": "Beats the market", "what": "chance it beats the median coin over 7 days"},
    "move_7d": {"kind": "regression", "label": "Expected 7-day range", "what": "expected high-to-low range over 7 days"},
    "drop_risk_7d": {"kind": "binary", "label": "Drop risk", "what": f"chance of a {DROP_ATR:g}-ATR drop within 7 days"},
    "long_tp_first": {"kind": "binary", "label": "Long hits target",
                      "what": f"chance a long (stop {STOP_ATR:g} ATR, target {TARGET_ATR:g} ATR) hits the target first within {TP_DAYS} days"},
    "short_tp_first": {"kind": "binary", "label": "Short hits target",
                       "what": f"chance a short (stop {STOP_ATR:g} ATR, target {TARGET_ATR:g} ATR) hits the target first within {TP_DAYS} days"},
}
CLS_PARAMS = dict(objective="binary", n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=500,
                  subsample=0.7, subsample_freq=1, colsample_bytree=0.7, reg_lambda=10.0, random_state=0, n_jobs=8,
                  verbose=-1)
REG_PARAMS = {**CLS_PARAMS, "objective": "regression"}


# ------------------------------------------------------------------------------------------- data

def load_wide():
    from app.ml.history import load_full
    from app.quant.lab.data import load_funding_daily
    from app.quant.lab.factors import build_wide

    try:
        fund = load_funding_daily()
    except Exception:  # noqa: BLE001 - funding is one feature among many
        fund = None
    return build_wide(load_full(), fund)


def _atr(w) -> pd.DataFrame:
    pc = w.close.shift(1)
    tr = pd.concat({"a": w.high - w.low, "b": (w.high - pc).abs(), "c": (w.low - pc).abs()}).groupby(level=1).max()
    tr = tr.reindex(index=w.close.index, columns=w.close.columns)
    return tr.rolling(14, min_periods=10).mean()


def features(w, min_adv: float = TIERS["core"]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Every feature as a dates x symbols frame, from data dated <= D only, and the universe mask."""
    from app.quant.lab.factors import compute_factors, universe

    facs = compute_factors(w)
    U = universe(w, min_history=90, min_adv=min_adv)
    c, lret = w.close, np.log(w.close / w.close.shift(1))
    atr = _atr(w)
    f: dict[str, pd.DataFrame] = {}
    for k, v in facs.items():
        f[k] = v
        f[f"r_{k}"] = v.where(U).rank(axis=1, pct=True)          # where it sits in today's universe
    f["atr_pct"] = atr / c
    f["rvol_7"] = lret.rolling(7, min_periods=5).std()
    f["range_7"] = (w.high.rolling(7, min_periods=5).max() - w.low.rolling(7, min_periods=5).min()) / c
    f["ret_1"] = c / c.shift(1) - 1
    f["dist_ma50"] = c / c.rolling(50, min_periods=35).mean() - 1
    f["dist_ma200"] = c / c.rolling(200, min_periods=140).mean() - 1
    # the market on day D, the same for every coin
    btc = c["BTCUSDT"] if "BTCUSDT" in c.columns else c.median(axis=1)
    above50 = (c > c.rolling(50, min_periods=35).mean()).where(U)
    mk = {"btc_mom_7": btc / btc.shift(7) - 1, "btc_mom_28": btc / btc.shift(28) - 1,
          "btc_rvol_30": np.log(btc / btc.shift(1)).rolling(30, min_periods=20).std(),
          "btc_dist_ma200": btc / btc.rolling(200, min_periods=140).mean() - 1,
          "breadth_50": above50.mean(axis=1), "mkt_mom_7": f["mom_7"].where(U).median(axis=1)}
    for k, s in mk.items():
        f[k] = pd.DataFrame(np.repeat(s.to_numpy()[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)
    from app.ml.technicals import technicals

    f |= technicals(w, U)   # CTREND-style indicators, raw and ranked (app/ml/technicals.py)
    for k in f:
        f[k] = f[k].replace([np.inf, -np.inf], np.nan)
    return f, U


def labels(w) -> dict[str, pd.DataFrame]:
    """Targets from strictly later candles; NaN where the future is not known yet."""
    c, h, lo = w.close, w.high, w.low
    atr = _atr(w)
    cf = c.ffill()
    out: dict[str, pd.DataFrame] = {}
    fwd7 = cf.shift(-7) / c - 1
    known7 = c.notna() & cf.shift(-7).notna() & (np.arange(len(c))[:, None] < len(c) - 7)
    out["direction_7d"] = (fwd7 > 0).astype(float).where(known7)
    med = fwd7.where(known7).median(axis=1)
    out["outperform_7d"] = fwd7.gt(med, axis=0).astype(float).where(known7)
    hi7 = pd.concat([h.shift(-k) for k in range(1, 8)]).groupby(level=0).max().reindex(c.index)
    lo7 = pd.concat([lo.shift(-k) for k in range(1, 8)]).groupby(level=0).min().reindex(c.index)
    out["move_7d"] = np.log(((hi7 - lo7) / c).where(known7 & (hi7 > lo7)))
    out["drop_risk_7d"] = (lo7 <= c - DROP_ATR * atr).astype(float).where(known7 & atr.notna())
    # first touch of stop or target within TP_DAYS (a day touching both counts as the stop)
    n = len(c)
    known14 = c.notna() & atr.notna() & (np.arange(n)[:, None] < n - TP_DAYS)
    for side, d in (("long", 1), ("short", -1)):
        stop = c - d * STOP_ATR * atr
        tgt = c + d * TARGET_ATR * atr
        first_t = pd.DataFrame(np.inf, index=c.index, columns=c.columns)
        first_s = pd.DataFrame(np.inf, index=c.index, columns=c.columns)
        for k in range(TP_DAYS, 0, -1):  # walk backwards so the earliest day wins
            hk, lk = h.shift(-k), lo.shift(-k)
            ht = (hk >= tgt) if d == 1 else (lk <= tgt)
            hs = (lk <= stop) if d == 1 else (hk >= stop)
            first_t = first_t.mask(ht.fillna(False), k)
            first_s = first_s.mask(hs.fillna(False), k)
        win = (first_t < first_s) & np.isfinite(first_t)
        out[f"{side}_tp_first"] = win.astype(float).where(known14)
    return out


def stack(f: dict[str, pd.DataFrame], U: pd.DataFrame, ys: dict[str, pd.DataFrame] | None = None):
    """Rows = (date, symbol) inside the universe; columns = features (+ labels)."""
    t_idx, s_idx = np.nonzero(U.to_numpy())
    names = sorted(f)
    X = np.empty((len(t_idx), len(names)), dtype=np.float32)
    for j, k in enumerate(names):
        X[:, j] = f[k].to_numpy(dtype=np.float32)[t_idx, s_idx]
    Y = {k: v.to_numpy()[t_idx, s_idx] for k, v in (ys or {}).items()}
    return X, Y, names, U.index[t_idx], U.columns[s_idx]


# ------------------------------------------------------------------------------------------- metrics

def _cls_metrics(y: np.ndarray, p: np.ndarray, base: float) -> dict:
    from sklearn.metrics import roc_auc_score

    out = {"n": int(len(y)), "base_rate": round(float(y.mean()), 4)}
    if len(np.unique(y)) < 2:
        return out
    brier = float(np.mean((p - y) ** 2))
    brier0 = float(np.mean((base - y) ** 2))
    out |= {"auc": round(float(roc_auc_score(y, p)), 4), "brier": round(brier, 4),
            "brier_skill": round(1 - brier / brier0, 4) if brier0 else None,
            "accuracy": round(float(((p >= 0.5) == (y == 1)).mean()), 4)}
    hi, lo = p >= 0.6, p <= 0.4
    conf = hi | lo
    out["confident_share"] = round(float(conf.mean()), 4)
    out["confident_accuracy"] = round(float(((p[conf] >= 0.5) == (y[conf] == 1)).mean()), 4) if conf.any() else None
    q = np.quantile(p, [0.1, 0.9])
    out["top_decile_rate"] = round(float(y[p >= q[1]].mean()), 4)
    out["bottom_decile_rate"] = round(float(y[p <= q[0]].mean()), 4)
    return out


def _reg_metrics(y: np.ndarray, p: np.ndarray, naive: np.ndarray) -> dict:
    from scipy.stats import spearmanr

    ok = np.isfinite(naive)
    ss = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - float(np.sum((y - p) ** 2)) / ss
    r2n = 1 - float(np.sum((y[ok] - naive[ok]) ** 2)) / float(np.sum((y[ok] - y[ok].mean()) ** 2))
    mape = float(np.median(np.abs(np.exp(p) - np.exp(y)) / np.exp(y)))
    return {"n": int(len(y)), "r2": round(r2, 4), "r2_naive": round(r2n, 4),
            "spearman": round(float(spearmanr(y, p).statistic), 4), "median_abs_error_pct": round(mape * 100, 1)}


# ------------------------------------------------------------------------------------------- training

VOL_COLS = ("atr_pct", "rvol_7", "rvol_30", "range_7")
CORE_COLS = VOL_COLS + ("dist_ma50", "dist_ma200", "mom_7", "mom_28", "dist_high_20", "vol_trend", "taker_imb_7",
                        "r_mom_28", "r_rvol_30", "btc_mom_28", "btc_dist_ma200", "btc_rvol_30", "breadth_50", "mkt_mom_7")
LOG_COLS = set(VOL_COLS) | {"btc_rvol_30"}
# learned from every coin's past charts (app/ml/patterns.py): what followed the 50 most similar charts, and the rates of
# the learned pattern the chart belongs to; out of sample by year in training
PATTERN_COLS = tuple(f"{src}_{k}" for src in ("analog", "pattern")
                     for k in ("direction_7d", "long_tp_first", "short_tp_first", "drop_risk_7d"))
# the technical indicators, ranked within each day (scale-free, so the linear model can use them)
TECH_RANK_COLS = tuple(f"r_{k}" for k in __import__("app.ml.technicals", fromlist=["TECH"]).TECH)
SEEDS = (0, 1, 2)          # LightGBM is averaged over three random seeds: less noise from one lucky or unlucky fit
SELECT_YEARS = (2020, 2021, 2022, 2023, 2024)   # the candidate is chosen on these
HOLDOUT_YEARS = (2025, 2026)        # and reported on these


class Linear:
    """Standardised linear model on a few columns (logs of the volatility columns), missing values = training median."""

    def __init__(self, names: list[str], cols: tuple[str, ...], binary: bool):
        self.idx = [names.index(c) for c in cols]
        self.log = [c in LOG_COLS for c in cols]
        self.binary = binary

    def _x(self, X: np.ndarray) -> np.ndarray:
        Z = X[:, self.idx].astype(np.float64)
        for j, lg in enumerate(self.log):
            if lg:
                Z[:, j] = np.log(np.clip(Z[:, j], 1e-6, None))
        return Z

    def fit(self, X, y):
        from sklearn.linear_model import LinearRegression, LogisticRegression

        Z = self._x(X)
        with np.errstate(all="ignore"):
            med = np.nanmedian(Z, axis=0) if len(Z) else np.zeros(Z.shape[1])
        self.med = np.where(np.isfinite(med), med, 0.0)   # a column with no values yet (e.g. patterns before 2021)
        Z = np.where(np.isnan(Z), self.med, Z)
        self.mu, self.sd = Z.mean(axis=0), Z.std(axis=0) + 1e-9
        Z = (Z - self.mu) / self.sd
        self.m = (LogisticRegression(C=0.1, max_iter=1000) if self.binary else LinearRegression()).fit(Z, y)
        return self

    def _z(self, X):
        Z = self._x(X)
        return (np.where(np.isnan(Z), self.med, Z) - self.mu) / self.sd

    def predict(self, X):
        return self.m.predict_proba(self._z(X))[:, 1] if self.binary else self.m.predict(self._z(X))


class Blend:
    """Average of the linear model and LightGBM (probabilities or log-range predictions)."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def predict(self, X):
        return (self.a.predict(X) + self.b.predict(X)) / 2


class GBM:
    def __init__(self, binary: bool, seed: int = 0):
        import lightgbm as lgb

        self.binary = binary
        prm = {**(CLS_PARAMS if binary else REG_PARAMS), "random_state": seed}
        self.m = lgb.LGBMClassifier(**prm) if binary else lgb.LGBMRegressor(**prm)

    def fit(self, X, y):
        self.m.fit(X, y.astype(int) if self.binary else y)
        return self

    def predict(self, X):
        return self.m.predict_proba(X)[:, 1] if self.binary else self.m.predict(X)


class Bag:
    """LightGBM fitted with several random seeds and averaged (bagging): the same model, less variance."""

    def __init__(self, binary: bool, seeds=SEEDS):
        self.parts = [GBM(binary, s) for s in seeds]
        self.m = self.parts[0].m          # feature importance is read from the first member

    def fit(self, X, y):
        for p in self.parts:
            p.fit(X, y)
        return self

    def predict(self, X):
        return np.mean([p.predict(X) for p in self.parts], axis=0)


def _candidates(names: list[str], binary: bool) -> dict:
    return {"volatility_only": lambda: Linear(names, VOL_COLS, binary),
            "linear": lambda: Linear(names, CORE_COLS, binary),
            **({"linear_patterns": lambda: Linear(names, CORE_COLS + PATTERN_COLS, binary)}
               if all(c in names for c in PATTERN_COLS) else {}),
            **({"linear_tech": lambda: Linear(names, CORE_COLS + TECH_RANK_COLS, binary)}
               if all(c in names for c in TECH_RANK_COLS) else {}),
            "lgbm": lambda: Bag(binary)}


def _score(kind: str, y, p, base=None, naive=None) -> float:
    if kind == "binary":
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y, p))
    return 1 - float(np.sum((y - p) ** 2)) / float(np.sum((y - y.mean()) ** 2))


def train_all() -> dict:
    """Train both tiers, then score every coin."""
    w = load_wide()
    out = {t: train(t, w) for t in TIERS}
    predict(w)
    return out


def train(tier: str = "core", w=None) -> dict:
    from sklearn.isotonic import IsotonicRegression

    t0 = time.time()
    w = w if w is not None else load_wide()
    f, U = features(w, TIERS[tier])
    ys = labels(w)
    X, Y, names, dates, syms = stack(f, U, ys)
    # chart patterns as features: analogs and pattern rates from earlier years only (app/ml/patterns.py)
    from app.ml import patterns as pt

    V, valid, _ = pt.shapes(w, U)
    pf = pt.oos_features(V, valid, {k: Y[k] for k in pt.OUTCOMES}, dates)
    X = np.concatenate([X, np.stack([pf[c] for c in PATTERN_COLS], axis=1)], axis=1)
    names = names + list(PATTERN_COLS)
    naive_move = np.log(f["range_7"].to_numpy(dtype=np.float64)[np.nonzero(U.to_numpy())])
    log.info("forecast_dataset", rows=len(X), features=len(names), start=str(dates.min().date()), end=str(dates.max().date()))
    report = {"tier": tier, "min_adv_usd": TIERS[tier], "trained_at": pd.Timestamp.now("UTC").isoformat(),
              "rows": int(len(X)), "features": names,
              "panel_end": str(dates.max().date()), "test_years": list(TEST_YEARS), "select_years": list(SELECT_YEARS),
              "holdout_years": list(HOLDOUT_YEARS), "purge_days": PURGE_DAYS,
              "plan": {"stop_atr": STOP_ATR, "target_atr": TARGET_ATR, "days": TP_DAYS}, "models": {}}
    final: dict[str, dict] = {}
    oos_out: dict[str, np.ndarray] = {}
    for tgt, spec in TARGETS.items():
        binary = spec["kind"] == "binary"
        y = Y[tgt]
        ok = np.isfinite(y)
        cands = _candidates(names, binary)
        blends = {"blend": "linear", **({"blend_tech": "linear_tech"} if "linear_tech" in cands else {})}
        preds = {c: {} for c in (*cands, *blends)}   # name -> year -> predictions
        every = {c: np.full(len(X), np.nan, dtype=np.float32) for c in preds}  # out-of-sample on every row of a test year
        truth, naive_y, base_y = {}, {}, {}
        for yr in TEST_YEARS:
            start = pd.Timestamp(f"{yr}-01-01")
            in_year = (dates >= start) & (dates < pd.Timestamp(f"{yr + 1}-01-01"))
            tr = ok & (dates < start - pd.Timedelta(days=PURGE_DAYS))
            te = ok & in_year
            if tr.sum() < 20_000 or te.sum() < 1_000:
                continue
            fitted = {c: mk().fit(X[tr], y[tr]) for c, mk in cands.items()}
            for c, m in fitted.items():
                every[c][in_year] = m.predict(X[in_year])
            for b, lin in blends.items():
                every[b][in_year] = (every[lin][in_year] + every["lgbm"][in_year]) / 2
            for c in preds:
                preds[c][yr] = every[c][te]
            truth[yr], naive_y[yr], base_y[yr] = y[te], naive_move[te], float(y[tr].mean())
            log.info("forecast_fold", target=tgt, year=yr, train=int(tr.sum()), test=int(te.sum()))
        years = sorted(truth)
        sel = [yr for yr in years if yr in SELECT_YEARS]
        hold = [yr for yr in years if yr in HOLDOUT_YEARS]
        sel_score = {c: float(np.mean([_score(spec["kind"], truth[yr], preds[c][yr]) for yr in sel])) for c in preds}
        chosen = max(sel_score, key=sel_score.get)
        P = np.concatenate([preds[chosen][yr] for yr in years])
        Yo = np.concatenate([truth[yr] for yr in years])
        Ph = np.concatenate([preds[chosen][yr] for yr in hold])
        Yh = np.concatenate([truth[yr] for yr in hold])
        Vh = np.concatenate([preds["volatility_only"][yr] for yr in hold])
        iso = None
        if binary:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(P, Yo)
            base = base_y[years[0]]
            pooled = {**_cls_metrics(Yo, iso.predict(P), base), "auc": round(_score("binary", Yo, P), 4)}
            holdout = {**_cls_metrics(Yh, iso.predict(Ph), base_y[hold[-1]]), "auc": round(_score("binary", Yh, Ph), 4)}
            yearly = [{"year": yr, **_cls_metrics(truth[yr], preds[chosen][yr], base_y[yr])} for yr in years]
            vol_hold = round(_score("binary", Yh, Vh), 4)
            beats = holdout["auc"] > vol_hold + 0.005
        else:
            Nh = np.concatenate([naive_y[yr] for yr in hold])
            pooled = _reg_metrics(Yo, P, np.concatenate([naive_y[yr] for yr in years]))
            holdout = _reg_metrics(Yh, Ph, Nh)
            yearly = [{"year": yr, **_reg_metrics(truth[yr], preds[chosen][yr], naive_y[yr])} for yr in years]
            vol_hold = round(_score("regression", Yh, Vh), 4)
            beats = holdout["r2"] > vol_hold + 0.005
        status, why = _verdict2(spec["kind"], holdout, yearly, beats, chosen)
        # the served model: the chosen candidate, refitted on every labelled row
        if chosen in blends:
            model = Blend(cands[blends[chosen]]().fit(X[ok], y[ok]), cands["lgbm"]().fit(X[ok], y[ok]))
        else:
            model = cands[chosen]().fit(X[ok], y[ok])
        top = []
        gbm = model.b if chosen in blends else model if chosen == "lgbm" else None
        if gbm is not None:
            imp = sorted(zip(names, gbm.m.booster_.feature_importance("gain")), key=lambda x: -x[1])[:8]
            tot = sum(v for _, v in imp) or 1
            top = [{"feature": k, "share": round(v / tot, 3)} for k, v in imp]
        final[tgt] = {"model": model, "iso": iso, "status": status, "chosen": chosen}
        oos_out[tgt] = every[chosen]
        report["models"][tgt] = {**spec, "status": status, "why": why, "chosen": chosen,
                                 "selection_scores": {k: round(v, 4) for k, v in sel_score.items()},
                                 "holdout": holdout, "oos": pooled, "volatility_only_holdout": vol_hold,
                                 "beats_volatility_only": bool(beats), "yearly": yearly, "top_features": top}
        log.info("forecast_model", target=tgt, status=status, chosen=chosen, why=why)
    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump({"names": names, "models": final, "trained_at": report["trained_at"]}, _artifact_path(tier))
    # every test-year row's out-of-sample prediction (raw score of the chosen model) + its pattern features, for the
    # signal backtest in app/ml/signal_plan.py
    keep = np.isfinite(oos_out["long_tp_first"])
    np.savez_compressed(_oos_path(tier), date=dates[keep].to_numpy().astype("datetime64[D]"),
                        symbol=np.asarray(syms[keep], dtype=str),
                        **{f"pred_{k}": v[keep] for k, v in oos_out.items()},
                        **{c: X[keep, names.index(c)] for c in PATTERN_COLS})
    _cache["art"].pop(tier, None)
    report["seconds"] = round(time.time() - t0, 1)
    _report_path(tier).write_text(json.dumps(report, indent=1, default=str))
    return report


def _verdict2(kind: str, h: dict, yearly: list[dict], beats: bool, chosen: str) -> tuple[str, str]:
    """Judged on the 2025-2026 holdout, which played no part in choosing the model."""
    edge = "" if beats or chosen == "volatility_only" else "; no better than the volatility-only formula"
    if kind == "binary":
        auc, bss = h.get("auc") or 0.5, h.get("brier_skill") or 0
        worst = min((y.get("auc") or 0.5) for y in yearly) if yearly else 0.5
        if auc >= 0.55 and bss > 0 and worst > 0.5:
            return "accepted", f"AUC {auc:.3f} on 2025-26 (unseen), above the base rate in every test year{edge}"
        if auc >= 0.52 and bss >= -0.002:
            return "weak", f"AUC {auc:.3f} on 2025-26: a small edge over a coin flip; shown as weak, not used for decisions"
        return "rejected", f"AUC {auc:.3f} on 2025-26: no better than the base rate"
    r2 = h["r2"]
    if r2 > 0.1:
        return "accepted", f"explains {r2:.0%} of the variation in the 7-day range on 2025-26 (unseen){edge}"
    if r2 > 0:
        return "weak", f"R² {r2:.2f} on 2025-26"
    return "rejected", f"R² {r2:.2f} on 2025-26"


# ------------------------------------------------------------------------------------------- serving

_cache: dict = {"t": 0.0, "data": None, "art": {}}


def _artifact(tier: str) -> dict | None:
    if tier not in _cache["art"] and _artifact_path(tier).exists():
        _cache["art"][tier] = joblib.load(_artifact_path(tier))
    return _cache["art"].get(tier)


def _score_tier(tier: str, w, f, U, skip: set[str]) -> dict[str, dict]:
    """Score each coin of this tier's universe on its latest universe day within the last 3 days."""
    art = _artifact(tier)
    if art is None:
        return {}
    mask = U.copy()
    mask.loc[:] = False
    seen: set[str] = set(skip)
    for d in U.index[-3:][::-1]:
        fresh = U.loc[d] & ~U.columns.isin(list(seen))
        mask.loc[d] = fresh
        seen |= set(U.columns[fresh.to_numpy()])
    X, _, names, rdates, syms = stack(f, mask)
    if not len(X):
        return {}
    if any(c in art["names"] for c in PATTERN_COLS):
        from app.ml import patterns as pt

        pf = pt.live_features(w, mask)
        X = np.concatenate([X, np.stack([pf[c] for c in PATTERN_COLS], axis=1)], axis=1)
        names = names + list(PATTERN_COLS)
    X = X[:, [names.index(n) for n in art["names"]]] if names != art["names"] else X
    atr_all, close_all = _atr(w), w.close
    out: dict[str, np.ndarray] = {}
    raw: dict[str, np.ndarray] = {}
    for tgt, m in art["models"].items():
        p = m["model"].predict(X)
        out[tgt] = (m["iso"].predict(p) if m["iso"] is not None else p) if TARGETS[tgt]["kind"] == "binary" else np.exp(p)
        raw[tgt] = p
    rows = {}
    for i, s in enumerate(syms):
        r = {tgt: round(float(v[i]) * (100 if tgt == "move_7d" else 1), 4) for tgt, v in out.items()}
        a, cp = float(atr_all.at[rdates[i], s]), float(close_all.at[rdates[i], s])
        r["raw"] = {tgt: round(float(v[i]), 5) for tgt, v in raw.items()}
        r |= {"date": str(rdates[i].date()), "tier": tier, "close": cp,
              "atr_pct": round(a / cp * 100, 3) if np.isfinite(a) and cp else None}
        rows[s] = r
    return rows


def predict(w=None) -> dict:
    """Forecasts for every covered coin, saved to forecast_today.json: core models for liquid coins, wide for the rest."""
    if _artifact("core") is None and _artifact("wide") is None:
        return {"error": "no trained models yet: run `python -m app.ml.forecast train`"}
    w = w if w is not None else load_wide()
    rows: dict[str, dict] = {}
    for tier in TIERS:  # core first: a liquid coin keeps the more accurate model
        f, U = features(w, TIERS[tier])
        rows |= _score_tier(tier, w, f, U, set(rows))
    btc = w.close["BTCUSDT"].dropna()
    ma = btc.rolling(50, min_periods=50).mean()
    market = {"btc_close": float(btc.iloc[-1]), "btc_ma50": round(float(ma.iloc[-1]), 2), "date": str(btc.index[-1].date()),
              "trend": "up" if btc.iloc[-1] > ma.iloc[-1] else "down"}
    data = {"as_of": str(w.close.index[-1].date()), "made_at": pd.Timestamp.now("UTC").isoformat(), "coins": len(rows),
            "market": market,
            "by_tier": {t: sum(r["tier"] == t for r in rows.values()) for t in TIERS},
            "plan": {"stop_atr": STOP_ATR, "target_atr": TARGET_ATR, "days": TP_DAYS}, "rows": rows}
    TODAY.write_text(json.dumps(data))
    _cache.update(t=time.time(), data=data)
    return {k: data[k] for k in ("as_of", "coins", "by_tier")}


def report(tier: str = "core") -> dict | None:
    p = _report_path(tier)
    return json.loads(p.read_text()) if p.exists() else None


def today() -> dict | None:
    if _cache["data"] is None and TODAY.exists():
        _cache["data"] = json.loads(TODAY.read_text())
    return _cache["data"]


def for_symbol(symbol: str) -> dict:
    """One coin's forecasts with each model's status and measured accuracy, in plain words."""
    td = today()
    row = (td or {}).get("rows", {}).get(symbol)
    tier = (row or {}).get("tier", "core")
    rep = report(tier)
    if not rep or not td:
        return {"available": False, "note": "The forecast models have not been trained yet."}
    models = []
    for tgt, m in rep["models"].items():
        h = m.get("holdout") or m["oos"]
        acc = (f"on 2025-26 (unseen): AUC {h.get('auc')}; its top tenth of forecasts came true "
               f"{round((h.get('top_decile_rate') or 0) * 100)}% of the time, its bottom tenth "
               f"{round((h.get('bottom_decile_rate') or 0) * 100)}%, against {round((h.get('base_rate') or 0) * 100)}% "
               f"on average" if m["kind"] == "binary" else
               f"on 2025-26 (unseen): R² {h['r2']}, median error {h['median_abs_error_pct']}% of the range")
        acc += ("" if m.get("beats_volatility_only", True) or m.get("chosen") == "volatility_only"
                else " — most of this comes from volatility alone")
        models.append({"target": tgt, "label": m["label"], "what": m["what"], "status": m["status"], "why": m["why"],
                       "accuracy": acc, "base_rate": h.get("base_rate"), "chosen": m.get("chosen"),
                       "beats_volatility_only": m.get("beats_volatility_only"),
                       "value": row.get(tgt) if row else None, "kind": m["kind"]})
    return {"available": True, "symbol": symbol, "covered": row is not None, "as_of": td["as_of"], "tier": tier,
            "tier_note": None if tier == "core" else "A thinner coin ($1-5M a day): scored by the wide models, which "
                                                     "are less accurate than those for liquid coins.",
            "trained_at": rep["trained_at"], "plan": td["plan"], "atr_pct": (row or {}).get("atr_pct"),
            "models": models,
            "note": None if row else "Not covered: the models only score coins with 90+ days of history and $1M+ "
                                     "average daily volume."}


def refresh_if_stale() -> bool:
    """Re-score when the panel has a newer day than today's forecasts (cheap); retrain both tiers at 30+ days old."""
    rep, td = report("core"), today()
    if rep is None:
        return False
    from app.ml import unified as _un
    from app.quant.lab.data import load_panel
    from app.quant.lab.live import _through

    # judge "new day" by BTC: the archive lands coin by coin, so a partial day must not count as done
    last = _through(load_panel())
    old = (pd.Timestamp.now("UTC") - pd.Timestamp(rep["trained_at"])).days >= 30
    seen = ((_un.today() or {}).get("market") or {}).get("date") or ""
    if td and str(td["as_of"])[:10] >= last and seen >= last and not old:
        return False
    from app.ml import history
    from app.ml import patterns as pt

    history.refresh()   # the full-history panel picks up the lab's new day (no downloads)
    w = load_wide()
    if old:      # patterns first: the forecast models use them as features
        pt.train()
        train_all()
        from app.ml import signal_plan  # the daily pick is re-tested on the new out-of-sample predictions

        signal_plan.backtest("core")
        from app.ml import unified  # the unified engine relearns on the new data, then scores today

        unified.train_all()
    else:
        pt.predict(w)
        predict(w)
        from app.ml import unified

        if unified.ART.exists():
            unified.score_today(w=w)
    return True


if __name__ == "__main__":
    # run through the importable module so the saved model classes load in the backend (not as __main__.Linear)
    from app.ml import forecast as _fc

    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    res = _fc.train_all() if cmd == "train" else _fc.predict()
    if cmd == "train":
        for tier, rep in res.items():
            print(f"--- {tier} (coins with ${rep['min_adv_usd'] / 1e6:g}M+ a day), {rep['rows']} rows, {rep['seconds']}s")
            for k, m in rep["models"].items():
                print(f"{k:16s} {m['status']:9s} {m['chosen']:16s} {m['why']}")
    else:
        print(res)
