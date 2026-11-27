import time
from datetime import UTC, datetime

import httpx
import pytest

from app.agents import market_extras as mx


@pytest.fixture(autouse=True)
def _fresh_cache():
    mx._cache.clear()
    yield
    mx._cache.clear()


def _fake(routes: dict):
    """A stand-in for mx._get: the first route whose key is in the URL answers (a callable gets the params)."""
    calls = []

    async def get(url, params=None):
        calls.append((url, params))
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val(params) if callable(val) else val
        raise AssertionError(f"unexpected url {url}")
    get.calls = calls
    return get


def _http_error(code: int, text: str = "") -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, text=text, request=req))


def _kline(i: int, o: float, c: float, vol: float, buy: float, closed: bool = True) -> list:
    t = int(time.time() * 1000) - (200 - i) * 3_600_000
    close_t = t + 3_599_999 if closed else int(time.time() * 1000) + 60_000
    return [t, str(o), str(max(o, c)), str(min(o, c)), str(c), str(vol), close_t, "0", 1, str(buy), "0", "0"]


# ---------------------------------------------------------------------------------------------- slippage

def test_slippage_walks_levels_and_includes_half_the_spread():
    asks = [(101.0, 10.0), (102.0, 10.0)]  # $1010 then $1020 of liquidity
    assert mx.slippage_bps(asks, 505.0, 100.0) == 100.0                      # all at 101: 1% from mid
    # $2030 takes everything: 20 units, average 101.5
    assert mx.slippage_bps(asks, 2030.0, 100.0) == 150.0


def test_slippage_for_sells_is_a_positive_cost_too():
    bids = [(99.0, 10.0), (98.0, 10.0)]
    assert mx.slippage_bps(bids, 99.0, 100.0) == 100.0


def test_slippage_none_when_book_too_thin_or_bad_inputs():
    asks = [(101.0, 1.0)]
    assert mx.slippage_bps(asks, 1000.0, 100.0) is None
    assert mx.slippage_bps(asks, 0.0, 100.0) is None
    assert mx.slippage_bps(asks, 10.0, 0.0) is None
    assert mx.slippage_bps([], 10.0, 100.0) is None


async def test_depth_slippage(monkeypatch):
    book = {"bids": [["99.9", "100"], ["99.0", "1000"]], "asks": [["100.1", "100"], ["101.0", "1000"]]}
    monkeypatch.setattr(mx, "_get", _fake({"/fapi/v1/depth": book}))
    d = await mx.depth_slippage("solusdt", 5000)
    assert d["source"] == "binance_futures" and d["mid"] == 100.0 and d["spread_bps"] == 20.0
    assert d["buy_slippage_bps"] == 10.0 and d["sell_slippage_bps"] == 10.0
    assert d["unavailable"] == []
    thin = await mx.depth_slippage("SOLUSDT", 10_000_000)
    assert thin["buy_slippage_bps"] is None and len(thin["unavailable"]) == 2


async def test_depth_falls_back_to_spot_and_reports_failure(monkeypatch):
    book = {"bids": [["9", "10"]], "asks": [["11", "10"]]}
    monkeypatch.setattr(mx, "_get", _fake({"/fapi/v1/depth": _http_error(400), "/api/v3/depth": book}))
    assert (await mx.depth_slippage("XUSDT", 50))["source"] == "binance_spot"
    mx._cache.clear()
    monkeypatch.setattr(mx, "_get", _fake({"depth": httpx.ConnectError("down")}))
    d = await mx.depth_slippage("XUSDT", 50)
    assert "buy_slippage_bps" not in d and "down" in d["unavailable"][0]


# --------------------------------------------------------------------------------------------- order flow

def test_flow_window_cvd_and_divergence():
    rows = [_kline(i, 100 + i, 101 + i, 10.0, 3.0) for i in range(10)]  # price up, 30% taker buying
    w = mx.flow_window(rows)
    assert w["cvd_base"] == -40.0 and w["cvd_pct_of_volume"] == -40.0 and w["taker_buy_pct"] == 30.0
    assert w["price_change_pct"] == 10.0 and w["divergence"] == "price up, CVD down"
    down_buying = [_kline(i, 100 - i, 99 - i, 10.0, 7.0) for i in range(10)]
    assert mx.flow_window(down_buying)["divergence"] == "price down, CVD up"
    assert mx.flow_window([_kline(0, 100, 105, 10, 8)])["divergence"] == "aligned"
    assert mx.flow_window([_kline(0, 100, 100.1, 10, 9)])["divergence"] == "flat"   # price barely moved
    assert mx.flow_window([_kline(0, 100, 105, 10, 5.05)])["divergence"] == "flat"  # balanced flow


async def test_order_flow_windows_and_drops_the_open_bar(monkeypatch):
    rows = [_kline(i, 100, 100.5, 10.0, 6.0) for i in range(97)]
    rows[-1] = _kline(96, 100, 200, 1000.0, 0.0, closed=False)  # forming bar must not count
    fake = _fake({"/fapi/v1/klines": rows})
    monkeypatch.setattr(mx, "_get", fake)
    f = await mx.order_flow("SOLUSDT", "1h")
    assert f["source"] == "binance_futures" and f["unavailable"] == []
    assert f["last_24"]["bars"] == 24 and f["last_96"]["bars"] == 96
    assert f["last_24"]["cvd_base"] == 48.0 and f["last_96"]["taker_buy_pct"] == 60.0
    await mx.order_flow("SOLUSDT", "1h")
    assert len(fake.calls) == 1  # cached


async def test_order_flow_spot_fallback_bad_tf_and_failure(monkeypatch):
    rows = [_kline(i, 100, 100, 10.0, 5.0) for i in range(30)]
    monkeypatch.setattr(mx, "_get", _fake({"/fapi/v1/klines": _http_error(400, "Invalid symbol"),
                                           "/api/v3/klines": rows}))
    f = await mx.order_flow("NEWUSDT", "4h")
    assert f["source"] == "binance_spot" and f["last_24"]["divergence"] == "flat"
    assert "fewer than 96" in f["unavailable"][0]
    assert "not supported" in (await mx.order_flow("NEWUSDT", "3m"))["unavailable"][0]
    monkeypatch.setattr(mx, "_get", _fake({"klines": _http_error(500, "boom")}))
    bad = await mx.order_flow("OTHERUSDT", "1h")
    assert "HTTP 500" in bad["unavailable"][0] and "last_24" not in bad


# ------------------------------------------------------------------------------------------------ funding

BINANCE_PREM = {"symbol": "SOLUSDT", "markPrice": "100", "lastFundingRate": "0.0001", "nextFundingTime": 1794412800000}
BYBIT = {"retCode": 0, "retMsg": "OK", "result": {"list": [{
    "symbol": "SOLUSDT", "markPrice": "100", "fundingRate": "0.0003", "fundingIntervalHour": "8",
    "openInterestValue": "2000000", "singleOpenInterestValue": "1000000", "nextFundingTime": "1794412800000"}]}}
OKX_FR = {"code": "0", "data": [{"instId": "SOL-USDT-SWAP", "fundingRate": "-0.0001", "fundingTime": "1794412800000",
                                 "nextFundingTime": "1794427200000"}]}  # 4h interval
OKX_OI = {"code": "0", "data": [{"oiUsd": "1000000"}]}


async def test_funding_across_three_venues(monkeypatch):
    monkeypatch.setattr(mx, "_get", _fake({
        "premiumIndex": BINANCE_PREM, "/openInterest": {"openInterest": "20000"}, "fundingInfo": [],
        "bybit": BYBIT, "funding-rate": OKX_FR, "open-interest": OKX_OI}))
    f = await mx.funding_across("SOLUSDT")
    assert f["unavailable"] == []
    v = {r["venue"]: r for r in f["venues"]}
    assert v["binance"]["open_interest_usd"] == 2_000_000 and v["binance"]["annualized_pct"] == 10.95
    assert v["bybit"]["open_interest_usd"] == 1_000_000 and v["bybit"]["annualized_pct"] == 32.85  # single side
    assert v["okx"]["interval_h"] == 4.0 and v["okx"]["funding_8h_pct"] == -0.02 and v["okx"]["annualized_pct"] == -21.9
    assert f["total_open_interest_usd"] == 4_000_000 and v["binance"]["oi_share_pct"] == 50.0
    assert f["oi_weighted_annualized_pct"] == pytest.approx((10.95 * 2 + 32.85 - 21.9) / 4, abs=0.01)
    assert f["spread_annualized_pp"] == 54.75
    assert f["most_crowded"] == {"venue": "bybit", "annualized_pct": 32.85, "paying": "longs"}


async def test_funding_uses_binance_interval_and_survives_missing_venues(monkeypatch):
    monkeypatch.setattr(mx, "_get", _fake({
        "premiumIndex": BINANCE_PREM, "/openInterest": {"openInterest": "10"},
        "fundingInfo": [{"symbol": "SOLUSDT", "fundingIntervalHours": 4}],
        "bybit": {"retCode": 10001, "retMsg": "symbol invalid", "result": {}},
        "okx.com": httpx.ConnectError("timeout")}))
    f = await mx.funding_across("SOLUSDT")
    assert [r["venue"] for r in f["venues"]] == ["binance"]
    assert f["venues"][0]["interval_h"] == 4.0 and f["venues"][0]["funding_8h_pct"] == 0.02
    assert len(f["unavailable"]) == 2 and f["spread_annualized_pp"] == 0.0


async def test_funding_okx_uses_the_base_without_the_1000_prefix(monkeypatch):
    fake = _fake({"binance.com": _http_error(400), "bybit": _http_error(503), "funding-rate": OKX_FR,
                  "open-interest": OKX_OI})
    monkeypatch.setattr(mx, "_get", fake)
    f = await mx.funding_across("1000PEPEUSDT")
    assert [r["venue"] for r in f["venues"]] == ["okx"]
    assert any(p and p.get("instId") == "PEPE-USDT-SWAP" for _, p in fake.calls)
    monkeypatch.setattr(mx, "_get", _fake({"": httpx.ConnectError("x")}))
    mx._cache.clear()
    none = await mx.funding_across("SOLUSDT")
    assert none["venues"] == [] and len(none["unavailable"]) == 3


# ------------------------------------------------------------------------------------------------ options

NOW = datetime(2026, 11, 11, 8, tzinfo=UTC)


def _opt(name: str, oi: float, iv: float, und: float) -> dict:
    return {"instrument_name": name, "open_interest": oi, "mark_iv": iv, "underlying_price": und}


BOOK = [
    _opt("BTC-12NOV26-74000-C", 500, 30, 74000),     # 1 day: too near, skipped for term structure
    _opt("BTC-14NOV26-74000-C", 10, 36, 74100), _opt("BTC-14NOV26-74000-P", 20, 38, 74100),
    _opt("BTC-14NOV26-80000-C", 10, 50, 74100),
    _opt("BTC-11DEC26-75000-C", 30, 42, 74600), _opt("BTC-11DEC26-75000-P", 40, 44, 74600),
    _opt("BTC-25DEC26-75000-C", 5, 60, 74800),
]


def test_option_stats_put_call_and_term_structure():
    s = mx.option_stats(BOOK, NOW)
    assert s["put_oi"] == 60 and s["call_oi"] == 555 and s["put_call_oi_ratio"] == round(60 / 555, 3)
    ts = s["term_structure"]
    assert ts["near"]["expiry"] == "2026-11-14" and ts["near"]["strike"] == 74000 and ts["near"]["atm_iv"] == 37.0
    assert ts["one_month"]["expiry"] == "2026-12-11" and ts["one_month"]["atm_iv"] == 43.0
    assert ts["month_minus_near_pts"] == 6.0 and ts["shape"].startswith("contango")


def test_option_stats_handles_odd_names_and_empty_book():
    assert mx._expiry("BTC-7NOV26-1000-P") == datetime(2026, 11, 7, 8, tzinfo=UTC)
    assert mx._expiry("BTC-PERPETUAL") is None and mx._expiry("BTC-1XYZ26-1-C") is None
    assert mx.option_stats([], NOW) == {"put_oi": 0, "call_oi": 0, "put_call_oi_ratio": None}


async def test_options_market(monkeypatch):
    t = int(time.time() * 1000)
    bars = [[t - h * 3_600_000, 50, 52 + h, 48 - h, 50 - h * 0.1] for h in range(48, -1, -1)]
    monkeypatch.setattr(mx, "_get", _fake({"get_volatility_index_data": {"result": {"data": bars}},
                                           "get_book_summary_by_currency": {"result": BOOK}}))
    o = await mx.options_market()
    assert "altcoins" in o["scope"] and o["unavailable"] == []
    assert o["btc"]["dvol"] == {"value": 50.0, "change_24h_pts": 2.4, "high_48h": 100.0, "low_48h": 0.0}
    assert o["eth"]["put_call_oi_ratio"] is not None


async def test_options_market_reports_failures(monkeypatch):
    monkeypatch.setattr(mx, "_get", _fake({"deribit": _http_error(429, "too many")}))
    o = await mx.options_market()
    assert len(o["unavailable"]) == 4 and o["btc"] == {} and "HTTP 429" in o["unavailable"][0]
