"""Phase 1 — point-in-time, survivorship-free daily panel of Binance spot USDT pairs (incl. delisted) + funding.

Sources (URL patterns verified against the live archive on 2026-11-11):
- symbol list:   https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/spot/monthly/klines/&delimiter=/
                 (S3 XML listing, 1000 prefixes per page, paged with ``marker``)
- monthly 1d:    https://data.binance.vision/data/spot/monthly/klines/{SYM}/1d/{SYM}-1d-{YYYY-MM}.zip
- daily 1d:      https://data.binance.vision/data/spot/daily/klines/{SYM}/1d/{SYM}-1d-{YYYY-MM-DD}.zip  (current month)
- funding:       https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YYYY-MM}.zip
                 (header ``calc_time,funding_interval_hours,last_funding_rate``); the current month comes from
                 https://fapi.binance.com/fapi/v1/fundingRate (no symbol, paged by startTime; there is no daily archive).
- validation:    https://api.binance.com/api/v3/klines

Kline CSVs have no header; open_time is in milliseconds until 2024-12 and in MICROseconds from 2025-01 (handled).
The archive keeps delisted pairs, so the panel is survivorship-free. A symbol that disappears for more than
``EPISODE_GAP_DAYS`` and comes back (e.g. LUNAUSDT: old Terra until 2022-05, new LUNA from 2022-05-31) is split into
episodes; earlier episodes are renamed ``SYM~YYYYMM`` so no return is ever computed across a relisting.

Files (data/quant_lab/): raw/spot/*.csv.gz, raw/funding/*.csv.gz, panel.csv.gz, funding_daily.csv.gz, symbols.csv,
manifest.json. pyarrow is not installed in this environment, so storage is gzip CSV.

CLI: ``uv run python -m app.quant.lab.data --update`` (incremental), ``--validate`` (live close check), ``--stats``.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import sys
import time
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] / "data" / "quant_lab"
RAW_SPOT = ROOT / "raw" / "spot"
RAW_FUND = ROOT / "raw" / "funding"
MANIFEST = ROOT / "manifest.json"
PANEL = ROOT / "panel.csv.gz"
FUNDING_DAILY = ROOT / "funding_daily.csv.gz"
SYMBOLS = ROOT / "symbols.csv"

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE = "https://data.binance.vision"
SPOT_API = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
START = date(2019, 1, 1)
EPISODE_GAP_DAYS = 7
CONCURRENCY = 48

COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "quote_volume", "trades",
           "taker_buy_base", "taker_buy_quote"]
NUM_COLS = COLUMNS[2:]

# --- exclusions -------------------------------------------------------------------------------------------------
# Stablecoins and fiat (incl. depegged ex-stables like UST/USTC — they are not investable risk assets by design).
STABLES = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "USDS", "USDSB", "SUSD", "UST", "USTC", "AEUR", "EURI",
    "USDE", "XUSD", "USD1", "BFUSD", "PYUSD", "RLUSD", "LUSD", "FRAX", "GUSD", "HUSD", "USDD", "TUSDB", "USDT", "U",
    "EUR", "GBP", "AUD", "TRY", "BRL", "RUB", "UAH", "NGN", "ZAR", "BIDR", "IDRT", "BKRW", "BVND", "VAI", "ARS",
    "PLN", "RON", "JPY", "MXN", "COP", "CZK",
}
# Wrapped / pegged / staked derivatives of another asset (their return is the underlying's, not a separate bet).
PEGGED = {"WBTC", "WBETH", "BETH", "BNSOL", "STETH", "WSTETH", "RETH", "CBETH", "PAXG", "XAUT", "WETH", "BTCB",
          "WNXM", "RENBTC", "HBTC", "BBTC"}
# Tokenized equities/ETFs: from 2026-06-11 Binance lists stock tokens as {TICKER}B (NVDAB, TSLAB, SPYB, QQQB, ...) in
# weekly batches. They track a stock, not a crypto asset. Rule: base ends in "B" and first trade on/after
# TOKENIZED_EQUITY_SINCE (checked when the panel is built, so future listings are caught too).
TOKENIZED_EQUITY_SINCE = "2026-06-01"
# Stablecoins found by the data-driven check (constant price) that are not in the list above.
STABLES |= {"KGST", "RWUSD"}
# Binance leveraged tokens: {UNDERLYING}{UP,DOWN,BULL,BEAR} (2019-2022) plus the original BULL/BEAR (BTC 3x).
LEVERAGED_UNDERLYINGS = {"BTC", "ETH", "BNB", "ADA", "XRP", "DOT", "LINK", "LTC", "EOS", "TRX", "XTZ", "YFI", "SUSHI",
                         "UNI", "FIL", "AAVE", "SXP", "BCH", "1INCH", "XLM"}
LEVERAGED = {u + s for u in LEVERAGED_UNDERLYINGS for s in ("UP", "DOWN", "BULL", "BEAR")} | {"BULL", "BEAR"}


def exclusion_reason(symbol: str) -> str | None:
    """None if the USDT pair is eligible, else why it is excluded."""
    if not symbol.endswith("USDT") or len(symbol) <= 4:
        return "not_usdt_quote"
    base = symbol[:-4]
    if base in STABLES:
        return "stablecoin_or_fiat"
    if base in PEGGED:
        return "wrapped_or_pegged"
    if base in LEVERAGED:
        return "leveraged_token"
    return None


def is_tokenized_equity(symbol: str, first_date: str) -> bool:
    base = symbol[:-4]
    return base.endswith("B") and len(base) >= 3 and first_date >= TOKENIZED_EQUITY_SINCE


def perp_base(perp: str) -> str:
    """USDT-M perp -> spot base (1000PEPEUSDT -> PEPE, 1000000MOGUSDT -> MOG, 1MBABYDOGEUSDT -> BABYDOGE)."""
    base = perp[:-4]
    for p in ("1000000", "1000", "1M"):
        if base.startswith(p) and len(base) > len(p) + 1:
            return base[len(p):]
    return base


# --- small helpers ----------------------------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _months(a: date, b: date) -> list[str]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"spot": {}, "funding": {}, "excluded": {}}


def save_manifest(m: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True, default=str))
    tmp.replace(MANIFEST)


def _to_dates(open_time: pd.Series) -> pd.Series:
    t = pd.to_numeric(open_time, errors="coerce").astype("int64")
    t = np.where(t > 10**14, t // 1000, t)  # microseconds from 2025-01
    return pd.to_datetime(t, unit="ms", utc=True).strftime("%Y-%m-%d")


def parse_kline_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw = z.read(z.namelist()[0]).decode()
    df = pd.read_csv(io.StringIO(raw), header=None)
    if not str(df.iloc[0, 0]).strip().lstrip("-").isdigit():  # some files carry a header row
        df = df.iloc[1:]
    df = df.iloc[:, :11]
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
                  "taker_buy_base", "taker_buy_quote"]
    out = pd.DataFrame({"date": _to_dates(df["open_time"])})
    for c in NUM_COLS:
        out[c] = pd.to_numeric(df[c], errors="coerce").to_numpy()
    return out


def parse_funding_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw = z.read(z.namelist()[0]).decode()
    df = pd.read_csv(io.StringIO(raw))
    if "calc_time" not in df.columns:  # headerless variant
        df = pd.read_csv(io.StringIO(raw), header=None, names=["calc_time", "funding_interval_hours",
                                                                "last_funding_rate"])
    return pd.DataFrame({"time_ms": pd.to_numeric(df["calc_time"], errors="coerce").astype("int64"),
                         "rate": pd.to_numeric(df["last_funding_rate"], errors="coerce")}).dropna()


# --- network ----------------------------------------------------------------------------------------------------
class Fetcher:
    def __init__(self, concurrency: int = CONCURRENCY) -> None:
        self.sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(timeout=30, follow_redirects=True,
                                        limits=httpx.Limits(max_connections=concurrency + 8))
        self.n = 0

    async def get(self, url: str, params: dict | None = None) -> httpx.Response | None:
        async with self.sem:
            for attempt in range(5):
                try:
                    r = await self.client.get(url, params=params)
                    self.n += 1
                    if r.status_code == 404:
                        return None
                    if r.status_code in (418, 429) or r.status_code >= 500:
                        await asyncio.sleep(2 ** attempt * (5 if r.status_code in (418, 429) else 1))
                        continue
                    r.raise_for_status()
                    return r
                except (httpx.TransportError, httpx.HTTPStatusError):
                    await asyncio.sleep(2 ** attempt)
            raise RuntimeError(f"failed after retries: {url} {params}")

    async def close(self) -> None:
        await self.client.aclose()

    async def s3_list(self, prefix: str, dirs: bool) -> list[str]:
        """List common prefixes (dirs=True) or keys under an S3 prefix, following pagination."""
        out, marker = [], ""
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        while True:
            params = {"prefix": prefix, "delimiter": "/"}
            if marker:
                params["marker"] = marker
            r = await self.get(S3, params)
            if r is None:
                return out
            root = ElementTree.fromstring(r.content)
            if dirs:
                items = [e.text for e in root.iter(f"{ns}Prefix") if e.text and e.text != prefix]
            else:
                items = [e.text for e in root.iter(f"{ns}Key") if e.text]
            out.extend(items)
            if (root.findtext(f"{ns}IsTruncated") or "false") != "true":
                return out
            marker = root.findtext(f"{ns}NextMarker") or (items[-1] if items else "")
            if not marker:
                return out


# --- spot -------------------------------------------------------------------------------------------------------
def _read_raw(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path, dtype={"date": str, "src": str}) if path.exists() else None


def _write_raw(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.10g")


def _merge_rows(old: pd.DataFrame | None, new: list[pd.DataFrame]) -> pd.DataFrame:
    """Monthly archive rows ('m') win over daily archive rows ('d') for the same date."""
    frames = ([old] if old is not None else []) + new
    df = pd.concat(frames, ignore_index=True)
    df["_p"] = df["src"].map({"m": 0, "d": 1}).fillna(2)
    df = df.sort_values(["date", "_p"]).drop_duplicates("date", keep="first").drop(columns="_p")
    return df[df["date"] >= START.isoformat()].reset_index(drop=True)


async def _update_spot_symbol(f: Fetcher, sym: str, info: dict, last_month: str, yesterday: date,
                              force_list: bool) -> dict:
    path = RAW_SPOT / f"{sym}.csv.gz"
    have = set(info.get("months", []))
    keys = None
    if force_list or not info.get("dead"):
        keys = await f.s3_list(f"data/spot/monthly/klines/{sym}/1d/", dirs=False)
        avail = sorted({k.rsplit("-1d-", 1)[1][:7] for k in keys if k.endswith(".zip")})
    else:
        avail = sorted(have)
    want = [m for m in avail if m >= START.strftime("%Y-%m") and m not in have]
    frames = []
    got = await asyncio.gather(*[f.get(f"{ARCHIVE}/data/spot/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
                                 for m in want])
    for m, r in zip(want, got):
        if r is not None:
            d = parse_kline_zip(r.content)
            d["src"] = "m"
            frames.append(d)
            have.add(m)
    # daily archive for days after the last published monthly file (only if the symbol is still alive)
    latest_m = max(have) if have else None
    old = _read_raw(path)
    alive = latest_m is not None and latest_m >= last_month
    if alive:
        last_stored = old["date"].max() if old is not None and len(old) else None
        start = date.fromisoformat(f"{latest_m}-01")
        start = (start.replace(day=28) + timedelta(days=4)).replace(day=1)  # first day after latest month
        if last_stored and date.fromisoformat(last_stored) >= start:
            start = date.fromisoformat(last_stored) + timedelta(days=1)
        days = [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]
        got = await asyncio.gather(*[f.get(f"{ARCHIVE}/data/spot/daily/klines/{sym}/1d/{sym}-1d-{d}.zip")
                                     for d in days])
        for r in got:
            if r is not None:
                d = parse_kline_zip(r.content)
                d["src"] = "d"
                frames.append(d)
    if frames or old is None:
        if not frames and old is None:
            return {**info, "months": sorted(have), "rows": 0, "dead": True, "checked": _now().isoformat()}
        df = _merge_rows(old, frames)
        if df.empty:
            return {**info, "months": sorted(have), "rows": 0, "dead": True, "checked": _now().isoformat()}
        _write_raw(path, df)
    else:
        df = old
    out = {"months": sorted(have), "rows": int(len(df)),
           "first": df["date"].min() if len(df) else None, "last": df["date"].max() if len(df) else None,
           "sha256": _sha256(path) if path.exists() else None, "checked": _now().isoformat()}
    # a symbol whose last monthly file is >= 3 months old will not come back under the same name (cheap re-checks)
    out["dead"] = bool(latest_m is None or latest_m < _shift_month(last_month, -2))
    return out


def _shift_month(m: str, k: int) -> str:
    y, mo = int(m[:4]), int(m[5:])
    i = y * 12 + mo - 1 + k
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


async def update_spot(f: Fetcher, man: dict, force_list: bool = False) -> None:
    t0 = time.time()
    prefixes = await f.s3_list("data/spot/monthly/klines/", dirs=True)
    syms = sorted({p.rstrip("/").rsplit("/", 1)[1] for p in prefixes})
    usdt = [s for s in syms if s.endswith("USDT")]
    excluded = {s: exclusion_reason(s) for s in usdt if exclusion_reason(s)}
    eligible = [s for s in usdt if s not in excluded]
    man["excluded"] = excluded
    today = _now().date()
    yesterday = today - timedelta(days=1)
    first_of_month = today.replace(day=1)
    last_complete = (first_of_month - timedelta(days=1)).strftime("%Y-%m")
    # the previous month's file is published a few days into the new month; accept the month before as "alive"
    alive_month = _shift_month(last_complete, -1)
    print(f"spot: {len(syms)} archive symbols, {len(usdt)} USDT pairs, {len(eligible)} eligible, "
          f"{len(excluded)} excluded", flush=True)
    spot = man.setdefault("spot", {})
    done = 0

    async def one(s: str) -> None:
        nonlocal done
        spot[s] = await _update_spot_symbol(f, s, spot.get(s, {}), alive_month, yesterday, force_list)
        done += 1
        if done % 100 == 0:
            print(f"  spot {done}/{len(eligible)} symbols, {f.n} requests, {time.time() - t0:.0f}s", flush=True)

    # bounded fan-out per symbol (each symbol's months fan out through the shared semaphore)
    for i in range(0, len(eligible), 64):
        await asyncio.gather(*[one(s) for s in eligible[i:i + 64]])
    man["spot_updated"] = _now().isoformat()
    print(f"spot done: {f.n} requests, {time.time() - t0:.0f}s", flush=True)


# --- funding ----------------------------------------------------------------------------------------------------
async def update_funding(f: Fetcher, man: dict) -> None:
    t0 = time.time()
    n0 = f.n
    prefixes = await f.s3_list("data/futures/um/monthly/fundingRate/", dirs=True)
    perps = sorted({p.rstrip("/").rsplit("/", 1)[1] for p in prefixes})
    perps = [p for p in perps if p.endswith("USDT") and exclusion_reason(perp_base(p) + "USDT") is None]
    fund = man.setdefault("funding", {})
    print(f"funding: {len(perps)} USDT-M perps in archive", flush=True)

    async def one(p: str) -> None:
        info = fund.get(p, {})
        have = set(info.get("months", []))
        if info.get("dead"):
            return
        keys = await f.s3_list(f"data/futures/um/monthly/fundingRate/{p}/", dirs=False)
        avail = sorted({k.rsplit("-fundingRate-", 1)[1][:7] for k in keys if k.endswith(".zip")})
        want = [m for m in avail if m >= START.strftime("%Y-%m") and m not in have]
        got = await asyncio.gather(*[f.get(f"{ARCHIVE}/data/futures/um/monthly/fundingRate/{p}/{p}-fundingRate-{m}.zip")
                                     for m in want])
        frames = []
        for m, r in zip(want, got):
            if r is not None:
                frames.append(parse_funding_zip(r.content))
                have.add(m)
        path = RAW_FUND / f"{p}.csv.gz"
        old = pd.read_csv(path) if path.exists() else None
        if frames:
            df = pd.concat(([old] if old is not None else []) + frames, ignore_index=True)
            df = df.drop_duplicates("time_ms", keep="last").sort_values("time_ms")
            _write_raw(path, df)
        else:
            df = old
        latest = max(have) if have else None
        last_complete = (_now().date().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        fund[p] = {"months": sorted(have), "rows": int(len(df)) if df is not None else 0,
                   "sha256": _sha256(path) if path.exists() else None,
                   "dead": bool(latest is None or latest < _shift_month(last_complete, -2))}

    for i in range(0, len(perps), 64):
        await asyncio.gather(*[one(p) for p in perps[i:i + 64]])
    print(f"funding archive done: {f.n - n0} requests, {time.time() - t0:.0f}s", flush=True)
    await _funding_tail(f, man, perps)


async def _funding_tail(f: Fetcher, man: dict, perps: list[str]) -> None:
    """Current month from /fapi/v1/fundingRate without a symbol (all perps, time-ordered, paged by startTime)."""
    fund = man["funding"]
    alive = [p for p in perps if not fund.get(p, {}).get("dead")]
    last = {}
    for p in alive:
        path = RAW_FUND / f"{p}.csv.gz"
        if path.exists():
            last[p] = int(pd.read_csv(path, usecols=["time_ms"])["time_ms"].max())
    if not last:
        return
    # perps still settling are within a few days of the newest record; older ones stopped (their final settlements
    # arrive with next month's archive), so they do not drag the paging start back
    newest = max(last.values())
    start = min(v for v in last.values() if v >= newest - 3 * 86_400_000) + 1
    start = max(start, int((_now() - timedelta(days=40)).timestamp() * 1000))
    rows, ok = [], True
    while True:
        r = await f.get(f"{FAPI}/fapi/v1/fundingRate", {"startTime": start, "limit": 1000})
        page = r.json() if r is not None else []
        if not page:
            break
        rows.extend(page)
        t_first, t_last = page[0]["fundingTime"], page[-1]["fundingTime"]
        if len(page) < 1000:
            break
        if t_first == t_last:  # a single timestamp fills a page: paging by time would skip rows
            ok = False
            break
        start = t_last  # re-read the last timestamp (deduped below) so no row at t_last is lost
        await asyncio.sleep(0.4)
    if not ok:
        print("funding tail: page saturated by one timestamp; falling back to per-symbol requests", flush=True)
        rows = []
        for p in alive:
            r = await f.get(f"{FAPI}/fapi/v1/fundingRate", {"symbol": p, "startTime": last.get(p, start) + 1,
                                                            "limit": 1000})
            rows.extend(r.json() if r is not None else [])
            await asyncio.sleep(0.65)  # 500 requests / 5 min / IP
    if not rows:
        return
    df = pd.DataFrame(rows)
    df = df[df["symbol"].isin(alive)]
    df["time_ms"] = df["fundingTime"].astype("int64")
    df["rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    added = 0
    for p, g in df.groupby("symbol"):
        path = RAW_FUND / f"{p}.csv.gz"
        old = pd.read_csv(path) if path.exists() else None
        new = pd.concat(([old] if old is not None else []) + [g[["time_ms", "rate"]]], ignore_index=True)
        new = new.drop_duplicates("time_ms", keep="first").sort_values("time_ms")
        added += len(new) - (len(old) if old is not None else 0)
        _write_raw(path, new)
        fund.setdefault(p, {})["rows"] = int(len(new))
        fund[p]["sha256"] = _sha256(path)
    man["funding_tail_through"] = datetime.fromtimestamp(df["time_ms"].max() / 1000, UTC).isoformat()
    print(f"funding tail: {added} new settlements from the live API", flush=True)


# --- panel build ------------------------------------------------------------------------------------------------
def adjust_redenominations(df: pd.DataFrame, sym: str, log: list | None = None) -> pd.DataFrame:
    """Back-adjust token redenominations (e.g. COCOS x1000 2021-01, SUN /1000 2021-06, QUICK /1000 2023-07).

    Detected as an opening gap versus the previous close that is a power of ten (within 2% in log10), which a real
    market move essentially never is; earlier prices are multiplied and base volumes divided by that factor (quote
    volume is unchanged), like a stock-split adjustment. Returns are unaffected except at the split itself.
    """
    df = df.reset_index(drop=True)
    ratio = np.log10(df["open"] / df["close"].shift(1))
    k = ratio.round()
    hit = (k != 0) & ((ratio - k).abs() < 0.02 * np.abs(k).clip(lower=1)) & ratio.notna()
    for i in np.flatnonzero(hit.to_numpy()):
        f = 10.0 ** k.iloc[i]
        for c in ("open", "high", "low", "close"):
            df.loc[: i - 1, c] *= f
        for c in ("volume", "taker_buy_base"):
            df.loc[: i - 1, c] /= f
        if log is not None:
            log.append({"symbol": sym, "date": df.loc[i, "date"], "factor": float(f)})
    return df


def build_panel(man: dict) -> pd.DataFrame:
    """Concatenate raw per-symbol files into the long panel, split relisting episodes, drop the open candle."""
    frames = []
    adjustments = man.setdefault("redenominations", []) if isinstance(man, dict) else None
    if adjustments is not None:
        adjustments.clear()
    tokenized = man.setdefault("excluded_tokenized_equities", []) if isinstance(man, dict) else []
    tokenized.clear()
    today = _now().date().isoformat()
    for path in sorted(RAW_SPOT.glob("*.csv.gz")):
        sym = path.name[:-7]
        if exclusion_reason(sym):
            continue
        df = pd.read_csv(path, dtype={"date": str, "src": str})
        df = df[(df["date"] < today) & df["close"].gt(0)].sort_values("date")
        if df.empty:
            continue
        if is_tokenized_equity(sym, df["date"].iloc[0]):
            tokenized.append(sym)
            continue
        df = adjust_redenominations(df, sym, adjustments)
        d = pd.to_datetime(df["date"])
        gap = d.diff().dt.days.fillna(0)
        ep = (gap > EPISODE_GAP_DAYS).cumsum()
        n_ep = int(ep.max())
        names = np.array([sym] * len(df), dtype=object)
        if n_ep > 0:
            for k in range(n_ep):  # earlier episodes get a dated suffix; the latest keeps the exchange symbol
                m = (ep == k).to_numpy()
                names[m] = f"{sym}~{d[m].iloc[0].strftime('%Y%m')}"
        df.insert(1, "symbol", names)
        df["exchange_symbol"] = sym
        frames.append(df.drop(columns="src"))
    panel = pd.concat(frames, ignore_index=True)
    panel["trades"] = panel["trades"].fillna(0).astype("int64")
    panel = panel.sort_values(["date", "symbol"]).reset_index(drop=True)
    return panel


def build_symbols(panel: pd.DataFrame) -> pd.DataFrame:
    end = panel["date"].max()
    g = panel.groupby("symbol")
    s = pd.DataFrame({"exchange_symbol": g["exchange_symbol"].first(), "first_date": g["date"].min(),
                      "last_date": g["date"].max(), "rows": g.size()})
    s["delisted"] = s["last_date"] < (pd.Timestamp(end) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    return s.reset_index()


def build_funding_daily(spot_symbols: set[str]) -> pd.DataFrame:
    """Daily sum of funding settlements per spot symbol (a settlement belongs to its UTC day)."""
    frames = []
    for path in sorted(RAW_FUND.glob("*.csv.gz")):
        perp = path.name[:-7]
        spot = perp_base(perp) + "USDT"
        if spot not in spot_symbols:
            continue
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        g = df.groupby("date")["rate"].agg(["sum", "count"]).reset_index()
        g.columns = ["date", "funding", "n_settlements"]
        g["symbol"] = spot
        g["perp"] = perp
        frames.append(g)
    fd = pd.concat(frames, ignore_index=True)
    # if two perps map to one spot symbol (e.g. a 1000X and an X contract), keep the one with more history per day
    fd = fd.sort_values(["date", "symbol", "n_settlements"]).drop_duplicates(["date", "symbol"], keep="last")
    return fd[["date", "symbol", "perp", "funding", "n_settlements"]].reset_index(drop=True)


def write_derived(man: dict) -> dict:
    t0 = time.time()
    panel = build_panel(man)
    syms = build_symbols(panel)
    panel_out = panel[COLUMNS]
    panel_out.to_csv(PANEL, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.10g")
    syms.to_csv(SYMBOLS, index=False)
    fd = build_funding_daily(set(panel["exchange_symbol"]))
    # funding is keyed by exchange symbol; attach it to the latest episode only (earlier episodes predate perps or
    # belong to a different asset under the same ticker)
    ep_first = syms[~syms["symbol"].str.contains("~")].set_index("symbol")["first_date"]
    fd = fd[fd["date"] >= fd["symbol"].map(ep_first).fillna("9999")]
    fd.to_csv(FUNDING_DAILY, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.10g")
    man["panel"] = {
        "rows": int(len(panel_out)), "symbols": int(panel_out["symbol"].nunique()),
        "delisted_symbols": int(syms["delisted"].sum()), "first_date": panel_out["date"].min(),
        "last_date": panel_out["date"].max(), "sha256": _sha256(PANEL), "built": _now().isoformat(),
        "episodes_split": sorted(syms.loc[syms["symbol"].str.contains("~"), "symbol"].tolist()),
    }
    man["funding_daily"] = {"rows": int(len(fd)), "symbols": int(fd["symbol"].nunique()),
                            "first_date": fd["date"].min(), "last_date": fd["date"].max(),
                            "sha256": _sha256(FUNDING_DAILY)}
    print(f"panel: {len(panel_out)} rows, {panel_out['symbol'].nunique()} symbols "
          f"({int(syms['delisted'].sum())} delisted), funding_daily {len(fd)} rows, {time.time() - t0:.0f}s",
          flush=True)
    return man


# --- loading (used by factors/research/live) --------------------------------------------------------------------
_CACHE: dict = {}


def load_panel(path: Path = PANEL) -> pd.DataFrame:
    key = ("panel", str(path), path.stat().st_mtime if path.exists() else 0)
    if key not in _CACHE:
        _CACHE.clear() if len(_CACHE) > 4 else None
        _CACHE[key] = pd.read_csv(path, dtype={"date": str, "symbol": str})
    return _CACHE[key]


def load_funding_daily(path: Path = FUNDING_DAILY) -> pd.DataFrame:
    key = ("funding", str(path), path.stat().st_mtime if path.exists() else 0)
    if key not in _CACHE:
        _CACHE[key] = (pd.read_csv(path, dtype={"date": str, "symbol": str}) if path.exists()
                       else pd.DataFrame(columns=["date", "symbol", "funding"]))
    return _CACHE[key]


def load_symbols() -> pd.DataFrame:
    return pd.read_csv(SYMBOLS, dtype={"first_date": str, "last_date": str})


# --- validation -------------------------------------------------------------------------------------------------
async def validate_live(symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"),
                        days: int = 20) -> dict:
    """Compare the panel's recent daily closes (and quote volume) with Binance's live /api/v3/klines."""
    panel = load_panel()
    f = Fetcher(8)
    out = {"checked_at": _now().isoformat(), "symbols": {}}
    try:
        for s in symbols:
            r = await f.get(f"{SPOT_API}/api/v3/klines", {"symbol": s, "interval": "1d", "limit": days + 1})
            if r is None:
                out["symbols"][s] = {"error": "not found"}
                continue
            live = pd.DataFrame(r.json()).iloc[:, [0, 4, 7]]
            live.columns = ["t", "close", "qv"]
            live["date"] = pd.to_datetime(live["t"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
            live = live.astype({"close": float, "qv": float})
            mine = panel[panel["symbol"] == s][["date", "close", "quote_volume"]]
            j = live.merge(mine, on="date", suffixes=("_live", "_panel"))
            rel = (j["close_live"] / j["close_panel"] - 1).abs()
            relq = (j["qv"] / j["quote_volume"] - 1).abs()
            out["symbols"][s] = {"days_compared": int(len(j)), "max_close_rel_diff": float(rel.max()) if len(j) else None,
                                 "max_quote_volume_rel_diff": float(relq.max()) if len(j) else None,
                                 "last_date": j["date"].max() if len(j) else None}
    finally:
        await f.close()
    diffs = [v.get("max_close_rel_diff") for v in out["symbols"].values() if v.get("max_close_rel_diff") is not None]
    out["passed"] = bool(diffs) and max(diffs) < 1e-9 and all(
        v.get("days_compared", 0) >= days - 2 for v in out["symbols"].values())
    return out


def sanity_checks(panel: pd.DataFrame) -> dict:
    """Structural checks: unique (date, symbol), OHLC consistency, extreme daily moves (data-error candidates)."""
    dup = int(panel.duplicated(["date", "symbol"]).sum())
    bad_ohlc = int(((panel["high"] < panel[["open", "close"]].max(axis=1) * (1 - 1e-9)) |
                    (panel["low"] > panel[["open", "close"]].min(axis=1) * (1 + 1e-9))).sum())
    p = panel.sort_values(["symbol", "date"])
    r = p.groupby("symbol")["close"].pct_change()
    ext = p.loc[(r > 3) | (r < -0.8), ["date", "symbol", "close"]].assign(ret=r[(r > 3) | (r < -0.8)])
    # data-driven check that no stablecoin slipped through the explicit exclusion lists
    st = p.assign(r=r).groupby("symbol").agg(med=("close", "median"), vol=("r", "std"), n=("close", "size"))
    stable_like = st[(st["vol"] < 0.004) & (st["n"] > 20)].index.tolist()
    return {"duplicate_rows": dup, "ohlc_inconsistent_rows": bad_ohlc, "extreme_daily_moves": int(len(ext)),
            "stable_like_symbols": stable_like,
            "extreme_examples": ext.sort_values("ret").head(5).round(4).to_dict("records") +
            ext.sort_values("ret").tail(5).round(4).to_dict("records")}


# --- entry points -----------------------------------------------------------------------------------------------
async def update(force_list: bool = False, funding: bool = True) -> dict:
    ROOT.mkdir(parents=True, exist_ok=True)
    man = load_manifest()
    f = Fetcher()
    t0 = time.time()
    try:
        await update_spot(f, man, force_list)
        save_manifest(man)
        if funding:
            await update_funding(f, man)
            save_manifest(man)
    finally:
        await f.close()
    write_derived(man)
    man["last_update"] = {"at": _now().isoformat(), "seconds": round(time.time() - t0, 1), "requests": f.n}
    save_manifest(man)
    return man


def stats() -> dict:
    panel = load_panel()
    syms = load_symbols()
    by_year = panel.assign(y=panel["date"].str[:4]).groupby("y")["symbol"].nunique().to_dict()
    return {"rows": len(panel), "symbols": int(panel["symbol"].nunique()), "delisted": int(syms["delisted"].sum()),
            "first_date": panel["date"].min(), "last_date": panel["date"].max(), "symbols_by_year": by_year,
            "size_mb": round(sum(p.stat().st_size for p in ROOT.rglob("*") if p.is_file()) / 1e6, 1)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Quant lab data: Binance survivorship-free daily panel")
    ap.add_argument("--update", action="store_true", help="incremental download + rebuild panel")
    ap.add_argument("--rebuild", action="store_true", help="rebuild panel from raw files only")
    ap.add_argument("--force-list", action="store_true", help="re-list every symbol, including dead ones")
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--validate", action="store_true", help="compare recent closes with the live API")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    if a.update:
        asyncio.run(update(a.force_list, not a.no_funding))
    if a.rebuild:
        man = load_manifest()
        write_derived(man)
        save_manifest(man)
    if a.update or a.validate:
        v = asyncio.run(validate_live())
        v["sanity"] = sanity_checks(load_panel())
        man = load_manifest()
        man["validation"] = v
        save_manifest(man)
        print(json.dumps(v, indent=1, default=str))
    if a.stats or not (a.update or a.rebuild or a.validate):
        print(json.dumps(stats(), indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
