"""Forecast hub: turns the team's views into one number with maths, attaches the evidence, and books every
forecast in the ledger so it can be graded later.

Used by both brains (LLM session and rule-based room) so the Desk always shows the same kind of answer:
team probability, past odds of the setup, the coin's quant rank, the market regime and the case against.
"""
from app.logging import log
from app.quant import brain as quant
from app.quant import ledger, odds


def quant_p(qc: dict | None) -> float | None:
    """The trend rank as a deliberately weak prior on P(up): 50% ± 10 points. Its weight is earned in the ledger."""
    if not qc:
        return None
    return round(0.5 + (qc["score"] / 100 - 0.5) * 0.2, 3)


def regime_word(snap: dict) -> str | None:
    return (snap.get("regime") or {}).get("regime")


async def build(symbol: str, tf: str, snap: dict, items: list[dict], best: dict | None,
                case_against: str | None = None) -> dict:
    qd = quant.peek() or await quant.get()
    qc = quant.coin(symbol)
    items = [i for i in items if i.get("p_up") is not None]
    if qc:
        items.append({"source": "quant", "p_up": quant_p(qc), "brain": "model"})
    try:
        w = await ledger.weights()
    except Exception as e:  # noqa: BLE001 - no record yet: equal weights
        log.warning("ledger_weights_failed", error=str(e))
        w = {}
    team = ledger.combine(items, w)
    try:
        od = await odds.setup_odds(best["strategy"], best["direction"], tf, regime_word(snap)) if best else None
    except Exception as e:  # noqa: BLE001
        log.warning("setup_odds_failed", error=str(e))
        od = None
    if od is not None:
        od["setup"] = f"{best['strategy'].replace('_', ' ')} {best['direction']}"
    proof = (qd or {}).get("proof", {})
    return {"items": items, "forecast": team, "odds": od, "quant": qc, "case_against": case_against,
            "market": (qd or {}).get("regime"), "horizon_candles": ledger.HORIZON,
            "quant_proof": {"weeks": proof.get("weeks"), **(proof.get("long_short") or {})} if proof else None}


async def book(session_id: str, symbol: str, tf: str, ref_price: float, hub: dict) -> int:
    items = hub["items"] + [{"source": "team", "p_up": hub["forecast"]["p_up"], "brain": "team"}]
    m = hub.get("market") or {}
    try:
        return await ledger.record(session_id, symbol, tf, ref_price, items, m.get("btc_trend"))
    except Exception as e:  # noqa: BLE001 - booking failure must not lose the analysis
        log.warning("ledger_book_failed", error=str(e))
        return 0


def plain(hub: dict) -> str:
    """One beginner sentence for the team probability."""
    f = hub["forecast"]
    p = f["p_up"]
    n = len(f["parts"])
    side = "higher" if p >= 0.5 else "lower"
    q = p if p >= 0.5 else 1 - p
    strength = "a coin flip" if q < 0.53 else "a slight lean" if q < 0.58 else "a clear lean" if q < 0.65 else "a strong lean"
    return (f"Team forecast: {q:.0%} chance price is {side} in {hub['horizon_candles']} candles — {strength}. "
            f"{n} forecasters, weighted by their track records.")
