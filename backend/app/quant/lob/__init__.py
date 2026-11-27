"""Order-book and trade recorder for Binance USDⓈ-M perps (TECH_PLAN layer 1.2) and the order-flow features built on it.

    await run_recorder(stop_event)          # runs until stop_event is set (or the task is cancelled)
    status() -> dict                         # health for the API
    load("BTCUSDT", "book", start, end)      # stored rows as a DataFrame ("book", "trades_1s" or "trades_raw")
    features.minute_features(book, trades)   # per-minute OFI, depth imbalance, microprice, spread, signed volume
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd

from app.quant.lob import features, storage
from app.quant.lob.recorder import DEFAULT_ROOT, LobConfig, Recorder

__all__ = ["DEFAULT_ROOT", "LobConfig", "Recorder", "features", "load", "recorder", "run_recorder", "status"]

_recorder: Recorder | None = None


def recorder() -> Recorder | None:
    return _recorder


async def run_recorder(stop_event: asyncio.Event | None = None, symbols: list[str] | None = None,
                       config: LobConfig | None = None) -> None:
    """Record until `stop_event` is set. `symbols=None` records the auto universe (top 20 perps by 24h quote volume,
    refreshed daily). Settings come from `config`, else from AIT_LOB_* environment variables, else the defaults."""
    global _recorder
    cfg = config or LobConfig.from_env()
    if symbols:
        cfg.symbols = [s.upper() for s in symbols]
    _recorder = Recorder(cfg)
    await _recorder.run(stop_event)


def status() -> dict:
    """Live status when the recorder runs in this process; otherwise what is on disk."""
    if _recorder is not None:
        return _recorder.status()
    r = Recorder(LobConfig.from_env())
    if r.cfg.root.exists():
        r.budget.refresh()
        r._resume()
    return r.status()


def load(symbol: str, stream: str, start, end, root: Path | str | None = None) -> pd.DataFrame:
    """Stored rows for one symbol and stream with start <= ts < end (epoch ms, datetime, Timestamp or ISO string,
    UTC). Streams: "book" (ts, u, src, bp1..20, bq1..20, ap1..20, aq1..20), "trades_1s" (ts, n, n_buy, buy_qty,
    sell_qty, vwap, high, low, last, a_last) or "trades_raw" (ts, a, price, qty,
    is_buyer_maker). A `time` column (UTC datetime) is added after `ts`."""
    if root is None:
        root = _recorder.cfg.root if _recorder is not None else LobConfig.from_env().root
    return storage.load(symbol, stream, start, end, Path(root))

