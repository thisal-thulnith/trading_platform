"""Transparent position sizing and liquidation estimates. All formulas are returned alongside results."""
from dataclasses import asdict, dataclass

# Conservative first-tier maintenance margin assumptions (Binance USDⓈ-M leverage brackets require an
# authenticated endpoint; public FAQ shows BTC/ETH tier-1 MMR 0.4–0.5%, most alts 0.5–1.0%+).
DEFAULT_MMR = {"BTCUSDT": 0.004, "ETHUSDT": 0.005}
ALT_MMR = 0.01


@dataclass
class RiskProfile:
    equity: float
    risk_per_trade_pct: float = 1.0
    max_leverage: float = 3.0
    max_position_pct: float = 25.0  # cap on notional as % of equity * leverage budget
    max_open_positions: int = 5
    max_portfolio_risk_pct: float = 5.0
    taker_fee: float = 0.0005
    slippage_bps: float = 2.0
    market: str = "perp"  # perp | spot


def mmr_for(symbol: str) -> float:
    return DEFAULT_MMR.get(symbol, ALT_MMR)


def liquidation_price_isolated(side: str, entry: float, leverage: float, mmr: float, maint_amount: float = 0.0,
                               qty: float = 1.0) -> float | None:
    """Binance USDⓈ-M isolated, one-way mode, single position (official FAQ formula):
    LP = (WB + cum - side*qty*EP) / (qty*MMR - side*qty), WB = isolated margin = qty*EP/leverage.
    Ignores fees/funding accrued. Returns None for leverage <= 1 long (no liquidation in practice)."""
    s = 1 if side == "LONG" else -1
    wb = qty * entry / leverage
    den = qty * mmr - s * qty
    if den == 0:
        return None
    lp = (wb + maint_amount - s * qty * entry) / den
    return lp if lp > 0 else None


def size_position(p: RiskProfile, symbol: str, side: str, entry: float, stop: float,
                  open_risk_pct: float = 0.0, open_positions: int = 0) -> dict:
    """Risk-based sizing: qty such that loss at stop (incl. round-trip fees and slippage) = equity*risk%."""
    if entry <= 0 or stop <= 0 or entry == stop:
        return {"ok": False, "reason": "invalid entry/stop"}
    if (side == "LONG" and stop >= entry) or (side == "SHORT" and stop <= entry):
        return {"ok": False, "reason": "stop on wrong side of entry"}
    stop_dist = abs(entry - stop)
    slip = entry * p.slippage_bps / 1e4
    per_unit_loss = stop_dist + 2 * slip + entry * p.taker_fee + stop * p.taker_fee
    remaining_risk_pct = max(0.0, min(p.risk_per_trade_pct, p.max_portfolio_risk_pct - open_risk_pct))
    reasons = []
    if open_positions >= p.max_open_positions:
        return {"ok": False, "reason": f"max open positions ({p.max_open_positions}) reached"}
    if remaining_risk_pct <= 0:
        return {"ok": False, "reason": "portfolio risk budget exhausted"}
    risk_amount = p.equity * remaining_risk_pct / 100
    qty = risk_amount / per_unit_loss
    notional = qty * entry
    max_lev = p.max_leverage if p.market == "perp" else 1.0
    # Spot: notional can never exceed equity. Perp: notional <= equity × max_leverage × max_position_pct.
    cap = p.equity if p.market == "spot" else p.equity * max_lev * p.max_position_pct / 100
    if notional > cap:
        reasons.append(f"notional capped by leverage/position limits ({cap:,.2f})")
        qty = cap / entry
        notional = cap
    lev_used = notional / p.equity if p.equity else 0
    mmr = mmr_for(symbol)
    liq = (liquidation_price_isolated(side, entry, max(lev_used, 1e-9), mmr) if p.market == "perp" and lev_used > 1
           else None)
    liq_buffer = None
    if liq:
        liq_buffer = abs(entry - liq) / stop_dist
        if (side == "LONG" and liq >= stop) or (side == "SHORT" and liq <= stop):
            return {"ok": False, "reason": "estimated liquidation price is before the stop — reduce leverage",
                    "liquidation_estimate": liq}
    actual_risk = qty * per_unit_loss
    return {
        "ok": True, "qty": qty, "notional": notional, "leverage_used": lev_used,
        "margin_required": notional / max(lev_used, 1) if p.market == "perp" else notional,
        "risk_amount": actual_risk, "risk_pct": actual_risk / p.equity * 100,
        "fees_estimate": notional * p.taker_fee + qty * stop * p.taker_fee,
        "slippage_estimate": 2 * slip * qty, "stop_distance_pct": stop_dist / entry * 100,
        "liquidation_estimate": liq, "liquidation_vs_stop_multiple": liq_buffer, "mmr_assumed": mmr,
        "notes": reasons,
        "formula": "qty = equity × risk% ÷ (|entry−stop| + 2×slippage + entry×fee + stop×fee); capped by "
                   "equity × max_leverage. Leverage does not change the risk at the stop — only margin usage.",
        "profile": asdict(p),
    }


def rr(entry: float, stop: float, target: float, fee: float = 0.0005, slip_bps: float = 2.0) -> float:
    """Reward/risk net of round-trip fees and slippage."""
    cost = entry * (2 * fee + 2 * slip_bps / 1e4)
    risk = abs(entry - stop) + cost
    reward = abs(target - entry) - cost
    return reward / risk if risk > 0 else 0.0
