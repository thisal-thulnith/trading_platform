"""Fundamental data for the desk's Fundamentals team (Ledger and its specialists): measured, never a verdict.

Sources (free, public, cached so a desk run costs at most a handful of requests):
  CoinGecko  /coins/markets (top 250, one call: supply, valuation, rank, volume, ATH, 7d/30d/1y change), /global
             (total market cap, BTC dominance) and /coins/{id} (categories, launch date, community votes).
  DefiLlama  /v2/chains (chain TVL), /protocols (protocol TVL by token symbol), /summary/fees (fees and revenue) and
             stablecoins (total stablecoin supply: the market's dry powder).

Everything derived here is arithmetic on those numbers (FDV / market cap, volume / market cap, market cap / TVL, price
to fees and revenue, performance against BTC, breadth of the top 100). Reference bands for reading them live in
docs/FUNDAMENTALS.md, which the agents search; the data itself carries no opinion. A source that fails leaves its
fields out and says so in `unavailable`, so an agent can say what is unknown instead of guessing.
"""
import asyncio
import time
from datetime import UTC, datetime

import httpx

from app.config import get_settings
from app.logging import log

LLAMA = "https://api.llama.fi"
STABLES = "https://stablecoins.llama.fi"
TIMEOUT = 25
_cache: dict[str, tuple[float, object]] = {}
_locks: dict[str, asyncio.Lock] = {}
NOT_USAGE = {"CEX", "Chain", "Canonical Bridge", "Bridge"}


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
        try:
            val = await fetch()
        except Exception:
            if hit:  # a source that fails (CoinGecko's free tier rate-limits) keeps serving its last good answer
                return hit[1]
            raise
        _cache[key] = (time.time(), val)
        return val


async def _get(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "ConfluenceAI/1.0"}) as c:
        for wait in (2, 5, None):  # CoinGecko's free tier answers 429 to bursts: back off twice, then give up
            r = await c.get(url, params=params)
            if r.status_code != 429 or wait is None:
                break
            await asyncio.sleep(wait)
        r.raise_for_status()
        return r.json()


def _cg_params(extra: dict | None = None) -> dict:
    s = get_settings()
    return {**(extra or {}), **({"x_cg_demo_api_key": s.coingecko_api_key} if s.coingecko_api_key else {})}


async def markets() -> list[dict]:
    """The top 250 coins by market cap with 7d/30d/1y changes (one call, an hour)."""
    async def fetch():
        return await _get(f"{get_settings().coingecko_rest}/coins/markets", _cg_params({
            "vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": 1,
            "price_change_percentage": "7d,30d,1y"}))
    return await _cached("cg_markets", 3600, fetch)


async def _detail(cg_id: str) -> dict:
    async def fetch():
        return await _get(f"{get_settings().coingecko_rest}/coins/{cg_id}", _cg_params({
            "localization": "false", "tickers": "false", "market_data": "false", "community_data": "false",
            "developer_data": "false", "sparkline": "false"}))
    return await _cached(f"cg_detail:{cg_id}", 86400, fetch)


async def _chains() -> list[dict]:
    return await _cached("llama_chains", 6 * 3600, lambda: _get(f"{LLAMA}/v2/chains"))


async def _protocols() -> list[dict]:
    return await _cached("llama_protocols", 6 * 3600, lambda: _get(f"{LLAMA}/protocols"))


async def _fees(slug: str, kind: str) -> dict | None:
    async def fetch():
        try:
            return await _get(f"{LLAMA}/summary/fees/{slug}", {"dataType": kind, "excludeTotalDataChart": "true",
                                                              "excludeTotalDataChartBreakdown": "true"})
        except httpx.HTTPStatusError:
            return None  # no fee adapter for this project
    return await _cached(f"llama_fees:{slug}:{kind}", 6 * 3600, fetch)


def base_of(symbol: str) -> str:
    b = symbol.upper().replace("USDT", "")
    for p in ("1000000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):]
    return b


def _pick(rows: list[dict], base: str) -> dict | None:
    """The coin for a ticker: the highest market cap among the coins with that symbol (tickers are not unique)."""
    same = [r for r in rows if (r.get("symbol") or "").upper() == base and r.get("market_cap")]
    return max(same, key=lambda r: r["market_cap"]) if same else None


def _r(x, n: int = 2):
    return round(float(x), n) if isinstance(x, (int, float)) else None


def _ratio(a, b, n: int = 2):
    return round(a / b, n) if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b else None


def _chg(r: dict, k: str):
    return _r(r.get(f"price_change_percentage_{k}_in_currency"))


async def coin(symbol: str) -> dict:
    """Every fundamental measurement for one Binance symbol, grouped for the three specialists."""
    base = base_of(symbol)
    out: dict = {"symbol": symbol, "base": base, "unavailable": [], "as_of": datetime.now(UTC).isoformat(timespec="minutes")}
    try:
        rows = await markets()
    except Exception as e:  # noqa: BLE001 - fundamentals are optional context
        log.warning("fundamentals_markets_failed", error=str(e))
        out["unavailable"].append(f"CoinGecko market data ({str(e)[:60]})")
        return out
    r = _pick(rows, base)
    if not r:
        out["unavailable"].append(f"{base} is not in CoinGecko's top 250 coins: a small or new coin, fundamentals unknown")
        return out
    btc = _pick(rows, "BTC") or {}
    mcap, fdv = r.get("market_cap"), r.get("fully_diluted_valuation")
    circ, total, mx = r.get("circulating_supply"), r.get("total_supply"), r.get("max_supply")
    cap_supply = mx or total
    out.update(coingecko_id=r["id"], name=r.get("name"))
    out["tokenomics"] = {
        "market_cap_usd": _r(mcap, 0), "fdv_usd": _r(fdv, 0), "fdv_to_market_cap": _ratio(fdv, mcap),
        "locked_or_unissued_value_usd": _r((fdv or 0) - (mcap or 0), 0) if fdv and mcap else None,
        "circulating_supply": _r(circ, 0), "total_supply": _r(total, 0), "max_supply": _r(mx, 0),
        "circulating_pct_of_max_or_total": _r(100 * circ / cap_supply, 1) if circ and cap_supply else None,
        "supply_cap": "fixed max supply" if mx else "no max supply (can inflate)" if total else "unknown",
    }
    ath_date = r.get("ath_date")
    days_ath = None
    if ath_date:
        try:
            days_ath = (datetime.now(UTC) - datetime.fromisoformat(ath_date.replace("Z", "+00:00"))).days
        except ValueError:
            pass
    rel = {k: _r(_chg(r, k) - _chg(btc, k)) if _chg(r, k) is not None and _chg(btc, k) is not None else None
           for k in ("7d", "30d", "1y")}
    out["standing"] = {
        "market_cap_rank": r.get("market_cap_rank"), "price_usd": r.get("current_price"),
        "volume_24h_usd": _r(r.get("total_volume"), 0),
        "volume_to_market_cap_pct": _r(100 * r["total_volume"] / mcap, 2) if r.get("total_volume") and mcap else None,
        "ath_usd": r.get("ath"), "pct_from_ath": _r(r.get("ath_change_percentage"), 1), "ath_date": (ath_date or "")[:10] or None,
        "days_since_ath": days_ath,
        "change_pct": {"7d": _chg(r, "7d"), "30d": _chg(r, "30d"), "1y": _chg(r, "1y")},
        "vs_btc_pct_points": rel,
    }
    # categories and community (one detail call a day; optional)
    try:
        d = await _detail(r["id"])
        out["standing"].update(categories=[c for c in (d.get("categories") or []) if c][:8],
                               launched=d.get("genesis_date"),
                               community_votes_up_pct=_r(d.get("sentiment_votes_up_percentage"), 1),
                               watchlist_users=d.get("watchlist_portfolio_users"))
    except Exception as e:  # noqa: BLE001
        out["unavailable"].append(f"CoinGecko categories ({str(e)[:50]})")
    out["usage"] = await _usage(r["id"], base, mcap)
    if out["usage"].get("note"):
        out["unavailable"].append(out["usage"]["note"])
    return out


async def _usage(cg_id: str, base: str, mcap) -> dict:
    """TVL, fees and revenue: what the network or protocol actually does, and what the market pays for it."""
    u: dict = {"kind": None}
    slugs: list[str] = [cg_id]
    try:
        chain = next((c for c in await _chains() if c.get("gecko_id") == cg_id), None)
        if chain:
            u.update(kind="chain", name=chain.get("name"), tvl_usd=_r(chain.get("tvl"), 0))
            slugs.append((chain.get("name") or "").lower())
        else:
            ps = [p for p in await _protocols() if (p.get("symbol") or "").upper() == base
                  and p.get("category") not in NOT_USAGE]
            if ps:
                groups: dict[str, list[dict]] = {}
                for p in ps:
                    groups.setdefault(p.get("parentProtocol") or p["slug"], []).append(p)
                key, grp = max(groups.items(), key=lambda kv: sum(p.get("tvl") or 0 for p in kv[1]))
                tvl = sum(p.get("tvl") or 0 for p in grp)
                w7 = [(p.get("tvl") or 0, p.get("change_7d")) for p in grp if isinstance(p.get("change_7d"), (int, float))]
                u.update(kind="protocol", name=key.replace("parent#", ""), tvl_usd=_r(tvl, 0),
                         categories=sorted({p.get("category") for p in grp if p.get("category")})[:5],
                         tvl_change_7d_pct=_r(sum(t * c for t, c in w7) / sum(t for t, _ in w7), 1)
                         if w7 and sum(t for t, _ in w7) else None)
                slugs.append(key.replace("parent#", ""))
    except Exception as e:  # noqa: BLE001
        u["note"] = f"DefiLlama TVL ({str(e)[:50]})"
    if u.get("tvl_usd"):
        u["market_cap_to_tvl"] = _ratio(mcap, u["tvl_usd"])
    for slug in dict.fromkeys(s for s in slugs if s):
        try:
            fees = await _fees(slug, "dailyFees")
        except Exception:  # noqa: BLE001
            fees = None
        if fees and fees.get("total30d"):
            rev = None
            try:
                rev = await _fees(slug, "dailyRevenue")
            except Exception:  # noqa: BLE001
                pass
            f30, r30 = fees.get("total30d"), (rev or {}).get("total30d")
            u.update(fees_30d_usd=_r(f30, 0), fees_1y_usd=_r(fees.get("total1y"), 0),
                     fees_annualized_from_30d_usd=_r(f30 * 365 / 30, 0),
                     revenue_30d_usd=_r(r30, 0), revenue_1y_usd=_r((rev or {}).get("total1y"), 0),
                     market_cap_to_annual_fees=_ratio(mcap, f30 * 365 / 30, 1),
                     market_cap_to_annual_revenue=_ratio(mcap, r30 * 365 / 30, 1) if r30 else None,
                     fees_trend_30d_vs_1y_avg=_ratio(f30 * 365 / 30, fees.get("total1y")))
            break
    if not u["kind"] and "fees_30d_usd" not in u:
        u.setdefault("note", f"no TVL or fee data for {base}: not a DeFi protocol or chain tracked by DefiLlama "
                             "(a payments, meme or store-of-value coin has no usage metric here)")
    return u


async def macro() -> dict:
    """The whole crypto market: total cap, BTC/ETH dominance, stablecoin supply (dry powder) and breadth."""
    out: dict = {"unavailable": []}
    try:
        g = (await _cached("cg_global", 900, lambda: _get(f"{get_settings().coingecko_rest}/global", _cg_params())))["data"]
        dom = g.get("market_cap_percentage") or {}
        out.update(total_market_cap_usd=_r((g.get("total_market_cap") or {}).get("usd"), 0),
                   total_market_cap_change_24h_pct=_r(g.get("market_cap_change_percentage_24h_usd")),
                   btc_dominance_pct=_r(dom.get("btc")), eth_dominance_pct=_r(dom.get("eth")),
                   stablecoin_dominance_pct=_r(sum(dom.get(k, 0) for k in ("usdt", "usdc", "dai", "usde"))))
    except Exception as e:  # noqa: BLE001
        out["unavailable"].append(f"CoinGecko global ({str(e)[:50]})")
    try:
        st = await _cached("llama_stables", 6 * 3600, lambda: _get(f"{STABLES}/stablecoincharts/all"))
        usd = [float((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0) for x in st]
        if len(usd) > 31 and usd[-1]:
            out.update(stablecoin_supply_usd=_r(usd[-1], 0),
                       stablecoin_supply_change_7d_pct=_r(100 * (usd[-1] / usd[-8] - 1)),
                       stablecoin_supply_change_30d_pct=_r(100 * (usd[-1] / usd[-31] - 1)))
    except Exception as e:  # noqa: BLE001
        out["unavailable"].append(f"DefiLlama stablecoins ({str(e)[:50]})")
    try:
        rows = await markets()
        btc = _pick(rows, "BTC") or {}
        top = [r for r in rows if (r.get("symbol") or "").lower() not in STABLE_TICKERS][:100]
        for k in ("7d", "30d"):
            b = _chg(btc, k)
            vals = [_chg(r, k) for r in top if _chg(r, k) is not None]
            if b is not None and vals:
                out[f"top100_beating_btc_{k}_pct"] = _r(100 * sum(v > b for v in vals) / len(vals), 0)
                out[f"top100_up_{k}_pct"] = _r(100 * sum(v > 0 for v in vals) / len(vals), 0)
        out["btc_change_pct"] = {"7d": _chg(btc, "7d"), "30d": _chg(btc, "30d"), "1y": _chg(btc, "1y")}
    except Exception as e:  # noqa: BLE001
        out["unavailable"].append(f"breadth ({str(e)[:50]})")
    return out


STABLE_TICKERS = {"usdt", "usdc", "dai", "usde", "fdusd", "tusd", "usds", "pyusd", "usd1", "usdd", "susde", "busd",
                  "usdtb", "rlusd", "gusd", "lusd", "frax", "usdp", "eurc", "bsc-usd", "usdx", "usd0"}
