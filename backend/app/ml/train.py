"""Model research & registry. Usage: uv run python -m app.ml.train --tf 1h --horizon 24 --days 540

Acceptance policy (documented in docs/ML_RESEARCH.md): a model is ACCEPTED only if, on purged walk-forward
out-of-sample folds, it
  1. beats the class-prior baseline log loss on >= 70% of folds,
  2. has mean AUC >= 0.52 and beats the logistic-regression baseline's mean AUC,
  3. a thresholded long/short rule on its calibrated probabilities has positive mean net return after
     costs with a bootstrap 95% CI lower bound > 0.
Otherwise it is registered as REJECTED (kept for audit) and never used by the signal engine.
"""
import argparse
import asyncio
import json
import pickle
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.config import get_settings
from app.data.timeframes import tf_ms
from app.ml.cv import purged_walk_forward
from app.ml.features import FEATURE_VERSION, build_features, triple_barrier

LGB_PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=200, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, verbose=-1)


def make_dataset(frames: dict[str, pd.DataFrame], horizon: int, btc_key: str = "BTCUSDT") -> pd.DataFrame:
    parts = []
    btc = frames.get(btc_key)
    for sym, df in frames.items():
        f = build_features(df, btc if sym != btc_key else None)
        lab = triple_barrier(df, horizon)
        d = pd.concat([f, lab], axis=1)
        d["symbol"] = sym
        d["time"] = df["open_time"].to_numpy()
        d["t1_time"] = df["open_time"].to_numpy()[np.clip(np.nan_to_num(lab["t1"].to_numpy(), nan=0).astype(int),
                                                         0, len(df) - 1)]
        parts.append(d.iloc[250:])  # warm-up
    return pd.concat(parts, ignore_index=True)


def economic_eval(p: np.ndarray, fwd: np.ndarray, cost: float, thr: float = 0.55) -> dict:
    pos = np.where(p > thr, 1, np.where(p < 1 - thr, -1, 0))
    m = pos != 0
    r = pos[m] * fwd[m] - cost
    if m.sum() < 20:
        return {"n": int(m.sum()), "mean": None, "ci_low": None}
    rng = np.random.default_rng(0)
    boots = rng.choice(r, size=(2000, len(r))).mean(axis=1)
    return {"n": int(m.sum()), "mean": float(r.mean()), "ci_low": float(np.quantile(boots, 0.025)),
            "ci_high": float(np.quantile(boots, 0.975)), "hit_rate": float((r > 0).mean())}


def research(ds: pd.DataFrame, horizon: int, cost: float, n_splits: int = 5) -> dict:
    feats = [c for c in ds.columns if c not in ("label", "t1", "fwd_ret", "barrier_sigma", "symbol", "time", "t1_time")]
    d = ds.dropna(subset=["label"]).dropna(subset=feats, how="any").sort_values("time").reset_index(drop=True)
    X, y = d[feats].to_numpy(), d["label"].to_numpy().astype(int)
    folds = purged_walk_forward(d["time"].to_numpy(), d["t1_time"].to_numpy(), n_splits, embargo=horizon)
    rows = []
    oos_p, oos_idx = [], []
    for k, (tr, te) in enumerate(folds):
        if len(tr) < 1000 or len(te) < 200:
            continue
        cal_cut = int(len(tr) * 0.8)  # last 20% of training (time-ordered) for calibration
        tr_fit, tr_cal = tr[:cal_cut], tr[cal_cut:]
        prior = y[tr].mean()
        lr = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500)).fit(X[tr], y[tr])
        gbm = lgb.LGBMClassifier(**LGB_PARAMS).fit(X[tr_fit], y[tr_fit])
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(
            gbm.predict_proba(X[tr_cal])[:, 1], y[tr_cal])
        p_g = iso.predict(gbm.predict_proba(X[te])[:, 1])
        p_l = lr.predict_proba(X[te])[:, 1]
        p_b = np.full(len(te), prior)
        yt = y[te]
        row = {"fold": k, "n_train": len(tr), "n_test": len(te),
               "test_start": int(d["time"].iloc[te[0]]), "test_end": int(d["time"].iloc[te[-1]]),
               "base_rate": float(yt.mean()),
               "logloss_prior": float(log_loss(yt, p_b, labels=[0, 1])),
               "logloss_lr": float(log_loss(yt, p_l, labels=[0, 1])),
               "logloss_gbm": float(log_loss(yt, p_g, labels=[0, 1])),
               "auc_lr": float(roc_auc_score(yt, p_l)) if len(set(yt)) > 1 else None,
               "auc_gbm": float(roc_auc_score(yt, p_g)) if len(set(yt)) > 1 else None,
               "brier_gbm": float(brier_score_loss(yt, p_g)), "brier_prior": float(brier_score_loss(yt, p_b))}
        rows.append(row)
        oos_p.append(p_g)
        oos_idx.append(te)
    if not rows:
        return {"status": "rejected", "reason": "insufficient data for walk-forward", "folds": []}
    p_all = np.concatenate(oos_p)
    idx = np.concatenate(oos_idx)
    econ = economic_eval(p_all, d["fwd_ret"].to_numpy()[idx], cost)
    # reliability curve (calibration dashboard)
    bins = np.linspace(0, 1, 11)
    yo = y[idx]
    rel = []
    for a, b in zip(bins[:-1], bins[1:]):
        m = (p_all >= a) & (p_all < b)
        if m.sum() >= 30:
            rel.append({"p_mean": float(p_all[m].mean()), "freq": float(yo[m].mean()), "n": int(m.sum())})
    beats_prior = np.mean([r["logloss_gbm"] < r["logloss_prior"] for r in rows])
    auc_g = np.mean([r["auc_gbm"] for r in rows if r["auc_gbm"] is not None])
    auc_l = np.mean([r["auc_lr"] for r in rows if r["auc_lr"] is not None])
    criteria = {
        "beats_prior_logloss_>=70pct_folds": bool(beats_prior >= 0.7),
        "mean_auc_>=0.52": bool(auc_g >= 0.52),
        "beats_logistic_baseline_auc": bool(auc_g > auc_l),
        "net_of_cost_ci_low_>0": bool(econ.get("ci_low") is not None and econ["ci_low"] > 0),
    }
    accepted = all(criteria.values())
    # final model on all data (for inference) — only meaningful if accepted
    cut = int(len(d) * 0.85)
    final = lgb.LGBMClassifier(**LGB_PARAMS).fit(X[:cut], y[:cut])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(final.predict_proba(X[cut:])[:, 1], y[cut:])
    imp = sorted(zip(feats, final.booster_.feature_importance("gain")), key=lambda x: -x[1])
    train_dist = {f: np.quantile(d[f].to_numpy(), np.linspace(0, 1, 11)).tolist() for f in feats}
    return {"status": "accepted" if accepted else "rejected", "criteria": criteria, "folds": rows,
            "summary": {"mean_auc_gbm": float(auc_g), "mean_auc_lr": float(auc_l), "beats_prior_frac": float(beats_prior),
                        "n_samples": len(d), "label_balance": float(y.mean())},
            "economic": econ, "reliability": rel, "features": feats,
            "feature_importance": [{"feature": f, "gain": float(g)} for f, g in imp[:25]],
            "_model": final, "_calibrator": iso, "_train_dist": train_dist}


async def run(tf: str, horizon: int, days: int, symbols: list[str] | None) -> dict:
    from app.data.binance import BinanceClient
    from app.data.ingest import backfill
    from app.data.store import load_candles
    from app.db import create_all, init_engine, session_factory
    from app.models import ModelRecord

    s = get_settings()
    init_engine()
    await create_all()
    client = BinanceClient()
    syms = symbols or [b + s.quote_asset for b in s.candidate_universe if b != "TON"]
    bars = int(days * 86_400_000 / tf_ms(tf))
    frames = {}
    for sym in syms:
        try:
            await backfill(client, sym, tf, bars)
        except Exception as e:  # noqa: BLE001
            print("backfill failed", sym, e)
        async with session_factory()() as db:
            df = await load_candles(db, sym, tf, limit=bars)
        if len(df) > 1000:
            frames[sym] = df
    await client.close()
    print(f"training on {len(frames)} symbols, {sum(len(v) for v in frames.values())} rows")
    ds = make_dataset(frames, horizon)
    cost = 2 * (s.taker_fee + s.slippage_bps / 1e4)
    res = research(ds, horizon, cost)
    version = time.strftime("%Y%m%d%H%M%S")
    name = f"lgbm_tb_{tf}_h{horizon}"
    path = Path(s.model_dir) / f"{name}_{version}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump({"model": res.pop("_model"), "calibrator": res.pop("_calibrator"), "features": res["features"],
                     "train_dist": res.pop("_train_dist"), "tf": tf, "horizon": horizon,
                     "feature_version": FEATURE_VERSION}, fh)
    async with session_factory()() as db:
        db.add(ModelRecord(name=name, version=version, target=f"triple_barrier_k1.5_h{horizon}",
                           status=res["status"], features=res["features"],
                           params={**LGB_PARAMS, "tf": tf, "horizon": horizon, "days": days, "symbols": list(frames),
                                   "feature_version": FEATURE_VERSION, "cost_round_trip": cost},
                           metrics={k: v for k, v in res.items() if k not in ("features",)}, artifact_path=str(path)))
        await db.commit()
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--days", type=int, default=540)
    ap.add_argument("--symbols", nargs="*")
    a = ap.parse_args()
    res = asyncio.run(run(a.tf, a.horizon, a.days, a.symbols))
    print(json.dumps({k: res[k] for k in ("status", "criteria", "summary", "economic")}, indent=2))


if __name__ == "__main__":
    main()
