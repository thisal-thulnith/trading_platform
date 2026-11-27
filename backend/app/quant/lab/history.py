"""Deep history from the Binance public archive, stored as compact hourly data (resumable, disk-guarded).

    uv run python -m app.quant.lab.history klines      # 1h candles: every panel coin (spot) + every perp, since 2019
    uv run python -m app.quant.lab.history metrics     # perps ever liquid: open interest, long/short ratios, taker flow
    uv run python -m app.quant.lab.history depth       # top-30 perps since 2023: order-book depth at ±1..5%, hourly
    uv run python -m app.quant.lab.history all

Raw archive files are parsed in memory and only hourly rows are kept (data/quant_lab/hist/<kind>/<SYMBOL>.csv.gz).
Progress is per symbol and month/day in data/quant_lab/hist/state.json, so an interrupted run resumes. The run stops
before free disk drops below MIN_FREE_GB. Every trade / full order-book history is not downloaded: it is terabytes.
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import time
import zipfile

import numpy as np
import pandas as pd

from app.quant.lab.data import ARCHIVE, RAW_FUND, ROOT, START, Fetcher, perp_base

HIST = ROOT / "hist"
STATE = HIST / "state.json"
MIN_FREE_GB = 5.0
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
              "taker_buy_base", "taker_buy_quote"]


class DiskFull(RuntimeError):
    pass


def _free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(st: dict) -> None:
    HIST.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def _unzip_csv(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read(z.namelist()[0]).decode()


def _ms(x: pd.Series) -> pd.Series:
    t = pd.to_numeric(x, errors="coerce").astype("int64")
    return pd.Series(np.where(t > 10**14, t // 1000, t), index=x.index)  # microseconds from 2025-01 (spot)


def _hour_ms(col: pd.Series) -> pd.Series:
    """Datetime strings -> the hour's start in epoch milliseconds (independent of pandas' datetime resolution)."""
    t = pd.to_datetime(col, utc=True)
    return ((t - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(hours=1)) * 3_600_000


def parse_klines(content: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(_unzip_csv(content)), header=None)
    if not str(df.iloc[0, 0]).strip().isdigit():
        df = df.iloc[1:]
    df = df.iloc[:, :11]
    df.columns = KLINE_COLS
    out = pd.DataFrame({"time": _ms(df["open_time"])})
    for c in ("open", "high", "low", "close", "quote_volume", "taker_buy_quote", "trades"):
        out[c] = pd.to_numeric(df[c], errors="coerce").to_numpy()
    return out.dropna(subset=["close"])


def parse_metrics(content: bytes) -> pd.DataFrame:
    """5-minute futures metrics -> hourly: last open interest (contracts, USD), mean long/short ratios, taker ratio."""
    df = pd.read_csv(io.StringIO(_unzip_csv(content)))
    if df.empty:
        return df
    df = df.assign(time=_hour_ms(df["create_time"]))
    num = ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
           "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
    for c in num:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    g = df.groupby("time")
    return pd.DataFrame({
        "oi": g["sum_open_interest"].last(), "oi_usd": g["sum_open_interest_value"].last(),
        "top_ls_accounts": g["count_toptrader_long_short_ratio"].mean(), "top_ls_positions": g["sum_toptrader_long_short_ratio"].mean(),
        "ls_accounts": g["count_long_short_ratio"].mean(), "taker_ls": g["sum_taker_long_short_vol_ratio"].mean(),
    }).reset_index()


def parse_depth(content: bytes) -> pd.DataFrame:
    """bookDepth snapshots (cumulative notional within ±x% of mid) -> hourly means per band, plus imbalance at 1%/5%."""
    df = pd.read_csv(io.StringIO(_unzip_csv(content)))
    if df.empty:
        return df
    df = df.assign(time=_hour_ms(df["timestamp"]), notional=pd.to_numeric(df["notional"], errors="coerce"))
    w = df.pivot_table(index="time", columns="percentage", values="notional", aggfunc="mean")
    w.columns = [f"n{'m' if c < 0 else 'p'}{abs(c):g}" for c in w.columns]
    for k in ("1", "5"):
        b, a = w.get(f"nm{k}"), w.get(f"np{k}")
        if b is not None and a is not None:
            w[f"imb{k}"] = (b - a) / (b + a)
    return w.reset_index()


def _append(path, new: list[pd.DataFrame]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(path) if path.exists() else None
    df = pd.concat(([old] if old is not None else []) + new, ignore_index=True)
    df = df.drop_duplicates("time", keep="last").sort_values("time")
    df.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.8g")
    return len(df)


async def _months(f: Fetcher, prefix: str) -> list[str]:
    keys = await f.s3_list(prefix, dirs=False)
    return sorted(k.rsplit("/", 1)[1] for k in keys if k.endswith(".zip"))


async def _job_symbol(f: Fetcher, st: dict, kind: str, sym: str, prefix: str, parse, since: str = "0000") -> None:
    """Fetch every archive file under `prefix` not yet done for (kind, sym), parse and append."""
    done = set(st.setdefault(kind, {}).get(sym, []))
    files = [n for n in await _months(f, prefix) if n.split("-", 2)[-1][:7] >= since[:7] and n not in done]
    for i in range(0, len(files), 31):
        if _free_gb() < MIN_FREE_GB:
            raise DiskFull(f"free disk below {MIN_FREE_GB} GB")
        chunk = files[i:i + 31]
        got = await asyncio.gather(*[_safe(f, f"{ARCHIVE}/{prefix}{n}") for n in chunk])
        frames = []
        for n, r in zip(chunk, got, strict=True):
            if r is None:
                continue
            try:
                d = parse(r.content)
            except Exception:  # noqa: BLE001 - a malformed archive file is skipped and retried next run
                continue
            if len(d):
                frames.append(d)
            done.add(n)
        if frames:
            _append(HIST / kind / f"{sym}.csv.gz", frames)
        st[kind][sym] = sorted(done)


async def _safe(f: Fetcher, url: str):
    try:
        return await f.get(url)
    except RuntimeError:
        return None


async def _run(kind: str, jobs: list[tuple[str, str, object, str]]) -> None:
    st = _state()
    f = Fetcher(32)
    t0 = time.time()
    try:
        for i in range(0, len(jobs), 16):
            batch = jobs[i:i + 16]
            await asyncio.gather(*[_job_symbol(f, st, kind, s, p, parse, since) for s, p, parse, since in batch])
            _save_state(st)
            print(f"  {kind}: {min(i + 16, len(jobs))}/{len(jobs)} symbols, {f.n} requests, {time.time() - t0:.0f}s, "
                  f"{_free_gb():.1f} GB free", flush=True)
    finally:
        _save_state(st)
        await f.close()


def _panel_symbols() -> list[str]:
    from app.quant.lab.data import load_symbols

    s = load_symbols()
    col = "exchange_symbol" if "exchange_symbol" in s.columns else "symbol"
    return sorted(set(s[col].astype(str)) - {""})


def _perps() -> list[str]:
    return sorted(p.name[:-7] for p in RAW_FUND.glob("*.csv.gz"))


def _liquid_perps(top: int | None = None, days: int | None = None) -> list[str]:
    """Perps whose spot coin was ever in the tradeable universe; with `top`, the most liquid over the last `days`."""
    from app.quant.lab import data as qd
    from app.quant.lab.factors import adv, build_wide, universe

    w = build_wide(qd.load_panel())
    u = universe(w)
    ever = set(u.columns[u.any()])
    perps = [p for p in _perps() if perp_base(p) + "USDT" in ever]
    if top:
        a = adv(w).iloc[-(days or 365):].median()
        perps = sorted(perps, key=lambda p: -float(np.nan_to_num(a.get(perp_base(p) + "USDT", 0))))[:top]
    return perps


async def klines() -> None:
    since = START.isoformat()
    jobs = [(s, f"data/spot/monthly/klines/{s}/1h/", parse_klines, since) for s in _panel_symbols()]
    await _run("klines_spot", jobs)
    jobs = [(p, f"data/futures/um/monthly/klines/{p}/1h/", parse_klines, since) for p in _perps()]
    await _run("klines_perp", jobs)


async def metrics() -> None:
    await _run("metrics", [(p, f"data/futures/um/daily/metrics/{p}/", parse_metrics, "2020-09") for p in _liquid_perps()])


async def depth(top: int = 30) -> None:
    await _run("depth", [(p, f"data/futures/um/daily/bookDepth/{p}/", parse_depth, "2023-01") for p in _liquid_perps(top)])


def coverage() -> dict:
    out = {}
    for kind in ("klines_spot", "klines_perp", "metrics", "depth"):
        files = list((HIST / kind).glob("*.csv.gz"))
        out[kind] = {"symbols": len(files), "mb": round(sum(p.stat().st_size for p in files) / 1e6, 1)}
    out["free_gb"] = round(_free_gb(), 1)
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"klines": klines, "metrics": metrics, "depth": depth}
    for name in (steps if what == "all" else [what]):
        try:
            asyncio.run(steps[name]())
        except DiskFull as e:
            print(f"stopped: {e}", flush=True)
            break
    print(coverage(), flush=True)
