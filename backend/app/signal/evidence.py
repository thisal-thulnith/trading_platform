"""The evidence registry: how much each signal component has earned the right to move the final signal.

Every component starts from a literature prior (what published research says, small and documented) and is pulled
toward what this platform has measured out of sample as its sample grows:

    weight = prior x (1 - n / (n + N0))  +  strength x n / (n + N0)        strength = clip((t - 1) / 2, 0, 1)

so measured evidence starts to count only past t = 1 and counts fully at t = 3; an insignificant edge earns nothing.

A component whose own measured edge is negative or insignificant drifts to zero weight; one that keeps proving itself
grows. A sign is never flipped because of a bad stretch (that would be fitting the noise): a negative edge only removes
weight. Components that are models (the selector, the ML meta-label, the AI forecast, the quant lab model) get weight
only once their pre-registered acceptance test passed.
"""
import json
import time
from pathlib import Path

N0 = 52  # observations at which measured evidence and the literature prior weigh the same

# literature priors (0-1). Sources: docs/QUANT_PLAN.md (Liu, Tsyvinski & Wu JF 2022; Schmeling, Schrimpf & Todorov,
# BIS 2023; volatility-managed crypto momentum 2025) and docs/COMPETITORS.md.
PRIORS = {
    "momentum": (0.30, "cross-sectional momentum is one of three crypto factors (JF 2022)"),
    "carry": (0.25, "high funding / basis predicts crashes (BIS 2023)"),
    "regime": (0.15, "momentum and trades with the BTC trend work mainly in that regime"),
    "order_flow": (0.05, "order-flow imbalance predicts minutes to hours; weak for swing trades"),
    "fundamentals": (0.05, "dilution is a multi-week headwind; little short-horizon evidence"),
    "setup": (0.0, "chart-pattern setups: no literature support; measured edge about zero after costs"),
    "ai_desk": (0.0, "LLM desk decisions earn weight only from their graded record"),
    "selector": (0.0, "used only once the evolution loop promotes a version"),
    "meta_label": (0.0, "used only once the ML filter passes acceptance"),
    "ai_forecast": (0.0, "used only once the foundation-model forecast passes its pre-registered test"),
    "quant_model": (0.0, "used only once the quant lab promotes a model"),
    "quant_factors": (0.0, "earned only by measurement: the quant lab's factor composite and its out-of-sample rank IC"),
}
# The composite ranks coins well out of sample (rank IC t 12.6), but that is a median effect: its own long-short
# portfolio lost after costs in combinatorial cross-validation (mean Sharpe -0.49) and the lab's best model failed the
# deflated-Sharpe test. So it may say which side a coin is on relative to the others (enough to veto a short of a
# top-ranked coin), with a capped weight — not drive trades by itself.
QUANT_FACTORS_CAP = 0.3
ACCEPTED_WEIGHT = 0.5  # a model that passed its acceptance test starts here
PATH = Path("data/signal/evidence.json")
_mem: dict = {"t": 0.0, "data": None}


def weight(prior: float, t: float | None, n: int | None) -> float:
    if not n or t is None:
        return round(prior, 4)
    k = n / (n + N0)
    strength = min(1.0, max(0.0, (t - 1) / 2))
    return round(prior * (1 - k) + strength * k, 4)


def _measured() -> dict[str, dict]:
    """What the platform has measured for each component (t-statistic and sample size), from its own records."""
    out: dict[str, dict] = {}
    # momentum: the quant brain's weekly long-short of its trend ranking
    try:
        from app.quant import brain

        ls = ((brain.peek() or {}).get("proof") or {}).get("long_short") or {}
        weeks = ((brain.peek() or {}).get("proof") or {}).get("weeks")
        if weeks:
            out["momentum"] = {"t": ls.get("t_stat"), "n": weeks, "basis": f"quant brain top-minus-bottom quintile, "
                               f"{weeks} weeks: {ls.get('mean_week_pct')}% a week (t {ls.get('t_stat')})"}
    except Exception:  # noqa: BLE001
        pass
    # chart setups: the daily-5 research's take-all book
    try:
        from app.research.evolution import latest_report

        h = (latest_report("4h").get("history") or {}).get("take_all") or {}
        ci = h.get("expectancy_ci95")
        if h.get("n_trades") and ci:
            se = (ci[1] - ci[0]) / 3.92 or 1
            out["setup"] = {"t": round(h["expectancy_r"] / se, 2), "n": h["n_trades"],
                            "basis": f"every 4h engine setup, {h['n_trades']} trades: {h['expectancy_r']:+.3f}R"}
    except Exception:  # noqa: BLE001
        pass
    # AI desk: the head trader's graded calls
    try:
        from app.agents import calibration

        r = calibration.reliability()
        if r.get("graded"):
            wr = r.get("win_rate") or 0
            out["ai_desk"] = {"t": round((wr - 0.333) / max(0.47 / r["graded"] ** 0.5, 1e-9), 2), "n": r["graded"],
                              "basis": f"{r['graded']} graded desk calls, {wr:.0%} reached the target first (2R break-even ≈ 33%)"}
    except Exception:  # noqa: BLE001
        pass
    # quant lab (if run): its factor report replaces the cruder numbers above — measured on a survivorship-free panel
    try:
        from app.quant.lab import latest_report

        rep = latest_report()
        facs = {f["name"]: f for f in rep.get("factors") or []}
        rung = next((r for r in (rep.get("model_ladder") or {}).get("7", {}).get("rungs", []) if r["model"] == "composite"), None)
        days = (rung or {}).get("days") or 1000
        for name, comp, sign in (("mom_28", "momentum", 1), ("funding_7", "carry", -1)):  # high funding should mean LOWER returns
            f = facs.get(name)
            if f and f.get("t_ic7") is not None:
                out[comp] = {"t": round(sign * f["t_ic7"], 2), "n": days,
                             "basis": f"quant lab: {name} rank IC with 7-day returns {f['ic7']:+.3f} (t {f['t_ic7']}), {days} days"}
        if rung and rung.get("oos_ic_t_nw") is not None:
            out["quant_factors"] = {"t": rung["oos_ic_t_nw"], "n": days, "cap": QUANT_FACTORS_CAP,
                                    "basis": f"quant lab factor composite, walk-forward out-of-sample rank IC {rung['oos_ic_mean']:+.3f} "
                                             f"(t {rung['oos_ic_t_nw']}, {days} days); capped at {QUANT_FACTORS_CAP}: a median effect whose "
                                             "own portfolio lost after costs in cross-validation and the best model failed the deflated-Sharpe test"}
    except Exception:  # noqa: BLE001 - the lab may not be built or run
        pass
    return out


def _accepted() -> dict[str, tuple[bool, str]]:
    acc: dict[str, tuple[bool, str]] = {}
    try:
        from app.research.selector import promoted_params

        src = str(promoted_params("4h").get("source", ""))
        acc["selector"] = (src.startswith("promoted"), src)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.ml.meta_label import accepted_artifact

        acc["meta_label"] = (accepted_artifact("4h") is not None, "meta-label 4h")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.ai_forecast import accepted

        acc["ai_forecast"] = (bool(accepted()), "foundation-model forecast")
    except Exception:  # noqa: BLE001
        acc["ai_forecast"] = (False, "not installed")
    try:
        acc["quant_model"] = (Path("data/quant_lab/model.json").exists(), "quant lab model")
    except Exception:  # noqa: BLE001
        pass
    return acc


def registry(max_age_s: float = 600) -> dict:
    """Each component's weight and why (cached ten minutes, written to data/signal/evidence.json for audit)."""
    if _mem["data"] and time.time() - _mem["t"] < max_age_s:
        return _mem["data"]
    meas, acc = _measured(), _accepted()
    comps = {}
    for name, (prior, why) in PRIORS.items():
        m = meas.get(name) or {}
        if name in acc:
            ok, src = acc[name]
            w = ACCEPTED_WEIGHT if ok else 0.0
            comps[name] = {"weight": w, "prior": prior, "accepted": ok, "basis": f"{src}: {'accepted' if ok else 'not accepted'}",
                           "literature": why}
            continue
        w = weight(prior, m.get("t"), m.get("n"))
        if m.get("cap") is not None:
            w = min(w, m["cap"])
        comps[name] = {"weight": w, "prior": prior, "t": m.get("t"), "n": m.get("n"),
                       "basis": m.get("basis") or "no measurement yet: literature prior only", "literature": why}
    out = {"computed_at": int(time.time() * 1000), "n0": N0, "components": comps}
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(out, indent=1, default=str))
    except OSError:
        pass
    _mem.update(t=time.time(), data=out)
    return out
