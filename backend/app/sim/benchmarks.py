"""Head-to-head: the strategies competitors' bots and charting platforms sell, run on every coin through the same
simulator (same data, next-open fills, fees, slippage, funding, liquidation, stops, luck test) as the platform's signal.

What is and is not testable: rule-based templates (EMA cross, RSI, MACD, Supertrend, Bollinger, breakout, DCA bot,
momentum index) are public and replayable. Proprietary AI grades/signals (Token Metrics, Tickeron, Intellectia...) have
no public history per coin, so they cannot be replayed here; their published claims are compared in the report instead.
Grid bots need intraday prices and are left out (daily candles would flatter or punish them arbitrarily).

    uv run python -m app.sim.benchmarks            # run all, write data/sim/compare.json
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd

from app.sim import service
from app.sim.engine import START, SimConfig, stats

# name -> (as sold by, kind, sides it is usually run with)
STRATEGIES = {
    "ema_cross": ("EMA 20/50 crossover", "TradingView strategy; 3Commas, Cryptohopper, Coinrule, WunderTrading templates"),
    "supertrend": ("Supertrend (10, 3)", "TradingView's most-used trend indicator; signal bots and Telegram groups"),
    "macd": ("MACD (12, 26, 9) cross", "TradingView; Cryptohopper, Coinrule, Altrady templates"),
    "donchian": ("20-day breakout (Turtle)", "TradingView strategies; TrendSpider; trend-following bots"),
    "rsi": ("RSI(14) buy < 30, sell > 50", "Cryptohopper, Coinrule, 3Commas, Pionex templates"),
    "bollinger": ("Bollinger (20, 2) buy the lower band", "Cryptohopper, Coinrule, TradingView mean-reversion"),
    "dca_bot": ("DCA bot (base + 5 safety orders, +3% TP)", "3Commas, Pionex, Bitget, Binance, Cryptohopper DCA bots"),
    "momentum_index": ("Top-10 momentum index, weekly", "AI index / rotation products (Token Metrics indices, Stoic)"),
}
TREND = ("ema_cross", "supertrend", "macd", "donchian")
MEAN_REV = ("rsi", "bollinger")


# ---------------------------------------------------------------- indicator signals (dates x symbols, decided at D)

def _ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def _sign(cond_up: pd.DataFrame, valid: pd.DataFrame) -> np.ndarray:
    return np.where(valid.to_numpy(), np.where(cond_up.to_numpy(), 1.0, -1.0), np.nan)


def ema_cross(c: pd.DataFrame) -> np.ndarray:
    a, b = _ema(c, 20), _ema(c, 50)
    return _sign(a > b, a.notna() & b.notna())


def macd(c: pd.DataFrame) -> np.ndarray:
    line = _ema(c, 12) - _ema(c, 26)
    sig = line.ewm(span=9, adjust=False, min_periods=9).mean()
    return _sign(line > sig, line.notna() & sig.notna())


def supertrend(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 10, k: float = 3.0) -> np.ndarray:  # noqa: E741
    T, N = c.shape
    pc = np.vstack([np.full((1, N), np.nan), c[:-1]])
    tr = np.nanmax(np.stack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    atr = pd.DataFrame(tr).rolling(n, min_periods=n).mean().to_numpy()
    mid = (h + l) / 2
    ub, lb = mid + k * atr, mid - k * atr
    fu, fl = np.full(N, np.nan), np.full(N, np.nan)
    d = np.full(N, np.nan)
    out = np.full((T, N), np.nan)
    for t in range(T):
        ok = np.isfinite(ub[t]) & np.isfinite(c[t])
        prev_c = pc[t]
        nfu = np.where(np.isfinite(fu) & (ub[t] >= fu) & (prev_c <= fu), fu, ub[t])
        nfl = np.where(np.isfinite(fl) & (lb[t] <= fl) & (prev_c >= fl), fl, lb[t])
        nd = np.where(np.isfinite(d), d, 1.0)
        nd = np.where(c[t] > np.where(np.isfinite(fu), fu, np.inf), 1.0, np.where(c[t] < np.where(np.isfinite(fl), fl, -np.inf), -1.0, nd))
        fu, fl, d = np.where(ok, nfu, np.nan), np.where(ok, nfl, np.nan), np.where(ok, nd, np.nan)
        out[t] = d
    return out


def _stateful(enter_long: np.ndarray, exit_long: np.ndarray, enter_short=None, exit_short=None, valid=None) -> np.ndarray:
    """Hold +1 from an entry until its exit (and -1 likewise); NaN where the indicator is not defined."""
    T, N = enter_long.shape
    pos = np.zeros(N)
    out = np.zeros((T, N))
    for t in range(T):
        pos = np.where((pos > 0) & exit_long[t], 0.0, pos)
        if enter_short is not None:
            pos = np.where((pos < 0) & exit_short[t], 0.0, pos)
            pos = np.where((pos <= 0) & enter_long[t], 1.0, np.where((pos >= 0) & enter_short[t], -1.0, pos))
        else:
            pos = np.where(enter_long[t], 1.0, pos)
        if valid is not None:
            pos = np.where(valid[t], pos, 0.0)
        out[t] = pos
    return out if valid is None else np.where(valid, out, np.nan)


def rsi_signal(c: pd.DataFrame) -> np.ndarray:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    r = rsi.to_numpy()
    return _stateful(np.nan_to_num(r, nan=50) < 30, np.nan_to_num(r, nan=50) > 50, valid=np.isfinite(r))


def bollinger(c: pd.DataFrame) -> np.ndarray:
    mid = c.rolling(20, min_periods=20).mean()
    lo = mid - 2 * c.rolling(20, min_periods=20).std()
    v = (mid.notna() & c.notna()).to_numpy()
    return _stateful((c < lo).to_numpy(), (c > mid).to_numpy(), valid=v)


def donchian(c: pd.DataFrame, h: pd.DataFrame, l: pd.DataFrame) -> np.ndarray:  # noqa: E741
    hi20, lo20 = h.rolling(20, min_periods=20).max().shift(1), l.rolling(20, min_periods=20).min().shift(1)
    hi10, lo10 = h.rolling(10, min_periods=10).max().shift(1), l.rolling(10, min_periods=10).min().shift(1)
    v = (hi20.notna() & c.notna()).to_numpy()
    return _stateful((c > hi20).to_numpy(), (c < lo10).to_numpy(), (c < lo20).to_numpy(), (c > hi10).to_numpy(), valid=v)


# ---------------------------------------------------------------- DCA bot (its own loop)

DCA_STEPS = (0.03, 0.06, 0.09, 0.12, 0.15)
DCA_SIZES = (1.0, 1.0, 1.5, 2.25, 3.375, 5.0625)  # base order, then safety orders scaled 1.5x
DCA_TP = 0.03


def dca_coin(o, h, l, c, univ, cost, t0: int, t1: int) -> dict:  # noqa: E741
    """3Commas-style long DCA bot on spot: base order at the open, safety orders as limits 3/6/9/12/15% below it,
    sell everything at +3% over the average price; no stop loss (as these bots are usually run). Daily candles: a
    limit fills when the day's low reaches it; the take profit fills when the high reaches it, but never on the same day
    as a safety order (the candle does not say which came first)."""
    T = len(c)
    eq, expo = np.full(T, np.nan), np.zeros(T)
    cash, qty, spent, base_px, filled, cap = START, 0.0, 0.0, 0.0, 0, 0.0
    trades: list[dict] = []
    costs = 0.0
    cur = None
    valid = np.flatnonzero(np.isfinite(c[t0:t1 + 1]))
    last = t0 + int(valid[-1]) if len(valid) else -1
    unit_total = sum(DCA_SIZES)

    def sell(t, px, why):
        nonlocal cash, qty, spent, costs, cur, filled
        fee = cost[t] * qty * px
        cash += qty * px - fee
        costs += fee
        cur.update(exit_t=t, exit_px=px, why=why, ret=cash / cur["eq0"] - 1, cost=cur["cost"] + fee)
        trades.append(cur)
        cur, qty, spent, filled = None, 0.0, 0.0, 0

    def buy(t, px, size):
        nonlocal cash, qty, spent, costs
        notional = min(cash, cap * size / unit_total)
        fee = cost[t] * notional
        q = (notional - fee) / px
        cash -= notional
        qty += q
        spent += notional
        costs += fee
        cur["cost"] += fee

    for t in range(t0, t1 + 1):
        if not math.isfinite(c[t]):
            eq[t] = eq[t - 1] if t > t0 and math.isfinite(eq[t - 1]) else cash
            continue
        if cur is None and t > t0 and univ[t - 1] and math.isfinite(o[t]) and o[t] > 0 and t < last:
            cap, base_px = cash, o[t]
            cur = {"side": "long", "entry_t": t, "entry_px": o[t], "lev": 1.0, "eq0": cash, "cost": 0.0, "funding": 0.0}
            buy(t, o[t], DCA_SIZES[0])
            filled = 1
        so_today = False
        if cur is not None:
            while filled <= len(DCA_STEPS) and l[t] <= base_px * (1 - DCA_STEPS[filled - 1]):
                lvl = base_px * (1 - DCA_STEPS[filled - 1])
                buy(t, min(o[t], lvl) if t > cur["entry_t"] else lvl, DCA_SIZES[filled])
                filled += 1
                so_today = True
            avg = spent / qty if qty else 0
            tp = avg * (1 + DCA_TP)
            # a daily candle does not say whether the low (safety order) or the high came first: on a day a safety
            # order fills, the take profit waits for a later day (the conservative order)
            if qty and h[t] >= tp and not so_today:
                sell(t, max(o[t], tp) if t > cur["entry_t"] else tp, "take profit")
            elif t == last and t < t1:
                sell(t, c[t], "delisted")
        eq[t] = cash + qty * c[t]
        expo[t] = (qty * c[t] / eq[t]) if eq[t] > 0 else 0.0
    if cur is not None:
        cur.update(exit_t=None, exit_px=float(c[last]) if last >= 0 else 0.0, why="open",
                   ret=(eq[last] / cur["eq0"] - 1) if last >= 0 else 0.0)
        trades.append(cur)
    return {"eq": eq, "expo": expo, "trades": trades, "costs": costs, "funding": 0.0, "ruined": False}


# ---------------------------------------------------------------- rotation index (portfolio level)

def rotation(m, score: np.ndarray, t0: int, t1: int, k: int = 10, every: int = 7) -> dict:
    """Long the top-k coins by `score` in the tradeable universe, equal weight, rebalanced every `every` days;
    decided at the close, held from the next close (one day of delay, no look-ahead), costs on turnover."""
    c = m.c
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = np.nan_to_num(c[1:] / c[:-1] - 1)
    ret = np.vstack([np.zeros((1, c.shape[1])), ret])
    w = np.zeros(c.shape[1])
    eq = [START]
    pending = None
    for t in range(t0 + 1, t1 + 1):
        r = float(w @ ret[t])
        e = eq[-1] * (1 + r)
        w = w * (1 + ret[t]) / max(1 + r, 1e-9)  # the held weights drift with the day's moves
        if pending is not None:  # decided yesterday, rebalanced at today's close
            e -= e * float((np.abs(pending - w) * np.nan_to_num(m.cost[t], nan=0.003)).sum())
            w = pending
            pending = None
        if (t - t0) % every == 0:
            s = np.where(m.univ[t] & np.isfinite(score[t]), score[t], -np.inf)
            top = np.argsort(-s)[:k]
            top = top[np.isfinite(s[top])]
            nw = np.zeros_like(w)
            if len(top):
                nw[top] = 1 / len(top)
            pending = nw
        eq.append(e)
    return {"eq": np.array(eq)}


# ---------------------------------------------------------------- the comparison

def _row(key, name, sold_by, sides, res) -> dict:
    s, p = res["summary"], res["pooled"]
    return {"key": key, "name": name, "sold_by": sold_by, "sides": sides, "return_pct": p.get("return_pct"),
            "cagr_pct": p.get("cagr_pct"), "sharpe": p.get("sharpe"), "max_dd_pct": p.get("max_dd_pct"),
            "by_year": p.get("by_year"), "coins": s["coins"], "beat_hold_pct": round(100 * s["beat_hold"] / s["coins"], 1) if s["coins"] else None,
            "median_sharpe": s["median_sharpe"], "p05": s["p05"], "expected_by_luck": s["expected_by_luck"], "fdr_pass": s["fdr_pass"],
            "trades": s["trades"], "win_rate": s["win_rate"], "avg_trade_pct": s["avg_trade_pct"], "day_hit_rate": s["day_hit_rate"],
            "ruined": s["ruined"], "curve": [{"t": x["t"], "v": x["sim"]} for x in res["curve"]]}


def compare(start: str = "2021-01-01", progress=None) -> dict:
    m = service.market()
    cdf = pd.DataFrame(m.c)
    hdf, ldf = pd.DataFrame(m.h), pd.DataFrame(m.l)
    signals = {"ema_cross": ema_cross(cdf), "macd": macd(cdf), "supertrend": supertrend(m.h, m.l, m.c),
               "donchian": donchian(cdf, hdf, ldf), "rsi": rsi_signal(cdf), "bollinger": bollinger(cdf)}
    rows = []
    jobs = [("ours_live", True), ("ours_live", False), ("ours_prior", True)]
    jobs += [(k, s) for k in TREND for s in (True, False)] + [(k, False) for k in MEAN_REV] + [("dca_bot", False)]
    for n, (key, shorts) in enumerate(jobs):
        cfg = SimConfig(start=start, shorts=shorts, weights="prior" if key == "ours_prior" else "live", n_null=100, mc_paths=200)
        sides = "long + short" if shorts else "long only"
        if key.startswith("ours"):
            res = service.run_all(cfg)
            name = "ConfluenceAI signal" + (" (prior weights)" if key == "ours_prior" else "")
            sold = "this platform — fused systematic signal" + (", literature weights only (out of sample)" if key == "ours_prior" else ", live weights (partly in-sample)")
        elif key == "dca_bot":
            res = service.run_all(cfg, coin_fn=lambda mm, j, a, b: dca_coin(mm.o[:, j], mm.h[:, j], mm.l[:, j], mm.c[:, j], mm.univ[:, j], mm.cost[:, j], a, b))
            name, sold = STRATEGIES[key]
            sides = "long only, no stop"
        else:
            cfg = SimConfig(start=start, shorts=shorts, entry=0.5, exit=0.5, n_null=100, mc_paths=200)
            res = service.run_all(cfg, F=signals[key])
            name, sold = STRATEGIES[key]
        rows.append(_row(f"{key}_{'ls' if shorts else 'l'}", name, sold, sides, res))
        if progress:
            progress((n + 1) / (len(jobs) + 2))
    # portfolio-level indices
    t0, t1 = service._range(m, SimConfig(start=start))
    d = m.dates[t0:t1 + 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        mom = m.c / np.vstack([np.full((28, m.c.shape[1]), np.nan), m.c[:-28]]) - 1
    idx = []
    for key, score, name, sold in (
        ("momentum_index", mom, *STRATEGIES["momentum_index"]),
        ("composite_index", m.comps["quant_factors"], "ConfluenceAI factor composite top-10, weekly", "this platform — the quant lab's composite (point-in-time signs)"),
    ):
        eq = rotation(m, score, t0, t1)["eq"]
        st = stats(eq, d)
        idx.append({"key": key, "name": name, "sold_by": sold, "sides": "long only, 10 coins", **{k: st.get(k) for k in ("return_pct", "cagr_pct", "sharpe", "max_dd_pct", "by_year")},
                    "curve": [{"t": service._ms(d[i]), "v": round(float(eq[i]), 2)} for i in service._thin(len(d), 300)]})
    ew = service.run_all(SimConfig(start=start, n_null=10, mc_paths=50))["ew_hold"]
    out = service.py({"from": str(d[0].date()), "to": str(d[-1].date()), "rows": rows, "indices": idx,
                      "hold_all": {k: ew.get(k) for k in ("return_pct", "cagr_pct", "sharpe", "max_dd_pct", "by_year")},
                      "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "panel": service._panel_key(),
                      "notes": NOTES})
    service.DIR.mkdir(parents=True, exist_ok=True)
    (service.DIR / "compare.json").write_text(json.dumps(out))
    return out


NOTES = [
    "Every strategy runs on the same 591-coin survivorship-free daily panel (delisted coins included), from the same "
    "start date, with the same rules: decided at the close, filled at the next open, fee + slippage by liquidity, "
    "funding on perps, a 3 x ATR stop (except the DCA bot, which is run without a stop as it is usually sold), one "
    "$10,000 account per coin, and a pooled book with equal capital across the coins each strategy holds.",
    "Indicator settings are the defaults these products ship with; no strategy (ours included) was tuned on this data.",
    "Proprietary AI signals (Token Metrics grades, Tickeron, Intellectia, signal groups) publish no per-coin history "
    "and cannot be replayed; grid bots need intraday prices and are not included.",
    "Accuracy: 'win rate' is the share of closed trades that made money after costs; 'day hit rate' is the share of days "
    "in a position on which the price moved the position's way. 50% is a coin flip.",
]


def latest() -> dict | None:
    try:
        return json.loads((service.DIR / "compare.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    t = time.time()
    r = compare(progress=lambda p: print(f"  {p:.0%}", flush=True))
    for x in r["rows"] + r["indices"]:
        print(f"{x['name'][:44]:44} {x['sides'][:18]:18} ret {x['return_pct']!s:>9} sh {x['sharpe']!s:>6} dd {x['max_dd_pct']!s:>6} "
              f"win {x.get('win_rate')!s:>5} hit {x.get('day_hit_rate')!s:>5} p05 {x.get('p05')!s:>4} fdr {x.get('fdr_pass')!s:>3}")
    print("hold all", r["hold_all"])
    print(f"{time.time() - t:.0f}s")
