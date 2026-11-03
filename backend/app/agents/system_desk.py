"""System desk: seven specialist agents that run the system account, each owning one measurable job.

  Regime      — is the market in a state where the edge exists? (BTC vs 200-day, breadth)
  Scout       — which coins broke out or are about to (20-day high), ranked by 60-day strength
  Risk        — what each order may risk, what is already at risk, which limits bind
  Leverage    — on Binance Futures: the margin, the liquidation price and the largest leverage the stop allows
  Auditor     — is the live record behaving like the research said it would, or is the edge breaking? And what a
                higher win rate would cost, from the win-rate study
  Carry       — when crowded longs pay high funding, the market-neutral spot + short-perp income it offers
  Manager     — the decision: the orders for the next daily open, or why the account waits

Every number is computed here from the account; the optional LLM briefing may only rephrase these messages.
"""
import json

import numpy as np

from app.agents.llm import complete, provider
from app.logging import log
from app.quant.odds import wilson
from app.risk import futures as fut

AGENTS = {
    "regime": {"name": "Regime", "role": "Market state — BTC trend and breadth", "color": "#38bdf8"},
    "scout": {"name": "Scout", "role": "Breakout hunter — 20-day highs, strongest coins first", "color": "#a78bfa"},
    "risk": {"name": "Risk Officer", "role": "Sizes every order and enforces the limits", "color": "#f59e0b"},
    "leverage": {"name": "Leverage", "role": "Margin, liquidation price and the safe leverage for each ticket",
                 "color": "#fb7185"},
    "auditor": {"name": "Auditor", "role": "Checks the live record against the research", "color": "#f472b6"},
    "carry": {"name": "Carry", "role": "Market-neutral income — funding paid by crowded longs", "color": "#22d3ee"},
    "manager": {"name": "Manager", "role": "Final decision for the next daily open", "color": "#34d399"},
}


def _breadth(frames: dict) -> dict:
    up = [bool(f.close.iloc[-1] > f.close.rolling(50).mean().iloc[-1]) for f in frames.values() if len(f) > 50]
    hi = [bool(f.close.iloc[-1] >= 0.97 * f.hh.iloc[-1]) for f in frames.values() if np.isfinite(f.hh.iloc[-1])]
    return {"above_50d_pct": round(100 * np.mean(up), 0) if up else None,
            "near_20d_high_pct": round(100 * np.mean(hi), 0) if hi else None, "coins": len(up)}


def _coin(sym: str) -> str:
    return sym.replace("USDT", "")


def brief(acct: dict, frames: dict, mode: str) -> list[dict]:
    m, a, r = acct["market"], acct["account"], acct["rules"]
    btc = frames["BTCUSDT"]
    ma200 = float(btc.close.rolling(200).mean().iloc[-1])
    dist = (m["btc_close"] / ma200 - 1) * 100
    state = btc.btc_ok if m["btc_above_200d"] else ~btc.btc_ok
    days_in = int(state.astype(int)[::-1].cumprod().sum())
    br = _breadth(frames)
    study = acct.get("leverage_study") or {}
    futures = acct.get("futures")
    msgs = []

    def say(agent, text, level="info", data=None):
        msgs.append({"agent": agent, **AGENTS[agent], "text": text, "level": level, "data": data or {}})

    trend = "uptrend" if m["btc_above_200d"] else "downtrend"
    say("regime", f"BTC {m['btc_close']:,.0f} is {abs(dist):.1f}% {'above' if dist > 0 else 'below'} its 200-day "
        f"average ({ma200:,.0f}) — {trend} for {days_in} days. {br['above_50d_pct']:.0f}% of {br['coins']} coins are "
        f"above their 50-day average and {br['near_20d_high_pct']:.0f}% are near a 20-day high. "
        + ("Breakouts are allowed." if m["btc_above_200d"] else "The edge does not exist in this state: no buying."),
        "good" if m["btc_above_200d"] else "warn", {"btc_vs_200d_pct": round(dist, 1), **br})

    trig = acct["next_orders"] + [w for w in acct["watchlist"] if w.get("action") == "skip"]
    near = [w for w in acct["watchlist"] if "distance_pct" in w]
    top = acct.get("top_list") or []
    top_txt = (" Top list (analysed): " + "; ".join(f"{_coin(c['symbol'])} — {c['reason'].rstrip('.')}" for c in top[:3]) + "."
               if top else "")
    if trig:
        say("scout", "Broke out at the last daily close: " + ", ".join(
            f"{_coin(t['symbol'])} (60-day {t['momentum_60d_pct']:+.0f}%)" for t in trig) + "." + top_txt, "good")
    elif near:
        say("scout", "No breakout yet. Closest: " + ", ".join(
            f"{_coin(w['symbol'])} {w['distance_pct']}% below its 20-day high" for w in near[:5]) + "." + top_txt)
    elif top:
        say("scout", "No coin is within 5% of its 20-day high, so no setup can trigger tomorrow." + top_txt)
    else:
        say("scout", "No coin is within 5% of its 20-day high. Nothing to hunt today.")

    eq = a["equity_usd"]
    bp = acct.get("budget_plan")
    budget_txt = ""
    if bp:
        budget_txt = (f" Your ${bp['budget_usd']:,.0f}: about ${bp['typical_position_usd']} per trade, "
                      f"{bp['coins_tradeable']} of {bp['coins_total']} coins big enough to trade"
                      + (f" — {bp['recommendation']['text']}" if bp["recommendation"]["switch"] else "."))
    say("risk", f"Mode {mode}: {r['risk_now_pct']:.2g}% risk per trade (${eq * r['risk_now_pct'] / 100:.2f} on "
        f"${eq:.2f}), at most {r['max_positions']} trades and {r['max_open_risk_pct']:.0f}% total risk. Now at risk: "
        f"${a['open_risk_usd']:.2f} across {len(acct['open_positions'])} open trade(s). Drawdown {a['drawdown_pct']}%"
        + (" — risk already reduced by the drawdown brake." if r["risk_now_pct"] < r["risk_pct"] else ".") + budget_txt,
        "warn" if a["drawdown_pct"] >= r["dd_half"] or (bp and bp["recommendation"]["switch"]) else "info")

    # Leverage: the ticket facts for today, then what the study measured on the system's own trades
    lq = study.get("liquidation")
    research = ""
    if lq:
        l20 = lq["by_leverage"]["20x"]
        research = (f" Research on the system's {lq['trades']} trades 2020-now: stops sit about {lq['median_stop_pct']:.0f}% "
                    f"below entry and the median winner first fell {lq['median_mae_of_winners_pct']:.1f}%, so at 20x "
                    f"{l20['liquidated_pct']:.0f}% of trades and {l20['winners_liquidated_pct']:.0f}% of winners were "
                    f"liquidated before paying; the average fell from {lq['base']['E']:+.2f}R to {l20['E_risk_sized']:+.2f}R "
                    f"and $100 at 20x with 20% margin ended at ${l20['end_usd_margin20pct']:.0f}.")
    if futures:
        want = futures["leverage_requested"]
        tickets = futures["tickets"]
        if tickets:
            parts, capped = [], False
            for t in tickets:
                if t["refused"]:
                    parts.append(f"{_coin(t['symbol'])}: {t['refused']}")
                    continue
                capped |= t["leverage_set"] < want
                parts.append(f"{_coin(t['symbol'])}: stop {t['stop']['pct_below']}% away → {t['leverage_set']}x "
                             f"(asked {want}x, max safe {t['leverage_safe_max']}x), margin ${t['margin_usd']:.2f} on "
                             f"${t['notional_usd']:.2f}, liquidation {t['liquidation']:.6g} ({t['liquidation_pct_below']}% below)")
            say("leverage", "; ".join(parts) + "." + (" Leverage was capped so the stop, not Binance, closes a loser."
                                                      if capped else "") + research, "warn" if capped else "good")
        else:
            typical = fut.safe_leverage((lq["median_stop_pct"] if lq else 15) / 100, "XUSDT")
            say("leverage", f"No ticket today. The system's usual stop needs about {typical}x at most; at {want}x an isolated "
                f"long is liquidated {fut.liq_distance(want, 'XUSDT') * 100:.1f}% below entry, long before a "
                f"{(lq['median_stop_pct'] if lq else 15):.0f}% stop." + research,
                "warn" if want > typical else "info")
    else:
        say("leverage", "Spot account: no leverage, no liquidation. Switch the venue to Binance Futures to get the same "
            "trades as tickets with the leverage each stop allows." + research, "warn" if research else "info")

    proof = (acct.get("research_proof") or {}).get(mode, {}).get("all 2020-now", {})
    n, wins = a.get("trades", 0), round(a.get("win_rate", 0) * a.get("trades", 0))
    rows = {x["target_R"]: x["pooled"] for x in study.get("winrate_vs_target", []) if "pooled" in x}
    wr_truth = ""
    if {0.25, 2.0, 3.0} <= set(rows):
        wr_truth = (f" Win-rate study: a 0.25R target won {rows[0.25]['win']:.0%} of {rows[0.25]['n']} trades for "
                    f"{rows[0.25]['E']:+.2f}R each after costs; 2R won {rows[2.0]['win']:.0%} for {rows[2.0]['E']:+.2f}R; "
                    f"3R won {rows[3.0]['win']:.0%} for {rows[3.0]['E']:+.2f}R. No setting reaches 90%, and the most accurate "
                    f"one earns the least.")
    if n:
        lo, hi = wilson(wins, n)
        exp_w = proof.get("win_rate")
        ok = exp_w is None or lo <= exp_w <= hi
        say("auditor", f"Live replay: {n} trades, {wins} won ({wins / n:.0%}, likely range {lo:.0%}-{hi:.0%}), average "
            f"{a.get('avg_r', 0):+.2f}R. Research expects {exp_w:.0%} wins and {proof.get('avg_r', 0):+.2f}R. "
            + ("Consistent with the research." if ok else "Outside the expected range — watch closely.")
            + (" Loss guard has paused new trades." if a["paused"] else "") + wr_truth,
            "good" if ok and not a["paused"] else "warn", {"n": n, "wins": wins, "range": [round(lo, 2), round(hi, 2)]})
    else:
        say("auditor", "No closed trades in the replay window yet — nothing to grade." + wr_truth)

    c = acct.get("carry") or {}
    if c.get("top_7d_apr"):
        fund_top = ", ".join(f"{_coin(t['symbol'])} {t['apr_pct']:+.1f}%" for t in c["top_7d_apr"])
        rs = (c.get("research") or {}).get("TEST 2025-now", {})
        if c["worth_it"]:
            say("carry", f"Highest funding (7-day, per year): {fund_top}. Above the {c['enter_apr_pct']:.0f}% bar: "
                f"{', '.join(_coin(s) for s in c['worth_it'])} — a spot + short-perp hedge would earn about "
                f"{c['account_apr_pct']}% a year on the account with almost no price risk.", "good")
        else:
            say("carry", f"Highest funding (7-day, per year): {fund_top}. All below the {c['enter_apr_pct']:.0f}% bar, so "
                f"the hedge would not beat its costs. (Research: about 8%/year in 2020-23, "
                f"{rs.get('yearly_pct', 0)}%/year since 2025 as funding dried up.)")
    if acct["next_orders"]:
        if futures:
            by_sym = {t["symbol"]: t for t in futures["tickets"]}
            say("manager", "Decision: " + "; ".join(
                f"LONG {_coin(o['symbol'])} perpetual at the next daily open, {t['qty']} coins (${t['notional_usd']:.2f}) at "
                f"{t['leverage_set']}x isolated, margin ${t['margin_usd']:.2f}, stop {t['stop']['trigger']:.6g} "
                f"(-{t['stop']['pct_below']}%), take profit on a third at {o['target_2r']:.6g} (2R), risk ${o['risk_usd']:.2f}"
                if not t["refused"] else f"skip {_coin(o['symbol'])} ({t['refused']})"
                for o in acct["next_orders"] for t in [by_sym[o["symbol"]]]) + ".", "good")
        else:
            say("manager", "Decision: " + "; ".join(
                f"BUY {_coin(o['symbol'])} at the next daily open, ${o['size_usd']:.2f} size, stop "
                f"{o['stop']:.6g} (-{o['stop_pct']}%), sell a third at {o['target_2r']:.6g} (2R), risk ${o['risk_usd']:.2f}"
                for o in acct["next_orders"]) + ".", "good")
    elif not m["btc_above_200d"]:
        say("manager", "Decision: stay in cash. Buying breakouts in a BTC downtrend lost money in the research.", "warn")
    elif a["paused"]:
        say("manager", "Decision: no new trades until the loss guard clears.", "warn")
    else:
        say("manager", "Decision: wait. Open trades keep their stops; a new order appears only after a daily close "
            "above a 20-day high." + (" Watch: " + ", ".join(_coin(c["symbol"]) for c in top[:3]) + " — the closest to a "
                                      "setup on the top list." if top else ""))
    return msgs


async def llm_briefing(msgs: list[dict]) -> str | None:
    """Optional: one short plain-English paragraph from the agents' messages. No new numbers allowed."""
    if not provider():
        return None
    try:
        return await complete(
            "You are the head of a trading desk writing a 3-sentence briefing for a beginner. Use ONLY facts and numbers "
            "in the messages. Never add a number, price, coin or prediction that is not in them. No hype.",
            json.dumps([{"agent": m["name"], "text": m["text"]} for m in msgs]), max_tokens=1500)  # room for reasoning tokens
    except Exception as e:  # noqa: BLE001 - the deterministic messages are the product; the LLM is optional
        log.warning("system_briefing_failed", error=str(e))
        return None
