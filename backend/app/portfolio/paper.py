"""Paper trading accounts. Simulated only — no exchange orders are ever placed."""
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.metrics import max_drawdown
from app.data.store import load_candles
from app.models import EquityPoint, PaperAccount, PaperPosition
from app.risk.sizing import RiskProfile, size_position

DEFAULT_RISK = {"risk_per_trade_pct": 1.0, "max_drawdown_pct": 20.0, "max_open_positions": 5, "max_leverage": 3.0,
                "market": "perp", "exchange": "binance", "timeframes": ["1h", "4h"], "max_portfolio_risk_pct": 5.0,
                "notifications": {"in_app": True, "browser": True, "telegram": False, "email": False},
                "auto_follow_signals": False, "taker_fee": 0.0005, "slippage_bps": 2.0}


def profile(acct: PaperAccount, equity: float) -> RiskProfile:
    r = {**DEFAULT_RISK, **(acct.risk or {})}
    return RiskProfile(equity=equity, risk_per_trade_pct=r["risk_per_trade_pct"], max_leverage=r["max_leverage"],
                       max_open_positions=r["max_open_positions"], max_portfolio_risk_pct=r["max_portfolio_risk_pct"],
                       taker_fee=r["taker_fee"], slippage_bps=r["slippage_bps"], market=r["market"])


def upnl(p: PaperPosition, price: float) -> float:
    s = 1 if p.side == "LONG" else -1
    return s * (price - p.entry_price) * p.qty


async def open_positions(db: AsyncSession, acct_id: str) -> list[PaperPosition]:
    return list((await db.execute(select(PaperPosition).where(PaperPosition.account_id == acct_id,
                                                              PaperPosition.status == "open"))).scalars())


def equity_of(acct: PaperAccount, positions: list[PaperPosition], prices: dict[str, float]) -> float:
    return acct.cash + sum(upnl(p, prices.get(p.symbol, p.entry_price)) for p in positions)


async def open_position(db: AsyncSession, acct: PaperAccount, symbol: str, side: str, price: float, stop: float,
                        take_profit: float | None, prices: dict[str, float], signal_id: str | None = None) -> dict:
    """Size by risk, then open at `price` (+slippage). Cash model: margin is not locked from cash; equity =
    cash + unrealised PnL; fees are charged to cash. Leverage only changes margin usage."""
    positions = await open_positions(db, acct.id)
    eq = equity_of(acct, positions, prices)
    peak = await peak_equity(db, acct)
    dd = (eq / peak - 1) * 100 if peak else 0
    r = {**DEFAULT_RISK, **(acct.risk or {})}
    if dd <= -r["max_drawdown_pct"]:
        return {"ok": False, "reason": f"max drawdown limit reached ({dd:.1f}%) — trading paused"}
    open_risk = sum(abs(p.entry_price - (p.stop or p.entry_price)) * p.qty for p in positions) / eq * 100 if eq else 0
    prof = profile(acct, eq)
    s = 1 if side == "LONG" else -1
    fill = price * (1 + s * prof.slippage_bps / 1e4)
    sz = size_position(prof, symbol, side, fill, stop, open_risk_pct=open_risk, open_positions=len(positions))
    if not sz["ok"]:
        return sz
    fee = sz["notional"] * prof.taker_fee
    pos = PaperPosition(account_id=acct.id, symbol=symbol, market=prof.market, side=side, qty=sz["qty"],
                        leverage=max(sz["leverage_used"], 1.0), entry_price=fill, stop=stop, take_profit=take_profit,
                        fees=fee, signal_id=signal_id)
    acct.cash -= fee
    db.add(pos)
    await db.commit()
    return {"ok": True, "position_id": pos.id, "sizing": sz, "fill": fill, "fee": fee}


async def close_position(db: AsyncSession, acct: PaperAccount, pos: PaperPosition, price: float, reason: str,
                         slippage: bool = True) -> PaperPosition:
    r = {**DEFAULT_RISK, **(acct.risk or {})}
    s = 1 if pos.side == "LONG" else -1
    px = price * (1 - s * r["slippage_bps"] / 1e4) if slippage else price
    exit_fee = px * pos.qty * r["taker_fee"]
    gross = s * (px - pos.entry_price) * pos.qty
    # Cash accounting: entry fee was charged at open, funding as it accrued; now settle gross PnL and exit fee.
    acct.cash += gross - exit_fee
    pos.fees += exit_fee
    pos.exit_price, pos.closed_at, pos.status, pos.close_reason = px, datetime.now(UTC), "closed", reason
    pos.realized_pnl = gross - pos.fees + pos.funding  # net trade result
    await db.commit()
    return pos


async def check_exits(db: AsyncSession, acct: PaperAccount, prices: dict[str, float]) -> list[str]:
    closed = []
    for p in await open_positions(db, acct.id):
        px = prices.get(p.symbol)
        if px is None:
            continue
        s = 1 if p.side == "LONG" else -1
        if p.stop and s * (px - p.stop) <= 0:
            # fill at the stop unless price has gapped >0.2% beyond it, then at the (worse) current price
            await close_position(db, acct, p, p.stop if s * (px - p.stop) > -abs(p.stop) * 0.002 else px, "stop")
            closed.append(p.id)
        elif p.take_profit and s * (px - p.take_profit) >= 0:
            await close_position(db, acct, p, p.take_profit, "take_profit", slippage=False)
            closed.append(p.id)
    return closed


async def apply_funding(db: AsyncSession, acct: PaperAccount, symbol: str, rate: float, mark: float) -> None:
    """Longs pay shorts when funding > 0 (Binance USDⓈ-M convention)."""
    for p in await open_positions(db, acct.id):
        if p.symbol != symbol or p.market != "perp":
            continue
        s = 1 if p.side == "LONG" else -1
        amt = -s * rate * p.qty * mark
        p.funding += amt
        acct.cash += amt
    await db.commit()


async def peak_equity(db: AsyncSession, acct: PaperAccount) -> float:
    vals = (await db.execute(select(EquityPoint.equity).where(EquityPoint.account_id == acct.id))).scalars().all()
    return max([acct.starting_capital, *vals])


async def summary(db: AsyncSession, acct: PaperAccount, prices: dict[str, float]) -> dict:
    opens = await open_positions(db, acct.id)
    closed = list((await db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id,
                                                                PaperPosition.status == "closed")
                                    .order_by(PaperPosition.closed_at))).scalars())
    eq = equity_of(acct, opens, prices)
    curve = list((await db.execute(select(EquityPoint).where(EquityPoint.account_id == acct.id)
                                   .order_by(EquityPoint.ts))).scalars())
    eqs = np.array([acct.starting_capital] + [c.equity for c in curve] + [eq])
    pnls = [p.realized_pnl or 0 for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    exposure: dict[str, float] = {}
    for p in opens:
        exposure[p.symbol] = exposure.get(p.symbol, 0) + p.qty * prices.get(p.symbol, p.entry_price) * (1 if p.side == "LONG" else -1)
    corr = await correlation_risk(db, list({p.symbol for p in opens}))
    return {
        "account_id": acct.id, "base_currency": acct.base_currency, "starting_capital": acct.starting_capital,
        "cash": acct.cash, "equity": eq, "realized_pnl": sum(pnls),
        "unrealized_pnl": sum(upnl(p, prices.get(p.symbol, p.entry_price)) for p in opens),
        "total_return_pct": (eq / acct.starting_capital - 1) * 100,
        "max_drawdown_pct": max_drawdown(eqs)[0] * 100, "current_drawdown_pct": (eq / eqs.max() - 1) * 100,
        "trades": len(closed), "win_rate": len(wins) / len(closed) if closed else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "fees_paid": sum(p.fees for p in opens + closed), "funding_paid": sum(p.funding for p in opens + closed),
        "exposure": exposure, "gross_exposure": sum(abs(v) for v in exposure.values()),
        "correlation": corr, "risk": {**DEFAULT_RISK, **(acct.risk or {})},
        "open_positions": [pos_dict(p, prices.get(p.symbol)) for p in opens],
        "closed_positions": [pos_dict(p) for p in closed[-100:]],
        "equity_curve": [{"time": int(c.ts.replace(tzinfo=UTC).timestamp()), "value": c.equity} for c in curve[-2000:]],
        "r_distribution": [((p.realized_pnl or 0) / (abs(p.entry_price - p.stop) * p.qty)) for p in closed if p.stop
                           and p.entry_price != p.stop],
        "disclaimer": "Paper trading — simulated fills; not real trading performance.",
    }


def pos_dict(p: PaperPosition, price: float | None = None) -> dict:
    d = {c.name: getattr(p, c.name) for c in PaperPosition.__table__.columns}
    for k in ("opened_at", "closed_at"):
        d[k] = d[k].isoformat() if d[k] else None
    s = 1 if p.side == "LONG" else -1
    if price is not None:
        d["mark"] = price
        d["unrealized_pnl"] = upnl(p, price)
        d["unrealized_pnl_pct"] = s * (price - p.entry_price) / p.entry_price * 100  # price move %
        d["roe_pct"] = d["unrealized_pnl_pct"] * max(p.leverage, 1.0)  # return on margin
    if p.exit_price is not None:
        d["realized_pnl_pct"] = (p.realized_pnl or 0) / (p.qty * p.entry_price) * 100  # net of fees/funding, % of notional
    return d


async def correlation_risk(db: AsyncSession, symbols: list[str]) -> dict:
    """Pairwise correlation of 1h log returns over the last 30 days for open-position symbols."""
    if len(symbols) < 2:
        return {"pairs": [], "max": None}
    rets = {}
    for sym in symbols:
        df = await load_candles(db, sym, "1h", limit=720)
        if len(df) > 50:
            rets[sym] = np.diff(np.log(df["close"].to_numpy()))
    pairs = []
    keys = list(rets)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            n = min(len(rets[keys[i]]), len(rets[keys[j]]))
            c = float(np.corrcoef(rets[keys[i]][-n:], rets[keys[j]][-n:])[0, 1])
            pairs.append({"a": keys[i], "b": keys[j], "corr": c, "high": c > 0.7})
    return {"pairs": pairs, "max": max((p["corr"] for p in pairs), default=None),
            "warning": "Highly correlated positions behave like one larger position" if any(p["high"] for p in pairs) else None}
