"""Meta-labeling research: can a second model learn WHEN the unified engine's picks fail, and skip or shrink them?

Meta-labeling (Lopez de Prado, Advances in Financial Machine Learning, ch. 3): the primary model sets the trade and the
side; a secondary model, trained only on the primary model's own past trades, predicts the chance that this trade makes
money, and the size follows that chance. It cannot invent an edge the primary model doesn't have; it can only filter.

Fair test (the platform's usual rules):
- data: the unified engine's out-of-sample picks (unified_oos.npz + its chosen rule), one trade a day, 2020-2026
- features: only what is known on the pick day (the pick's scores, the plan, volatility, BTC trend, how crowded the
  top of the ranking is, and the engine's own recent record from trades that had already CLOSED by that day)
- walk-forward by year: the meta model for year Y is trained on picks before Y, with a 15-day purge (a 14-day trade
  started in late December ends in January), so nothing it learns from overlaps the year it predicts
- choice: the model, threshold or sizing rule is chosen on 2022-24 only (the picks start in 2021, which only trains);
  2025-26 is reported as it came out, next to the plain engine, with a weekly-bootstrap range of the difference
- policies tried are all listed (multiple-testing honesty)

Run: .venv/bin/python -m app.ml.meta_engine     (writes data/models/meta_engine_report.json; ~1 minute)
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from app.ml import forecast as fc
from app.ml import style as sty

REPORT = fc.MODELS / "meta_engine_report.json"
PURGE_DAYS = 15
HOLD = sty.HOLD
FIRST_META_YEAR = 2022   # the engine's out-of-sample picks start in 2021: the first meta model learns from 2021
CHOOSE_YEARS = (2022, 2023, 2024)
MIN_KEEP = 0.5          # a filter must keep at least half the trades on 2022-24 (else it is a different strategy)
FEATURES = ["score", "pred", "gap", "second", "day_best_median", "dispersion", "is_long", "stop_atr", "target_r",
            "atr_pct", "btc_up", "against_trend", "other_side", "recent_avg_r", "recent_win"]


def picks_with_features() -> pd.DataFrame:
    """The engine's past picks (its chosen rule), one row per pick, with the features known on that day."""
    from app.ml import unified as un

    rep = un.report()
    z = np.load(un.OOS, allow_pickle=True)
    dates = pd.to_datetime(z["date"])
    P, R, atr = z["P"].astype(np.float64), z["R"].astype(np.float64), z["atr_pct"].astype(np.float64)
    rule = rep["rule"]
    S = un._scores(P, dates, rule["score"]).astype(np.float64)
    S[~(atr <= un.MAX_ATR_PCT)] = np.nan
    best_s = np.nanmax(np.where(np.isfinite(S), S, -np.inf), axis=1)
    best_s[~np.isfinite(best_s)] = np.nan
    day = pd.Series(best_s).groupby(np.asarray(dates))
    second = day.transform(lambda s: s.nlargest(2).iloc[-1] if s.notna().sum() >= 2 else np.nan).to_numpy()
    disp = day.transform("std").to_numpy()
    best_p = np.nanmax(P, axis=1)
    day_med = pd.Series(best_p).groupby(np.asarray(dates)).transform("median").to_numpy()

    pk = un.decide(P, R, dates, atr, rule)
    pk = pk[np.isfinite(pk["real"])].copy()
    row, plan = pk["row"].to_numpy(), pk["plan"].to_numpy()
    sides = np.array([sty.PLANS[j][0] for j in plan])
    long_idx = [i for i, p in enumerate(sty.PIDS) if p.startswith("LONG")]
    short_idx = [i for i, p in enumerate(sty.PIDS) if p.startswith("SHORT")]
    other = np.where(sides == "LONG", np.nanmax(P[row][:, short_idx], axis=1), np.nanmax(P[row][:, long_idx], axis=1))
    btc_up = z["trend"][row].astype(float)
    is_long = (sides == "LONG").astype(float)
    pk = pk.assign(
        symbol=z["symbol"].astype(str)[row], side=sides, second=second[row], gap=pk["score"].to_numpy() - second[row],
        dispersion=disp[row], day_best_median=day_med[row], is_long=is_long,
        stop_atr=[sty.PLANS[j][1] for j in plan], target_r=[sty.PLANS[j][2] for j in plan], atr_pct=atr[row],
        btc_up=btc_up, against_trend=np.where(is_long == 1, 1 - btc_up, btc_up), other_side=other,
        year=pd.DatetimeIndex(pk["date"]).year)
    # the engine's own recent record, from trades already closed on the pick day (entry + HOLD days <= pick day)
    pk = pk.sort_values("date").reset_index(drop=True)
    closed_at = pk["date"] + pd.Timedelta(days=HOLD)
    rec_avg, rec_win = [], []
    for d in pk["date"]:
        done = pk.loc[closed_at <= d, "real"].tail(30)
        rec_avg.append(done.mean() if len(done) >= 10 else np.nan)
        rec_win.append((done > 0).mean() if len(done) >= 10 else np.nan)
    return pk.assign(recent_avg_r=rec_avg, recent_win=rec_win)


def _models():
    from lightgbm import LGBMClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "logistic": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.3, max_iter=500)),
        "lgbm_small": lambda: LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=4, max_depth=2,
                                             min_child_samples=60, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                             reg_lambda=5.0, verbose=-1, random_state=7),
    }


def walk_forward(pk: pd.DataFrame) -> pd.DataFrame:
    """Out-of-sample meta probabilities, year by year, with a purge before each test year."""
    X, y = pk[FEATURES].to_numpy(float), (pk["real"].to_numpy() > 0).astype(int)
    out = pk.copy()
    for name, make in _models().items():
        p = np.full(len(pk), np.nan)
        for Y in range(FIRST_META_YEAR, int(pk["year"].max()) + 1):
            test = (pk["year"] == Y).to_numpy()
            cut = pd.Timestamp(f"{Y}-01-01") - pd.Timedelta(days=PURGE_DAYS)
            train = (pk["date"] < cut).to_numpy()
            if train.sum() < 200 or not test.any():
                continue
            m = make()
            m.fit(X[train], y[train])
            p[test] = m.predict_proba(X[test])[:, 1]
        out[f"p_{name}"] = p
    return out


def _policies() -> list[dict]:
    pol = [{"model": None, "kind": "all"}]
    for m in _models():
        pol += [{"model": m, "kind": "skip_below", "q": q} for q in (0.30, 0.34, 0.38, 0.42)]
        pol += [{"model": m, "kind": "size_by_p"}]
    return pol


def _weights(df: pd.DataFrame, pol: dict, base_win: float) -> np.ndarray:
    if pol["kind"] == "all":
        return np.ones(len(df))
    p = df[f"p_{pol['model']}"].to_numpy()
    if pol["kind"] == "skip_below":
        return (p >= pol["q"]).astype(float)
    return np.clip(p / base_win, 0.0, 2.0)      # size by the chance of profit, relative to the usual chance, capped at 2x


def _book(df: pd.DataFrame, w: np.ndarray, risk_per_day: float = 0.005) -> dict:
    """Trades, R per trade taken, R per day of the period (what the account feels) and a daily-compounded account."""
    r = df["real"].to_numpy() * w
    taken = w > 0
    daily = pd.Series(r * risk_per_day, index=df["date"]).groupby(level=0).sum()
    idx = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    daily = daily.reindex(idx, fill_value=0.0)
    eq = (1 + daily).cumprod()
    yrs = max(len(idx) / 365.25, 1 / 365.25)
    return {"trades": int(taken.sum()), "kept_pct": round(float(taken.mean()) * 100, 1),
            "avg_r_per_trade": round(float(r[taken].sum() / max(w[taken].sum(), 1e-9)), 3) if taken.any() else None,
            "total_r": round(float(r.sum()), 1), "win_rate": round(float((df["real"].to_numpy()[taken] > 0).mean()), 3) if taken.any() else None,
            "per_year_pct": round(float(eq.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
            "max_drawdown_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 1),
            "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(365)), 2) if daily.std() > 0 else None}


def _boot_diff(df: pd.DataFrame, w: np.ndarray, n: int = 2000) -> list[float]:
    """90% range of (policy total R - engine total R) per trade-day, resampling whole weeks."""
    d = df["real"].to_numpy() * (w - 1)
    wk = pd.Series(d).groupby(pd.DatetimeIndex(df["date"]).to_period("W").astype(str).to_numpy()).sum().to_numpy()
    rng = np.random.default_rng(0)
    means = wk[rng.integers(0, len(wk), (n, len(wk)))].sum(axis=1)
    return [round(float(np.quantile(means, 0.05)), 1), round(float(np.quantile(means, 0.95)), 1)]


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score

    ok = np.isfinite(p)
    return round(float(roc_auc_score(y[ok], p[ok])), 3) if ok.sum() > 50 and len(set(y[ok])) == 2 else None


def research() -> dict:
    t0 = time.time()
    pk = walk_forward(picks_with_features())
    choose = pk[pk["year"].isin(CHOOSE_YEARS)]
    hold = pk[pk["year"].isin(fc.HOLDOUT_YEARS)]
    base_win = float((choose["real"] > 0).mean())
    rows = []
    for pol in _policies():
        wc, wh = _weights(choose, pol, base_win), _weights(hold, pol, base_win)
        rows.append({"policy": pol, "choose_2022_24": _book(choose, wc), "unseen_2025_26": _book(hold, wh),
                     "unseen_diff_total_r_90": _boot_diff(hold, wh)})
    ok = [r for r in rows if r["policy"]["kind"] != "all" and r["choose_2022_24"]["kept_pct"] >= MIN_KEEP * 100]
    best = max(ok, key=lambda r: r["choose_2022_24"]["sharpe"] or -9)
    base = rows[0]
    y_c, y_h = (choose["real"] > 0).astype(int).to_numpy(), (hold["real"] > 0).astype(int).to_numpy()
    helps = (best["choose_2022_24"]["sharpe"] or 0) > (base["choose_2022_24"]["sharpe"] or 0) and \
        (best["unseen_2025_26"]["sharpe"] or 0) > (base["unseen_2025_26"]["sharpe"] or 0) and best["unseen_diff_total_r_90"][0] > 0
    out = {
        "what": "Meta-labeling on the unified engine's own out-of-sample picks; chosen on 2022-24, reported on 2025-26.",
        "picks": int(len(pk)), "features": FEATURES, "base_win_rate_2022_24": round(base_win, 3),
        "auc": {m: {"choose_2022_24": _auc(y_c, choose[f"p_{m}"].to_numpy()), "unseen_2025_26": _auc(y_h, hold[f"p_{m}"].to_numpy())}
                for m in _models()},
        "engine_alone": base, "chosen": best, "policies_tried": len(rows) - 1, "all": rows,
        "verdict": "helps" if helps else "no_reliable_gain",
        "seconds": round(time.time() - t0, 1),
    }
    REPORT.write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    from app.ml import meta_engine as _ml

    r = _ml.research()
    print("picks", r["picks"], "auc", r["auc"], "verdict", r["verdict"])
    for row in r["all"]:
        p, c, h = row["policy"], row["choose_2022_24"], row["unseen_2025_26"]
        print(f"{str(p.get('model')):10} {p['kind']:10} {p.get('q', ''):<5} | 22-24 kept {c['kept_pct']:5}% avg {c['avg_r_per_trade']} "
              f"tot {c['total_r']:7} sharpe {c['sharpe']} | 25-26 kept {h['kept_pct']:5}% avg {h['avg_r_per_trade']} tot {h['total_r']:7} "
              f"sharpe {h['sharpe']} dd {h['max_drawdown_pct']} diff90 {row['unseen_diff_total_r_90']}")
    print("chosen", r["chosen"]["policy"])
