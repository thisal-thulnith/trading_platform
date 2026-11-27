"""Pre-registered candidate books exposed for a FORWARD paper test, without promotion.

Book C of docs/research/EXECUTION_PREREG_2026-11-11.md: LightGBM on the 18 factor ranks, target = centred
cross-sectional rank of the 7-day forward return, long–short k = 10, weekly rebalance anchored to 2021-01-01, fills at
the next open as taker orders, no band, lab-2 weights/caps/vol target/turnover limit. It did NOT pass the promotion
gate (deflated Sharpe); it is run forward to collect evidence the backtest window cannot give.

The frozen specification lives in data/quant_lab/candidates/<name>/spec.json. It is written once (the first call)
and never overwritten; every call runs from the file, so the forward test runs exactly what was frozen.

Model schedule (identical to model.walk_forward, the backtest's): a LightGBM is fitted at every quarter start q
(from 2021-01-01) on the rows dated <= q - h - 1 (labels realised before q, expanding window) and scores the dates of
that quarter. Fitted boosters are saved as model_<q>.txt and reused, never refitted. Everything is computed from the
panel truncated at ``as_of``: no data after as_of is read.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "data" / "quant_lab" / "candidates"
REPLAY_DAYS = 120


def _spec_c() -> dict:
    from app.quant.lab import model as qm
    from app.quant.lab import retest as rt
    from app.quant.lab.factors import FACTORS, SLIPPAGE_BUCKETS, TAKER_FEE
    cfg = rt.book_cfg("C", "taker", 0.0)
    b = rt.BOOKS["C"]
    spec = {
        "name": "C", "frozen": datetime.now(UTC).date().isoformat(),
        "prereg": "docs/research/EXECUTION_PREREG_2026-11-11.md", "status": "unvalidated candidate under forward test",
        "backtest": {"config": "C|taker|band0", "oos_sharpe_next_open": 1.06, "costs_x2": 0.84, "delay_1d": 1.17,
                     "vwap_fill_close_marked": 1.37, "promotion_gate": "not passed (not selected; the selected book A "
                     "failed the deflated-Sharpe test at N = 156)",
                     "report": "data/quant_lab/exec_retest_report.json"},
        "model": {"type": "lgbm", "params": qm.LGBM_PARAMS, "horizon_days": b["h"], "factors": FACTORS,
                  "features": "centred cross-sectional percentile rank of each factor in the universe, missing = 0",
                  "target": "centred cross-sectional rank of the forward h-day close-to-close return"},
        "training": {"schedule": "quarter starts (pandas 'QS') from 2021-01-01", "window": "expanding",
                     "rows": "dated <= quarter_start - (h + 1) days with a realised label",
                     "scores": "dates in [quarter_start, next quarter_start)"},
        "universe": {"min_history_days": 90, "min_adv30_usd": 5e6},
        "portfolio": asdict(cfg),
        "costs": {"taker_fee": TAKER_FEE, "slippage_by_adv30": [list(x) for x in SLIPPAGE_BUCKETS]},
        "execution": "decide at the close of D, fill at the open of D+1 (taker); rebalance when "
                     "(D - anchor) % rebalance_days == 0",
    }
    spec["spec_sha"] = hashlib.sha1(json.dumps({k: v for k, v in spec.items() if k != "frozen"}, sort_keys=True,
                                               default=str).encode()).hexdigest()[:12]
    return spec


SPECS = {"C": _spec_c}


def load_spec(name: str) -> dict:
    """The frozen spec; written on first use and never overwritten."""
    d = ROOT / name
    p = d / "spec.json"
    if p.exists():
        return json.loads(p.read_text())
    if name not in SPECS:
        raise KeyError(f"unknown candidate {name!r}")
    d.mkdir(parents=True, exist_ok=True)
    spec = SPECS[name]()
    p.write_text(json.dumps(spec, indent=1, default=str))
    return spec


def _quarter_model(spec: dict, ds, q, rd):
    """Booster for quarter q: loaded from model_<q>.txt, or fitted once on rows dated <= q - h - 1 and saved."""
    import lightgbm as lgb
    import numpy as np
    import pandas as pd

    from app.quant.lab import model as qm
    h = int(spec["model"]["horizon_days"])
    path = ROOT / spec["name"] / f"model_{pd.Timestamp(q).date()}.txt"
    if path.exists():
        return lgb.Booster(model_file=str(path))
    train = (rd <= pd.Timestamp(q) - pd.Timedelta(days=h + 1)) & np.isfinite(ds.y[h])
    if train.sum() < 1000:
        return None
    qm_params = qm.LGBM_PARAMS
    assert qm_params == spec["model"]["params"], "LGBM_PARAMS changed since the spec was frozen"
    booster = qm.fit(ds, "lgbm", h, train)["booster"]
    path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(path))
    meta = path.with_suffix(".json")
    meta.write_text(json.dumps({"quarter": str(pd.Timestamp(q).date()), "n_train_rows": int(train.sum()),
                                "trained_through": str(rd[train].max().date()), "spec_sha": spec["spec_sha"],
                                "fitted": datetime.now(UTC).isoformat()}))
    return booster


def candidate_book(name: str = "C", as_of: str | None = None) -> dict:
    """Today's book of a pre-registered, unpromoted candidate (same shape as ``live_ranking()``).

    Returns ``{as_of, data_through, generated, promoted: False, candidate, spec_sha, model, note, universe_size,
    factors_used, portfolio, rows}``; rows are ``{symbol, rank, score, weight, side, adv30_usd, vol30, factors}``.
    Weights are the replayed book after today's orders (on a rebalance day) or the current holdings otherwise."""
    import numpy as np
    import pandas as pd

    from app.quant.lab import data as qd
    from app.quant.lab import model as qm
    from app.quant.lab import portfolio as qp
    from app.quant.lab.factors import FACTOR_DOC, build_wide, compute_factors, universe
    from app.quant.lab.live import _portfolio_field

    spec = load_spec(name)
    panel = qd.load_panel()
    end = pd.Timestamp(as_of) if as_of else pd.Timestamp(panel["date"].max())
    w = build_wide(panel, qd.load_funding_daily(), end=end)          # nothing after as_of is read
    facs = compute_factors(w)
    uni = spec["universe"]
    univ = universe(w, uni["min_history_days"], uni["min_adv30_usd"])
    h = int(spec["model"]["horizon_days"])
    names = spec["model"]["factors"]
    ds = qm.build_dataset(w, facs, univ, horizons=(h,), names=names)
    rd = ds.row_dates
    t = len(w.dates) - 1
    lo = max(0, t - REPLAY_DAYS)
    pred = np.full(len(rd), np.nan)
    quarters = pd.date_range("2021-01-01", w.dates[t], freq="QS")
    used = []
    for i, q in enumerate(quarters):
        qend = quarters[i + 1] if i + 1 < len(quarters) else w.dates[t] + pd.Timedelta(days=1)
        rows = (rd >= max(q, w.dates[lo])) & (rd < qend)
        if not rows.any():
            continue
        booster = _quarter_model(spec, ds, q, rd)
        if booster is not None:
            pred[rows] = booster.predict(ds.X[rows])
            used.append(str(q.date()))
    S = ds.to_matrix(pred)
    cfg = qp.PortfolioConfig(**spec["portfolio"])
    mkt = qp.prepare_market(w, univ, fill=cfg.execution)
    book = qp.live_book(S, mkt, cfg, start=w.dates[lo])
    wts = book["book"]
    u = univ.iloc[t].to_numpy()
    syms = w.symbols[u]
    score = S[t, u]
    order = np.argsort(-np.nan_to_num(score, nan=-1e9), kind="stable")
    rows_out = []
    for rnk, i in enumerate(order, 1):
        s = syms[i]
        j = w.symbols.get_loc(s)
        rows_out.append({
            "symbol": s, "rank": rnk, "score": None if not np.isfinite(score[i]) else round(float(score[i]), 4),
            "weight": round(float(wts[j]), 4), "side": "long" if wts[j] > 0 else "short" if wts[j] < 0 else None,
            "adv30_usd": round(float(mkt.adv[t, j]), 0),
            "vol30": None if not np.isfinite(mkt.vol[t, j]) else round(float(mkt.vol[t, j]), 3),
            "factors": {f: (None if not np.isfinite(x := float(facs[f].iloc[t][s])) else round(x, 5))
                        for f in FACTOR_DOC},
        })
    # holdings outside today's universe (e.g. a coin that dropped out) are still part of the book
    held_out = [int(j) for j in np.flatnonzero(wts) if not u[j]]
    for j in held_out:
        rows_out.append({"symbol": w.symbols[j], "rank": None, "score": None, "weight": round(float(wts[j]), 4),
                         "side": "long" if wts[j] > 0 else "short", "adv30_usd": None, "vol30": None, "factors": {}})
    model_q = used[-1] if used else None
    meta = ROOT / name / f"model_{model_q}.json"
    nz = np.flatnonzero(wts)
    return {
        "as_of": str(end.date()), "data_through": str(w.dates[t].date()), "generated": datetime.now(UTC).isoformat(),
        "promoted": False, "candidate": name, "spec_sha": spec["spec_sha"], "spec_frozen": spec["frozen"],
        "model": {"model": spec["model"]["type"], "horizon_days": h, "quarter_model": model_q,
                  "trained_through": json.loads(meta.read_text())["trained_through"] if meta.exists() else None},
        "note": (f"unvalidated pre-registered candidate {name} ({spec['prereg']}) under forward paper test: it did not "
                 "pass the promotion gate (deflated Sharpe) and must not be treated as a validated signal"),
        "universe_size": int(u.sum()),
        "factors_used": [{"name": f, "sign_or_weight": None, "doc": FACTOR_DOC.get(f, "")} for f in names],
        "portfolio": _portfolio_field(cfg, wts, nz, book, w, t),
        "rows": rows_out,
    }
