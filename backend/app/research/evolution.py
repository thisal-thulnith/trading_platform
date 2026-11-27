"""Evolution loop for the daily-5 selector parameters, with an out-of-sample promotion gate.

    uv run python -m app.research.evolution --tf 4h
    uv run python -m app.research.evolution --tf 1h --pop 24 --gens 8 --seed 7

Seven explicit steps (each logged and stored in the run record):
  1. SEED      population = DEFAULT_PARAMS + mutations of it + uniform random draws within PARAM_BOUNDS.
  2. EVALUATE  each candidate on the TRAIN window with the book simulator (selected book only).
  3. SELECT    rank by the train objective; the top `elite` survive unchanged.
  4. VARY      tournament parents -> uniform crossover -> gaussian mutation (clipped to bounds). Steps 2-4 repeat for
               `generations` generations; everything is driven by one seeded RNG, so a run is reproducible.
  5. VALIDATE  the best few (by TRAIN objective, over all generations) on the later OUT-OF-SAMPLE window, next to
               DEFAULT_PARAMS, take-all and random-5. The winner is the train-best candidate; the OOS numbers of the
               others are reported only (choosing among them by OOS would leak OOS into the selection).
  6. STRESS    the winner, out of sample: (a) fees and slippage x2, (b) without its best month, (c) a full re-run of
               steps 1-4 with a different seed must also produce a train-best that beats DEFAULT_PARAMS and
               take-all OOS (the improvement is not one lucky search).
  7. PROMOTE   write data/research/selector_{tf}.json only if every criterion in `criteria` holds; otherwise the
               incumbent stays and the failed criteria are recorded as the reason.
Every run appends one JSON line to data/research/evolution_{tf}.jsonl.

Split: walk-forward by time. TRAIN = setups created before the cut (first `train_frac`, default 60%, of the calendar
span) whose trade/order also resolved before the cut (purged); OOS = setups created at/after the cut. Nothing from
the OOS window influences steps 1-4.

Train objective (higher is better):
    J = E[R per trade] - 0.5 * max(0, 1 - n_trades / n_min) - 0.2 * max_drawdown
  n_min = half the trades the take-all book makes on the same train window (a selector that trades much less than
  half as often is penalised up to 0.5R; it would leave most of the 5 daily slots empty), max_drawdown is the fraction
  of equity at 1% risk per trade (a 50% drawdown costs 0.1R). Fewer than 30 trades -> J = -1.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.research import selector as sel
from app.research.selector import DEFAULT_PARAMS, PARAM_BOUNDS, STRATEGY_BIAS_BOUNDS, clean_params, promoted_params
from app.research.simulate import (
    MAX_OPEN,
    RISK,
    K,
    load_candidates,
    quick_metrics,
    run_book,
    selector_picker,
    simulate,
    simulate_rows,
    take_all_picker,
    window,
)

DAY_MS = 86_400_000
POP = 24
GENS = 8
SEED = 7
TRAIN_FRAC = 0.6
ELITE = 4
N_VALIDATE = 3
MIN_OOS_TRADES = 50
LAMBDA_FEW = 0.5
LAMBDA_DD = 0.2
OBJECTIVE_DOC = ("J = E[R/trade] - 0.5*max(0, 1 - n/n_min) - 0.2*maxDD, n_min = 0.5 x take-all trades on the "
                 "train window; J = -1 if n < 30")


def _log(log: list[str], msg: str) -> None:
    line = f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}"
    log.append(line)
    print(line, flush=True)


# ---------------------------------------------------------------- genome

def _key(p: Mapping) -> str:
    return hashlib.sha1(json.dumps(clean_params(p), sort_keys=True).encode()).hexdigest()[:12]


def _clip(name: str, v: float) -> float | int:
    lo, hi, kind = PARAM_BOUNDS[name]
    v = min(max(v, lo), hi)
    return int(round(v)) if kind == "int" else round(float(v), 4)


def random_params(rng: random.Random) -> dict:
    p = clean_params(DEFAULT_PARAMS)
    for name, (lo, hi, _) in PARAM_BOUNDS.items():
        p[name] = _clip(name, rng.uniform(lo, hi))
    lo, hi = STRATEGY_BIAS_BOUNDS
    p["strategy_bias"] = {s: round(rng.uniform(lo, hi), 4) for s in p["strategy_bias"]}
    return p


def mutate(p: Mapping, rng: random.Random, rate: float = 0.3, sigma: float = 0.15) -> dict:
    """Each gene mutates with probability `rate` by gaussian noise of `sigma` x its range, clipped to bounds."""
    q = clean_params(p)
    for name, (lo, hi, _) in PARAM_BOUNDS.items():
        if rng.random() < rate:
            q[name] = _clip(name, float(q[name]) + rng.gauss(0, sigma * (hi - lo)))
    lo, hi = STRATEGY_BIAS_BOUNDS
    for s in q["strategy_bias"]:
        if rng.random() < rate:
            q["strategy_bias"][s] = round(min(max(q["strategy_bias"][s] + rng.gauss(0, sigma * (hi - lo)), lo), hi), 4)
    return q


def crossover(a: Mapping, b: Mapping, rng: random.Random) -> dict:
    a, b = clean_params(a), clean_params(b)
    c = {name: (a if rng.random() < 0.5 else b)[name] for name in PARAM_BOUNDS}
    c["strategy_bias"] = {s: (a if rng.random() < 0.5 else b)["strategy_bias"][s] for s in a["strategy_bias"]}
    return clean_params(c)


def seed_population(rng: random.Random, size: int) -> list[dict]:
    """DEFAULT_PARAMS, then alternating mutations of the default (sigma 0.25) and uniform random draws."""
    pop = [clean_params(DEFAULT_PARAMS)]
    while len(pop) < size:
        pop.append(mutate(DEFAULT_PARAMS, rng, rate=0.5, sigma=0.25) if len(pop) % 2 else random_params(rng))
    return pop


# ---------------------------------------------------------------- objective

def objective(m: Mapping, n_min: float) -> float:
    if not m.get("n_trades") or m["n_trades"] < 30 or m.get("expectancy_r") is None:
        return -1.0
    few = max(0.0, 1.0 - m["n_trades"] / n_min) if n_min > 0 else 0.0
    return float(m["expectancy_r"] - LAMBDA_FEW * few - LAMBDA_DD * m["max_drawdown"])


def evaluate(params: Mapping, rows: list, first_day: int, last_day: int, n_min: float) -> dict:
    m = quick_metrics(run_book(rows, selector_picker(params, K), MAX_OPEN, RISK, first_day, last_day))
    return {**m, "objective": objective(m, n_min)}


def _tournament(scored: list[dict], rng: random.Random, size: int = 3) -> dict:
    return max(rng.sample(scored, min(size, len(scored))),
               key=lambda c: (c["train"]["objective"], -c["order"]))["params"]


def search(train: list, first_day: int, last_day: int, n_min: float, pop_size: int, generations: int, seed: int,
           elite: int, log: list[str], label: str = "") -> list[dict]:
    """Steps 1-4. Returns every evaluated candidate {"params", "train", "gen"} sorted by train objective (best
    first; ties keep the earlier candidate, so DEFAULT_PARAMS wins a tie)."""
    rng = random.Random(seed)
    seen: dict[str, dict] = {}

    def ev(p: dict, gen: int) -> dict:
        k = _key(p)
        if k not in seen:
            seen[k] = {"params": p, "train": evaluate(p, train, first_day, last_day, n_min), "gen": gen,
                       "order": len(seen)}
        return seen[k]

    pop = seed_population(rng, pop_size)
    _log(log, f"{label}step 1 SEED: {len(pop)} candidates (DEFAULT_PARAMS + {len(pop) - 1} variants), seed {seed}")
    for g in range(generations):
        scored = sorted((ev(p, g) for p in pop), key=lambda c: (-c["train"]["objective"], c["order"]))
        best = scored[0]
        _log(log, f"{label}step 2 EVALUATE gen {g}: {len(pop)} on train; best J={best['train']['objective']:+.4f} "
                  f"E={best['train']['expectancy_r'] or 0:+.4f}R n={best['train']['n_trades']} "
                  f"dd={best['train']['max_drawdown']:.1%}; median J="
                  f"{sorted(c['train']['objective'] for c in scored)[len(scored) // 2]:+.4f}")
        elites = [c["params"] for c in scored[:elite]]
        _log(log, f"{label}step 3 SELECT gen {g}: keep top {len(elites)} "
                  f"(J {', '.join(f'{c['train']['objective']:+.3f}' for c in scored[:elite])})")
        if g == generations - 1:
            break

        children = []
        while len(elites) + len(children) < pop_size:
            children.append(mutate(crossover(_tournament(scored, rng), _tournament(scored, rng), rng), rng))
        pop = [copy.deepcopy(p) for p in elites] + children
        _log(log, f"{label}step 4 VARY gen {g}: {len(children)} children by tournament(3) + uniform crossover + "
                  f"mutation (rate 0.3, sigma 0.15 x range)")
    return sorted(seen.values(), key=lambda c: (-c["train"]["objective"], c["order"]))


# ---------------------------------------------------------------- run

def _slim(m: Mapping) -> dict:
    return {k: v for k, v in m.items() if k not in ("equity_curve", "monthly_returns")}


def _research_dir() -> Path:
    return sel.RESEARCH_DIR


def split(rows: list, train_frac: float) -> tuple[int, list, list]:
    t0, t1 = min(r["time"] for r in rows), max(r["time"] for r in rows)
    cut = int(t0 + train_frac * (t1 - t0))
    cut -= cut % DAY_MS  # whole UTC days
    return cut, window(rows, None, cut, purge=True), window(rows, cut, None)


def decide(winner_oos: Mapping, default_oos: Mapping, take_all_oos: Mapping, stress: Mapping,
           same_as_default: bool) -> tuple[dict, bool]:
    """Promotion criteria (all must hold)."""
    we, de, te = winner_oos.get("expectancy_r"), default_oos.get("expectancy_r"), take_all_oos.get("expectancy_r")
    ci = winner_oos.get("expectancy_ci95")
    crit = {
        "winner_differs_from_default": not same_as_default,
        "oos_beats_default_expectancy": we is not None and (de is None or we > de),
        "oos_beats_take_all_expectancy": we is not None and (te is None or we > te),
        f"oos_trades_>={MIN_OOS_TRADES}": (winner_oos.get("n_trades") or 0) >= MIN_OOS_TRADES,
        "oos_block_bootstrap_ci_low_>0": bool(ci) and ci[0] > 0,
        "stress_costs_x2_expectancy_>0": (stress.get("costs_x2", {}).get("expectancy_r") or -1) > 0,
        "stress_without_best_month_expectancy_>0": (stress.get("drop_best_month", {}).get("expectancy_r") or -1) > 0,
        "stress_other_seed_beats_default_and_take_all_oos": bool(
            stress.get("other_seed", {}).get("beats_default_and_take_all_oos")),
    }
    return crit, all(crit.values())


def run(tf: str = "4h", pop_size: int = POP, generations: int = GENS, seed: int = SEED,
        train_frac: float = TRAIN_FRAC, elite: int = ELITE, n_validate: int = N_VALIDATE, n_random: int = 200,
        rows: list | None = None, rows_cost_x2: list | None = None, write: bool = True) -> dict:
    """One evolution run (steps 1-7). `rows`/`rows_cost_x2` override the cached candidates (tests)."""
    t_start = time.time()
    log: list[str] = []
    run_id = f"{tf}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-s{seed}"
    rows = rows if rows is not None else load_candidates(tf)
    if not rows:
        raise SystemExit(f"no cached setups for {tf}")
    cut, train, oos = split(rows, train_frac)
    first_day = min(r["day"] for r in rows)
    cut_day = cut // DAY_MS
    _log(log, f"{tf}: {len(rows)} candidates; TRAIN {len(train)} (purged, < {_d(cut)}), OOS {len(oos)} (>= {_d(cut)})")
    ta_train = quick_metrics(run_book(train, take_all_picker, MAX_OPEN, RISK, first_day, cut_day - 1))
    n_min = 0.5 * ta_train["n_trades"]
    _log(log, f"objective: {OBJECTIVE_DOC}; take-all train n={ta_train['n_trades']} -> n_min={n_min:.0f}")

    ranked = search(train, first_day, cut_day - 1, n_min, pop_size, generations, seed, elite, log)
    default_key = _key(DEFAULT_PARAMS)
    default_train = next((c["train"] for c in ranked if _key(c["params"]) == default_key), None)
    winner = ranked[0]
    _log(log, f"train-best: J={winner['train']['objective']:+.4f} (gen {winner['gen']}); DEFAULT_PARAMS J="
              f"{default_train['objective']:+.4f}; {len(ranked)} distinct candidates evaluated")

    # step 5: out of sample
    def oos_eval(p: Mapping, baselines: bool = False) -> dict:
        return simulate_rows(oos, p, cut, None, n_random=n_random if baselines else 0, seed=seed,
                             baselines=baselines, curve=False)

    oos_default = oos_eval(DEFAULT_PARAMS, baselines=True)
    validated = []
    for c in ranked[:n_validate]:
        r = oos_eval(c["params"])
        validated.append({"key": _key(c["params"]), "gen": c["gen"], "train": c["train"],
                          "oos": _slim(r["selected"]), "is_default": _key(c["params"]) == default_key,
                          "_drop": r["drop_best_month"]})
    w_oos = validated[0]["oos"]
    _log(log, "step 5 VALIDATE on OOS (never seen by steps 1-4): " + "; ".join(
        f"#{i + 1} train J={v['train']['objective']:+.3f} -> OOS E={_r(v['oos']['expectancy_r'])} "
        f"n={v['oos']['n_trades']}" for i, v in enumerate(validated))
        + f"; DEFAULT OOS E={_r(oos_default['selected']['expectancy_r'])}, take-all OOS E="
          f"{_r(oos_default['take_all']['expectancy_r'])}, random-5 OOS E={_r(oos_default['random_5']['expectancy_r'])}")

    # step 6: stress tests on the winner
    stress: dict = {}
    rows2 = rows_cost_x2 if rows_cost_x2 is not None else load_candidates(tf, cost_mult=2.0)
    oos2 = window(rows2, cut, None)
    c2 = simulate_rows(oos2, winner["params"], cut, None, n_random=0, seed=seed, curve=False)
    stress["costs_x2"] = {"expectancy_r": c2["selected"]["expectancy_r"], "n_trades": c2["selected"]["n_trades"],
                          "total_r": c2["selected"]["total_r"], "take_all_expectancy_r": c2["take_all"]["expectancy_r"]}
    stress["drop_best_month"] = validated[0].pop("_drop")
    for v in validated[1:]:
        v.pop("_drop", None)
    other_log: list[str] = []
    other = search(train, first_day, cut_day - 1, n_min, pop_size, generations, seed + 1, elite, other_log,
                   label="(seed+1) ")
    o_oos = _slim(oos_eval(other[0]["params"])["selected"])
    de, te = oos_default["selected"]["expectancy_r"], oos_default["take_all"]["expectancy_r"]
    stress["other_seed"] = {"seed": seed + 1, "train_objective": other[0]["train"]["objective"],
                            "oos_expectancy_r": o_oos["expectancy_r"], "oos_n_trades": o_oos["n_trades"],
                            "is_default": _key(other[0]["params"]) == default_key,
                            "beats_default_and_take_all_oos": (
                                o_oos["expectancy_r"] is not None and _key(other[0]["params"]) != default_key
                                and (de is None or o_oos["expectancy_r"] > de)
                                and (te is None or o_oos["expectancy_r"] > te))}
    _log(log, f"step 6 STRESS winner OOS: costs x2 E={_r(stress['costs_x2']['expectancy_r'])}; without best month "
              f"({stress['drop_best_month']['dropped_month']}) E={_r(stress['drop_best_month']['expectancy_r'])}; "
              f"seed {seed + 1} re-run train-best OOS E={_r(o_oos['expectancy_r'])} "
              f"(beats default and take-all: {stress['other_seed']['beats_default_and_take_all_oos']})")

    # step 7: promotion gate
    crit, ok = decide(w_oos, oos_default["selected"], oos_default["take_all"], stress, validated[0]["is_default"])
    incumbent = promoted_params(tf)
    failed = [k for k, v in crit.items() if not v]
    if ok:
        decision, reason = "promoted", "passed every criterion"
    else:
        decision = "kept_incumbent"
        reason = f"failed: {', '.join(failed)}; incumbent ({incumbent['source']}) kept"
    _log(log, f"step 7 PROMOTE: {decision} - {reason}")
    record = {
        "run_id": run_id, "tf": tf, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "settings": {"pop": pop_size, "generations": generations, "seed": seed, "elite": elite,
                     "train_frac": train_frac, "cut": _d(cut), "cut_ms": cut, "n_validate": n_validate,
                     "risk": RISK, "max_open": MAX_OPEN, "k": K, "objective": OBJECTIVE_DOC,
                     "n_candidates": len(rows), "n_train": len(train), "n_oos": len(oos),
                     "candidates_evaluated": len(ranked)},
        "winner": {"params": winner["params"], "gen": winner["gen"], "train": winner["train"]},
        "default": {"train": default_train, "oos": _slim(oos_default["selected"])},
        "train_take_all": ta_train,
        "oos": {"winner": w_oos, "take_all": _slim(oos_default["take_all"]), "random_5": oos_default["random_5"],
                "validated": validated},
        "stress": stress, "criteria": crit, "decision": decision, "reason": reason,
        "incumbent_source": incumbent["source"], "runtime_s": round(time.time() - t_start, 1), "log": log,
    }
    if write:
        d = _research_dir()
        d.mkdir(parents=True, exist_ok=True)
        if ok:
            sel.selector_path(tf).write_text(json.dumps(
                {"status": "promoted", "run_id": run_id, "promoted_at": record["finished_at"], "tf": tf,
                 "params": winner["params"], "train": winner["train"], "oos": w_oos, "criteria": crit,
                 "cut": _d(cut)}, indent=1, default=float))
        with open(d / f"evolution_{tf}.jsonl", "a") as fh:
            fh.write(json.dumps(record, default=float) + "\n")
    return record


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _r(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}R"


# ---------------------------------------------------------------- report for the API

def last_run(tf: str) -> dict | None:
    p = _research_dir() / f"evolution_{tf}.jsonl"
    if not p.exists():
        return None
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else None


def _mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


@lru_cache(maxsize=4)
def _report_cached(tf: str, _stamp: tuple) -> dict:
    run_rec = last_run(tf)
    params = promoted_params(tf)
    summary = None
    cut = None
    if run_rec:
        cut = run_rec["settings"]["cut_ms"]
        summary = {k: run_rec.get(k) for k in ("run_id", "finished_at", "decision", "reason", "criteria", "settings",
                                                "runtime_s")}
        summary.update({"winner_train": run_rec["winner"]["train"], "winner_oos": run_rec["oos"]["winner"],
                        "default_oos": run_rec["default"]["oos"], "take_all_oos": run_rec["oos"]["take_all"],
                        "random_5_oos": run_rec["oos"]["random_5"], "stress": run_rec["stress"]})
    full = simulate(tf, params, n_random=100)
    oos = simulate(tf, params, start=cut, n_random=100) if cut else None
    return {"tf": tf, "params": params, "params_source": params["source"], "last_run": summary,
            "history": full, "oos": oos}


def latest_report(tf: str = "4h") -> dict:
    """Last evolution run's summary + simulate() of the currently promoted params (or DEFAULT_PARAMS) over the whole
    cached history and over the last run's OOS window. Cached until the run log or the promoted file changes."""
    d = _research_dir()
    stamp = (_mtime(d / f"evolution_{tf}.jsonl"), _mtime(sel.selector_path(tf)))
    return _report_cached(tf, stamp)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evolve the daily-5 selector params with an OOS promotion gate")
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--pop", type=int, default=POP)
    ap.add_argument("--gens", type=int, default=GENS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--train-frac", type=float, default=TRAIN_FRAC)
    ap.add_argument("--elite", type=int, default=ELITE)
    ap.add_argument("--validate", type=int, default=N_VALIDATE)
    ap.add_argument("--n-random", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true", help="do not write the run log or promote")
    a = ap.parse_args()
    rec = run(a.tf, a.pop, a.gens, a.seed, a.train_frac, a.elite, a.validate, a.n_random, write=not a.dry_run)
    print(json.dumps({k: rec[k] for k in ("run_id", "decision", "reason", "criteria", "runtime_s")}, indent=1))
    print("winner params:", json.dumps(rec["winner"]["params"]))


if __name__ == "__main__":
    main()
