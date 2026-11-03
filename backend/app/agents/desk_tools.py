"""The toolbox the desk agents reason with. Every tool is deterministic and computed from the platform's own data:
the system account, the daily frames of the researched coins, the quant brain's ranking of ~120 coins, the research
files on disk, live funding, the budget planner, the futures ticket math and the user's own portfolio.

`analysis_cards()` pre-analyses every researched coin once per desk run, so the Scout's top list is "already analysed":
price, distance to the 20-day breakout, trend state, 60-day momentum, ATR and stop, safe leverage, quant rank and
crowding, this coin's own breakout record since 2020, and the system's verdict for today.
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app.agents.toolkit import Tool
from app.logging import log
from app.quant import brain
from app.risk import futures as fut
from app.strategies import daily_trend as dt

HIST = Path("data/history")
_records: dict = {"day": None, "data": {}}


def breakout_records() -> dict[str, dict]:
    """Each researched coin's own daily-breakout record 2020-now (BTC filter, 2.5-ATR stop, fixed 2R exit, futures
    costs): trades, win rate, average R, best R and the last three outcomes. Computed once a day from data/history."""
    today = str(pd.Timestamp.now("UTC").date())
    if _records["day"] == today:
        return _records["data"]
    out: dict[str, dict] = {}
    try:
        from app.maintenance.edge_study import load
        from app.maintenance.leverage_study import SLIP, simulate

        btc_ok = dt.btc_filter(load("1d", "BTCUSDT"))
        for p in sorted((HIST / "1d").glob("*.csv")):
            d = dt.prepare(load("1d", p.stem), btc_ok).reset_index()
            stop = (d.open.shift(-1) * (1 + SLIP) - dt.STOP_ATR * d.atr).to_numpy()
            sig = (d.signal & (dt.STOP_ATR * d.atr / d.close >= dt.MIN_STOP_PCT)).to_numpy()
            trades = simulate(d, p.stem, sig, stop, dt.RR, dt.MAX_HOLD_DAYS)
            if not trades:
                continue
            r = np.array([t.r for t in trades])
            out[p.stem] = {"trades": len(r), "win_rate": round(float((r > 0).mean()), 2), "avg_r": round(float(r.mean()), 2),
                           "best_r": round(float(r.max()), 1), "since": str(trades[0].t.date()),
                           "last_3": [{"date": str(t.t.date()), "r": round(t.r, 2)} for t in trades[-3:]]}
    except Exception as e:  # noqa: BLE001 - the record is an extra; the desk works without it
        log.warning("breakout_records_failed", error=str(e))
    _records.update(day=today, data=out)
    return out


def analysis_cards(acct: dict, frames: dict[str, pd.DataFrame], funding: dict | None = None) -> dict[str, dict]:
    quant = brain.peek() or {}
    qmap = {r["symbol"]: r for r in quant.get("ranks", [])}
    orders = {o["symbol"] for o in acct["next_orders"]}
    watch = {w["symbol"]: w for w in acct["watchlist"]}
    held = {p["symbol"] for p in acct["open_positions"]}
    btc_ok = acct["market"]["btc_above_200d"]
    records = breakout_records()
    funding = funding or {}
    cards = {}
    for sym, f in frames.items():
        row, prev = f.iloc[-1], f.iloc[-2] if len(f) > 1 else f.iloc[-1]
        close, atr = float(row.close), float(row.atr)
        if not np.isfinite(close) or not np.isfinite(atr) or close <= 0:
            continue
        hh = float(row.hh) if np.isfinite(row.hh) else None
        ma50 = float(f.close.rolling(50).mean().iloc[-1]) if len(f) >= 50 else None
        ma200 = float(f.close.rolling(200).mean().iloc[-1]) if len(f) >= 200 else None
        stop_pct = max(dt.STOP_ATR * atr / close, dt.MIN_STOP_PCT)
        dist = round((hh / close - 1) * 100, 1) if hh else None
        if sym in held:
            verdict = "already held by the system account"
        elif not btc_ok:
            verdict = "blocked: BTC is below its 200-day average, the system buys nothing"
        elif bool(row.signal):
            verdict = ("setup live: closed above the 20-day high — system order for the next open" if sym in orders else
                       f"setup live but skipped: {watch.get(sym, {}).get('why', 'risk limits')}")
        elif dist is not None and dist <= 5:
            verdict = f"near: {dist}% below the 20-day high"
        else:
            verdict = "far from a setup"
        q = qmap.get(sym)
        cards[sym] = {
            "symbol": sym, "price": close, "change_1d_pct": round((close / float(prev.close) - 1) * 100, 2),
            "breakout_level": hh, "distance_to_breakout_pct": dist,
            "above_50d": bool(close > ma50) if ma50 else None, "above_200d": bool(close > ma200) if ma200 else None,
            "momentum_60d_pct": round(float(row["rank"]) * 100, 1) if np.isfinite(row["rank"]) else None,
            "atr_pct": round(atr / close * 100, 1), "stop_pct": round(stop_pct * 100, 1),
            "safe_leverage": fut.safe_leverage(stop_pct, sym), "signal_today": bool(row.signal),
            "quant": {"rank": q["rank"], "of": q["of"], "score": q["score"], "trend": q["trend"],
                      "p_up_week": q["p_up_week"], "crowding": q["funding_note"]} if q else None,
            "funding_apr_pct": round(funding[sym], 1) if sym in funding else None,
            "breakout_record": records.get(sym), "verdict": verdict,
        }
    return cards


def _compact(card: dict) -> dict:
    q, rec = card.get("quant") or {}, card.get("breakout_record") or {}
    return {"symbol": card["symbol"], "verdict": card["verdict"], "price": card["price"],
            "distance_to_breakout_pct": card["distance_to_breakout_pct"], "momentum_60d_pct": card["momentum_60d_pct"],
            "trend_rank": f"{q['rank']} of {q['of']}" if q else None, "trend_score": q.get("score"),
            "stop_pct": card["stop_pct"], "safe_leverage": card["safe_leverage"],
            "funding_apr_pct": card["funding_apr_pct"],
            "record": f"{rec['trades']} breakouts since {rec['since']}, {rec['win_rate']:.0%} won, {rec['avg_r']:+.2f}R avg"
            if rec else None}


def reason(c: dict) -> str:
    """One plain-English line that justifies a coin's place in the top list, built only from its analysis card."""
    bits = []
    v = c.get("verdict", "")
    if v.startswith("setup live"):
        bits.append("closed above its 20-day high")
    elif c.get("distance_to_breakout_pct") is not None:
        bits.append(f"{c['distance_to_breakout_pct']}% below its 20-day high")
    if c.get("momentum_60d_pct") is not None:
        bits.append(f"60-day {c['momentum_60d_pct']:+.0f}%")
    if c.get("above_50d") is not None:
        bits.append(("above" if c["above_50d"] else "below") + " its 50-day average")
    q = c.get("quant")
    if q:
        bits.append(f"trend rank {q['rank']} of {q['of']} ({q['trend']})")
    f = c.get("funding_apr_pct")
    if f is not None and f >= 10:
        bits.append(f"crowded: funding {f:+.0f}%/yr")
    r = c.get("breakout_record")
    if r:
        bits.append(f"record {r['trades']} breakouts, {r['win_rate']:.0%} won, {r['avg_r']:+.2f}R")
    if c.get("stop_pct") is not None:
        bits.append(f"stop {c['stop_pct']}% away → safe leverage {c['safe_leverage']}x")
    return ("; ".join(bits) or "no analysis available") + "."


def _sort_key(c: dict) -> tuple:
    v = c["verdict"]
    tier = 0 if v.startswith("setup live: closed") else 1 if v.startswith("setup live") else 2 if v.startswith("near") else 3
    dist = c["distance_to_breakout_pct"] if c["distance_to_breakout_pct"] is not None else 99
    return (tier, dist, -(c["momentum_60d_pct"] or -99))


def research_facts(topic: str) -> dict:
    files = {"system": "portfolio_1d.json", "leverage": "leverage_study.json", "carry": "carry_study.json",
             "win_rate": "leverage_study.json", "shorts": "edge_study_1d.json"}
    name = files.get(topic)
    if not name:
        return {"error": f"unknown topic {topic}", "topics": sorted(files)}
    p = HIST / name
    if not p.exists():
        return {"error": f"no research file for {topic} yet"}
    d = json.loads(p.read_text())
    if topic == "system":
        return {"protocol": "train 2020-23, validation 2024, untouched test 2025-now; $100 start, spot costs 0.1%+0.05%/side",
                "per_mode": {m: {k: {x: v[x] for x in ("end_usd", "return_pct", "yearly_pct", "max_drawdown_pct", "trades",
                                                        "win_rate", "avg_r", "best_r", "profit_factor") if x in v}
                                 for k, v in per.items() if k != "rules"} for m, per in d.items()}}
    if topic == "leverage":
        lq = d["liquidation"]
        return {"trades": lq["trades"], "median_stop_pct": lq["median_stop_pct"],
                "median_mae_of_winners_pct": lq["median_mae_of_winners_pct"], "big_wins": lq["big_wins"],
                "by_leverage": lq["by_leverage"], "tight_stops": d["tight_stops"]}
    if topic == "win_rate":
        return {"same_signal_and_stop_different_targets": d["winrate_vs_target"],
                "note": "no target reaches 90%; the most accurate target earns the least after costs"}
    if topic == "carry":
        return {"rows": d.get("rows", [])[:12], "note": "spot + short-perp hedge; the account earns half the funding APR"}
    rows = [r for r in d["rows"] if r["kind"] == "breakout" and r["side"] == "SHORT" and r["p"].get("n") == 20]
    return {"shorts_20day_breakdown": rows, "note": "shorts failed the 2024 validation (about -0.6R per trade)"}


def build_tools(acct: dict, frames: dict[str, pd.DataFrame], cards: dict[str, dict], extra: dict | None = None,
                filters: dict | None = None, funding: dict | None = None) -> list[Tool]:
    from app.portfolio import budget as budget_plan

    extra, filters, funding = extra or {}, filters or {}, funding or {}
    btc = frames.get("BTCUSDT")

    def market_regime() -> dict:
        m = acct["market"]
        out = {"btc_price": m["btc_close"], "btc_above_200d": m["btc_above_200d"], "plain": m["plain"]}
        if btc is not None and len(btc) > 200:
            ma = float(btc.close.rolling(200).mean().iloc[-1])
            state = btc.btc_ok if m["btc_above_200d"] else ~btc.btc_ok
            out |= {"btc_200d_avg": ma, "btc_vs_200d_pct": round((m["btc_close"] / ma - 1) * 100, 1),
                    "days_in_this_state": int(state.astype(int)[::-1].cumprod().sum())}
        vals = list(cards.values())
        if vals:
            out |= {"coins": len(vals),
                    "share_above_50d_pct": round(100 * np.mean([bool(c["above_50d"]) for c in vals]), 0),
                    "share_above_200d_pct": round(100 * np.mean([bool(c["above_200d"]) for c in vals]), 0),
                    "share_near_breakout_pct": round(100 * np.mean([c["distance_to_breakout_pct"] is not None
                                                                   and c["distance_to_breakout_pct"] <= 3 for c in vals]), 0),
                    "live_setups": [c["symbol"] for c in vals if c["signal_today"]]}
        q = (brain.peek() or {}).get("regime")
        if q:
            out["quant_regime"] = {k: q.get(k) for k in ("label", "btc_trend", "vol_state", "btc_vol_ann_pct", "breadth_pct",
                                                         "btc_30d_pct")}
        return out

    def top_coins(n: int = 10, universe: str = "researched") -> dict:
        n = int(max(1, min(n, 25)))
        if universe == "all":
            q = brain.peek()
            if not q:
                return {"error": "the quant ranking is still computing; use universe='researched'"}
            rows = [{"symbol": r["symbol"], "rank": r["rank"], "of": r["of"], "score": r["score"], "trend": r["trend"],
                     "p_up_week": r["p_up_week"], "mom_28d_pct": r["mom_28d_pct"], "crowding": r["funding_note"],
                     "in_researched_universe": r["symbol"] in cards} for r in q["ranks"][:n]]
            return {"universe": f"quant ranking of {q['coins']} liquid coins (8 trend signals, walk-forward proof)",
                    "proof": q.get("proof", {}).get("long_short"), "coins": rows,
                    "note": "only coins in the researched universe can become system orders"}
        ranked = sorted(cards.values(), key=_sort_key)[:n]
        return {"universe": f"the {len(cards)} researched coins, live setups first, then nearest to a 20-day breakout, "
                            f"then strongest 60-day momentum", "coins": [_compact(c) | {"reason": reason(c)} for c in ranked]}

    def coin_analysis(symbol: str) -> dict:
        sym = symbol.upper()
        sym = sym if sym.endswith("USDT") else sym + "USDT"
        if sym in cards:
            return cards[sym]
        q = brain.coin(sym)
        if q:
            return {"symbol": sym, "in_researched_universe": False, "quant": q,
                    "note": "outside the researched universe: the system never orders it; trend data only"}
        return {"error": f"{sym} is not tracked"}

    def account_state() -> dict:
        a = acct["account"]
        return {"as_of": acct["as_of"], "mode": acct["mode"], "venue": acct["venue"], "paper_only": True,
                "account": {k: a.get(k) for k in ("start_usd", "equity_usd", "return_pct", "trades", "win_rate", "avg_r",
                                                   "max_drawdown_pct", "drawdown_pct", "paused", "open_risk_usd",
                                                   "replay_from", "profit_factor", "best_r", "worst_r")},
                "rules": acct["rules"], "open_positions": acct["open_positions"], "next_orders": acct["next_orders"],
                "watchlist": acct["watchlist"][:8], "recent_trades": acct["recent_trades"][:6],
                "futures": {k: acct["futures"][k] for k in ("leverage_requested", "tickets", "open")} if acct.get("futures") else None}

    def plan_tool(budget: float | None = None, mode: str | None = None, venue: str | None = None,
                  leverage: int | None = None) -> dict:
        p = acct.get("budget_plan") or {}
        if budget is None and mode is None and venue is None and leverage is None and p:
            return p
        return budget_plan.plan(float(budget or p.get("budget_usd") or 100), mode or acct["mode"], venue or acct["venue"],
                                int(leverage or p.get("leverage_wanted") or 3), frames, filters, acct.get("research_proof"))

    def futures_ticket(symbol: str, leverage: int | None = None) -> dict:
        c = coin_analysis(symbol)
        if "error" in c or not c.get("stop_pct"):
            return c
        sym = c["symbol"]
        f = frames[sym].iloc[-1]
        pl = dt.plan(float(f.close), float(f.atr))
        if pl is None:
            return {"error": "stop too tight for the costs"}
        rules, a = acct["rules"], acct["account"]
        risk_usd = a["equity_usd"] * rules["risk_now_pct"] / 100
        per_unit = pl["risk_per_unit"] + f.close * (2 * rules["fee"] + rules["slip"])
        qty = min(risk_usd / per_unit, a["equity_usd"] * rules["max_position_pct"] / 100 / f.close)
        order = {"symbol": sym, "close": float(f.close), "stop": pl["stop"], "target_2r": pl["target"],
                 "size_usd": round(qty * f.close, 2), "risk_usd": round(qty * per_unit, 2),
                 "stop_pct": round(pl["risk_per_unit"] / f.close * 100, 1)}
        want = int(leverage or (acct.get("futures") or {}).get("leverage_requested") or 3)
        return {"hypothetical": "a long opened at the last close under the system's sizing", **fut.ticket(
            order, want, filters.get(sym), funding.get(sym), rules.get("partial_frac", 1 / 3))}

    def funding_now() -> dict:
        return acct.get("carry") or {"error": "no funding data"}

    def my_portfolio() -> dict:
        p = extra.get("portfolio")
        if not p:
            return {"note": "no user portfolio in this context"}
        return {"stats": p["stats"], "open": p["open"], "closed": p["closed"][:10], "budget_plan": p["budget_plan"]}

    num = {"type": "number"}
    return [
        Tool("market_regime", "Market state: BTC vs its 200-day average, days in this state, breadth of the researched "
             "coins, live setups today, and the quant brain's regime label and volatility.", market_regime),
        Tool("top_coins", "Ranked coin list. universe='researched' (default): the coins the system can trade, live "
             "setups first, then nearest to a 20-day breakout, then strongest momentum. universe='all': the quant "
             "brain's trend ranking of ~120 liquid coins with its walk-forward proof.", top_coins,
             {"type": "object", "properties": {"n": {"type": "integer", "description": "how many, 1-25"},
                                               "universe": {"type": "string", "enum": ["researched", "all"]}}}),
        Tool("coin_analysis", "Full pre-computed analysis of one coin: price, breakout level and distance, trend state, "
             "60-day momentum, ATR and stop, safe leverage, quant rank and crowding, funding, this coin's breakout record "
             "since 2020, and the system's verdict today.", coin_analysis,
             {"type": "object", "properties": {"symbol": {"type": "string", "description": "e.g. SOL or SOLUSDT"}},
              "required": ["symbol"]}),
        Tool("research_facts", "Measured research results. topic: 'system' (the $100 account per mode and period), "
             "'leverage' (liquidation by leverage, tight stops), 'win_rate' (win rate vs target), 'carry' (funding "
             "hedge), 'shorts' (why shorts were rejected).", research_facts,
             {"type": "object", "properties": {"topic": {"type": "string",
                                                         "enum": ["system", "leverage", "win_rate", "carry", "shorts"]}},
              "required": ["topic"]}),
        Tool("account_state", "The system account now: equity, return, live replay stats, drawdown, pause state, "
             "rules, open positions, the orders for the next daily open, watchlist, recent trades, futures tickets.",
             account_state),
        Tool("budget_plan", "The settings the system invests with for a budget: risk per trade, limits, typical "
             "position/margin/leverage, which coins are too small, the recommendation and research expectations. "
             "Without arguments: the plan for the user's current budget.", plan_tool,
             {"type": "object", "properties": {"budget": num, "mode": {"type": "string", "enum": ["conservative", "growth",
                                                                                                    "aggressive"]},
                                               "venue": {"type": "string", "enum": ["spot", "futures"]},
                                               "leverage": {"type": "integer"}}}),
        Tool("futures_ticket", "A Binance USD-M ticket for a hypothetical long on a coin now, under the system's sizing: "
             "quantity, margin, safe leverage, liquidation price, stop and take-profit legs, funding per day, warnings.",
             futures_ticket, {"type": "object", "properties": {"symbol": {"type": "string"}, "leverage": {"type": "integer"}},
                              "required": ["symbol"]}),
        Tool("funding_now", "Live 7-day funding of the highest-paying coins and the carry hedge's worth today.", funding_now),
        Tool("my_portfolio", "The user's own recorded trades: open cards with mark, P&L, next action; closed trades; "
             "stats; their budget plan.", my_portfolio),
    ]


def fresh(ts: float, ttl: float) -> bool:
    return time.time() - ts < ttl
