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


@router.get("/plan")
async def my_plan(user: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    """The caller's plan, today's usage and every plan (app/plans.py)."""
    from app import plans

    return await plans.status(db, user)


class PlanSwitch(BaseModel):
    plan: str


@router.post("/plan")
async def switch_plan(body: PlanSwitch, user: User | None = Depends(current_user_optional),
                      db: AsyncSession = Depends(get_session)):
    """Change the logged-in user's plan. Billing is not connected in this build, so the switch is free; wire a payment
    provider's webhook to this before charging anyone."""
    from app import plans

    if body.plan not in plans.PLANS:
        raise HTTPException(400, f"plan must be one of {', '.join(plans.PLANS)}")
    if user is None:
        raise HTTPException(401, "log in to choose a plan (without an account the installation's plan applies)")
    user.settings = {**(user.settings or {}), "plan": body.plan}
    await db.commit()
    return await plans.status(db, user)


class TradeIn(BaseModel):
    symbol: str
    side: str = "LONG"
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
async def my_desk(budget: float = 100.0, mode: str = "conservative", venue: str = "spot", leverage: int = 3, run: bool = False,
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
    return intel_desk.portfolio_desk(acct, frames, portfolio, filters, system._cache.get("funding") or {}, start=run)


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


# ------------------------------------------------------------------------------------------- your account, managed
class BalanceIn(BaseModel):
    balance: float


async def _account_view(db: AsyncSession, uid: str | None, symbol: str | None = None) -> dict:
    from app.api.agent import signal_today
    from app.ml import signal_test
    from app.portfolio import manager

    trades = await my_trades.list_trades(db, uid)
    open_t = [t for t in trades if t.status == "open"]
    try:
        frames = await system._frames()
    except RuntimeError:
        frames = {}
    fallback = {s: float(f.close.iloc[-1]) for s, f in frames.items()}
    px = await my_trades.marks([t.symbol for t in open_t], fallback)
    cards = [my_trades.card(t, px.get(t.symbol), my_trades.plan_state(t, frames.get(t.symbol))) for t in open_t]
    bal = manager.get_balance()
    margin = 100.0 if bal is None else max(10.0, min(1_000_000.0, bal["balance"]))
    if symbol:   # any coin's main signal, sized for your account (you picked it)
        from app.api.agent import signal_plan

        p = await signal_plan(symbol=symbol, margin=margin, side=None)
        today = {"plan": p, "final": p.get("final")}
    else:
        today = await signal_today(margin=margin)
    return await manager.view(db, uid, today, await signal_test.report(), cards, trades, explicit=bool(symbol))


@router.get("/account")
async def my_account(u: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    """Your account: the balance you entered, its equity since then, the Risk manager's mode (with its reasons) and
    today's main signal sized for your account."""
    return await _account_view(db, _uid(u))


@router.post("/account")
async def set_my_account(body: BalanceIn, u: User | None = Depends(current_user_optional),
                         db: AsyncSession = Depends(get_session)):
    from app.portfolio import manager

    if not 10 <= body.balance <= 10_000_000:
        raise HTTPException(400, "balance must be 10..10,000,000 USDT")
    manager.set_balance(body.balance)
    return await _account_view(db, _uid(u))


@router.get("/account/plan")
async def coin_plan(symbol: str, u: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    """Any coin's main signal sized for your account by the Risk manager (half size at most when the machine does not
    trade it today)."""
    return await _account_view(db, _uid(u), symbol)


class AcceptIn(BaseModel):
    symbol: str | None = None


@router.post("/account/accept")
async def accept_plan(body: AcceptIn | None = None, u: User | None = Depends(current_user_optional),
                      db: AsyncSession = Depends(get_session)):
    """You placed today's suggested trade on Binance: record it in your trades (recomputed now, so the numbers are the
    current ones). Paper only: nothing is sent to an exchange."""
    v = await _account_view(db, _uid(u), body.symbol if body else None)
    s = (v or {}).get("suggestion") or {}
    if not v.get("set"):
        raise HTTPException(400, "enter your account balance first")
    if not s.get("ok"):
        raise HTTPException(400, s.get("reason") or "no trade to accept today")
    t = await my_trades.add(db, _uid(u), {
        "symbol": s["symbol"], "side": s["side"], "venue": "futures", "leverage": s["leverage"], "entry": s["entry"],
        "qty": s["qty"], "stop": s["stop"], "target": s["target"], "budget_usd": v["account"]["equity"],
        "mode": s["mode"], "source": "planner",
        "note": f"Today's plan ({s['mode']}, {s['risk_pct']}% risk): " + " ".join(v["risk"]["reasons"])[:400]})
    return {"trade": my_trades.card(t, None, my_trades.plan_state(t, None)), "account": await _account_view(db, _uid(u))}


@router.get("/account/check")
async def pre_trade_check(symbol: str, side: str, entry: float | None = None, stop: float | None = None,
                          target: float | None = None, u: User | None = Depends(current_user_optional),
                          db: AsyncSession = Depends(get_session)):
    """Before you record a trade (any signal: the main one, a Lab one or your own): the Risk manager's advice — take it,
    careful, or not recommended — with its reasons, and the size for your account at these levels."""
    from app.api.agent import signal_plan
    from app.ml import board
    from app.ml import unified as un
    from app.portfolio import manager

    side = side.upper()
    if side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    sym = symbol.upper().replace("/", "")
    sym = sym if sym.endswith("USDT") else f"{sym}USDT"
    v = await _account_view(db, _uid(u), sym)
    p = await signal_plan(symbol=sym, margin=100.0, side=None)
    me = next((x for x in un.ranked() if x["symbol"] == sym), None)
    conf = board.confidence(me["rank"], side, board.calibration()) if me else None
    trades = await my_trades.list_trades(db, _uid(u))
    open_cards = [{"symbol": t.symbol, "open_risk_usd": 0.0} for t in trades if t.status == "open"]
    use = {"entry": entry, "stop": stop, "target": target} if entry and stop and target else None
    if use is None and p.get("covered") and p.get("plans") and p.get("side") == side:
        sw = p["plans"]["swing"]
        use = {"entry": float(sw["entry"]), "stop": float(sw["stop"]), "target": float(sw["tp1"])}
    price = float(p.get("price") or 0) or None
    acct, risk = v.get("account"), v.get("risk")
    out = manager.assess(side, p, acct, risk, open_cards, conf, price, use)
    sizing = None
    if acct and risk and use:
        d = 1 if side == "LONG" else -1
        if (use["entry"] - use["stop"]) * d > 0 and (use["target"] - use["entry"]) * d > 0:
            safe = int(min(((p.get("plans") or {}).get("swing") or {}).get("leverage", {}).get("safe_max", 20), 20)) \
                if p.get("side") == side else 20
            machines_trade = p.get("status") == "trade" and p.get("side") == side
            pct = risk["risk_pct"] if machines_trade else min(risk["risk_pct"], 0.5)   # not the machine's trade: half size at most
            sz = manager.size_trade(acct["equity"], pct,
                                    risk["mode"], side, use["entry"], use["stop"], max(1, safe), acct["open_risk_usd"])
            if sz.get("ok"):
                reward = sz["notional_usd"] * (abs(use["target"] - use["entry"]) / use["entry"] - 2 * manager.FEE)
                sizing = {**sz, **use, "reward_usd": round(reward, 2), "mode": risk["mode"]}
            else:
                sizing = sz
    return {**out, "symbol": sym, "side": side, "levels": use, "sizing": sizing, "account": acct,
            "main": {"side": p.get("side"), "clear": p.get("clear"), "status": p.get("status"), "rank": p.get("rank")}}

