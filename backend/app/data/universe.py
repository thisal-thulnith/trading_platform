"""Dynamic universe selection: rank liquid, tradable USDT markets on Binance."""
import numpy as np

from app.config import get_settings
from app.data.binance import BinanceClient
from app.data.http import ProviderUnavailable, ResilientClient
from app.logging import log

EXCLUDE = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USDS", "PYUSD", "USD1", "BUSD", "EUR", "EURI",
           "WBTC", "WETH", "STETH", "WSTETH", "WBETH", "WEETH", "CBBTC", "XAUT", "PAXG", "BFUSD", "RLUSD"}
WEIGHTS = {"market_cap": 0.30, "volume": 0.30, "perp": 0.15, "spread": 0.10, "candidate_prior": 0.15}


def _rank_score(values: dict[str, float], higher_better: bool = True) -> dict[str, float]:
    if not values:
        return {}
    keys = list(values)
    arr = np.array([values[k] for k in keys], dtype=float)
    order = arr.argsort()
    if higher_better:
        order = order[::-1]
    ranks = np.empty(len(arr))
    ranks[order] = np.arange(len(arr))
    return {k: float(1 - ranks[i] / max(len(arr) - 1, 1)) for i, k in enumerate(keys)}


async def rank_universe(client: BinanceClient, size: int | None = None) -> dict:
    s = get_settings()
    size = size or s.universe_size
    q = s.quote_asset
    spot_info = await client.exchange_info("spot")
    trading = {x["baseAsset"]: x["symbol"] for x in spot_info["symbols"]
               if x["quoteAsset"] == q and x["status"] == "TRADING" and x.get("isSpotTradingAllowed", True)}
    try:
        fut_info = await client.exchange_info("perp")
        perps = {x["baseAsset"] for x in fut_info["symbols"]
                 if x["quoteAsset"] == q and x["contractType"] == "PERPETUAL" and x["status"] == "TRADING"}
    except ProviderUnavailable:
        perps = set()
    tickers = {t["symbol"]: t for t in await client.ticker_24h_all("spot")}
    mcap: dict[str, float] = {}
    cg_ok = True
    cg = ResilientClient("coingecko", [s.coingecko_rest], 25)
    try:
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100, "page": 1}
        if s.coingecko_api_key:
            params["x_cg_demo_api_key"] = s.coingecko_api_key
        for c in await cg.get("/coins/markets", params, retries=2):
            sym = c["symbol"].upper()
            if sym not in mcap and c.get("market_cap"):
                mcap[sym] = float(c["market_cap"])
    except Exception as e:  # noqa: BLE001  (CoinGecko is optional)
        cg_ok = False
        log.warning("coingecko_unavailable", error=str(e))
    finally:
        await cg.close()

    pool = (set(list(mcap)[:80]) | set(s.candidate_universe)) & set(trading)
    pool -= EXCLUDE
    vol, spread = {}, {}
    for b in pool:
        t = tickers.get(trading[b])
        if not t:
            continue
        vol[b] = float(t["quoteVolume"])
        bid, ask = float(t.get("bidPrice") or 0), float(t.get("askPrice") or 0)
        spread[b] = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else 999.0
    pool = {b for b in pool if vol.get(b, 0) > 5_000_000}  # minimum $5M daily quote volume
    sc_m = _rank_score({b: mcap.get(b, 0.0) for b in pool})
    sc_v = _rank_score({b: vol[b] for b in pool})
    sc_s = _rank_score({b: spread[b] for b in pool}, higher_better=False)
    rows = []
    for b in pool:
        comp = {"market_cap": sc_m.get(b, 0) if cg_ok else 0.0, "volume": sc_v.get(b, 0),
                "perp": 1.0 if b in perps else 0.0, "spread": sc_s.get(b, 0),
                "candidate_prior": 1.0 if b in s.candidate_universe else 0.0}
        w = dict(WEIGHTS)
        if not cg_ok:
            w["volume"] += w.pop("market_cap")
            comp.pop("market_cap")
        score = sum(w[k] * comp[k] for k in comp)
        rows.append({"base": b, "symbol": trading[b], "score": round(score, 4), "components": comp,
                     "market_cap_usd": mcap.get(b), "quote_volume_24h": vol[b], "spread_bps": round(spread[b], 3),
                     "has_perp": b in perps})
    rows.sort(key=lambda r: -r["score"])
    selected = [r["base"] for r in rows[:size]]
    return {"selected": selected, "rankings": rows[: size * 2], "coingecko_ok": cg_ok, "weights": WEIGHTS}
