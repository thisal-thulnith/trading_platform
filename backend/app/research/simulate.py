"""Historical simulation of the "Today's 5 trades" method on the engine's real cached setups.

    uv run python -m app.research.simulate --tf 4h
    uv run python -m app.research.simulate --tf 1h --params promoted --start 2026-01-01

Data: the setups cached by app/ml/meta_label.py in data/meta_setups/{tf}_{days}d_{SYMBOL}.json (never regenerated
here), labelled with meta_label.label_setups (the live exit profile edge_tp2r: one limit at the zone edge, the
strategy's stop, one take-profit at +2R; TradeSim with taker fee + slippage), and featurised with
meta_label.setup_row, i.e. exactly the dict the live system gets from live_setup_features.

Book rules (identical for the selected-5, take-all and random-5 books):
  - Each UTC day D, the candidates are the setups created during D (setup "time" = close of the setup bar). The
    picker chooses among them (selector: pick_daily; take-all: every setup in time order; random-5: 5 random distinct
    symbols). Picks are placed at their own setup time, in time order.
  - A pick is skipped if its symbol already has an open position/order or 5 positions/orders are open. Skips are
    not back-filled.
  - By default unfilled setups (limit never reached: EXPIRED/INVALIDATED) are candidates too, because live you
    cannot know whether a limit will fill. An unfilled pick earns 0R, is not counted as a trade, and blocks its
    symbol and slot until its expiry (setup time + expiry_bars). `include_unfilled=False` reproduces "filled trades
    only". Setups still open when the data ends are dropped.
  - A filled position occupies its slot from setup time to exit_time (TradeSim's exit bar; if missing, fill time +
    the labeller's max hold of max(3 x hold_bars, 24) bars).
  - Sizing: fixed fraction. Each trade risks `risk` (1%) of realised equity at entry; at exit equity += risk_amount
    x R, with R after fees and slippage.
Known simplification: choosing among the day's setups uses the knowledge of which setups appear later that day (not
their outcomes). Live, the daily job picks from what is on the board at run time.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from app.research.selector import DEFAULT_PARAMS, pick_daily, promoted_params

DAY_MS = 86_400_000
RISK = 0.01
MAX_OPEN = 5
K = 5
BLOCK_DAYS = 10      # bootstrap block length in days (>= typical holding time, so overlapping trades stay together)
N_BOOT = 2000
N_RANDOM = 200

CAVEATS = [
    "Survivorship: the symbol lists are today's liquid survivors (12 coins on 4h, 19 on 1h); delisted/collapsed coins "
    "are absent, which flatters any long-biased method.",
    "Setups of different strategies on the same symbol/bar and trades across correlated coins overlap; the block "
    "bootstrap by day keeps a day's (and neighbouring days') trades together, but correlation across coins is real "
    "and five simultaneous positions behave more like 2-3 independent bets.",
    "Exit profile is edge_tp2r (single +2R target) as labelled by meta_label.label_setups; live 4h signals currently "
    "use edge_tp1r_q50, so live R distributions differ.",
    "Selection within a day uses the day's full candidate list (availability, not outcomes).",
    "Backtest snapshots have no order book or derivatives data (funding history only where cached).",
]


# ---------------------------------------------------------------- data

def _tf_ms(tf: str) -> int:
    from app.data.timeframes import tf_ms

    return tf_ms(tf)


def _cache_root(cache_dir: str | None) -> Path:
    from app.ml.meta_label import CACHE

    return Path(cache_dir) if cache_dir else CACHE


def cached_days(tf: str, cache_dir: str | None = None) -> int | None:
    """The history length (days) of the meta_label setup cache for tf (most common if several)."""
    days = Counter(int(p.name.split("_")[1][:-1]) for p in _cache_root(cache_dir).glob(f"{tf}_*d_*.json"))
    return days.most_common(1)[0][0] if days else None


def _load_cached(symbol: str, tf: str, days: int, cache_dir: str | None = None
                 ) -> tuple[pd.DataFrame, list[dict]] | None:
    """Candles + setups from the cache file, pinned to the bars the cache was generated on. Never regenerates."""
    from app.ml.meta_label import load_history

    path = _cache_root(cache_dir) / f"{tf}_{days}d_{symbol}.json"
    if not path.exists():
        return None
    c = json.loads(path.read_text())
    full = load_history(symbol, tf, None)
    df = full[(full.open_time >= c["first_open_time"]) & (full.open_time <= c["last_open_time"])]
    df = df.reset_index(drop=True)
    if len(df) != c["n_bars"]:
        print(f"  skip {symbol}: candle history no longer matches the cache ({len(df)} vs {c['n_bars']} bars)")
        return None
    return df, c["setups"]


def label_with_targets(df: pd.DataFrame, setups: list[dict], fee: float, slip_bps: float,
                       targets_r: Sequence[float] | None = None) -> list[dict]:
    """meta_label.label_setups (edge_tp2r), or the same edge entry and stop with take-profits at `targets_r` R
    (e.g. (1.5,) one target at +1.5R; (1.0, 2.0) half at +1R, stop to break-even, half at +2R). Same TradeSim rules,
    expiry, time stop, fee and slippage."""
    from app.ml.meta_label import PROFILE, label_setups
    from app.signals.simulator import TradeSim
    from app.strategies.exits import apply_exit_profile

    if not targets_r:
        return label_setups(df, setups, fee, slip_bps)
    o, h, lo, c, tm = (df[k].to_numpy() for k in ("open", "high", "low", "close", "open_time"))
    out = []
    for s0 in setups:
        s = apply_exit_profile(s0, PROFILE)
        sgn = 1 if s["direction"] == "LONG" else -1
        edge = s["entry_high"] if sgn > 0 else s["entry_low"]
        risk = abs(edge - s["stop"])
        s["targets"] = [edge + sgn * m * risk for m in targets_r]
        sim = TradeSim(s["direction"], s["entry_low"], s["entry_high"], s["stop"], list(s["targets"]),
                       expiry_bars=s["expiry_bars"], max_hold_bars=max(3 * s["hold_bars"], 24), fee=fee,
                       slip_bps=slip_bps)
        j = s["bar"] + 1
        while j < len(df) and not sim.terminal:
            sim.on_bar(int(tm[j]), o[j], h[j], lo[j], c[j])
            j += 1
        filled = sim.fill is not None
        done = filled and sim.terminal
        out.append({**s, "outcome": sim.outcome or "OPEN_AT_END", "filled": filled,
                    "r": float(sim.realized_r) if done else None,
                    "label": (1 if sim.outcome == "TARGET_HIT" else 0) if done else None,
                    "fill_time": sim.fill_time, "exit_time": sim.exit_time})
    return out


def _pending_contexts(labelled: list[dict], df: pd.DataFrame, tfms: int) -> list[dict[str, float]]:
    """pending_long/short at each labelled setup's time from the symbol's earlier setups (context.pending_counts)."""
    from app.research.context import annotate_pending, pending_counts

    ann = annotate_pending(labelled, df, tfms)
    order = sorted(range(len(ann)), key=lambda i: int(ann[i]["time"]))
    times = [int(ann[i]["time"]) for i in order]
    horizon = 64 * tfms  # >> the longest expiry (12 bars): older setups cannot still be waiting
    out: list[dict[str, float]] = [{} for _ in ann]
    lo = 0
    for k, i in enumerate(order):
        t = times[k]
        while lo < k and times[lo] < t - horizon:
            lo += 1
        out[i] = pending_counts(t, (ann[order[m]] for m in range(lo, k)))
    return out


@lru_cache(maxsize=16)
def _candidates_cached(tf: str, days: int, cost_mult: float, include_unfilled: bool, cache_dir: str | None = None,
                       targets_r: tuple | None = None, with_context: bool = True) -> tuple[dict, ...]:
    from app.config import get_settings
    from app.ml.features import build_features
    from app.ml.meta_label import base_feature_columns, load_funding, setup_row
    from app.research.context import DailyPanel, load_daily, market_context

    s = get_settings()
    fee, slip = s.taker_fee * cost_mult, s.slippage_bps * cost_mult
    tfms = _tf_ms(tf)
    syms = sorted(p.name.split("_", 2)[2][:-5] for p in _cache_root(cache_dir).glob(f"{tf}_{days}d_*.json"))
    frames = {sym: x for sym in syms if (x := _load_cached(sym, tf, days, cache_dir)) is not None}
    btc = frames["BTCUSDT"][0] if "BTCUSDT" in frames else None
    cols = base_feature_columns(True)
    # context universe: the dataset's coins plus BTCUSDT (docs/research/HYPOTHESES_2026-11-11.md)
    panel = DailyPanel(load_daily(sorted({*frames, "BTCUSDT"}))) if with_context else None
    out: list[dict] = []
    for sym, (df, setups) in frames.items():
        if not setups:
            continue
        base = build_features(df, btc if sym != "BTCUSDT" else None).reindex(columns=cols)
        close = df["close"].to_numpy()
        fund = load_funding(sym)
        labelled = label_with_targets(df, setups, fee, slip, targets_r)
        pend = _pending_contexts(labelled, df, tfms) if with_context else None
        for i, t in enumerate(labelled):
            filled = bool(t["filled"])
            if t["outcome"] == "OPEN_AT_END" or (filled and t["r"] is None):
                continue
            if not filled and not include_unfilled:
                continue
            ctx = None
            if with_context:
                ctx = market_context(sym, int(t["time"]), panel, fund)
                ctx.update(pend[i])
            row = setup_row(base.iloc[t["bar"]].to_dict(), float(close[t["bar"]]), t, fund, ctx)
            row.update(candidate_meta(t, sym, tfms))
            out.append(row)
    out.sort(key=lambda r: (r["time"], r["symbol"], r["strategy"]))
    return tuple(out)


def candidate_meta(t: Mapping, symbol: str, tfms: int) -> dict:
    """Book-keeping fields of one labelled setup (see module docstring for the release-time rules)."""
    time = int(t["time"])
    filled = bool(t.get("filled"))
    max_hold = max(3 * int(t.get("hold_bars") or 0), 24) * tfms
    if filled:
        if t.get("exit_time") is not None:
            release = int(t["exit_time"])
        else:
            release = int(t.get("fill_time") or time) + max_hold
    else:
        release = time + int(t.get("expiry_bars") or 0) * tfms
    return {"symbol": symbol, "strategy": t["strategy"], "direction": t["direction"], "time": time,
            "day": time // DAY_MS, "filled": filled, "r": float(t["r"]) if filled else 0.0,
            "label": t.get("label"), "outcome": t["outcome"], "fill_time": t.get("fill_time"),
            "exit_time": t.get("exit_time"), "release_time": max(release, time)}


def load_candidates(tf: str, days: int | None = None, cost_mult: float = 1.0,
                    include_unfilled: bool = True, cache_dir: str | None = None,
                    targets_r: Sequence[float] | None = None, with_context: bool = True) -> list[dict]:
    """All resolved setups of the cache for tf as selector rows (setup_row features incl. the market-context
    features + book-keeping fields: symbol, strategy, direction, time, day, filled, r, label, outcome, fill_time,
    exit_time, release_time). cost_mult scales taker fee and slippage (stress test). cache_dir: another setup cache
    in the meta_label format (hypothesis holdout sets); targets_r: re-label with other take-profits (see
    label_with_targets). Cached in-process; do not mutate the rows."""
    days = days or cached_days(tf, cache_dir)
    if days is None:
        return []
    return list(_candidates_cached(tf, days, float(cost_mult), bool(include_unfilled),
                                   str(cache_dir) if cache_dir else None,
                                   tuple(float(x) for x in targets_r) if targets_r else None, bool(with_context)))


# ---------------------------------------------------------------- book

Picker = Callable[[list[Mapping]], list[Mapping]]


def selector_picker(params: Mapping, k: int = K) -> Picker:
    return lambda rows: pick_daily(rows, params, k)


def take_all_picker(rows: list[Mapping]) -> list[Mapping]:
    return sorted(rows, key=lambda r: (r["time"], r["symbol"], r["strategy"], r["direction"]))


def random_picker(rng: random.Random, k: int = K) -> Picker:
    def pick(rows: list[Mapping]) -> list[Mapping]:
        rs = sorted(rows, key=lambda r: (r["time"], r["symbol"], r["strategy"], r["direction"]))
        rng.shuffle(rs)
        out, seen = [], set()
        for r in rs:
            if r["symbol"] not in seen:
                seen.add(r["symbol"])
                out.append(r)
                if len(out) == k:
                    break
        return out
    return pick


def window(rows: Sequence[Mapping], start_ms: int | None = None, end_ms: int | None = None,
           purge: bool = False) -> list[Mapping]:
    """Rows with start <= time < end. purge=True also drops rows that resolve at/after `end` (training windows:
    no label may depend on bars after the cut)."""
    out = []
    for r in rows:
        if start_ms is not None and r["time"] < start_ms:
            continue
        if end_ms is not None and (r["time"] >= end_ms or (purge and r["release_time"] >= end_ms)):
            continue
        out.append(r)
    return out


def run_book(rows: Sequence[Mapping], picker: Picker, max_open: int = MAX_OPEN, risk: float = RISK,
             first_day: int | None = None, last_day: int | None = None) -> dict:
    """Day-by-day book over `rows` (already windowed). Returns the opened trades (with entry equity, risk amount and
    R), counts of skipped/unfilled picks and the daily realised equity curve (equity starts at 1.0)."""
    by_day: dict[int, list[Mapping]] = defaultdict(list)
    for r in rows:
        by_day[int(r["day"])].append(r)
    if not by_day and (first_day is None or last_day is None):
        return {"trades": [], "picks": 0, "skipped": 0, "unfilled": 0, "curve_days": [], "curve": []}
    d0 = min(by_day) if first_day is None else first_day
    d1 = max(by_day) if last_day is None else last_day
    equity = 1.0
    open_pos: list[dict] = []
    trades: list[dict] = []
    picks = skipped = unfilled = 0
    curve_days: list[int] = []
    curve: list[float] = []

    def release(until: int, inclusive: bool) -> None:
        nonlocal equity
        due = [p for p in open_pos if (p["release"] <= until if inclusive else p["release"] < until)]
        for p in sorted(due, key=lambda p: (p["release"], p["symbol"])):
            equity += p["risk_amt"] * p["r"]
            open_pos.remove(p)

    day = d0
    while day <= d1 or open_pos:
        for r in sorted(picker(by_day.get(day, [])) if day <= d1 else [],
                        key=lambda r: (r["time"], r["symbol"])):
            release(int(r["time"]), inclusive=True)
            if len(open_pos) >= max_open or any(p["symbol"] == r["symbol"] for p in open_pos):
                skipped += 1
                continue
            picks += 1
            if r["filled"]:
                amt = risk * equity
                open_pos.append({"symbol": r["symbol"], "release": int(r["release_time"]), "risk_amt": amt,
                                 "r": float(r["r"])})
                trades.append({"symbol": r["symbol"], "strategy": r["strategy"], "direction": r["direction"],
                               "time": int(r["time"]), "day": day, "exit_time": int(r["release_time"]),
                               "r": float(r["r"]), "outcome": r["outcome"], "risk_amt": amt,
                               "score": r.get("selector_score")})
            else:
                unfilled += 1
                open_pos.append({"symbol": r["symbol"], "release": int(r["release_time"]), "risk_amt": 0.0,
                                 "r": 0.0})
        release((day + 1) * DAY_MS, inclusive=False)
        curve_days.append(day)
        curve.append(equity)
        day += 1
    return {"trades": trades, "picks": picks, "skipped": skipped, "unfilled": unfilled,
            "curve_days": curve_days, "curve": curve}


# ---------------------------------------------------------------- metrics

def _day_str(day: int) -> str:
    return datetime.fromtimestamp(day * 86_400, UTC).strftime("%Y-%m-%d")


def max_drawdown(curve: Sequence[float]) -> float:
    if not curve:
        return 0.0
    a = np.asarray([1.0, *curve])
    peak = np.maximum.accumulate(a)
    return float(np.max(1 - a / peak))


def block_bootstrap_ci(trade_days: Sequence[int], rs: Sequence[float], d0: int, d1: int, block: int = BLOCK_DAYS,
                       n_boot: int = N_BOOT, seed: int = 0) -> list[float] | None:
    """95% CI of mean R per trade from a circular block bootstrap over calendar days (blocks of `block` days),
    so trades of the same and neighbouring days are resampled together."""
    if len(rs) < 5 or d1 < d0:
        return None
    n_days = d1 - d0 + 1
    sum_r, cnt = np.zeros(n_days), np.zeros(n_days)
    for d, r in zip(trade_days, rs):
        sum_r[d - d0] += r
        cnt[d - d0] += 1
    b = max(1, min(block, n_days))
    nb = math.ceil(n_days / b)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n_days, size=(n_boot, nb))
    idx = ((starts[:, :, None] + np.arange(b)) % n_days).reshape(n_boot, -1)[:, :n_days]
    s, c = sum_r[idx].sum(axis=1), cnt[idx].sum(axis=1)
    means = s[c > 0] / c[c > 0]
    if not len(means):
        return None
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def quick_metrics(book: Mapping) -> dict:
    """Cheap metrics (used inside the evolution loop)."""
    rs = np.array([t["r"] for t in book["trades"]], dtype=float)
    n_days = max(len(book["curve"]), 1)
    years = n_days / 365.25
    final = book["curve"][-1] if book["curve"] else 1.0
    return {"n_trades": int(len(rs)), "picks": int(book["picks"]), "skipped": int(book["skipped"]),
            "unfilled_picks": int(book["unfilled"]), "days": int(n_days),
            "trades_per_day": float(len(rs) / n_days),
            "win_rate": float((rs > 0).mean()) if len(rs) else None,
            "hit_2r_rate": float(np.mean([t["outcome"] == "TARGET_HIT" for t in book["trades"]])) if len(rs) else None,
            "expectancy_r": float(rs.mean()) if len(rs) else None,
            "total_r": float(rs.sum()),
            "final_equity": float(final),
            "cagr": float(final ** (1 / years) - 1) if years > 0 and final > 0 else None,
            "max_drawdown": max_drawdown(book["curve"])}


def full_metrics(book: Mapping, seed: int = 0, curve: bool = True) -> dict:
    m = quick_metrics(book)
    trades = book["trades"]
    days = book["curve_days"]
    if days:
        m["start"], m["end"] = _day_str(days[0]), _day_str(days[-1])
        m["expectancy_ci95"] = block_bootstrap_ci([t["day"] for t in trades], [t["r"] for t in trades],
                                                  days[0], days[-1], seed=seed)
    else:
        m["expectancy_ci95"] = None
    # monthly returns from the daily realised equity curve
    eq = pd.Series(book["curve"], index=pd.to_datetime([d * DAY_MS for d in days], unit="ms"), dtype=float)
    monthly: list[dict] = []
    if len(eq):
        me = eq.groupby(eq.index.to_period("M")).last()
        prev = 1.0
        for per, v in me.items():
            monthly.append({"month": str(per), "return": float(v / prev - 1)})
            prev = float(v)
    m["monthly_returns"] = monthly
    m["worst_month"] = min(monthly, key=lambda x: x["return"]) if monthly else None
    m["best_month"] = max(monthly, key=lambda x: x["return"]) if monthly else None
    m["positive_months_frac"] = float(np.mean([x["return"] > 0 for x in monthly])) if monthly else None
    years: list[dict] = []
    if len(eq):
        by_year_tr: dict[int, list[float]] = defaultdict(list)
        for t in trades:
            by_year_tr[datetime.fromtimestamp(t["time"] / 1000, UTC).year].append(t["r"])
        prev = 1.0
        for y, grp in eq.groupby(eq.index.year):
            rs = np.array(by_year_tr.get(int(y), []), dtype=float)
            years.append({"year": int(y), "n_trades": int(len(rs)),
                          "win_rate": float((rs > 0).mean()) if len(rs) else None,
                          "expectancy_r": float(rs.mean()) if len(rs) else None, "total_r": float(rs.sum()),
                          "return": float(grp.iloc[-1] / prev - 1),
                          "max_drawdown": max_drawdown(list(grp.to_numpy() / prev))})
            prev = float(grp.iloc[-1])
    m["per_year"] = years
    m["by_strategy"] = {}
    for sid in sorted({t["strategy"] for t in trades}):
        rs = np.array([t["r"] for t in trades if t["strategy"] == sid])
        m["by_strategy"][sid] = {"n": int(len(rs)), "expectancy_r": float(rs.mean()), "total_r": float(rs.sum())}
    if curve:
        m["equity_curve"] = [{"date": _day_str(d), "equity": round(float(v), 6)} for d, v in zip(days, book["curve"])]
    return m


def drop_best_month(book: Mapping) -> dict:
    """Expectancy and total R after removing every trade entered in the calendar month with the highest total R."""
    by_m: dict[str, list[float]] = defaultdict(list)
    for t in book["trades"]:
        by_m[datetime.fromtimestamp(t["time"] / 1000, UTC).strftime("%Y-%m")].append(t["r"])
    if not by_m:
        return {"dropped_month": None, "n_trades": 0, "expectancy_r": None, "total_r": 0.0}
    best = max(by_m, key=lambda k: sum(by_m[k]))
    rs = [r for k, v in by_m.items() if k != best for r in v]
    return {"dropped_month": best, "dropped_r": float(sum(by_m[best])), "n_trades": len(rs),
            "expectancy_r": float(np.mean(rs)) if rs else None, "total_r": float(sum(rs))}


# ---------------------------------------------------------------- public API

def _to_ms(x: str | int | None) -> int | None:
    if x is None:
        return None
    if isinstance(x, int | float):
        return int(x)
    return int(datetime.fromisoformat(x).replace(tzinfo=UTC).timestamp() * 1000)


def random_baseline(rows: Sequence[Mapping], n: int = N_RANDOM, seed: int = 0, k: int = K,
                    max_open: int = MAX_OPEN, risk: float = RISK, first_day: int | None = None,
                    last_day: int | None = None) -> dict:
    """Mean of quick_metrics over n random-5 books (each day: k random distinct-symbol setups), plus the spread."""
    runs = [quick_metrics(run_book(rows, random_picker(random.Random(seed + i), k), max_open, risk,
                                   first_day, last_day)) for i in range(n)]
    out: dict = {"n_draws": n, "seed": seed}
    for key in ("n_trades", "trades_per_day", "win_rate", "hit_2r_rate", "expectancy_r", "total_r", "cagr",
                "max_drawdown", "final_equity"):
        vals = np.array([r[key] for r in runs if r[key] is not None], dtype=float)
        out[key] = float(vals.mean()) if len(vals) else None
        if key in ("expectancy_r", "total_r", "cagr") and len(vals):
            out[f"{key}_p05_p95"] = [float(np.quantile(vals, 0.05)), float(np.quantile(vals, 0.95))]
    out["_expectancies"] = [r["expectancy_r"] for r in runs]
    return out


def simulate_rows(rows: Sequence[Mapping], params: Mapping | None = None, start: str | int | None = None,
                  end: str | int | None = None, *, k: int = K, max_open: int = MAX_OPEN, risk: float = RISK,
                  n_random: int = N_RANDOM, seed: int = 0, baselines: bool = True, purge: bool = False,
                  curve: bool = True) -> dict:
    """simulate() on given candidate rows (pure; no I/O). Returns selected / take_all / random_5 metrics."""
    params = params or DEFAULT_PARAMS
    s_ms, e_ms = _to_ms(start), _to_ms(end)
    w = window(rows, s_ms, e_ms, purge)
    first = s_ms // DAY_MS if s_ms is not None else (min(r["day"] for r in w) if w else None)
    last = (e_ms - 1) // DAY_MS if e_ms is not None else (max(r["day"] for r in w) if w else None)
    sel = run_book(w, selector_picker(params, k), max_open, risk, first, last)
    out = {"selected": full_metrics(sel, seed, curve)}
    sel_exp = out["selected"]["expectancy_r"]
    if baselines:
        allb = run_book(w, take_all_picker, max_open, risk, first, last)
        out["take_all"] = full_metrics(allb, seed, curve)
        if n_random:
            rb = random_baseline(w, n_random, seed, k, max_open, risk, first, last)
            exps = [x for x in rb.pop("_expectancies") if x is not None]
            rb["selected_beats_frac"] = (float(np.mean([sel_exp > x for x in exps]))
                                         if exps and sel_exp is not None else None)
            out["random_5"] = rb
    out["drop_best_month"] = drop_best_month(sel)
    out["settings"] = {"k": k, "max_open": max_open, "risk_per_trade": risk, "n_candidates": len(w),
                       "candidate_days": len({r["day"] for r in w}),
                       "candidates_per_day": len(w) / max(len({r["day"] for r in w}), 1),
                       "include_unfilled": any(not r["filled"] for r in w),
                       "symbols": sorted({r["symbol"] for r in w}),
                       "bootstrap": f"circular block bootstrap by calendar day, block {BLOCK_DAYS} days, "
                                    f"{N_BOOT} resamples"}
    return out


def simulate(tf: str, params: Mapping | None = None, start: str | int | None = None, end: str | int | None = None,
             *, k: int = K, max_open: int = MAX_OPEN, risk: float = RISK, n_random: int = N_RANDOM, seed: int = 0,
             include_unfilled: bool = True, cost_mult: float = 1.0, baselines: bool = True,
             curve: bool = True) -> dict:
    """Simulate the daily-5 method for `tf` from the cached setups. params: selector params (default
    DEFAULT_PARAMS). start/end: ISO date or epoch ms (end exclusive). Returns {"selected", "take_all", "random_5",
    "drop_best_month", "settings", "tf", "params", "caveats"}; see module docstring for the book rules."""
    params = params or DEFAULT_PARAMS
    rows = load_candidates(tf, cost_mult=cost_mult, include_unfilled=include_unfilled)
    out = simulate_rows(rows, params, start, end, k=k, max_open=max_open, risk=risk, n_random=n_random, seed=seed,
                        baselines=baselines, curve=curve)
    out.update({"tf": tf, "days_cached": cached_days(tf), "cost_mult": cost_mult,
                "params": {k_: v for k_, v in params.items()}, "params_source": params.get("source", "explicit"),
                "caveats": CAVEATS})
    return out


# ---------------------------------------------------------------- CLI

def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _r(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}R"


def format_table(res: Mapping) -> str:
    lines = ["| Book | Trades | /day | Win | +2R hit | E[R] | 95% CI (block) | Total R | CAGR @1% | Max DD | Worst month |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, key in (("Selected 5", "selected"), ("Take all", "take_all"), ("Random 5 (mean)", "random_5")):
        m = res.get(key)
        if not m:
            continue
        ci = m.get("expectancy_ci95")
        ci_s = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else ("p05-p95 " + ", ".join(f"{x:+.3f}" for x in
                                                                                    m.get("expectancy_r_p05_p95", []))
                                                          if m.get("expectancy_r_p05_p95") else "n/a")
        wm = m.get("worst_month")
        lines.append(f"| {name} | {m['n_trades']:,.0f} | {m['trades_per_day']:.2f} | {_pct(m['win_rate'])[1:]} | "
                     f"{_pct(m['hit_2r_rate'])[1:]} | {_r(m['expectancy_r'])} | {ci_s} | {m['total_r']:+.1f}R | "
                     f"{_pct(m['cagr'])} | {_pct(m['max_drawdown'])[1:]} | "
                     f"{(wm['month'] + ' ' + _pct(wm['return'])) if wm else 'n/a'} |")
    return "\n".join(lines)


def format_years(m: Mapping) -> str:
    lines = ["| Year | Trades | Win | E[R] | Total R | Return | Max DD |", "|---|---|---|---|---|---|---|"]
    for y in m.get("per_year", []):
        lines.append(f"| {y['year']} | {y['n_trades']} | {_pct(y['win_rate'])[1:]} | {_r(y['expectancy_r'])} | "
                     f"{y['total_r']:+.1f}R | {_pct(y['return'])} | {_pct(y['max_drawdown'])[1:]} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Historical simulation of the daily-5 selector")
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--params", default="default", help="default | promoted | path to a JSON params file")
    ap.add_argument("--risk", type=float, default=RISK)
    ap.add_argument("--n-random", type=int, default=N_RANDOM)
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--filled-only", action="store_true", help="candidates = filled setups only")
    ap.add_argument("--out", help="write the full result JSON here")
    a = ap.parse_args()
    if a.params == "default":
        params = {**DEFAULT_PARAMS, "source": "default"}
    elif a.params == "promoted":
        params = promoted_params(a.tf)
    else:
        params = {**json.loads(Path(a.params).read_text()), "source": a.params}
    res = simulate(a.tf, params, a.start, a.end, risk=a.risk, n_random=a.n_random, cost_mult=a.cost_mult,
                   include_unfilled=not a.filled_only)
    st = res["settings"]
    sel = res["selected"]
    print(f"{a.tf}: {len(st['symbols'])} symbols, {sel.get('start')} .. {sel.get('end')}, {st['n_candidates']:,} "
          f"candidate setups ({st['candidates_per_day']:.1f} per active day), params {res['params_source']}, "
          f"risk {a.risk:.1%}/trade, max {MAX_OPEN} open, cost x{a.cost_mult}")
    print(format_table(res))
    rb = res.get("random_5") or {}
    if rb.get("selected_beats_frac") is not None:
        print(f"selected expectancy beats {rb['selected_beats_frac']:.0%} of {rb['n_draws']} random-5 draws")
    print(f"unfilled picks: selected {sel['unfilled_picks']}, take-all {res['take_all']['unfilled_picks']}; "
          f"skipped by limits: selected {sel['skipped']}, take-all {res['take_all']['skipped']}")
    db = res["drop_best_month"]
    print(f"selected without best month ({db['dropped_month']}): E={_r(db['expectancy_r'])}")
    print("\nSelected 5 per year:\n" + format_years(sel))
    print("\nTake all per year:\n" + format_years(res["take_all"]))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
