"""Public perpetual funding-rate history (Binance USD-M, no key) for the carry study.

    uv run python -m app.maintenance.funding_history
Saved to data/history/funding/{SYMBOL}.csv (time, rate per 8 hours).
"""
import asyncio
from pathlib import Path

import httpx
import pandas as pd

from app.maintenance.history import COINS

URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OUT = Path("data/history/funding")


async def fetch(client: httpx.AsyncClient, sym: str, start_ms: int) -> pd.DataFrame:
    rows, t = [], start_ms
    while True:
        r = await client.get(URL, params={"symbol": sym, "startTime": t, "limit": 1000})
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        rows += k
        t = k[-1]["fundingTime"] + 1
        if len(k) < 1000:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return pd.DataFrame({"time": df.fundingTime.astype("int64"), "rate": df.fundingRate.astype(float)})


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    start = int(pd.Timestamp("2020-01-01", tz="UTC").value // 1_000_000)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=30) as client:
        async def one(c: str) -> None:
            async with sem:
                try:
                    df = await fetch(client, f"{c}USDT", start)
                except httpx.HTTPError as e:
                    print(f"  {c}: failed {e}")
                    return
                if len(df):
                    df.to_csv(OUT / f"{c}USDT.csv", index=False)
                print(f"  {c}: {len(df)} funding prints", flush=True)
        await asyncio.gather(*(one(c) for c in COINS))


if __name__ == "__main__":
    asyncio.run(main())
