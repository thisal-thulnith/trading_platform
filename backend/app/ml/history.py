"""Full-history panel for the forecast and pattern models: every Binance USDT spot coin since the archive starts
(August 2017), not just since 2019.

The quant lab's panel (app/quant/lab/data.py) starts on 2019-01-01 and feeds the lab, the simulator and the paper
books; it is left untouched. This module downloads the 2017-2018 monthly daily-candle files from the same public
archive (data.binance.vision) into their own store, then builds `panel_full.csv.gz` = early rows + the lab's raw rows,
cleaned the same way (excluded leveraged/stable tokens, redenominations, relisting episodes split, the open candle
dropped). The 2018 crash and bear market are then part of what the models learn from.

Run: uv run python -m app.ml.history          (a few minutes; only missing months are downloaded)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

from app.quant.lab import data as lab

EARLY_START = date(2017, 8, 1)
RAW_EARLY = lab.ROOT / "raw_early" / "spot"
RAW_EARLY_BTC = lab.ROOT / "raw_early" / "btc_pairs"   # ALT/BTC converted to USD with that day's BTC/USDT close
EARLY_MANIFEST = lab.ROOT / "early_manifest.json"
PANEL_FULL = lab.ROOT / "panel_full.csv.gz"


def _months_wanted(avail: list[str]) -> list[str]:
    lo, hi = EARLY_START.strftime("%Y-%m"), lab.START.strftime("%Y-%m")
    return [m for m in avail if lo <= m < hi]


async def download() -> dict:
    """Fetch the monthly 1d klines of every eligible USDT pair for Aug 2017 - Dec 2018 (missing months only)."""
    man = json.loads(EARLY_MANIFEST.read_text()) if EARLY_MANIFEST.exists() else {"symbols": {}}
    f = lab.Fetcher()
    t0 = time.time()
    try:
        prefixes = await f.s3_list("data/spot/monthly/klines/", dirs=True)
        syms = sorted({p.rstrip("/").rsplit("/", 1)[1] for p in prefixes})
        eligible = [s for s in syms if s.endswith("USDT") and not lab.exclusion_reason(s)]
        print(f"early history: {len(eligible)} eligible USDT pairs", flush=True)
        done = 0

        async def one(sym: str) -> None:
            nonlocal done
            info = man["symbols"].get(sym, {})
            if info.get("checked") and not info.get("months_missing"):
                done += 1
                return
            keys = await f.s3_list(f"data/spot/monthly/klines/{sym}/1d/", dirs=False)
            avail = sorted({k.rsplit("-1d-", 1)[1][:7] for k in keys if k.endswith(".zip")})
            want = _months_wanted(avail)
            have = set(info.get("months", []))
            todo = [m for m in want if m not in have]
            frames = []
            got = await asyncio.gather(*[f.get(f"{lab.ARCHIVE}/data/spot/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
                                         for m in todo])
            for m, r in zip(todo, got):
                if r is not None:
                    d = lab.parse_kline_zip(r.content)
                    d["src"] = "m"
                    frames.append(d)
                    have.add(m)
            if frames:
                path = RAW_EARLY / f"{sym}.csv.gz"
                old = lab._read_raw(path)
                df = pd.concat(([old] if old is not None else []) + frames, ignore_index=True)
                df = df.sort_values("date").drop_duplicates("date", keep="first")
                df = df[df["date"] < lab.START.isoformat()].reset_index(drop=True)
                lab._write_raw(path, df)
            man["symbols"][sym] = {"months": sorted(have), "months_missing": sorted(set(want) - have),
                                   "checked": pd.Timestamp.now("UTC").isoformat()}
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(eligible)} symbols, {f.n} requests, {time.time() - t0:.0f}s", flush=True)

        for i in range(0, len(eligible), 64):
            await asyncio.gather(*[one(s) for s in eligible[i:i + 64]])
    finally:
        await f.close()
    with_early = sum(1 for v in man["symbols"].values() if v.get("months"))
    man["downloaded_at"] = pd.Timestamp.now("UTC").isoformat()
    EARLY_MANIFEST.write_text(json.dumps(man))
    print(f"early history: {with_early} symbols with 2017-18 data, {f.n} requests, {time.time() - t0:.0f}s", flush=True)
    return {"symbols_with_early_data": with_early, "seconds": round(time.time() - t0, 1)}


async def download_btc_pairs() -> dict:
    """Most altcoins traded only against BTC in 2017-2018. Fetch those pairs' monthly 1d klines and convert them to USD
    with the day's BTC/USDT close (prices and quote volumes), so the 2018 bear market is in the training data. Rows of a
    real USDT pair win over converted ones for the same day (see build)."""
    man = json.loads(EARLY_MANIFEST.read_text()) if EARLY_MANIFEST.exists() else {"symbols": {}}
    btc_done = man.setdefault("btc_pairs", {})
    btc = lab._read_raw(RAW_EARLY / "BTCUSDT.csv.gz")
    if btc is None:
        raise RuntimeError("download() first: BTCUSDT 2017-18 is needed to convert BTC pairs")
    btc_close = btc.set_index("date")["close"]
    f = lab.Fetcher()
    t0 = time.time()
    n_rows = 0
    try:
        prefixes = await f.s3_list("data/spot/monthly/klines/", dirs=True)
        syms = sorted({p.rstrip("/").rsplit("/", 1)[1] for p in prefixes})
        pairs = [s for s in syms if s.endswith("BTC") and len(s) > 3 and not lab.exclusion_reason(s[:-3] + "USDT")]
        print(f"btc pairs: {len(pairs)} ALT/BTC symbols", flush=True)

        async def one(sym: str) -> int:
            if sym in btc_done:
                return 0
            keys = await f.s3_list(f"data/spot/monthly/klines/{sym}/1d/", dirs=False)
            avail = sorted({k.rsplit("-1d-", 1)[1][:7] for k in keys if k.endswith(".zip")})
            want = _months_wanted(avail)
            got = await asyncio.gather(*[f.get(f"{lab.ARCHIVE}/data/spot/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
                                         for m in want])
            frames = [lab.parse_kline_zip(r.content) for r in got if r is not None]
            btc_done[sym] = len(frames)
            if not frames:
                return 0
            df = pd.concat(frames, ignore_index=True).sort_values("date").drop_duplicates("date")
            px = df["date"].map(btc_close)
            df = df[px.notna()].copy()
            px = px[px.notna()]
            for c in ("open", "high", "low", "close", "quote_volume", "taker_buy_quote"):
                df[c] = df[c] * px
            df["src"] = "b"
            df = df[df["date"] < lab.START.isoformat()]
            if len(df):
                lab._write_raw(RAW_EARLY_BTC / f"{sym[:-3]}USDT.csv.gz", df.reset_index(drop=True))
            return len(df)

        for i in range(0, len(pairs), 64):
            n_rows += sum(await asyncio.gather(*[one(s) for s in pairs[i:i + 64]]))
    finally:
        await f.close()
    EARLY_MANIFEST.write_text(json.dumps(man))
    out = {"btc_pairs_with_data": len(list(RAW_EARLY_BTC.glob("*.csv.gz"))), "rows": n_rows,
           "seconds": round(time.time() - t0, 1)}
    print(f"btc pairs: {out}", flush=True)
    return out


def build() -> dict:
    """panel_full = early rows + the lab's raw rows, cleaned like the lab panel."""
    t0 = time.time()
    frames = []
    adjustments: list = []
    today = pd.Timestamp.now("UTC").date().isoformat()
    names = sorted({p.name[:-7] for d in (lab.RAW_SPOT, RAW_EARLY, RAW_EARLY_BTC) for p in d.glob("*.csv.gz")})
    for sym in names:
        if lab.exclusion_reason(sym):
            continue
        # converted BTC-pair rows first, then real USDT rows: keep="last" lets a real USDT day win
        parts = [lab._read_raw(d / f"{sym}.csv.gz") for d in (RAW_EARLY_BTC, RAW_EARLY, lab.RAW_SPOT)]
        parts = [p for p in parts if p is not None and len(p)]
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True).drop_duplicates("date", keep="last")
        df = df[(df["date"] < today) & df["close"].gt(0)].sort_values("date").reset_index(drop=True)
        if df.empty or lab.is_tokenized_equity(sym, df["date"].iloc[0]):
            continue
        for c in lab.NUM_COLS:
            if c != "trades":
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        df = lab.adjust_redenominations(df, sym, adjustments)
        d = pd.to_datetime(df["date"])
        ep = (d.diff().dt.days.fillna(0) > lab.EPISODE_GAP_DAYS).cumsum()
        sym_names = np.array([sym] * len(df), dtype=object)
        for k in range(int(ep.max())):  # earlier episodes get a dated suffix; the latest keeps the exchange symbol
            m = (ep == k).to_numpy()
            sym_names[m] = f"{sym}~{d[m].iloc[0].strftime('%Y%m')}"
        df.insert(1, "symbol", sym_names)
        frames.append(df[lab.COLUMNS])
    panel = pd.concat(frames, ignore_index=True)
    panel["trades"] = panel["trades"].fillna(0).astype("int64")
    panel = panel.sort_values(["date", "symbol"]).reset_index(drop=True)
    panel.to_csv(PANEL_FULL, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.10g")
    out = {"rows": int(len(panel)), "symbols": int(panel["symbol"].nunique()), "first_date": panel["date"].min(),
           "last_date": panel["date"].max(), "rows_before_2019": int((panel["date"] < lab.START.isoformat()).sum()),
           "seconds": round(time.time() - t0, 1)}
    print(f"panel_full: {out}", flush=True)
    return out


def load_full() -> pd.DataFrame:
    """The full-history panel if it has been built, else the lab panel."""
    return lab.load_panel(PANEL_FULL) if PANEL_FULL.exists() else lab.load_panel()


def refresh() -> dict:
    """Rebuild panel_full from the stores (after the lab's daily update added a day). No downloads."""
    return build()


if __name__ == "__main__":
    if (sys.argv[1] if len(sys.argv) > 1 else "all") in ("all", "download"):
        print(asyncio.run(download()))
        print(asyncio.run(download_btc_pairs()))
    print(build())
