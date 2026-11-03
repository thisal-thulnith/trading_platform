"""Load the latest ACCEPTED model and produce calibrated probabilities + per-feature contributions.
Rejected models are never served."""
import pickle
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db import session_factory
from app.ml.features import build_features
from app.models import ModelRecord


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    with open(path, "rb") as fh:
        return pickle.load(fh)


async def accepted_model(tf: str) -> ModelRecord | None:
    async with session_factory()() as db:
        q = (select(ModelRecord).where(ModelRecord.status == "accepted", ModelRecord.name.like(f"%_{tf}_%"))
             .order_by(ModelRecord.created_at.desc()).limit(1))
        return (await db.execute(q)).scalar_one_or_none()


def psi(train_q: list[float], live: np.ndarray) -> float:
    """Population stability index of live values against training deciles."""
    edges = np.unique(np.array(train_q))
    if len(edges) < 3 or len(live) < 20:
        return 0.0
    exp = np.full(len(edges) - 1, 1 / (len(edges) - 1))
    act = np.histogram(np.clip(live, edges[0], edges[-1]), edges)[0] / len(live)
    act = np.clip(act, 1e-4, None)
    return float(((act - exp) * np.log(act / exp)).sum())


async def predict(symbol: str, tf: str, df: pd.DataFrame, btc: pd.DataFrame | None) -> dict | None:
    rec = await accepted_model(tf)
    if rec is None or not rec.artifact_path:
        return None
    art = _load(rec.artifact_path)
    f = build_features(df, btc if symbol != "BTCUSDT" else None)
    feats = art["features"]
    if any(c not in f for c in feats):
        return None
    row = f[feats].iloc[[-1]]
    if row.isna().any(axis=1).iloc[0]:
        return None
    raw = float(art["model"].predict_proba(row.to_numpy())[:, 1][0])
    p = float(art["calibrator"].predict([raw])[0])
    contrib = art["model"].booster_.predict(row.to_numpy(), pred_contrib=True)[0]  # TreeSHAP; last = bias
    top = sorted(zip(feats, contrib[:-1]), key=lambda x: -abs(x[1]))[:8]
    drift = {c: psi(art["train_dist"][c], f[c].tail(200).dropna().to_numpy()) for c in feats[:40]}
    worst = max(drift.values()) if drift else 0
    return {"accepted": True, "model": rec.name, "version": rec.version, "horizon": f"{art['horizon']} bars",
            "p_up": p, "p_down": 1 - p, "raw_score": raw,
            "target": rec.target, "contributions": [{"feature": k, "shap": float(v)} for k, v in top],
            "contribution_note": "SHAP-style contributions explain the model, not market causality.",
            "drift_psi_max": worst, "drift_alert": worst > 0.25}
