"""Phase 3c — validation: walk-forward model ladder, configuration grid, CPCV, deflated Sharpe, PBO, stress, gate.

Statistics
- Probabilistic / deflated Sharpe ratio: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio". Sharpe ratios
  are per-period (daily); the expected maximum Sharpe of N unskilled trials is
  SR0 = sqrt(V[SR]) * ((1 - g) * Z^-1(1 - 1/N) + g * Z^-1(1 - 1/(N e))), g = Euler-Mascheroni, and
  DSR = Z((SR - SR0) sqrt(T - 1) / sqrt(1 - skew SR + (kurt - 1)/4 SR^2)); p = 1 - DSR. N and V[SR] come from EVERY
  configuration ever logged in data/quant_lab/trials.jsonl (unique keys, across runs).
- Probability of backtest overfitting: CSCV of Bailey, Borwein, Lopez de Prado & Zhu (2017). The T x N matrix of
  daily OOS returns of all grid configurations is cut into S blocks; for every half/half split the in-sample best
  configuration's out-of-sample relative rank w gives logit(w); PBO = P(logit <= 0).
- CPCV: model.cpcv (N=10 groups, k=2 test groups, purge = horizon, embargo = 14 days) -> 9 backtest paths -> a
  distribution of OOS Sharpe ratios.

Promotion gate (all required): DSR p < 0.05, PBO < 0.3, net Sharpe > equal-weight universe Sharpe over the same OOS
window, and costs x2 still has PSR(SR > 0) >= 0.95. Only then data/quant_lab/model.json is written.

CLI: ``uv run python -m app.quant.lab.validate``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from datetime import UTC, datetime
from itertools import combinations, product

import numpy as np
import pandas as pd
from scipy.stats import norm

from app.quant.lab import data as qd
from app.quant.lab import model as qm
from app.quant.lab import portfolio as qp
from app.quant.lab import research as qr
from app.quant.lab.factors import ANN, btc_regime, newey_west_t

TRIALS = qd.ROOT / "trials.jsonl"
REPORT = qd.ROOT / "validation_report.json"
MODEL_JSON = qd.ROOT / "model.json"
MODEL_LGBM = qd.ROOT / "model_lgbm.txt"
OOS_START = "2021-01-01"
EMC = 0.5772156649015329
VERSION = "lab-2"  # lab-1: first run (panel still had tokenized equities, no dust rule)

GRID = {"model": list(qm.MODELS), "h": [3, 7], "rebalance": [1, 7], "side": ["long", "long_short"], "k": [5, 10, 20]}
LADDER_T = 1.65   # paired NW t of the daily OOS IC difference needed to keep the more complex rung
GATE = {"dsr_p_max": 0.05, "pbo_max": 0.3, "psr_costs_x2_min": 0.95}


# --- statistics -------------------------------------------------------------------------------------------------
def sharpe(r) -> float:
    r = np.asarray(pd.Series(r).dropna(), dtype=float)
    return float(r.mean() / r.std(ddof=1)) if len(r) > 2 and r.std(ddof=1) > 0 else 0.0


def psr(sr: float, T: int, skew: float, kurt: float, sr0: float = 0.0) -> float:
    """Probabilistic Sharpe ratio P(true SR > sr0); per-period SR, kurt is raw (normal = 3)."""
    den = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if T < 3 or den <= 0:
        return float("nan")
    return float(norm.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)))


def expected_max_sr(sr_var: float, n_trials: int) -> float:
    if n_trials <= 1 or sr_var <= 0:
        return 0.0
    return float(math.sqrt(sr_var) * ((1 - EMC) * norm.ppf(1 - 1 / n_trials)
                                      + EMC * norm.ppf(1 - 1 / (n_trials * math.e))))


def deflated_sharpe(returns, n_trials: int, sr_var: float) -> dict:
    r = pd.Series(returns).dropna()
    sr = sharpe(r)
    T = len(r)
    sk = float(r.skew())
    ku = float(r.kurt() + 3)
    sr0 = expected_max_sr(sr_var, n_trials)
    d = psr(sr, T, sk, ku, sr0)
    return {"sr_daily": round(sr, 5), "sr_ann": round(sr * math.sqrt(ANN), 3), "T": T, "skew": round(sk, 3),
            "kurtosis": round(ku, 3), "n_trials": n_trials, "sr_var_trials": sr_var,
            "sr0_daily": round(sr0, 5), "sr0_ann": round(sr0 * math.sqrt(ANN), 3),
            "dsr": round(d, 4), "p_value": round(1 - d, 4), "psr_vs_0": round(psr(sr, T, sk, ku, 0.0), 4)}


def pbo_cscv(M: np.ndarray, S: int = 16) -> dict:
    """Probability of backtest overfitting via CSCV on a T x N matrix of strategy returns."""
    M = np.nan_to_num(np.asarray(M, dtype=float))
    T, N = M.shape
    blocks = np.array_split(np.arange(T), S)
    s1 = np.array([M[b].sum(axis=0) for b in blocks])
    s2 = np.array([(M[b] ** 2).sum(axis=0) for b in blocks])
    n = np.array([len(b) for b in blocks], dtype=float)
    lam, is_best, oos_best, oos_loss = [], [], [], []
    for comb in combinations(range(S), S // 2):
        m = np.zeros(S, dtype=bool)
        m[list(comb)] = True

        def sr(mask):
            nn = n[mask].sum()
            mu = s1[mask].sum(axis=0) / nn
            var = s2[mask].sum(axis=0) / nn - mu**2
            return mu / np.sqrt(np.maximum(var, 1e-18))

        a, b = sr(m), sr(~m)
        best = int(np.argmax(a))
        rank = (b < b[best]).sum() + 0.5 * ((b == b[best]).sum() - 1) + 1  # 1..N, ties averaged
        wbar = rank / (N + 1)
        lam.append(math.log(wbar / (1 - wbar)))
        is_best.append(a[best])
        oos_best.append(b[best])
        oos_loss.append(b[best] < 0)
    lam = np.array(lam)
    slope = float(np.polyfit(is_best, oos_best, 1)[0]) if len(set(np.round(is_best, 12))) > 1 else float("nan")
    return {"pbo": round(float((lam <= 0).mean()), 4), "n_combinations": int(len(lam)), "n_strategies": int(N),
            "blocks": S, "logit_median": round(float(np.median(lam)), 3),
            "prob_oos_loss": round(float(np.mean(oos_loss)), 4), "degradation_slope": round(slope, 3)}


def promotion_gate(ev: dict) -> tuple[bool, dict]:
    checks = {
        "dsr_significant": ev.get("dsr_p") is not None and ev["dsr_p"] < GATE["dsr_p_max"],
        "pbo_low": ev.get("pbo") is not None and ev["pbo"] < GATE["pbo_max"],
        "beats_ew_universe": ev.get("sharpe") is not None and ev.get("ew_sharpe") is not None
        and ev["sharpe"] > ev["ew_sharpe"],
        "survives_costs_x2": ev.get("psr_costs_x2") is not None and ev["psr_costs_x2"] >= GATE["psr_costs_x2_min"],
    }
    return all(checks.values()), checks


# --- trials log -------------------------------------------------------------------------------------------------
def trial_key(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def log_trial(run_id: str, kind: str, cfg: dict, r: pd.Series | None = None, extra: dict | None = None) -> dict:
    rec = {"run_id": run_id, "ts": datetime.now(UTC).isoformat(), "kind": kind, "key": trial_key(cfg),
           "config": cfg}
    if r is not None:
        r = r.dropna()
        rec.update({"days": int(len(r)), "start": str(r.index[0].date()) if len(r) else None,
                    "end": str(r.index[-1].date()) if len(r) else None, "sr_daily": round(sharpe(r), 6),
                    "sr_ann": round(sharpe(r) * math.sqrt(ANN), 4), "ann_return": round(float(r.mean() * ANN), 4)})
    if extra:
        rec.update(extra)
    qd.ROOT.mkdir(parents=True, exist_ok=True)
    with open(TRIALS, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def trials_summary(kind: str = "strategy") -> tuple[int, float]:
    """(number of distinct configurations ever tried, variance of their daily Sharpe ratios)."""
    if not TRIALS.exists():
        return 0, 0.0
    last = {}
    for line in TRIALS.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == kind and rec.get("sr_daily") is not None:
            last[rec["key"]] = rec["sr_daily"]
    v = np.array(list(last.values()), dtype=float)
    return len(v), float(v.var(ddof=1)) if len(v) > 1 else 0.0


# --- orchestration ----------------------------------------------------------------------------------------------
def _by_period(r: pd.Series, regime: pd.Series) -> dict:
    yrs = {int(y): qp.metrics(g) for y, g in r.groupby(r.index.year)}
    reg = regime.shift(1).reindex(r.index)  # regime known when the position was formed
    return {"by_year": {y: {"sharpe": m["sharpe"], "ann_return": m["ann_return"], "max_drawdown": m["max_drawdown"]}
                        for y, m in yrs.items()},
            "btc_above_200d": qp.metrics(r[reg == True]),   # noqa: E712
            "btc_below_200d": qp.metrics(r[reg == False])}  # noqa: E712


def _excl_best_month(r: pd.Series) -> dict:
    m = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    best = m.idxmax()
    rr = r[r.index.to_period("M") != best]
    return {"best_month": str(best), "best_month_return": round(float(m.max()), 4), **qp.metrics(rr)}


def portfolio_cfg(side: str, k: int, rebalance: int) -> qp.PortfolioConfig:
    return qp.PortfolioConfig(k=k, side=side, rebalance=rebalance, turnover_limit=1.0 if rebalance >= 7 else 0.4)


def run(oos_start: str = OOS_START, cpcv_all: bool = True) -> dict:
    t0 = time.time()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    timings = {}
    w, facs, univ = qr.load_inputs()
    tables = qr.ic_tables(facs, w, univ, horizons=(3, 7))
    ds = qm.build_dataset(w, facs, univ)
    mkt = qp.prepare_market(w, univ)
    regime = btc_regime(w)
    timings["inputs"] = round(time.time() - t0, 1)
    print(f"[{time.time() - t0:.0f}s] dataset rows={len(ds.t_idx)} dates={len(ds.dates)}", flush=True)

    # 1. walk-forward predictions for every rung and horizon
    preds, folds, ics = {}, {}, {}
    for h in (3, 7):
        for m in qm.MODELS:
            t1 = time.time()
            preds[(m, h)], folds[(m, h)] = qm.walk_forward(ds, m, h, tables[h], oos_start)
            ics[(m, h)] = qm.daily_ic(ds, preds[(m, h)], h)
            timings[f"wf_{m}_{h}"] = round(time.time() - t1, 1)
            print(f"[{time.time() - t0:.0f}s] walk-forward {m} h={h}: OOS IC {ics[(m, h)].mean():+.4f}", flush=True)

    # 2. ladder: keep a rung only if its OOS IC beats the incumbent with paired NW t >= LADDER_T
    ladder = {}
    for h in (3, 7):
        rungs = []
        for m in qm.MODELS:
            s = ics[(m, h)]
            rungs.append({"model": m, "h": h, "oos_ic_mean": round(float(s.mean()), 4),
                          "oos_ic_t_nw": round(newey_west_t(s, qr.nw_lags(h)), 2), "days": int(len(s)),
                          "ic_by_year": {int(y): round(float(v), 4) for y, v in s.groupby(s.index.year).mean().items()}})
            log_trial(run_id, "ladder_ic", {"model": m, "h": h, "oos_start": oos_start},
                      extra={"oos_ic_mean": float(s.mean())})
        best = "composite"
        for rg in rungs[1:]:
            a, b = ics[(best, h)], ics[(rg["model"], h)]
            j = pd.concat([a, b], axis=1, join="inner").dropna()
            diff = j.iloc[:, 1] - j.iloc[:, 0]
            t = newey_west_t(diff, qr.nw_lags(h))
            rg["vs"] = best
            rg["ic_diff"] = round(float(diff.mean()), 4)
            rg["ic_diff_t_nw"] = round(t, 2)
            rg["kept"] = bool(diff.mean() > 0 and t >= LADDER_T)
            if rg["kept"]:
                best = rg["model"]
        rungs[0]["kept"] = True
        ladder[h] = {"rungs": rungs, "winner": best}
    print(f"[{time.time() - t0:.0f}s] ladder winners: { {h: v['winner'] for h, v in ladder.items()} }", flush=True)

    # 3. configuration grid (every configuration is logged)
    results, rets = {}, {}
    for m, h, reb, side, k in product(GRID["model"], GRID["h"], GRID["rebalance"], GRID["side"], GRID["k"]):
        cfg = portfolio_cfg(side, k, reb)
        res = qp.simulate(ds.to_matrix(preds[(m, h)]), mkt, cfg, start=oos_start)
        key = f"{m}|h{h}|reb{reb}|{side}|k{k}"
        summ = qp.summarize(res)
        results[key] = {"model": m, "h": h, "rebalance": reb, "side": side, "k": k, **summ}
        rets[key] = res.returns
        log_trial(run_id, "strategy", {"model": m, "h": h, "oos_start": oos_start, "universe": "adv5M_hist90",
                                       "version": VERSION, **cfg.to_dict()}, res.returns)
    timings["grid"] = round(time.time() - t0, 1)
    print(f"[{time.time() - t0:.0f}s] grid of {len(results)} configurations done", flush=True)

    # 4. baselines over the same window
    base, base_r = {}, {}
    for kind in ("ew_universe", "btc_hold", "trend20"):
        s, cfg = qp.baseline_scores(w, mkt, kind)
        res = qp.simulate(s, mkt, cfg, start=oos_start)
        base[kind] = qp.summarize(res)
        base_r[kind] = res.returns

    # 5. selection = best OOS net Sharpe across the grid (this selection is what DSR/PBO deflate)
    # candidates are restricted to each horizon's ladder winner; every grid configuration still counts as a trial
    cands = [k_ for k_, v in results.items() if v["model"] == ladder[v["h"]]["winner"]]
    best_key = max(cands, key=lambda k_: results[k_]["sharpe"])
    b = results[best_key]
    bcfg = portfolio_cfg(b["side"], b["k"], b["rebalance"])
    bS = ds.to_matrix(preds[(b["model"], b["h"])])
    r_best = rets[best_key]
    stress = {}
    for name, cfg in (("costs_x2", qp.PortfolioConfig(**{**bcfg.to_dict(), "cost_mult": 2.0})),
                      ("lag_1d", qp.PortfolioConfig(**{**bcfg.to_dict(), "lag": 1})),
                      ("no_costs", qp.PortfolioConfig(**{**bcfg.to_dict(), "cost_mult": 0.0}))):
        res = qp.simulate(bS, mkt, cfg, start=oos_start)
        stress[name] = qp.metrics(res.returns)
        if name == "costs_x2":
            r_x2 = res.returns
    stress["excl_best_month"] = _excl_best_month(r_best)
    stress["periods"] = _by_period(r_best, regime)
    stress["ew_periods"] = _by_period(base_r["ew_universe"], regime)

    # 6. deflated Sharpe with every configuration ever tried
    n_trials, sr_var = trials_summary("strategy")
    dsr = deflated_sharpe(r_best, n_trials, sr_var)
    x2 = deflated_sharpe(r_x2, 1, 0.0)

    # 7. PBO over the grid
    M = pd.DataFrame(rets).fillna(0.0)
    pbo = pbo_cscv(M.to_numpy(), 16)
    print(f"[{time.time() - t0:.0f}s] best={best_key} SR={b['sharpe']} DSR p={dsr['p_value']} PBO={pbo['pbo']}",
          flush=True)

    # 8. CPCV: distribution of OOS Sharpe for the selected (model, h, portfolio) and each rung at that portfolio cfg
    cp = {}
    for m in (qm.MODELS if cpcv_all else (b["model"],)):
        t1 = time.time()
        paths, groups = qm.cpcv(ds, m, b["h"], tables[b["h"]])
        srs = []
        for p in paths:
            res = qp.simulate(ds.to_matrix(p), mkt, bcfg, start=groups[0][0])
            srs.append(qp.metrics(res.returns)["sharpe"])
        srs = np.array(srs)
        cp[m] = {"paths": len(srs), "sharpe_paths": [round(float(x), 3) for x in srs],
                 "mean": round(float(srs.mean()), 3), "std": round(float(srs.std(ddof=1)), 3),
                 "min": round(float(srs.min()), 3), "max": round(float(srs.max()), 3),
                 "share_positive": round(float((srs > 0).mean()), 3),
                 "groups": [[str(a.date()), str(b_.date())] for a, b_ in groups], "seconds": round(time.time() - t1, 1)}
        print(f"[{time.time() - t0:.0f}s] CPCV {m}: mean SR {srs.mean():.2f} (min {srs.min():.2f})", flush=True)

    # 9. promotion gate
    ev = {"sharpe": b["sharpe"], "ew_sharpe": base["ew_universe"]["sharpe"], "dsr_p": dsr["p_value"],
          "pbo": pbo["pbo"], "psr_costs_x2": x2["psr_vs_0"]}
    promoted, checks = promotion_gate(ev)
    decision = {"promoted": promoted, "checks": checks, "evidence": ev, "selected": best_key,
                "reason": ("all gates passed" if promoted else
                           "not promoted: failed " + ", ".join(k_ for k_, v in checks.items() if not v))}

    report = {
        "generated": datetime.now(UTC).isoformat(), "run_id": run_id, "version": VERSION, "oos_start": oos_start,
        "data_through": str(w.dates[-1].date()), "ladder": {str(h): v for h, v in ladder.items()},
        "folds": {f"{m}|h{h}": f for (m, h), f in folds.items()},
        "grid": results, "selected": {"key": best_key, **b, "config": bcfg.to_dict()},
        "baselines": base, "stress": stress, "dsr": dsr, "costs_x2_psr": x2, "pbo": pbo, "cpcv": cp,
        "decision": decision, "gate_thresholds": GATE, "ladder_rule": f"paired NW t >= {LADDER_T}",
        "equity_curves": {"strategy": qp.equity_curve(r_best), **{k_: qp.equity_curve(v) for k_, v in base_r.items()},
                          "strategy_costs_x2": qp.equity_curve(r_x2)},
        "timings_s": timings,
    }
    if promoted:
        report["model_file"] = str(MODEL_JSON)
        _write_model(ds, b, bcfg, tables, report)
    elif MODEL_JSON.exists() and _model_version() == VERSION:
        # a stale promotion of THIS pipeline must not survive a failed run (a model promoted by another pre-registered
        # pipeline, e.g. retest.py's execution re-test, is retired only by that pipeline)
        MODEL_JSON.rename(MODEL_JSON.with_suffix(".retired.json"))
    report["runtime_s"] = round(time.time() - t0, 1)
    REPORT.write_text(json.dumps(report, indent=1, default=str))
    return report


def _model_version() -> str | None:
    try:
        return json.loads(MODEL_JSON.read_text()).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def _write_model(ds: qm.Dataset, b: dict, cfg: qp.PortfolioConfig, tables: dict, report: dict,
                 version: str = VERSION) -> None:
    """Refit the selected rung on all labelled data and persist it with its evidence."""
    h = b["h"]
    rows = np.isfinite(ds.y[h])
    last = ds.row_dates[rows].max()
    tdates = ds.dates[ds.dates <= last]
    st = qm.fit(ds, b["model"], h, rows, tables[h], tdates)
    out = {"version": version, "created": datetime.now(UTC).isoformat(), "model": b["model"], "horizon_days": h,
           "factor_list": ds.factor_names, "portfolio": cfg.to_dict(), "trained_through": str(last.date()),
           "evidence": {k: report[k] for k in ("dsr", "pbo", "decision")} | {"selected": report["selected"]}}
    if st["model"] == "composite":
        out["signs"] = st["signs"]
    elif st["model"] == "ridge":
        out["coef"], out["intercept"] = st["coef"], st["intercept"]
    else:
        st["booster"].save_model(str(MODEL_LGBM))
        out["lgbm_model_file"] = str(MODEL_LGBM)
    MODEL_JSON.write_text(json.dumps(out, indent=1, default=str))


def to_markdown(rep: dict) -> str:
    L = ["## Model ladder (walk-forward OOS from " + rep["oos_start"] + ")", "",
         "| h | model | OOS IC | t(NW) | vs | IC diff | t diff | kept |", "|---|---|---|---|---|---|---|---|"]
    for h, v in rep["ladder"].items():
        for r in v["rungs"]:
            L.append(f"| {h} | {r['model']} | {r['oos_ic_mean']:+.4f} | {r['oos_ic_t_nw']:+.2f} | {r.get('vs', '-')} | "
                     f"{r.get('ic_diff', float('nan')):+.4f} | {r.get('ic_diff_t_nw', float('nan')):+.2f} | "
                     f"{'yes' if r.get('kept') else 'no'} |")
    s = rep["selected"]
    L += ["", "## Portfolio vs baselines (OOS, net of costs and funding)", "",
          "| strategy | Sharpe | ann. return | ann. vol | CAGR | max DD | turnover/day |", "|---|---|---|---|---|---|---|"]
    L.append(f"| selected: {s['key']} | {s['sharpe']:.2f} | {s['ann_return']:.1%} | {s['ann_vol']:.1%} | "
             f"{s['cagr']:.1%} | {s['max_drawdown']:.1%} | {s['avg_turnover_per_day']:.3f} |")
    for k, m in rep["baselines"].items():
        L.append(f"| {k} | {m['sharpe']:.2f} | {m['ann_return']:.1%} | {m['ann_vol']:.1%} | {m['cagr']:.1%} | "
                 f"{m['max_drawdown']:.1%} | {m['avg_turnover_per_day']:.3f} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Quant lab validation and promotion gate")
    ap.add_argument("--oos-start", default=OOS_START)
    ap.add_argument("--cpcv-selected-only", action="store_true")
    a = ap.parse_args(argv)
    rep = run(a.oos_start, cpcv_all=not a.cpcv_selected_only)
    print(to_markdown(rep))
    print(json.dumps({k: rep[k] for k in ("dsr", "pbo", "decision")}, indent=1, default=str))
    print(f"runtime {rep['runtime_s']}s -> {REPORT}")


if __name__ == "__main__":
    main()
