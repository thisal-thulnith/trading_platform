"""The agent-side trade plan: entry, stop, target, leverage, margin, quantity, risk and liquidation for one trade.

The agents choose the side, the stop and the target from the analysis (`levels()` offers the measured options);
this module turns that choice into exact numbers so no figure is ever invented by a language model:

  risk ($)        = budget x the mode's risk % (conservative 1 %, growth 2 %, aggressive 3 %)
  risk per unit   = |entry - stop| + entry x (2 x fee + slippage)        (a stop-out costs the move plus the costs)
  quantity        = risk / risk per unit, rounded DOWN to the exchange lot step
  leverage        = the LOWEST whole leverage that keeps the margin within the mode's per-trade cap, and never above
                    the safe leverage (liquidation at least 1.5 x the stop distance away, app/risk/futures.py)
  margin          = notional / leverage

Leverage never changes the risk: the position is sized by the stop. Higher leverage only locks less margin and moves
the liquidation closer, so the plan uses the least leverage that fits. Paper only: nothing here is sent to Binance.
"""
import math

from app.risk import futures as fut
from app.risk.portfolio import RISK_MODES

SLIP = 0.0005
SPOT_FEE = 0.001
MIN_RR = 2.0           # research: reward/risk >= 2 net of costs (app/strategies validation, CHANGELOG 0.8.0)
MAX_COST_SHARE = 0.20  # research: costs <= 20 % of the risk, else fees eat the edge
MIN_STOP_PCT = 0.002
MAX_STOP_PCT = 0.30
# margin sizing: the most of the trader's margin a stop-out may lose, by risk style
MARGIN_RISK = {"conservative": 0.10, "growth": 0.20, "aggressive": 0.30}
STYLES = ("conservative", "growth", "aggressive")


def mode_of(s: dict) -> str:
    """The risk style a desk session uses: the trader's, or — when they leave it to the desk ("auto") — the one the Risk
    Officer chose for this trade, conservative until it has chosen."""
    m = s.get("mode")
    return m if m in STYLES else s.get("mode_chosen") or "conservative"


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, n)


def _px(x: float) -> float:
    """Round a price to 6 significant digits for display without losing small-cap precision."""
    return float(f"{x:.6g}")


def levels(snap: dict, side: str) -> dict:
    """Measured stop and target options for one side, from a live analysis snapshot: the last structural swing, an
    ATR stop, support/resistance and resting liquidity, and the engine's own setup when one exists."""
    price = float(snap["price"])
    atr = float((snap.get("indicators") or {}).get("atr14") or 0) or price * 0.02
    d = 1 if side == "LONG" else -1
    swings = (snap.get("smc") or {}).get("swings") or []
    stops: list[dict] = []
    # structural stop: beyond the most recent swing on the protective side, with a quarter-ATR buffer
    kind = "low" if side == "LONG" else "high"
    sw = next((s for s in reversed(swings) if s.get("kind") == kind and (s["price"] - price) * d < 0), None)
    if sw:
        sp = sw["price"] - d * 0.25 * atr
        stops.append({"key": "structure", "price": _px(sp), "pct": _r(abs(price - sp) / price * 100, 2),
                      "why": f"beyond the last swing {kind} {sw['price']:.6g} ({sw.get('label', '')}) plus a quarter ATR"})
    sp = price - d * 2 * atr
    stops.append({"key": "atr", "price": _px(sp), "pct": _r(2 * atr / price * 100, 2),
                  "why": f"2 x ATR ({atr:.6g}) from the price — outside normal noise"})
    # engine setup in this direction (best stage first)
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    cands = sorted([c for c in snap.get("candidates", []) if "error" not in c and c.get("direction") == side],
                   key=lambda c: -stage_w.get(c.get("stage"), 0))
    setup = None
    if cands:
        c = cands[0]
        setup = {"strategy": c["strategy"], "stage": c["stage"], "entry": _px((c["entry_low"] + c["entry_high"]) / 2),
                 "stop": _px(c["stop"]), "targets": [_px(t) for t in c.get("targets", [])[:3]],
                 "passed_checks": bool((c.get("validation") or {}).get("passed"))}
        if (c["stop"] - price) * d < 0:
            stops.append({"key": "engine", "price": _px(c["stop"]), "pct": _r(abs(price - c["stop"]) / price * 100, 2),
                          "why": f"the engine's {c['strategy'].replace('_', ' ')} setup stop ({c['stage']})"})
    # pick: the structural stop when it sits 1-4 ATR away (a real invalidation, not noise), else the ATR stop
    pick = next((s for s in stops if s["key"] == "structure" and atr <= abs(price - s["price"]) <= 4 * atr), None)
    pick = pick or next(s for s in stops if s["key"] == "atr")
    risk = abs(price - pick["price"])
    # a target must clear 2R AFTER costs: distance >= 2 x (risk + costs) + costs (worst case: spot fees)
    cost = price * (2 * SPOT_FEE + SLIP)
    need = (MIN_RR * (risk + cost) + cost) / risk if risk > 0 else MIN_RR
    # targets: support/resistance and resting liquidity on the profit side, with their R multiple from the pick
    pool = []
    for lv in (snap.get("price_action") or {}).get("levels") or []:
        if (lv["price"] - price) * d > 0:
            pool.append((lv["price"], f"{lv['type']} tested {lv.get('touches', '?')}x"))
    for lq in (snap.get("smc") or {}).get("liquidity") or []:
        if lq.get("status") == "resting" and (lq["price"] - price) * d > 0:
            pool.append((lq["price"], f"{lq['type'].replace('_', ' ')} ({lq.get('source', '')})"))
    pool.sort(key=lambda x: abs(x[0] - price))
    targets = [{"price": _px(p), "pct": _r(abs(p - price) / price * 100, 2), "r": _r(abs(p - price) / risk, 2),
                "why": w} for p, w in pool[:8]] if risk > 0 else []
    two_r = price + d * need * risk
    first_ok = next((t for t in targets if t["r"] is not None and t["r"] >= need), None)
    tgt = first_ok or {"price": _px(two_r), "pct": _r(need * risk / price * 100, 2), "r": _r(need, 2),
                       "why": "2R after costs: no measured level that far, so the research minimum is used"}
    if first_ok:
        tgt = {**first_ok, "why": f"{first_ok['why']}, {first_ok['r']}R — the first measured level beyond 2R after costs"}
    return {"side": side, "price": price, "atr": _px(atr), "atr_pct": _r(atr / price * 100, 2), "stop_options": stops,
            "target_options": targets, "engine_setup": setup,
            "default": {"stop": pick["price"], "stop_why": pick["why"], "target": tgt["price"], "target_why": tgt["why"]}}


def build(symbol: str, side: str, entry: float, stop: float, target: float | None, budget: float, mode: str,
          venue: str = "futures", filters: fut.Filters | None = None, funding_apr_pct: float | None = None,
          margin: float | None = None) -> dict:
    """Exact numbers for one trade. Returns {"ok": False, "error": ...} when the inputs cannot make a sane trade.

    Two ways to size:
      budget (default)  risk the mode's % of the account; the lowest leverage that fits the margin cap
      margin            the trader says how much margin goes into this trade; the leverage is the highest that keeps
                        the loss at the stop within MARGIN_RISK[mode] of that margin, never above the safe leverage
    """
    p = _build(symbol, side, entry, stop, target, budget, mode, venue, filters, funding_apr_pct, margin)
    return p if "stop" in p else {**p, "symbol": symbol, "side": side.upper(), "venue": venue, "mode": mode}


def _build(symbol: str, side: str, entry: float, stop: float, target: float | None, budget: float, mode: str,
           venue: str = "futures", filters: fut.Filters | None = None, funding_apr_pct: float | None = None,
           margin: float | None = None) -> dict:
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        return {"ok": False, "error": "side must be LONG or SHORT"}
    if venue == "spot" and side == "SHORT":
        return {"ok": False, "error": "a short needs futures; spot can only buy"}
    if mode not in RISK_MODES:
        return {"ok": False, "error": f"mode must be one of {sorted(RISK_MODES)}"}
    if margin is not None and margin <= 0:
        return {"ok": False, "error": "the margin must be positive"}
    if margin:
        budget = margin
    if not (entry > 0 and stop > 0 and budget > 0):
        return {"ok": False, "error": "entry, stop and budget must be positive"}
    d = 1 if side == "LONG" else -1
    if (entry - stop) * d <= 0:
        return {"ok": False, "error": f"for a {side} the stop must be {'below' if d == 1 else 'above'} the entry"}
    stop_pct = abs(entry - stop) / entry
    if not MIN_STOP_PCT <= stop_pct <= MAX_STOP_PCT:
        return {"ok": False, "error": f"stop {stop_pct * 100:.2f}% away: it must be 0.2-30% from the entry"}
    target = float(target) if target else entry + d * MIN_RR * abs(entry - stop)
    if (target - entry) * d <= 0:
        return {"ok": False, "error": f"for a {side} the target must be {'above' if d == 1 else 'below'} the entry"}

    m = RISK_MODES[mode]
    fee = fut.TAKER if venue == "futures" else SPOT_FEE
    cost_per_unit = entry * (2 * fee + SLIP)
    per_unit = abs(entry - stop) + cost_per_unit
    risk_budget = budget * m["risk_pct"] / 100
    cap_usd = budget * m["max_position_pct"] / 100  # margin cap (futures) or position cap (spot) per trade
    f = filters or fut.DEFAULT_FILTERS.get(symbol) or fut.Filters()
    safe = fut.safe_leverage(stop_pct, symbol) if venue == "futures" else 1
    notes: list[str] = []

    share = MARGIN_RISK[mode]
    eff = per_unit / entry  # what a stop-out costs per $1 of position, fees and slippage included
    if margin:
        risk_budget = share * margin
        want = math.floor(share / eff + 1e-9) if venue == "futures" else 1
        lev = max(1, min(want, safe)) if venue == "futures" else 1
        qty = margin * lev / entry
        if want < 1 or (venue == "spot" and eff > share):
            qty = risk_budget / per_unit
            notes.append(f"A stop-out at 1x would lose {eff:.1%} of the margin, more than the {share:.0%} limit, so only "
                         f"${qty * entry:.2f} of your ${margin:.2f} margin is used.")
        elif venue == "futures" and want > safe:
            notes.append(f"Leverage capped at the safe {safe}x for this stop: the loss at the stop is about "
                         f"{safe * eff:.0%} of the margin, under the {share:.0%} limit.")
    else:
        qty = risk_budget / per_unit
    notional = qty * entry
    if margin:
        pass  # leverage already chosen from the margin
    elif venue == "futures":
        lev = max(1, math.ceil(notional / cap_usd - 1e-9))
        if lev > safe:
            lev = safe
            qty = cap_usd * safe / entry
            notes.append(f"Size cut so the margin stays within {m['max_position_pct']:g}% of the budget at the safe "
                         f"{safe}x; the risk is below the {m['risk_pct']:g}% plan.")
    else:
        lev = 1
        if notional > cap_usd:
            qty = cap_usd / entry
            notes.append(f"Size cut to {m['max_position_pct']:g}% of the budget (spot has no leverage).")
    qty = fut.round_step(qty, f.step)
    notional = qty * entry
    refused = None
    if qty < f.min_qty or notional < f.min_notional:
        need = budget * f.min_notional / max(notional, 1e-9) if notional > 0 else None
        if margin:  # the smallest margin that buys Binance's minimum order at this leverage
            need = max(f.min_notional, f.min_qty * entry) / max(lev, 1) * 1.02
        refused = (f"The order would be ${notional:.2f}, under Binance's ${max(f.min_notional, f.min_qty * entry):,.2f} "
                   f"minimum for {symbol}. "
                   + ((f"Put at least ${need:,.2f} margin on this trade." if margin else
                       f"At this stop you need a budget of about ${need:,.0f} in {mode} mode, or a wider-risk mode.")
                      if need else "Use a larger amount."))
    risk_usd = qty * per_unit
    reward_usd = qty * (abs(target - entry) - cost_per_unit)
    rr_net = reward_usd / risk_usd if risk_usd > 0 else None
    cost_share = cost_per_unit / abs(entry - stop)
    margin_ = notional / lev if lev else notional  # margin actually locked
    liq = fut.liquidation_price(entry, lev, symbol, side) if venue == "futures" else None
    liq_pct = abs(liq - entry) / entry * 100 if liq else None
    warnings = []
    if rr_net is not None and rr_net < MIN_RR:
        warnings.append(f"Reward/risk after costs is {rr_net:.2f}, below the research minimum of {MIN_RR:g}.")
    if cost_share > MAX_COST_SHARE:
        warnings.append(f"Fees and slippage are {cost_share:.0%} of the risk (research limit {MAX_COST_SHARE:.0%}): "
                        f"the stop is too tight for the costs.")
    if venue == "futures" and side == "SHORT":
        warnings.append("Shorts failed the research validation for the daily system (about -0.6R per trade in 2024); "
                        "treat this as a discretionary trade.")

    stop = fut.round_step(stop, f.tick) if d == 1 else round(math.ceil(stop / f.tick - 1e-9) * f.tick, 10)
    side_word = "below" if d == 1 else "above"
    if margin:
        explain = [
            f"You put ${margin:,.2f} margin. In {mode} style the desk lets a stop-out lose at most {share:.0%} of it "
            f"(${risk_budget:.2f}).",
            f"Stop {stop:.6g}, {stop_pct * 100:.2f}% {('below' if d == 1 else 'above')} the entry; with fees a stop-out "
            f"costs {eff:.2%} of the position, so "
            + (f"{lev}x is the most leverage that keeps the loss within {share:.0%} of the margin (the safe maximum for "
               f"this stop is {safe}x)." if venue == "futures" else "spot runs at 1x."),
            f"Position ${notional:,.2f} = ${margin_:.2f} margin x {lev}: {qty:g} {symbol.replace('USDT', '')}."
            + (f" Liquidation {liq:.6g} sits {liq_pct:.1f}% away, {liq_pct / (stop_pct * 100):.1f}x the stop distance."
               if liq else ""),
            f"At the stop you lose ${risk_usd:.2f} ({risk_usd / margin * 100:.1f}% of the margin); at the target "
            f"{target:.6g} you make ${reward_usd:.2f} ({reward_usd / margin * 100:.1f}% of the margin), reward/risk "
            f"{(rr_net or 0):.2f} after fees.",
        ]
    else:
        explain = []
    explain += [] if margin else [
        f"Risk: {m['risk_pct']:g}% of your ${budget:,.0f} budget = ${risk_budget:.2f} ({mode} mode). The plan risks "
        f"${risk_usd:.2f} after rounding to Binance's lot size.",
        f"Stop {stop:.6g}, {stop_pct * 100:.2f}% {side_word} the entry; quantity = risk / (stop distance + fees) = "
        f"{qty:g} {symbol.replace('USDT', '')} (${notional:,.2f} position).",
    ]
    if venue == "futures" and not margin:
        explain.append(
            f"Leverage {lev}x: the lowest that keeps the margin (${margin_:.2f}) within {m['max_position_pct']:g}% of the "
            f"budget. Liquidation {liq:.6g} sits {liq_pct:.1f}% away, {liq_pct / (stop_pct * 100):.1f}x the stop "
            f"distance; the safe maximum for this stop is {safe}x. More leverage would not raise the profit — the size "
            f"is set by the stop — it would only move the liquidation closer.")
    if not margin:
        explain.append(f"Target {target:.6g} ({abs(target - entry) / entry * 100:.2f}% away): reward/risk "
                       f"{(rr_net or 0):.2f} after fees.")
    return {
        "ok": refused is None, "error": refused, "symbol": symbol, "side": side, "venue": venue, "mode": mode,
        "budget_usd": budget, "entry": _px(entry), "stop": stop,
        "target": _px(target), "stop_pct": _r(stop_pct * 100, 2), "target_pct": _r(abs(target - entry) / entry * 100, 2),
        "qty": qty, "notional_usd": _r(notional), "leverage": lev, "leverage_safe_max": safe,
        "margin_usd": _r(margin_), "liquidation": _px(liq) if liq else None, "liquidation_pct": _r(liq_pct, 1),
        "liquidation_vs_stop_x": _r(liq_pct / (stop_pct * 100), 1) if liq_pct else None,
        "risk_usd": _r(risk_usd), "risk_pct_of_budget": None if margin else _r(risk_usd / budget * 100),
        "reward_usd": _r(reward_usd), "sizing": "margin" if margin else "budget", "margin_input": margin,
        "risk_pct_of_margin": _r(risk_usd / margin * 100, 1) if margin else None,
        "reward_pct_of_margin": _r(reward_usd / margin * 100, 1) if margin else None,
        "margin_risk_limit_pct": _r(share * 100, 0) if margin else None,
        "rr_net": _r(rr_net), "fees_usd": _r(2 * fee * notional, 3), "cost_share_of_risk_pct": _r(cost_share * 100, 1),
        "funding_usd_per_day": _r(notional * funding_apr_pct / 100 / 365 * (-d), 4) if funding_apr_pct is not None
        and venue == "futures" else None,
        "partial": {"qty": fut.round_step(qty / 3, f.step), "at": _px(target),
                    "then": "move the stop to the entry and trail the rest"},
        "orders": _orders(symbol, side, venue, lev, qty, entry, stop, target, f) if refused is None else [],
        "explain": explain, "notes": notes, "warnings": warnings,
        "leverage_options": (margin_options(symbol, side, entry, stop, target, margin, qty, lev, fee, f, share) if margin
                             else leverage_options(symbol, side, entry, stop, target, qty, lev, budget, fee, f))
        if venue == "futures" and refused is None else [],
    }


RISKY_LEVERAGES = (10, 20)


def _option(key: str, title: str, symbol: str, side: str, entry: float, stop: float, target: float, qty: float,
            lev: int, budget: float, fee: float, limits: tuple[float, float] = (5.0, 15.0), base: str = "budget") -> dict:
    """One way to place the same idea (same entry, stop and target) at a given leverage and size."""
    cost = entry * (2 * fee + SLIP)
    notional = qty * entry
    margin = notional / lev
    stop_pct = abs(entry - stop) / entry * 100
    liq = fut.liquidation_price(entry, lev, symbol, side)
    liq_pct = abs(liq - entry) / entry * 100
    liq_first = liq_pct <= stop_pct  # the exchange closes it before the stop can
    # isolated margin: a liquidation takes the whole margin (Binance's liquidation fee comes out of it), never more
    loss = margin if liq_first else min(margin, qty * (abs(entry - stop) + cost))
    profit = qty * (abs(target - entry) - cost)
    loss_pct = loss / budget * 100
    if liq_first:
        level, note = "extreme", (f"Liquidated at {liq:.6g} ({liq_pct:.1f}% away) BEFORE your stop ({stop_pct:.1f}%): the "
                                  f"whole margin is lost (the liquidation fee comes out of it) and the stop never fires.")
    elif liq_pct < fut.LIQ_BUFFER * stop_pct:
        level, note = "very risky", (f"Liquidation {liq_pct:.1f}% away is only {liq_pct / stop_pct:.1f}x the stop distance: "
                                     f"a wick on the mark price can liquidate it before the stop.")
    elif loss_pct > limits[1] + 0.05:
        level, note = "very risky", (f"A stop-out loses {loss_pct:.0f}% of the {base}: 5 losses in a row (about 1 chance "
                                     f"in 13 at a 40% win rate) leave {max(0.0, 1 - loss_pct / 100) ** 5:.0%} of it.")
    elif loss_pct > limits[0] + 0.05:
        level, note = "risky", (f"A stop-out loses {loss_pct:.0f}% of the {base}; 5 losses in a row leave "
                                f"{max(0.0, 1 - loss_pct / 100) ** 5:.0%} of it.")
    elif lev > fut.safe_leverage(stop_pct / 100, symbol):
        level, note = "risky", "Above the safe leverage for this stop."
    else:
        level, note = "safe", "Liquidation is far beyond the stop; the stop closes a loser."
    return {"key": key, "title": title, "leverage": lev, "qty": qty, "notional_usd": _r(notional), "margin_usd": _r(margin),
            "liquidation": _px(liq), "liquidation_pct": _r(liq_pct, 1), "liquidated_before_stop": liq_first,
            "loss_at_stop_usd": _r(loss), "loss_pct_of_budget": _r(loss_pct, 1), "profit_at_target_usd": _r(profit),
            "profit_pct_of_budget": _r(profit / budget * 100, 1), "roe_at_target_pct": _r(profit / margin * 100, 1),
            "roe_at_stop_pct": _r(-loss / margin * 100, 1), "risk_level": level, "note": note}


def leverage_options(symbol: str, side: str, entry: float, stop: float, target: float, qty: float, lev: int,
                     budget: float, fee: float, f: fut.Filters) -> list[dict]:
    """The safe plan plus the high-leverage ways to place the same trade, each with its loss at the stop, profit at the
    target and where it gets liquidated:
      'min margin'   same size and same dollar risk, less margin locked (higher return on margin, closer liquidation)
      'high return'  the safe plan's margin kept, so the position is leverage x bigger: bigger profit AND bigger loss
    The research (app/maintenance/leverage_study.py): 20x with 20% margin per trade took $100 to about $4."""
    margin = qty * entry / lev
    out = [_option("safe", f"Safe · {lev}x", symbol, side, entry, stop, target, qty, lev, budget, fee)]
    for L in RISKY_LEVERAGES:
        if L <= lev:
            continue
        out.append(_option(f"min_margin_{L}", f"{L}x · less margin", symbol, side, entry, stop, target, qty, L,
                           budget, fee))
        big = fut.round_step(min(margin * L, budget * L) / entry, f.step)
        if big * entry >= f.min_notional:
            out.append(_option(f"high_return_{L}", f"{L}x · {L / lev:.1f}x bigger", symbol, side, entry, stop,
                               target, big, L, budget, fee))
    return out


def _orders(symbol: str, side: str, venue: str, lev: int, qty: float, entry: float, stop: float, target: float,
            f: fut.Filters) -> list[str]:
    """What to type into Binance, step by step (the platform never sends orders)."""
    close_side = "SELL" if side == "LONG" else "BUY"
    if venue == "spot":
        return [f"Spot -> {symbol}: BUY {qty:g} at market (about {entry:.6g}).",
                f"Place a STOP-LIMIT SELL {qty:g} with the stop at {stop:.6g}.",
                f"Sell a third ({fut.round_step(qty / 3, f.step):g}) with a LIMIT at {target:.6g}."]
    return [f"Futures -> USDⓈ-M -> {symbol} Perpetual. Margin mode Isolated, leverage {lev}x.",
            f"{'BUY / LONG' if side == 'LONG' else 'SELL / SHORT'} {qty:g} at market (about {entry:.6g}).",
            f"STOP_MARKET {close_side} {qty:g}, trigger {stop:.6g}, reduce-only (mark price).",
            f"TAKE_PROFIT_MARKET {close_side} {fut.round_step(qty / 3, f.step):g}, trigger {target:.6g}, reduce-only "
            f"(a third), then move the stop to the entry."]


def margin_options(symbol: str, side: str, entry: float, stop: float, target: float, margin: float, qty: float, lev: int,
                   fee: float, f: fut.Filters, share: float) -> list[dict]:
    """The same margin at other leverages: the agents' pick first, then the others from low to high. Higher leverage
    means a bigger position from the same margin — bigger profit and bigger loss — and a closer liquidation."""
    lim = (share * 100, min(2 * share * 100, 90.0))
    out = [_option("safe", f"{lev}x · agents' pick", symbol, side, entry, stop, target, qty, lev, margin, fee, lim, "margin")]
    for L in sorted({1, 2, 3, 5, *RISKY_LEVERAGES} - {lev}):
        if L > fut.MAX_LEVERAGE:
            continue
        q = fut.round_step(margin * L / entry, f.step)
        if q * entry < f.min_notional or q < f.min_qty:
            continue
        out.append(_option(f"lev_{L}", f"{L}x", symbol, side, entry, stop, target, q, L, margin, fee, lim, "margin"))
    return out
