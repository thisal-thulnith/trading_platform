"""Pre-registered hypothesis round for the daily-5 picks (2026-11-11).

The hypotheses, data splits and pass criteria are fixed in docs/research/HYPOTHESES_2026-11-11.md, written before
any of them was evaluated. This module only executes that document:

    uv run python -m app.research.hypotheses gen-holdout      # engine setups for the fresh holdout sets (slow, once)
    uv run python -m app.research.hypotheses run              # train -> fix H9 -> OOS once -> Holm -> stress -> gate

Fresh holdout sets (setups the earlier research never generated, written to data/research/holdout_setups/, NOT to
data/meta_setups/ so the canonical caches the simulator and evolution loop read are untouched):
  newcoins  4h, the coins of data/history/4h that are not in the 4h meta_label cache, same period as that cache.
  early     4h, the 12 cached coins before the cache's first setup bar (2020-01 .. 2022-11).
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from app.research import selector as sel
from app.research.selector import DEFAULT_PARAMS, NEUTRAL_PARAMS, clean_params

DAY_MS = 86_400_000
HOLDOUT_DIR = Path("data/research/holdout_setups")
ROUND_ID = "hyp-2026-11-11"
LOG_NAME = "hypotheses_2026-11-11.jsonl"
TRAIN_FRAC = 0.6
N_BOOT = 10_000
BLOCK_DAYS = 10
ALPHA = 0.05
TRAIN_ALPHA = 0.10
MIN_TRAIN_N = 100
MIN_OOS_N = 100
EARLY_TAIL_BARS = 200  # extra bars after the early-set boundary so its last setups can resolve


# ---------------------------------------------------------------- holdout generation

def _main_cache_span(tf: str = "4h") -> tuple[int, int, int, list[str]]:
    """(first_open_time, last_open_time, days, symbols) of the canonical meta_label cache for tf."""
    from app.ml.meta_label import CACHE

    files = sorted(CACHE.glob(f"{tf}_*d_*.json"))
    if not files:
        raise SystemExit(f"no {tf} meta_label cache")
    c = json.loads(files[0].read_text())
    syms = sorted(p.name.split("_", 2)[2][:-5] for p in files)
    return int(c["first_open_time"]), int(c["last_open_time"]), int(c["days"]), syms


def _gen_holdout_one(args: tuple) -> tuple[str, str, int, float]:
    """Generate one symbol's setups for a holdout set and write it in the meta_label cache format."""
    from app.backtest.engine import BacktestConfig, generate_setups
    from app.data.timeframes import tf_ms
    from app.ml.meta_label import HTF, WINDOW, _jsonable, code_fingerprint, load_history, snapshot_extras

    name, symbol, tf, days, first, last = args
    t0 = time.time()
    step = tf_ms(tf)
    full = load_history(symbol, tf, None)
    if name == "newcoins":
        df = full[(full.open_time >= first) & (full.open_time <= last)].reset_index(drop=True)
        scan = df
    else:  # early: setups strictly before the main cache's first setup bar
        boundary = first + WINDOW * step
        df = full[full.open_time <= boundary + EARLY_TAIL_BARS * step].reset_index(drop=True)
        scan = df[df.open_time <= boundary].reset_index(drop=True)
    cfg = BacktestConfig(symbols=[symbol], timeframe=tf, htf=HTF[tf], window=WINDOW)
    setups = generate_setups(scan, symbol, cfg, extras=snapshot_extras) if len(scan) > WINDOW + 50 else []
    out = HOLDOUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{tf}_{days}d_{symbol}.json").write_text(json.dumps(
        {"fingerprint": code_fingerprint(), "symbol": symbol, "tf": tf, "days": days, "n_bars": len(df),
         "first_open_time": int(df.open_time.iloc[0]) if len(df) else 0,
         "last_open_time": int(df.open_time.iloc[-1]) if len(df) else 0, "holdout": name,
         "generated_at": int(time.time() * 1000), "setups": setups}, default=_jsonable))
    return name, symbol, len(setups), time.time() - t0


def gen_holdout(workers: int = 8, sets: Sequence[str] = ("newcoins", "early")) -> None:
    from app.ml.meta_label import HISTORY

    first, last, days, cached = _main_cache_span("4h")
    todo = []
    if "newcoins" in sets:
        new = sorted(p.stem for p in (HISTORY / "4h").glob("*USDT.csv") if p.stem not in cached)
        todo += [("newcoins", s, "4h", days, first, last) for s in new]
    if "early" in sets:
        todo += [("early", s, "4h", days, first, last) for s in cached]
    todo = [a for a in todo if not (HOLDOUT_DIR / a[0] / f"4h_{days}d_{a[1]}.json").exists()]
    print(f"generating {len(todo)} holdout files with {workers} workers", flush=True)
    with ProcessPoolExecutor(workers) as ex:
        for name, sym, n, secs in ex.map(_gen_holdout_one, todo):
            print(f"  {name} {sym}: {n} setups ({secs:.0f}s)", flush=True)


# ---------------------------------------------------------------- hypotheses (docs/research/HYPOTHESES_2026-11-11.md)

HYPOTHESES: dict[str, dict] = {
    "H1": {"name": "stacked unfilled >= 2 same-side", "filter": {"min_pending_same_side": 2}},
    "H2": {"name": "BTC daily trend direction", "filter": {"btc_trend_filter": True}},
    "H3": {"name": "28d cross-sectional momentum tercile", "filter": {"min_xs_mom_dir_pct": 0.6667}},
    "H4": {"name": "strategy subset chosen on TRAIN", "filter": {"strategy_allow": None}},  # list set from TRAIN
    "H5": {"name": "funding crowding (skip top/bottom 90d decile)", "filter": {"max_funding_dir_pct": 0.9}},
    "H6": {"name": "stop distance 1-3 ATR", "filter": {"stop_atr_band": [1.0, 3.0]}},
    "H7": {"name": "exit: single +1.5R target", "filter": {}, "targets_r": (1.5,)},
    "H8": {"name": "exit: half at +1R, break-even, half at +2R", "filter": {}, "targets_r": (1.0, 2.0)},
}
FILTER_IDS = ("H1", "H2", "H3", "H4", "H5", "H6")
EXIT_IDS = ("H7", "H8")


def hypothesis_params(filt: Mapping) -> dict:
    """NEUTRAL_PARAMS + one hypothesis filter (the exact params the book and a promotion use)."""
    return clean_params({**NEUTRAL_PARAMS, **filt})


def strategy_subset(train_rows: Sequence[Mapping], min_n: int = 50) -> list[str]:
    """H4: strategies whose filled, resolved TRAIN setups have mean R > 0 and n >= min_n (all candidates)."""
    by: dict[str, list[float]] = defaultdict(list)
    for r in train_rows:
        if r["filled"]:
            by[r["strategy"]].append(float(r["r"]))
    return sorted(s for s, rs in by.items() if len(rs) >= min_n and float(np.mean(rs)) > 0)


def _daily(trades: Sequence[Mapping], d0: int, d1: int) -> tuple[np.ndarray, np.ndarray]:
    n = d1 - d0 + 1
    s, c = np.zeros(n), np.zeros(n)
    for t in trades:
        k = int(t["day"]) - d0
        if 0 <= k < n:
            s[k] += t["r"]
            c[k] += 1
    return s, c


def paired_bootstrap(trades_a: Sequence[Mapping], trades_b: Sequence[Mapping], d0: int, d1: int,
                     block: int = BLOCK_DAYS, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """One-sided paired circular day-block bootstrap of delta = E[R](a) - E[R](b): the same resampled days for both
    books. p = (1 + #{delta* <= 0}) / (1 + n_boot)."""
    sa, ca = _daily(trades_a, d0, d1)
    sb, cb = _daily(trades_b, d0, d1)
    if ca.sum() == 0 or cb.sum() == 0:
        return {"delta": None, "p": 1.0, "delta_ci95": None}
    n_days = d1 - d0 + 1
    b = max(1, min(block, n_days))
    nb = math.ceil(n_days / b)
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    chunk = 1000
    for k in range(0, n_boot, chunk):
        m = min(chunk, n_boot - k)
        starts = rng.integers(0, n_days, size=(m, nb))
        idx = ((starts[:, :, None] + np.arange(b)) % n_days).reshape(m, -1)[:, :n_days]
        na, nb_ = ca[idx].sum(1), cb[idx].sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[k:k + m] = sa[idx].sum(1) / na - sb[idx].sum(1) / nb_
    out = out[np.isfinite(out)]
    delta = float(sa.sum() / ca.sum() - sb.sum() / cb.sum())
    return {"delta": delta, "p": float((1 + np.sum(out <= 0)) / (1 + len(out))),
            "delta_ci95": [float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))]}


def holm(pvals: Mapping[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (step-down, monotone)."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, run_max = {}, 0.0
    for j, (k, p) in enumerate(items):
        run_max = max(run_max, min(1.0, (m - j) * p))
        out[k] = run_max
    return out


def _book(rows: Sequence[Mapping], picker, d0: int, d1: int) -> dict:
    from app.research.simulate import MAX_OPEN, RISK, run_book

    return run_book(rows, picker, MAX_OPEN, RISK, d0, d1)


def _summ(book: Mapping, seed: int = 0) -> dict:
    from app.research.simulate import drop_best_month, full_metrics

    m = full_metrics(book, seed, curve=False)
    keep = ("n_trades", "trades_per_day", "win_rate", "hit_2r_rate", "expectancy_r", "expectancy_ci95", "total_r",
            "cagr", "max_drawdown", "start", "end", "per_year")
    out = {k: m.get(k) for k in keep}
    out["drop_best_month"] = drop_best_month(book)
    return out


class DataSet:
    """One dataset (tf, cache dir) with its rows per (cost_mult, targets_r), loaded lazily."""

    def __init__(self, name: str, tf: str, cache_dir: str | None = None):
        self.name, self.tf, self.cache_dir = name, tf, cache_dir

    def rows(self, cost_mult: float = 1.0, targets_r: tuple | None = None) -> list:
        from app.research.simulate import load_candidates

        return load_candidates(self.tf, cost_mult=cost_mult, cache_dir=self.cache_dir, targets_r=targets_r)


def _window(rows: Sequence[Mapping], start: int | None, end: int | None, purge: bool) -> list:
    from app.research.simulate import window

    return window(rows, start, end, purge)


def evaluate_set(ds: DataSet, rule_params: Mapping[str, dict], rule_targets: Mapping[str, tuple | None],
                 start: int | None, end: int | None, purge: bool, cost_mult: float = 1.0,
                 extra: Mapping[str, dict] | None = None) -> dict:
    """Books of every rule + control + take-all (+ extra param sets) on one window of one dataset, with the paired
    bootstrap delta vs take-all. Window: start <= time < end (purge: resolved before end)."""
    from app.research.simulate import selector_picker, take_all_picker

    base = _window(ds.rows(cost_mult), start, end, purge)
    if not base:
        return {"n_candidates": 0}
    d0 = start // DAY_MS if start is not None else min(r["day"] for r in base)
    d1 = (end - 1) // DAY_MS if end is not None else max(r["day"] for r in base)
    ta = _book(base, take_all_picker, d0, d1)
    out: dict = {"n_candidates": len(base), "first_day": d0, "last_day": d1, "take_all": _summ(ta)}
    ctrl = _book(base, selector_picker(hypothesis_params({})), d0, d1)
    out["control"] = {**_summ(ctrl), "vs_take_all": paired_bootstrap(ctrl["trades"], ta["trades"], d0, d1)}
    for name, params in {**rule_params, **(extra or {})}.items():
        tr = rule_targets.get(name)
        rows = base if not tr else _window(ds.rows(cost_mult, tr), start, end, purge)
        bk = _book(rows, selector_picker(params), d0, d1)
        out[name] = {**_summ(bk), "vs_take_all": paired_bootstrap(bk["trades"], ta["trades"], d0, d1)}
    return out


def _log_path() -> Path:
    return sel.RESEARCH_DIR / LOG_NAME


def _append(rec: Mapping) -> None:
    d = sel.RESEARCH_DIR
    d.mkdir(parents=True, exist_ok=True)
    with open(_log_path(), "a") as fh:
        fh.write(json.dumps(rec, default=float) + "\n")


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def train_stage(main: DataSet, write: bool = True) -> dict:
    """Steps on 4h TRAIN only: the H4 list, every hypothesis's TRAIN book and TRAIN pass, the H9 composition.
    Appends a "registration" record (written before any OOS evaluation)."""
    from app.research.evolution import split

    rows = main.rows()
    cut, train, _ = split(rows, TRAIN_FRAC)
    h4 = strategy_subset(train)
    params = {h: hypothesis_params(HYPOTHESES[h]["filter"]) for h in HYPOTHESES}
    params["H4"] = hypothesis_params({"strategy_allow": h4})
    targets = {h: HYPOTHESES[h].get("targets_r") for h in HYPOTHESES}
    tr = evaluate_set(main, params, targets, None, cut, purge=True)
    ta_e = tr["take_all"]["expectancy_r"]
    passes = {}
    for h in HYPOTHESES:
        m = tr[h]
        passes[h] = bool(m["n_trades"] >= MIN_TRAIN_N and m["expectancy_r"] is not None and ta_e is not None
                         and m["expectancy_r"] > ta_e and m["vs_take_all"]["p"] < TRAIN_ALPHA)
    comp_f = [h for h in FILTER_IDS if passes[h]]
    comp_x = [h for h in EXIT_IDS if passes[h]]
    exit_pick = max(comp_x, key=lambda h: tr[h]["expectancy_r"]) if comp_x else None
    h9 = None
    if comp_f or exit_pick:
        filt: dict = {}
        for h in comp_f:
            filt.update({k: v for k, v in params[h].items() if k in sel.FILTER_KEYS and v != NEUTRAL_PARAMS.get(k)})
        h9 = {"components": comp_f + ([exit_pick] if exit_pick else []), "filter": filt,
              "targets_r": HYPOTHESES[exit_pick]["targets_r"] if exit_pick else None,
              "params": hypothesis_params(filt)}
    rec = {"round": ROUND_ID, "kind": "registration", "at": datetime.now(UTC).isoformat(timespec="seconds"),
           "cut": _d(cut), "cut_ms": cut, "h4_strategies": h4, "train_pass": passes, "h9": h9,
           "train": {k: ({kk: vv for kk, vv in v.items() if kk != "per_year"} if isinstance(v, dict) else v)
                     for k, v in tr.items()}}
    if write:
        _append(rec)
    return {"cut": cut, "params": params, "targets": targets, "h4": h4, "passes": passes, "h9": h9, "train": tr,
            "record": rec}


def run(write: bool = True, promote: bool = True) -> dict:
    """The whole pre-registered round. Writes the registration record, then the result record, and promotes only
    if a rule passes every criterion (G1-G8)."""
    from app.research.evolution import decide, split

    t_start = time.time()
    main = DataSet("4h", "4h")
    reg = train_stage(main, write)
    cut, params, targets, h9 = reg["cut"], dict(reg["params"]), dict(reg["targets"]), reg["h9"]
    if h9 is not None:
        params["H9"], targets["H9"] = h9["params"], h9["targets_r"]
    ids = [h for h in params]  # H1..H8 (+H9)
    print(f"registered: cut {_d(cut)}; H4 strategies {reg['h4']}; TRAIN pass "
          f"{[h for h, v in reg['passes'].items() if v]}; H9 {h9['components'] if h9 else 'none'}", flush=True)

    # ---- the single OOS evaluation (4h), costs x2 on the same window
    oos = evaluate_set(main, params, targets, cut, None, purge=False, extra={"DEFAULT": clean_params(DEFAULT_PARAMS)})
    oos2 = evaluate_set(main, params, targets, cut, None, purge=False, cost_mult=2.0)
    pv = {h: oos[h]["vs_take_all"]["p"] for h in ids}
    adj = holm(pv)

    # ---- 1h independent check (its own 60% cut; H4 list re-chosen on 1h TRAIN)
    h1d = DataSet("1h", "1h")
    rows1 = h1d.rows()
    cut1, train1, _ = split(rows1, TRAIN_FRAC)
    h4_1h = strategy_subset(train1)
    p1 = dict(params)
    p1["H4"] = hypothesis_params({"strategy_allow": h4_1h})
    if h9 is not None and "H4" in h9["components"]:
        p1["H9"] = hypothesis_params({**h9["filter"], "strategy_allow": h4_1h})
    one_h = evaluate_set(h1d, p1, targets, cut1, None, purge=False)

    # ---- fresh holdout sets (whole period each)
    fresh = {}
    for name in ("newcoins", "early"):
        d = HOLDOUT_DIR / name
        if not any(d.glob("4h_*d_*.json")):
            fresh[name] = {"n_candidates": 0, "missing": True}
            continue
        fresh[name] = evaluate_set(DataSet(name, "4h", str(d)), params, targets, None, None, purge=False)

    # ---- gate
    results, decisions = {}, {}
    de = oos["DEFAULT"]
    for h in ids:
        m, m2 = oos[h], oos2[h]
        trm = reg["train"].get(h) or {}
        if h == "H9":
            trm = evaluate_set(main, {"H9": params["H9"]}, {"H9": targets["H9"]}, None, cut, purge=True)["H9"]
        d1h = (one_h.get(h) or {}).get("vs_take_all", {}).get("delta")
        dn = (fresh["newcoins"].get(h) or {}).get("vs_take_all", {}).get("delta")
        de_ = (fresh["early"].get(h) or {}).get("vs_take_all", {}).get("delta")
        g = {
            "G1_holm_p_<0.05": adj[h] < ALPHA,
            "G2_oos_n_>=100": (m["n_trades"] or 0) >= MIN_OOS_N,
            "G3_oos_ci_low_>0": bool(m["expectancy_ci95"]) and m["expectancy_ci95"][0] > 0,
            "G4_costs_x2_>0": (m2["expectancy_r"] or -1) > 0,
            "G5_drop_best_month_>0": (m["drop_best_month"]["expectancy_r"] or -1) > 0,
            "G6_train_delta_>0": (trm.get("vs_take_all") or {}).get("delta") is not None
            and trm["vs_take_all"]["delta"] > 0,
            "G7_1h_delta_>=0": d1h is not None and d1h >= 0,
            "G8_fresh_delta_>0_both": dn is not None and de_ is not None and dn > 0 and de_ > 0,
        }
        stress = {"costs_x2": {"expectancy_r": m2["expectancy_r"]}, "drop_best_month": m["drop_best_month"],
                  "other_seed": {"beats_default_and_take_all_oos": g["G7_1h_delta_>=0"]
                                 and g["G8_fresh_delta_>0_both"]}}
        crit, ok_decide = decide(m, de, oos["take_all"], stress, same_as_default=False)
        ok = ok_decide and all(g.values())
        decisions[h] = {"passed": ok, "gate": g, "evolution_decide": crit,
                        "promotable": ok and h not in EXIT_IDS and not (h == "H9" and h9 and h9["targets_r"])}
        results[h] = {"name": HYPOTHESES[h]["name"] if h in HYPOTHESES else "combination of TRAIN passes",
                      "params": {k: v for k, v in params[h].items() if k in sel.FILTER_KEYS},
                      "targets_r": targets.get(h), "train": trm, "oos": m, "p": pv[h], "p_holm": adj[h],
                      "oos_costs_x2": {k: m2[k] for k in ("n_trades", "expectancy_r", "expectancy_ci95")},
                      "oos_costs_x2_take_all": oos2["take_all"]["expectancy_r"],
                      "h1": one_h.get(h), "fresh_newcoins": fresh["newcoins"].get(h),
                      "fresh_early": fresh["early"].get(h)}
    promotable = [h for h in ids if decisions[h]["promotable"]]
    chosen = None
    if promotable:
        chosen = "H9" if "H9" in promotable else min(promotable, key=lambda h: adj[h])
    decision = f"promoted {chosen}" if chosen else "nothing promoted"
    rec = {"round": ROUND_ID, "kind": "result", "at": datetime.now(UTC).isoformat(timespec="seconds"),
           "cut": _d(cut), "cut_1h": _d(cut1), "h4_strategies_1h": h4_1h, "holm_family": ids,
           "results": results, "decisions": decisions, "decision": decision,
           "baselines": {"4h_oos": {k: oos[k] for k in ("take_all", "control", "DEFAULT")},
                         "4h_oos_costs_x2": {k: oos2[k] for k in ("take_all", "control")},
                         "1h_oos": {k: one_h.get(k) for k in ("take_all", "control")},
                         "fresh_newcoins": {k: fresh["newcoins"].get(k) for k in ("take_all", "control")},
                         "fresh_early": {k: fresh["early"].get(k) for k in ("take_all", "control")}},
           "fresh_sets": {k: {"n_candidates": v.get("n_candidates"), "first_day": v.get("first_day"),
                              "last_day": v.get("last_day")} for k, v in fresh.items()},
           "runtime_s": round(time.time() - t_start, 1)}
    if write:
        _append(rec)
        if chosen and promote:
            sel.selector_path("4h").write_text(json.dumps(
                {"status": "promoted", "run_id": f"{ROUND_ID}-{chosen}", "promoted_at": rec["at"], "tf": "4h",
                 "params": params[chosen], "oos": results[chosen]["oos"], "criteria": decisions[chosen],
                 "cut": _d(cut), "evidence": f"data/research/{LOG_NAME}",
                 "preregistration": "docs/research/HYPOTHESES_2026-11-11.md"}, indent=1, default=float))
    return rec


# ---------------------------------------------------------------- report

def _r(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def _ci(c: Sequence[float] | None) -> str:
    return f"[{c[0]:+.3f}, {c[1]:+.3f}]" if c else "n/a"


def format_result(rec: Mapping) -> str:
    lines = ["| ID | Rule | TRAIN E (Δ) | OOS n | OOS E[R] | 95% CI | Δ vs take-all | p | Holm p | costs×2 | "
             "drop best month | 1h OOS Δ | newcoins Δ | early Δ | Pass |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for h, r in rec["results"].items():
        tr, o = r["train"], r["oos"]
        f = [r.get("h1") or {}, r.get("fresh_newcoins") or {}, r.get("fresh_early") or {}]
        dl = [(x.get("vs_take_all") or {}).get("delta") for x in f]
        lines.append(
            f"| {h} | {r['name']} | {_r(tr.get('expectancy_r'))} ({_r((tr.get('vs_take_all') or {}).get('delta'))}) | "
            f"{o['n_trades']} | {_r(o['expectancy_r'])} | {_ci(o['expectancy_ci95'])} | "
            f"{_r(o['vs_take_all']['delta'])} | {r['p']:.4f} | {r['p_holm']:.4f} | "
            f"{_r(r['oos_costs_x2']['expectancy_r'])} | {_r(o['drop_best_month']['expectancy_r'])} | "
            f"{_r(dl[0])} | {_r(dl[1])} | {_r(dl[2])} | "
            f"{'PASS' if rec['decisions'][h]['passed'] else 'fail'} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-registered hypothesis round for the daily-5 selector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen-holdout")
    g.add_argument("--workers", type=int, default=8)
    r = sub.add_parser("run", help="the full pre-registered round (writes the log; promotes only on a pass)")
    r.add_argument("--dry-run", action="store_true", help="write nothing")
    sub.add_parser("train", help="TRAIN stage only (prints the registration; writes nothing)")
    a = ap.parse_args()
    if a.cmd == "gen-holdout":
        gen_holdout(a.workers)
    elif a.cmd == "train":
        reg = train_stage(DataSet("4h", "4h"), write=False)
        print(json.dumps({k: reg["record"][k] for k in ("cut", "h4_strategies", "train_pass", "h9")}, indent=1,
                         default=float))
    else:
        rec = run(write=not a.dry_run)
        print(format_result(rec))
        print(rec["decision"])


if __name__ == "__main__":
    main()
