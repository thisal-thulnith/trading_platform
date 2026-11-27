"""The unified signal engine: every method in ONE model and ONE decision, instead of separate opinions side by side.

    data (591 Binance coins, daily, 2017-2026)
      -> features: market & factors (app/quant/lab/factors.py), technical indicators (app/ml/technicals.py),
                   human trading concepts (app/ml/concepts.py: structure, liquidity, Wyckoff, VWAP, price action),
                   learned chart patterns (app/ml/patterns.py: what followed the 50 most similar past charts)
      -> 18 plan models (app/ml/style.py): for each plan (side x stop 0.75/1.5/2.5 ATR x target 1.5/2/3R, 14 days) a
                   LightGBM model learns, from all the features at once, the R that plan will make on that coin
      -> decision: each day the coins and plans with the best predicted R (a rule chosen on 2020-24 among 8)
      -> the signal: coin, side, entry, stop, target, leverage, and WHY: the model's own contributions grouped by
                   concept (structure, liquidity, trend, volatility, flow, patterns ...)

Honesty rules (the same as the rest of the platform): walk-forward by year with a 15-day purge; everything that is
chosen (the decision rule) is chosen on 2020-2024 only; 2025-2026 is reported as it came out, with a bootstrap range,
against a random coin with the same plan and against the engine without the concepts; coins whose ATR is above 25% of
the price are never picked (signal_plan.MAX_ATR_PCT).

Run: .venv/bin/python -m app.ml.unified        (research + decision + final models + today; ~20 minutes)
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd

from app.logging import log
from app.ml import forecast as fc
from app.ml import style as sty
from app.ml.signal_plan import MAX_ATR_PCT

REPORT = fc.MODELS / "unified_report.json"
OOS = fc.MODELS / "unified_oos.npz"
ART = fc.MODELS / "unified_models.joblib"
TODAY = fc.MODELS / "unified_today.json"
MIN_SELECT_TRADES = 300
BAR_LOOKBACK = 365
# The concept features (app/ml/concepts.py) were tested on 2026-11-24 and REJECTED: with the same decision rule the
# engine scored +0.17R a trade on 2020-24 and +0.05R on 2025-26 with them, +0.20R and +0.08R without (the model already
# learns what structure, liquidity and Wyckoff measure from price, momentum and volatility; the extra inputs add noise).
# Kept for the record in unified_report_concepts.json / unified_oos_concepts.npz. Flip only after a new fair test.
USE_CONCEPTS = False
OOS_CONCEPTS = fc.MODELS / "unified_oos_concepts.npz"

# ------------------------------------------------------------------------------------------- feature groups (for "why")
GROUPS = [
    ("Market structure (SMC)", ("ms_",)),
    ("Liquidity & gaps (ICT)", ("liq_", "fvg_", "disp_")),
    ("Wyckoff & value (VWAP)", ("wy_", "vwap_")),
    ("Price action & levels", ("pa_", "round_dist")),
    ("Desk checklist", ("conf_",)),
    ("Trend & momentum", ("mom_", "vadj_mom", "p_ma_", "dist_ma", "macd", "dist_high_", "tr_", "rs_btc")),
    ("Oscillators & reversal", ("rev_", "rsi_", "stoch_", "cci_", "bb_pctb", "ret_", "down_days")),
    ("Volatility", ("atr_pct", "rvol_", "range_", "bb_width", "max_ret", "skew_")),
    ("Volume & order flow", ("vol_trend", "v_ma_", "taker_imb", "cmf_", "amihud", "size")),
    ("Funding (perps)", ("funding_",)),
    ("Link to BTC", ("beta_btc", "corr_btc")),
    ("Whole market", ("btc_", "breadth", "mkt_")),
    ("Learned chart patterns", ("analog_", "pattern_")),
]


def group_of(name: str) -> str:
    base = name[2:] if name.startswith("r_") else name
    for g, prefixes in GROUPS:
        if base.startswith(prefixes):
            return g
    return "Other"


def describe(name: str) -> str:
    from app.ml.concepts import CONCEPT_DOC
    from app.ml.technicals import TECH_DOC
    from app.quant.lab.factors import FACTOR_DOC

    extra = {"atr_pct": "14-day ATR / price", "rvol_7": "7-day volatility", "range_7": "7-day high-low range / price",
             "ret_1": "1-day return", "dist_ma50": "close / 50-day average - 1", "dist_ma200": "close / 200-day average - 1",
             "btc_mom_7": "BTC 7-day return", "btc_mom_28": "BTC 28-day return", "btc_rvol_30": "BTC 30-day volatility",
             "btc_dist_ma200": "BTC vs its 200-day average", "breadth_50": "share of coins above their 50-day average",
             "mkt_mom_7": "median coin's 7-day return"}
    ranked = name.startswith("r_")
    base = name[2:] if ranked else name
    if base.startswith(("analog_", "pattern_")):
        src, k = base.split("_", 1)
        d = f"{'50 most similar past charts' if src == 'analog' else 'learned chart pattern'}: rate of {k.replace('_', ' ')}"
    else:
        d = CONCEPT_DOC.get(base) or TECH_DOC.get(base) or FACTOR_DOC.get(base) or extra.get(base) or base
    return f"{d} (rank among today's coins)" if ranked else d


# ------------------------------------------------------------------------------------------- data

def dataset(tier: str = "core", w=None, with_concepts: bool = USE_CONCEPTS):
    from app.ml import patterns as pt
    from app.ml.concepts import concepts

    w = w if w is not None else fc.load_wide()
    f, U = fc.features(w, fc.TIERS[tier])
    atr = fc._atr(w)
    if with_concepts:
        f |= concepts(w, atr)
    R = sty.plan_returns(w.close, w.high, w.low, atr)
    ys = fc.labels(w)
    X, Y, names, dates, syms = fc.stack(f, U, {**R, **{k: ys[k] for k in pt.OUTCOMES}})
    V, valid, _ = pt.shapes(w, U)
    pf = pt.oos_features(V, valid, {k: Y[k] for k in pt.OUTCOMES}, dates)
    X = np.concatenate([X, np.stack([pf[c] for c in fc.PATTERN_COLS], axis=1)], axis=1)
    names = names + list(fc.PATTERN_COLS)
    t_idx, s_idx = np.nonzero(U.to_numpy())
    atr_pct = (atr / w.close * 100).to_numpy()[t_idx, s_idx]
    btc = w.close["BTCUSDT"]
    trend = (btc > btc.rolling(50, min_periods=50).mean()).reindex(dates).fillna(False).to_numpy()
    return {"w": w, "f": f, "U": U, "X": X, "Y": Y, "names": names, "dates": dates, "syms": np.asarray(syms),
            "atr_pct": atr_pct, "trend": trend}


# ------------------------------------------------------------------------------------------- decision layer

RULES = [{"score": s, "k": k, "bar": b} for s in ("raw", "edge") for k in (1, 3) for b in (False, True)]


def _scores(P: np.ndarray, dates: pd.DatetimeIndex, kind: str) -> np.ndarray:
    if kind == "raw":
        return P
    med = pd.DataFrame(P).groupby(np.asarray(dates)).transform("median").to_numpy()
    return P - med       # how far this coin's plan stands out from the same plan on the other coins today


def decide(P: np.ndarray, Rr: np.ndarray | None, dates, atr_pct: np.ndarray, rule: dict) -> pd.DataFrame:
    """The picks of one decision rule: per day the top-k coins by their best plan's score (distinct coins), only
    coins under the volatility cap, and (bar) only when the score beats the median daily top score of the past year."""
    S = _scores(P, dates, rule["score"]).astype(np.float64)
    S[~(atr_pct <= MAX_ATR_PCT)] = np.nan
    valid = np.isfinite(S).any(axis=1)
    j = np.where(valid, np.nanargmax(np.where(np.isfinite(S), S, -np.inf), axis=1), 0)
    best = np.where(valid, S[np.arange(len(S)), j], np.nan)
    df = pd.DataFrame({"date": dates, "row": np.arange(len(S)), "plan": j, "score": best,
                       "pred": np.where(valid, P[np.arange(len(P)), j], np.nan)})
    df = df[np.isfinite(df["score"])].sort_values(["date", "score"], ascending=[True, False])
    df["rank"] = df.groupby("date").cumcount() + 1
    top1 = df[df["rank"] == 1].set_index("date")["score"]
    # the bar: the median top score of the previous BAR_LOOKBACK days (known on the day: shifted by one)
    bar = top1.rolling(BAR_LOOKBACK, min_periods=60).median().shift(1)
    df = df[df["rank"] <= rule["k"]]
    if rule["bar"]:
        df = df[df["score"].to_numpy() > bar.reindex(df["date"]).to_numpy()]
    if Rr is not None:
        df = df.assign(real=Rr[df["row"].to_numpy(), df["plan"].to_numpy()])
    return df.assign(bar=bar.reindex(df["date"]).to_numpy())


def _boot(r: np.ndarray, dates, n: int = 2000) -> list[float] | None:
    """90% range of the average R, resampling whole weeks (trades in the same week are not independent)."""
    if len(r) < 30:
        return None
    wk = pd.Series(r).groupby(pd.DatetimeIndex(dates).to_period("W").astype(str).to_numpy())
    s, c = wk.sum().to_numpy(), wk.count().to_numpy()
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(s), (n, len(s)))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return [round(float(np.quantile(means, 0.05)), 3), round(float(np.quantile(means, 0.95)), 3)]


def _portfolio(pk: pd.DataFrame, k: int, risk_per_day: float = 0.005) -> dict:
    """Compounded paper account: each day's picks share `risk_per_day` of the equity (booked on the entry day)."""
    if not len(pk):
        return {}
    daily = (pk.groupby("date")["real"].sum() * risk_per_day / k)
    idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(idx, fill_value=0.0)
    eq = (1 + daily).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    yrs = max(len(idx) / 365.25, 1 / 365.25)
    return {"risk_per_day_pct": risk_per_day * 100, "growth_pct": round(float(eq.iloc[-1] - 1) * 100, 1),
            "per_year_pct": round(float(eq.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
            "max_drawdown_pct": round(dd * 100, 1),
            "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(365)), 2) if daily.std() > 0 else None}


def evaluate(P, Rr, dates, atr_pct, syms=None) -> dict:
    rows = []
    for rule in RULES:
        pk = decide(P, Rr, dates, atr_pct, rule)
        pk = pk[np.isfinite(pk["real"])]
        y = pd.DatetimeIndex(pk["date"]).year
        sel, hold = pk[np.isin(y, fc.SELECT_YEARS)], pk[np.isin(y, fc.HOLDOUT_YEARS)]
        rows.append({"rule": rule, "select": sty._stats(sel["real"].to_numpy()),
                     "holdout": sty._stats(hold["real"].to_numpy()), "_pk": pk})
    ok = [r for r in rows if r["select"].get("trades", 0) >= MIN_SELECT_TRADES]
    best = max(ok, key=lambda r: r["select"]["avg_r"])
    pk = best["_pk"]
    y = pd.DatetimeIndex(pk["date"]).year
    sel, hold = pk[np.isin(y, fc.SELECT_YEARS)], pk[np.isin(y, fc.HOLDOUT_YEARS)]
    # a random liquid coin with the SAME plan on the SAME day: what the coin choice adds
    capped = np.where((atr_pct <= MAX_ATR_PCT)[:, None], Rr, np.nan)
    day_mean = pd.DataFrame(capped).groupby(np.asarray(dates)).mean()
    rnd = day_mean.to_numpy()[day_mean.index.get_indexer(pk["date"]), pk["plan"].to_numpy()]
    pk = pk.assign(random_coin=rnd, year=y.to_numpy())
    hr, sr = pk.loc[np.isin(y, fc.HOLDOUT_YEARS), "random_coin"], pk.loc[np.isin(y, fc.SELECT_YEARS), "random_coin"]
    by_side, by_stop, by_target = {}, {}, {}
    for (side, s, r), j in zip(sty.PLANS, range(len(sty.PLANS)), strict=True):
        m = hold["plan"].to_numpy() == j
        by_side.setdefault(side, []).extend(hold["real"].to_numpy()[m])
        by_stop.setdefault(str(s), []).extend(hold["real"].to_numpy()[m])
        by_target.setdefault(str(r), []).extend(hold["real"].to_numpy()[m])
    hs = sty._stats(hold["real"].to_numpy())
    verdict = ("tested_edge" if best["select"]["avg_r"] >= 0.05 and hs.get("avg_r", -1) >= 0.05
               and hs["avg_r"] > sty._stats(hr.to_numpy()).get("avg_r", 9) else
               "weak" if hs.get("avg_r", -1) > 0 else "no_edge")
    return {"rules_tried": [{"rule": r["rule"], "select": r["select"], "holdout": r["holdout"]} for r in rows],
            "rule": best["rule"], "verdict": verdict,
            "select": best["select"], "holdout": hs,
            "holdout_range_90": _boot(hold["real"].to_numpy(), hold["date"]),
            "select_range_90": _boot(sel["real"].to_numpy(), sel["date"]),
            "random_coin_same_plan": {"select": sty._stats(sr.to_numpy()), "holdout": sty._stats(hr.to_numpy())},
            "by_year": {int(k): sty._stats(v["real"].to_numpy()) for k, v in pk.groupby("year")},
            "holdout_by_side": {k: sty._stats(np.asarray(v)) for k, v in by_side.items()},
            "holdout_by_stop_atr": {k: sty._stats(np.asarray(v)) for k, v in by_stop.items()},
            "holdout_by_target_r": {k: sty._stats(np.asarray(v)) for k, v in by_target.items()},
            "portfolio_holdout": _portfolio(hold, best["rule"]["k"]),
            "portfolio_select": _portfolio(sel, best["rule"]["k"]),
            "_picks": pk}


# ------------------------------------------------------------------------------------------- research

def _align(path, dates, syms) -> np.ndarray | None:
    """Out-of-sample predictions saved for another feature set, aligned to these rows (NaN where missing)."""
    if not path.exists():
        return None
    z = np.load(path, allow_pickle=True)   # our own files
    key = pd.MultiIndex.from_arrays([pd.to_datetime(z["date"]), z["symbol"]])
    pos = key.get_indexer(pd.MultiIndex.from_arrays([dates, syms]))
    P = np.full((len(dates), z["P"].shape[1]), np.nan, dtype=np.float32)
    P[pos >= 0] = z["P"][pos[pos >= 0]]
    return P


def _rule_stats(P, Rr, dates, atr_pct, rule) -> dict:
    pk = decide(P, Rr, dates, atr_pct, rule)
    pk = pk[np.isfinite(pk["real"])]
    y = pd.DatetimeIndex(pk["date"]).year
    return {"select": sty._stats(pk.loc[np.isin(y, fc.SELECT_YEARS), "real"].to_numpy()),
            "holdout": sty._stats(pk.loc[np.isin(y, fc.HOLDOUT_YEARS), "real"].to_numpy())}


def build_report(P, Rr, dates, syms, atr_pct, tier="core", rows=None, features=None, t0=None) -> dict:
    ev = evaluate(P, Rr, dates, atr_pct)
    pk = ev.pop("_picks")
    # the feature set NOT used, scored with the same decision rule: the concepts test
    alt = _align(sty.OOS if USE_CONCEPTS else OOS_CONCEPTS, dates, syms)   # the other feature set's predictions
    concepts_test = None
    if alt is not None:
        a = _rule_stats(alt, Rr, dates, atr_pct, ev["rule"])
        me = {"select": ev["select"], "holdout": ev["holdout"]}
        concepts_test = {"with_concepts": me if USE_CONCEPTS else a, "without_concepts": a if USE_CONCEPTS else me,
                         "adopted": "with" if USE_CONCEPTS else "without",
                         "rule": ev["rule"]}
    hold = pk[pk["year"].isin(fc.HOLDOUT_YEARS)]
    rep = {"tier": tier, "made_at": pd.Timestamp.now("UTC").isoformat(), "rows": rows, "features": features,
           "uses_concepts": USE_CONCEPTS, "plans": sty.PIDS, "hold_days": sty.HOLD, "max_atr_pct": MAX_ATR_PCT,
           "min_select_trades": MIN_SELECT_TRADES, **ev, "concepts_test": concepts_test,
           "last_picks": [{"date": str(r.date.date()), "symbol": str(syms[r.row]), "plan": sty.PIDS[r.plan],
                           "pred": round(float(r.pred), 3), "real": round(float(r.real), 3)}
                          for r in hold.tail(20).itertuples()],
           "seconds": round(time.time() - t0) if t0 else None}
    REPORT.write_text(json.dumps(rep, indent=1, default=str))
    return rep


def research(tier: str = "core") -> dict:
    t0 = time.time()
    d = dataset(tier)
    X, Y, dates = d["X"], d["Y"], d["dates"]
    log.info("unified_data", rows=len(X), features=len(d["names"]), concepts=USE_CONCEPTS)
    P = sty.walk_forward(X, Y, dates)
    Rr = np.stack([Y[p] for p in sty.PIDS], axis=1)
    np.savez_compressed(OOS, date=dates.to_numpy().astype("datetime64[D]"), symbol=np.asarray(d["syms"], dtype=str), P=P,
                        R=Rr.astype(np.float32), atr_pct=d["atr_pct"].astype(np.float32), trend=d["trend"])
    return build_report(P, Rr, dates, d["syms"], d["atr_pct"], tier, int(len(X)), len(d["names"]), t0)


def rebuild_from_saved() -> dict:
    """Re-decide from saved out-of-sample predictions (no retraining): the no-concepts engine (style_oos.npz) on the
    rows, outcomes and volatility of the last research run."""
    z = np.load(OOS_CONCEPTS, allow_pickle=True)
    dates, syms = pd.to_datetime(z["date"]), z["symbol"]
    P = _align(sty.OOS, dates, syms)
    np.savez_compressed(OOS, date=z["date"], symbol=np.asarray(syms, dtype=str), P=P, R=z["R"], atr_pct=z["atr_pct"], trend=z["trend"])
    return build_report(P, z["R"].astype(np.float64), dates, syms, z["atr_pct"].astype(np.float64),
                        rows=int(len(P)), features=None)


def fit_final(tier: str = "core") -> None:
    """Every plan's model refitted on all labelled rows (served live)."""
    d = dataset(tier)
    models = {}
    for p in sty.PIDS:
        ok = np.isfinite(d["Y"][p])
        models[p] = sty._model().fit(d["X"][ok], d["Y"][p][ok])
    # out-of-sample daily top scores of the last year: the live bar
    rep = report() or {}
    bar = None
    if OOS.exists() and rep.get("rule"):
        z = np.load(OOS)
        pk = decide(z["P"], None, pd.to_datetime(z["date"]), z["atr_pct"], {**rep["rule"], "bar": False})
        top = pk[pk["rank"] == 1].set_index("date")["score"]
        bar = float(top.iloc[-BAR_LOOKBACK:].median())
    joblib.dump({"names": d["names"], "models": models, "bar": bar, "trained_at": pd.Timestamp.now("UTC").isoformat()},
                ART)
    _cache.pop("art", None)


_cache: dict = {}


def _art() -> dict | None:
    if "art" not in _cache and ART.exists():
        _cache["art"] = joblib.load(ART)
    return _cache.get("art")


def report() -> dict | None:
    if "rep" not in _cache or _cache.get("rep_mtime") != (REPORT.stat().st_mtime if REPORT.exists() else None):
        _cache["rep"] = json.loads(REPORT.read_text()) if REPORT.exists() else None
        _cache["rep_mtime"] = REPORT.stat().st_mtime if REPORT.exists() else None
    return _cache["rep"]


# ------------------------------------------------------------------------------------------- live

def score_today(tier: str = "core", w=None) -> dict:
    """Every covered coin scored for every plan on its latest day, with the model's reasons for its best plan.
    Saved to unified_today.json (the API reads that file)."""
    from app.ml import patterns as pt
    from app.ml.concepts import concepts

    art = _art()
    if art is None:
        return {"error": "no unified models yet: run `python -m app.ml.unified`"}
    w = w if w is not None else fc.load_wide()
    f, U = fc.features(w, fc.TIERS[tier])
    atr = fc._atr(w)
    if USE_CONCEPTS:
        f |= concepts(w, atr)
    mask = U.copy()
    mask.loc[:] = False
    seen: set[str] = set()
    for dte in U.index[-3:][::-1]:
        fresh = U.loc[dte] & ~U.columns.isin(list(seen))
        mask.loc[dte] = fresh
        seen |= set(U.columns[fresh.to_numpy()])
    X, _, names, rdates, syms = fc.stack(f, mask)
    pf = pt.live_features(w, mask)
    X = np.concatenate([X, np.stack([pf[c] for c in fc.PATTERN_COLS], axis=1)], axis=1)
    names = names + list(fc.PATTERN_COLS)
    X = X[:, [names.index(n) for n in art["names"]]]
    names = art["names"]
    P = np.stack([art["models"][p].predict(X) for p in sty.PIDS], axis=1)
    ti = pd.Index(w.close.index).get_indexer(rdates)
    sj = pd.Index(w.close.columns).get_indexer(syms)
    close = w.close.to_numpy()[ti, sj]
    atr_v = atr.to_numpy()[ti, sj]
    btc = w.close["BTCUSDT"].dropna()  # BTC's newest day can land after other coins'; use its last real close
    ma = btc.rolling(50, min_periods=50).mean()
    rows = {}
    for i, s in enumerate(syms):
        rows[str(s)] = {"date": str(rdates[i].date()), "close": float(close[i]), "atr": float(atr_v[i]),
                        "atr_pct": round(float(atr_v[i] / close[i] * 100), 3) if close[i] else None,
                        "pred": {p: round(float(P[i, k]), 4) for k, p in enumerate(sty.PIDS)},
                        "x": [None if not np.isfinite(v) else round(float(v), 5) for v in X[i]]}
    out = {"as_of": str(max(rdates).date()), "tier": tier, "bar": art.get("bar"), "names": names,
           "market": {"btc_close": float(btc.iloc[-1]), "btc_ma50": round(float(ma.iloc[-1]), 2),
                      "date": str(btc.index[-1].date()), "trend": "up" if btc.iloc[-1] > ma.iloc[-1] else "down"},
           "rows": rows}
    TODAY.write_text(json.dumps(out))
    _cache.pop("today", None)
    try:   # keep the day's ranking for Signal history (the daily file above is overwritten every day)
        from app.ml import signal_history

        signal_history.save_snapshot()
    except Exception as e:  # noqa: BLE001 - history must never break the daily score
        log.warning("ranking_snapshot_failed", error=str(e))
    return {"as_of": out["as_of"], "coins": len(rows)}


def today() -> dict | None:
    if "today" not in _cache and TODAY.exists():
        _cache["today"] = json.loads(TODAY.read_text())
    return _cache.get("today")


def explain(symbol: str, plan: str, top: int = 3) -> list[dict]:
    """The model's own reasons for this coin and plan: LightGBM's per-feature contributions (TreeSHAP), summed by
    concept group, each with its strongest features, their values and what they mean."""
    art, td = _art(), today()
    if art is None or td is None or symbol not in td["rows"]:
        return []
    x = np.array([[np.nan if v is None else v for v in td["rows"][symbol]["x"]]], dtype=np.float64)
    contrib = art["models"][plan].predict(x, pred_contrib=True)[0]
    names = art["names"]
    groups: dict[str, list[tuple[float, str, float]]] = {}
    for k, n in enumerate(names):
        groups.setdefault(group_of(n), []).append((float(contrib[k]), n, x[0, k]))
    out = []
    for g, items in groups.items():
        tot = sum(c for c, _, _ in items)
        items.sort(key=lambda t: -abs(t[0]))
        out.append({"group": g, "r": round(tot, 3),
                    "features": [{"name": n, "what": describe(n), "value": None if not np.isfinite(v) else round(v, 4),
                                  "r": round(c, 3)} for c, n, v in items[:top] if abs(c) >= 0.002]})
    out.sort(key=lambda t: -abs(t["r"]))
    return out


def ranked(side: str | None = None) -> list[dict]:
    """Today's coins by their best plan's score under the chosen rule (volatility cap applied)."""
    td, rep = today(), report() or {}
    if not td:
        return []
    syms = list(td["rows"])
    P = np.array([[td["rows"][s]["pred"][p] for p in sty.PIDS] for s in syms], dtype=np.float64)
    if side:
        P[:, [k for k, (sd, _, _) in enumerate(sty.PLANS) if sd != side]] = np.nan
    atr_pct = np.array([td["rows"][s]["atr_pct"] or np.inf for s in syms])
    rule = rep.get("rule") or {"score": "raw", "k": 1, "bar": False}
    S = _scores(P, pd.DatetimeIndex([pd.Timestamp(td["as_of"])] * len(syms)), rule["score"])
    S[~(atr_pct <= MAX_ATR_PCT)] = np.nan
    out = []
    for i, s in enumerate(syms):
        if not np.isfinite(S[i]).any():
            continue
        j = int(np.nanargmax(S[i]))
        out.append({"symbol": s, "plan": sty.PIDS[j], "score": round(float(S[i, j]), 4), "pred_r": round(float(P[i, j]), 4)})
    out.sort(key=lambda r: -r["score"])
    for n, r in enumerate(out):
        r["rank"] = n + 1
    return out


def _parse(plan: str) -> tuple[str, float, float]:
    side, stop, r = plan.split("_")
    return side, float(stop.replace("ATR", "")), float(r.replace("R", ""))


def _tested(rep: dict, plan: str) -> dict:
    side, stop, r = _parse(plan)
    h = rep.get("holdout") or {}
    return {"recipe": "unified", "filter": "learned", "confidence": None, "verdict": rep.get("verdict", "no_edge"),
            "holdout": h, "select": rep.get("select") or {},
            "baseline_holdout": (rep.get("random_coin_same_plan") or {}).get("holdout") or {},
            "baseline_select": (rep.get("random_coin_same_plan") or {}).get("select") or {},
            "beats_average_coin": h.get("avg_r", -9) > ((rep.get("random_coin_same_plan") or {}).get("holdout") or {}).get("avg_r", 9),
            "yearly_avg_r": {k: v.get("avg_r") for k, v in (rep.get("by_year") or {}).items()},
            "range_90": rep.get("holdout_range_90"),
            "this_side": (rep.get("holdout_by_side") or {}).get(side),
            "this_stop": (rep.get("holdout_by_stop_atr") or {}).get(f"{stop:g}"),
            "rule": rep.get("rule")}


LIMIT_ATR = 0.33


def entry_for(close: float, atr: float, side: str, price: float) -> tuple[float, str]:
    """The ONE entry rule every page uses: market at the live price, unless the price already ran more than 0.33 ATR
    the trade's way since the model's close; then a limit at 0.33 ATR from that close (wait for a pull-back)."""
    d = 1 if side == "LONG" else -1
    moved = (price - close) / atr if atr else 0.0
    if d * moved > LIMIT_ATR:
        return close + d * LIMIT_ATR * atr, "limit"
    return price, "market"


def plan_for(symbol: str, side: str | None = None, margin: float = 100.0, live: dict | None = None) -> dict:
    """The unified plan for one coin: its best plan (or the best for the side asked), the levels at the live price, the
    leverage table, the tight-stop option for 10-20x, the model's reasons by concept and the tested record."""
    from app.ml import signal_plan as sp

    td, rep = today(), report()
    if not td or not rep:
        return {"available": False, "note": "The unified engine is not trained yet."}
    row = td["rows"].get(symbol)
    if row is None:
        return {"available": True, "covered": False, "symbol": symbol,
                "note": "Not covered: the unified engine scores liquid coins ($5M+ a day) with 90+ days of history."}
    preds = row["pred"]
    ranks = ranked(side)
    me = next((x for x in ranks if x["symbol"] == symbol), None)
    # the coin's plan under the chosen rule's score (raw R, or R relative to the other coins); max R if not ranked
    plan = me["plan"] if me else max((p for p in preds if side is None or p.startswith(side)), key=preds.get)
    pside, stop_atr, r_mult = _parse(plan)
    # the tight-stop version (0.75 ATR) for the same side: the high-leverage option
    tight = max((p for p in preds if p.startswith(pside) and _parse(p)[1] == 0.75), key=preds.get)
    close, atr = row["close"], row["atr"]
    price = float((live or {}).get("price") or close)
    d = 1 if pside == "LONG" else -1
    notes = []
    moved = (price - close) / atr if atr else 0.0
    # price already ran more than 0.33 ATR the trade's way: don't chase, wait for a pull-back to that level (a limit on
    # the right side of the price). Within 0.33 ATR the price is still fine to take at market. (Before 2026-11-26 the
    # switch was at 0.25 ATR, so between 0.25 and 0.33 the "limit" sat on the wrong side of the price and filled at once.)
    entry, entry_type = entry_for(close, atr, pside, price)
    if entry_type == "limit":
        notes.append(f"Price moved {abs(price / close - 1) * 100:.1f}% {'up' if moved > 0 else 'down'} since the "
                     f"models' data ({row['date']} close {sp._fmt(close)}). Don't chase: wait for the limit.")

    def part(p: str) -> dict:
        _, s, r = _parse(p)
        stop, tgt = entry - d * s * atr, entry + d * r * s * atr
        lt = sp.leverage_table(entry, stop, tgt, None, pside, symbol, margin)
        safe = max(1, min(lt["safe_max"], 20))
        risk_at_safe = next((x["stop_usd"] for x in lt["rows"] if x["leverage"] == safe), None)
        pct = lambda v: round((v / entry - 1) * 100, 1)  # noqa: E731
        return {"plan": p, "entry": sp._fmt(entry), "stop": sp._fmt(stop), "stop_pct": pct(stop), "tp1": sp._fmt(tgt),
                "tp1_pct": pct(tgt), "tp2": None, "tp2_pct": None, "days": sty.HOLD, "stop_atr": s, "target_r": r,
                "predicted_r": round(float(preds[p]), 3),
                "label": f"Stop {s:g} ATR, one target at {r:g}R ({r * s:g} ATR), held up to {sty.HOLD} days",
                "leverage": lt, "tested": _tested(rep, p),
                "expected_usd_per_trade_at_safe": (round(-risk_at_safe * float(preds[p]), 2) if risk_at_safe else None)}

    if side is not None:                       # the rank that decides a trade is the one across both sides
        ranks = ranked()
        me = next((x for x in ranks if x["symbol"] == symbol), None)
    rule = rep.get("rule") or {}
    gates = []
    a = row.get("atr_pct")
    if a is not None and a > MAX_ATR_PCT:
        gates.append(f"Extreme volatility: its average daily move (ATR) is {a:.0f}% after a pump or crash; ATR-based "
                     f"levels are meaningless. Not traded (cap {MAX_ATR_PCT:.0f}%).")
    if me is None or me["rank"] > rule.get("k", 1) or me["plan"] != plan:
        gates.append(f"Not among today's top {rule.get('k', 1)} picks of the engine "
                     f"({'ranked #' + str(me['rank']) + ' of ' + str(len(ranks)) if me else 'not ranked'}).")
    bar = td.get("bar")
    if rule.get("bar") and bar is not None and me is not None and me["score"] <= bar:
        gates.append(f"Score {me['score']:.2f} is under today's bar {bar:.2f} (the median top score of the past year).")
    if preds[plan] <= 0:
        gates.append("The best plan for this coin is expected to lose money (predicted R below zero).")
    if rep.get("verdict") == "no_edge":
        gates.append("The engine found no edge on the unseen years (2025-26).")
    risks = [f"In 2025-26 tests the engine's picks made money {round((rep.get('holdout') or {}).get('win_rate', 0) * 100)}% "
             f"of the time: it wins by making {r_mult:g}R on winners against 1R on losers, not by being right often.",
             "One target, no partial close: the plan is tested exactly as shown. Moving the stop changes the odds."]
    if pside == "LONG" and (td.get("market") or {}).get("trend") == "down":
        risks.append("BTC is below its 50-day average: longs went against the market in 2025-26 tests.")
    sw = part(plan)
    # a clear side needs the model's own forecast for the plan to be positive. When it is not (and the coin is not the
    # tested #1 pick), neither side has a reliable record: tested 2026-11-26 on such rows, the ranked side made +0.04R on
    # 2021-24 and -0.13R on 2025-26, the best-forecast side the reverse (-0.05R / +0.12R). So no side is claimed.
    is_pick = me is not None and me["rank"] <= rule.get("k", 1) and me["plan"] == plan
    clear = bool(preds[plan] > 0 or is_pick)
    # live result since the model's close, as if the signal had been taken there (display only)
    since = d * (price - close)
    live = {"move_pct": round(d * (price / close - 1) * 100, 2) if close else None,
            "r": round(since / (stop_atr * atr), 3) if atr else None, "from": sp._fmt(close), "at": row["date"]}
    return {"available": True, "covered": True, "engine": "unified", "symbol": symbol, "side": pside, "tier": td["tier"],
            "clear": clear, "live_since_signal": live,
            "unclear_note": None if clear else (
                f"No clear signal for {symbol.replace('USDT', '')}: the model ranks it #{me['rank'] if me else '?'} and its own "
                "forecast for its best-ranked plan is negative. Past tests could not say which side is right for coins "
                "like this, so no direction is given."),
            "plan_id": plan, "status": "trade" if not gates else "watch", "gates": gates, "fast_gates": gates,
            "market": td.get("market"), "as_of": row["date"], "model_close": sp._fmt(close), "price": sp._fmt(price),
            "atr": sp._fmt(atr), "atr_pct": row.get("atr_pct"), "entry_type": entry_type,
            "score": me["score"] if me else None, "rank": me["rank"] if me else None, "of": len(ranks),
            "scores": {s: round(max(v for p, v in preds.items() if p.startswith(s)), 3) for s in ("LONG", "SHORT")},
            "plans": {"swing": sw, "fast": part(tight)}, "why": explain(symbol, plan),
            "evidence": [], "notes": notes, "risks": risks,
            "cancel_if": (f"Cancel if price reaches {sw['stop']} before you are filled, or if not filled within 2 days "
                          f"(the engine re-scores every day)."),
            "margin": margin, "live": live or None}


def candidates(n: int = 10) -> dict:
    td, rep = today(), report()
    if not td or not rep:
        return {"available": False}
    rule = rep.get("rule") or {"k": 1}
    rk = ranked()
    bar = td.get("bar")
    picks = [x for x in rk[: rule.get("k", 1)] if x["pred_r"] > 0 and (not rule.get("bar") or bar is None or x["score"] > bar)]
    return {"available": True, "engine": "unified", "as_of": td["as_of"], "market": td.get("market"), "rule": rule,
            "bar": bar, "verdict": rep.get("verdict"), "status": "trade" if picks and rep.get("verdict") != "no_edge" else "no_trade",
            "picks": picks, "top": rk[:n],
            "by_side": {s: ranked(s)[:5] for s in ("LONG", "SHORT")},
            "tested": {k: rep.get(k) for k in ("select", "holdout", "holdout_range_90", "random_coin_same_plan",
                                                "concepts_test", "by_year", "portfolio_holdout", "holdout_by_side")}}


def train_all() -> dict:
    rep = research()
    fit_final()
    score_today()
    return rep


if __name__ == "__main__":
    from app.ml import unified as un

    r = un.train_all()
    print(json.dumps({k: r[k] for k in ("rule", "verdict", "select", "holdout", "holdout_range_90",
                                        "random_coin_same_plan", "concepts_test", "by_year", "holdout_by_side",
                                        "portfolio_holdout", "portfolio_select", "seconds")}, indent=1, default=str))
    for x in r["rules_tried"]:
        print(x)
    print("today:", un.candidates()["picks"])
