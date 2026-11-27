"""Step B of the pre-registered re-tests: CTREND and conformal abstention.

Pre-registration: docs/research/CTREND_CONFORMAL_PREREG_2026-11-11.md. Base book = step A's selected configuration
(read from data/quant_lab/exec_retest_report.json). Every portfolio configuration is logged as a ``strategy`` trial;
model-ladder comparisons as ``ladder_ic``; stresses as ``stress``.

CLI: ``uv run python -m app.quant.lab.retest ctrend_conformal``
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.quant.lab import conformal as qc
from app.quant.lab import ctrend as ct
from app.quant.lab import data as qd
from app.quant.lab import model as qm
from app.quant.lab import portfolio as qp
from app.quant.lab import research as qr
from app.quant.lab import retest as rt
from app.quant.lab import validate as qv
from app.quant.lab.factors import FACTORS, btc_regime, cost_rate, forward_returns, newey_west_t, nw_lags, rank_ic

REPORT_B = qd.ROOT / "ctrend_conformal_report.json"
CTREND_CACHE = qd.ROOT / "ctrend_wf.csv.gz"
VERSION_CT = "ctrend-1"
VERSION_CF = "conformal-1"
CT_PASS_T = 3.0
CT_YEARS = (2021, 2022, 2023, 2024, 2025)
COVERAGE_ALPHAS = (0.1, 0.25, 0.5)
BOOK_ALPHAS = (0.25, 0.5)
COVERAGE_TOL = 0.03


def _stresses(run_id: str, S, mkts, cfg: qp.PortfolioConfig, book: str, version: str, of: str, start, extra: dict,
              masks: tuple = (None, None)) -> tuple[dict, pd.Series]:
    out, r_x2 = {}, None
    for sname, scfg, fill in (("costs_x2", qp.PortfolioConfig(**{**cfg.__dict__, "cost_mult": 2.0}), "next_open"),
                              ("delay_1d", qp.PortfolioConfig(**{**cfg.__dict__, "lag": 1}), "next_open"),
                              ("vwap_fill", qp.PortfolioConfig(**{**cfg.__dict__, "execution": "next_vwap"}),
                               "next_vwap")):
        res = qp.simulate(S, mkts[fill], scfg, start=start, long_ok=masks[0], short_ok=masks[1])
        m = qp.summarize(res)
        m["psr_vs_0"] = qv.deflated_sharpe(res.returns, 1, 0.0)["psr_vs_0"]
        out[sname] = m
        qv.log_trial(run_id, "stress", rt.trial_config(book, scfg, version=version, stress=sname, of=of, **extra),
                     res.returns)
        if sname == "costs_x2":
            r_x2 = res.returns
    return out, r_x2


def run() -> dict:
    t0 = time.time()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-b-" + uuid.uuid4().hex[:6]
    timings: dict = {}
    rep_a = json.loads(rt.REPORT_A.read_text())
    sel = rep_a["selected"]
    book, sel_name = sel["book"], sel["key"]
    cfg = qp.PortfolioConfig(**sel["config"])
    h = int(sel["h"])
    w, facs, univ = qr.load_inputs()
    regime = btc_regime(w)
    tables = qr.ic_tables(facs, w, univ, horizons=(1, 3, 7))
    ds = qm.build_dataset(w, facs, univ)
    mkts = {"next_open": qp.prepare_market(w, univ, fill="next_open"),
            "next_vwap": qp.prepare_market(w, univ, fill="next_vwap", mark="close")}   # VWAP marked at closes
    timings["inputs"] = round(time.time() - t0, 1)

    # ---------------------------------------------------------------- CTREND
    t1 = time.time()
    if CTREND_CACHE.exists():
        ctf = pd.read_csv(CTREND_CACHE, index_col=0, parse_dates=True).reindex(index=w.dates, columns=w.symbols)
        fits = json.loads((qd.ROOT / "ctrend_fits.json").read_text())
    else:
        ctf, fits = ct.walk_forward(w, univ)
        ctf.to_csv(CTREND_CACHE)
        (qd.ROOT / "ctrend_fits.json").write_text(json.dumps(fits))
    timings["ctrend_walk_forward"] = round(time.time() - t1, 1)
    print(f"[{time.time() - t0:.0f}s] CTREND: {len(fits)} monthly fits", flush=True)
    ct_tables = {hh: pd.DataFrame({"ctrend": rank_ic(ctf, forward_returns(w, hh), univ)}) for hh in (1, 3, 7)}
    r1 = qr.next_day_returns(w)
    costs = cost_rate(w)
    row_all = qr.factor_row("ctrend", ctf, ct_tables, univ, regime, r1, costs,
                            qr.screen(ct_tables[7]), "CTREND (elastic net of 20 technical indicators, walk-forward)")
    oos = ctf.index >= rt.OOS_START
    ct_oos = ctf.where(pd.DataFrame(np.repeat(oos[:, None], ctf.shape[1], 1), index=ctf.index, columns=ctf.columns))
    ct_tables_oos = {hh: v.loc[rt.OOS_START:] for hh, v in ct_tables.items()}
    row_oos = qr.factor_row("ctrend", ct_oos, ct_tables_oos, univ, regime, r1, costs, qr.screen(ct_tables_oos[7]),
                            "CTREND, OOS window only")
    ls = qr.quintile_ls(ct_oos, univ, r1, costs, 7, sign=1, series=True)["ls_net_series"]
    ls_by_year = {int(y): round(float(g.mean() * 365), 4) for y, g in ls.groupby(ls.index.year)}
    s7 = ct_tables_oos[7]["ctrend"].dropna()
    ct_t = newey_west_t(s7, nw_lags(7))
    pos_years = sum(1 for y in CT_YEARS if ls_by_year.get(y, -1) > 0)
    ct_pass = {"ic7_t_nw": round(ct_t, 2), "t_ok": bool(ct_t > CT_PASS_T), "ls_weekly_net_by_year": ls_by_year,
               "positive_years_2021_2025": pos_years, "years_ok": pos_years >= 4}
    ct_pass["passes"] = ct_pass["t_ok"] and ct_pass["years_ok"]
    # orientation is fixed a priori: CTREND predicts the return rank, so sign +1 (no in-sample orientation)

    # ladder with CTREND added as a factor
    t1 = time.time()
    facs_ct = {**facs, "ctrend": ctf}
    names_ct = FACTORS + ["ctrend"]
    ds_ct = qm.build_dataset(w, facs_ct, univ, names=names_ct)
    ladder = {}
    preds_ct = {}
    for hh in (3, 7):
        tab_ct = tables[hh].join(ct_tables[hh])
        for m in qm.MODELS:
            p0 = rt.walk_forward_cached(ds, m, hh, "lab2", tables[hh])
            p1 = rt.walk_forward_cached(ds_ct, m, hh, "ctrend", tab_ct)
            preds_ct[(m, hh)] = p1
            a, b = qm.daily_ic(ds, p0, hh), qm.daily_ic(ds_ct, p1, hh)
            j = pd.concat([a, b], axis=1, join="inner").dropna()
            diff = j.iloc[:, 1] - j.iloc[:, 0]
            t = newey_west_t(diff, qr.nw_lags(hh))
            ladder[f"{m}|h{hh}"] = {"ic_without": round(float(a.mean()), 4), "ic_with": round(float(b.mean()), 4),
                                    "diff": round(float(diff.mean()), 4), "t_nw": round(t, 2),
                                    "improves": bool(diff.mean() > 0 and t >= qv.LADDER_T)}
            qv.log_trial(run_id, "ladder_ic", {"model": m, "h": hh, "oos_start": rt.OOS_START, "factors": "+ctrend",
                                               "version": VERSION_CT}, extra={"oos_ic_mean": float(b.mean())})
            print(f"[{time.time() - t0:.0f}s] ladder {m} h={hh}: IC {a.mean():+.4f} -> {b.mean():+.4f} (t {t:.2f})",
                  flush=True)
    timings["ctrend_ladder"] = round(time.time() - t1, 1)

    books, rets, stresses, x2 = {}, {}, {}, {}
    S_ct = ds_ct.to_matrix(preds_ct[("lgbm", h)])
    name = f"{sel_name}|+ctrend"
    summ, r = rt.evaluate(S_ct, mkts["next_open"], cfg, regime)
    qv.log_trial(run_id, "strategy", rt.trial_config(book, cfg, version=VERSION_CT, factors="+ctrend"), r)
    stresses[name], x2[name] = _stresses(run_id, S_ct, mkts, cfg, book, VERSION_CT, name, rt.OOS_START,
                                         {"factors": "+ctrend"})
    books[name], rets[name] = summ, r

    # ---------------------------------------------------------------- conformal abstention
    t1 = time.time()
    S = ds.to_matrix(rt.walk_forward_cached(ds, "lgbm", h, "lab2"))
    P = qc.centred_rank(S)
    Y = ds.to_matrix(ds.y[h])
    iv = qc.intervals(P, Y, h, alphas=COVERAGE_ALPHAS)
    first = int(np.argmax(np.isfinite(iv["q"][0.5])))
    cstart = w.dates[first]
    dates_m = pd.DatetimeIndex(np.repeat(w.dates.to_numpy()[:, None], P.shape[1], 1).ravel())
    cov = {}
    for a in COVERAGE_ALPHAS:
        lo, hi = iv["lower"][a], iv["upper"][a]
        c = qc.coverage(Y.ravel(), lo.ravel(), hi.ravel(), dates_m)
        lo_ok, sh_ok = qc.abstention_masks(lo, hi)
        has = np.isfinite(lo) & np.isfinite(P)
        c.update({"target": 1 - a, "within_tolerance": bool(c["coverage"] is not None
                                                             and abs(c["coverage"] - (1 - a)) <= COVERAGE_TOL),
                  "share_long_ok": round(float(lo_ok[has].mean()), 4), "share_short_ok": round(float(sh_ok[has].mean()), 4),
                  "q_median": round(float(np.nanmedian(iv["q"][a])), 3)})
        cov[str(a)] = c
    cov["sigma_median"] = round(float(np.nanmedian(iv["sigma"])), 4)
    timings["conformal_intervals"] = round(time.time() - t1, 1)
    print(f"[{time.time() - t0:.0f}s] conformal from {cstart.date()}: "
          + ", ".join(f"a={a} cov {cov[str(a)]['coverage']}" for a in COVERAGE_ALPHAS), flush=True)

    base_summ, base_r = rt.evaluate(S, mkts["next_open"], cfg, regime, start=str(cstart.date()))
    # control: the same book without abstention over the same window (already logged in step A on a longer window;
    # this window-restricted copy is logged as a stress so it does not add a trial)
    qv.log_trial(run_id, "stress", rt.trial_config(book, cfg, version=VERSION_CF, stress="no_abstention_same_window",
                                                   of=sel_name), base_r)
    conf = {"window_start": str(cstart.date()), "coverage": cov, "control_no_abstention": base_summ, "books": {}}
    for a in BOOK_ALPHAS:
        lo_ok, sh_ok = qc.abstention_masks(iv["lower"][a], iv["upper"][a])
        name = f"{sel_name}|conformal_a{a}"
        summ, r = rt.evaluate(S, mkts["next_open"], cfg, regime, start=str(cstart.date()), long_ok=lo_ok,
                              short_ok=sh_ok)
        qv.log_trial(run_id, "strategy", rt.trial_config(book, cfg, version=VERSION_CF, conformal_alpha=a,
                                                         calib_days=qc.CALIB_DAYS, sigma_days=qc.SIGMA_DAYS), r)
        stresses[name], x2[name] = _stresses(run_id, S, mkts, cfg, book, VERSION_CF, name, str(cstart.date()),
                                             {"conformal_alpha": a}, masks=(lo_ok, sh_ok))
        boot = rt.block_bootstrap_sharpe_diff(r, base_r)
        summ["vs_no_abstention"] = {**boot, "beats": bool(boot["sharpe_diff_ann"] > 0 and boot["p_one_sided"] < 0.05)}
        conf["books"][str(a)] = summ
        books[name], rets[name] = summ, r
        print(f"[{time.time() - t0:.0f}s] {name}: SR {summ['sharpe']:.2f} vs {base_summ['sharpe']:.2f} "
              f"(p {boot['p_one_sided']})", flush=True)

    # ---------------------------------------------------------------- gate for each new book
    n_trials, sr_var = qv.trials_summary("strategy")
    allr = {k: pd.Series(v) for k, v in _step_a_returns(S, ds, mkts, rep_a, regime).items()} | rets
    common = max(s.index[0] for s in allr.values())
    M = pd.DataFrame({k: v[v.index >= common] for k, v in allr.items()}).fillna(0.0)
    pbo = qv.pbo_cscv(M.to_numpy(), 16)
    ew = {}
    for start in {rt.OOS_START, str(cstart.date())}:
        s_, c_ = qp.baseline_scores(w, mkts["next_open"], "ew_universe")
        c_ = qp.PortfolioConfig(**{**c_.__dict__, "execution": "next_open"})
        ew[start] = qp.metrics(qp.simulate(s_, mkts["next_open"], c_, start=start).returns)["sharpe"]
    gates = {}
    for name, summ in books.items():
        st = stresses[name]
        start = rt.OOS_START if name.endswith("+ctrend") else str(cstart.date())
        dsr = qv.deflated_sharpe(rets[name], n_trials, sr_var)
        ev = {"sharpe": summ["sharpe"], "ew_sharpe": ew[start], "dsr_p": dsr["p_value"], "pbo": pbo["pbo"],
              "psr_costs_x2": st["costs_x2"]["psr_vs_0"], "psr_vwap_fill": st["vwap_fill"]["psr_vs_0"]}
        ok, checks = qv.promotion_gate(ev)
        checks["survives_vwap_fill"] = ev["psr_vwap_fill"] >= qv.GATE["psr_costs_x2_min"]
        gates[name] = {"passed": bool(ok and checks["survives_vwap_fill"]), "checks": checks, "evidence": ev,
                       "dsr": dsr}
    passed = [k for k, v in gates.items() if v["passed"]]
    decision = {"promoted": False, "passed": passed,
                "reason": "no step-B book passed the gate" if not passed else
                "passed the gate; promotion requires live CTREND/conformal support (see report)"}
    report = {"generated": datetime.now(UTC).isoformat(), "run_id": run_id,
              "prereg": "docs/research/CTREND_CONFORMAL_PREREG_2026-11-11.md", "base_config": sel_name,
              "base_selected_step_a": {k: sel[k] for k in ("sharpe", "cagr", "max_drawdown")},
              "ctrend": {"ic_report_walk_forward_2020": row_all, "ic_report_oos_2021": row_oos, "pass_test": ct_pass,
                         "n_fits": len(fits), "last_fit": fits[-1] if fits else None,
                         "mean_abs_coef": {f: round(float(np.mean([abs(x["coef"][f]) for x in fits])), 5)
                                           for f in ct.INDICATORS},
                         "ladder": ladder},
              "conformal": conf, "books": books, "stresses": stresses, "pbo": pbo, "gates": gates,
              "n_strategy_trials": n_trials, "decision": decision, "timings_s": timings}
    report["runtime_s"] = round(time.time() - t0, 1)
    REPORT_B.write_text(json.dumps(report, indent=1, default=str))
    return report


def _step_a_returns(S, ds, mkts, rep_a: dict, regime) -> dict:
    """Re-simulate step A's 12 pre-registered configurations (deterministic; already logged) for the PBO matrix."""
    out = {}
    preds = {hh: ds.to_matrix(rt.walk_forward_cached(ds, "lgbm", hh, "lab2")) for hh in (3, 7)}
    for name, r in rep_a["configs"].items():
        res = qp.simulate(preds[int(r["h"])], mkts["next_open"], qp.PortfolioConfig(**r["config"]), start=rt.OOS_START)
        out[name] = res.returns
    return out


def to_markdown(rep: dict) -> str:
    c = rep["ctrend"]
    o = c["ic_report_oos_2021"]
    L = ["## CTREND", "", "| span | IC1 | IC3 | IC7 | t(IC7) | L/S weekly net SR |", "|---|---|---|---|---|---|"]
    for lab, r in (("2020→ (walk-forward)", c["ic_report_walk_forward_2020"]), ("2021→ (OOS)", o)):
        ic = {int(k): v for k, v in r["ic"].items()}
        L.append(f"| {lab} | {ic[1]['mean']:+.4f} | {ic[3]['mean']:+.4f} | {ic[7]['mean']:+.4f} | "
                 f"{ic[7]['t_nw']:+.2f} | {r['quintile_ls_weekly']['ls_net']['sharpe']} |")
    L += ["", "pass test: " + json.dumps(c["pass_test"]), "", "| rung | IC without | IC with | diff | t | improves |",
          "|---|---|---|---|---|---|"]
    for k, v in c["ladder"].items():
        L.append(f"| {k} | {v['ic_without']:+.4f} | {v['ic_with']:+.4f} | {v['diff']:+.4f} | {v['t_nw']:+.2f} | "
                 f"{'yes' if v['improves'] else 'no'} |")
    L += ["", "## Books", "", "| book | Sharpe | CAGR | max DD | turnover | costs/yr | DSR p | gate |",
          "|---|---|---|---|---|---|---|---|"]
    for k, b in rep["books"].items():
        g = rep["gates"][k]
        L.append(f"| {k} | {b['sharpe']:.2f} | {b['cagr']:.1%} | {b['max_drawdown']:.1%} | "
                 f"{b['avg_turnover_per_day']:.3f} | {b['avg_cost_per_year']:.1%} | {g['dsr']['p_value']} | "
                 f"{'pass' if g['passed'] else 'fail'} |")
    L += ["", "coverage: " + json.dumps(rep["conformal"]["coverage"])]
    return "\n".join(L) + "\n"
