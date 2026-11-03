import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, market, my_trades, user, ws
from app.config import get_settings
from app.db import create_all, init_engine
from app.engine import engine
from app.logging import log, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    s = get_settings()
    if s.env == "prod" and s.jwt_secret == "dev-only-change-me":
        raise RuntimeError("AIT_JWT_SECRET must be set in production")
    init_engine()
    await create_all()
    if s.run_engine:
        await engine.start()
    log.info("startup", engine=s.run_engine, symbols=len(engine.latest))
    loop_task = asyncio.create_task(_intelligence_loop()) if s.run_engine else None
    yield
    if loop_task:
        loop_task.cancel()
    if s.run_engine:
        await engine.stop()


async def _intelligence_loop() -> None:
    """Every 5 minutes: keep the quant brain fresh and grade forecasts whose horizon has passed."""
    from app.quant import brain, ledger

    while True:
        try:
            await brain.get()
            await ledger.resolve(engine.client)
        except Exception as e:  # noqa: BLE001 - the loop must survive any single failure
            log.warning("intelligence_loop_error", error=str(e))
        await asyncio.sleep(300)


app = FastAPI(title="AI Crypto Market Intelligence", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(market.router)
app.include_router(user.router)
app.include_router(ws.router)
app.include_router(chat.router)
app.include_router(my_trades.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
