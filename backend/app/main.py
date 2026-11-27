import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, chat, market, my_trades, sim, user, ws
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
    from app.agents import readiness

    watch_task = asyncio.create_task(readiness.watch()) if s.run_engine and s.readiness_watch else None
    quant_task = asyncio.create_task(_quant_daily_loop()) if s.run_engine else None
    live_task = asyncio.create_task(_live_grading_loop()) if s.run_engine else None
    lob_stop, lob_task = asyncio.Event(), None
    if s.run_engine and s.lob_record:
        from app.quant import lob
        from app.quant.lob.recorder import LobConfig

        lob_task = asyncio.create_task(lob.run_recorder(lob_stop, config=LobConfig.from_env(
            top_n=s.lob_top_n, depth_speed=s.lob_depth_speed)))
    yield
    if lob_task:
        lob_stop.set()
        try:
            await asyncio.wait_for(lob_task, timeout=20)  # let it flush the last minute
        except Exception as e:  # noqa: BLE001
            log.warning("lob_stop_failed", error=str(e))
    for t in (loop_task, watch_task, quant_task, live_task):
        if t:
            t.cancel()
    if s.run_engine:
        await engine.stop()


async def _quant_daily_loop() -> None:
    """Once a UTC day: refresh the quant lab's panel (incremental), advance the quant sandbox's paper portfolio and
    re-run the all-coins simulator (cached per panel version, so this is a no-op until new data arrives)."""
    from datetime import UTC, datetime, timedelta

    while True:
        try:
            from app.quant.lab import live_ranking
            from app.quant.lab.data import update
            from app.signal import fusion, quant_sandbox

            yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
            if (live_ranking().get("data_through") or "") < yesterday:
                await update()
                fusion._lab.update(t=0.0, data=None)  # the fused signal re-reads the new ranking
            await asyncio.to_thread(quant_sandbox.step)
            from app.sim import service as sim_service
            from app.sim.engine import SimConfig

            await asyncio.to_thread(sim_service.run_all_cached, SimConfig())  # replay every coin on the new data
            await asyncio.to_thread(sim_service.run_forward)  # pre-registered forward tests (new data only)
            from app.quant.lab import perps
            from app.sim import carry

            if (carry.forward() or {}).get("data_through", "") < yesterday:
                await perps.update()  # perp candles for the carry books (incremental)
                await asyncio.to_thread(carry.run_all_presets)
                await asyncio.to_thread(carry.run_forward)
        except Exception as e:  # noqa: BLE001 - the loop must survive any single failure
            log.warning("quant_daily_loop_error", error=str(e))
        await asyncio.sleep(3600)


async def _live_grading_loop() -> None:
    """Every minute: save today's signals and grade the open ones on the live 1h candles (the signal test and Live
    results stay live; paper only)."""
    from app.ml import signal_test

    while True:
        try:
            await signal_test.tick(every_s=55)
        except Exception as e:  # noqa: BLE001 - the loop must survive any single failure
            log.warning("live_grading_error", error=str(e))
        await asyncio.sleep(60)


async def _intelligence_loop() -> None:
    """Every 5 minutes: keep the quant brain fresh and grade forecasts whose horizon has passed."""
    from app.quant import brain, ledger

    while True:
        try:
            await brain.get()
            await ledger.resolve(engine.client)
            from app.agents import journal

            await journal.grade()  # grade the desk's calls on the candles since they were made
            from app.agents import daily

            await daily.watch_triggers()  # enter "wait for trigger" picks whose setup turned ready
            await daily.grade()     # grade the daily picks for the sandbox
            await daily.today()     # make today's picks once the UTC day turns
            from app.ml import forecast

            # re-score the trained models when the panel has a new day; retrain monthly (in a worker thread)
            await asyncio.to_thread(forecast.refresh_if_stale)
            from app.ml import signal_test

            await signal_test.tick()  # save today's signals and grade the open ones on live candles (paper only)
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
app.include_router(agent.router)
app.include_router(sim.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
