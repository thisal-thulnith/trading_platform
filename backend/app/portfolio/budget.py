"""Budget planner: what the system's rules mean for the user's own budget — the settings the agents would invest with.

Given a budget, a risk mode, a venue and the leverage the user wants, it answers from today's prices and volatility:
risk per trade in dollars, the position and margin a typical ticket needs, which of the coins are too small to trade
at the exchange minimum, the mode (or the bigger budget) that makes most coins tradeable, and what the research says
a year like 2020-now, 2024 or 2025-26 would do to this budget. Numbers only; the desk agents put them into words.
"""
import numpy as np
import pandas as pd

from app.risk import futures as fut
from app.risk.portfolio import RISK_MODES, rules_for
from app.strategies import daily_trend as dt

SPOT_MIN_NOTIONAL = 5.0
TRADEABLE_SHARE = 0.8  # a budget "fits" when at least this share of the coins clears the exchange minimum


def _min_notional(sym: str, venue: str, filters: dict) -> float:
    if venue != "futures":
        return SPOT_MIN_NOTIONAL
    f = filters.get(sym) or fut.DEFAULT_FILTERS.get(sym) or fut.Filters()
    return f.min_notional


def coin_rows(budget: float, mode: str, venue: str, leverage: int, frames: dict[str, pd.DataFrame], filters: dict
              ) -> list[dict]:
    """Per coin: the stop a ticket would have today, the position and margin it needs, and whether the budget can
    place it at all."""
    rules = rules_for(mode, budget)
    risk_usd = budget * rules.risk_pct / 100
    rows = []
    for sym, f in frames.items():
        row = f.iloc[-1]
        close, atr = float(row.close), float(row.atr)
        if not np.isfinite(atr) or close <= 0:
            continue
        stop_pct = max(dt.STOP_ATR * atr / close, dt.MIN_STOP_PCT)
        per_unit_pct = stop_pct + 2 * rules.fee + rules.slip
        size = min(risk_usd / per_unit_pct, budget * rules.max_position_pct / 100)
        min_notional = _min_notional(sym, venue, filters)
        safe = fut.safe_leverage(stop_pct, sym) if venue == "futures" else 1
        lev = max(1, min(leverage, safe)) if venue == "futures" else 1
        rows.append({"symbol": sym, "stop_pct": round(stop_pct * 100, 1), "size_usd": round(size, 2),
                     "margin_usd": round(size / lev, 2), "leverage": lev, "safe_leverage": safe,
                     "min_notional": min_notional, "tradeable": bool(size >= min_notional),
                     # the budget at which this coin's ticket just clears the exchange minimum in this mode
                     "budget_needed_usd": float(np.ceil(min_notional * per_unit_pct / (rules.risk_pct / 100) / 10) * 10)})
    return rows


def _expectations(budget: float, mode: str, proof: dict | None) -> list[dict]:
    out = []
    for period, res in ((proof or {}).get(mode) or {}).items():
        if period == "rules" or not isinstance(res, dict) or "return_pct" not in res:
            continue
        out.append({"period": period, "return_pct": res["return_pct"], "yearly_pct": res.get("yearly_pct"),
                    "end_usd": round(budget * (1 + res["return_pct"] / 100), 2),
                    "max_drawdown_pct": res.get("max_drawdown_pct"),
                    "worst_drop_usd": round(budget * (res.get("max_drawdown_pct") or 0) / 100, 2)})
    return out


def plan(budget: float, mode: str, venue: str, leverage: int, frames: dict[str, pd.DataFrame], filters: dict,
         proof: dict | None = None) -> dict:
    rules = rules_for(mode, budget)
    rows = coin_rows(budget, mode, venue, leverage, frames, filters)
    tradeable = [r for r in rows if r["tradeable"]]
    too_small = sorted((r for r in rows if not r["tradeable"]), key=lambda r: r["budget_needed_usd"])
    med = (lambda k: round(float(np.median([r[k] for r in rows])), 2)) if rows else (lambda k: None)
    rec = {"mode": mode, "budget_needed_usd": None, "switch": False,
           "text": f"Your budget fits {mode} mode: {len(tradeable)} of {len(rows)} coins are tradeable."}
    if rows and len(tradeable) < TRADEABLE_SHARE * len(rows):
        better = None
        for m in RISK_MODES:  # modes are ordered from the lowest risk up
            if RISK_MODES[m]["risk_pct"] <= rules.risk_pct:
                continue
            ok = sum(r["tradeable"] for r in coin_rows(budget, m, venue, leverage, frames, filters))
            if ok >= TRADEABLE_SHARE * len(rows):
                better = (m, ok)
                break
        if better:
            m, ok = better
            exp = ((proof or {}).get(m) or {}).get("all 2020-now") or {}
            rec = {"mode": m, "budget_needed_usd": None, "switch": True,
                   "text": f"Only {len(tradeable)} of {len(rows)} coins are tradeable at ${budget:,.0f} in {mode} mode. "
                           f"Switch to {m} mode ({RISK_MODES[m]['risk_pct']:g}% per trade): {ok} of {len(rows)} become "
                           f"tradeable. Higher risk grows faster and falls harder"
                           + (f" (research {exp.get('yearly_pct')}%/year, worst drop {exp.get('max_drawdown_pct')}%)."
                              if exp else ".")}
        else:
            need = float(np.percentile([r["budget_needed_usd"] for r in rows], TRADEABLE_SHARE * 100))
            rec = {"mode": mode, "budget_needed_usd": round(need, 0), "switch": True,
                   "text": f"Only {len(tradeable)} of {len(rows)} coins are tradeable at ${budget:,.0f} in {mode} mode, "
                           f"and no mode fixes that. About ${need:,.0f} makes {TRADEABLE_SHARE:.0%} of the coins tradeable "
                           f"at {rules.risk_pct:g}% risk; until then the system will skip the small tickets."}
    return {"budget_usd": budget, "mode": mode, "venue": venue, "leverage_wanted": leverage,
            "risk_pct": rules.risk_pct, "risk_per_trade_usd": round(budget * rules.risk_pct / 100, 2),
            "max_positions": rules.max_positions, "max_open_risk_usd": round(budget * rules.max_open_risk_pct / 100, 2),
            "max_per_coin_usd": round(budget * rules.max_position_pct / 100, 2),
            "typical_stop_pct": med("stop_pct"), "typical_position_usd": med("size_usd"),
            "typical_margin_usd": med("margin_usd"), "typical_leverage": med("leverage"),
            "coins_total": len(rows), "coins_tradeable": len(tradeable),
            "too_small": [{k: r[k] for k in ("symbol", "size_usd", "min_notional", "budget_needed_usd")} for r in too_small[:10]],
            "recommendation": rec, "expectations": _expectations(budget, mode, proof)}
