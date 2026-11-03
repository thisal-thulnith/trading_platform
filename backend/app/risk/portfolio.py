"""System-managed risk for a small spot account (the platform decides size and exposure, not the user).

One daily event loop, used for the research backtest and for the live paper account, so both follow identical rules:
  1. manage open positions on today's candle (stop checked first, a third sold at 2R, stop to entry, ATR trail)
  2. open yesterday's signals at today's open, strongest first, while every rule below allows it

Rules (RiskRules): 1 % of equity at risk per trade (loss at the stop including fees and slippage), at most 4 open
positions and 4 % total open risk (crypto coins fall together, so this is the real portfolio limit), at most 30 % of
equity in one coin, no leverage, orders below the exchange minimum skipped, risk halved after a 10 % drawdown and
quartered after 20 %, and new entries paused for 30 days when the last 30 closed trades average below -0.1R (a broken edge).
Paper only: nothing here places an exchange order.
"""
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from app.strategies import daily_trend as st


@dataclass
class RiskRules:
    capital: float = 100.0
    risk_pct: float = 1.0
    max_positions: int = 4
    max_open_risk_pct: float = 4.0
    max_position_pct: float = 30.0
    min_order_usd: float = 5.0
    fee: float = 0.001  # Binance spot taker, no BNB discount
    slip: float = 0.0005
    dd_half: float = 10.0
    dd_quarter: float = 20.0
    pause_window: int = 30
    pause_below_r: float = -0.1
    pause_days: int = 30
    # exits: share sold at the 2R target, trailing-stop width (ATR) for the rest, and the holding limit (days)
    partial_frac: float = st.PARTIAL
    trail_atr: float = st.TRAIL_ATR
    max_hold_days: int = st.MAX_HOLD_DAYS
    # market breadth gate: buy only when at least this share of coins closed above their 50-day average (0 = off)
    min_breadth: float = 0.0

    def d(self) -> dict:
        return asdict(self)


RISK_MODES = {
    "conservative": {"risk_pct": 1.0, "max_positions": 4, "max_open_risk_pct": 4.0, "max_position_pct": 30.0},
    "growth": {"risk_pct": 2.0, "max_positions": 5, "max_open_risk_pct": 10.0, "max_position_pct": 40.0},
    "aggressive": {"risk_pct": 3.0, "max_positions": 6, "max_open_risk_pct": 18.0, "max_position_pct": 50.0},
}


def rules_for(mode: str, capital: float = 100.0) -> RiskRules:
    return RiskRules(capital=capital, **RISK_MODES[mode])


@dataclass
class Position:
    symbol: str
    opened: pd.Timestamp
    entry: float
    stop: float
    target: float
    qty: float
    risk_usd: float
    risk_per_unit: float
    half_done: bool = False
    banked: float = 0.0
    days: int = 0
    entry_fee: float = 0.0

    def open_risk(self) -> float:
        """$ still at risk if the stop is hit now (0 once the stop is at or above entry)."""
        return max(0.0, (self.entry - self.stop) * self.qty)


@dataclass
class Result:
    trades: list[dict] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)
    open: list[Position] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    cash: float = 0.0
    paused: bool = False
    risk_now_pct: float = 0.0


def _risk_pct(rules: RiskRules, equity: float, peak: float) -> float:
    dd = (peak - equity) / peak * 100 if peak else 0
    return rules.risk_pct * (0.25 if dd >= rules.dd_quarter else 0.5 if dd >= rules.dd_half else 1.0)


def breadth(frames: dict[str, pd.DataFrame]) -> pd.Series:
    """Share of coins whose close is above their 50-day average, per day."""
    return pd.concat([f.close > f.close.rolling(50).mean() for f in frames.values()], axis=1).mean(axis=1)


def run(frames: dict[str, pd.DataFrame], rules: RiskRules, start: pd.Timestamp, end: pd.Timestamp | None = None
        ) -> Result:
    """frames: symbol -> daily_trend.prepare() output. Simulates every day in [start, end]."""
    days = sorted({t for f in frames.values() for t in f.index if t >= start and (end is None or t <= end)})
    res, cash, peak = Result(), rules.capital, rules.capital
    br = breadth(frames) if rules.min_breadth else None
    pos: dict[str, Position] = {}
    prev_day = None
    for day in days:
        # 1) manage open positions on today's candle
        for sym in list(pos):
            f = frames[sym]
            if day not in f.index:
                continue
            p, bar = pos[sym], f.loc[day]
            p.days += 1
            exit_px, why = None, None
            if bar.low <= p.stop:
                exit_px, why = min(bar.open, p.stop) * (1 - rules.slip), "stop" if not p.half_done else "trail/entry stop"
            else:
                if not p.half_done and bar.high >= p.target:
                    q = p.qty * rules.partial_frac
                    cash += q * p.target * (1 - rules.fee)
                    p.banked += q * (p.target - p.entry) - q * p.target * rules.fee
                    p.qty -= q
                    p.half_done, p.stop = True, max(p.stop, p.entry)
                if p.half_done:
                    p.stop = max(p.stop, bar.close - rules.trail_atr * bar.atr)
                if p.days >= rules.max_hold_days:
                    exit_px, why = bar.close * (1 - rules.slip), "time limit"
            if exit_px is not None:
                cash += p.qty * exit_px * (1 - rules.fee)
                pnl = p.banked + p.qty * (exit_px - p.entry) - p.qty * exit_px * rules.fee - p.entry_fee
                res.trades.append({"symbol": sym, "opened": p.opened, "closed": day, "entry": p.entry,
                                   "exit": exit_px, "pnl": pnl, "r": pnl / p.risk_usd, "why": why,
                                   "half_at_2r": p.half_done, "days": p.days, "risk_usd": p.risk_usd})
                del pos[sym]

        # 2) open yesterday's signals at today's open
        marks = {s: frames[s].loc[day].close for s in pos if day in frames[s].index}
        equity = cash + sum(p.qty * marks.get(s, p.entry) for s, p in pos.items())
        peak = max(peak, equity)
        recent = [t["r"] for t in res.trades[-rules.pause_window:]]
        # the pause lasts pause_days after the latest close; without an end it could never lift (no new trades close)
        paused = (len(recent) >= rules.pause_window and np.mean(recent) < rules.pause_below_r
                  and (day - res.trades[-1]["closed"]).days < rules.pause_days)
        risk_pct = _risk_pct(rules, equity, peak)
        if prev_day is not None:
            cands = [(s, f.loc[prev_day]) for s, f in frames.items()
                     if s not in pos and prev_day in f.index and day in f.index and bool(f.loc[prev_day].signal)]
            cands.sort(key=lambda x: -(x[1]["rank"] if np.isfinite(x[1]["rank"]) else -9))
            for sym, sig in cands:
                why = None
                entry = frames[sym].loc[day].open * (1 + rules.slip)
                pl = st.plan(entry, sig.atr)
                open_risk = sum(p.open_risk() for p in pos.values())
                risk_usd = equity * risk_pct / 100
                if paused:
                    why = "paused: last 30 trades average below -0.1R"
                elif br is not None and br.get(prev_day, 0) < rules.min_breadth:
                    why = f"weak market: under {rules.min_breadth:.0%} of coins above their 50-day average"
                elif pl is None:
                    why = "stop too tight for the costs"
                elif len(pos) >= rules.max_positions:
                    why = f"already {rules.max_positions} open positions"
                elif open_risk + risk_usd > equity * rules.max_open_risk_pct / 100 + 1e-9:
                    why = f"total open risk would pass {rules.max_open_risk_pct:.0f} %"
                if why is None:
                    per_unit = pl["risk_per_unit"] + entry * rules.fee + pl["stop"] * (rules.fee + rules.slip)
                    qty = risk_usd / per_unit
                    qty = min(qty, equity * rules.max_position_pct / 100 / entry, cash / (entry * (1 + rules.fee)))
                    if qty * entry < rules.min_order_usd:
                        why = f"order under the ${rules.min_order_usd:.0f} exchange minimum"
                if why:
                    res.skipped.append({"day": day, "symbol": sym, "why": why})
                    continue
                fee_in = qty * entry * rules.fee
                cash -= qty * entry + fee_in
                p = Position(sym, day, entry, pl["stop"], pl["target"], qty, qty * per_unit, pl["risk_per_unit"],
                             entry_fee=fee_in)
                pos[sym] = p
        marks = {s: frames[s].loc[day].close for s in pos if day in frames[s].index}
        equity = cash + sum(p.qty * marks.get(s, p.entry) for s, p in pos.items())
        peak = max(peak, equity)
        res.equity.append({"day": day, "equity": equity, "open": len(pos)})
        prev_day = day
        res.paused, res.risk_now_pct = paused, _risk_pct(rules, equity, peak)
    res.open, res.cash = list(pos.values()), cash
    return res


def summary(res: Result, rules: RiskRules) -> dict:
    r = np.array([t["r"] for t in res.trades]) if res.trades else np.array([])
    eq = pd.Series([e["equity"] for e in res.equity], index=[e["day"] for e in res.equity]) if res.equity else None
    out = {"trades": len(r), "start_usd": rules.capital}
    if eq is not None and len(eq):
        dd = (eq.cummax() - eq) / eq.cummax() * 100
        out |= {"end_usd": round(float(eq.iloc[-1]), 2), "return_pct": round(float(eq.iloc[-1] / rules.capital - 1) * 100, 1),
                "max_drawdown_pct": round(float(dd.max()), 1), "days": len(eq)}
        yrs = len(eq) / 365
        if yrs > 0.5 and eq.iloc[-1] > 0:
            out["yearly_pct"] = round(float((eq.iloc[-1] / rules.capital) ** (1 / yrs) - 1) * 100, 1)
        rets = eq.pct_change().dropna()
        if rets.std() > 0:
            out["sharpe"] = round(float(rets.mean() / rets.std() * np.sqrt(365)), 2)
    if len(r):
        se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
        out |= {"win_rate": round(float((r > 0).mean()), 3), "avg_r": round(float(r.mean()), 3),
                "t_stat": round(float(r.mean() / se), 2) if se and se > 0 else None,
                "best_r": round(float(r.max()), 2), "worst_r": round(float(r.min()), 2),
                "reached_2r_pct": round(float(np.mean([t["half_at_2r"] for t in res.trades])) * 100, 1),
                "profit_factor": round(float(r[r > 0].sum() / -r[r < 0].sum()), 2) if (r < 0).any() else None}
        streak = m = 0
        for x in r:
            streak = streak + 1 if x <= 0 else 0
            m = max(m, streak)
        out["max_losing_streak"] = m
    return out
