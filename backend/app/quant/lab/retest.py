"""Pre-registered re-tests of the lab's best book (run once each; every configuration is logged in trials.jsonl).

Step A — execution re-test, docs/research/EXECUTION_PREREG_2026-11-11.md:
  the decision made at the close of D is filled at the OPEN of D+1 (never at the signal close), taker or maker
  orders, with or without a no-trade band, for three books taken from the lab-2 grid's best rungs. The best of these
  pre-registered configurations goes through the promotion gate with N = every strategy configuration ever logged.

Step B — CTREND factor and conformal abstention, docs/research/CTREND_CONFORMAL_PREREG_2026-11-11.md.

CLI: ``uv run python -m app.quant.lab.retest exec`` (step A) / ``... retest ctrend_conformal`` (step B)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from datetime import UTC, datetime
from itertools import product

import numpy as np
import pandas as pd

from app.quant.lab import data as qd
from app.quant.lab import model as qm
from app.quant.lab import portfolio as qp
from app.quant.lab import research as qr
from app.quant.lab import validate as qv
from app.quant.lab.factors import ANN, btc_regime

OOS_START = qv.OOS_START
ANCHOR = "2021-01-01"
VERSION_A = "exec-1"
REPORT_A = qd.ROOT / "exec_retest_report.json"
PRED_CACHE = qd.ROOT / "wf_preds.npz"

# --- pre-registered configurations (docs/research/EXECUTION_PREREG_2026-11-11.md) --------------------------------
BOOKS = {
    "A": {"model": "lgbm", "h": 3, "rebalance": 1, "side": "long_short", "k": 5},    # lab-2 selected book
    "B": {"model": "lgbm", "h": 3, "rebalance": 7, "side": "long_short", "k": 5},    # same book, weekly
    "C": {"model": "lgbm", "h": 7, "rebalance": 7, "side": "long_short", "k": 10},   # best weekly rung, 7-day model
}
ORDER_TYPES = ("taker", "maker")
BANDS = (0.0, 0.02)
MAKER_FEE = 0.0002
MAKER_THROUGH_BPS = 10.0
STRESS_MAKER_THROUGH_BPS = 25.0
BOOT_BLOCK, BOOT_N = 10, 2000


def book_cfg(book: str, order: str = "taker", band: float = 0.0, **over) -> qp.PortfolioConfig:
    b = BOOKS[book]
    cfg = qp.PortfolioConfig(k=b["k"], side=b["side"], rebalance=b["rebalance"],
                             turnover_limit=1.0 if b["rebalance"] >= 7 else 0.4, execution="next_open",
                             order_type=order, band=band, anchor=ANCHOR, maker_fee=MAKER_FEE,
                             maker_through_bps=MAKER_THROUGH_BPS)
    return qp.PortfolioConfig(**{**cfg.__dict__, **over})


def config_name(book: str, order: str, band: float) -> str:
    return f"{book}|{order}|band{band:g}"


def trial_config(book: str, cfg: qp.PortfolioConfig, version: str = VERSION_A, **extra) -> dict:
    b = BOOKS[book]
    return {"model": b["model"], "h": b["h"], "oos_start": OOS_START, "universe": "adv5M_hist90", "version": version,
            "book": book, **extra, **cfg.to_dict()}


# --- helpers ---------------------------------------------------------------------------------------------------
def walk_forward_cached(ds: qm.Dataset, model: str, h: int, tag: str = "lab2", tables=None) -> np.ndarray:
    """Walk-forward predictions (validate.run's exact procedure), cached on disk by dataset shape and factor list."""
    sig = hashlib.sha1(json.dumps([tag, model, h, len(ds.t_idx), ds.factor_names, str(ds.dates[-1])]).encode())
    key = f"{tag}_{model}_{h}_{sig.hexdigest()[:10]}"
    cache = dict(np.load(PRED_CACHE)) if PRED_CACHE.exists() else {}
    if key in cache:
        return cache[key]
    pred, _ = qm.walk_forward(ds, model, h, tables, OOS_START)
    cache[key] = pred
    np.savez_compressed(PRED_CACHE, **cache)
    return pred


def evaluate(S: np.ndarray, mkt: qp.Market, cfg: qp.PortfolioConfig, regime: pd.Series, start: str = OOS_START,
             **kw) -> tuple[dict, pd.Series]:
    res = qp.simulate(S, mkt, cfg, start=start, **kw)
    out = qp.summarize(res)
    out["periods"] = qv._by_period(res.returns, regime)
    return out, res.returns


def block_bootstrap_sharpe_diff(a: pd.Series, b: pd.Series, block: int = BOOT_BLOCK, n: int = BOOT_N,
                                seed: int = 0) -> dict:
    """One-sided paired circular block bootstrap of Sharpe(a) - Sharpe(b) > 0 on common dates."""
    j = pd.concat([a, b], axis=1, join="inner").dropna().to_numpy()
    T = len(j)
    rng = np.random.default_rng(seed)

    def sr(x):
        s = x.std(axis=0, ddof=1)
        return np.where(s > 0, x.mean(axis=0) / np.where(s > 0, s, 1), 0.0)

    d0 = float(np.diff(sr(j)[::-1])[0]) * math.sqrt(ANN)  # sr(a) - sr(b)
    nb = math.ceil(T / block)
    le = 0
    for _ in range(n):
        st = rng.integers(0, T, nb)
        ix = ((st[:, None] + np.arange(block)[None, :]) % T).ravel()[:T]
        x = sr(j[ix])
        le += (x[0] - x[1]) <= 0
    return {"sharpe_diff_ann": round(d0, 3), "p_one_sided": round((1 + le) / (1 + n), 4), "days": T,
            "block": block, "resamples": n}


def _md_table(rows: list[dict], cols: list[tuple[str, str, str]]) -> str:
    L = ["| " + " | ".join(c[1] for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        L.append("| " + " | ".join(_fmt(r.get(c[0]), c[2]) for c in cols) + " |")
    return "\n".join(L)


def _fmt(v, f: str) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if f == "s":
        return str(v)
    if f == "%":
        return f"{v:.1%}"
    return format(v, f)


# --- step A ----------------------------------------------------------------------------------------------------
def run_exec() -> dict:
    t0 = time.time()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-exec-" + uuid.uuid4().hex[:6]
    timings: dict = {}
    w, facs, univ = qr.load_inputs()
    ds = qm.build_dataset(w, facs, univ)
    regime = btc_regime(w)
    mkts = {f: qp.prepare_market(w, univ, fill=f) for f in qp.FILLS}
    timings["inputs"] = round(time.time() - t0, 1)
    S = {}
    for h in sorted({b["h"] for b in BOOKS.values()}):
        t1 = time.time()
        S[h] = ds.to_matrix(walk_forward_cached(ds, "lgbm", h))
        timings[f"wf_lgbm_{h}"] = round(time.time() - t1, 1)
    print(f"[{time.time() - t0:.0f}s] inputs and walk-forward predictions ready", flush=True)

    # 1. the pre-registered configurations (each logged as a strategy trial) and their stress tests (logged as stress)
    results, rets, stress_r = {}, {}, {}
    t1 = time.time()
    for book, order, band in product(BOOKS, ORDER_TYPES, BANDS):
        name = config_name(book, order, band)
        h = BOOKS[book]["h"]
        cfg = book_cfg(book, order, band)
        summ, r = evaluate(S[h], mkts["next_open"], cfg, regime)
        qv.log_trial(run_id, "strategy", trial_config(book, cfg), r)
        st = {}
        variants = {"costs_x2": (book_cfg(book, order, band, cost_mult=2.0), "next_open"),
                    "delay_1d": (book_cfg(book, order, band, lag=1), "next_open"),
                    "vwap_fill": (book_cfg(book, order, band, execution="next_vwap"), "next_vwap")}
        if order == "maker":
            variants["maker_through_25bps"] = (book_cfg(book, order, band, maker_through_bps=STRESS_MAKER_THROUGH_BPS),
                                               "next_open")
        for sname, (scfg, fill) in variants.items():
            res = qp.simulate(S[h], mkts[fill], scfg, start=OOS_START)
            m = qp.summarize(res)
            m["psr_vs_0"] = qv.deflated_sharpe(res.returns, 1, 0.0)["psr_vs_0"]
            st[sname] = m
            stress_r[(name, sname)] = res.returns
            qv.log_trial(run_id, "stress", trial_config(book, scfg, stress=sname, of=name), res.returns)
        st["excl_best_month"] = qv._excl_best_month(r)
        results[name] = {"book": book, "order_type": order, "band": band, **BOOKS[book], **summ, "stress": st,
                         "config": cfg.to_dict()}
        rets[name] = r
        print(f"[{time.time() - t0:.0f}s] {name}: SR {summ['sharpe']:.2f} turnover {summ['avg_turnover_per_day']:.3f} "
              f"costs {summ['avg_cost_per_year']:.1%} | x2 {st['costs_x2']['sharpe']:.2f} "
              f"delay {st['delay_1d']['sharpe']:.2f} vwap {st['vwap_fill']['sharpe']:.2f}", flush=True)
    timings["configs"] = round(time.time() - t1, 1)

    # 2. reference: the same books executed at the signal close (lab-2 numbers, already logged by lab-2)
    reference = {}
    for book in BOOKS:
        b = BOOKS[book]
        cfg = qv.portfolio_cfg(b["side"], b["k"], b["rebalance"])
        m, _ = evaluate(S[b["h"]], mkts["close"], cfg, regime)
        reference[book] = {k: m[k] for k in ("sharpe", "cagr", "max_drawdown", "avg_turnover_per_day",
                                               "avg_cost_per_year", "avg_funding_per_year")}

    # 3. baselines under the same next-open execution (taker)
    base = {}
    for kind in ("ew_universe", "btc_hold"):
        s, cfg = qp.baseline_scores(w, mkts["next_open"], kind)
        cfg = qp.PortfolioConfig(**{**cfg.__dict__, "execution": "next_open"})
        m, _ = evaluate(s, mkts["next_open"], cfg, regime)
        base[kind] = {k: v for k, v in m.items() if k != "periods"}

    # 4. selection, DSR with the updated N, PBO over the pre-registered set, costs x2, execution realism
    best = max(results, key=lambda k: results[k]["sharpe"])
    b = results[best]
    r_best = rets[best]
    n_trials, sr_var = qv.trials_summary("strategy")
    dsr = qv.deflated_sharpe(r_best, n_trials, sr_var)
    M = pd.DataFrame(rets).fillna(0.0)
    pbo = qv.pbo_cscv(M.to_numpy(), 16)
    psr_x2 = b["stress"]["costs_x2"]["psr_vs_0"]
    psr_vwap = b["stress"]["vwap_fill"]["psr_vs_0"]
    print(f"[{time.time() - t0:.0f}s] selected {best}: SR {b['sharpe']} N={n_trials} DSR p={dsr['p_value']} "
          f"PBO={pbo['pbo']}", flush=True)

    # 5. CPCV paths of the selected model, traded with the selected execution settings
    t1 = time.time()
    bh = BOOKS[b["book"]]["h"]
    paths, groups = qm.cpcv(ds, "lgbm", bh, None)
    bcfg = qp.PortfolioConfig(**b["config"])
    srs = np.array([qp.metrics(qp.simulate(ds.to_matrix(p), mkts["next_open"], bcfg, start=groups[0][0]).returns)
                    ["sharpe"] for p in paths])
    cp = {"paths": len(srs), "sharpe_paths": [round(float(x), 3) for x in srs], "mean": round(float(srs.mean()), 3),
          "min": round(float(srs.min()), 3), "max": round(float(srs.max()), 3),
          "share_positive": round(float((srs > 0).mean()), 3)}
    timings["cpcv"] = round(time.time() - t1, 1)

    # 6. gate (the lab gate + the pre-registered execution-realism condition)
    ev = {"sharpe": b["sharpe"], "ew_sharpe": base["ew_universe"]["sharpe"], "dsr_p": dsr["p_value"],
          "pbo": pbo["pbo"], "psr_costs_x2": psr_x2, "psr_vwap_fill": psr_vwap}
    ok, checks = qv.promotion_gate(ev)
    checks["survives_vwap_fill"] = psr_vwap is not None and psr_vwap >= qv.GATE["psr_costs_x2_min"]
    promoted = ok and checks["survives_vwap_fill"]
    decision = {"promoted": promoted, "checks": checks, "evidence": ev, "selected": best,
                "reason": ("all gates passed" if promoted else
                           "not promoted: failed " + ", ".join(k for k, v in checks.items() if not v))}

    report = {"generated": datetime.now(UTC).isoformat(), "run_id": run_id, "version": VERSION_A,
              "prereg": "docs/research/EXECUTION_PREREG_2026-11-11.md", "oos_start": OOS_START,
              "data_through": str(w.dates[-1].date()), "configs": results, "reference_same_close": reference,
              "baselines_next_open": base, "selected": {"key": best, **b}, "dsr": dsr, "pbo": pbo, "cpcv": cp,
              "decision": decision, "n_strategy_trials": n_trials, "timings_s": timings,
              "equity_curves": {"selected": qp.equity_curve(r_best)}}
    if promoted:
        promote(ds, b, report)
    report["runtime_s"] = round(time.time() - t0, 1)
    REPORT_A.write_text(json.dumps(report, indent=1, default=str))
    return report


def recheck_marks() -> dict:
    """Post-run correction of step A's VWAP stress: mark at closes, trade at the D+1 VWAP (the first run marked
    VWAP-to-VWAP, which smooths daily returns and overstates Sharpe). Also re-marks next-open fills at closes as a
    consistency check. Logged as stress (no new strategy configuration); G5 is re-evaluated on the corrected number."""
    rep = json.loads(REPORT_A.read_text())
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-exec-recheck-" + uuid.uuid4().hex[:6]
    w, facs, univ = qr.load_inputs()
    ds = qm.build_dataset(w, facs, univ)
    S = {h: ds.to_matrix(walk_forward_cached(ds, "lgbm", h)) for h in (3, 7)}
    mk = {f: qp.prepare_market(w, univ, fill=f, mark="close") for f in ("next_open", "next_vwap")}
    out = {}
    for name, r in rep["configs"].items():
        cfg = qp.PortfolioConfig(**r["config"])
        row = {}
        for sname, fill in (("vwap_fill_close_marked", "next_vwap"), ("next_open_close_marked", "next_open")):
            c = qp.PortfolioConfig(**{**cfg.__dict__, "execution": fill})
            res = qp.simulate(S[int(r["h"])], mk[fill], c, start=OOS_START)
            m = qp.summarize(res)
            m["psr_vs_0"] = qv.deflated_sharpe(res.returns, 1, 0.0)["psr_vs_0"]
            row[sname] = m
            qv.log_trial(run_id, "stress", trial_config(r["book"], c, stress=sname, of=name, mark="close"),
                         res.returns)
        out[name] = row
        print(f"{name}: VWAP (close-marked) {row['vwap_fill_close_marked']['sharpe']:.2f} "
              f"[as first run {r['stress']['vwap_fill']['sharpe']:.2f}] | next-open close-marked "
              f"{row['next_open_close_marked']['sharpe']:.2f} [open-marked {r['sharpe']:.2f}]", flush=True)
    sel = rep["selected"]["key"]
    psr = out[sel]["vwap_fill_close_marked"]["psr_vs_0"]
    d = rep["decision"]
    d["checks"]["survives_vwap_fill"] = bool(psr >= qv.GATE["psr_costs_x2_min"])
    d["evidence"]["psr_vwap_fill_as_first_run"] = d["evidence"]["psr_vwap_fill"]
    d["evidence"]["psr_vwap_fill"] = psr
    d["promoted"] = d["promoted"] and d["checks"]["survives_vwap_fill"]
    d["reason"] = ("all gates passed" if d["promoted"] else
                   "not promoted: failed " + ", ".join(k for k, v in d["checks"].items() if not v))
    rep["stress_close_marked"] = out
    rep["recheck_note"] = recheck_marks.__doc__
    REPORT_A.write_text(json.dumps(rep, indent=1, default=str))
    return rep


def promote(ds: qm.Dataset, b: dict, report: dict) -> None:
    """Write model.json through validate's promotion path with the promoted execution settings."""
    cfg = qp.PortfolioConfig(**b["config"])
    sel = {"model": b["model"], "h": b["h"]}
    qv._write_model(ds, sel, cfg, {}, {"dsr": report["dsr"], "pbo": report["pbo"], "decision": report["decision"],
                                         "selected": report["selected"]}, version=report["version"])
    report["model_file"] = str(qv.MODEL_JSON)


def exec_markdown(rep: dict) -> str:
    rows = []
    for name, r in rep["configs"].items():
        st = r["stress"]
        rows.append({"name": name, "sharpe": r["sharpe"], "cagr": r["cagr"], "dd": r["max_drawdown"],
                     "to": r["avg_turnover_per_day"], "cost": r["avg_cost_per_year"],
                     "fund": r["avg_funding_per_year"], "fill": r.get("fill_rate"),
                     "x2": st["costs_x2"]["sharpe"], "d1": st["delay_1d"]["sharpe"], "vw": st["vwap_fill"]["sharpe"],
                     "m25": st.get("maker_through_25bps", {}).get("sharpe")})
    L = [_md_table(rows, [("name", "config", "s"), ("sharpe", "Sharpe", ".2f"), ("cagr", "CAGR", "%"),
                          ("dd", "max DD", "%"), ("to", "turnover/day", ".3f"), ("cost", "costs/yr", "%"),
                          ("fund", "funding/yr", "%"), ("fill", "maker fill rate", ".1%"), ("x2", "costs x2", ".2f"),
                          ("d1", "+1 day delay", ".2f"), ("vw", "VWAP fill", ".2f"), ("m25", "maker 25 bp", ".2f")])]
    years = sorted({y for r in rep["configs"].values() for y in r["periods"]["by_year"]})
    L += ["", "| config | " + " | ".join(map(str, years)) + " | BTC>200d | BTC<200d |",
          "|---|" + "---|" * (len(years) + 2)]
    for name, r in rep["configs"].items():
        p = r["periods"]
        L.append(f"| {name} | " + " | ".join(f"{p['by_year'].get(y, {}).get('sharpe', float('nan')):.2f}"
                                             for y in years)
                 + f" | {p['btc_above_200d']['sharpe']:.2f} | {p['btc_below_200d']['sharpe']:.2f} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Pre-registered quant lab re-tests")
    ap.add_argument("step", choices=["exec", "exec_recheck", "ctrend_conformal"])
    a = ap.parse_args(argv)
    if a.step == "exec_recheck":
        t0 = time.time()
        rep = recheck_marks()
        print(json.dumps(rep["decision"], indent=1))
        print(f"runtime {time.time() - t0:.0f}s")
        return
    if a.step == "exec":
        rep = run_exec()
        print(exec_markdown(rep))
        print(json.dumps({k: rep[k] for k in ("reference_same_close", "baselines_next_open", "dsr", "pbo", "cpcv",
                                              "decision")}, indent=1, default=str))
    else:
        from app.quant.lab import retest_b
        rep = retest_b.run()
        print(retest_b.to_markdown(rep))
    print(f"runtime {rep['runtime_s']}s")


if __name__ == "__main__":
    main()
