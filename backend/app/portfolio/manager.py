"""Your account, managed: you enter your balance once; the Risk manager chooses the risk mode from the evidence and
sizes today's main signal for your account; you accept it after placing it on Binance, and the portfolio tracks it.

The Risk manager is code, not an AI: its choice must be the same every time for the same facts, and it must be
explainable in one line per reason. It reads only measured evidence:
- the main signal's LIVE record (Live results: finished signals and their average R), not the backtest alone
- your own account since you set the balance (drawdown from the peak, losing streak)
- today's signal (TRADE or watch only) and the AI agents' stance on it (agree / wait / against / not asked)

Policy (written before use; change it only with a reason in docs):
- start CONSERVATIVE: 1% of the account at risk per trade (a 10-trade losing streak, which happens, costs ~10%)
- GROWTH, 2%: only after 10+ finished live signals averaging at least +0.10R, the account within 10% of its peak,
  fewer than 3 losses in a row, and the agents not saying wait or the other side
- AGGRESSIVE, 3%: only after 30+ finished live signals averaging at least +0.20R, the agents' veto test passed
  (Live results), the agents agreeing today, and the account within 5% of its peak
- step DOWN to conservative at half size (0.5%) after 3 losses in a row or a 10% drop from the peak
- half size when the agents say wait; NO new trade when the agents say the other side, when the signal is watch only,
  when the account is 20% below its peak, or when the open risk is already at the mode's limit
Leverage is never chosen for excitement: the position is sized from the dollar risk and the stop distance, and the
leverage is the lowest that keeps the margin within the mode's per-trade limit, never above the signal's safe maximum.
Paper only: nothing is ever sent to an exchange.
"""
from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from app.risk.portfolio import RISK_MODES

STATE = Path(__file__).resolve().parents[2] / "data" / "my_account.json"
FEE = 0.0005           # taker fee per side on Binance USDⓈ-M (entry + exit counted)
MMR = 0.005            # maintenance margin rate used for the liquidation estimate (small coins can be higher)


# ------------------------------------------------------------------------------------------- the balance
def get_balance() -> dict | None:
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else None
    except (OSError, ValueError):
        return None


def set_balance(amount: float) -> dict:
    v = {"balance": round(float(amount), 2), "set_at": time.time()}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(v))
    return v


# ------------------------------------------------------------------------------------------- the Risk manager (pure)
def choose_mode(live: dict, account: dict, stance: str, status: str, veto_on: bool) -> dict:
    """live: {finished, avg_r}; account: {drawdown_pct (>=0), loss_streak}; stance: agree|wait|against|none;
    status: trade|watch. Returns {mode, risk_pct, size, trade, reasons}."""
    n, avg = live.get("finished") or 0, live.get("avg_r")
    dd, streak = account.get("drawdown_pct") or 0.0, account.get("loss_streak") or 0
    reasons: list[str] = []
    mode, size, trade = "conservative", 1.0, True

    if n >= 30 and (avg or 0) >= 0.20 and veto_on and stance == "agree" and dd < 5:
        mode = "aggressive"
        reasons.append(f"{n} finished live signals average {avg:+.2f}R, the agents passed their test and agree today.")
    elif n >= 10 and (avg or 0) >= 0.10 and dd < 10 and streak < 3 and stance not in ("wait", "against"):
        mode = "growth"
        reasons.append(f"{n} finished live signals average {avg:+.2f}R and your account is near its peak.")
    else:
        reasons.append("Conservative: " + (
            f"only {n} of 10 live signals have finished, so the signal has not proven itself on new days yet."
            if n < 10 else f"the live record ({avg:+.2f}R over {n}) is not strong enough to risk more."))

    if streak >= 3 or dd >= 10:
        mode, size = "conservative", 0.5
        reasons.append(f"Half size: {'3+ losses in a row' if streak >= 3 else f'your account is {dd:.0f}% below its peak'}.")
    if stance == "wait":
        size = min(size, 0.5)
        reasons.append("Half size: the AI agents say wait on today's signal.")
    if stance == "against":
        trade = False
        reasons.append("No new trade: the AI agents say the other side.")
    if status != "trade":
        trade = False
        reasons.append("No new trade: today's signal is watch only.")
    if dd >= 20:
        trade = False
        reasons.append(f"Paused: your account is {dd:.0f}% below its peak. Take a break and review before trading again.")
    return {"mode": mode, "risk_pct": round(RISK_MODES[mode]["risk_pct"] * size, 2), "size": size, "trade": trade,
            "reasons": reasons}


def size_trade(equity: float, risk_pct: float, mode: str, side: str, entry: float, stop: float, safe_max: int,
               open_risk_usd: float = 0.0) -> dict:
    """Position size from the dollar risk and the stop distance; the lowest leverage that keeps the margin within the
    mode's per-trade limit; never above the safe maximum; trimmed to the mode's open-risk limit."""
    d = 1 if side == "LONG" else -1
    stop_pct = abs(entry - stop) / entry
    rules = RISK_MODES[mode]
    risk_usd = equity * risk_pct / 100
    room = equity * rules["max_open_risk_pct"] / 100 - open_risk_usd
    note = None
    if room <= 0:
        return {"ok": False, "reason": f"Your open trades already use the {mode} limit of "
                                       f"{rules['max_open_risk_pct']:g}% of the account at risk."}
    if risk_usd > room:
        risk_usd, note = room, "Trimmed to the open-risk limit."
    notional = risk_usd / (stop_pct + 2 * FEE)
    max_margin = equity * rules["max_position_pct"] / 100
    lev = max(1, math.ceil(notional / max_margin))
    if lev > safe_max:   # even the safe maximum needs too much margin: shrink the position instead
        lev = max(1, safe_max)
        notional = max_margin * lev
        risk_usd = notional * (stop_pct + 2 * FEE)
        note = f"Smaller than your risk budget: at {lev}x the margin would pass {rules['max_position_pct']:g}% of the account."
    margin = notional / lev
    liq = entry * (1 - d * (1 / lev - MMR))
    return {"ok": True, "leverage": lev, "notional_usd": round(notional, 2), "margin_usd": round(margin, 2),
            "qty": float(f"{notional / entry:.6g}"), "risk_usd": round(risk_usd, 2), "stop_pct": round(stop_pct * 100, 2),
            "liquidation": float(f"{liq:.6g}"), "note": note}


# ------------------------------------------------------------------------------------------- the account view
def _account_stats(balance: dict, trades: list, open_cards: list[dict]) -> dict:
    """Equity curve since the balance was set: closed trades in order, then the open ones marked to market."""
    since = datetime.fromtimestamp(balance["set_at"], UTC)
    closed = sorted((t for t in trades if t.status == "closed" and t.closed_at and
                     (t.closed_at if t.closed_at.tzinfo else t.closed_at.replace(tzinfo=UTC)) >= since),
                    key=lambda t: t.closed_at)
    eq, peak, streak = balance["balance"], balance["balance"], 0
    for t in closed:
        eq += t.pnl_usd or 0.0
        peak = max(peak, eq)
        streak = streak + 1 if (t.pnl_usd or 0) < 0 else 0
    realized = eq - balance["balance"]
    unreal = sum(c.get("pnl_usd") or 0.0 for c in open_cards)
    equity = eq + unreal
    return {"balance": balance["balance"], "set_at": datetime.fromtimestamp(balance["set_at"], UTC).isoformat(),
            "realized_usd": round(realized, 2), "unrealized_usd": round(unreal, 2), "equity": round(equity, 2),
            "peak": round(max(peak, equity), 2), "drawdown_pct": round(max(0.0, (1 - equity / max(peak, equity)) * 100), 2),
            "loss_streak": streak, "closed_since": len(closed), "open": len(open_cards),
            "open_risk_usd": round(sum(c.get("open_risk_usd") or 0.0 for c in open_cards), 2),
            "margin_in_use_usd": round(sum(c.get("margin_usd") or 0.0 for c in open_cards), 2)}


async def view(db, user_id: str | None, today: dict, test_report: dict, open_cards: list[dict], trades: list,
               explicit: bool = False) -> dict:
    """Everything the portfolio page needs: the account, the Risk manager's choice and today's suggested trade."""
    bal = get_balance()
    if bal is None:
        return {"set": False}
    acct = _account_stats(bal, trades, open_cards)
    main = test_report.get("main_group") or "unified"
    g = (test_report.get("stats", {}).get(main) or {}).get("swing") or {}
    plan = today.get("plan") or {}
    final = today.get("final") or {}
    stance = final.get("stance", "none")
    veto_on = bool((test_report.get("agent_veto") or {}).get("veto_on"))
    risk = choose_mode({"finished": g.get("finished", 0), "avg_r": g.get("avg_r")}, acct, stance,
                       plan.get("status", "watch"), veto_on)
    out = {"set": True, "account": acct, "risk": risk, "suggestion": None}
    if not plan.get("covered") or not plan.get("plans"):
        out["suggestion"] = {"ok": False, "reason": "No main signal today."}
        return out
    sw = plan["plans"]["swing"]
    base = {"symbol": plan["symbol"], "side": plan["side"], "entry_type": plan["entry_type"], "entry": sw["entry"],
            "stop": sw["stop"], "target": sw["tp1"], "stop_pct": sw["stop_pct"], "target_pct": sw["tp1_pct"],
            "days": sw.get("days"), "mode": risk["mode"], "risk_pct": risk["risk_pct"], "as_of": plan.get("as_of")}
    already = any(c.get("symbol") == plan["symbol"] and c.get("side") == plan["side"] for c in open_cards)
    since = (plan.get("live_state") or {}).get("since")
    traded = since and any(t.symbol == plan["symbol"] and t.side == plan["side"] and t.status == "closed" and t.opened_at
                           and (t.opened_at if t.opened_at.tzinfo else t.opened_at.replace(tzinfo=UTC)).timestamp() >= since
                           for t in trades)
    if already:
        out["suggestion"] = {**base, "ok": False, "reason": "You already hold this trade. Manage it below."}
    elif traded:
        out["suggestion"] = {**base, "ok": False, "reason": "You already traded this signal (it is closed in your trades). "
                                                            "Wait for the next one: taking the same idea twice doubles the risk."}
    elif (plan.get("live_state") or {}).get("state") in ("stop_hit", "target_hit", "ran_away"):
        out["suggestion"] = {**base, "ok": False, "reason": plan["live_state"]["text"]}
    elif plan.get("clear") is False:
        out["suggestion"] = {**base, "ok": False, "reason": plan.get("unclear_note") or "No clear side for this coin."}
    elif not risk["trade"] and not explicit:
        out["suggestion"] = {**base, "ok": False, "reason": risk["reasons"][-1]}
    elif explicit and (stance == "against" or acct["drawdown_pct"] >= 20):
        out["suggestion"] = {**base, "ok": False, "reason": risk["reasons"][-1]}
    else:
        if explicit and plan.get("status") != "trade":   # you chose a coin the machine does not trade today: smaller
            risk = {**risk, "risk_pct": round(min(risk["risk_pct"], 0.5), 2),
                    "reasons": [*risk["reasons"], "Not the machine's trade today (you picked this coin): half size at most."]}
            out["risk"] = risk
            base["risk_pct"] = risk["risk_pct"]
        sz = size_trade(acct["equity"], risk["risk_pct"], risk["mode"], plan["side"], float(sw["entry"]),
                        float(sw["stop"]), int(min(sw["leverage"]["safe_max"], 20)), acct["open_risk_usd"])
        if not sz["ok"]:
            out["suggestion"] = {**base, **sz}
        else:
            reward = sz["notional_usd"] * (abs(float(sw["tp1"]) - float(sw["entry"])) / float(sw["entry"]) - 2 * FEE)
            out["suggestion"] = {**base, **sz, "reward_usd": round(reward, 2),
                                 "safe_max": int(min(sw["leverage"]["safe_max"], 20))}
    return out


# ------------------------------------------------------------------------------------------- the pre-trade check
def assess(side: str, plan: dict, account: dict | None, risk: dict | None, open_cards: list[dict], conf: dict | None,
           price: float | None, levels: dict | None) -> dict:
    """Before you record a trade: should you take it? Each reason is one line marked good / warn / bad; any bad means
    'not recommended', two or more warnings mean 'careful'. Advice only: you can take it anyway."""
    sym = plan.get("symbol", "")
    coin = sym.replace("USDT", "")
    r: list[dict] = []
    add = lambda lvl, text: r.append({"level": lvl, "text": text})  # noqa: E731
    main = plan.get("covered") and plan.get("engine") == "unified"
    if not main:
        add("warn", f"The main signal does not cover {coin} (it scores liquid coins only).")
    elif plan.get("clear") is False:
        add("warn", f"The main signal has no clear side for {coin} today.")
    elif plan.get("side") == side:
        add("good" if plan.get("status") == "trade" else "warn",
            f"The main signal agrees ({side})" + (" and it is the machine's trade today." if plan.get("status") == "trade"
                                                   else f", but it is not the machine's trade today (ranked #{plan.get('rank')})."))
    else:
        add("bad", f"The main signal says {plan.get('side')} for {coin}, the other way.")
    ls = plan.get("live_state") or {}
    if main and plan.get("side") == side and ls.get("state") in ("stop_hit", "target_hit", "ran_away"):
        add("bad", ls["text"])
    if levels and price:
        d = 1 if side == "LONG" else -1
        if (price - levels["stop"]) * d <= 0:
            add("bad", "The price is already past your stop loss.")
        elif (levels["target"] - price) * d <= 0:
            add("bad", "The price is already past your take profit: too late.")
    if conf and conf.get("label") in ("High", "Medium", "Low") and (not main or plan.get("side") == side):
        add({"High": "good", "Medium": "warn", "Low": "bad"}[conf["label"]],
            f"{conf['label']} confidence: similar signals won {round((conf.get('win_rate') or 0) * 100)}% and made "
            f"{(conf.get('avg_r') or 0):+.2f}R a trade on unseen years.")
    v = plan.get("agents")
    if v and not v.get("stale") and v.get("entry") and price and abs(float(v["entry"]) / price - 1) > 0.05:
        add("warn", f"The AI agents' plan was made at another price (entry {v['entry']:.6g}, now {price:.6g}): it may be out of date.")
    if v and not v.get("stale"):
        if v["action"] == side:
            add("good", f"The AI agents agree ({side}).")
        elif v["action"] == "WAIT":
            add("warn", "The AI agents said wait" + (f": {v['conditions'][0]}" if v.get("conditions") else "."))
        else:
            add("bad", f"The AI agents said {v['action']}.")
    trend = (plan.get("market") or {}).get("trend")
    if trend == "down" and side == "LONG":
        add("warn", "The market is falling (BTC below its 50-day average): longs went against it in tests.")
    if any(c.get("symbol") == sym for c in open_cards):
        add("bad", f"You already hold {coin}. Don't double the same idea.")
    if account is None:
        add("warn", "Your balance is not set, so the size can't be checked. Set it in Portfolio.")
    else:
        if account["drawdown_pct"] >= 20:
            add("bad", f"Your account is {account['drawdown_pct']:.0f}% below its peak: pause and review first.")
        elif account["drawdown_pct"] >= 10:
            add("warn", f"Your account is {account['drawdown_pct']:.0f}% below its peak: trade smaller.")
        if account["loss_streak"] >= 3:
            add("warn", f"{account['loss_streak']} losses in a row: a common time for revenge trades. Trade smaller.")
        if risk:
            limit = account["equity"] * RISK_MODES[risk["mode"]]["max_open_risk_pct"] / 100
            if account["open_risk_usd"] >= limit:
                add("bad", f"Your open trades already risk ${account['open_risk_usd']:,.0f}, the {risk['mode']} limit.")
            elif len(open_cards) >= RISK_MODES[risk["mode"]]["max_positions"]:
                add("warn", f"You already have {len(open_cards)} open trades (the {risk['mode']} limit is "
                            f"{RISK_MODES[risk['mode']]['max_positions']}).")
    bad = sum(x["level"] == "bad" for x in r)
    warn = sum(x["level"] == "warn" for x in r)
    verdict = "avoid" if bad else "careful" if warn >= 2 else "take"
    title = {"avoid": "Not recommended", "careful": "Careful", "take": "Looks fine"}[verdict]
    return {"verdict": verdict, "title": title, "reasons": r,
            "note": "Advice from the Risk manager (code, no AI). You decide: you can take it anyway."}
