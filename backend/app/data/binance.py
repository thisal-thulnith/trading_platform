"""Binance public market data (no API key). Endpoints verified against live API on 2026-11-01:
spot  /api/v3/klines, /api/v3/depth, /api/v3/ticker/24hr, /api/v3/exchangeInfo
usdm  /fapi/v1/klines, /fapi/v1/premiumIndex, /fapi/v1/openInterest, /fapi/v1/fundingRate,
      /fapi/v1/exchangeInfo, /futures/data/openInterestHist, /futures/data/globalLongShortAccountRatio,
      /futures/data/takerlongshortRatio
"""
from app.config import get_settings
from app.data.http import ResilientClient
from app.data.timeframes import tf_ms

KLINE_FIELDS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
)


def parse_kline(row: list) -> dict:
    return {
        "open_time": int(row[0]),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "quote_volume": float(row[7]),
        "trades": int(row[8]),
        "taker_buy_base": float(row[9]),
        "taker_buy_quote": float(row[10]),
    }


def kline_weight(limit: int) -> int:
    # Spot klines weight is 2 (verified docs, 2024+). Futures: 1/2/5/10 by limit buckets.
    return 2 if limit <= 1000 else 5


class BinanceClient:
    def __init__(self) -> None:
        s = get_settings()
        self.spot = ResilientClient("binance_spot", [s.binance_spot_rest, s.binance_spot_rest_fallback], 5000)
        self.futures = ResilientClient("binance_futures", [s.binance_futures_rest], 1800)

    async def close(self) -> None:
        await self.spot.close()
        await self.futures.close()

    def _c(self, market: str) -> tuple[ResilientClient, str]:
        return (self.spot, "/api/v3") if market == "spot" else (self.futures, "/fapi/v1")

    async def klines(
        self, symbol: str, tf: str, market: str = "spot", limit: int = 1000,
        start_ms: int | None = None, end_ms: int | None = None,
    ) -> list[dict]:
        tf_ms(tf)
        client, prefix = self._c(market)
        params: dict = {"symbol": symbol, "interval": tf, "limit": min(limit, 1000 if market == "spot" else 1500)}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        w = kline_weight(params["limit"]) if market == "spot" else (5 if params["limit"] > 1000 else 2)
        rows = await client.get(f"{prefix}/klines", params, weight=w)
        return [parse_kline(r) for r in rows]

    async def depth(self, symbol: str, limit: int = 100, market: str = "spot") -> dict:
        client, prefix = self._c(market)
        d = await client.get(f"{prefix}/depth", {"symbol": symbol, "limit": limit}, weight=5)
        return {
            "last_update_id": d["lastUpdateId"],
            "bids": [(float(p), float(q)) for p, q in d["bids"]],
            "asks": [(float(p), float(q)) for p, q in d["asks"]],
        }

    async def ticker_24h_all(self, market: str = "spot") -> list[dict]:
        client, prefix = self._c(market)
        return await client.get(f"{prefix}/ticker/24hr", weight=80 if market == "spot" else 40)

    async def exchange_info(self, market: str = "spot") -> dict:
        client, prefix = self._c(market)
        return await client.get(f"{prefix}/exchangeInfo", weight=20 if market == "spot" else 1)

    async def premium_index(self, symbol: str | None = None) -> list[dict] | dict:
        params = {"symbol": symbol} if symbol else None
        return await self.futures.get("/fapi/v1/premiumIndex", params, weight=1 if symbol else 10)

    async def open_interest(self, symbol: str) -> dict:
        return await self.futures.get("/fapi/v1/openInterest", {"symbol": symbol}, weight=1)

    async def funding_history(self, symbol: str, limit: int = 100, start_ms: int | None = None) -> list[dict]:
        params: dict = {"symbol": symbol, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        return await self.futures.get("/fapi/v1/fundingRate", params, weight=1)

    async def futures_data(self, kind: str, symbol: str, period: str = "5m", limit: int = 30) -> list[dict]:
        """kind in openInterestHist | globalLongShortAccountRatio | takerlongshortRatio (last ~30 days only)."""
        assert kind in {"openInterestHist", "globalLongShortAccountRatio", "takerlongshortRatio"}
        return await self.futures.get(
            f"/futures/data/{kind}", {"symbol": symbol, "period": period, "limit": limit}, weight=1
        )
