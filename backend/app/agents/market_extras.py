"""Market-structure measurements the price chart does not show: order flow, funding across venues, options, depth.

Sources (free, public, no key, cached so a desk run costs a handful of requests):
  Binance USD-M futures  /fapi/v1/klines (taker-buy volume per bar: index 5 is total base volume, index 9 the
                         taker-buy part), /premiumIndex (mark price, current funding), /openInterest (base units),
                         /fundingInfo (symbols whose funding interval is not 8h) and /depth (order book, 500 levels).
                         Binance spot /api/v3 (same columns) is the fallback for a coin without a perpetual.
  Bybit                  /v5/market/tickers (linear): funding, its interval, single-side open interest in USD.
  OKX                    /api/v5/public/funding-rate and /open-interest (SWAP).
  Deribit                /public/get_volatility_index_data (DVOL) and /get_book_summary_by_currency (every listed
                         option: open interest, mark IV, underlying). BTC and ETH only: market-wide context, since
                         altcoins have no liquid listed options.

Everything is arithmetic on those numbers: cumulative volume delta (2 x taker buy - volume), funding normalised to
8h and annualised, OI-weighted funding, put/call open interest, ATM implied vol by expiry, and the average fill of a
market order walked through the book. No opinions. A source that fails leaves its fields out and says so in
`unavailable`; no function here raises.
"""
import asyncio
import re
import time
from datetime import UTC, datetime

import httpx

from app.agents.fundamentals import base_of
from app.logging import log

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
DERIBIT = "https://www.deribit.com/api/v2/public"
TIMEOUT = 20
TFS = ("15m", "1h", "4h", "1d")
WINDOWS = (24, 96)
PRICE_FLAT_PCT, CVD_FLAT_PCT = 0.5, 2.0  # below these a move is called flat, not a direction
MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                      "DEC"), 1)}
_cache: dict[str, tuple[float, object]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _cached(key: str, ttl: float, fetch):
    """One fetch per key per `ttl` seconds, even when several agents ask at the same moment."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = await fetch()
        _cache[key] = (time.time(), val)
        return val


async def _get(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "ConfluenceAI/1.0"}) as c:
        for wait in (3, None):  # one back-off on 429, then give up
            r = await c.get(url, params=params)
            if r.status_code != 429 or wait is None:
                break
            await asyncio.sleep(wait)
        r.raise_for_status()
        return r.json()


def _r(x, n: int = 2):
    return round(float(x), n) if isinstance(x, (int, float)) else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="minutes")


def _err(e: BaseException) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}: {e.response.text[:60]}"
    return str(e)[:80] or type(e).__name__


# ------------------------------------------------------------------------------------------------ order flow

async def _klines(symbol: str, tf: str) -> tuple[str, list]:
    """Closed bars (perpetual first, spot if the perpetual does not exist) and which market they came from."""
    async def fetch():
        p = {"symbol": symbol, "interval": tf, "limit": max(WINDOWS) + 1}
        try:
            return "binance_futures", await _get(f"{FAPI}/fapi/v1/klines", p)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:  # 400 is "Invalid symbol": no perpetual, use spot
                raise
            return "binance_spot", await _get(f"{SPOT}/api/v3/klines", p)
    return await _cached(f"klines:{symbol}:{tf}", 60, fetch)


def _direction(price_pct: float, cvd_pct: float) -> str:
    if abs(price_pct) < PRICE_FLAT_PCT or abs(cvd_pct) < CVD_FLAT_PCT:
        return "flat"
    if price_pct > 0 > cvd_pct:
        return "price up, CVD down"
    if price_pct < 0 < cvd_pct:
        return "price down, CVD up"
    return "aligned"


def flow_window(rows: list) -> dict:
    """CVD, taker-buy share and price change over a run of kline rows."""
    vol = sum(float(k[5]) for k in rows)
    buy = sum(float(k[9]) for k in rows)
    delta = 2 * buy - vol
    cvd_pct = 100 * delta / vol if vol else 0.0
    price_pct = 100 * (float(rows[-1][4]) / float(rows[0][1]) - 1)
    return {"bars": len(rows), "cvd_base": _r(delta, 2), "cvd_pct_of_volume": _r(cvd_pct),
            "volume_base": _r(vol, 2), "taker_buy_pct": _r(100 * buy / vol if vol else None),
            "price_change_pct": _r(price_pct), "divergence": _direction(price_pct, cvd_pct)}


async def order_flow(symbol: str, tf: str) -> dict:
    """Cumulative volume delta over the last 24 and 96 closed bars, against the price move over the same bars."""
    symbol = symbol.upper()
    out: dict = {"symbol": symbol, "tf": tf, "unavailable": [], "as_of": _now(),
                 "method": "delta per bar = 2 x taker-buy volume - volume (base units); CVD is its sum",
                 "flat_below": {"price_change_pct": PRICE_FLAT_PCT, "cvd_pct_of_volume": CVD_FLAT_PCT}}
    if tf not in TFS:
        out["unavailable"].append(f"timeframe {tf!r} not supported (use one of {', '.join(TFS)})")
        return out
    try:
        source, rows = await _klines(symbol, tf)
    except Exception as e:  # noqa: BLE001 - order flow is optional context
        log.warning("order_flow_failed", symbol=symbol, error=str(e))
        out["unavailable"].append(f"Binance klines ({_err(e)})")
        return out
    now_ms = time.time() * 1000
    rows = [k for k in rows if len(k) > 9 and k[6] < now_ms][-max(WINDOWS):]  # drop the bar still forming
    out["source"] = source
    if not rows:
        out["unavailable"].append(f"no closed {tf} bars for {symbol}")
        return out
    out["last_bar_close"] = datetime.fromtimestamp((rows[-1][6] + 1) / 1000, UTC).isoformat(timespec="minutes")
    for n in WINDOWS:
        out[f"last_{n}"] = flow_window(rows[-n:])
        if len(rows) < n:
            out["unavailable"].append(f"only {len(rows)} closed bars, last_{n} covers fewer than {n}")
    return out


# ---------------------------------------------------------------------------------------------- funding & OI

def _funding_row(venue: str, rate, interval_h, oi_usd, mark=None, next_ms=None) -> dict:
    rate, interval_h = float(rate), float(interval_h or 8)
    return {"venue": venue, "funding_rate_pct": _r(100 * rate, 5), "interval_h": _r(interval_h, 1),
            "funding_8h_pct": _r(100 * rate * 8 / interval_h, 5),
            "annualized_pct": _r(100 * rate * 24 / interval_h * 365),
            "open_interest_usd": _r(oi_usd, 0), "mark_price": _r(mark, 6),
            "next_funding": datetime.fromtimestamp(int(next_ms) / 1000, UTC).isoformat(timespec="minutes")
            if next_ms else None}


async def _binance_funding(symbol: str) -> dict:
    async def fetch():
        prem, oi = await asyncio.gather(_get(f"{FAPI}/fapi/v1/premiumIndex", {"symbol": symbol}),
                                        _get(f"{FAPI}/fapi/v1/openInterest", {"symbol": symbol}))
        try:
            info = await _cached("binance_funding_info", 6 * 3600, lambda: _get(f"{FAPI}/fapi/v1/fundingInfo"))
        except Exception:  # noqa: BLE001 - only lists symbols with a non-8h interval; 8h is the default
            info = []
        hours = next((i.get("fundingIntervalHours") for i in info if i.get("symbol") == symbol), 8)
        mark = float(prem["markPrice"])
        return _funding_row("binance", prem["lastFundingRate"], hours, float(oi["openInterest"]) * mark, mark,
                            prem.get("nextFundingTime"))
    return await _cached(f"fund:binance:{symbol}", 120, fetch)


async def _bybit_funding(symbol: str) -> dict:
    async def fetch():
        d = await _get(f"{BYBIT}/v5/market/tickers", {"category": "linear", "symbol": symbol})
        if d.get("retCode") != 0 or not (d.get("result") or {}).get("list"):
            raise ValueError(f"no linear perpetual {symbol} ({d.get('retMsg')})")
        t = d["result"]["list"][0]
        # openInterestValue counts both sides; the single-side figure is comparable with Binance and OKX
        oi = t.get("singleOpenInterestValue") or t.get("openInterestValue")
        mark = float(t["markPrice"]) if t.get("markPrice") else None
        return _funding_row("bybit", t["fundingRate"], t.get("fundingIntervalHour"), float(oi), mark,
                            t.get("nextFundingTime"))
    return await _cached(f"fund:bybit:{symbol}", 120, fetch)


async def _okx_funding(base: str) -> dict:
    inst = f"{base}-USDT-SWAP"

    async def fetch():
        f, oi = await asyncio.gather(_get(f"{OKX}/api/v5/public/funding-rate", {"instId": inst}),
                                     _get(f"{OKX}/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}))
        if f.get("code") != "0" or not f.get("data"):
            raise ValueError(f"no swap {inst} ({f.get('msg')})")
        fr = f["data"][0]
        hours = (int(fr["nextFundingTime"]) - int(fr["fundingTime"])) / 3.6e6 if fr.get("nextFundingTime") else 8
        oi_usd = float(oi["data"][0]["oiUsd"]) if oi.get("code") == "0" and oi.get("data") else None
        return _funding_row("okx", fr["fundingRate"], hours, oi_usd, None, fr.get("fundingTime"))
    return await _cached(f"fund:okx:{inst}", 120, fetch)


async def funding_across(symbol: str) -> dict:
    """Funding (per interval, per 8h, annualised) and open interest on Binance, Bybit and OKX, and how they compare."""
    symbol = symbol.upper()
    out: dict = {"symbol": symbol, "unavailable": [], "as_of": _now(),
                 "note": "positive funding: longs pay shorts; annualized = rate x periods per day x 365"}
    got = await asyncio.gather(_binance_funding(symbol), _bybit_funding(symbol), _okx_funding(base_of(symbol)),
                               return_exceptions=True)
    rows = []
    for venue, g in zip(("binance", "bybit", "okx"), got):
        if isinstance(g, BaseException):
            out["unavailable"].append(f"{venue} funding ({_err(g)})")
        else:
            rows.append(g)
    out["venues"] = rows
    if not rows:
        return out
    with_oi = [r for r in rows if r["open_interest_usd"]]
    total = sum(r["open_interest_usd"] for r in with_oi)
    for r in rows:
        r["oi_share_pct"] = _r(100 * r["open_interest_usd"] / total, 1) if total and r["open_interest_usd"] else None
    ann = [r["annualized_pct"] for r in rows]
    top = max(rows, key=lambda r: abs(r["annualized_pct"]))
    out.update(
        total_open_interest_usd=_r(total, 0),
        oi_weighted_annualized_pct=_r(sum(r["annualized_pct"] * r["open_interest_usd"] for r in with_oi) / total)
        if total else None,
        spread_annualized_pp=_r(max(ann) - min(ann)),
        most_crowded={"venue": top["venue"], "annualized_pct": top["annualized_pct"],
                      "paying": "longs" if top["annualized_pct"] > 0 else "shorts" if top["annualized_pct"] < 0
                      else "nobody"})
    return out


# -------------------------------------------------------------------------------------------------- options

def _expiry(name: str) -> datetime | None:
    """BTC-27NOV26-90000-C -> 27 Nov 2026 08:00 UTC (Deribit settles at 08:00)."""
    m = re.fullmatch(r"(\d{1,2})([A-Z]{3})(\d{2})", name.split("-")[1]) if name.count("-") == 3 else None
    if not m or m[2] not in MONTHS:
        return None
    return datetime(2000 + int(m[3]), MONTHS[m[2]], int(m[1]), 8, tzinfo=UTC)


def option_stats(book: list[dict], now: datetime | None = None) -> dict:
    """Put/call open interest and ATM implied vol for the nearest expiry (> 2 days) and the one closest to 30 days."""
    now = now or datetime.now(UTC)
    puts = sum(float(b.get("open_interest") or 0) for b in book if b["instrument_name"].endswith("-P"))
    calls = sum(float(b.get("open_interest") or 0) for b in book if b["instrument_name"].endswith("-C"))
    out: dict = {"put_oi": _r(puts, 1), "call_oi": _r(calls, 1), "put_call_oi_ratio": _r(puts / calls, 3) if calls
                 else None}
    by_exp: dict[datetime, list[dict]] = {}
    for b in book:
        exp = _expiry(b["instrument_name"])
        if exp and b.get("mark_iv") and b.get("underlying_price"):
            by_exp.setdefault(exp, []).append(b)
    days = {e: (e - now).total_seconds() / 86400 for e in by_exp}
    live = [e for e in by_exp if days[e] > 2]
    if not live:
        return out

    def atm(e: datetime) -> dict:
        legs = by_exp[e]
        und = float(legs[0]["underlying_price"])
        strike = min({float(b["instrument_name"].split("-")[2]) for b in legs}, key=lambda k: abs(k - und))
        ivs = [float(b["mark_iv"]) for b in legs if float(b["instrument_name"].split("-")[2]) == strike]
        return {"expiry": e.date().isoformat(), "days": _r(days[e], 1), "underlying": _r(und, 2), "strike": strike,
                "atm_iv": _r(sum(ivs) / len(ivs), 1)}
    near, month = atm(min(live, key=lambda e: days[e])), atm(min(live, key=lambda e: abs(days[e] - 30)))
    slope = month["atm_iv"] - near["atm_iv"]
    out["term_structure"] = {"near": near, "one_month": month, "month_minus_near_pts": _r(slope, 1),
                             "shape": "flat" if abs(slope) < 1 or near["expiry"] == month["expiry"]
                             else "contango (longer dated higher)" if slope > 0 else "inverted (near dated higher)"}
    return out


async def options_market() -> dict:
    """BTC and ETH implied volatility (DVOL), put/call open interest and ATM term structure from Deribit."""
    out: dict = {"scope": "BTC/ETH market-wide context; altcoins have no liquid listed options", "unavailable": [],
                 "as_of": _now()}
    end = int(time.time() * 1000)
    for cur in ("BTC", "ETH"):
        o: dict = {}
        try:
            d = await _cached(f"dvol:{cur}", 900, lambda c=cur: _get(f"{DERIBIT}/get_volatility_index_data", {
                "currency": c, "start_timestamp": end - 2 * 86400_000, "end_timestamp": end, "resolution": 3600}))
            bars = (d.get("result") or {}).get("data") or []  # [ts, open, high, low, close], oldest first
            if bars:
                last = bars[-1][4]
                ago = next((b[4] for b in reversed(bars) if b[0] <= bars[-1][0] - 86400_000), None)
                o["dvol"] = {"value": _r(last, 1), "change_24h_pts": _r(last - ago, 1) if ago else None,
                             "high_48h": _r(max(b[2] for b in bars), 1), "low_48h": _r(min(b[3] for b in bars), 1)}
            else:
                out["unavailable"].append(f"{cur} DVOL (no data)")
        except Exception as e:  # noqa: BLE001
            out["unavailable"].append(f"{cur} DVOL ({_err(e)})")
        try:
            d = await _cached(f"opt_book:{cur}", 900, lambda c=cur: _get(f"{DERIBIT}/get_book_summary_by_currency",
                                                                          {"currency": c, "kind": "option"}))
            o.update(option_stats(d.get("result") or []))
        except Exception as e:  # noqa: BLE001
            out["unavailable"].append(f"{cur} option book ({_err(e)})")
        out[cur.lower()] = o
    return out


# ---------------------------------------------------------------------------------------------------- depth

def slippage_bps(levels: list[tuple[float, float]], notional: float, mid: float) -> float | None:
    """Average fill of a market order for `notional` USD walked through (price, qty) levels, in bps away from mid.
    None when the visible book is too thin to fill it (or the inputs make no sense)."""
    if notional <= 0 or mid <= 0:
        return None
    left, qty = notional, 0.0
    for price, size in levels:
        take = min(left, price * size)
        qty += take / price
        left -= take
        if left <= 1e-9:
            return round(abs(notional / qty - mid) / mid * 1e4, 2)
    return None


async def depth_slippage(symbol: str, notional_usd: float) -> dict:
    """Expected slippage (bps from mid, half the spread included) of a market buy and sell of `notional_usd`."""
    symbol = symbol.upper()
    out: dict = {"symbol": symbol, "notional_usd": notional_usd, "unavailable": [], "as_of": _now()}

    async def fetch():
        try:
            return "binance_futures", await _get(f"{FAPI}/fapi/v1/depth", {"symbol": symbol, "limit": 500})
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:
                raise
            return "binance_spot", await _get(f"{SPOT}/api/v3/depth", {"symbol": symbol, "limit": 500})
    try:
        source, book = await _cached(f"depth:{symbol}", 15, fetch)
        bids = [(float(p), float(q)) for p, q in book["bids"]]
        asks = [(float(p), float(q)) for p, q in book["asks"]]
    except Exception as e:  # noqa: BLE001
        out["unavailable"].append(f"Binance order book ({_err(e)})")
        return out
    if not bids or not asks:
        out["unavailable"].append("empty order book")
        return out
    mid = (bids[0][0] + asks[0][0]) / 2
    out.update(source=source, mid=_r(mid, 8), spread_bps=_r((asks[0][0] - bids[0][0]) / mid * 1e4),
               buy_slippage_bps=slippage_bps(asks, notional_usd, mid),
               sell_slippage_bps=slippage_bps(bids, notional_usd, mid),
               depth_within_1pct_usd={"bids": _r(sum(p * q for p, q in bids if p >= mid * 0.99), 0),
                                      "asks": _r(sum(p * q for p, q in asks if p <= mid * 1.01), 0)},
               visible_depth_usd={"bids": _r(sum(p * q for p, q in bids), 0),
                                  "asks": _r(sum(p * q for p, q in asks), 0)})
    for side in ("buy", "sell"):
        if out[f"{side}_slippage_bps"] is None:
            out["unavailable"].append(f"{side}: the {len(bids if side == 'sell' else asks)} visible levels hold less "
                                      f"than ${notional_usd:,.0f}")
    return out


def book_capacity(levels: list[tuple[float, float]], mid: float, max_bps: float) -> float:
    """Dollar size resting on one side of the book within `max_bps` of the mid: what can be filled before the price
    moves that far."""
    if not mid:
        return 0.0
    lim = max_bps / 1e4
    return sum(p * q for p, q in levels if abs(p / mid - 1) <= lim)


CAPACITY_BPS = 10      # the price move our users may cause together
CAPACITY_SHARE = 0.25  # the share of that resting size our users may take, so the crowd never becomes the market


async def signal_capacity(symbol: str, side: str, max_bps: float = CAPACITY_BPS, share: float = CAPACITY_SHARE) -> dict:
    """How much of one signal the platform can hand out before its users move the price against themselves: the
    resting size within `max_bps` on the side a market entry takes (asks for a long, bids for a short), times `share`.
    Delivery stops recommending the signal once the size sent out reaches it (docs/PLATFORM_PLAN.md)."""
    symbol = symbol.upper()
    out: dict = {"symbol": symbol, "side": side, "max_bps": max_bps, "share": share, "unavailable": [], "as_of": _now()}
    try:
        source, book = await _cached(f"depth:{symbol}", 15, lambda: _depth_fetch(symbol))
    except Exception as e:  # noqa: BLE001
        out["unavailable"].append(f"Binance order book ({_err(e)})")
        return out
    bids = [(float(p), float(q)) for p, q in book["bids"]]
    asks = [(float(p), float(q)) for p, q in book["asks"]]
    if not bids or not asks:
        out["unavailable"].append("empty order book")
        return out
    mid = (bids[0][0] + asks[0][0]) / 2
    resting = book_capacity(asks if side == "LONG" else bids, mid, max_bps)
    out.update(source=source, resting_usd=_r(resting, 0), capacity_usd=_r(resting * share, 0))
    return out


async def _depth_fetch(symbol: str):
    try:
        return "binance_futures", await _get(f"{FAPI}/fapi/v1/depth", {"symbol": symbol, "limit": 500})
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 400:
            raise
        return "binance_spot", await _get(f"{SPOT}/api/v3/depth", {"symbol": symbol, "limit": 500})
