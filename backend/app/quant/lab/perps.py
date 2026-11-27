"""USDT-M perpetual daily candles since 2019 (Binance public archive), for measuring the spot–perp basis.

    uv run python -m app.quant.lab.perps          # download / update, then write data/quant_lab/perp_daily.csv.gz

Every perp that has funding history in the lab (data/quant_lab/raw/funding) gets its monthly 1d kline archives plus the
daily archives after the last published month. Prices are converted to spot units (a 1000PEPE contract is divided by
1000), so `close / spot close - 1` is the basis.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta

import pandas as pd

from app.quant.lab.data import (
    ARCHIVE,
    RAW_FUND,
    ROOT,
    START,
    Fetcher,
    _now,
    _read_raw,
    _shift_month,
    _write_raw,
    parse_kline_zip,
    perp_base,
)

RAW_PERP = ROOT / "raw" / "perp"
PERP_DAILY = ROOT / "perp_daily.csv.gz"
STATE = ROOT / "perp_manifest.json"


def multiplier(perp: str) -> float:
    base = perp[:-4]
    for p, m in (("1000000", 1e6), ("1000", 1e3), ("1M", 1e6)):
        if base.startswith(p) and len(base) > len(p) + 1:
            return m
    return 1.0


async def _get(f: Fetcher, url: str):
    """One archive file; a file that keeps failing is skipped (retried on the next update), not fatal."""
    try:
        return await f.get(url)
    except RuntimeError:
        return None


async def _one(f: Fetcher, perp: str, info: dict, alive_month: str, yesterday: date) -> dict:
    path = RAW_PERP / f"{perp}.csv.gz"
    have = set(info.get("months", []))
    if not have and path.exists():  # resume: months already stored from the monthly archive
        old = _read_raw(path)
        have = set(old.loc[old["src"] == "m", "date"].str[:7]) if old is not None and len(old) else set()
    if info.get("dead"):
        return info
    keys = await f.s3_list(f"data/futures/um/monthly/klines/{perp}/1d/", dirs=False)
    avail = sorted({k.rsplit("-1d-", 1)[1][:7] for k in keys if k.endswith(".zip")})
    want = [m for m in avail if m >= START.strftime("%Y-%m") and m not in have]
    got = await asyncio.gather(*[_get(f, f"{ARCHIVE}/data/futures/um/monthly/klines/{perp}/1d/{perp}-1d-{m}.zip") for m in want])
    frames = []
    for m, r in zip(want, got, strict=True):
        if r is not None:
            d = parse_kline_zip(r.content)
            d["src"] = "m"
            frames.append(d)
            have.add(m)
    latest = max(have) if have else None
    old = _read_raw(path)
    if latest is not None and latest >= alive_month:  # daily files after the last monthly one
        start = (date.fromisoformat(f"{latest}-01").replace(day=28) + timedelta(days=4)).replace(day=1)
        if old is not None and len(old) and date.fromisoformat(old["date"].max()) >= start:
            start = date.fromisoformat(old["date"].max()) + timedelta(days=1)
        days = [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]
        got = await asyncio.gather(*[_get(f, f"{ARCHIVE}/data/futures/um/daily/klines/{perp}/1d/{perp}-1d-{d}.zip") for d in days])
        for r in got:
            if r is not None:
                d = parse_kline_zip(r.content)
                d["src"] = "d"
                frames.append(d)
    if frames:
        df = pd.concat(([old] if old is not None else []) + frames, ignore_index=True)
        df["_p"] = df["src"].map({"m": 0, "d": 1}).fillna(2)
        df = df.sort_values(["date", "_p"]).drop_duplicates("date", keep="first").drop(columns="_p")
        _write_raw(path, df.reset_index(drop=True))
    rows = len(_read_raw(path)) if path.exists() else 0
    return {"months": sorted(have), "rows": rows, "dead": bool(latest is None or latest < _shift_month(alive_month, -1)),
            "checked": _now().isoformat()}


async def update(perps: list[str] | None = None) -> dict:
    t0 = time.time()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    perps = perps or sorted(p.name[:-7] for p in RAW_FUND.glob("*.csv.gz"))
    today = _now().date()
    last_complete = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    alive_month = _shift_month(last_complete, -1)
    f = Fetcher()
    try:
        for i in range(0, len(perps), 48):
            res = await asyncio.gather(*[_one(f, p, state.get(p, {}), alive_month, today - timedelta(days=1)) for p in perps[i:i + 48]])
            for p, r in zip(perps[i:i + 48], res, strict=True):
                state[p] = r
            print(f"  perps {min(i + 48, len(perps))}/{len(perps)}, {f.n} requests, {time.time() - t0:.0f}s", flush=True)
    finally:
        await f.close()
    STATE.write_text(json.dumps(state))
    out = build()
    print(f"perp_daily: {len(out)} rows, {out['perp'].nunique()} perps, {time.time() - t0:.0f}s", flush=True)
    return {"rows": len(out), "perps": int(out["perp"].nunique())}


def build() -> pd.DataFrame:
    frames = []
    for path in sorted(RAW_PERP.glob("*.csv.gz")):
        perp = path.name[:-7]
        df = pd.read_csv(path, dtype={"date": str})
        if df.empty:
            continue
        k = multiplier(perp)
        out = pd.DataFrame({"date": df["date"], "symbol": perp_base(perp) + "USDT", "perp": perp})
        for c in ("open", "high", "low", "close"):
            out[c] = df[c] / k
        out["quote_volume"] = df["quote_volume"]
        frames.append(out)
    pdf = pd.concat(frames, ignore_index=True)
    # two contracts on one coin (X and 1000X): keep the more liquid one each day
    pdf = pdf.sort_values(["date", "symbol", "quote_volume"]).drop_duplicates(["date", "symbol"], keep="last")
    pdf = pdf.sort_values(["symbol", "date"]).reset_index(drop=True)
    pdf.to_csv(PERP_DAILY, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.10g")
    return pdf


def load() -> pd.DataFrame:
    return pd.read_csv(PERP_DAILY, dtype={"date": str})


if __name__ == "__main__":
    asyncio.run(update())
