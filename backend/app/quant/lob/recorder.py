"""Binance USDⓈ-M futures microstructure recorder: top-20 book snapshots every 10 s and aggregated trades.

Endpoints verified against the live service on 2026-11-11:
  wss://fstream.binance.com/public/stream?streams=<sym>@depth20@100ms/...   (depth is ONLY on /public; legacy /stream
      still serves it, /market does not). Payload data: e="depthUpdate", E, T, s, U, u, pu, b[[p,q]x20], a[[p,q]x20].
  wss://fstream.binance.com/market/stream?streams=<sym>@aggTrade/...        (aggTrade is ONLY on /market).
      Payload data: e="aggTrade", E, a, s, p, q, nq, f, l, T, m, st.
  GET https://fapi.binance.com/fapi/v1/depth?symbol=&limit=20   weight 2 -> lastUpdateId, E, T, bids, asks
  GET https://fapi.binance.com/fapi/v1/aggTrades?symbol=&fromId=&limit=1000   weight 20 -> [{a,p,q,nq,f,l,T,m}]
  GET /fapi/v1/ticker/24hr (weight 40) + /fapi/v1/exchangeInfo (weight 1) for the universe.

Book: the 100 ms stream is held in memory as the raw message string (parsed only when sampled); every `book_interval_s`
on the wall clock the latest message per symbol is written. A symbol whose last message is older than `stale_s` is
filled from REST instead (src="r"), within a weight budget.
Trades: aggregate ids are consecutive per symbol, so any jump (dropped message, reconnect, Binance's 24 h disconnect)
is backfilled from REST /aggTrades; ids still missing are counted as a trade gap.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets

from app.logging import log
from app.quant.lob import storage as st

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "lob"


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds") if ms else None


@dataclass
class LobConfig:
    root: Path = DEFAULT_ROOT
    symbols: list[str] | None = None          # None = auto universe (top_n perps by 24h quote volume, daily)
    top_n: int = 20
    book_interval_s: float = 10.0
    depth_speed: str = "100ms"                # "100ms" | "250ms" (write "") | "500ms"
    trade_mode: str = "1s"                    # "1s" per-second aggregates (default, fits the budget) | "raw"
    flush_interval_s: float = 60.0
    gap_s: float = 60.0
    stale_s: float = 3.0
    cap_bytes: int = 3_000_000_000
    max_age_days: float | None = 35.0
    evict_to_fit: bool = False                # True: delete oldest files to stay under the cap instead of refusing
    min_free_bytes: int = 2_000_000_000       # also refuse when the machine has less than this free
    retention_interval_s: float = 300.0
    universe_refresh_s: float = 86_400.0
    rest_weight_per_min: int = 600            # this recorder's share of Binance's 2400/min IP limit
    max_backfill_trades: int = 20_000         # per id gap
    public_ws: str = "wss://fstream.binance.com/public/stream"
    market_ws: str = "wss://fstream.binance.com/market/stream"
    rest: str = "https://fapi.binance.com"

    @classmethod
    def from_env(cls, **overrides) -> LobConfig:
        """AIT_LOB_ROOT, AIT_LOB_SYMBOLS (comma list), AIT_LOB_TOP_N, AIT_LOB_CAP_GB, AIT_LOB_MAX_AGE_DAYS,
        AIT_LOB_TRADE_MODE, AIT_LOB_DEPTH_SPEED, AIT_LOB_EVICT_TO_FIT. Explicit overrides win."""
        e = os.environ.get
        kw: dict = {}
        if e("AIT_LOB_ROOT"):
            kw["root"] = Path(e("AIT_LOB_ROOT"))
        if e("AIT_LOB_SYMBOLS"):
            kw["symbols"] = [s.strip().upper() for s in e("AIT_LOB_SYMBOLS").split(",") if s.strip()]
        if e("AIT_LOB_TOP_N"):
            kw["top_n"] = int(e("AIT_LOB_TOP_N"))
        if e("AIT_LOB_CAP_GB"):
            kw["cap_bytes"] = int(float(e("AIT_LOB_CAP_GB")) * 1e9)
        if e("AIT_LOB_MAX_AGE_DAYS"):
            kw["max_age_days"] = float(e("AIT_LOB_MAX_AGE_DAYS"))
        if e("AIT_LOB_TRADE_MODE"):
            kw["trade_mode"] = e("AIT_LOB_TRADE_MODE")
        if e("AIT_LOB_DEPTH_SPEED") is not None:
            kw["depth_speed"] = e("AIT_LOB_DEPTH_SPEED")
        if e("AIT_LOB_EVICT_TO_FIT"):
            kw["evict_to_fit"] = e("AIT_LOB_EVICT_TO_FIT").lower() in ("1", "true", "yes")
        kw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kw)

    @property
    def trade_stream(self) -> str:
        return "trades_raw" if self.trade_mode == "raw" else "trades_1s"


# ---------------------------------------------------------------- pure helpers (unit-tested)
class GapTracker:
    """Counts gaps longer than `gap_ms` between consecutive rows of one key, once per gap episode. `check` flags a gap
    that is still open (no row for > gap_ms) so status shows outages before the next row arrives."""

    def __init__(self, gap_ms: int):
        self.gap_ms = gap_ms
        self.last: dict = {}
        self.open: dict = {}       # key -> ts of the last row before the gap
        self.count: dict = defaultdict(int)
        self.events: deque = deque(maxlen=200)

    def seed(self, key, ts: int) -> None:
        self.last[key] = max(self.last.get(key, 0), ts)

    def observe(self, key, ts: int) -> int | None:
        """Record a row; returns the gap length in ms if this row closes a gap."""
        prev = self.last.get(key)
        gap = None
        if prev is not None and ts - prev > self.gap_ms:
            gap = ts - prev
            if key not in self.open:
                self.count[key] += 1
            self.events.append({"key": key, "from": prev, "to": ts, "ms": gap})
            log.warning("lob_gap", key=str(key), seconds=round(gap / 1000, 1), since=iso(prev))
        self.open.pop(key, None)
        if prev is None or ts > prev:
            self.last[key] = ts
        return gap

    def check(self, key, now: int) -> bool:
        prev = self.last.get(key)
        if prev is not None and now - prev > self.gap_ms and key not in self.open:
            self.open[key] = prev
            self.count[key] += 1
            log.warning("lob_gap_open", key=str(key), since=iso(prev))
        return key in self.open

    def reset_counts(self) -> None:
        self.count.clear()


def depth_row(data: dict, src: str = "w", levels: int = st.LEVELS) -> tuple[int, list]:
    """(ts, fields) for the book stream from a depthUpdate payload or a REST /depth response. Prices and sizes are
    kept as Binance's own strings (exact, and they compress better than float reprs)."""
    bids = data.get("b", data.get("bids")) or []
    asks = data.get("a", data.get("asks")) or []
    ts = int(data.get("T") or data.get("E"))
    u = data.get("u", data.get("lastUpdateId"))

    def side(lv, i):
        return [x[i] for x in lv[:levels]] + [""] * (levels - min(len(lv), levels))

    return ts, [u, src] + side(bids, 0) + side(bids, 1) + side(asks, 0) + side(asks, 1)


def _g(x: float) -> str:
    return format(x, ".10g")


class SecondAggregator:
    """Per-second trade aggregates for one symbol: count, buyer-initiated count, buy/sell base volume, VWAP, high, low,
    last price and last aggregate id. `m` = is_buyer_maker (True means the taker sold)."""

    def __init__(self):
        self.b: dict[int, list] = {}

    def add(self, T: int, a: int, p: float, q: float, m: bool) -> None:
        sec = T // 1000 * 1000
        b = self.b.get(sec)
        if b is None:
            b = self.b[sec] = [0, 0, 0.0, 0.0, 0.0, 0.0, p, p, p, a, a]
        b[0] += 1
        if m:
            b[3] += q
            b[5] += p * q
        else:
            b[1] += 1
            b[2] += q
            b[4] += p * q
        b[6] = max(b[6], p)
        b[7] = min(b[7], p)
        if a >= b[10]:
            b[8], b[10] = p, a
        b[9] = min(b[9], a)

    def pop_before(self, sec_ms: int | None = None) -> list[tuple[int, list]]:
        """Finished seconds (< sec_ms; all if None) as (ts, fields) rows in time order."""
        done = sorted(s for s in self.b if sec_ms is None or s < sec_ms)
        rows = []
        for s in done:
            n, nb, bq, sq, bqq, sqq, hi, lo, last, _af, al = self.b.pop(s)
            vol = bq + sq
            rows.append((s, [n, nb, _g(bq), _g(sq), _g((bqq + sqq) / vol) if vol else "", _g(hi), _g(lo), _g(last),
                             al]))
        return rows


def select_universe(tickers: list[dict], exchange_info: dict, top_n: int, current: list[str] | None = None,
                    keep_within: float = 1.5) -> list[str]:
    """Top `top_n` TRADING USDT perpetuals by 24h quote volume. Members of `current` stay while they rank within
    top_n * keep_within (hysteresis, so the set does not churn daily on the margin)."""
    ok = {s["symbol"] for s in exchange_info.get("symbols", [])
          if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
          and s["symbol"].isascii() and s["symbol"].isalnum()}
    ranked = [t["symbol"] for t in sorted((t for t in tickers if t["symbol"] in ok),
                                          key=lambda t: -float(t.get("quoteVolume") or 0))]
    keep = set(ranked[: int(top_n * keep_within)]) & set(current or [])
    out = [s for s in ranked if s in keep]
    for s in ranked:
        if len(out) >= top_n:
            break
        if s not in out:
            out.append(s)
    rank = {s: i for i, s in enumerate(ranked)}
    return sorted(out[:top_n], key=rank.__getitem__)


class WeightLimiter:
    """Client-side request-weight budget per rolling minute window; honours 429/418 back-off."""

    def __init__(self, per_min: int):
        self.per_min = per_min
        self.start = time.monotonic()
        self.used = 0
        self.blocked_until = 0.0

    async def acquire(self, w: int) -> None:
        while True:
            now = time.monotonic()
            if now < self.blocked_until:
                await asyncio.sleep(self.blocked_until - now)
                continue
            if now - self.start >= 60:
                self.start, self.used = now, 0
            if self.used + w <= self.per_min:
                self.used += w
                return
            await asyncio.sleep(60 - (now - self.start) + 0.05)

    def block(self, seconds: float) -> None:
        self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)


@dataclass
class StreamState:
    connected: bool = False
    reconnects: int = 0
    messages: int = 0
    last_msg_ms: int | None = None
    last_error: str | None = None
    connected_since_ms: int | None = None


@dataclass
class _TradeState:
    last_id: int | None = None
    last_ts: int | None = None
    agg: SecondAggregator = field(default_factory=SecondAggregator)


# ---------------------------------------------------------------- recorder
class Recorder:
    def __init__(self, config: LobConfig | None = None, clock=now_ms):
        self.cfg = config or LobConfig()
        self.cfg.root = Path(self.cfg.root)
        self.clock = clock
        self.budget = st.Budget(self.cfg.root, self.cfg.cap_bytes, self.cfg.min_free_bytes)
        self.manifest = st.Manifest(self.cfg.root)
        self.store = st.HourlyStore(self.cfg.root, self.budget, self.manifest)
        self.symbols: list[str] = [s.upper() for s in self.cfg.symbols] if self.cfg.symbols else []
        self.auto_universe = not self.cfg.symbols
        self.gaps = GapTracker(int(self.cfg.gap_s * 1000))
        self.streams = {"depth": StreamState(), "trades": StreamState()}
        self._depth: dict[str, tuple[int, str]] = {}
        self._trades: dict[str, _TradeState] = defaultdict(_TradeState)
        self._backfill_q: asyncio.Queue | None = None
        self.limiter = WeightLimiter(self.cfg.rest_weight_per_min)
        self.running = False
        self.started_ms: int | None = None
        self.universe_at: int | None = None
        self.errors: deque = deque(maxlen=20)
        self._day = st.today_str(self.clock())
        self._init_counters()
        self._client: httpx.AsyncClient | None = None
        self._stream_tasks: list[asyncio.Task] = []
        self.last_retention: dict = {}
        self._files_info: tuple[int, int | None] | None = None

    def _scan_files(self) -> None:
        files = st.list_files(self.cfg.root) if self.cfg.root.exists() else []
        self._files_info = (len(files), files[0][0] if files else None)

    def _init_counters(self) -> None:
        self.rows_today: dict[tuple[str, str], int] = defaultdict(int)
        self.last_row: dict[tuple[str, str], int] = {}
        self.c = defaultdict(int)  # rest_books, backfilled, missed, refused_rows, ... (today)
        self.trade_gaps: dict[str, int] = defaultdict(int)

    # ---- day roll-over / resume from manifest
    def _roll_day(self, now: int) -> None:
        day = st.today_str(now)
        if day != self._day:
            self._day = day
            self._init_counters()
            self.gaps.reset_counts()
            self.budget.refused_rows = 0

    def _resume(self) -> None:
        now = self.clock()
        for k, n in self.manifest.rows_by_day(st.today_str(now)).items():
            self.rows_today[k] += n
        for (stream, sym), ts in self.manifest.last_ts([st.yesterday_str(now), st.today_str(now)]).items():
            self.last_row[(stream, sym)] = ts
            if stream == "book":
                self.gaps.seed(("book", sym), ts)

    # ---- row sinks
    def _book(self, sym: str, ts: int, fields: list) -> None:
        self.store.add("book", sym, ts, fields)
        self.gaps.observe(("book", sym), ts)
        self.rows_today[("book", sym)] += 1
        self.last_row[("book", sym)] = ts

    def _trade_rows(self, sym: str, rows: list[tuple[int, list]]) -> None:
        stream = self.cfg.trade_stream
        for ts, fields in rows:
            self.store.add(stream, sym, ts, fields)
        if rows:
            self.rows_today[(stream, sym)] += len(rows)
            self.last_row[(stream, sym)] = max(self.last_row.get((stream, sym), 0), rows[-1][0])

    def on_trade(self, sym: str, a: int, T: int, p: float, q: float, m: bool, late: SecondAggregator | None = None
                 ) -> None:
        """Feed one aggregate trade. Live trades check id continuity; `late` routes REST-backfilled trades."""
        if late is not None:
            if self.cfg.trade_mode == "raw":
                self._trade_rows(sym, [(T, [a, _g(p), _g(q), int(m)])])
            else:
                late.add(T, a, p, q, m)
            return
        s = self._trades[sym]
        if s.last_id is not None:
            if a <= s.last_id:
                return  # duplicate / replay
            if a > s.last_id + 1:
                self._schedule_backfill(sym, s.last_id + 1, a - 1)
        s.last_id, s.last_ts = a, T
        if self.cfg.trade_mode == "raw":
            self._trade_rows(sym, [(T, [a, _g(p), _g(q), int(m)])])
        else:
            s.agg.add(T, a, p, q, m)

    def _schedule_backfill(self, sym: str, lo: int, hi: int) -> None:
        if self._backfill_q is not None:
            self._backfill_q.put_nowait((sym, lo, hi))
        else:  # not running (tests): count as missed
            self._missed(sym, hi - lo + 1)

    def _missed(self, sym: str, n: int) -> None:
        if n <= 0:
            return
        self.c["trades_missed"] += n
        self.trade_gaps[sym] += 1
        log.warning("lob_trades_missed", symbol=sym, trades=n)

    def finalize_seconds(self, now: int, grace_ms: int = 3000) -> None:
        if self.cfg.trade_mode == "raw":
            return
        cut = (now - grace_ms) // 1000 * 1000
        for sym, s in self._trades.items():
            self._trade_rows(sym, s.agg.pop_before(cut))

    def on_depth_message(self, msg: str, recv_ms: int) -> None:
        # parse lazily: only the symbol is read here (Binance sends '{"stream":"btcusdt@depth20@100ms","data":...')
        i = msg.find('"stream"')
        i = msg.find('"', msg.find(":", i) + 1) + 1 if i >= 0 else 0
        j = msg.find("@", i)
        if i <= 0 or j < 0:
            return
        self._depth[msg[i:j].upper()] = (recv_ms, msg)

    def sample_books(self, now: int) -> list[str]:
        """Write the freshest websocket book per symbol; return the symbols that need a REST snapshot."""
        stale_ms = int(self.cfg.stale_s * 1000)
        need = []
        for sym in self.symbols:
            rec = self._depth.get(sym)
            if rec and now - rec[0] <= stale_ms:
                try:
                    ts, fields = depth_row(json.loads(rec[1])["data"], "w")
                except (ValueError, KeyError, TypeError) as e:
                    self._err(f"depth parse {sym}: {e}")
                    need.append(sym)
                    continue
                self._book(sym, ts, fields)
            else:
                need.append(sym)
        return need

    def flush(self, now: int | None = None, final: bool = False) -> dict:
        now = now or self.clock()
        if final:
            for sym, s in self._trades.items():
                self._trade_rows(sym, s.agg.pop_before(None))
        else:
            self.finalize_seconds(now)
        out = self.store.flush(now)
        if out["refused"]:
            self.c["refused_rows"] += out["refused"]
            log.error("lob_budget_refused", rows=out["refused"], reason=self.budget.reason)
        if not final:
            self.store.compact_closed(now)
        return out

    def _err(self, text: str) -> None:
        self.errors.append({"at": iso(self.clock()), "error": text[:300]})
        log.warning("lob_error", error=text[:300])

    # ---- status
    def status(self) -> dict:
        now = self.clock()
        self._roll_day(now)
        ts_stream = self.cfg.trade_stream
        per = {}
        for sym in sorted(set(self.symbols) | {k[1] for k in self.rows_today}):
            per[sym] = {
                "rows_today": {"book": self.rows_today.get(("book", sym), 0),
                               ts_stream: self.rows_today.get((ts_stream, sym), 0)},
                "last_book": iso(self.last_row.get(("book", sym))),
                "last_trade": iso(self.last_row.get((ts_stream, sym))),
                "book_gaps_today": self.gaps.count.get(("book", sym), 0),
                "trade_gaps_today": self.trade_gaps.get(sym, 0),
                "book_gap_open": ("book", sym) in self.gaps.open,
                "recording": sym in self.symbols,
            }
        if self._files_info is None:  # cached by the retention pass; a cold status() call scans once
            self._scan_files()
        n_files, oldest = self._files_info
        return {
            "running": self.running,
            "started_at": iso(self.started_ms),
            "symbols": list(self.symbols),
            "universe": f"top {self.cfg.top_n} USDT perps by 24h quote volume, refreshed daily" if self.auto_universe
            else "explicit list",
            "universe_refreshed_at": iso(self.universe_at),
            "book_interval_s": self.cfg.book_interval_s,
            "depth_stream": f"@depth20{'@' + self.cfg.depth_speed if self.cfg.depth_speed else ''}",
            "trade_stream": ts_stream,
            "streams": {k: {"connected": v.connected, "reconnects": v.reconnects, "messages": v.messages,
                            "last_message": iso(v.last_msg_ms), "last_error": v.last_error}
                        for k, v in self.streams.items()},
            "rows_today": sum(self.rows_today.values()),
            "gaps_today": sum(self.gaps.count.values()) + sum(self.trade_gaps.values()),
            "rest_book_snapshots_today": self.c["rest_books"],
            "trades_backfilled_today": self.c["trades_backfilled"],
            "trades_missed_today": self.c["trades_missed"],
            "last_flush": iso(self.store.last_flush_ms),
            "pending_rows": self.store.pending_rows(),
            "per_symbol": per,
            "disk": {
                "used_bytes": self.budget.used_bytes,
                "cap_bytes": self.cfg.cap_bytes,
                "used_pct": round(100 * self.budget.used_bytes / self.cfg.cap_bytes, 2) if self.cfg.cap_bytes else None,
                "machine_free_bytes": self.budget.free_bytes,
                "refusing_writes": self.budget.refusing,
                "refusal_reason": self.budget.reason,
                "refused_rows_today": self.budget.refused_rows,
                "max_age_days": self.cfg.max_age_days,
                "evict_to_fit": self.cfg.evict_to_fit,
                "files": n_files,
                "oldest_hour": iso(oldest),
                "last_retention": self.last_retention,
            },
            "recent_gaps": [{"key": "/".join(e["key"]), "from": iso(e["from"]), "to": iso(e["to"]),
                             "seconds": round(e["ms"] / 1000, 1)} for e in list(self.gaps.events)[-10:]],
            "recent_errors": list(self.errors)[-10:],
        }

    # ---- network
    async def _get(self, path: str, params: dict, weight: int):
        await self.limiter.acquire(weight)
        r = await self._client.get(self.cfg.rest + path, params=params)
        if r.status_code in (418, 429):
            wait = float(r.headers.get("Retry-After") or 60)
            self.limiter.block(wait)
            raise RuntimeError(f"Binance {r.status_code}, backing off {wait:.0f}s")
        r.raise_for_status()
        used = r.headers.get("x-mbx-used-weight-1m")
        if used and int(used) > 1800:  # the IP is near Binance's 2400/min: pause until the window turns
            self.limiter.block(60 - time.time() % 60 + 1)
        return r.json()

    async def refresh_universe(self) -> bool:
        info, tick = await asyncio.gather(self._get("/fapi/v1/exchangeInfo", {}, 1),
                                          self._get("/fapi/v1/ticker/24hr", {}, 40))
        new = select_universe(tick, info, self.cfg.top_n, self.symbols)
        self.universe_at = self.clock()
        if new != self.symbols:
            log.info("lob_universe", added=sorted(set(new) - set(self.symbols)),
                     removed=sorted(set(self.symbols) - set(new)), symbols=new)
            self.symbols = new
            return True
        return False

    async def _ws_loop(self, kind: str) -> None:
        state = self.streams[kind]
        attempt = 0
        while True:
            syms = [s.lower() for s in self.symbols]
            if kind == "depth":
                suffix = f"@depth20@{self.cfg.depth_speed}" if self.cfg.depth_speed else "@depth20"
                url = self.cfg.public_ws + "?streams=" + "/".join(s + suffix for s in syms)
            else:
                url = self.cfg.market_ws + "?streams=" + "/".join(s + "@aggTrade" for s in syms)
            t0 = self.clock()
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, open_timeout=15,
                                              close_timeout=5, max_size=2**22) as ws:
                    state.connected, state.connected_since_ms = True, self.clock()
                    log.info("lob_ws_connected", stream=kind, symbols=len(syms))
                    quiet = 30 if kind == "depth" else 120  # depth pushes every 100 ms; trades can pause
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=quiet)
                        now = self.clock()
                        state.messages += 1
                        state.last_msg_ms = now
                        if kind == "depth":
                            self.on_depth_message(msg, now)
                        else:
                            d = json.loads(msg).get("data") or {}
                            if d.get("e") == "aggTrade":
                                self.on_trade(d["s"], int(d["a"]), int(d["T"]), float(d["p"]), float(d["q"]),
                                              bool(d["m"]))
            except asyncio.CancelledError:
                state.connected = False
                raise
            except Exception as e:  # noqa: BLE001 - reconnect on anything
                state.last_error = f"{type(e).__name__}: {e}"[:300]
                log.warning("lob_ws_dropped", stream=kind, error=state.last_error)
            state.connected = False
            state.reconnects += 1
            attempt = 1 if self.clock() - t0 > 60_000 else attempt + 1
            await asyncio.sleep(min(60.0, 2 ** min(attempt - 1, 6)) + random.random())

    async def _rest_book(self, sym: str) -> None:
        try:
            d = await self._get("/fapi/v1/depth", {"symbol": sym, "limit": 20}, 2)
            ts, fields = depth_row(d, "r")
            self._book(sym, ts, fields)
            self.c["rest_books"] += 1
        except Exception as e:  # noqa: BLE001
            self._err(f"rest depth {sym}: {type(e).__name__}: {e}")

    async def _sampler_loop(self) -> None:
        iv = self.cfg.book_interval_s
        while True:
            await asyncio.sleep(iv - (time.time() % iv) + 0.05)
            now = self.clock()
            self._roll_day(now)
            need = self.sample_books(now)
            if need and self.started_ms and now - self.started_ms > 15_000:  # give the socket time on startup
                await asyncio.wait([asyncio.create_task(self._rest_book(s)) for s in need], timeout=iv * 0.8)
            now = self.clock()
            for sym in self.symbols:
                self.gaps.check(("book", sym), now)

    async def _backfill_loop(self) -> None:
        while True:
            sym, lo, hi = await self._backfill_q.get()
            want = hi - lo + 1
            if want > self.cfg.max_backfill_trades:  # keep the most recent stretch, count the rest as missed
                self._missed(sym, want - self.cfg.max_backfill_trades)
                lo = hi - self.cfg.max_backfill_trades + 1
                want = self.cfg.max_backfill_trades
            late = SecondAggregator()
            got, cur = 0, lo
            try:
                while cur <= hi:
                    rows = await self._get("/fapi/v1/aggTrades", {"symbol": sym, "fromId": cur, "limit": 1000}, 20)
                    if not rows:
                        break
                    for t in rows:
                        a = int(t["a"])
                        if lo <= a <= hi:
                            self.on_trade(sym, a, int(t["T"]), float(t["p"]), float(t["q"]), bool(t["m"]), late=late)
                            got += 1
                    nxt = int(rows[-1]["a"]) + 1
                    if nxt <= cur:
                        break
                    cur = nxt
            except Exception as e:  # noqa: BLE001
                self._err(f"backfill {sym}: {type(e).__name__}: {e}")
            self._trade_rows(sym, late.pop_before(None))
            self.c["trades_backfilled"] += got
            self._missed(sym, want - got)
            if got:
                log.info("lob_backfilled", symbol=sym, trades=got, wanted=want)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.flush_interval_s - (time.time() % self.cfg.flush_interval_s) + 1.5)
            try:
                self._roll_day(self.clock())
                self.flush()
            except Exception as e:  # noqa: BLE001
                self._err(f"flush: {type(e).__name__}: {e}")

    async def _retention_loop(self) -> None:
        while True:
            try:
                deleted = await asyncio.to_thread(
                    st.apply_retention, self.cfg.root, self.clock(), self.cfg.max_age_days, self.cfg.cap_bytes,
                    self.cfg.evict_to_fit, 0.9, 2 * st.HOUR_MS, self.manifest)
                await asyncio.to_thread(self.budget.refresh)
                await asyncio.to_thread(self._scan_files)
                self.last_retention = {"at": iso(self.clock()), "deleted_files": len(deleted)}
                if deleted:
                    log.info("lob_retention", deleted=len(deleted), oldest_deleted=deleted[0])
            except Exception as e:  # noqa: BLE001
                self._err(f"retention: {type(e).__name__}: {e}")
            await asyncio.sleep(self.cfg.retention_interval_s)

    def _start_streams(self) -> None:
        for t in self._stream_tasks:
            t.cancel()
        self._stream_tasks = [asyncio.create_task(self._ws_loop("depth"), name="lob-depth"),
                              asyncio.create_task(self._ws_loop("trades"), name="lob-trades")]

    async def _universe_loop(self) -> None:
        while True:
            # refresh shortly after the UTC day turns (24h volumes then cover the previous day)
            day = self.cfg.universe_refresh_s
            await asyncio.sleep(day - (time.time() % day) + 300 if day == 86_400 else day)
            try:
                if await self.refresh_universe():
                    self.flush()
                    self._depth = {k: v for k, v in self._depth.items() if k in self.symbols}
                    self._start_streams()
            except Exception as e:  # noqa: BLE001
                self._err(f"universe refresh: {type(e).__name__}: {e}")

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop_event = stop_event or asyncio.Event()
        self.cfg.root.mkdir(parents=True, exist_ok=True)
        self._backfill_q = asyncio.Queue()
        self.running, self.started_ms = True, self.clock()
        tasks: list[asyncio.Task] = []
        self._client = httpx.AsyncClient(timeout=15, headers={"User-Agent": "aitrade-lob-recorder"})
        try:
            await asyncio.to_thread(self.budget.refresh)
            self._resume()
            attempt = 0
            while self.auto_universe and not self.symbols:
                try:
                    await self.refresh_universe()
                except Exception as e:  # noqa: BLE001
                    attempt += 1
                    self._err(f"universe: {type(e).__name__}: {e}")
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=min(300, 5 * 2**attempt))
                        return
                    except TimeoutError:
                        continue
            log.info("lob_recorder_start", symbols=self.symbols, root=str(self.cfg.root),
                     trade_stream=self.cfg.trade_stream, cap_gb=self.cfg.cap_bytes / 1e9)
            self._start_streams()
            tasks = [asyncio.create_task(c) for c in (self._sampler_loop(), self._flush_loop(),
                                                      self._retention_loop(), self._backfill_loop())]
            if self.auto_universe:
                tasks.append(asyncio.create_task(self._universe_loop()))
            await stop_event.wait()
        finally:
            for t in tasks + self._stream_tasks:
                t.cancel()
            await asyncio.gather(*tasks, *self._stream_tasks, return_exceptions=True)
            self._stream_tasks = []
            try:
                self.flush(final=True)
            except Exception as e:  # noqa: BLE001
                log.warning("lob_final_flush_failed", error=str(e))
            await self._client.aclose()
            self.running = False
            log.info("lob_recorder_stop", rows=self.store.rows_written, bytes=self.store.bytes_written)
