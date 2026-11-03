"""Long public price history for research (Binance spot klines, no API key).

    uv run python -m app.maintenance.history --tf 4h --since 2020-01-01

Saved as one CSV per symbol in data/history/{tf}/. Research only: live trading reads the candles table.
Survivorship note: the list holds coins that are still large today, which flatters long-only results.
"""
import argparse
import asyncio
from pathlib import Path

import httpx
import pandas as pd

BASE = "https://data-api.binance.vision/api/v3/klines"
OUT = Path("data/history")
COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "ATOM",
         "NEAR", "ETC", "FIL", "UNI", "AAVE", "XLM", "ALGO", "VET", "ICP", "SAND", "MANA", "AXS", "EOS", "XTZ",
         "THETA", "HBAR", "INJ", "SUI", "APT", "ARB", "OP"]
TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


async def fetch(client: httpx.AsyncClient, sym: str, tf: str, start_ms: int) -> pd.DataFrame:
    rows, t = [], start_ms
    while True:
        r = await client.get(BASE, params={"symbol": sym, "interval": tf, "startTime": t, "limit": 1000})
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        rows += k
        t = k[-1][0] + TF_MS[tf]
        if len(k) < 1000:
            break
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n",
                                     "tb", "tq", "x"])[["open_time", "open", "high", "low", "close", "volume", "qv"]]
    df = df.astype(float).astype({"open_time": "int64"})
    now = pd.Timestamp.now("UTC").value // 1_000_000
    return df[df.open_time + TF_MS[tf] <= now].drop_duplicates("open_time")  # closed candles only


async def main(tf: str, since: str) -> None:
    (OUT / tf).mkdir(parents=True, exist_ok=True)
    start = int(pd.Timestamp(since, tz="UTC").value // 1_000_000)
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=30) as client:
        async def one(c: str) -> None:
            async with sem:
                try:
                    df = await fetch(client, f"{c}USDT", tf, start)
                except httpx.HTTPError as e:
                    print(f"  {c}: failed {e}")
                    return
                df.to_csv(OUT / tf / f"{c}USDT.csv", index=False)
                print(f"  {c}: {len(df)} bars from {pd.to_datetime(df.open_time.iloc[0], unit='ms').date()}", flush=True)
        await asyncio.gather(*(one(c) for c in COINS))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--since", default="2020-01-01")
    a = ap.parse_args()
    asyncio.run(main(a.tf, a.since))
