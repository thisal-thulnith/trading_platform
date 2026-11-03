"""WebSocket fan-out. Client sends {"subscribe": ["BTCUSDT:1h", ...]} to filter candle updates; signal,
notification, scan and liquidation events are always delivered."""
import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.bus import bus

router = APIRouter()
ALWAYS = {"signal", "notification", "scan", "liquidation"}


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q = bus.subscribe("candle", *ALWAYS)
    subs: set[str] = set()

    async def reader():
        while True:
            msg = json.loads(await ws.receive_text())
            if "subscribe" in msg:
                subs.clear()
                subs.update(str(x) for x in msg["subscribe"][:50])

    rtask = asyncio.create_task(reader())
    try:
        while True:
            ev = await q.get()
            if ev["topic"] == "candle":
                d = ev["data"]
                if f"{d['symbol']}:{d['tf']}" not in subs and f"{d['symbol']}:*" not in subs:
                    continue
            await ws.send_text(json.dumps(ev, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rtask.cancel()
        bus.unsubscribe(q)
        with contextlib.suppress(Exception):
            await ws.close()
