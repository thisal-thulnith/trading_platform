"""Chart patterns learned from history: every coin, every day since 2019, as a scale-free shape.

Shape of a chart on day D (only data dated <= D):
  - the last 30 daily closes as a log path, standardised (mean 0, unit spread): the pure shape, any coin, any price
  - the last 10 days' quote volume relative to its 30-day average (log), weighted half
  - the move over the 30 days in units of the coin's daily volatility (how strong the shape is)
Shapes are compressed with PCA (12 components) for search.

Two ways to use them:
  analogs   the 50 most similar past charts across all coins and what happened next (up after 7 days, a long or a
            short reaching 2R before its stop, a 2-ATR drop) — "this chart looks like these, and then ..."
  library   k-means groups the shapes into 40 learned patterns; each pattern is named from its average shape and
            carries its historical outcomes

Validation: walk-forward by year. For each test year (2022-2026) the analogs and the library come only from charts
dated at least 15 days before the year starts, then predict the test year; scored by AUC and lift against the base
rate. Nothing here is a rule written by hand: the patterns and their outcomes are learned from the data.

Train:  uv run python -m app.ml.patterns train
"""
from __future__ import annotations

import json
import sys
import time

import joblib
import numpy as np
import pandas as pd

from app.logging import log
from app.ml import forecast as fc

L = 30            # days in a shape
LV = 10           # days of volume in a shape
K = 50            # analogs per chart
N_PCA = 12
N_PATTERNS = 40
MIN_ADV = 1e6
OUTCOMES = ("direction_7d", "long_tp_first", "short_tp_first", "drop_risk_7d")
ARTIFACT = fc.MODELS / "patterns.joblib"
REPORT = fc.MODELS / "patterns_report.json"
TODAY = fc.MODELS / "patterns_today.json"
AFTER = 14        # days shown after an analog's date


# ------------------------------------------------------------------------------------------- shapes

def shapes(w, U: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Shape vectors for the universe rows (np.nonzero(U) order). Returns (V, valid mask, raw close paths)."""
    lc = np.log(w.close.to_numpy(dtype=np.float64))
    qv = w.quote_volume.to_numpy(dtype=np.float64)
    t_idx, s_idx = np.nonzero(U.to_numpy())
    back = np.arange(L - 1, -1, -1)
    ti = t_idx[:, None] - back[None, :]
    ok_t = ti[:, 0] >= 0
    ti = np.clip(ti, 0, None)
    path = lc[ti, s_idx[:, None]]                                  # n x L, oldest first
    dv = np.diff(path, axis=1)
    vol = np.nanstd(dv, axis=1)
    z = (path - np.nanmean(path, axis=1, keepdims=True)) / (np.nanstd(path, axis=1, keepdims=True) + 1e-9)
    strength = (path[:, -1] - path[:, 0]) / (vol * np.sqrt(L) + 1e-9)
    qv30 = pd.DataFrame(qv).rolling(30, min_periods=20).mean().to_numpy()
    tv = t_idx[:, None] - np.arange(LV - 1, -1, -1)[None, :]
    tv = np.clip(tv, 0, None)
    vrel = np.log((qv[tv, s_idx[:, None]] + 1) / (qv30[t_idx, s_idx][:, None] + 1))
    V = np.concatenate([z, 0.5 * np.clip(vrel, -3, 3), np.clip(strength, -5, 5)[:, None]], axis=1).astype(np.float32)
    valid = ok_t & np.isfinite(V).all(axis=1) & (vol > 0)
    return V, valid, path.astype(np.float32)


def name_pattern(centroid_z: np.ndarray, strength: float, vol_last: float) -> str:
    """A plain-English name from a pattern's average shape."""
    c = centroid_z
    third = L // 3
    a, b, e = c[:third].mean(), c[third:2 * third].mean(), c[2 * third:].mean()
    last5 = c[-1] - c[-6]
    trend = "uptrend" if strength > 1.0 else "downtrend" if strength < -1.0 else "range"
    if trend == "range":
        shape = "V-shaped recovery" if b < min(a, e) - 0.3 else "rounded top" if b > max(a, e) + 0.3 else "sideways range"
    else:
        shape = trend
    tail = ("sharp bounce in the last 5 days" if last5 > 0.8 else "pullback in the last 5 days" if last5 < -0.8 else
            "drifting up lately" if last5 > 0.3 else "drifting down lately" if last5 < -0.3 else "flat lately")
    volw = "volume surging" if vol_last > 0.35 else "volume drying up" if vol_last < -0.35 else "normal volume"
    return f"{shape.capitalize()}, {tail}, {volw}"


# ------------------------------------------------------------------------------------------- training

def _dataset(tier_adv: float = MIN_ADV):
    w = fc.load_wide()
    _, U = fc.features(w, tier_adv)
    ys = fc.labels(w)
    t_idx, s_idx = np.nonzero(U.to_numpy())
    V, valid, paths = shapes(w, U)
    Y = {k: ys[k].to_numpy()[t_idx, s_idx] for k in OUTCOMES}
    fwd7 = (w.close.ffill().shift(-7) / w.close - 1).to_numpy()[t_idx, s_idx]
    dates = U.index[t_idx]
    return w, U, V, valid, paths, Y, fwd7, dates, t_idx, s_idx


def _fit(Vtr: np.ndarray, Ytr: dict, seed: int = 0):
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.decomposition import PCA
    from sklearn.neighbors import NearestNeighbors

    pca = PCA(n_components=N_PCA, random_state=seed).fit(Vtr[np.random.default_rng(seed).choice(len(Vtr), min(len(Vtr), 200_000), replace=False)])
    Ztr = pca.transform(Vtr).astype(np.float32)
    km = MiniBatchKMeans(n_clusters=N_PATTERNS, random_state=seed, n_init=3, batch_size=4096).fit(Ztr)
    lab = km.labels_
    rates = {k: np.array([np.nanmean(Ytr[k][lab == c]) if (lab == c).any() else np.nan for c in range(N_PATTERNS)])
             for k in OUTCOMES}
    nn = NearestNeighbors(n_neighbors=K, algorithm="kd_tree").fit(Ztr)
    return pca, km, nn, Ztr, lab, rates


def _analog(nn, Zq: np.ndarray, Ytr: dict, chunk: int = 50_000) -> dict[str, np.ndarray]:
    out = {k: np.empty(len(Zq), dtype=np.float32) for k in OUTCOMES}
    for i in range(0, len(Zq), chunk):
        _, idx = nn.kneighbors(Zq[i:i + chunk])
        for k in OUTCOMES:
            out[k][i:i + chunk] = np.nanmean(Ytr[k][idx], axis=1)
    return out


def oos_features(V, valid, Y, dates, years=fc.TEST_YEARS, purge=fc.PURGE_DAYS):
    """Out-of-sample analog and pattern rates for every row of the test years (index only from earlier dates)."""
    n = len(V)
    feats = {f"analog_{k}": np.full(n, np.nan, dtype=np.float32) for k in OUTCOMES}
    feats |= {f"pattern_{k}": np.full(n, np.nan, dtype=np.float32) for k in OUTCOMES}
    known = valid & np.isfinite(Y["direction_7d"]) & np.isfinite(Y["long_tp_first"])
    first = years[0] - 1
    for yr in (first, *years):  # the year before the first test year too, so training rows have the features
        start = pd.Timestamp(f"{yr}-01-01")
        tr = known & (dates < start - pd.Timedelta(days=purge))
        te = valid & (dates >= start) & (dates < pd.Timestamp(f"{yr + 1}-01-01"))
        if tr.sum() < 20_000 or not te.any():
            continue
        Ytr = {k: Y[k][tr] for k in OUTCOMES}
        pca, km, nn, _, _, rates = _fit(V[tr], Ytr)
        Zte = pca.transform(V[te]).astype(np.float32)
        an = _analog(nn, Zte, Ytr)
        lab = km.predict(Zte)
        for k in OUTCOMES:
            feats[f"analog_{k}"][te] = an[k]
            feats[f"pattern_{k}"][te] = rates[k][lab]
        log.info("patterns_fold", year=yr, train=int(tr.sum()), test=int(te.sum()))
    return feats


def train() -> dict:
    t0 = time.time()
    w, U, V, valid, paths, Y, fwd7, dates, t_idx, s_idx = _dataset()
    log.info("patterns_dataset", rows=len(V), valid=int(valid.sum()))
    feats = oos_features(V, valid, Y, dates)
    report = {"trained_at": pd.Timestamp.now("UTC").isoformat(), "rows": int(valid.sum()), "shape_days": L,
              "analogs": K, "patterns": N_PATTERNS, "test_years": list(fc.TEST_YEARS), "evaluation": {}}
    # how well the learned patterns predicted each test year (they never saw it)
    test = valid & (dates >= pd.Timestamp(f"{fc.TEST_YEARS[0]}-01-01"))
    for k in OUTCOMES:
        y = Y[k]
        ok = test & np.isfinite(y)
        ev = {}
        for src in ("analog", "pattern"):
            p = feats[f"{src}_{k}"]
            m = ok & np.isfinite(p)
            base = float(np.nanmean(y[valid & (dates < pd.Timestamp(f"{fc.TEST_YEARS[0]}-01-01")) & np.isfinite(y)]))
            ev[src] = fc._cls_metrics(y[m], p[m], base)
            ev[src]["yearly_auc"] = []
            for yr in fc.TEST_YEARS:
                my = m & (dates >= pd.Timestamp(f"{yr}-01-01")) & (dates < pd.Timestamp(f"{yr + 1}-01-01"))
                if my.sum() > 1000 and len(np.unique(y[my])) == 2:
                    ev[src]["yearly_auc"].append({"year": yr, "auc": round(fc._score("binary", y[my], p[my]), 4)})
        report["evaluation"][k] = ev
        log.info("patterns_eval", outcome=k, analog_auc=ev["analog"].get("auc"), pattern_auc=ev["pattern"].get("auc"))
    # the served library and analog index: every chart whose outcome is known
    known = valid & np.isfinite(Y["direction_7d"]) & np.isfinite(Y["long_tp_first"])
    Ytr = {k: Y[k][known] for k in OUTCOMES}
    pca, km, nn, Ztr, lab, rates = _fit(V[known], Ytr)
    lib = []
    Vk = V[known]
    for c in range(N_PATTERNS):
        sel = lab == c
        cz = Vk[sel, :L].mean(axis=0)
        strength = float(Vk[sel, -1].mean())
        vol_last = float(Vk[sel, L:L + LV][:, -3:].mean() * 2)
        lib.append({"id": c, "name": name_pattern(cz, strength, vol_last), "count": int(sel.sum()),
                    "shape": [round(float(x), 3) for x in cz],
                    "rates": {k: round(float(rates[k][c]), 4) for k in OUTCOMES},
                    "median_fwd7_pct": round(float(np.nanmedian(fwd7[known][sel])) * 100, 2)})
    base = {k: round(float(np.nanmean(Ytr[k])), 4) for k in OUTCOMES}
    report |= {"library": lib, "base_rates": base, "seconds": round(time.time() - t0, 1)}
    meta = {"sym": np.asarray(U.columns[s_idx[known]]), "date": np.asarray(dates[known].strftime("%Y-%m-%d")),
            "fwd7": fwd7[known].astype(np.float32)}
    joblib.dump({"pca": pca, "km": km, "nn": nn, "Y": Ytr, "meta": meta, "rates": rates, "base": base,
                 "trained_at": report["trained_at"]}, ARTIFACT)
    _cache["art"] = None
    REPORT.write_text(json.dumps(report, indent=1, default=str))
    predict(w)
    return report


# ------------------------------------------------------------------------------------------- serving

_cache: dict = {"art": None, "today": None}


def _art():
    if _cache["art"] is None and ARTIFACT.exists():
        _cache["art"] = joblib.load(ARTIFACT)
    return _cache["art"]


def report() -> dict | None:
    return json.loads(REPORT.read_text()) if REPORT.exists() else None


def predict(w=None) -> dict:
    """Today's analogs and pattern for every covered coin (latest universe day within 3 days), with the five closest
    past charts and what happened after them."""
    art, rep = _art(), report()
    if art is None or rep is None:
        return {"error": "patterns not trained: run `python -m app.ml.patterns train`"}
    w = w if w is not None else fc.load_wide()
    _, U = fc.features(w, MIN_ADV)
    mask = U.copy()
    mask.loc[:] = False
    seen: set[str] = set()
    for d in U.index[-3:][::-1]:
        fresh = U.loc[d] & ~U.columns.isin(list(seen))
        mask.loc[d] = fresh
        seen |= set(U.columns[fresh.to_numpy()])
    t_idx, s_idx = np.nonzero(mask.to_numpy())
    V, valid, paths = shapes(w, mask)
    lib = {p["id"]: p for p in rep["library"]}
    sym_col = {s: j for j, s in enumerate(w.close.columns)}
    date_row = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(w.close.index)}
    lc = np.log(w.close.to_numpy(dtype=np.float64))
    rows = {}
    Z = art["pca"].transform(V[valid]).astype(np.float32) if valid.any() else np.zeros((0, N_PCA), np.float32)
    dist, idx = art["nn"].kneighbors(Z) if len(Z) else (np.zeros((0, K)), np.zeros((0, K), int))
    labs = art["km"].predict(Z) if len(Z) else []
    for j, i in enumerate(np.nonzero(valid)[0]):
        sym = str(U.columns[s_idx[i]])
        nb = idx[j]
        an = {k: round(float(np.nanmean(art["Y"][k][nb])), 4) for k in OUTCOMES}
        f7 = art["meta"]["fwd7"][nb]
        examples = []
        for r in nb[:5]:
            es, ed = str(art["meta"]["sym"][r]), str(art["meta"]["date"][r])
            col, row = sym_col.get(es), date_row.get(ed)
            if col is None or row is None:
                continue
            seg = lc[max(0, row - L + 1): row + AFTER + 1, col]
            base_v = lc[row, col]
            examples.append({"symbol": es, "date": ed, "fwd7_pct": round(float(art["meta"]["fwd7"][r]) * 100, 1),
                             "path": [round(float(np.exp(x - base_v) - 1) * 100, 2) for x in seg if np.isfinite(x)],
                             "split": min(L, row + 1)})
        cur = paths[i]
        pat = lib[int(labs[j])]
        rows[sym] = {"date": str(U.index[t_idx[i]].date()), "analog": an,
                     "analog_up_share": round(float(np.mean(f7 > 0)), 3),
                     "analog_median_fwd7_pct": round(float(np.nanmedian(f7)) * 100, 2),
                     "analog_p10_p90_fwd7_pct": [round(float(np.nanpercentile(f7, 10)) * 100, 1),
                                                 round(float(np.nanpercentile(f7, 90)) * 100, 1)],
                     "pattern": {"id": pat["id"], "name": pat["name"], "count": pat["count"], "rates": pat["rates"],
                                 "shape": pat["shape"], "median_fwd7_pct": pat["median_fwd7_pct"]},
                     "path": [round(float(np.exp(x - cur[-1]) - 1) * 100, 2) for x in cur],
                     "examples": examples}
    data = {"as_of": str(U.index[-1].date()), "coins": len(rows), "base_rates": art["base"], "rows": rows}
    TODAY.write_text(json.dumps(data))
    _cache["today"] = data
    return {"as_of": data["as_of"], "coins": len(rows)}


def live_features(w, mask: pd.DataFrame) -> dict[str, np.ndarray]:
    """Analog and pattern rates for the rows of `mask` (np.nonzero order) from the served index (all history)."""
    art = _art()
    n = int(mask.to_numpy().sum())
    out = {f"{src}_{k}": np.full(n, np.nan, dtype=np.float32) for src in ("analog", "pattern") for k in OUTCOMES}
    if art is None or not n:
        return out
    V, valid, _ = shapes(w, mask)
    if not valid.any():
        return out
    Z = art["pca"].transform(V[valid]).astype(np.float32)
    an = _analog(art["nn"], Z, art["Y"])
    lab = art["km"].predict(Z)
    for k in OUTCOMES:
        out[f"analog_{k}"][valid] = an[k]
        out[f"pattern_{k}"][valid] = art["rates"][k][lab]
    return out


def today() -> dict | None:
    if _cache["today"] is None and TODAY.exists():
        _cache["today"] = json.loads(TODAY.read_text())
    return _cache["today"]


LABELS = {"direction_7d": "up after 7 days", "long_tp_first": "a long hit 2R before its stop",
          "short_tp_first": "a short hit 2R before its stop", "drop_risk_7d": "a 2-ATR drop within 7 days"}


def for_symbol(symbol: str) -> dict:
    rep, td = report(), today()
    if not rep or not td:
        return {"available": False, "note": "The pattern models have not been trained yet."}
    row = td["rows"].get(symbol)
    ev = rep["evaluation"]
    acc = {k: {"analog_auc": ev[k]["analog"].get("auc"), "pattern_auc": ev[k]["pattern"].get("auc"),
               "analog_top_decile": ev[k]["analog"].get("top_decile_rate"),
               "analog_bottom_decile": ev[k]["analog"].get("bottom_decile_rate")} for k in OUTCOMES}
    if not row:
        return {"available": True, "covered": False, "as_of": td["as_of"], "accuracy": acc,
                "note": "Not covered: patterns are scored for coins with 90+ days of history and $1M+ a day."}
    return {"available": True, "covered": True, "symbol": symbol, "as_of": td["as_of"], "trained_at": rep["trained_at"],
            "charts_learned": rep["rows"], "base_rates": td["base_rates"], "labels": LABELS, "accuracy": acc, **row}


if __name__ == "__main__":
    from app.ml import patterns as _p  # through the importable module so the saved objects load in the backend

    res = _p.train() if (sys.argv[1] if len(sys.argv) > 1 else "train") == "train" else _p.predict()
    if "evaluation" in res:
        for k, ev in res["evaluation"].items():
            print(f"{k:16s} analog AUC {ev['analog'].get('auc')}  pattern AUC {ev['pattern'].get('auc')}  "
                  f"analog top/bottom decile {ev['analog'].get('top_decile_rate')}/{ev['analog'].get('bottom_decile_rate')} "
                  f"base {ev['analog'].get('base_rate')}")
        print(f"{res['rows']} charts, {res['seconds']}s")
    else:
        print(res)
