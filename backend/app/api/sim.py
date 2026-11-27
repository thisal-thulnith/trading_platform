"""Coin simulator API: replay the platform's systematic signal on one coin or on every coin, test it and simulate it."""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.sim import service
from app.sim.engine import SimConfig

router = APIRouter(prefix="/api/sim")


class SimIn(BaseModel):
    symbol: str = "ALL"  # a coin, or ALL for every coin
    start: str = "2021-01-01"
    end: str | None = None
    entry: float = Field(0.10, ge=0.0, le=1.0)
    exit: float = Field(0.0, ge=-1.0, le=1.0)
    shorts: bool = True
    leverage: float = Field(1.0, ge=1.0, le=10.0)
    stop_atr: float | None = Field(3.0, ge=0.5, le=20.0)
    weights: str = Field("live", pattern="^(live|prior)$")
    take_profit: float | None = Field(None, gt=0.0, le=2.0)
    partial_r: float | None = Field(None, gt=0.0, le=10.0)

    def config(self) -> SimConfig:
        return SimConfig(start=self.start, end=self.end, entry=self.entry, exit=min(self.exit, self.entry),
                         shorts=self.shorts, leverage=self.leverage, stop_atr=self.stop_atr, weights=self.weights,
                         take_profit=self.take_profit, partial_r=self.partial_r)


@router.get("/coins")
async def coins():
    """Every coin in the quant lab's panel with its history range (591 coins, delisted ones included)."""
    return {"coins": await asyncio.to_thread(service.coins)}


@router.post("/run")
async def run(body: SimIn):
    """One coin: run now. ALL: a cached result when this setup already ran on today's data, else a background job."""
    cfg = body.config()
    if body.symbol.upper() == "ALL":
        hit = await asyncio.to_thread(service.cached_all, cfg)
        if hit:
            return {"status": "done", "result": hit}
        return {"status": "running", "job": service.start_job(cfg)}
    sym = body.symbol.upper().replace("/", "").replace(" ", "")
    sym = sym if sym.endswith("USDT") else f"{sym}USDT"   # "SOL", "SOL/USDT" and "SOLUSDT" all mean the same coin
    try:
        return {"status": "done", "result": await asyncio.to_thread(service.run_symbol, sym, cfg)}
    except KeyError:
        raise HTTPException(404, f"{body.symbol} has no history in the simulator's data") from None


@router.get("/job/{jid}")
async def job(jid: str):
    j = service.job(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    return j


@router.get("/latest")
async def latest():
    """The last all-coins run (the daily job refreshes the default setup after each data update)."""
    return {"result": await asyncio.to_thread(service.latest_all)}


_compare_job: dict = {"status": "idle", "progress": 0.0, "error": None}


@router.get("/compare")
async def compare():
    """Head-to-head on every coin: the platform's signal vs the strategies competitors' bots and charts sell."""
    from app.sim import benchmarks

    res = await asyncio.to_thread(benchmarks.latest)
    return {"result": res, "stale": bool(res) and res.get("panel") != service._panel_key(), "job": _compare_job}


@router.post("/compare")
async def rerun_compare():
    """Re-run the head-to-head (~1 minute) in the background."""
    import threading

    from app.sim import benchmarks

    if _compare_job["status"] == "running":
        return _compare_job

    def work():
        try:
            benchmarks.compare(progress=lambda p: _compare_job.update(progress=round(p, 3)))
            _compare_job.update(status="done", progress=1.0)
        except Exception as e:  # noqa: BLE001
            _compare_job.update(status="error", error=str(e))

    _compare_job.update(status="running", progress=0.0, error=None)
    threading.Thread(target=work, daemon=True).start()
    return _compare_job


@router.get("/forward")
async def forward():
    """Pre-registered forward tests on data after their start date (docs/research/WINRATE_PREREG_2026-11-13.md)."""
    return {"forward": await asyncio.to_thread(service.forward)}


@router.get("/carry")
async def carry_books():
    """The funding-carry books (long spot + short perp): backtest with the measured basis, pass/fail against the
    pre-registered bar, and the forward paper record."""
    from app.sim import carry

    return {"backtest": await asyncio.to_thread(carry.latest), "forward": await asyncio.to_thread(carry.forward)}
