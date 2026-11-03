"""Signal cards and the analysed top list for the system page.

Deterministic, so they are always there; the intelligent desk (LLM agents with tools) refines the ranking and the
reasons when it is available. A signal card is a Binance-style card for one candidate trade of the system:
  live     a setup the risk engine ordered for the next daily open — selectable: the user confirms the fill and it
           becomes a running trade in their portfolio
  skipped  a live setup the risk engine refused, with the reason (limits, tiny ticket, costs)
  near     a coin within 5 % of its 20-day high — not selectable: the rule enters only after the daily close above it
  blocked  the same while BTC is below its 200-day average
"""
from app.agents import desk_tools
from app.risk import futures as fut

RULE = ("Closed above its 20-day high while BTC is above its 200-day average. Buy at the next daily open; stop 2.5 ATR "
        "below; sell a third at 2R; trail the rest 3 ATR below the close; 60-day limit.")


def top_list(cards: dict[str, dict], n: int = 8) -> list[dict]:
    ranked = sorted(cards.values(), key=desk_tools._sort_key)[:n]
    return [{**c, "rank": i + 1, "reason": desk_tools.reason(c)} for i, c in enumerate(ranked)]


def _base(sym: str, cards: dict, venue: str) -> dict:
    c = cards.get(sym, {"symbol": sym})
    return {"symbol": sym, "venue": venue, "analysis": c, "reason": desk_tools.reason(c) if "verdict" in c else None,
            "safe_leverage": c.get("safe_leverage"), "rule": RULE}


def signal_cards(acct: dict, cards: dict[str, dict]) -> list[dict]:
    venue = acct["venue"]
    tickets = {t["symbol"]: t for t in (acct.get("futures") or {}).get("tickets", [])}
    out = []
    for o in acct["next_orders"]:
        t = tickets.get(o["symbol"])
        refused = t["refused"] if t else None
        out.append(_base(o["symbol"], cards, venue) | {
            "state": "live", "selectable": not refused, "entry_ref": o["close"], "stop": o["stop"],
            "target": o["target_2r"], "stop_pct": o["stop_pct"], "size_usd": o["size_usd"], "risk_usd": o["risk_usd"],
            "qty": t["qty"] if t else round(o["size_usd"] / o["close"], 8),
            "leverage": t["leverage_set"] if t else 1,
            "safe_leverage": fut.safe_leverage(o["stop_pct"] / 100, o["symbol"]),
            "margin_usd": t["margin_usd"] if t else o["size_usd"],
            "liquidation": t["liquidation"] if t else None,
            "liquidation_pct_below": t["liquidation_pct_below"] if t else None,
            "ticket": t, "warnings": t["warnings"] if t else [], "why": refused})
    for w in acct["watchlist"]:
        if w.get("action") == "skip":
            out.append(_base(w["symbol"], cards, venue) | {"state": "skipped", "selectable": False, "entry_ref": w["close"],
                                                            "why": f"Live setup, but skipped: {w.get('why')}."})
        else:
            blocked = "blocked" in str(w.get("action", ""))
            level = w.get("breakout_level")
            out.append(_base(w["symbol"], cards, venue) | {
                "state": "blocked" if blocked else "near", "selectable": False, "entry_ref": w["close"],
                "breakout_level": level, "distance_pct": w.get("distance_pct"),
                "why": ("Blocked: BTC is below its 200-day average, the system buys nothing." if blocked else
                        f"No entry yet: the rule needs a daily close above {level:.6g} first." if level else
                        "No entry yet.")})
    order = {"live": 0, "skipped": 1, "near": 2, "blocked": 3}
    out.sort(key=lambda s: (order[s["state"]], s.get("distance_pct") or 0))
    return out[:8]
