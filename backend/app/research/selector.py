"""The daily-5 selector: ONE scoring + picking function, used both by the live "Today's 5 trades" job and by the
historical simulation (app/research/simulate.py) and the evolution loop (app/research/evolution.py).

Input rows are the setup feature dicts of `app.ml.meta_label.setup_row` / `live_setup_features` (one per engine
setup), plus a "symbol" key for `pick_daily` (live_setup_features does not carry the symbol). The score is a plain,
documented linear rule over a handful of setup-level features; nothing is fitted inside this module. The weights are
either DEFAULT_PARAMS (priors, written before looking at outcomes) or a parameter set that the evolution loop
promoted after it beat the defaults and the take-all baseline out of sample (see `promoted_params`).

Score (all terms neutral, i.e. 0, when the feature is missing or NaN):

    score =   w_quality  * (quality - 50) / 10                    validation quality score, 0..100
            + w_htf      * htf_agree                              mean higher-timeframe trend vote in trade dir, -1..1
            + w_room     * min(opp_level_r, room_cap_r) / room_cap_r   room (in R) to the nearest opposing S/R level
            - w_stop     * ATRs outside [stop_atr_min, stop_atr_max]   stop too tight (noise) or too wide (slow)
            + w_regime   * regime_aligned                         +1 with the regime, -1 against it, 0 neutral
            + w_evidence * (n_support - n_oppose - 3)             confluence families for/against (3 = engine minimum)
            - w_gap      * max(0, entry_gap_atr - gap_free_atr)   limit far from price: less likely to fill in time
            - w_funding  * clip(funding_dir * 1e4, -10, 10)       bps of perp funding this side pays (+) / receives (-)
            + strategy_bias[strategy]                             per-strategy prior, 0 by default

Eligible: quality >= min_quality and score >= min_score and every active filter passes. `pick_daily` then takes the
highest scores (or, with order="time", the earliest eligible setups), at most one per symbol and at most
`max_per_direction` on the same side (crypto is one correlated beta; five longs is one bet).

Optional filters (hypothesis round 2026-11-11, docs/research/HYPOTHESES_2026-11-11.md). All are OFF in
DEFAULT_PARAMS, so the default behaviour is unchanged. When a filter is on and the feature it needs is missing/NaN,
the setup FAILS it (fail-closed). The context features come from meta_label.setup_row / live_setup_features with a
`context` (app/research/context.py):
    min_pending_same_side  > 0: keep only setups with pending_same_side >= this (earlier same-coin same-side limits
                           still waiting at setup time)
    btc_trend_filter       True: keep only btc_trend_dir > 0 (long when BTC's daily close is above its 50- and
                           200-day averages, short when below both; nothing when BTC is mixed)
    min_xs_mom_dir_pct     > 0: keep only xs_mom_dir_pct >= this (2/3: longs in the top tercile of 28-day return of
                           the universe, shorts in the bottom tercile)
    strategy_allow         list of strategy ids: keep only those strategies
    max_funding_dir_pct    not None: drop setups with funding_pct_dir >= this (0.9: longs when funding is in its top
                           90-day decile, shorts when in its bottom decile)
    stop_atr_band          [lo, hi]: keep only lo <= stop_atr <= hi
A live caller that only uses score_setup still sees filtered setups ranked last (score - FILTER_PENALTY), but
should drop them: use `selector_verdict` (score, eligible, failed filters).
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path

from app.strategies.library import STRATEGIES

RESEARCH_DIR = Path("data/research")
STRATEGY_IDS = sorted(STRATEGIES)

DEFAULT_PARAMS: dict = {
    # weights (score units; one unit ~ one "strong reason" to prefer a setup)
    "w_quality": 0.5,       # +0.5 per 10 quality points above 50
    "w_htf": 0.5,           # higher-timeframe trend agreement
    "w_room": 0.5,          # free room to the nearest opposing level, capped at the 2R target
    "room_cap_r": 2.0,      # room beyond the +2R target does not matter
    "w_stop": 0.5,          # per ATR outside the stop band
    "stop_atr_min": 0.6,    # engine stops are mostly 0.5-2.3 ATR; < 0.6 ATR is inside normal 4h noise
    "stop_atr_max": 2.5,    # > 2.5 ATR needs a long move to reach +2R inside the time stop
    "w_regime": 0.5,        # trading with / against the detected regime
    "w_evidence": 0.1,      # each extra supporting (or fewer opposing) confluence family
    "w_gap": 0.25,          # per ATR the limit sits beyond gap_free_atr from price
    "gap_free_atr": 1.0,
    "w_funding": 0.02,      # per bp of funding paid (a 10 bp print costs 0.2)
    "min_quality": 40.0,    # never pick the weakest validated setups
    "min_score": -0.5,      # drop setups with several reasons against them
    "max_per_direction": 3,  # at most 3 of the 5 picks on the same side
    "strategy_bias": {sid: 0.0 for sid in STRATEGY_IDS},
    # ordering and optional filters (neutral values: off)
    "order": "score",               # "score" (highest first) | "time" (earliest eligible first, score ignored)
    "min_pending_same_side": 0,     # H1
    "btc_trend_filter": False,      # H2
    "min_xs_mom_dir_pct": 0.0,      # H3
    "strategy_allow": None,         # H4
    "max_funding_dir_pct": None,    # H5
    "stop_atr_band": None,          # H6
}
FILTER_KEYS = ("min_pending_same_side", "btc_trend_filter", "min_xs_mom_dir_pct", "strategy_allow",
               "max_funding_dir_pct", "stop_atr_band")
FILTER_PENALTY = 1000.0

# A neutral daily-5 base for the hypothesis round: no scoring, no quality floor, earliest eligible setups first,
# one per coin, no side cap. Hypotheses = NEUTRAL_PARAMS + one filter.
NEUTRAL_PARAMS: dict = {
    **{k: v for k, v in DEFAULT_PARAMS.items() if k != "strategy_bias"},
    "w_quality": 0.0, "w_htf": 0.0, "w_room": 0.0, "w_stop": 0.0, "w_regime": 0.0, "w_evidence": 0.0, "w_gap": 0.0,
    "w_funding": 0.0, "min_quality": 0.0, "min_score": -1e9, "max_per_direction": 5, "order": "time",
    "strategy_bias": {sid: 0.0 for sid in STRATEGY_IDS},
}

# Search bounds for the evolution loop (lo, hi, kind). Weights may go negative so that the search can discover that a
# prior was backwards; the out-of-sample gate decides whether such a discovery is real.
PARAM_BOUNDS: dict[str, tuple[float, float, str]] = {
    "w_quality": (-1.0, 2.0, "float"),
    "w_htf": (-1.0, 2.0, "float"),
    "w_room": (-1.0, 2.0, "float"),
    "room_cap_r": (1.0, 6.0, "float"),
    "w_stop": (0.0, 2.0, "float"),
    "stop_atr_min": (0.3, 1.2, "float"),
    "stop_atr_max": (1.2, 4.0, "float"),
    "w_regime": (-1.0, 2.0, "float"),
    "w_evidence": (-0.5, 1.0, "float"),
    "w_gap": (0.0, 1.5, "float"),
    "gap_free_atr": (0.0, 3.0, "float"),
    "w_funding": (0.0, 0.2, "float"),
    "min_quality": (0.0, 65.0, "float"),
    "min_score": (-2.0, 2.0, "float"),
    "max_per_direction": (2, 5, "int"),
}
STRATEGY_BIAS_BOUNDS = (-1.0, 1.0)


def _f(row: Mapping, key: str) -> float | None:
    """Finite float feature or None."""
    v = row.get(key)
    if v is None or isinstance(v, str):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _p(params: Mapping, key: str) -> float:
    v = params.get(key, DEFAULT_PARAMS[key])
    return float(v)


def row_direction(row: Mapping) -> int:
    """+1 long, -1 short, from "direction" ("LONG"/"SHORT") or the setup_row "dir" feature."""
    d = row.get("direction")
    if isinstance(d, str):
        return 1 if d.upper() == "LONG" else -1
    x = _f(row, "dir")
    return 1 if (x or 0) >= 0 else -1


def row_strategy(row: Mapping) -> str | None:
    """Strategy id from "strategy" or the setup_row one-hot columns strat_<id>."""
    s = row.get("strategy")
    if isinstance(s, str):
        return s
    for sid in STRATEGY_IDS:
        if (_f(row, f"strat_{sid}") or 0) > 0.5:
            return sid
    return None


def failed_filters(row: Mapping, params: Mapping | None = None) -> list[str]:
    """Names of the active optional filters this setup fails (empty = passes all). Missing features fail."""
    p = params or DEFAULT_PARAMS
    bad: list[str] = []
    mp = p.get("min_pending_same_side") or 0
    if mp and float(mp) > 0:
        v = _f(row, "pending_same_side")
        if v is None or v < float(mp):
            bad.append("min_pending_same_side")
    if p.get("btc_trend_filter"):
        v = _f(row, "btc_trend_dir")
        if v is None or v <= 0:
            bad.append("btc_trend_filter")
    mx = p.get("min_xs_mom_dir_pct") or 0
    if mx and float(mx) > 0:
        v = _f(row, "xs_mom_dir_pct")
        if v is None or v < float(mx) - 1e-4:  # 0.6667 means >= 2/3
            bad.append("min_xs_mom_dir_pct")
    allow = p.get("strategy_allow")
    if allow is not None and row_strategy(row) not in set(allow):
        bad.append("strategy_allow")
    mf = p.get("max_funding_dir_pct")
    if mf is not None:
        v = _f(row, "funding_pct_dir")
        if v is None or v >= float(mf):
            bad.append("max_funding_dir_pct")
    band = p.get("stop_atr_band")
    if band is not None:
        v = _f(row, "stop_atr")
        if v is None or not (float(band[0]) <= v <= float(band[1])):
            bad.append("stop_atr_band")
    return bad


def score_setup(row: Mapping, params: Mapping | None = None) -> float:
    """Selector score of one setup (higher = better). Pure; accepts setup_row / live_setup_features dicts.
    Missing or NaN features contribute 0. A setup failing an active filter gets -FILTER_PENALTY on top."""
    p = params or DEFAULT_PARAMS
    s = -FILTER_PENALTY if failed_filters(row, p) else 0.0
    q = _f(row, "quality")
    if q is not None:
        s += _p(p, "w_quality") * (q - 50.0) / 10.0
    htf = _f(row, "htf_agree")
    if htf is not None:
        s += _p(p, "w_htf") * htf
    room = _f(row, "opp_level_r")
    if room is not None:
        cap = max(_p(p, "room_cap_r"), 1e-9)
        s += _p(p, "w_room") * min(max(room, 0.0), cap) / cap
    st = _f(row, "stop_atr")
    if st is not None:
        out = max(0.0, _p(p, "stop_atr_min") - st) + max(0.0, st - _p(p, "stop_atr_max"))
        s -= _p(p, "w_stop") * out
    reg = _f(row, "regime_aligned")
    if reg is not None:
        s += _p(p, "w_regime") * reg
    ns, no = _f(row, "n_support"), _f(row, "n_oppose")
    if ns is not None and no is not None:
        s += _p(p, "w_evidence") * (ns - no - 3.0)
    gap = _f(row, "entry_gap_atr")
    if gap is not None:
        s -= _p(p, "w_gap") * max(0.0, gap - _p(p, "gap_free_atr"))
    fund = _f(row, "funding_dir")
    if fund is not None:
        s -= _p(p, "w_funding") * max(-10.0, min(10.0, fund * 1e4))
    sid = row_strategy(row)
    bias = p.get("strategy_bias") or {}
    if sid is not None and sid in bias:
        s += float(bias[sid])
    return float(s)


def is_eligible(row: Mapping, score: float, params: Mapping | None = None) -> bool:
    p = params or DEFAULT_PARAMS
    q = _f(row, "quality")
    return math.isfinite(score) and (q if q is not None else 0.0) >= _p(p, "min_quality") \
        and score >= _p(p, "min_score") and not failed_filters(row, p)


def selector_verdict(row: Mapping, params: Mapping | None = None) -> dict:
    """{"score", "eligible", "failed_filters"} for one live setup: the live caller should drop setups that are not
    eligible (exactly what pick_daily does historically) and rank the rest by score."""
    p = params or DEFAULT_PARAMS
    sc = score_setup(row, p)
    return {"score": sc, "eligible": is_eligible(row, sc, p), "failed_filters": failed_filters(row, p)}


def _tiebreak(row: Mapping) -> tuple:
    return (str(row.get("symbol", "")), _f(row, "time") or 0.0, str(row_strategy(row) or ""), row_direction(row))


def pick_daily(rows: list[Mapping], params: Mapping | None = None, k: int = 5) -> list[dict]:
    """Top-k eligible setups of one day: highest score first, at most one per symbol, at most max_per_direction per
    side. Ties are broken by (symbol, time, strategy, direction) so the result is deterministic. Every row must have
    a "symbol". Returns copies of the chosen rows with "selector_score" and "selector_rank" (1-based) added."""
    p = params or DEFAULT_PARAMS
    max_dir = int(p.get("max_per_direction", DEFAULT_PARAMS["max_per_direction"]))
    scored = []
    for r in rows:
        sc = score_setup(r, p)
        if is_eligible(r, sc, p):
            scored.append((-sc, _tiebreak(r), r, sc))
    if p.get("order", "score") == "time":
        scored.sort(key=lambda x: ((_f(x[2], "time") or 0.0), x[1]))
    else:
        scored.sort(key=lambda x: (x[0], x[1]))
    out: list[dict] = []
    seen: set = set()
    per_dir = {1: 0, -1: 0}
    for _, _, r, sc in scored:
        if len(out) >= k:
            break
        sym, d = r["symbol"], row_direction(r)
        if sym in seen or per_dir[d] >= max_dir:
            continue
        seen.add(sym)
        per_dir[d] += 1
        out.append({**r, "selector_score": sc, "selector_rank": len(out) + 1})
    return out


def selector_path(tf: str) -> Path:
    return RESEARCH_DIR / f"selector_{tf}.json"


def clean_params(params: Mapping) -> dict:
    """Params restricted to known keys, filled from DEFAULT_PARAMS (drops "source" and unknown keys)."""
    out = {k: params.get(k, v) for k, v in DEFAULT_PARAMS.items() if k != "strategy_bias"}
    bias = dict(DEFAULT_PARAMS["strategy_bias"])
    bias.update({k: float(v) for k, v in (params.get("strategy_bias") or {}).items() if k in bias})
    out["strategy_bias"] = bias
    out["max_per_direction"] = int(out["max_per_direction"])
    if out.get("strategy_allow") is not None:
        out["strategy_allow"] = sorted(str(x) for x in out["strategy_allow"])
    if out.get("stop_atr_band") is not None:
        out["stop_atr_band"] = [float(x) for x in out["stop_atr_band"]]
    return out


def promoted_params(tf: str = "4h") -> dict:
    """The selector params to use live for `tf`: the evolution loop's promoted set (data/research/selector_{tf}.json,
    written only when a candidate passed the out-of-sample gate), else DEFAULT_PARAMS. The returned dict carries a
    "source" field: "promoted:<run_id>" or "default" (with "source_note" saying why)."""
    path = selector_path(tf)
    note = f"no promoted selector for {tf}"
    if path.exists():
        try:
            doc = json.loads(path.read_text())
            if doc.get("status") == "promoted" and isinstance(doc.get("params"), dict):
                return {**clean_params(doc["params"]), "source": f"promoted:{doc.get('run_id', '?')}",
                        "promoted_at": doc.get("promoted_at")}
            note = f"{path} is not a promoted selector"
        except (OSError, ValueError) as e:
            note = f"unreadable {path}: {e}"
    return {**clean_params(DEFAULT_PARAMS), "source": "default", "source_note": note}
