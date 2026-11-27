"""Coin simulator: the platform's systematic signal replayed day by day on every coin's history (pure functions).

Each day D, for every coin, the components the live fusion uses are rebuilt from data dated <= D only:
  momentum      cross-sectional percentile of the 28-day volatility-adjusted return in D's universe       ±1
  carry         -tanh(funding per year / 60%) from the 7-day mean of daily funding (perp only)            ±1
  regime        BTC above / below its 200-day average                                                   ±0.6
  order_flow    tanh(CVD % / 5), CVD = taker buy minus sell quote volume over 4 days, % of volume       ±1
  quant_factors the lab's factor composite, re-screened every 1 January on data that was already known  ±1
Fundamentals, chart setups and the AI desk have no history for 591 coins and are left out (their live weights are
small or zero). F = Σ wᵢsᵢ / Σ wᵢ over the components with data, exactly as app/signal/fusion.fuse.

Trading rules for one coin (one account per coin, all of its equity as margin):
  - decided at the close of D, filled at the open of D+1 (never at the close the signal saw);
  - open long when F >= entry, short when F <= -entry (shorts and leverage only where a perp existed that day);
  - close when F falls back through `exit` (or flip when it crosses the other entry), or the coin leaves the
    tradeable universe (30-day volume < $5M or < 90 days of history), or it delists (exit at its last close);
  - stop at entry ∓ stop_atr × ATR(14) checked on the day's high/low (filled at the stop, or at the open when it gaps
    through), then no re-entry on that side until F falls back through `exit`;
  - optional partial profit: half closed at +partial_r × R (R = initial stop distance), stop moved to the entry;
  - optional take profit at +x% from entry (checked after the stop: a candle reaching both counts as stopped);
  - a 1x long is held as spot (no funding, no liquidation); shorts and leveraged longs are perps with isolated margin,
    0.5% maintenance: the stop always sits before the liquidation price, and a gap through it loses the whole
    account (the run stops);
  - costs: taker fee + slippage by liquidity bucket on every fill (app/quant/lab/factors.cost_rate); funding charged
    or received on perp positions every day.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

START = 10_000.0
MM = 0.005  # maintenance margin
ORDER_FLOW_DAYS = 4  # ~24 four-hour candles, as the live order-flow component


@dataclass
class SimConfig:
    start: str = "2021-01-01"
    end: str | None = None
    entry: float = 0.10
    exit: float = 0.0
    shorts: bool = True
    leverage: float = 1.0
    stop_atr: float | None = 3.0
    take_profit: float | None = None  # close at +x (0.03 = 3%) from entry; None = exit on the signal only
    partial_r: float | None = None  # close half at entry ± partial_r × the initial stop distance, then stop to entry
    weights: str = "live"  # live (today's evidence weights) | prior (literature priors only: strictly out of sample)
    n_null: int = 200
    mc_paths: int = 1000
    mc_days: int = 365
    seed: int = 0

    def key(self) -> str:
        import hashlib
        import json

        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class Market:
    dates: pd.DatetimeIndex
    symbols: pd.Index
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    univ: np.ndarray
    cost: np.ndarray
    fund: np.ndarray  # daily summed funding (fraction), NaN where no perp
    atr: np.ndarray
    comps: dict[str, np.ndarray]
    signs: dict[str, dict[str, int]]  # year -> factor signs used by the composite that year


# ---------------------------------------------------------------- components (point in time)

def _pct(x: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    return x.where(mask).rank(axis=1, pct=True)


def pit_signs(ic7: pd.DataFrame, years: list[int], screen) -> dict[int, dict[str, int]]:
    """Composite factor signs for each year Y from the IC screen on the 7-day ICs already known on 1 January Y."""
    out = {}
    for y in years:
        cut = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=8)  # a 7-day forward return is known 7 days later
        out[y] = screen(ic7.loc[:cut]) if (ic7.index <= cut).sum() >= 120 else {}
    return out


def composite(facs: dict[str, pd.DataFrame], univ: pd.DataFrame, signs_by_year: dict[int, dict[str, int]]) -> pd.DataFrame:
    """Equal-weight composite of centred factor ranks with each year's point-in-time signs (NaN when no signs)."""
    out = pd.DataFrame(np.nan, index=univ.index, columns=univ.columns)
    ranks = {f: _pct(v, univ) - 0.5 for f, v in facs.items()}
    for y, sg in signs_by_year.items():
        if not sg:
            continue
        rows = univ.index.year == y
        acc = sum(ranks[f].loc[rows] * s for f, s in sg.items())
        out.loc[rows] = (acc * math.sqrt(12) / len(sg)).to_numpy()
    return out.where(univ)


def components(w, facs: dict[str, pd.DataFrame], univ: pd.DataFrame, comp: pd.DataFrame,
               regime: pd.Series) -> dict[str, pd.DataFrame]:
    """Component scores (dates x symbols, -1..+1, NaN where unknown) as the live fusion scores them."""
    out = {"momentum": 2 * _pct(facs["vadj_mom_28"], univ) - 1}
    f_ann = facs["funding_7"] * 365 * 100
    out["carry"] = -np.tanh(f_ann / 60)
    reg = regime.map({True: 0.6, False: -0.6}).astype(float)
    out["regime"] = pd.DataFrame(np.repeat(reg.to_numpy()[:, None], len(univ.columns), axis=1),
                                 index=univ.index, columns=univ.columns).where(w.close.notna())
    tb = w.taker_buy_quote.rolling(ORDER_FLOW_DAYS, min_periods=ORDER_FLOW_DAYS).sum()
    tq = w.quote_volume.rolling(ORDER_FLOW_DAYS, min_periods=ORDER_FLOW_DAYS).sum()
    cvd_pct = ((2 * tb / tq - 1) * 100).where(tq > 0)
    out["order_flow"] = np.tanh(cvd_pct / 5)
    out["quant_factors"] = 2 * _pct(comp, univ & comp.notna()) - 1
    return {k: v.replace([np.inf, -np.inf], np.nan) for k, v in out.items()}


def atr14(w) -> pd.DataFrame:
    pc = w.close.shift(1)
    tr = pd.concat([(w.high - w.low), (w.high - pc).abs(), (w.low - pc).abs()]).groupby(level=0).max()
    return tr.reindex(w.close.index).rolling(14, min_periods=10).mean()


def fuse(comps: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    """F = Σ wᵢsᵢ / Σ wᵢ over components with data (NaN where none has data)."""
    num = den = 0.0
    for k, s in comps.items():
        wk = weights.get(k, 0.0)
        if wk <= 0:
            continue
        ok = np.isfinite(s)
        num = num + np.where(ok, s, 0.0) * wk
        den = den + ok * wk
    if isinstance(den, float):
        shape = next(iter(comps.values())).shape
        return np.full(shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)


# ---------------------------------------------------------------- one coin

def run_coin(o, h, l, c, univ, cost, fund, atr, F, t0: int, t1: int, cfg: SimConfig) -> dict:  # noqa: E741
    """Simulate one coin over [t0, t1] (inclusive). Returns equity, exposure and trades."""
    T = len(c)
    eq = np.full(T, np.nan)
    expo = np.zeros(T)
    cash, side, qty, entry, stop, lev, perp_pos = START, 0, 0.0, 0.0, math.nan, 1.0, False
    pending: tuple | None = None
    blocked = 0
    trades: list[dict] = []
    cur: dict | None = None
    costs = funding_paid = 0.0
    ruined = False
    valid = np.flatnonzero(np.isfinite(c[t0:t1 + 1]))
    last_valid = t0 + int(valid[-1]) if len(valid) else -1

    def open_(t, tgt, tlev, sdist, px):
        nonlocal cash, side, qty, entry, stop, lev, costs, cur, perp_pos
        lev = tlev
        perp_pos = tgt < 0 or lev > 1  # a 1x long is held as spot: no funding, no liquidation
        qty = lev * cash / px
        fee = cost[t] * qty * px
        cur = {"side": "long" if tgt > 0 else "short", "entry_t": t, "entry_px": px, "lev": lev, "eq0": cash,
               "cost": fee, "funding": 0.0}
        cash -= fee
        costs += fee
        side, entry = tgt, px
        if perp_pos and cfg.stop_atr and math.isfinite(sdist) and sdist > 0:
            liq = (qty * px - cash) / (qty * (1 - MM)) if tgt > 0 else (cash + qty * px) / (qty * (1 + MM))
            sdist = min(sdist, 0.8 * abs(px - liq))  # the stop always sits before the liquidation price
        stop = px - tgt * sdist if (cfg.stop_atr and math.isfinite(sdist) and sdist > 0) else math.nan
        cur["r_dist"] = abs(px - stop) if math.isfinite(stop) else math.nan

    def close_(t, px, why):
        nonlocal cash, side, qty, costs, cur
        cash += side * qty * (px - entry)
        fee = cost[t] * qty * px if why != "liquidation" else 0.0
        cash -= fee
        costs += fee
        if why == "liquidation":
            cash = 0.0
        cur.update(exit_t=t, exit_px=px, why=why, cost=cur["cost"] + fee,
                   ret=cash / cur["eq0"] - 1 if cur["eq0"] > 0 else -1.0)
        trades.append(cur)
        cur, side, qty = None, 0, 0.0

    for t in range(t0, t1 + 1):
        if ruined:
            eq[t] = 0.0
            continue
        if not math.isfinite(c[t]):
            eq[t] = eq[t - 1] if t > t0 and math.isfinite(eq[t - 1]) else cash
            expo[t] = side * lev if side else 0.0
            continue
        # 1. yesterday's decision fills at today's open
        if pending is not None and math.isfinite(o[t]) and o[t] > 0:
            tgt, tlev, sdist = pending
            if side and tgt != side:
                close_(t, o[t], "signal")
            if tgt and not side:
                open_(t, tgt, tlev, sdist, o[t])
            pending = None
        # 2. stop and liquidation inside the day
        if side:
            if side > 0:
                liq = (qty * entry - cash) / (qty * (1 - MM)) if perp_pos else math.nan
                hit = [x for x in (stop, liq) if math.isfinite(x) and x > 0 and l[t] <= x]
                if hit:
                    lvl = max(hit)
                    px = min(o[t], lvl) if math.isfinite(o[t]) else lvl
                    if math.isfinite(liq) and px <= liq:
                        close_(t, px, "liquidation")
                        ruined = True
                    else:
                        close_(t, px, "stop")
                        blocked = 1
            else:
                liq = (cash + qty * entry) / (qty * (1 + MM))
                hit = [x for x in (stop, liq) if math.isfinite(x) and h[t] >= x]
                if hit:
                    lvl = min(hit)
                    px = max(o[t], lvl) if math.isfinite(o[t]) else lvl
                    if px >= liq:
                        close_(t, px, "liquidation")
                        ruined = True
                    else:
                        close_(t, px, "stop")
                        blocked = -1
            if ruined:
                eq[t] = 0.0
                continue
        # 2a. partial profit: half off at +partial_r R, the rest protected at the entry price
        if side and cfg.partial_r and not cur.get("partial") and math.isfinite(cur.get("r_dist", math.nan)):
            lvl = entry + side * cfg.partial_r * cur["r_dist"]
            if (side > 0 and h[t] >= lvl) or (side < 0 and l[t] <= lvl):
                gap = math.isfinite(o[t]) and cur["entry_t"] < t and (o[t] >= lvl if side > 0 else o[t] <= lvl)
                px = o[t] if gap else lvl
                q = qty / 2
                cash += side * q * (px - entry)
                fee = cost[t] * q * px
                cash -= fee
                costs += fee
                cur["cost"] += fee
                qty -= q
                stop = entry
                cur["partial"] = True
        # 2b. take profit (after the stop: when a candle reaches both, the stop is assumed first)
        if side and cfg.take_profit:
            tp = entry * (1 + side * cfg.take_profit)
            if (side > 0 and h[t] >= tp) or (side < 0 and l[t] <= tp):
                gap = math.isfinite(o[t]) and cur["entry_t"] < t and (o[t] >= tp if side > 0 else o[t] <= tp)
                close_(t, o[t] if gap else tp, "take profit")
        # 3. funding on the position held through the day
        if side and perp_pos and math.isfinite(fund[t]):
            f = side * qty * c[t] * fund[t]
            cash -= f
            funding_paid += f
            cur["funding"] += f
        if side and t == last_valid and t < t1:
            close_(t, c[t], "delisted")
        eq[t] = cash + (side * qty * (c[t] - entry) if side else 0.0)
        expo[t] = side * lev if side else 0.0
        if t == last_valid and t < t1:
            continue  # no candles after this: nothing more to decide
        # 4. decide at the close
        f = F[t]
        perp = math.isfinite(fund[t])
        if blocked == 1 and math.isfinite(f) and f < cfg.exit:
            blocked = 0
        if blocked == -1 and math.isfinite(f) and f > -cfg.exit:
            blocked = 0
        can_long = blocked != 1
        can_short = cfg.shorts and perp and blocked != -1
        tgt = side
        if not univ[t]:
            tgt = 0
        elif not math.isfinite(f):
            pass
        elif side > 0:
            if f < cfg.exit:
                tgt = -1 if (f <= -cfg.entry and can_short) else 0
        elif side < 0:
            if f > -cfg.exit:
                tgt = 1 if (f >= cfg.entry and can_long) else 0
        elif f >= cfg.entry and can_long:
            tgt = 1
        elif f <= -cfg.entry and can_short:
            tgt = -1
        if tgt != side and t < t1:
            sdist = (cfg.stop_atr or 0) * atr[t] if math.isfinite(atr[t]) else math.nan
            pending = (tgt, cfg.leverage if perp else 1.0, sdist)
    if cur is not None:  # still open at the end: marked to the last close
        lt = last_valid if last_valid >= 0 else t1
        cur.update(exit_t=None, exit_px=float(c[lt]), why="open",
                   ret=(eq[lt] / cur["eq0"] - 1) if cur["eq0"] > 0 else 0.0)
        trades.append(cur)
    return {"eq": eq, "expo": expo, "trades": trades, "costs": costs, "funding": funding_paid, "ruined": ruined}


def hold_curve(o, c, cost, t0: int, t1: int) -> np.ndarray:
    """Buy at the first open in range, hold (1x, spot); flat at the last close after a delisting."""
    eq = np.full(len(c), np.nan)
    first = next((t for t in range(t0, t1 + 1) if math.isfinite(o[t]) and o[t] > 0 and math.isfinite(c[t])), None)
    if first is None:
        return eq
    units = START * (1 - cost[first]) / o[first]
    cf = pd.Series(c[first:t1 + 1]).ffill().to_numpy()
    eq[first:t1 + 1] = units * cf
    return eq


# ---------------------------------------------------------------- tests and statistics

def null_test(expo: np.ndarray, ret: np.ndarray, cost: np.ndarray, a: int, b: int, n: int, seed: int) -> dict:
    """Random timing: the same positions shifted to random dates (circular shift within the coin's live window).
    Statistic: mean daily return after costs. p = share of shifted books doing at least as well."""
    x = expo[a:b + 1].astype(float)
    r = np.nan_to_num(ret[a:b + 1])
    k = np.nan_to_num(cost[a:b + 1], nan=0.003)
    m = len(x)
    if m < 120 or not np.any(x):
        return {"p": None, "obs": None, "null_mean": None}

    def stat(X):
        return (X[..., :-1] * r[1:] - k[1:] * np.abs(np.diff(X, axis=-1))).mean(axis=-1)

    obs = float(stat(x))
    rng = np.random.default_rng(seed)
    shifts = rng.integers(30, m - 30, n)
    idx = (np.arange(m)[None, :] + shifts[:, None]) % m
    nul = stat(x[idx])
    return {"p": float((1 + (nul >= obs).sum()) / (n + 1)), "obs": obs, "null_mean": float(nul.mean())}


def bh(pvals: list[float | None], q: float = 0.10) -> list[bool]:
    """Benjamini-Hochberg: which hypotheses pass at false-discovery rate q."""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out = [False] * len(pvals)
    if not m:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    kmax = 0
    for rank, i in enumerate(order, 1):
        if pvals[i] <= rank / m * q:
            kmax = rank
    for i in order[:kmax]:
        out[i] = True
    return out


def stats(eq: np.ndarray, dates: pd.DatetimeIndex) -> dict:
    ok = np.isfinite(eq)
    if ok.sum() < 2:
        return {}
    e, d = eq[ok], dates[ok]
    r = np.diff(e) / np.where(e[:-1] > 0, e[:-1], np.nan)
    r = r[np.isfinite(r)]
    peak = np.maximum.accumulate(e)
    dd = float(np.max(1 - e / np.where(peak > 0, peak, 1)))
    yrs = max((d[-1] - d[0]).days / 365, 1 / 365)
    sd = r.std(ddof=1) if len(r) > 2 else 0
    by_year = {}
    s = pd.Series(e, index=d)
    prev = s.iloc[0]
    for y, g in s.groupby(s.index.year):
        by_year[str(y)] = round((g.iloc[-1] / prev - 1) * 100, 2) if prev > 0 else None
        prev = g.iloc[-1]
    return {"return_pct": round((e[-1] / e[0] - 1) * 100, 2),
            "cagr_pct": round(((e[-1] / e[0]) ** (1 / yrs) - 1) * 100, 2) if e[-1] > 0 else -100.0,
            "sharpe": round(float(r.mean() / sd * math.sqrt(365)), 2) if sd > 0 else None,
            "max_dd_pct": round(dd * 100, 2), "days": int(len(e)), "by_year": by_year}


def trade_stats(trades: list[dict]) -> dict:
    rs = [t["ret"] for t in trades if t.get("why") != "open"]
    if not rs:
        return {"trades": 0}
    wins, losses = [x for x in rs if x > 0], [x for x in rs if x <= 0]
    gl = -sum(losses)
    return {"trades": len(rs), "win_rate": round(len(wins) / len(rs) * 100, 1),
            "avg_trade_pct": round(float(np.mean(rs)) * 100, 2),
            "best_pct": round(max(rs) * 100, 2), "worst_pct": round(min(rs) * 100, 2),
            "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None,
            "stops": sum(t["why"] == "stop" for t in trades), "liquidations": sum(t["why"] == "liquidation" for t in trades)}


def monte_carlo(daily: np.ndarray, paths: int, days: int, seed: int, block: int = 10) -> dict | None:
    """Stationary-block bootstrap of the daily returns into `paths` one-year futures (keeps volatility clustering)."""
    r = daily[np.isfinite(daily)]
    if len(r) < 60:
        return None
    rng = np.random.default_rng(seed)
    n = len(r)
    out = np.empty((paths, days))
    for p in range(paths):
        i, pos = 0, rng.integers(n)
        while i < days:
            if rng.random() < 1 / block:
                pos = rng.integers(n)
            out[p, i] = r[pos % n]
            pos += 1
            i += 1
    out = np.maximum(out, -1.0)
    curves = np.cumprod(1 + out, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((paths, 1)), curves], axis=1), axis=1)[:, 1:]
    dd = np.max(1 - curves / peak, axis=1)
    step = max(1, days // 73)
    qs = np.percentile(curves[:, ::step], [5, 25, 50, 75, 95], axis=0) * START
    end = curves[:, -1]
    return {"days": days, "paths": paths, "step": step,
            "bands": {k: [round(float(v), 2) for v in qs[i]] for i, k in enumerate(("p5", "p25", "p50", "p75", "p95"))},
            "p_loss": round(float((end < 1).mean()) * 100, 1), "p_dd30": round(float((dd > 0.3).mean()) * 100, 1),
            "p_ruin": round(float((end < 0.05).mean()) * 100, 1),
            "median_return_pct": round(float(np.median(end) - 1) * 100, 1)}
