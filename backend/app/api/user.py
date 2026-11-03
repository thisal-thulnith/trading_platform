"""Authenticated endpoints: accounts, drawings, layouts, watchlists, paper portfolio, backtests, notifications."""
import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BacktestIn,
    CloseIn,
    DrawingIn,
    DrawingPatch,
    LayoutIn,
    LoginIn,
    OnboardingIn,
    PositionIn,
    RegisterIn,
    SizingIn,
    WatchlistIn,
)
from app.auth.security import (
    auth_limiter,
    create_token,
    current_user,
    current_user_optional,
    hash_password,
    verify_password,
)
from app.backtest.engine import BacktestConfig, run_backtest
from app.data.hub import hub
from app.data.ingest import backfill
from app.data.store import load_candles
from app.db import get_session, session_factory
from app.engine import engine
from app.logging import log
from app.models import (
    AuditLog,
    BacktestRun,
    ChartLayout,
    Drawing,
    Notification,
    PaperAccount,
    PaperPosition,
    Signal,
    User,
    Watchlist,
)
from app.portfolio import paper
from app.risk.sizing import RiskProfile, size_position
from app.signals.manager import close_manual

router = APIRouter(prefix="/api")


async def audit(db: AsyncSession, user_id: str | None, action: str, detail: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, detail=detail))


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role, "settings": u.settings,
            "created_at": u.created_at.isoformat() if u.created_at else None}


# ---------------------------------------------------------------- auth
@router.post("/auth/register", dependencies=[Depends(auth_limiter)])
async def register(body: RegisterIn, db: AsyncSession = Depends(get_session)):
    email = body.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar():
        raise HTTPException(409, "email already registered")
    first = (await db.execute(select(User.id).limit(1))).first() is None
    u = User(email=email, password_hash=hash_password(body.password), role="admin" if first else "user")
    db.add(u)
    await db.flush()
    db.add(Watchlist(user_id=u.id, name="Top universe", symbols=hub.symbols[:20]))
    await audit(db, u.id, "register", {})
    await db.commit()
    return {"token": create_token(u.id, u.role), "user": user_out(u)}


@router.post("/auth/login", dependencies=[Depends(auth_limiter)])
async def login(body: LoginIn, db: AsyncSession = Depends(get_session)):
    u = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar()
    if not u or not verify_password(body.password, u.password_hash):
        await audit(db, None, "login_failed", {"email": body.email.lower()})
        await db.commit()
        raise HTTPException(401, "invalid credentials")
    await audit(db, u.id, "login", {})
    await db.commit()
    return {"token": create_token(u.id, u.role), "user": user_out(u)}


@router.get("/auth/me")
async def me(u: User = Depends(current_user)):
    return user_out(u)


# ---------------------------------------------------------------- drawings (AI vs user separation)
@router.get("/drawings")
async def list_drawings(symbol: str, timeframe: str, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Drawing).where(Drawing.user_id == u.id, Drawing.symbol == symbol,
                                                   Drawing.timeframe == timeframe, Drawing.deleted.is_(False)))).scalars()
    return [drawing_out(d) for d in rows]


def drawing_out(d: Drawing) -> dict:
    return {"id": d.id, "symbol": d.symbol, "timeframe": d.timeframe, "origin": d.origin, "parent_id": d.parent_id,
            "kind": d.kind, "payload": d.payload, "hidden": d.hidden, "updated_at": d.updated_at.isoformat()}


@router.post("/drawings")
async def create_drawing(body: DrawingIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    """User drawings are always origin='user'. Editing an AI annotation = create a user drawing with parent_id
    set to the AI annotation id; the AI original stays untouched in its snapshot."""
    if body.parent_id:
        existing = (await db.execute(select(Drawing).where(Drawing.user_id == u.id, Drawing.parent_id == body.parent_id,
                                                           Drawing.deleted.is_(False)))).scalar()
        if existing:
            existing.payload, existing.kind, existing.hidden = body.payload, body.kind, body.hidden
            await db.commit()
            return drawing_out(existing)
    d = Drawing(user_id=u.id, symbol=body.symbol, timeframe=body.timeframe, origin="user", parent_id=body.parent_id,
                kind=body.kind, payload=body.payload, hidden=body.hidden)
    db.add(d)
    await db.commit()
    return drawing_out(d)


@router.patch("/drawings/{did}")
async def patch_drawing(did: str, body: DrawingPatch, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    d = await db.get(Drawing, did)
    if not d or d.user_id != u.id:
        raise HTTPException(404, "drawing not found")
    if body.payload is not None:
        d.payload = body.payload
    if body.hidden is not None:
        d.hidden = body.hidden
    await db.commit()
    return drawing_out(d)


@router.delete("/drawings/{did}")
async def delete_drawing(did: str, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    d = await db.get(Drawing, did)
    if not d or d.user_id != u.id:
        raise HTTPException(404, "drawing not found")
    d.deleted = True
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- layouts & watchlists
@router.get("/layouts")
async def layouts(u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(ChartLayout).where(ChartLayout.user_id == u.id))).scalars()
    return [{"id": r.id, "name": r.name, "payload": r.payload, "updated_at": r.updated_at.isoformat()} for r in rows]


@router.put("/layouts")
async def save_layout(body: LayoutIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    r = (await db.execute(select(ChartLayout).where(ChartLayout.user_id == u.id, ChartLayout.name == body.name))).scalar()
    if r:
        r.payload = body.payload
    else:
        r = ChartLayout(user_id=u.id, name=body.name, payload=body.payload)
        db.add(r)
    await db.commit()
    return {"id": r.id, "name": r.name}


@router.get("/watchlists")
async def watchlists(u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Watchlist).where(Watchlist.user_id == u.id))).scalars()
    return [{"id": r.id, "name": r.name, "symbols": r.symbols} for r in rows]


@router.put("/watchlists")
async def save_watchlist(body: WatchlistIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    r = (await db.execute(select(Watchlist).where(Watchlist.user_id == u.id, Watchlist.name == body.name))).scalar()
    syms = [s.upper() for s in body.symbols]
    if r:
        r.symbols = syms
    else:
        r = Watchlist(user_id=u.id, name=body.name, symbols=syms)
        db.add(r)
    await db.commit()
    return {"id": r.id, "name": r.name, "symbols": r.symbols}


# ---------------------------------------------------------------- portfolio
async def _acct(db, u: User) -> PaperAccount:
    a = (await db.execute(select(PaperAccount).where(PaperAccount.user_id == u.id))).scalar()
    if not a:
        raise HTTPException(404, "complete onboarding first")
    return a


@router.post("/portfolio/onboarding")
async def onboarding(body: OnboardingIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    a = (await db.execute(select(PaperAccount).where(PaperAccount.user_id == u.id))).scalar()
    risk = body.model_dump(exclude={"starting_capital", "base_currency", "reset"})
    if a and body.reset:
        await db.execute(update(PaperPosition).where(PaperPosition.account_id == a.id, PaperPosition.status == "open")
                         .values(status="closed", close_reason="account_reset", closed_at=datetime.now(UTC)))
        a.starting_capital = a.cash = body.starting_capital
    if a:
        a.risk, a.base_currency = {**(a.risk or {}), **risk}, body.base_currency
    else:
        a = PaperAccount(user_id=u.id, starting_capital=body.starting_capital, cash=body.starting_capital,
                         base_currency=body.base_currency, risk={**paper.DEFAULT_RISK, **risk})
        db.add(a)
    u.settings = {**(u.settings or {}), "onboarded": True, "timeframes": body.timeframes}
    await audit(db, u.id, "onboarding", body.model_dump())
    await db.commit()
    return await paper.summary(db, a, hub.last_price)


@router.get("/portfolio")
async def portfolio(u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    return await paper.summary(db, await _acct(db, u), hub.last_price)


@router.post("/portfolio/positions")
async def open_pos(body: PositionIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    a = await _acct(db, u)
    price = hub.last_price.get(body.symbol)
    if price is None or hub.freshness(body.symbol)["stale"]:
        raise HTTPException(409, "live price unavailable or stale — cannot open paper position")
    r = await paper.open_position(db, a, body.symbol, body.side, price, body.stop, body.take_profit, hub.last_price,
                                  body.signal_id)
    if not r["ok"]:
        raise HTTPException(422, r["reason"])
    await audit(db, u.id, "paper_open", {**body.model_dump(), "fill": r["fill"]})
    await db.commit()
    return r


@router.post("/portfolio/positions/{pid}/close")
async def close_pos(pid: str, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    a = await _acct(db, u)
    p = await db.get(PaperPosition, pid)
    if not p or p.account_id != a.id or p.status != "open":
        raise HTTPException(404, "open position not found")
    price = hub.last_price.get(p.symbol)
    if price is None:
        raise HTTPException(409, "live price unavailable")
    await paper.close_position(db, a, p, price, "manual")
    return paper.pos_dict(p)


@router.post("/portfolio/sizing")
async def sizing(body: SizingIn, u: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    base = RiskProfile(equity=1000.0)
    if u:
        a = (await db.execute(select(PaperAccount).where(PaperAccount.user_id == u.id))).scalar()
        if a:
            base = paper.profile(a, paper.equity_of(a, await paper.open_positions(db, a.id), hub.last_price))
    prof = RiskProfile(equity=body.equity or base.equity,
                       risk_per_trade_pct=body.risk_per_trade_pct or base.risk_per_trade_pct,
                       max_leverage=body.max_leverage or base.max_leverage, market=body.market or base.market,
                       taker_fee=base.taker_fee, slippage_bps=base.slippage_bps)
    return size_position(prof, body.symbol, body.side, body.entry, body.stop)


# ---------------------------------------------------------------- signals (user actions)
@router.post("/signals/{sid}/close")
async def close_signal(sid: str, body: CloseIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    s = await db.get(Signal, sid)
    if not s:
        raise HTTPException(404, "signal not found")
    if u.role != "admin":
        raise HTTPException(403, "only admins can close platform signals")
    price = hub.last_price.get(s.symbol) or s.reference_price
    try:
        await close_manual(db, s, price, f"manual: {body.reason}")
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    await audit(db, u.id, "signal_close", {"signal": sid})
    await db.commit()
    return {"ok": True, "outcome": s.outcome}


# ---------------------------------------------------------------- notifications
@router.get("/notifications")
async def notifications(limit: int = 100, u: User | None = Depends(current_user_optional),
                        db: AsyncSession = Depends(get_session)):
    q = select(Notification).order_by(Notification.ts.desc()).limit(min(limit, 500))
    q = q.where(or_(Notification.user_id.is_(None), Notification.user_id == (u.id if u else None)))
    rows = (await db.execute(q)).scalars().all()
    return [{"id": n.id, "ts": n.ts.isoformat(), "category": n.category, "severity": n.severity, "title": n.title,
             "body": n.body, "symbol": n.symbol, "timeframe": n.timeframe, "signal_id": n.signal_id,
             "snapshot_id": n.snapshot_id, "read": n.read} for n in rows]


# ---------------------------------------------------------------- backtests
_bt_tasks: dict[str, asyncio.Task] = {}


@router.post("/backtests")
async def start_backtest(body: BacktestIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    running = sum(1 for t in _bt_tasks.values() if not t.done())
    if running >= 2:
        raise HTTPException(429, "two backtests already running; try again later")
    cfg = BacktestConfig(symbols=[s.upper() for s in body.symbols], timeframe=body.timeframe, strategies=body.strategies,
                         step=body.step, initial_equity=body.initial_equity, risk_per_trade_pct=body.risk_per_trade_pct,
                         max_concurrent=body.max_concurrent, fee=body.fee, slip_bps=body.slip_bps)
    run = BacktestRun(config={**cfg.__dict__, "bars": body.bars}, config_hash=cfg.hash(), status="running")
    db.add(run)
    await audit(db, u.id, "backtest_start", {"run": run.id})
    await db.commit()
    _bt_tasks[run.id] = asyncio.create_task(_run_bt(run.id, cfg, body.bars))
    return {"id": run.id, "status": "running"}


async def _run_bt(run_id: str, cfg: BacktestConfig, bars: int) -> None:
    try:
        data = {}
        for sym in cfg.symbols:
            await backfill(engine.client, sym, cfg.timeframe, bars)
            # higher timeframes are resampled from base data inside the backtester (no look-ahead)
            async with session_factory()() as db:
                df = await load_candles(db, sym, cfg.timeframe, limit=bars)
            # drop the forming candle
            data[sym] = df.iloc[:-1].reset_index(drop=True)
        res = await asyncio.to_thread(run_backtest, data, cfg)
        status = "done"
    except Exception as e:  # noqa: BLE001
        log.error("backtest_failed", run=run_id, error=str(e))
        res, status = {"error": str(e)}, "failed"
    async with session_factory()() as db:
        r = await db.get(BacktestRun, run_id)
        r.results, r.status = res, status
        await db.commit()
    if status == "done":
        await engine.refresh_research_gate()


@router.get("/backtests")
async def list_backtests(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(50))).scalars()
    out = []
    for r in rows:
        base = (r.results or {}).get("scenarios", {}).get("base", {})
        out.append({"id": r.id, "created_at": r.created_at.isoformat(), "status": r.status, "config": r.config,
                    "config_hash": r.config_hash, "summary": {"trades": base.get("trades"), "equity": base.get("equity")}})
    return out


@router.get("/backtests/{bid}")
async def get_backtest(bid: str, db: AsyncSession = Depends(get_session)):
    r = await db.get(BacktestRun, bid)
    if not r:
        raise HTTPException(404, "backtest not found")
    return {"id": r.id, "status": r.status, "config": r.config, "config_hash": r.config_hash, "results": r.results,
            "created_at": r.created_at.isoformat()}
