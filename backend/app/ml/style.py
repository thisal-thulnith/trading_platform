"""The machine's own trading style: instead of a human choosing the plan (stop 1.5 ATR, target 3 ATR, buy only with the
trend), the platform learns from every coin's history since 2017 which plan to use, on which coin, in which market.

1. A menu of 18 plans: LONG or SHORT x stop 0.75 / 1.5 / 2.5 ATR x target 1.5R / 2R / 3R, held up to 14 days, entered at
   the daily close. For every coin and day, what each plan really returned (R after fees, daily high/low, a day touching
   both the stop and the target counts as the stop).
2. One model per plan (LightGBM regression on the realised R) learns from the 98 market features and the learned chart
   patterns WHEN that plan makes money. Walk-forward by year: each year is predicted by models trained only on earlier
   years (15-day purge).
3. The policy: each day, the (coin, plan) with the highest predicted R among the liquid coins, traded only when the
   prediction clears a bar. The bar is chosen on 2020-2024; 2025-2026 is reported as it comes out, against a random coin
   traded with the same plan on the same days, and against the human-designed plan of Today's signal.
4. The style it learned: which sides, stops and targets it picks, in up and down markets, and how each did.

Run: .venv/bin/python -m app.ml.style        (research + today's pick; writes data/models/style_report.json)
Nothing here places orders.
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd

from app.logging import log
from app.ml import forecast as fc

HOLD = 14
FEE = 0.001
STOPS = (0.75, 1.5, 2.5)
TARGETS_R = (1.5, 2.0, 3.0)
PLANS = [(side, s, r) for side in ("LONG", "SHORT") for s in STOPS for r in TARGETS_R]
BARS = (None, 0.0, 0.05, 0.1, 0.15, 0.2, 0.3)     # trade only when the predicted R clears this (None = always trade)
MIN_SELECT_TRADES = 150
REPORT = fc.MODELS / "style_report.json"
OOS = fc.MODELS / "style_oos.npz"
ART = fc.MODELS / "style_models.joblib"
PARAMS = {**fc.REG_PARAMS, "n_estimators": 300}


def pid(side: str, stop: float, r: float) -> str:
    return f"{side}_{stop:g}ATR_{r:g}R"


PIDS = [pid(*p) for p in PLANS]


def plan_returns(c: pd.DataFrame, h: pd.DataFrame, lo: pd.DataFrame, atr: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Realised R (after fees) of every plan entered at each day's close; NaN where the future is not known yet."""
    n = len(c)
    known = c.notna() & atr.notna() & (atr > 0) & (np.arange(n)[:, None] < n - HOLD)
    exit_close = c.ffill().shift(-HOLD)
    hs = [h.shift(-k) for k in range(1, HOLD + 1)]
    ls = [lo.shift(-k) for k in range(1, HOLD + 1)]
    out: dict[str, pd.DataFrame] = {}
    for side, s, r in PLANS:
        d = 1 if side == "LONG" else -1
        sd = s * atr
        stop, tgt = c - d * sd, c + d * r * sd
        first_t = np.full(c.shape, np.inf)
        first_s = np.full(c.shape, np.inf)
        for k in range(HOLD, 0, -1):          # backwards so the earliest day wins
            hk, lk = hs[k - 1], ls[k - 1]
            ht = ((hk >= tgt) if d == 1 else (lk <= tgt)).to_numpy()
            hit_s = ((lk <= stop) if d == 1 else (hk >= stop)).to_numpy()
            first_t[ht] = k
            first_s[hit_s] = k
        stopped = np.isfinite(first_s) & (first_s <= first_t)
        won = np.isfinite(first_t) & ~stopped
        drift = (d * (exit_close - c) / sd).to_numpy()
        R = np.where(stopped, -1.0, np.where(won, r, drift)) - (FEE * c / sd).to_numpy()
        out[pid(side, s, r)] = pd.DataFrame(R, index=c.index, columns=c.columns).where(known)
    return out


def dataset(tier: str = "core", w=None):
    from app.ml import patterns as pt

    w = w if w is not None else fc.load_wide()
    f, U = fc.features(w, fc.TIERS[tier])
    R = plan_returns(w.close, w.high, w.low, fc._atr(w))
    ys = fc.labels(w)
    X, Y, names, dates, syms = fc.stack(f, U, {**R, **{k: ys[k] for k in pt.OUTCOMES}})
    V, valid, _ = pt.shapes(w, U)
    pf = pt.oos_features(V, valid, {k: Y[k] for k in pt.OUTCOMES}, dates)
    X = np.concatenate([X, np.stack([pf[c] for c in fc.PATTERN_COLS], axis=1)], axis=1)
    names = names + list(fc.PATTERN_COLS)
    btc = w.close["BTCUSDT"]
    trend = (btc > btc.rolling(50, min_periods=50).mean()).reindex(dates).to_numpy()
    return w, f, U, X, Y, names, dates, syms, trend


def _model():
    import lightgbm as lgb

    return lgb.LGBMRegressor(**PARAMS)


def walk_forward(X, Y, dates) -> np.ndarray:
    """Out-of-sample predicted R for every row of every test year, one column per plan."""
    P = np.full((len(X), len(PIDS)), np.nan, dtype=np.float32)
    for j, p in enumerate(PIDS):
        y = Y[p]
        ok = np.isfinite(y)
        for yr in fc.TEST_YEARS:
            start = pd.Timestamp(f"{yr}-01-01")
            in_year = (dates >= start) & (dates < pd.Timestamp(f"{yr + 1}-01-01"))
            tr = ok & (dates < start - pd.Timedelta(days=fc.PURGE_DAYS))
            if tr.sum() < 20_000 or in_year.sum() < 1_000:
                continue
            P[in_year, j] = _model().fit(X[tr], y[tr]).predict(X[in_year])
        log.info("style_plan_done", plan=p, n=j + 1, of=len(PIDS))
    return P


def _stats(r: np.ndarray) -> dict:
    r = r[np.isfinite(r)]
    if not len(r):
        return {"trades": 0}
    return {"trades": int(len(r)), "avg_r": round(float(r.mean()), 3), "win_rate": round(float((r > 0).mean()), 3),
            "sum_r": round(float(r.sum()), 1), "median_r": round(float(np.median(r)), 3)}


def policy(P: np.ndarray, Rr: np.ndarray, dates, bar: float | None) -> pd.DataFrame:
    """Each day: the row and plan with the highest predicted R (trade only if it clears the bar)."""
    best_j = np.nanargmax(np.where(np.isfinite(P), P, -np.inf), axis=1)
    best_p = P[np.arange(len(P)), best_j]
    df = pd.DataFrame({"date": dates, "row": np.arange(len(P)), "plan": best_j, "pred": best_p})
    df = df[np.isfinite(df["pred"])]
    pick = df.loc[df.groupby("date")["pred"].idxmax()]
    if bar is not None:
        pick = pick[pick["pred"] > bar]
    pick = pick.assign(real=Rr[pick["row"].to_numpy(), pick["plan"].to_numpy()])
    return pick[np.isfinite(pick["real"])]


def research(tier: str = "core") -> dict:
    t0 = time.time()
    w, f, U, X, Y, names, dates, syms, trend = dataset(tier)
    log.info("style_data", rows=len(X), features=len(names), plans=len(PIDS))
    P = walk_forward(X, Y, dates)
    Rr = np.stack([Y[p] for p in PIDS], axis=1)
    np.savez_compressed(OOS, date=dates.to_numpy().astype("datetime64[D]"), symbol=np.asarray(syms, dtype=str), P=P,
                        R=Rr.astype(np.float32))
    # the bar is chosen on 2020-24 only
    grid = {}
    for bar in BARS:
        pk = policy(P, Rr, dates, bar)
        s = pk[np.isin(pk["date"].dt.year, fc.SELECT_YEARS)]
        grid[str(bar)] = {"select": _stats(s["real"].to_numpy()),
                          "holdout": _stats(pk[np.isin(pk["date"].dt.year, fc.HOLDOUT_YEARS)]["real"].to_numpy())}
    ok = {b: v for b, v in grid.items() if v["select"].get("trades", 0) >= MIN_SELECT_TRADES}
    bar_key = max(ok, key=lambda b: ok[b]["select"]["avg_r"])
    bar = None if bar_key == "None" else float(bar_key)
    pk = policy(P, Rr, dates, bar)
    pk = pk.assign(year=pk["date"].dt.year, side=[PLANS[j][0] for j in pk["plan"]],
                   stop=[PLANS[j][1] for j in pk["plan"]], target_r=[PLANS[j][2] for j in pk["plan"]],
                   trend=np.where(trend[pk["row"].to_numpy()], "up", "down"),
                   symbol=np.asarray(syms)[pk["row"].to_numpy()])
    # a random liquid coin traded with the SAME plan on the SAME day (what the coin choice adds)
    day_mean = {}
    for j in range(len(PIDS)):
        s = pd.Series(Rr[:, j]).groupby(dates).mean()
        day_mean[j] = s
    pk["random_coin"] = [day_mean[j].get(d, np.nan) for j, d in zip(pk["plan"], pk["date"], strict=True)]
    hold = pk[pk["year"].isin(fc.HOLDOUT_YEARS)]
    sel = pk[pk["year"].isin(fc.SELECT_YEARS)]

    def profile(d: pd.DataFrame) -> dict:
        if not len(d):
            return {}
        g = lambda col: {str(k): {"share": round(len(v) / len(d), 3), **_stats(v["real"].to_numpy())}  # noqa: E731
                         for k, v in d.groupby(col)}
        return {"side": g("side"), "stop_atr": g("stop"), "target_r": g("target_r"), "market": g("trend"),
                "long_share_by_market": {t: round(float((v["side"] == "LONG").mean()), 3) for t, v in d.groupby("trend")}}

    # every fixed plan traded on the same daily pick rule, as a reference (no learning of the plan)
    fixed = {}
    for j, p in enumerate(PIDS):
        one = np.full_like(P, np.nan)
        one[:, j] = P[:, j]
        f_pk = policy(one, Rr, dates, None)
        fixed[p] = {"select": _stats(f_pk[np.isin(f_pk["date"].dt.year, fc.SELECT_YEARS)]["real"].to_numpy()),
                    "holdout": _stats(f_pk[np.isin(f_pk["date"].dt.year, fc.HOLDOUT_YEARS)]["real"].to_numpy())}
    human = None
    try:
        from app.ml import signal_plan as sp

        bt = sp.backtest_report() or {}
        human = {k: {"holdout": v["holdout"], "rule": f"{v['recipe']} · filter {v['filter']} · conf {v['confidence']}"}
                 for k, v in (bt.get("chosen") or {}).items()}
    except Exception:  # noqa: BLE001
        pass
    h_stats, r_stats = _stats(hold["real"].to_numpy()), _stats(hold["random_coin"].to_numpy())
    verdict = ("tested_edge" if h_stats.get("avg_r", -1) >= 0.05 and h_stats["avg_r"] > r_stats.get("avg_r", 0)
               and _stats(sel["real"].to_numpy()).get("avg_r", -1) >= 0.05
               else "weak" if h_stats.get("avg_r", -1) > 0 else "no_edge")
    rep = {"tier": tier, "rows": int(len(X)), "features": len(names), "plans": PIDS, "hold_days": HOLD,
           "bar_grid": grid, "bar": bar, "verdict": verdict,
           "select": _stats(sel["real"].to_numpy()), "holdout": h_stats,
           "random_coin_same_plan_holdout": r_stats, "random_coin_same_plan_select": _stats(sel["random_coin"].to_numpy()),
           "by_year": {int(y): _stats(v["real"].to_numpy()) for y, v in pk.groupby("year")},
           "style_select": profile(sel), "style_holdout": profile(hold),
           "fixed_plans": fixed, "human_plan": human,
           "last_picks": [{"date": str(r.date.date()), "symbol": r.symbol, "plan": PIDS[r.plan], "pred": round(float(r.pred), 3),
                           "real": round(float(r.real), 3)} for r in hold.tail(15).itertuples()],
           "seconds": round(time.time() - t0)}
    REPORT.write_text(json.dumps(rep, indent=1, default=str))
    return rep


def fit_final(tier: str = "core") -> dict:
    """Refit every plan's model on all labelled rows and score today's coins: the machine's pick for today."""
    w, f, U, X, Y, names, dates, syms, trend = dataset(tier)
    models = {}
    for p in PIDS:
        ok = np.isfinite(Y[p])
        models[p] = _model().fit(X[ok], Y[p][ok])
    joblib.dump({"names": names, "models": models}, ART)
    last = dates == dates.max()
    P = np.stack([models[p].predict(X[last]) for p in PIDS], axis=1)
    j = np.unravel_index(np.argmax(P), P.shape)
    rep = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    top = sorted(((float(P[i, k]), str(np.asarray(syms)[last][i]), PIDS[k]) for i in range(P.shape[0]) for k in range(P.shape[1])),
                 reverse=True)[:10]
    today = {"as_of": str(dates.max().date()), "symbol": str(np.asarray(syms)[last][j[0]]), "plan": PIDS[j[1]],
             "predicted_r": round(float(P[j]), 3), "trade": rep.get("bar") is None or float(P[j]) > rep["bar"],
             "top10": [{"pred": round(a, 3), "symbol": b, "plan": c} for a, b, c in top]}
    rep["today"] = today
    REPORT.write_text(json.dumps(rep, indent=1, default=str))
    return today


def report() -> dict | None:
    return json.loads(REPORT.read_text()) if REPORT.exists() else None


if __name__ == "__main__":
    from app.ml import style as st

    r = st.research()
    print(f"bar {r['bar']} · verdict {r['verdict']} · {r['seconds']}s")
    print("2020-24", r["select"], "| random coin same plan", r["random_coin_same_plan_select"])
    print("2025-26", r["holdout"], "| random coin same plan", r["random_coin_same_plan_holdout"])
    print("by year", r["by_year"])
    print("style 2025-26", json.dumps(r["style_holdout"], indent=0)[:1500])
    print("today", st.fit_final())
