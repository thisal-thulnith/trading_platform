"""Simulation for one signal: what the engine's own past picks like this one did, and what $margin per trade could do
over the next 20 such trades.

Everything uses the unified engine's out-of-sample record (unified_oos.npz: walk-forward by year, each day predicted
by models that never saw it), the realised R of each plan (style.plan_returns: one target, a day touching both stop and
target counts as the stop, fees included) and the engine's own decision rule (unified.decide). Nothing is fitted here.

Why the engine's PICKS and not "days with a forecast this high": the forecast level on its own is not a trade signal
(across all coins, days whose SHORT 1.5 ATR 3R forecast was >= 1.24R averaged -0.15R); the engine trades the coin that
stands out against the rest of the market that day, so the honest sample is the trades the engine actually took.

    coin_plan   this coin, this plan, every past day, no model      (the plan's own base rate on this coin)
    engine      every trade the engine took (2020 on)               (+ the unseen years 2025-26 on their own)
    side        the engine's trades on this side (long or short)
    plan        the engine's trades with this exact plan
    coin        the engine's trades on this coin
    monte_carlo 10,000 runs of the next 20 trades drawn from the engine's trades on this side in the unseen years
                (then all years, then every trade, when thin), $margin each, no compounding, at each leverage; a
                leverage whose liquidation price comes before the stop is not simulated
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml import forecast as fc
from app.ml import style as sty

N_TRADES = 20
N_PATHS = 10_000
MIN_SAMPLE = 30
LEVERAGES = (1, 3, 5, 10, 20)
_cache: dict = {}


def _record() -> dict | None:
    """The OOS arrays and the engine's past picks under its chosen rule (cached until the files change)."""
    from app.ml import unified as un

    rep = un.report()
    if not un.OOS.exists() or not rep or not rep.get("rule"):
        return None
    m = un.OOS.stat().st_mtime
    if _cache.get("m") != m:
        z = np.load(un.OOS, allow_pickle=True)
        dates = pd.to_datetime(z["date"])
        R = z["R"].astype(np.float64)
        pk = un.decide(z["P"], R, dates, z["atr_pct"], rep["rule"])
        pk = pk[np.isfinite(pk["real"])]
        _cache.update(m=m, sym=z["symbol"].astype(str), R=R, atr=z["atr_pct"], date=z["date"],
                      pk_sym=z["symbol"].astype(str)[pk["row"].to_numpy()], pk_plan=pk["plan"].to_numpy(),
                      pk_real=pk["real"].to_numpy(), pk_year=pd.DatetimeIndex(pk["date"]).year.to_numpy())
    return _cache


def _stats(r: np.ndarray, years: np.ndarray | None = None) -> dict:
    r = r[np.isfinite(r)]
    if not len(r):
        return {"n": 0}
    out = {"n": int(len(r)), "win_rate": round(float((r > 0).mean()), 3), "avg_r": round(float(r.mean()), 3)}
    if years is not None:
        h = r[np.isin(years, fc.HOLDOUT_YEARS)]
        out["unseen"] = {"n": int(len(h)), "win_rate": round(float((h > 0).mean()), 3) if len(h) else None,
                         "avg_r": round(float(h.mean()), 3) if len(h) else None}
    return out


def _liq_pct(lev: int) -> float:
    """The adverse move (in %) that liquidates an isolated position at this leverage (0.5% maintenance margin)."""
    return 100.0 / lev - 0.5


def monte_carlo(r: np.ndarray, stop_pct: float, margin: float = 100.0, leverages=LEVERAGES, seed: int = 7) -> list[dict]:
    """The next N_TRADES trades drawn from past R's: total P/L in $ at each leverage."""
    r = r[np.isfinite(r)]
    if len(r) < MIN_SAMPLE or not stop_pct:
        return []
    rng = np.random.default_rng(seed)
    draws = r[rng.integers(0, len(r), size=(N_PATHS, N_TRADES))]
    out = []
    for lev in leverages:
        if stop_pct >= _liq_pct(lev):
            # liquidation comes before the stop: the record has no intraday path, so even winners may have been
            # liquidated on the way; no honest number exists, so none is shown
            out.append({"leverage": lev, "liquidated_before_stop": True})
            continue
        per = margin * lev * stop_pct / 100.0          # $ lost at the stop (1R)
        tot = (draws * per).sum(axis=1)
        out.append({"leverage": lev, "liquidated_before_stop": False, "risk_per_trade": round(per, 2),
                    "median": round(float(np.median(tot))), "p5": round(float(np.percentile(tot, 5))),
                    "p95": round(float(np.percentile(tot, 95))), "chance_down": round(float((tot < 0).mean()), 3)})
    return out


def simulate(symbol: str, plan: str, stop_pct: float | None, margin: float = 100.0) -> dict:
    """The simulation block for one coin and plan (e.g. 'SHORT_1.5ATR_3R'); stop_pct is today's stop distance in %."""
    z = _record()
    if z is None or plan not in sty.PIDS:
        return {"available": False, "note": "No out-of-sample record yet (the unified engine is not trained)."}
    k = sty.PIDS.index(plan)
    side = plan.split("_")[0]
    side_idx = [i for i, p in enumerate(sty.PIDS) if p.startswith(side)]
    me = np.isfinite(z["R"][:, k]) & (z["atr"] <= 25) & (z["sym"] == symbol)
    real, yrs = z["pk_real"], z["pk_year"]
    m_side, m_plan, m_coin = np.isin(z["pk_plan"], side_idx), z["pk_plan"] == k, z["pk_sym"] == symbol
    out = {"available": True, "plan": plan, "side": side, "margin": margin, "n_trades": N_TRADES,
           "coin_plan": _stats(z["R"][me, k]),
           "engine": _stats(real, yrs), "side_stats": _stats(real[m_side], yrs[m_side]),
           "plan_stats": _stats(real[m_plan], yrs[m_plan]), "coin": _stats(real[m_coin], yrs[m_coin])}
    if me.any():
        out["coin_plan"]["since"] = str(z["date"][me].min())
    # draw from the unseen years first (2025-26: no choice was made on them), then all years, then every engine trade
    hold = np.isin(yrs, fc.HOLDOUT_YEARS)
    sample, src = real[m_side & hold], "side_unseen"
    if len(sample) < MIN_SAMPLE:
        sample, src = real[m_side], "side"
    if len(sample) < MIN_SAMPLE:
        sample, src = real, "engine"
    out["mc_sample"] = src
    out["monte_carlo"] = monte_carlo(sample, float(stop_pct or 0), margin)
    return out
