"""Market data ingestion: historical backfill, gap repair, live WebSocket streams, derivatives polling."""
import asyncio
import json
import random

import websockets

from app.bus import bus
from app.config import get_settings
from app.data.binance import BinanceClient
from app.data.http import ProviderUnavailable
from app.data.hub import hub, now_ms
from app.data.store import (
    earliest_open_time,
    find_gaps,
    latest_open_time,
    load_candles,
    upsert_candles,
    validate_candle,
)
from app.data.timeframes import floor_time, tf_ms
from app.db import session_factory
from app.logging import log
from app.models import DerivativesSnapshot


async def backfill(client: BinanceClient, symbol: str, tf: str, bars: int, market: str = "spot") -> int:
    """Fetch the most recent `bars` candles (paginated) and repair internal gaps. Idempotent."""
    step = tf_ms(tf)
    async with session_factory()() as s:
        first = await earliest_open_time(s, symbol, tf, market)
        last = await latest_open_time(s, symbol, tf, market)
    end = now_ms()
    want_start = end - bars * step
    ranges = []
    if last is None:
        ranges.append((want_start, end))
    else:
        if first is not None and first > want_start + step:
            ranges.append((want_start, first - step))  # extend history backwards
        ranges.append((max(last - 2 * step, want_start), end))  # catch up to now
    total = 0
    for start, stop in ranges:
        cur = start
        while cur < stop:
            rows = await client.klines(symbol, tf, market, 1000, start_ms=cur, end_ms=stop)
            if not rows:
                break
            async with session_factory()() as s:
                total += await upsert_candles(s, symbol, market, tf, rows)
            nxt = rows[-1]["open_time"] + step
            if nxt <= cur or len(rows) < 1000:
                break
            cur = nxt
    # gap repair on stored window
    async with session_factory()() as s:
        df = await load_candles(s, symbol, tf, market, limit=bars)
    gaps = find_gaps(df["open_time"].tolist(), tf) if len(df) else []
    for a, b in gaps[:20]:
        rows = await client.klines(symbol, tf, market, 1000, start_ms=a, end_ms=b)
        async with session_factory()() as s:
            await upsert_candles(s, symbol, market, tf, rows)
        hub.gaps_repaired += 1
    return total


class KlineStream:
    """Combined Binance spot kline stream for all (symbol, timeframe) pairs with reconnection + backoff.
    Binance closes connections after 24h; we also proactively reconnect every 23h."""

    def __init__(self, client: BinanceClient, symbols: list[str], tfs: list[str]):
        self.client, self.symbols, self.tfs = client, symbols, tfs
        self.status = hub.stream("binance_spot_klines")
        self._stop = asyncio.Event()

    def url(self) -> str:
        streams = "/".join(f"{s.lower()}@kline_{tf}" for s in self.symbols for tf in self.tfs)
        return f"{get_settings().binance_spot_ws}?streams={streams}"

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url(), ping_interval=20, ping_timeout=20, max_queue=4096,
                                              open_timeout=15) as ws:
                    self.status.connected, attempt = True, 0
                    log.info("ws_connected", stream=self.status.name, n=len(self.symbols) * len(self.tfs))
                    deadline = asyncio.get_running_loop().time() + 23 * 3600
                    # after (re)connect, close any gap created while disconnected
                    asyncio.create_task(self._resync())
                    while not self._stop.is_set():
                        timeout = max(1.0, deadline - asyncio.get_running_loop().time())
                        if timeout <= 1.0:
                            break
                        msg = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 90))
                        await self.handle(json.loads(msg))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.status.last_error = f"{type(e).__name__}: {e}"
                log.warning("ws_error", stream=self.status.name, error=self.status.last_error)
            self.status.connected = False
            self.status.reconnects += 1
            attempt += 1
            await asyncio.sleep(min(60, 2 ** min(attempt, 6)) + random.random())

    async def _resync(self) -> None:
        for sym in self.symbols:
            for tf in self.tfs:
                try:
                    await backfill(self.client, sym, tf, 50)
                except Exception as e:  # noqa: BLE001
                    log.warning("resync_failed", symbol=sym, tf=tf, error=str(e))

    async def handle(self, msg: dict) -> None:
        d = msg.get("data") or {}
        if d.get("e") != "kline":
            return
        k = d["k"]
        sym, tf = k["s"], k["i"]
        candle = {"open_time": int(k["t"]), "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]),
                  "close": float(k["c"]), "volume": float(k["v"]), "quote_volume": float(k["q"]),
                  "trades": int(k["n"]), "taker_buy_base": float(k["V"]), "taker_buy_quote": float(k["Q"])}
        self.status.messages += 1
        self.status.last_msg_ms = now_ms()
        if floor_time(candle["open_time"], tf) != candle["open_time"]:
            hub.candles_rejected += 1
            return
        if validate_candle(candle):
            hub.candles_rejected += 1
            return
        hub.live[(sym, tf)] = candle
        hub.last_price[sym] = candle["close"]
        hub.last_price_ms[sym] = now_ms()
        bus.publish("candle", {"symbol": sym, "tf": tf, "candle": candle, "closed": bool(k["x"])})
        if k["x"]:
            async with session_factory()() as s:
                await upsert_candles(s, sym, "spot", tf, [candle])
            bus.publish("candle_closed", {"symbol": sym, "tf": tf, "open_time": candle["open_time"]})


class FuturesStream:
    """USDⓈ-M mark price (all symbols, 1s) and liquidation orders. Requires the routed /market path."""

    def __init__(self, symbols: list[str]):
        self.symbols = set(symbols)
        self.status = hub.stream("binance_futures_market")
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        url = f"{get_settings().binance_futures_ws}?streams=!markPrice@arr@1s/!forceOrder@arr"
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, open_timeout=15) as ws:
                    self.status.connected, attempt = True, 0
                    while not self._stop.is_set():
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                        self.status.messages += 1
                        self.status.last_msg_ms = now_ms()
                        data = msg.get("data")
                        if isinstance(data, list):
                            for m in data:
                                if m.get("s") in self.symbols:
                                    hub.mark[m["s"]] = {"mark_price": float(m["p"]), "index_price": float(m["i"]),
                                                        "funding_rate": float(m["r"]),
                                                        "next_funding_time": int(m["T"]), "ts": int(m["E"])}
                        elif isinstance(data, dict) and data.get("e") == "forceOrder":
                            o = data["o"]
                            if o["s"] in self.symbols:
                                liq = {"symbol": o["s"], "side": o["S"], "qty": float(o["z"] or o["q"]),
                                       "price": float(o["ap"] or o["p"]), "time": int(o["T"]),
                                       "note": "Binance publishes at most one liquidation snapshot per symbol per "
                                               "second; this is a lower bound of actual liquidations."}
                                liq["notional"] = liq["qty"] * liq["price"]
                                hub.liquidations[o["s"]].append(liq)
                                bus.publish("liquidation", liq)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.status.last_error = f"{type(e).__name__}: {e}"
            self.status.connected = False
            self.status.reconnects += 1
            attempt += 1
            await asyncio.sleep(min(60, 2 ** min(attempt, 6)) + random.random())


async def poll_derivatives(client: BinanceClient, symbols: list[str], perps: set[str]) -> None:
    """Snapshot funding/OI/ratios into our own table (Binance keeps only ~30 days of OI history)."""
    try:
        prem = {p["symbol"]: p for p in await client.premium_index()}
    except ProviderUnavailable as e:
        log.warning("premium_index_failed", error=str(e))
        return
    ts = now_ms()
    rows = []
    for sym in symbols:
        if sym not in perps:
            continue
        p = prem.get(sym)
        if not p:
            continue
        row = {"symbol": sym, "ts": ts, "mark_price": float(p["markPrice"]), "index_price": float(p["indexPrice"]),
               "funding_rate": float(p["lastFundingRate"]), "next_funding_time": int(p["nextFundingTime"]),
               "spot_price": hub.last_price.get(sym)}
        try:
            oi = await client.open_interest(sym)
            row["open_interest"] = float(oi["openInterest"])
            row["open_interest_value"] = row["open_interest"] * row["mark_price"]
            ls = await client.futures_data("globalLongShortAccountRatio", sym, "5m", 1)
            tk = await client.futures_data("takerlongshortRatio", sym, "5m", 1)
            row["long_short_ratio"] = float(ls[0]["longShortRatio"]) if ls else None
            row["taker_buy_sell_ratio"] = float(tk[0]["buySellRatio"]) if tk else None
        except (ProviderUnavailable, KeyError, IndexError) as e:
            log.warning("deriv_partial", symbol=sym, error=str(e))
        hub.deriv[sym] = row
        rows.append(row)
    async with session_factory()() as s:
        s.add_all([DerivativesSnapshot(**r) for r in rows])
        await s.commit()
    bus.publish("derivatives", {"ts": ts, "n": len(rows)})


async def seed_oi_history(client: BinanceClient, symbol: str) -> list[dict]:
    """Bootstrap OI history (5m, up to 500 points ≈ 41h) from Binance for OI-change features."""
    rows = await client.futures_data("openInterestHist", symbol, "5m", 500)
    return [{"ts": int(r["timestamp"]), "oi": float(r["sumOpenInterest"]), "oi_value": float(r["sumOpenInterestValue"])}
            for r in rows]


async def poll_books(client: BinanceClient, symbols: list[str]) -> None:
    from app.analysis.microstructure import book_metrics

    for sym in symbols:
        try:
            d = await client.depth(sym, 100)
            m = book_metrics(d)
            m["ts"] = now_ms()
            hub.books[sym] = {**m, "raw": {"bids": d["bids"][:50], "asks": d["asks"][:50]}}
        except ProviderUnavailable as e:
            log.warning("depth_failed", symbol=sym, error=str(e))
        await asyncio.sleep(0.05)
