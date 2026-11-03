"""Order book, derivatives and order-flow analytics.

Every output is tagged `observed` (direct exchange data) or `estimate` (model/inference)."""
import numpy as np


def book_metrics(depth: dict, notional_probe: float = 100_000.0) -> dict:
    bids, asks = depth.get("bids") or [], depth.get("asks") or []
    if not bids or not asks:
        return {"available": False}
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    out: dict = {"available": True, "observed": True, "best_bid": bb, "best_ask": ba, "mid": mid,
                 "spread_bps": (ba - bb) / mid * 1e4}
    for pct in (0.1, 0.5, 1.0):
        bd = sum(p * q for p, q in bids if p >= mid * (1 - pct / 100))
        ad = sum(p * q for p, q in asks if p <= mid * (1 + pct / 100))
        out[f"bid_depth_{pct}pct"] = bd
        out[f"ask_depth_{pct}pct"] = ad
        out[f"imbalance_{pct}pct"] = (bd - ad) / (bd + ad) if bd + ad > 0 else 0.0
    # walls: levels whose size > mean + 3 sd within the fetched book
    walls = []
    for side, lv in (("bid", bids), ("ask", asks)):
        q = np.array([x[1] * x[0] for x in lv])
        if len(q) < 10:
            continue
        thr = q.mean() + 3 * q.std()
        walls += [{"side": side, "price": p, "notional": p * qty} for (p, qty), n in zip(lv, q) if n > thr]
    out["walls"] = sorted(walls, key=lambda w: -w["notional"])[:6]
    out["slippage_buy_bps"] = walk_book(asks, notional_probe, mid)
    out["slippage_sell_bps"] = walk_book(bids, notional_probe, mid)
    out["probe_notional"] = notional_probe
    out["book_levels"] = min(len(bids), len(asks))
    return out


def walk_book(levels: list[tuple[float, float]], notional: float, mid: float) -> float | None:
    """Average fill price impact (bps vs mid) to execute `notional` against the visible book.
    Returns None if visible depth is insufficient (true impact unknown)."""
    rem, cost, qty = notional, 0.0, 0.0
    for p, q in levels:
        take = min(rem, p * q)
        cost += take
        qty += take / p
        rem -= take
        if rem <= 1e-9:
            break
    if rem > 1e-9 or qty == 0:
        return None
    return abs(cost / qty - mid) / mid * 1e4


def derivatives_metrics(snap: dict | None, oi_hist: list[dict], funding_hist: list[float],
                        price_change_pct: dict[str, float]) -> dict:
    """snap: latest premium index/OI; oi_hist: [{'ts','oi','oi_value'}] 5m; funding_hist: past rates."""
    if not snap:
        return {"available": False}
    out: dict = {"available": True, "observed_fields": ["funding_rate", "mark_price", "index_price",
                                                        "open_interest", "long_short_ratio", "taker_ratio"]}
    fr = snap.get("funding_rate")
    out["funding_rate"] = fr
    if fr is not None:
        out["funding_annualized_pct"] = fr * 3 * 365 * 100  # 8h funding interval (verify per symbol)
        if len(funding_hist) >= 20:
            arr = np.array(funding_hist[-90:])
            sd = arr.std()
            out["funding_z"] = float((fr - arr.mean()) / sd) if sd > 0 else 0.0
    mp, spot = snap.get("mark_price"), snap.get("spot_price")
    if mp and spot:
        out["basis_bps"] = (mp - spot) / spot * 1e4
    out["long_short_ratio"] = snap.get("long_short_ratio")
    out["taker_buy_sell_ratio"] = snap.get("taker_buy_sell_ratio")
    out["open_interest"] = snap.get("open_interest")
    if oi_hist:
        cur = oi_hist[-1]["oi"]
        for label, bars in (("1h", 12), ("4h", 48), ("24h", 288)):
            if len(oi_hist) > bars:
                prev = oi_hist[-1 - bars]["oi"]
                out[f"oi_change_{label}_pct"] = (cur / prev - 1) * 100 if prev else None
    # Classic OI/price quadrant (interpretation, not observation)
    oc, pc = out.get("oi_change_4h_pct"), price_change_pct.get("4h")
    if oc is not None and pc is not None:
        if pc > 0 and oc > 0:
            q = "new longs entering (trend support)"
        elif pc > 0 and oc < 0:
            q = "short covering (weaker rally)"
        elif pc < 0 and oc > 0:
            q = "new shorts entering (trend support)"
        else:
            q = "long liquidation / deleveraging"
        out["oi_price_regime"] = {"label": q, "type": "interpretation"}
    z = out.get("funding_z")
    sq = []
    if fr is not None and oc is not None:
        if (fr < 0 or (z is not None and z < -2)) and oc > 1 and (pc or 0) > 0:
            sq.append("short_squeeze_conditions")
        if (fr > 0.0003 or (z is not None and z > 2)) and oc > 1 and (pc or 0) < 0:
            sq.append("long_squeeze_conditions")
    out["squeeze_flags"] = sq
    out["funding_extreme"] = bool(z is not None and abs(z) > 2.5) or bool(fr is not None and abs(fr) > 0.0005)
    if snap.get("mark_price"):
        out["liquidation_bands_estimate"] = estimated_liquidation_bands(float(snap["mark_price"]))
    return out


def estimated_liquidation_bands(price: float, leverages=(10, 25, 50, 100), mmr: float = 0.005) -> list[dict]:
    """Rough liquidation price bands for positions opened near the current price, assuming isolated margin
    and a flat maintenance margin rate. ESTIMATE ONLY: real liquidation depends on exchange brackets,
    entry prices and margin mode, none of which are public per position."""
    out = []
    for lev in leverages:
        long_liq = price * (1 - 1 / lev + mmr)
        short_liq = price * (1 + 1 / lev - mmr)
        out.append({"leverage": lev, "long_liq": long_liq, "short_liq": short_liq, "type": "estimate"})
    return out
