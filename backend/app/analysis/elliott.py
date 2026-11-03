"""Elliott Wave candidate generator.

Wave counting is subjective. This module enumerates *rule-valid* interpretations over zigzag pivots at
several scales and ranks them with a transparent guideline score. The score is a heuristic, NOT a
probability; `confidence_calibrated` stays False until a wave-feature study shows out-of-sample value
(see docs/ML_RESEARCH.md). Hard rules (Frost & Prechter):
  R1 wave 2 never retraces more than 100% of wave 1
  R2 wave 3 is never the shortest of waves 1, 3, 5
  R3 wave 4 does not enter wave 1's price territory (except diagonals, labelled separately)
"""
import numpy as np
import pandas as pd

from app.analysis.price_action import Pivot, stable_id, zigzag

SCALES = [(3, 3), (5, 5), (8, 8)]


def _in(x: float, lo: float, hi: float) -> float:
    """1 inside [lo, hi], decaying linearly to 0 at 50% outside the band width."""
    if lo <= x <= hi:
        return 1.0
    w = (hi - lo) * 0.5 or 0.1
    d = lo - x if x < lo else x - hi
    return max(0.0, 1 - d / w)


def _closest_ratio(x: float, targets: list[float]) -> float:
    return min(abs(x - t) / t for t in targets)


def _impulse(ps: list[Pivot], bull: bool, provisional_last: bool) -> dict | None:
    s = 1 if bull else -1
    p = [q.price * s for q in ps]  # normalise so bullish logic applies to both
    w1, w2, w3 = p[1] - p[0], p[1] - p[2], p[3] - p[2]
    if w1 <= 0 or w3 <= 0 or w2 <= 0:
        return None
    rules, violations = [], []
    (rules if p[2] > p[0] else violations).append("R1 wave 2 does not retrace beyond wave 1 start")
    (rules if p[3] > p[1] else violations).append("wave 3 exceeds wave 1 end")
    have5 = len(p) == 6
    w4 = p[3] - p[4] if len(p) >= 5 else None
    w5 = p[5] - p[4] if have5 else None
    if w4 is not None:
        if w4 <= 0:
            return None
        diagonal = p[4] <= p[1]
        if not diagonal:
            rules.append("R3 wave 4 does not overlap wave 1")
    else:
        diagonal = False
    if have5:
        if w5 <= 0:
            return None
        (rules if not (w3 < w1 and w3 < w5) else violations).append("R2 wave 3 is not the shortest")
    else:
        rules.append("R2 provisional (wave 5 incomplete)")
    if violations:
        return None
    if diagonal and have5 and not (w3 < w1 and w5 < w3):  # contracting diagonal shape check
        return None

    g = {}
    g["w2_retrace"] = _in(w2 / w1, 0.382, 0.786)
    g["w3_extension"] = _in(w3 / w1, 1.0, 2.618) * (1 - min(_closest_ratio(w3 / w1, [1.618, 2.618]), 0.5))
    if w4 is not None:
        g["w4_retrace"] = _in(w4 / w3, 0.236, 0.5)
        r2, r4 = w2 / w1, w4 / w3
        g["alternation"] = min(1.0, abs(r2 - r4) / 0.25)
    if have5:
        g["w5_equality"] = max(_in(w5 / w1, 0.618, 1.618), _in(w5 / w3, 0.382, 0.618))
    score = float(np.mean(list(g.values())))

    def unsign(v: float) -> float:
        return v * s

    out = {
        "pattern": "leading_diagonal" if diagonal else "impulse",
        "direction": "bullish" if bull else "bearish",
        "waves": [{"label": lbl, "time": q.time, "price": q.price}
                  for lbl, q in zip(["0", "1", "2", "3", "4", "5"], ps)],
        "complete": have5 and not provisional_last,
        "rules_passed": rules, "guidelines": {k: round(v, 3) for k, v in g.items()},
        "score": round(score, 3),
    }
    # Invalidation + projections for the *next* expected leg
    if not have5:
        if len(p) == 4:  # in wave 4 now
            out["current_wave"] = "4"
            out["invalidation"] = unsign(p[1])  # wave 4 into wave 1 territory
            out["projections"] = {"w4_0.382": unsign(p[3] - 0.382 * w3), "w4_0.5": unsign(p[3] - 0.5 * w3)}
        else:  # in wave 5
            out["current_wave"] = "5"
            out["invalidation"] = unsign(p[4])
            out["projections"] = {"w5=w1": unsign(p[4] + w1), "w5=0.618w3": unsign(p[4] + 0.618 * w3),
                                  "w5=1.618w1": unsign(p[4] + 1.618 * w1)}
    else:
        out["current_wave"] = "A (correction expected)"
        out["invalidation"] = unsign(p[5])  # new extreme beyond wave 5 negates completion
        rng = p[5] - p[0]
        out["projections"] = {"A_0.382": unsign(p[5] - 0.382 * rng), "A_0.5": unsign(p[5] - 0.5 * rng),
                              "A_0.618": unsign(p[5] - 0.618 * rng)}
    out["explanation"] = _explain(out, w1, w2, w3, w4, w5)
    return out


def _explain(o, w1, w2, w3, w4, w5) -> str:
    parts = [f"{o['direction']} {o['pattern']} candidate", f"W2 retraced {w2 / w1:.0%} of W1",
             f"W3 = {w3 / w1:.2f}×W1"]
    if w4 is not None:
        parts.append(f"W4 retraced {w4 / w3:.0%} of W3")
    if w5 is not None:
        parts.append(f"W5 = {w5 / w1:.2f}×W1")
    return "; ".join(parts) + ". Subjective interpretation; not a verified market fact."


def _corrective(ps: list[Pivot], bull_trend_prior: bool) -> list[dict]:
    """ABC zigzag / flat over 4 pivots (start, A, B, C)."""
    out = []
    s = -1 if bull_trend_prior else 1  # correction against a bullish trend moves down
    p = [q.price * s for q in ps]
    a, b, c = p[1] - p[0], p[1] - p[2], p[3] - p[2]
    if a <= 0 or b <= 0 or c <= 0:
        return out
    rb, rc = b / a, c / a
    def unsign(v: float) -> float:
        return v * s

    base = {"direction": "bearish" if bull_trend_prior else "bullish",
            "waves": [{"label": lbl, "time": q.time, "price": q.price} for lbl, q in zip(["0", "A", "B", "C"], ps)]}
    if rb < 1 and p[3] > p[1]:
        g = {"b_retrace": _in(rb, 0.382, 0.786), "c_vs_a": _in(rc, 0.618, 1.618) *
             (1 - min(_closest_ratio(rc, [1.0, 1.618]), 0.5))}
        out.append({**base, "pattern": "zigzag", "guidelines": g, "score": round(float(np.mean(list(g.values()))), 3),
                    "rules_passed": ["B retraces < 100% of A", "C extends beyond A"],
                    "invalidation": unsign(p[0]),
                    "explanation": f"ABC zigzag candidate: B={rb:.0%} of A, C={rc:.2f}×A. Subjective."})
    if 0.9 <= rb <= 1.382:
        g = {"b_retrace": _in(rb, 0.9, 1.382), "c_vs_a": _in(rc, 1.0, 1.65)}
        out.append({**base, "pattern": "flat" if rb <= 1.05 else "expanded_flat", "guidelines": g,
                    "score": round(float(np.mean(list(g.values()))), 3),
                    "rules_passed": ["B retraces ≥ 90% of A"], "invalidation": unsign(p[2] if rb > 1 else p[0]),
                    "explanation": f"Flat candidate: B={rb:.0%} of A, C={rc:.2f}×A. Subjective."})
    return out


def _triangle(ps: list[Pivot]) -> dict | None:
    """Contracting triangle over 6 pivots (5 legs a-e), each leg shorter than the previous."""
    legs = [abs(ps[i + 1].price - ps[i].price) for i in range(5)]
    if not all(legs[i + 1] < legs[i] for i in range(4)):
        return None
    highs = [q for q in ps if q.kind == "high"]
    lows = [q for q in ps if q.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    if not (highs[-1].price < highs[0].price and lows[-1].price > lows[0].price):
        return None
    return {"pattern": "contracting_triangle", "direction": "neutral",
            "waves": [{"label": lbl, "time": q.time, "price": q.price} for lbl, q in zip("0abcde", ps)],
            "guidelines": {"contracting_legs": 1.0}, "score": 0.6, "rules_passed": ["each leg shorter than prior",
                                                                                      "converging boundaries"],
            "invalidation": ps[1].price if ps[1].kind == "low" else ps[1].price,
            "explanation": "Five contracting legs with converging boundaries. Subjective."}


def analyze_elliott(df: pd.DataFrame, top_k: int = 4) -> dict:
    if len(df) < 60:
        return {"counts": [], "note": "insufficient data"}
    last_close = float(df["close"].iloc[-1])
    last_t = int(df["open_time"].iloc[-1])
    cands: list[dict] = []
    for left, right in SCALES:
        z = zigzag(df, left, right)
        if len(z) < 4:
            continue
        # provisional pivot = current price if it extends beyond the last confirmed pivot in the opposite kind
        prov = Pivot(len(df) - 1, len(df) - 1, last_t, last_close, "high" if z[-1].kind == "low" else "low")
        seqs = [(z, False), (z + [prov], True)]
        for seq, provisional in seqs:
            for k in (6, 5, 4):
                if len(seq) < k:
                    continue
                ps = seq[-k:]
                bull = ps[0].kind == "low"
                imp = _impulse(ps, bull, provisional)
                if imp:
                    imp.update(scale=f"{left}/{right}", provisional_last_point=provisional)
                    cands.append(imp)
            if len(seq) >= 4:
                ps = seq[-4:]
                for c in _corrective(ps, bull_trend_prior=ps[0].kind == "high"):
                    c.update(scale=f"{left}/{right}", provisional_last_point=provisional)
                    cands.append(c)
            if len(seq) >= 6:
                tri = _triangle(seq[-6:])
                if tri:
                    tri.update(scale=f"{left}/{right}", provisional_last_point=provisional)
                    cands.append(tri)
    # de-duplicate by wave anchor times
    uniq: dict[str, dict] = {}
    for c in cands:
        key = stable_id("ew", c["pattern"], *[w["time"] for w in c["waves"]])
        c["id"] = key
        if key not in uniq or c["score"] > uniq[key]["score"]:
            uniq[key] = c
    ranked = sorted(uniq.values(), key=lambda c: (-c["score"], -c["waves"][-1]["time"]))[:top_k]
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
        c["role"] = "primary" if i == 0 else "alternate"
        c["confidence_calibrated"] = False
    return {"counts": ranked, "method": "rule-filtered zigzag enumeration at scales " + str(SCALES),
            "disclaimer": "Elliott Wave labels are subjective interpretations. Scores rank guideline fit "
                          "and are not probabilities."}
