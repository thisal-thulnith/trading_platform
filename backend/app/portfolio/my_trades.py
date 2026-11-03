"""My trades: the trades the user chose to take, tracked like Binance position cards and managed by the system's rules.

The platform never sends an order, so the user picks a ticket, places it on Binance by hand and records it here.
From then on the platform marks it to market, tells the next action from the daily candles (stop touched, 2R
reached, raise the trailing stop, time limit), books the partial and the close the user confirms, and keeps the
record: realised P&L, R multiples, win rate and how the user's own discipline compares with the plan.
"""
import json
import time
from datetime import UTC, datetime

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import llm
from app.logging import log
from app.models import MyTrade
from app.portfolio import budget as budget_plan
from app.risk import futures as fut
from app.risk.portfolio import rules_for
from app.strategies import daily_trend as dt

FEES = {"spot": 0.001, "futures": fut.TAKER}
TICKER = "https://data-api.binance.vision/api/v3/ticker/price"
MARK_CACHE_S = 30
_marks: dict = {"t": 0.0, "px": {}}

AGENTS = {
    "planner": {"name": "Planner", "role": "Turns your budget into the settings the system invests with", "color": "#38bdf8"},
    "manager": {"name": "Position Manager", "role": "The next action for every open trade, from the daily candles",
                "color": "#34d399"},
    "coach": {"name": "Coach", "role": "Your record and discipline against the plan", "color": "#f472b6"},
}


def _fee(t: MyTrade) -> float:
    return FEES.get(t.venue, 0.001)


def _utc(ts: datetime | pd.Timestamp) -> pd.Timestamp:
    x = pd.Timestamp(ts)
    return x.tz_localize("UTC") if x.tzinfo is None else x.tz_convert("UTC")


def remaining_qty(t: MyTrade) -> float:
    return t.qty - (t.partial_qty or 0.0)


def banked(t: MyTrade) -> float:
    """Profit already taken at the partial, net of its fee."""
    if not t.partial_done or not t.partial_price or not t.partial_qty:
        return 0.0
    return (t.partial_price - t.entry) * t.partial_qty - t.partial_qty * t.partial_price * _fee(t)


def unrealized(t: MyTrade, mark: float) -> float:
    return banked(t) + (mark - t.entry) * remaining_qty(t) - t.qty * t.entry * _fee(t)


async def marks(symbols: list[str], fallback: dict[str, float] | None = None) -> dict[str, float]:
    """Last traded price per symbol (public Binance ticker, cached 30 s); the last daily close when unreachable."""
    syms = sorted(set(symbols))
    if not syms:
        return {}
    if time.time() - _marks["t"] >= MARK_CACHE_S or any(s not in _marks["px"] for s in syms):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(TICKER, params={"symbols": json.dumps(syms, separators=(",", ":"))})
                r.raise_for_status()
                _marks.update(t=time.time(), px={**_marks["px"], **{x["symbol"]: float(x["price"]) for x in r.json()}})
        except Exception as e:  # noqa: BLE001 - the cards still render from the last daily close
            log.warning("my_trades_marks_failed", error=str(e))
    out = {}
    for s in syms:
        v = _marks["px"].get(s) or (fallback or {}).get(s)
        if v:
            out[s] = float(v)
    return out


def plan_state(t: MyTrade, frame: pd.DataFrame | None) -> dict:
    """What the daily-trend rules say to do next, judged on the finished daily candles since the trade opened."""
    stop_now = max(t.stop, t.entry) if t.partial_done else t.stop
    out = {"action": "hold", "level": "info", "recommended_stop": stop_now, "days": 0,
           "text": f"Hold. Stop {stop_now:.6g} ({(1 - stop_now / t.entry) * 100:.1f}% below entry), target {t.target:.6g} "
                   f"(+{(t.target / t.entry - 1) * 100:.1f}%). No finished daily candle since the entry yet."}
    if frame is None or frame.empty:
        return out
    held = frame.loc[frame.index >= _utc(t.opened_at).floor("D")]
    if held.empty:
        return out
    last = frame.iloc[-1]
    days = len(held)
    since_partial = held.loc[held.index > _utc(t.partial_at).floor("D")] if t.partial_done and t.partial_at else held
    rec = stop_now
    if t.partial_done and np.isfinite(last.atr):
        rec = max(stop_now, float(last.close - dt.TRAIL_ATR * last.atr))
    out |= {"days": days, "recommended_stop": rec}
    if len(since_partial) and float(since_partial.low.min()) <= stop_now:
        out |= {"action": "close", "level": "warn",
                "text": f"Stop {stop_now:.6g} was touched (daily low {float(since_partial.low.min()):.6g}). If Binance did "
                        f"not close it, close now and record the exit."}
    elif not t.partial_done and float(held.high.max()) >= t.target:
        out |= {"action": "partial", "level": "good",
                "text": f"2R reached (high {float(held.high.max()):.6g} ≥ {t.target:.6g}): sell a third at the target, "
                        f"move the stop to entry {t.entry:.6g}, then confirm below."}
    elif days >= dt.MAX_HOLD_DAYS:
        out |= {"action": "close", "level": "warn",
                "text": f"{days} days held — the {dt.MAX_HOLD_DAYS}-day limit. Close at market and record the exit."}
    elif t.partial_done and rec > stop_now * (1 + 1e-9):
        out |= {"action": "raise_stop", "level": "good",
                "text": f"Raise the stop to {rec:.6g} ({dt.TRAIL_ATR:g} ATR below yesterday's close {float(last.close):.6g})."}
    else:
        out["text"] = (f"Hold. Stop {stop_now:.6g} ({(1 - stop_now / t.entry) * 100:.1f}% below entry), target "
                       f"{t.target:.6g} (+{(t.target / t.entry - 1) * 100:.1f}%), day {days} of {dt.MAX_HOLD_DAYS}.")
    return out


def card(t: MyTrade, mark: float | None, state: dict) -> dict:
    rem = remaining_qty(t)
    margin = t.qty * t.entry / max(t.leverage, 1.0)
    pnl = unrealized(t, mark) if mark else None
    liq = fut.liquidation_price(t.entry, t.leverage, t.symbol) if t.venue == "futures" and t.leverage > 1 else None
    return {"id": t.id, "symbol": t.symbol, "venue": t.venue, "side": t.side, "leverage": t.leverage,
            "contract": f"{t.symbol} Perpetual" if t.venue == "futures" else f"{t.symbol} Spot",
            "margin_mode": "Isolated" if t.venue == "futures" else None,
            "qty": t.qty, "qty_open": round(rem, 10), "entry": t.entry, "mark": mark,
            "size_usd": round(rem * mark, 2) if mark else None, "margin_usd": round(margin, 2),
            "liquidation": round(liq, 8) if liq else None,
            "liq_distance_pct": round((mark / liq - 1) * 100, 1) if liq and mark else None,
            "stop": t.stop, "target": t.target, "recommended_stop": state["recommended_stop"],
            "partial_done": t.partial_done, "partial_price": t.partial_price, "banked_usd": round(banked(t), 2),
            "pnl_usd": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round((mark / t.entry - 1) * 100, 2) if mark else None,
            "roe_pct": round(pnl / margin * 100, 2) if pnl is not None and margin else None,
            "r": round(pnl / t.risk_usd, 2) if pnl is not None and t.risk_usd else None,
            "risk_usd": round(t.risk_usd, 2),
            "open_risk_usd": round(max(0.0, (t.entry - (max(t.stop, t.entry) if t.partial_done else t.stop)) * rem), 2),
            "days": state["days"], "opened_at": _utc(t.opened_at).isoformat(), "action": state["action"],
            "plan": state["text"], "level": state["level"], "mode": t.mode, "budget_usd": t.budget_usd,
            "source": t.source, "note": t.note}


def closed_card(t: MyTrade) -> dict:
    margin = t.qty * t.entry / max(t.leverage, 1.0)
    return {"id": t.id, "symbol": t.symbol, "venue": t.venue, "side": t.side, "leverage": t.leverage,
            "contract": f"{t.symbol} Perpetual" if t.venue == "futures" else f"{t.symbol} Spot",
            "qty": t.qty, "entry": t.entry, "exit": t.exit_price, "partial_done": t.partial_done,
            "partial_price": t.partial_price, "pnl_usd": round(t.pnl_usd or 0.0, 2), "r": round(t.r_multiple or 0.0, 2),
            "pnl_pct": round((t.exit_price / t.entry - 1) * 100, 2) if t.exit_price else None,
            "roe_pct": round((t.pnl_usd or 0.0) / margin * 100, 2) if margin else None, "reason": t.close_reason,
            "opened_at": _utc(t.opened_at).isoformat(), "closed_at": _utc(t.closed_at).isoformat() if t.closed_at else None,
            "days": int((_utc(t.closed_at) - _utc(t.opened_at)).days) if t.closed_at else None, "mode": t.mode,
            "budget_usd": t.budget_usd, "source": t.source, "note": t.note}


def stats(open_cards: list[dict], closed: list[MyTrade]) -> dict:
    r = np.array([t.r_multiple for t in closed if t.r_multiple is not None])
    pnl = np.array([t.pnl_usd or 0.0 for t in closed])
    out = {"open": len(open_cards), "closed": len(closed), "wins": int((pnl > 0).sum()),
           "win_rate": round(float((pnl > 0).mean()), 3) if len(pnl) else None,
           "avg_r": round(float(r.mean()), 2) if len(r) else None,
           "best_r": round(float(r.max()), 2) if len(r) else None, "worst_r": round(float(r.min()), 2) if len(r) else None,
           "realized_usd": round(float(pnl.sum()), 2),
           "unrealized_usd": round(sum(c["pnl_usd"] or 0.0 for c in open_cards), 2),
           "open_risk_usd": round(sum(c["open_risk_usd"] for c in open_cards), 2),
           "margin_in_use_usd": round(sum(c["margin_usd"] for c in open_cards), 2)}
    out["total_usd"] = round(out["realized_usd"] + out["unrealized_usd"], 2)
    return out


def coach(open_cards: list[dict], trades: list[MyTrade], st: dict, plan: dict, proof: dict | None, mode: str
          ) -> list[dict]:
    msgs = []

    def say(agent, text, level="info"):
        msgs.append({"agent": agent, **AGENTS[agent], "text": text, "level": level})

    b = plan["budget_usd"]
    fut_part = (f" with about ${plan['typical_margin_usd']} margin at {plan['typical_leverage']:g}x"
                if plan["venue"] == "futures" and plan["typical_margin_usd"] is not None else "")
    exp = {e["period"]: e for e in plan["expectations"]}
    al, te = exp.get("all 2020-now"), exp.get("TEST 2025-now")
    exp_txt = ""
    if al:
        exp_txt = (f" Research for {mode} mode: {al['yearly_pct']}%/year over 2020-now (${b:,.0f} → "
                   f"${b * (1 + (al['yearly_pct'] or 0) / 100):,.0f} in an average year), worst drop {al['max_drawdown_pct']}% "
                   f"(−${al['worst_drop_usd']:,.0f} at some point)"
                   + (f"; the untouched 2025-26 test did {te['return_pct']:+.1f}%." if te else "."))
    say("planner", f"With ${b:,.0f} in {mode} mode on {plan['venue']}: risk ${plan['risk_per_trade_usd']} per trade "
        f"({plan['risk_pct']:g}%), at most {plan['max_positions']} trades and ${plan['max_open_risk_usd']} at risk together, "
        f"about ${plan['typical_position_usd']} per trade{fut_part} (typical stop {plan['typical_stop_pct']}% away). "
        f"{plan['recommendation']['text']}{exp_txt}", "warn" if plan["recommendation"]["switch"] else "good")

    if open_cards:
        say("manager", " ".join(f"{c['symbol'].replace('USDT', '')}: {c['plan']}" for c in open_cards),
            "warn" if any(c["level"] == "warn" for c in open_cards) else
            "good" if any(c["level"] == "good" for c in open_cards) else "info")
    else:
        say("manager", "No open trades. When a ticket appears on the System account, press 'I took this trade' and it "
            "shows up here as a card with its stop, target and next action.")

    issues = []
    rules = rules_for(mode, b)
    for t in trades:
        plan_risk = t.budget_usd * rules_for(t.mode, t.budget_usd).risk_pct / 100 if t.mode in ("conservative", "growth", "aggressive") else None
        if plan_risk and t.risk_usd > 1.5 * plan_risk:
            issues.append(f"{t.symbol.replace('USDT', '')} risked ${t.risk_usd:.2f} against a plan of ${plan_risk:.2f}")
        if t.venue == "futures":
            safe = fut.safe_leverage((t.entry - t.stop) / t.entry, t.symbol)
            if t.leverage > safe:
                issues.append(f"{t.symbol.replace('USDT', '')} at {t.leverage:g}x is above the safe {safe}x for its stop")
    if st["open_risk_usd"] > b * rules.max_open_risk_pct / 100 + 1e-9:
        issues.append(f"open risk ${st['open_risk_usd']:.2f} is above the {rules.max_open_risk_pct:g}% limit "
                      f"(${b * rules.max_open_risk_pct / 100:.2f})")
    research = ((proof or {}).get(mode) or {}).get("all 2020-now") or {}
    record = ""
    if st["closed"]:
        record = (f"Your record: {st['closed']} closed trades, {st['wins']} won ({st['win_rate']:.0%}), average "
                  f"{st['avg_r']:+.2f}R, realised ${st['realized_usd']:+.2f}"
                  + (f"; the research expects about {research['win_rate']:.0%} wins and {research['avg_r']:+.2f}R per "
                     f"trade, so judge yourself on 30+ trades, not on a few." if research.get("win_rate") else "."))
    else:
        record = "No closed trades yet — your record starts with the first close."
    disc = (" Discipline: " + "; ".join(issues) + "." if issues else
            " Discipline: every recorded trade is within the plan's risk and leverage.")
    say("coach", record + disc, "warn" if issues else "good" if st["closed"] else "info")
    return msgs


async def list_trades(db: AsyncSession, user_id: str | None) -> list[MyTrade]:
    cond = MyTrade.user_id.is_(None) if user_id is None else MyTrade.user_id == user_id
    return list((await db.execute(select(MyTrade).where(cond).order_by(MyTrade.opened_at))).scalars().all())


async def get(db: AsyncSession, user_id: str | None, trade_id: str) -> MyTrade | None:
    t = await db.get(MyTrade, trade_id)
    return t if t is not None and t.user_id == user_id else None


async def add(db: AsyncSession, user_id: str | None, body: dict) -> MyTrade:
    venue = body.get("venue", "spot")
    if venue not in ("spot", "futures"):
        raise ValueError("venue must be spot or futures")
    entry, qty, stop = float(body["entry"]), float(body["qty"]), float(body["stop"])
    if entry <= 0 or qty <= 0:
        raise ValueError("entry and qty must be positive")
    if stop >= entry:
        raise ValueError("the stop must be below the entry (long trades only)")
    target = float(body.get("target") or entry + dt.RR * (entry - stop))
    if target <= entry:
        raise ValueError("the target must be above the entry")
    lev = float(body.get("leverage") or 1) if venue == "futures" else 1.0
    if not 1 <= lev <= 125:
        raise ValueError("leverage must be 1..125")
    fee = FEES[venue]
    opened = body.get("opened_at")
    t = MyTrade(user_id=user_id, symbol=str(body["symbol"]).upper(), venue=venue, side="LONG", leverage=lev,
                entry=entry, qty=qty, stop=stop, target=target,
                risk_usd=(entry - stop) * qty + qty * entry * fee + qty * stop * fee,
                budget_usd=float(body.get("budget_usd") or 100.0), mode=str(body.get("mode") or "conservative"),
                source=str(body.get("source") or "manual"), note=body.get("note"),
                opened_at=_utc(opened).to_pydatetime() if opened else datetime.now(UTC))
    db.add(t)
    await db.commit()
    return t


async def partial(db: AsyncSession, t: MyTrade, price: float, qty: float | None = None) -> MyTrade:
    if t.status != "open" or t.partial_done:
        raise ValueError("the partial was already taken or the trade is closed")
    if price <= 0:
        raise ValueError("price must be positive")
    q = float(qty) if qty else t.qty * dt.PARTIAL
    if not 0 < q < t.qty:
        raise ValueError("the partial quantity must be between 0 and the position size")
    t.partial_done, t.partial_price, t.partial_qty, t.partial_at = True, float(price), q, datetime.now(UTC)
    t.stop = max(t.stop, t.entry)
    await db.commit()
    return t


async def close(db: AsyncSession, t: MyTrade, price: float, reason: str = "manual") -> MyTrade:
    if t.status != "open":
        raise ValueError("the trade is already closed")
    if price <= 0:
        raise ValueError("price must be positive")
    rem = remaining_qty(t)
    fee = _fee(t)
    pnl = banked(t) + (price - t.entry) * rem - rem * price * fee - t.qty * t.entry * fee
    t.exit_price, t.closed_at, t.close_reason, t.status = float(price), datetime.now(UTC), reason[:24], "closed"
    t.pnl_usd, t.r_multiple = pnl, (pnl / t.risk_usd if t.risk_usd else None)
    await db.commit()
    return t


async def portfolio(db: AsyncSession, user_id: str | None, budget: float, mode: str, venue: str, leverage: int,
                    frames: dict[str, pd.DataFrame], filters: dict, proof: dict | None) -> dict:
    trades = await list_trades(db, user_id)
    open_t = [t for t in trades if t.status == "open"]
    closed_t = [t for t in trades if t.status == "closed"]
    fallback = {s: float(f.close.iloc[-1]) for s, f in frames.items()}
    px = await marks([t.symbol for t in open_t], fallback)
    cards = [card(t, px.get(t.symbol), plan_state(t, frames.get(t.symbol))) for t in open_t]
    st = stats(cards, closed_t)
    plan = budget_plan.plan(budget, mode, venue, leverage, frames, filters, proof)
    return {"budget_plan": plan, "open": cards, "closed": [closed_card(t) for t in closed_t][::-1], "stats": st,
            "agents": coach(cards, trades, st, plan, proof, mode), "llm": llm.status(),
            "marks_note": "Mark = last Binance spot price (30 s cache); perpetual prices differ slightly. The platform "
                          "never places orders — you place them, it tracks and manages."}
