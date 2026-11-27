"""Does a model upgrade really help? A fair test before anything is switched on.

Same walk-forward as app/ml/forecast.py (train on every year before, 15-day purge, test one calendar year), same rows,
same targets. Variants:
  linear / lgbm / blend       today's models
  *_tech                      + CTREND-style technical indicators (app/ml/technicals.py)
  bag_tech                    3 LightGBM models with different random seeds, averaged (less noise)
  blend_bag_tech              that bag averaged with the linear model
An upgrade is ADOPTED only if it wins on 2020-2024 (select years); 2025-2026 is reported as it comes out.

Run: .venv/bin/python -m app.ml.upgrade_eval      (≈ 30-60 minutes; writes data/models/upgrade_eval.json)
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from app.logging import log
from app.ml import forecast as fc
from app.ml.technicals import TECH, technicals

OUT = fc.MODELS / "upgrade_eval.json"
TARGETS = ("long_tp_first", "short_tp_first", "direction_7d", "outperform_7d", "drop_risk_7d")
SEEDS = (0, 1, 2)


class _LGB:
    def __init__(self, seed: int, cols: list[int] | None = None):
        import lightgbm as lgb

        self.cols = cols
        self.m = lgb.LGBMClassifier(**{**fc.CLS_PARAMS, "random_state": seed})

    def fit(self, X, y):
        self.m.fit(X if self.cols is None else X[:, self.cols], y.astype(int))
        return self

    def predict(self, X):
        return self.m.predict_proba(X if self.cols is None else X[:, self.cols])[:, 1]


def dataset(tier: str = "core", w=None):
    from app.ml import patterns as pt

    w = w if w is not None else fc.load_wide()
    f, U = fc.features(w, fc.TIERS[tier])
    base_names = sorted(f)
    f |= technicals(w, U)
    ys = fc.labels(w)
    X, Y, names, dates, syms = fc.stack(f, U, ys)
    V, valid, _ = pt.shapes(w, U)
    pf = pt.oos_features(V, valid, {k: Y[k] for k in pt.OUTCOMES}, dates)
    X = np.concatenate([X, np.stack([pf[c] for c in fc.PATTERN_COLS], axis=1)], axis=1)
    names = names + list(fc.PATTERN_COLS)
    return X, Y, names, base_names, dates


def run(tier: str = "core") -> dict:
    t0 = time.time()
    X, Y, names, base_names, dates = dataset(tier)
    now_cols = [names.index(n) for n in base_names + list(fc.PATTERN_COLS)]
    tech_rank = [f"r_{k}" for k in TECH]
    log.info("upgrade_eval_data", rows=len(X), features_now=len(now_cols), features_all=len(names))
    res: dict = {"tier": tier, "rows": int(len(X)), "features_now": len(now_cols), "features_with_tech": len(names),
                 "targets": {}}
    for tgt in TARGETS:
        y = Y[tgt]
        ok = np.isfinite(y)
        preds: dict[str, dict[int, np.ndarray]] = {}
        truth: dict[int, np.ndarray] = {}
        for yr in fc.TEST_YEARS:
            start = pd.Timestamp(f"{yr}-01-01")
            te = ok & (dates >= start) & (dates < pd.Timestamp(f"{yr + 1}-01-01"))
            tr = ok & (dates < start - pd.Timedelta(days=fc.PURGE_DAYS))
            if tr.sum() < 20_000 or te.sum() < 1_000:
                continue
            Xtr, ytr, Xte = X[tr], y[tr], X[te]
            p = {
                "linear": fc.Linear(names, fc.CORE_COLS, True).fit(Xtr, ytr).predict(Xte),
                "linear_tech": fc.Linear(names, fc.CORE_COLS + tuple(tech_rank), True).fit(Xtr, ytr).predict(Xte),
                "lgbm": _LGB(0, now_cols).fit(Xtr, ytr).predict(Xte),
            }
            bag = [_LGB(s).fit(Xtr, ytr).predict(Xte) for s in SEEDS]
            p["lgbm_tech"] = bag[0]
            p["bag_tech"] = np.mean(bag, axis=0)
            p["blend"] = (p["linear"] + p["lgbm"]) / 2
            p["blend_tech"] = (p["linear_tech"] + p["lgbm_tech"]) / 2
            p["blend_bag_tech"] = (p["linear_tech"] + p["bag_tech"]) / 2
            for k, v in p.items():
                preds.setdefault(k, {})[yr] = v
            truth[yr] = y[te]
            log.info("upgrade_eval_fold", target=tgt, year=yr, seconds=round(time.time() - t0))
        years = sorted(truth)
        sel = [yr for yr in years if yr in fc.SELECT_YEARS]
        hold = [yr for yr in years if yr in fc.HOLDOUT_YEARS]
        Yh = np.concatenate([truth[yr] for yr in hold])
        rows = {}
        for k, pv in preds.items():
            rows[k] = {"select_auc": round(float(np.mean([fc._score("binary", truth[yr], pv[yr]) for yr in sel])), 4),
                       "holdout_auc": round(fc._score("binary", Yh, np.concatenate([pv[yr] for yr in hold])), 4),
                       "by_year": {yr: round(fc._score("binary", truth[yr], pv[yr]), 4) for yr in years}}
        best = max(rows, key=lambda k: rows[k]["select_auc"])
        today = max(("linear", "lgbm", "blend"), key=lambda k: rows[k]["select_auc"])
        res["targets"][tgt] = {"variants": rows, "best_on_select": best, "today_on_select": today,
                               "gain_select": round(rows[best]["select_auc"] - rows[today]["select_auc"], 4),
                               "gain_holdout": round(rows[best]["holdout_auc"] - rows[today]["holdout_auc"], 4)}
        OUT.write_text(json.dumps(res, indent=1))
    res["seconds"] = round(time.time() - t0)
    OUT.write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = run()
    for t, v in r["targets"].items():
        print(f"{t:15s} today {v['today_on_select']:8s} -> best {v['best_on_select']:15s} "
              f"select {v['gain_select']:+.4f}  holdout {v['gain_holdout']:+.4f}")
        for k, x in v["variants"].items():
            print(f"    {k:15s} select {x['select_auc']:.4f}  holdout {x['holdout_auc']:.4f}  {x['by_year']}")
