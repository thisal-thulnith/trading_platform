"""Candle persistence: idempotent upserts, gap detection, DataFrame loading."""
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.timeframes import tf_ms
from app.models import Candle

CANDLE_COLS = ["open_time", "open", "high", "low", "close", "volume", "quote_volume", "trades",
               "taker_buy_base", "taker_buy_quote"]


def validate_candle(c: dict) -> str | None:
    """Return a reason string if a candle is malformed."""
    o, h, low, cl, v = c["open"], c["high"], c["low"], c["close"], c["volume"]
    if min(o, h, low, cl) <= 0:
        return "non-positive price"
    if h < max(o, cl) - 1e-12 or low > min(o, cl) + 1e-12 or h < low:
        return "inconsistent OHLC"
    if v < 0:
        return "negative volume"
    return None


async def upsert_candles(
    s: AsyncSession, symbol: str, market: str, tf: str, rows: list[dict], source: str = "binance"
) -> int:
    clean = []
    seen: set[int] = set()
    for r in rows:
        if r["open_time"] in seen or validate_candle(r):
            continue
        seen.add(r["open_time"])
        clean.append({**{k: r[k] for k in CANDLE_COLS}, "symbol": symbol, "market": market,
                      "timeframe": tf, "source": source})
    if not clean:
        return 0
    dialect = s.bind.dialect.name if s.bind else "sqlite"
    ins = pg_insert if dialect == "postgresql" else sqlite_insert
    for i in range(0, len(clean), 500):
        chunk = clean[i : i + 500]
        stmt = ins(Candle).values(chunk)
        upd = {k: getattr(stmt.excluded, k) for k in CANDLE_COLS[1:] + ["source"]}
        stmt = stmt.on_conflict_do_update(index_elements=["symbol", "market", "timeframe", "open_time"], set_=upd)
        await s.execute(stmt)
    await s.commit()
    return len(clean)


async def load_candles(
    s: AsyncSession, symbol: str, tf: str, market: str = "spot", limit: int = 1000,
    end_ms: int | None = None, start_ms: int | None = None,
) -> pd.DataFrame:
    q = select(Candle).where(Candle.symbol == symbol, Candle.market == market, Candle.timeframe == tf)
    if end_ms is not None:
        q = q.where(Candle.open_time <= end_ms)
    if start_ms is not None:
        q = q.where(Candle.open_time >= start_ms)
    q = q.order_by(Candle.open_time.desc()).limit(limit)
    rows = (await s.execute(q)).scalars().all()
    if not rows:
        return pd.DataFrame(columns=CANDLE_COLS)
    df = pd.DataFrame([{k: getattr(r, k) for k in CANDLE_COLS} for r in reversed(rows)])
    return df.reset_index(drop=True)


async def earliest_open_time(s: AsyncSession, symbol: str, tf: str, market: str = "spot") -> int | None:
    q = select(func.min(Candle.open_time)).where(
        Candle.symbol == symbol, Candle.market == market, Candle.timeframe == tf
    )
    return (await s.execute(q)).scalar()


async def latest_open_time(s: AsyncSession, symbol: str, tf: str, market: str = "spot") -> int | None:
    q = select(func.max(Candle.open_time)).where(
        Candle.symbol == symbol, Candle.market == market, Candle.timeframe == tf
    )
    return (await s.execute(q)).scalar()


def find_gaps(open_times: list[int], tf: str) -> list[tuple[int, int]]:
    """Return (missing_from, missing_to) inclusive ranges of absent candle open times."""
    step = tf_ms(tf)
    gaps = []
    for a, b in zip(open_times, open_times[1:]):
        if b - a > step:
            gaps.append((a + step, b - step))
    return gaps


def closed_only(df: pd.DataFrame, tf: str, now_ms: int) -> pd.DataFrame:
    """Drop the still-forming candle: analysis must only use closed bars to avoid repainting."""
    if df.empty:
        return df
    return df[df["open_time"] + tf_ms(tf) <= now_ms].reset_index(drop=True)
