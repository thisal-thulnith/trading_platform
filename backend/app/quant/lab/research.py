"""Phase 2b — factor research report: rank IC by horizon / year / BTC regime, IC decay, turnover, quintile L/S after costs.

Cost assumption (one way, per unit of notional traded): Binance taker fee 0.05% + slippage by 30-day average quote
volume: >= $100M 0.02%, $20-100M 0.05%, $5-20M 0.10%, < $5M 0.25%. Funding is NOT included in this report (shorts
in the quintile spread are a statistical construct; the portfolio backtest in validate.py does charge/credit funding).

CLI: ``uv run python -m app.quant.lab.research`` -> data/quant_lab/factor_report.json and factor_report.md
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.quant.lab import data as qd
from app.quant.lab.factors import (
    ANN,
    FACTOR_DOC,
    FACTORS,
    Wide,
    btc_regime,
    build_wide,
    compute_factors,
    cost_rate,
    forward_returns,
    newey_west_t,
    nw_lags,
    rank_ic,
    universe,
)

HORIZONS = (1, 3, 7)
DECAY_LAGS = (0, 1, 2, 4, 6, 9, 13, 20)
REPORT_JSON = qd.ROOT / "factor_report.json"
REPORT_MD = qd.ROOT / "factor_report.md"

# Pre-specified screen (written before looking at results): a factor "passes" when its 7-day rank IC has
# |Newey-West t| >= 2, the same sign in >= 60% of calendar years with >= 60 days, and (report only) the oriented
# weekly-rebalanced quintile long-short is positive after costs.
SCREEN_T = 2.0
SCREEN_YEAR_CONSISTENCY = 0.6


def next_day_returns(w: Wide) -> pd.DataFrame:
    """r[D] = return from close D to close D+1 (delisted -> 0, i.e. exit at the last close)."""
    cf = w.close.ffill()
    return (cf.shift(-1) / cf - 1).where(w.close.ffill().notna())


def ic_tables(facs: dict[str, pd.DataFrame], w: Wide, univ: pd.DataFrame,
              horizons=HORIZONS) -> dict[int, pd.DataFrame]:
    out = {}
    for h in horizons:
        fwd = forward_returns(w, h)
        out[h] = pd.DataFrame({k: rank_ic(v, fwd, univ) for k, v in facs.items()})
    return out


def screen(ic7: pd.DataFrame, dates: pd.DatetimeIndex | None = None, h: int = 7) -> dict[str, int]:
    """Factors passing the pre-specified IC screen on ``dates`` -> {factor: sign}."""
    x = ic7.loc[ic7.index.intersection(dates)] if dates is not None else ic7
    out = {}
    for f in x.columns:
        s = x[f].dropna()
        if len(s) < 120:
            continue
        t = newey_west_t(s, nw_lags(h))
        if not np.isfinite(t) or abs(t) < SCREEN_T:
            continue
        sign = 1 if s.mean() > 0 else -1
        yr = s.groupby(s.index.year).agg(["mean", "size"])
        yr = yr[yr["size"] >= 60]
        if len(yr) and (np.sign(yr["mean"]) == sign).mean() >= SCREEN_YEAR_CONSISTENCY:
            out[f] = sign
    return out


def quintile_ls(factor: pd.DataFrame, univ: pd.DataFrame, r_next: pd.DataFrame, costs: pd.DataFrame,
                rebalance: int = 1, q: float = 0.2, sign: int = 1, series: bool = False) -> dict:
    """Equal-weight top-minus-bottom quantile spread, rebalanced every ``rebalance`` days, after costs."""
    x = (factor * sign).where(univ)
    pr = x.rank(axis=1, pct=True)
    n = x.notna().sum(axis=1)
    ok = n >= 10
    top, bot = (pr > 1 - q) & ok.to_numpy()[:, None], (pr <= q) & ok.to_numpy()[:, None]
    wl = top.div(top.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    ws = bot.div(bot.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    uw = univ.div(univ.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    if rebalance > 1:
        keep = np.arange(len(wl)) % rebalance == 0
        wl = wl.where(pd.Series(keep, index=wl.index), np.nan).ffill().fillna(0)
        ws = ws.where(pd.Series(keep, index=ws.index), np.nan).ffill().fillna(0)
        uw = uw.where(pd.Series(keep, index=uw.index), np.nan).ffill().fillna(0)
    r = r_next.fillna(0)
    gl, gs, gu = (wl * r).sum(axis=1), (ws * r).sum(axis=1), (uw * r).sum(axis=1)
    tl = (wl - wl.shift(1).fillna(0)).abs()
    ts = (ws - ws.shift(1).fillna(0)).abs()
    tu = (uw - uw.shift(1).fillna(0)).abs()
    c = costs.fillna(costs.max().max())
    cl, cs, cu = (tl * c).sum(axis=1), (ts * c).sum(axis=1), (tu * c).sum(axis=1)
    live = ok
    ls_g = (gl - gs)[live]
    ls_n = (gl - gs - cl - cs)[live]
    ex_n = (gl - cl - (gu - cu))[live]
    out = {
        "ls_gross": _ann(ls_g), "ls_net": _ann(ls_n), "long_excess_net": _ann(ex_n),
        "turnover_per_day": float(((tl + ts).sum(axis=1))[live].mean()),
    }
    if series:
        out["ls_net_series"] = ls_n
    return out


def _ann(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {"ann_return": None, "ann_vol": None, "sharpe": None, "days": int(len(r))}
    mu, sd = r.mean() * ANN, r.std() * np.sqrt(ANN)
    return {"ann_return": round(float(mu), 4), "ann_vol": round(float(sd), 4),
            "sharpe": round(float(mu / sd), 3) if sd > 0 else None, "days": int(len(r))}


def factor_turnover(factor: pd.DataFrame, univ: pd.DataFrame, q: float = 0.2) -> dict:
    x = factor.where(univ)
    ac = rank_ic(x, x.shift(1), univ & univ.shift(1, fill_value=False))
    pr = x.rank(axis=1, pct=True)
    top = (pr > 1 - q).astype(float)
    prev = top.shift(1)
    both = (top * prev).sum(axis=1)
    size = top.sum(axis=1)
    churn = (1 - both / size.replace(0, np.nan)).where(prev.sum(axis=1) > 0)
    return {"rank_autocorr_1d": round(float(ac.mean()), 4), "top_quintile_churn_per_day": round(float(churn.mean()), 4)}


def factor_row(f: str, fac: pd.DataFrame, tables: dict[int, pd.DataFrame], univ: pd.DataFrame, regime: pd.Series,
               r1: pd.DataFrame, costs: pd.DataFrame, passed: dict, doc: str) -> dict:
    """One factor's report row: IC by horizon / year / regime, decay, turnover, quintile L/S after costs."""
    row: dict = {"doc": doc, "ic": {}}
    for h in HORIZONS:
        s = tables[h][f].dropna()
        row["ic"][h] = {"mean": round(float(s.mean()), 4), "std": round(float(s.std()), 4),
                        "t_nw": round(newey_west_t(s, nw_lags(h)), 2), "hit": round(float((s > 0).mean()), 3),
                        "days": int(len(s))}
    s7 = tables[7][f].dropna()
    row["ic7_by_year"] = {int(y): round(float(v), 4) for y, v in s7.groupby(s7.index.year).mean().items()}
    reg = regime.reindex(s7.index)
    row["ic7_by_regime"] = {
        "btc_above_200d": round(float(s7[reg == True].mean()), 4),   # noqa: E712
        "btc_below_200d": round(float(s7[reg == False].mean()), 4),  # noqa: E712
        "t_above": round(newey_west_t(s7[reg == True], 14), 2),      # noqa: E712
        "t_below": round(newey_west_t(s7[reg == False], 14), 2),     # noqa: E712
    }
    dec = {}
    for k in DECAY_LAGS:
        fwd_k = r1.shift(-k)  # return from close D+k to D+k+1
        dec[k] = round(float(rank_ic(fac, fwd_k, univ).mean()), 4)
    row["ic_decay_1d_return_at_lag"] = dec
    row["turnover"] = factor_turnover(fac, univ)
    sign = 1 if row["ic"][7]["mean"] >= 0 else -1
    row["orientation_sign"] = sign
    row["quintile_ls_daily"] = quintile_ls(fac, univ, r1, costs, 1, sign=sign)
    row["quintile_ls_weekly"] = quintile_ls(fac, univ, r1, costs, 7, sign=sign)
    row["passes_ic_screen"] = f in passed
    sh = row["quintile_ls_weekly"]["ls_net"]["sharpe"]
    row["passes"] = bool(f in passed and sh is not None and sh > 0)
    return row


def build_report(w: Wide, facs: dict[str, pd.DataFrame], univ: pd.DataFrame,
                 tables: dict[int, pd.DataFrame] | None = None) -> dict:
    t0 = time.time()
    tables = tables or ic_tables(facs, w, univ)
    regime = btc_regime(w)
    r1 = next_day_returns(w)
    costs = cost_rate(w)
    passed = screen(tables[7])
    rep: dict = {"generated": datetime.now(UTC).isoformat(), "factors": {}, "screen_passed": passed,
                 "period": [str(w.dates[0].date()), str(w.dates[-1].date())],
                 "cost_model": {"taker_fee_per_side": 0.0005,
                                "slippage_per_side_by_adv30": {">=100M": 0.0002, "20-100M": 0.0005, "5-20M": 0.0010,
                                                               "<5M": 0.0025},
                                "funding": "not included in the factor report"},
                 "screen_rule": f"|NW t(IC7)| >= {SCREEN_T}, same sign in >= {SCREEN_YEAR_CONSISTENCY:.0%} of years",
                 "universe": universe_stats(w, univ)}
    for f in FACTORS:
        rep["factors"][f] = factor_row(f, facs[f], tables, univ, regime, r1, costs, passed, FACTOR_DOC[f])
    rep["n_passing"] = sum(r["passes"] for r in rep["factors"].values())
    rep["seconds"] = round(time.time() - t0, 1)
    return rep


def universe_stats(w: Wide, univ: pd.DataFrame) -> dict:
    n = univ.sum(axis=1)
    n = n[n > 0]
    syms = qd.load_symbols().set_index("symbol") if qd.SYMBOLS.exists() else None
    ever = univ.columns[univ.any(axis=0)]
    out = {"daily_size_by_year": {int(y): {"min": int(g.min()), "median": int(g.median()), "max": int(g.max())}
                                  for y, g in n.groupby(n.index.year)},
           "distinct_symbols_ever_in_universe": int(len(ever)), "first_day": str(n.index[0].date())}
    if syms is not None:
        d = syms.reindex(ever)["delisted"].fillna(False).astype(bool)
        out["of_which_delisted"] = int(d.sum())
        out["delisted_examples"] = sorted(d[d].index.tolist())[:40]
    return out


def to_markdown(rep: dict) -> str:
    L = ["# Factor report", "", f"Period {rep['period'][0]} to {rep['period'][1]}. Universe: listed, >= 90 days of "
         "history, 30-day average quote volume >= $5M. Rank IC = daily cross-sectional Spearman with the forward "
         "return; t = Newey-West. L/S = top minus bottom quintile, oriented by the sign of the 7-day IC (in-sample "
         "orientation), after taker fee + liquidity-bucket slippage, no funding.", "",
         "| factor | IC1 | IC3 | IC7 | t(IC7) | IC7 bull | IC7 bear | rank AC | L/S daily net SR | L/S weekly net SR "
         "| weekly turnover/day | passes |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for f, r in rep["factors"].items():
        L.append(f"| {f} | {r['ic'][1]['mean']:+.4f} | {r['ic'][3]['mean']:+.4f} | {r['ic'][7]['mean']:+.4f} | "
                 f"{r['ic'][7]['t_nw']:+.2f} | {r['ic7_by_regime']['btc_above_200d']:+.4f} | "
                 f"{r['ic7_by_regime']['btc_below_200d']:+.4f} | {r['turnover']['rank_autocorr_1d']:.3f} | "
                 f"{_f(r['quintile_ls_daily']['ls_net']['sharpe'])} | {_f(r['quintile_ls_weekly']['ls_net']['sharpe'])} | "
                 f"{r['quintile_ls_weekly']['turnover_per_day']:.3f} | {'yes' if r['passes'] else 'no'} |")
    years = sorted({y for r in rep["factors"].values() for y in r["ic7_by_year"]})
    L += ["", "## 7-day IC by year", "", "| factor | " + " | ".join(map(str, years)) + " |",
          "|---|" + "---|" * len(years)]
    for f, r in rep["factors"].items():
        L.append(f"| {f} | " + " | ".join(f"{r['ic7_by_year'].get(y, float('nan')):+.3f}" for y in years) + " |")
    L += ["", "## IC decay (IC of factor on D with the 1-day return from D+k to D+k+1)", "",
          "| factor | " + " | ".join(f"k={k}" for k in DECAY_LAGS) + " |", "|---|" + "---|" * len(DECAY_LAGS)]
    for f, r in rep["factors"].items():
        d = r["ic_decay_1d_return_at_lag"]
        L.append(f"| {f} | " + " | ".join(f"{d[k]:+.4f}" for k in DECAY_LAGS) + " |")
    u = rep["universe"]
    L += ["", "## Universe size", "", "| year | min | median | max |", "|---|---|---|---|"]
    for y, v in u["daily_size_by_year"].items():
        L.append(f"| {y} | {v['min']} | {v['median']} | {v['max']} |")
    L += ["", f"Distinct symbols ever in the universe: {u['distinct_symbols_ever_in_universe']}"
          + (f", of which delisted: {u.get('of_which_delisted')}" if "of_which_delisted" in u else "") + ".",
          "", f"Factors passing (IC screen + positive weekly net L/S): {rep['n_passing']} — "
          + ", ".join(f for f, r in rep["factors"].items() if r["passes"])]
    return "\n".join(L) + "\n"


def _f(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


def load_inputs(min_adv: float = 5e6, min_history: int = 90):
    panel = qd.load_panel()
    fund = qd.load_funding_daily()
    w = build_wide(panel, fund)
    facs = compute_factors(w)
    univ = universe(w, min_history, min_adv)
    return w, facs, univ


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Quant lab factor research report")
    ap.add_argument("--min-adv", type=float, default=5e6)
    ap.add_argument("--min-history", type=int, default=90)
    a = ap.parse_args(argv)
    t0 = time.time()
    w, facs, univ = load_inputs(a.min_adv, a.min_history)
    print(f"inputs: {w.close.shape} in {time.time() - t0:.0f}s", flush=True)
    rep = build_report(w, facs, univ)
    rep["config"] = {"min_adv": a.min_adv, "min_history": a.min_history}
    rep["runtime_s"] = round(time.time() - t0, 1)
    qd.ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(rep, indent=1, default=str))
    REPORT_MD.write_text(to_markdown(rep))
    print(to_markdown(rep))
    print(f"wrote {REPORT_JSON} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
