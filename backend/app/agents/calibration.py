"""Calibration: does the desk's confidence mean what it says, and which leads read the market well?

Pure functions over desk journal rows (`app/agents/journal.py`); pass `rows` to use your own, else the journal is read.

- `reliability()` buckets the Head Trader's confidence on graded calls and compares it with how often the call reached
  its target first (win rate, avg R, Brier score, a `calibrated` flag per bucket).
- `adjust(confidence)` maps a fresh confidence onto the record: a monotone (isotonic) fit of the buckets' win rates,
  blended in with weight n / (n + 20), so a thin record barely moves it.
- `lead_skill()` scores each AI lead's directional view against what price did and turns it into a weight for the
  desk's lean.

Only calls that reached their target or their stop count; expired calls are left out (no clean win or loss).
The Head Trader is graded on what it called: LONG/SHORT, or the side a validator guard turned into WAIT. A plain WAIT's
confidence is confidence in waiting, not in the plan's side, so WAIT counterfactuals stay out of the confidence buckets
and are summarised on their own; for the leads they still count, since the plan's outcome says which way price went.
"""
from app.agents import journal

MIN_GRADED = 10       # below this many graded calls `adjust` returns the raw confidence
SHRINK = 20           # pseudo-count: a bucket's record gets weight n / (n + SHRINK)
CAL_TOL = 0.1         # a bucket is calibrated when |predicted - actual| <= this ...
CAL_MIN_N = 10        # ... over at least this many calls
BUCKETS = (("<0.5", 0.0, 0.5), ("0.5-0.6", 0.5, 0.6), ("0.6-0.7", 0.6, 0.7), ("0.7-0.8", 0.7, 0.8),
           ("0.8+", 0.8, 1.0001))
LEADS = journal.LEADS


def _rows(rows: list[dict] | None) -> list[dict]:
    return journal.rows() if rows is None else rows


def _conf(x) -> float | None:
    """A confidence as 0-1 (a 0-100 percentage is rescaled); None when missing."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return None
    x = float(x)
    if 1.0 < x <= 100.0:
        x /= 100.0
    return min(max(x, 0.0), 1.0)


def _decisive(r: dict) -> bool:
    return r.get("status") == "closed" and r.get("outcome") in ("target", "stop")


def _head_side(r: dict) -> str | None:
    """The side the Head Trader called (old rows: the recorded decision)."""
    a = r.get("head_action") or r.get("decision")
    return a if a in ("LONG", "SHORT") else None


def _head_calls(rows: list[dict]) -> list[dict]:
    """Graded LONG/SHORT calls with a head confidence: [{"p", "win", "r"}]."""
    out = []
    for r in rows:
        p = _conf(r.get("confidence"))
        if _decisive(r) and _head_side(r) and p is not None:
            out.append({"p": p, "win": 1 if r["outcome"] == "target" else 0, "r": float(r.get("r") or 0.0)})
    return out


def _bucket_of(p: float) -> int:
    return next(i for i, (_, lo, hi) in enumerate(BUCKETS) if lo <= p < hi)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _rnd(x: float | None, k: int = 3) -> float | None:
    return None if x is None else round(x, k)


def _buckets(calls: list[dict]) -> list[dict]:
    out = []
    for i, (label, lo, hi) in enumerate(BUCKETS):
        cs = [c for c in calls if _bucket_of(c["p"]) == i]
        if not cs and label == "<0.5":
            continue
        pred, act = _mean([c["p"] for c in cs]), _mean([c["win"] for c in cs])
        gap = None if pred is None else act - pred
        out.append({"range": label, "lo": lo, "hi": min(hi, 1.0), "n": len(cs), "predicted": _rnd(pred),
                    "win_rate": _rnd(act), "avg_r": _rnd(_mean([c["r"] for c in cs]), 2), "gap": _rnd(gap),
                    "calibrated": bool(cs) and len(cs) >= CAL_MIN_N and abs(gap) <= CAL_TOL + 1e-9,
                    "verdict": ("too few calls" if len(cs) < CAL_MIN_N else "calibrated" if abs(gap) <= CAL_TOL + 1e-9
                                else "overconfident" if gap < 0 else "underconfident")})
    return out


def _isotonic(buckets: list[dict]) -> dict[str, float]:
    """Pool-adjacent-violators over the non-empty buckets' win rates (weights n): a non-decreasing map range -> rate."""
    blocks = [[b["win_rate"] * b["n"], b["n"], [b["range"]]] for b in buckets if b["n"]]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] / blocks[i][1] > blocks[i + 1][0] / blocks[i + 1][1]:
            a, b = blocks[i], blocks.pop(i + 1)
            a[0], a[1], a[2] = a[0] + b[0], a[1] + b[1], a[2] + b[2]
            i = max(i - 1, 0)
        else:
            i += 1
    return {rng: s / n for s, n, rngs in blocks for rng in rngs}


def _wait_summary(rows: list[dict]) -> dict:
    waits = [r for r in rows if r.get("decision") == "WAIT" and not _head_side(r)]
    graded = [r for r in waits if _decisive(r)]
    won = [r for r in graded if r["outcome"] == "target"]
    return {"calls": len(waits), "graded": len(graded), "plan_side_won": len(won),
            "plan_side_lost": len(graded) - len(won),
            "missed_winner_rate": _rnd(len(won) / len(graded)) if graded else None,
            "avg_r_if_taken": _rnd(_mean([float(r.get("r") or 0.0) for r in graded]), 2),
            "expired": sum(r.get("status") == "closed" and r.get("outcome") == "expired" for r in waits),
            "meaning": "plan_side_won = waiting missed a winner; plan_side_lost = waiting saved a loss"}


def reliability(rows: list[dict] | None = None) -> dict:
    """How the Head Trader's confidence compares with its graded record, per confidence bucket.

    Returns {"graded", "win_rate", "avg_r", "brier", "brier_baseline", "buckets": [{"range", "lo", "hi", "n",
    "predicted", "win_rate", "avg_r", "gap", "calibrated", "verdict"}], "wait_counterfactual": {...},
    "skipped": {"open", "expired", "no_confidence"}, "note"}."""
    rows = _rows(rows)
    calls = _head_calls(rows)
    taken = [r for r in rows if _head_side(r)]
    wins = [c["win"] for c in calls]
    base = _mean(wins)
    brier = _mean([(c["p"] - c["win"]) ** 2 for c in calls])
    buckets = _buckets(calls)
    n = len(calls)
    bad = [b["range"] for b in buckets if b["verdict"] in ("overconfident", "underconfident")]
    note = (f"Not enough graded calls yet ({n} of {MIN_GRADED}); confidence is not adjusted." if n < MIN_GRADED else
            (f"{n} graded calls; off in {', '.join(bad)}." if bad else f"{n} graded calls; no bucket is clearly off."))
    return {"graded": n, "win_rate": _rnd(base), "avg_r": _rnd(_mean([c["r"] for c in calls]), 2),
            "brier": _rnd(brier, 4), "brier_baseline": _rnd(None if base is None else base * (1 - base), 4),
            "buckets": buckets, "wait_counterfactual": _wait_summary(rows),
            "skipped": {"open": sum(r.get("status") != "closed" for r in taken),
                        "expired": sum(r.get("status") == "closed" and r.get("outcome") == "expired" for r in taken),
                        "no_confidence": sum(_decisive(r) and _conf(r.get("confidence")) is None for r in taken)},
            "note": note}


def adjust(confidence: float, rows: list[dict] | None = None) -> dict:
    """The Head Trader's confidence shrunk toward its record: raw + w * (fit - raw), w = n / (n + 20), where fit is the
    isotonic win rate of the confidence's bucket and n the graded calls in it.

    Returns {"raw", "calibrated", "n", "basis", "graded", "bucket", "weight"}."""
    raw = _conf(confidence)
    raw = 0.5 if raw is None else raw
    calls = _head_calls(_rows(rows))
    total = len(calls)
    out = {"raw": round(raw, 3), "calibrated": round(raw, 3), "n": total, "graded": total, "bucket": None, "weight": 0.0}
    if total < MIN_GRADED:
        return out | {"basis": f"not enough graded calls yet ({total} of {MIN_GRADED}); raw confidence kept"}
    buckets = _buckets(calls)
    label = BUCKETS[_bucket_of(raw)][0]
    b = next((x for x in buckets if x["range"] == label), None)
    if not b or not b["n"]:
        return out | {"n": 0, "bucket": label,
                      "basis": f"no graded calls at confidence {label} yet; raw confidence kept"}
    fit = _isotonic(buckets)[b["range"]]
    w = b["n"] / (b["n"] + SHRINK)
    cal = raw + w * (fit - raw)
    return out | {"calibrated": round(cal, 3), "n": b["n"], "bucket": b["range"], "weight": round(w, 3),
                  "basis": f"{b['n']} graded calls at {b['range']} reached the target {b['win_rate']:.0%} of the time "
                           f"(monotone fit {fit:.0%}); blended {w:.0%} toward it"}


def _price_went(r: dict) -> str:
    """'up' or 'down': which way price resolved the call (a LONG at its target or a SHORT at its stop is 'up')."""
    long_ = r.get("side") == "LONG"
    return "up" if (r["outcome"] == "target") == long_ else "down"


def lead_skill(rows: list[dict] | None = None, include_wait: bool = True) -> dict:
    """How often each AI lead's directional view matched what price did on graded calls (target or stop reached).
    Bullish is right when price resolved up, bearish when it resolved down; neutral views are skipped. WAIT
    counterfactuals count unless include_wait is False.

    weight = clip(1 + (hit_rate - 0.5) * n / (n + 20) * 2, 0.25, 1.5): 1.0 with no record or a coin-flip record.

    Returns {"graded", "leads": {id: {"n", "right", "hit_rate", "weight"}}, "weights": {id: weight}, "note"}."""
    rows = [r for r in _rows(rows) if _decisive(r) and r.get("side") in ("LONG", "SHORT")
            and (include_wait or _head_side(r))]
    tally = {lid: [0, 0] for lid in LEADS}
    used = 0
    for r in rows:
        leads = r.get("leads") or {}
        if not leads:
            continue  # rows from before lead views were recorded
        used += 1
        went = _price_went(r)
        for lid, v in leads.items():
            view = (v or {}).get("view")
            if lid not in tally or view not in ("bullish", "bearish"):
                continue
            tally[lid][0] += 1
            tally[lid][1] += (view == "bullish") == (went == "up")
    out = {}
    for lid, (n, right) in tally.items():
        hr = right / n if n else None
        w = 1.0 if not n else min(max(1.0 + (hr - 0.5) * n / (n + SHRINK) * 2, 0.25), 1.5)
        out[lid] = {"n": n, "right": right, "hit_rate": _rnd(hr), "weight": round(w, 3)}
    return {"graded": used, "leads": out, "weights": {k: v["weight"] for k, v in out.items()},
            "note": ("No graded calls with lead views yet; every lead weighs 1.0." if not used else
                     f"{used} graded calls with lead views; weights shrink toward 1.0 until a lead has a long record.")}
