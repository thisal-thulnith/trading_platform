""""What if someone had taken every trade?" — replays every filled signal on a small account.

Sizing: fixed-fractional. Each trade risks `risk_pct` of the *realised* equity at the moment it was entered, measured on
the planned risk (entry-zone midpoint to stop) — the same R basis the simulator uses, so $ P&L = R x risk$.
Closed trades use the simulator's net R (fees + slippage included). Open trades are marked to the live price.
Events are processed in time order so compounding only uses results that were known at the time.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FollowTrade:
    id: str
    symbol: str
    timeframe: str
    strategy: str
    direction: str
    state: str
    entered_at: datetime
    closed_at: datetime | None
    fill: float
    stop: float
    entry_low: float
    entry_high: float
    r_multiple: float | None


def _planned_risk(t: FollowTrade) -> float:
    return abs((t.entry_low + t.entry_high) / 2 - t.stop)


def simulate_follow_all(trades: list[FollowTrade], prices: dict[str, float], capital: float = 100.0,
                        risk_pct: float = 1.0, fee: float = 0.0005) -> dict:
    trades = [t for t in trades if t.fill and t.stop and _planned_risk(t) > 0
              and (t.state == "ACTIVE" or t.r_multiple is not None)]
    events = [(t.entered_at, 0, "open", t) for t in trades]
    events += [(t.closed_at, 1, "close", t) for t in trades if t.r_multiple is not None and t.closed_at]
    # opens first on equal timestamps: a trade can fill and stop on the same bar, and a close on that bar is not yet
    # known when sizing another trade entered on it
    events.sort(key=lambda e: (e[0], e[1]))

    realized = 0.0
    sized: dict[str, dict] = {}
    open_ids: set[str] = set()
    curve = [{"time": events[0][0].isoformat() if events else None, "equity": capital}]
    peak, max_dd, peak_lev, peak_open = capital, 0.0, 0.0, 0
    for ts, _, kind, t in events:
        if kind == "open":
            eq = capital + realized
            risk_usd = eq * risk_pct / 100
            stop_dist = _planned_risk(t) / t.fill
            notional = risk_usd / stop_dist
            sized[t.id] = {"risk_usd": risk_usd, "notional": notional, "equity_at_entry": eq}
            open_ids.add(t.id)
            exposure = sum(sized[i]["notional"] for i in open_ids)
            peak_lev = max(peak_lev, exposure / eq)
            peak_open = max(peak_open, len(open_ids))
        else:
            pnl = t.r_multiple * sized[t.id]["risk_usd"]
            realized += pnl
            open_ids.discard(t.id)
            eq = capital + realized
            curve.append({"time": ts.isoformat(), "equity": eq})
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    rows, unrealized = [], 0.0
    for t in sorted(trades, key=lambda t: t.entered_at, reverse=True):
        s = sized[t.id]
        sgn = 1 if t.direction == "LONG" else -1
        if t.state == "ACTIVE":
            last = prices.get(t.symbol)
            r = (sgn * (last - t.fill) - t.fill * fee) / _planned_risk(t) if last else None  # entry fee already paid
            status = "OPEN"
        else:
            last, r, status = None, t.r_multiple, "CLOSED"
        pnl = r * s["risk_usd"] if r is not None else None
        if status == "OPEN" and pnl is not None:
            unrealized += pnl
        rows.append({"id": t.id, "symbol": t.symbol, "timeframe": t.timeframe, "strategy": t.strategy,
                     "direction": t.direction, "status": status, "outcome": t.state,
                     "entered_at": t.entered_at.isoformat(), "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                     "fill": t.fill, "last_price": last, "risk_usd": s["risk_usd"], "notional_usd": s["notional"],
                     "leverage": s["notional"] / s["equity_at_entry"], "r": r, "pnl_usd": pnl,
                     "pnl_pct_account": pnl / s["equity_at_entry"] * 100 if pnl is not None else None,
                     "price_move_pct": r * _planned_risk(t) / t.fill * 100 if r is not None else None})

    closed = [r for r in rows if r["status"] == "CLOSED"]
    wins = [r for r in closed if r["r"] > 0]
    equity = capital + realized + unrealized
    curve.append({"time": datetime.now().astimezone().isoformat(), "equity": equity, "live": True})
    return {
        "capital": capital, "risk_pct": risk_pct,
        "realized_pnl": realized, "unrealized_pnl": unrealized, "total_pnl": realized + unrealized,
        "equity": equity, "return_pct": (equity / capital - 1) * 100,
        "realized_return_pct": realized / capital * 100,
        "trades_total": len(rows), "open": len(rows) - len(closed), "closed": len(closed),
        "wins": len(wins), "losses": len(closed) - len(wins),
        "win_rate": len(wins) / len(closed) if closed else None,
        "max_drawdown_pct": max_dd, "peak_leverage": peak_lev, "peak_open_positions": peak_open,
        "equity_curve": curve, "trades": rows,
    }
