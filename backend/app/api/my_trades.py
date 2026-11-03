"""My trades: the user's own portfolio of trades taken from the system account (or by hand). Login optional — without a
token the trades belong to this installation (user NULL); with one, to that user."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import current_user_optional
from app.db import get_session
from app.models import User
from app.portfolio import my_trades, system
from app.risk.portfolio import RISK_MODES

router = APIRouter(prefix="/api/my")


class TradeIn(BaseModel):
    symbol: str
    venue: str = "spot"
    leverage: float = 1.0
    entry: float
    qty: float
    stop: float
    target: float | None = None
    budget_usd: float = 100.0
    mode: str = "conservative"
    source: str = "manual"
    note: str | None = None
    opened_at: datetime | None = None


class PartialIn(BaseModel):
    price: float
    qty: float | None = None


class CloseIn(BaseModel):
    price: float
    reason: str = "manual"


def _uid(u: User | None) -> str | None:
    return u.id if u else None


@router.get("/portfolio")
async def my_portfolio(budget: float = 100.0, mode: str = "conservative", venue: str = "spot", leverage: int = 3,
                       u: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    if mode not in RISK_MODES or venue not in system.VENUES or not 1 <= leverage <= 125 or not 10 <= budget <= 1_000_000:
        raise HTTPException(400, "mode conservative|growth|aggressive, venue spot|futures, leverage 1..125, budget 10..1,000,000")
    try:
        frames = await system._frames()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    filters = await system._filters() if venue == "futures" else {}
    return await my_trades.portfolio(db, _uid(u), budget, mode, venue, leverage, frames, filters, system.proof())


@router.get("/desk")
async def my_desk(budget: float = 100.0, mode: str = "conservative", venue: str = "spot", leverage: int = 3,
                  u: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    """The intelligent portfolio desk (Planner, Position Manager, Coach): LLM agents with tools over the user's own
    trades, run in the background and cached until the trades or settings change."""
    from app.agents import intel_desk

    if mode not in RISK_MODES or venue not in system.VENUES or not 1 <= leverage <= 125 or not 10 <= budget <= 1_000_000:
        raise HTTPException(400, "mode conservative|growth|aggressive, venue spot|futures, leverage 1..125, budget 10..1,000,000")
    try:
        frames = await system._frames()
        acct = await system.account(budget, 365, mode, venue, leverage)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    filters = await system._filters() if venue == "futures" else {}
    portfolio = await my_trades.portfolio(db, _uid(u), budget, mode, venue, leverage, frames, filters, system.proof())
    return intel_desk.portfolio_desk(acct, frames, portfolio, filters, system._cache.get("funding") or {})


@router.post("/trades")
async def add_trade(body: TradeIn, u: User | None = Depends(current_user_optional),
                    db: AsyncSession = Depends(get_session)):
    if body.mode not in RISK_MODES:
        raise HTTPException(400, "mode conservative|growth|aggressive")
    try:
        t = await my_trades.add(db, _uid(u), body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return my_trades.card(t, None, my_trades.plan_state(t, None))


@router.post("/trades/{trade_id}/partial")
async def take_partial(trade_id: str, body: PartialIn, u: User | None = Depends(current_user_optional),
                       db: AsyncSession = Depends(get_session)):
    t = await my_trades.get(db, _uid(u), trade_id)
    if t is None:
        raise HTTPException(404, "trade not found")
    try:
        await my_trades.partial(db, t, body.price, body.qty)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return my_trades.card(t, None, my_trades.plan_state(t, None))


@router.post("/trades/{trade_id}/close")
async def close_trade(trade_id: str, body: CloseIn, u: User | None = Depends(current_user_optional),
                      db: AsyncSession = Depends(get_session)):
    t = await my_trades.get(db, _uid(u), trade_id)
    if t is None:
        raise HTTPException(404, "trade not found")
    try:
        await my_trades.close(db, t, body.price, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return my_trades.closed_card(t)


@router.delete("/trades/{trade_id}")
async def delete_trade(trade_id: str, u: User | None = Depends(current_user_optional),
                       db: AsyncSession = Depends(get_session)):
    t = await my_trades.get(db, _uid(u), trade_id)
    if t is None:
        raise HTTPException(404, "trade not found")
    await db.delete(t)
    await db.commit()
    return {"deleted": trade_id}
