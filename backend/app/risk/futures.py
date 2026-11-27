"""Binance USDⓈ-M futures view of the system account: the same trades, written as tickets a Binance user places by hand.

Leverage here is a margin tool, not a size tool. The risk engine still decides the position (risk budget / stop
distance); leverage only changes how much margin the position locks and where the liquidation price sits. A ticket is
capped at the largest leverage whose liquidation price stays well beyond the stop, so the stop — not the exchange —
closes a losing trade. Paper only: nothing here is sent to Binance; the user copies the ticket into the app.

Exchange facts (public fapi/v1/exchangeInfo, refreshed hourly; defaults below when it is unreachable):
  lot step, min qty, price tick and min notional per symbol (BTC 50 USDT, ETH 20, most others 5)
Fees: taker 0.05 % per side without the BNB discount, maker 0.02 %. Funding every 8 h, estimated from the last 7 days.
Liquidation of an isolated long, approx.: entry x (1 - 1/L + MMR), MMR = lowest tier (BTC 0.4 %, ETH 0.5 %, others 1 %).
Binance's own liquidation price (mark price, tiered MMR, fees) is the one that counts; this is a conservative estimate.
"""
import math
from dataclasses import asdict, dataclass

import httpx

TAKER = 0.0005
MAKER = 0.0002
LIQ_FEE = 0.0125
MAX_LEVERAGE = 50
LIQ_BUFFER = 1.5  # the liquidation distance must be at least 1.5 x the stop distance (wicks, mark-price gaps)
EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"


@dataclass
class Filters:
    step: float = 0.001
    min_qty: float = 0.001
    tick: float = 0.0001
    min_notional: float = 5.0


DEFAULT_FILTERS = {"BTCUSDT": Filters(0.001, 0.001, 0.1, 50.0), "ETHUSDT": Filters(0.001, 0.001, 0.01, 20.0)}


def mmr(symbol: str) -> float:
    return 0.004 if symbol == "BTCUSDT" else 0.005 if symbol == "ETHUSDT" else 0.01


def liq_distance(leverage: float, symbol: str) -> float:
    """Fall below entry (share of entry) that liquidates an isolated long at this leverage."""
    return 1 / leverage - mmr(symbol)


def liquidation_price(entry: float, leverage: float, symbol: str, side: str = "LONG") -> float:
    """Isolated position: a long is liquidated about 1/leverage below entry, a short the same distance above."""
    d = liq_distance(leverage, symbol)
    return entry * (1 + d) if side == "SHORT" else entry * (1 - d)


def safe_leverage(stop_pct: float, symbol: str) -> int:
    """Largest whole leverage whose liquidation sits at least LIQ_BUFFER x the stop distance below entry."""
    lev = math.floor(1 / (stop_pct * LIQ_BUFFER + mmr(symbol)))
    return int(max(1, min(MAX_LEVERAGE, lev)))


def round_step(x: float, step: float) -> float:
    """Round DOWN to the exchange step (never buy more than the plan allows)."""
    if step <= 0:
        return x
    n = math.floor(x / step + 1e-9)
    return round(n * step, 10)


async def fetch_filters(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, Filters]:
    r = await client.get(EXCHANGE_INFO)
    r.raise_for_status()
    want = set(symbols)
    out = {}
    for s in r.json()["symbols"]:
        if s["symbol"] in want and s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING":
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"]] = Filters(float(f["LOT_SIZE"]["stepSize"]), float(f["LOT_SIZE"]["minQty"]),
                                       float(f["PRICE_FILTER"]["tickSize"]),
                                       float(f.get("MIN_NOTIONAL", {}).get("notional", 5.0)))
    return out


def ticket(order: dict, leverage: int, filters: Filters | None, funding_apr_pct: float | None = None,
           partial_frac: float = 1 / 3) -> dict:
    """A Binance-style ticket for one planned long. `order` is a system.account next_orders row (close, stop,
    target_2r, size_usd, risk_usd, stop_pct)."""
    sym, px = order["symbol"], float(order["close"])
    f = filters or DEFAULT_FILTERS.get(sym) or Filters()
    stop_pct = float(order["stop_pct"]) / 100
    safe = safe_leverage(stop_pct, sym)
    lev = int(max(1, min(leverage, safe)))
    qty = round_step(float(order["size_usd"]) / px, f.step)
    notional = qty * px
    tp_qty = round_step(qty * partial_frac, f.step)
    warnings = []
    if leverage > safe:
        warnings.append(f"At {leverage}x this trade would be liquidated at {liquidation_price(px, leverage, sym):.6g} "
                        f"({liq_distance(leverage, sym) * 100:.1f} % below entry), before its stop "
                        f"({stop_pct * 100:.1f} % below). Leverage capped at {safe}x so the stop, not Binance, "
                        f"closes a loser.")
    refused = None
    if qty < f.min_qty or notional < f.min_notional:
        refused = (f"order of {notional:.2f} USDT is under the {f.min_notional:g} USDT minimum for {sym}; the account "
                   f"is too small for this coin at this risk")
    if tp_qty < f.min_qty:
        tp_qty = None
    t = {"symbol": sym, "contract": f"{sym} Perpetual", "side": "BUY / LONG", "margin_mode": "Isolated",
         "leverage_set": lev, "leverage_requested": leverage, "leverage_safe_max": safe,
         "qty": qty, "notional_usd": round(notional, 2), "margin_usd": round(notional / lev, 2),
         "entry_type": "MARKET at the next daily open (or LIMIT at the open price)", "entry_ref": px,
         "stop": {"type": "STOP_MARKET", "trigger": round_step(float(order["stop"]), f.tick), "qty": qty,
                  "reduce_only": True, "pct_below": round(stop_pct * 100, 1)},
         "take_profit": {"type": "TAKE_PROFIT_MARKET", "trigger": round_step(float(order["target_2r"]), f.tick),
                         "qty": tp_qty, "reduce_only": True, "note": "a third of the position; then move the stop to "
                         "entry and trail 3 ATR"} if tp_qty else None,
         "liquidation": round(liquidation_price(px, lev, sym), 8),
         "liquidation_pct_below": round(liq_distance(lev, sym) * 100, 1),
         "risk_usd": float(order["risk_usd"]),
         "fees_usd_round_trip": round(2 * TAKER * notional, 2),
         "funding_usd_per_day": round(notional * funding_apr_pct / 100 / 365, 3) if funding_apr_pct is not None else None,
         "funding_apr_pct": funding_apr_pct, "warnings": warnings, "refused": refused}
    return t


def position_view(symbol: str, entry: float, stop: float, risk_per_unit: float, qty: float, now: float,
                  leverage: int) -> dict:
    """Leverage and liquidation for an OPEN long. The cap uses the stop distance at entry (risk_per_unit / entry): the
    liquidation price is fixed by the entry, so a position already under water must not be judged from today's price."""
    stop_pct = risk_per_unit / entry if entry > 0 else 1.0
    safe = safe_leverage(stop_pct, symbol)
    lev = int(max(1, min(leverage, safe)))
    liq = liquidation_price(entry, lev, symbol)
    return {"symbol": symbol, "leverage_set": lev, "leverage_safe_max": safe, "liquidation": round(liq, 8),
            "liquidation_below_stop": bool(liq < stop), "notional_usd": round(qty * now, 2),
            "margin_usd": round(qty * now / lev, 2)}


def venue_rules() -> dict:
    return {"venue": "Binance USDⓈ-M perpetual futures", "taker_fee_pct": TAKER * 100, "maker_fee_pct": MAKER * 100,
            "liquidation_fee_pct": LIQ_FEE * 100, "max_leverage": MAX_LEVERAGE, "liq_buffer": LIQ_BUFFER,
            "mmr_pct": {"BTCUSDT": 0.4, "ETHUSDT": 0.5, "others": 1.0},
            "liquidation_formula": "isolated long: entry x (1 - 1/leverage + maintenance margin rate), approximate",
            "funding": "paid every 8 hours while the position is open; estimate from the last 7 days",
            "filters": {k: asdict(v) for k, v in DEFAULT_FILTERS.items()}}
