"""Validators: check the desk's research before the decision, the way a senior reviewer would.

Six groups of checks, each check pass / warn / fail with the measured detail:

  Data quality          is the data fresh, deep enough, and is the live price still near the analysed close?
  Research coverage     did the research look at the higher timeframe, positioning, news, the ten methods, the backtest
                        evidence and (with AI) the knowledge base? Gaps are FILLED by the validator itself, then checked.
  Trade checks          the plan against the research rules: side vs chart read and higher timeframe, regime stress,
                        reward/risk >= 2 after fees, costs <= 20 % of the risk, stop 1-4 ATR, liquidation >= 1.5x the
                        stop, risk within the mode, crowded funding, shorts, and (warnings only) a supply overhang
                        on multi-day longs and a thin or churning market.
  Evidence & base rates independent evidence families for the side, the engine's validated setup, the strategy's
                        backtest, the methods with a measured edge that agree.
  Fact-check            every number an agent wrote is traced to platform data (tool results, the analysis, the plan,
                        the playbook). Numbers that trace nowhere are listed as unverified.
  Consistency           the Head Trader's call against the analysts (the fundamentals lead votes on 4h and 1d), the
                        validators and its own confidence.

Critical failures (stale data, a plan that fails reward/risk or costs, a side against both the chart and the higher
timeframe, a decision built on unverified numbers) turn a LONG/SHORT into WAIT. The report has a 0-100 research
quality score and a grade, so a reader sees at a glance how much the analysis can be trusted.
"""
import bisect
import json
import re
import time
from pathlib import Path

from app.risk import plan as tp

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
HIGHER = {"15m": "4h", "1h": "4h", "4h": "1d", "1d": "1d"}
STRESS = ("liquidity_stress", "news_instability", "high_volatility")
FAMILIES = ("big_picture", "structure", "liquidity", "zones", "imbalance", "premium_discount", "momentum", "derivatives",
            "elliott", "news")
GROUPS = ["Data quality", "Research coverage", "Trade checks", "Market & execution", "Evidence & base rates", "Fact-check",
          "Consistency"]
HOLD_DAYS = {"15m": 0.25, "1h": 1, "4h": 3, "1d": 10}  # a typical holding time per timeframe, for carrying costs
PLAYBOOK = Path(__file__).resolve().parents[3] / "docs" / "AGENT_PLAYBOOK.md"
_NUM = re.compile(r"[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def check(group: str, name: str, status: str, detail: str, critical: bool = False) -> dict:
    return {"group": group, "name": name, "status": status, "detail": detail, "critical": critical and status == "fail"}


# ------------------------------------------------------------------------------------------- fact-check

def numbers(text: str) -> list[tuple[str, float, int, bool]]:
    """Numbers a reader would take as facts: (token, value, decimals, is_percent). Timeframes (4h, 15m), small whole
    counts (<= 10: '3 methods', '2R', '5x'), years and zero are skipped."""
    out = []
    for m in _NUM.finditer(text or ""):
        tok = m.group(0)
        nxt = text[m.end():m.end() + 2]
        if nxt[:1] in ("m", "h", "d", "w") and not nxt[1:2].isalpha():
            continue  # a timeframe
        if m.start() > 0 and (text[m.start() - 1].isalpha() or text[m.start() - 1] == "_"):
            continue  # part of a word or symbol (W3, 1000PEPE)
        raw = tok.replace("$", "").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        dec = len(raw.split(".")[1]) if "." in raw else 0
        if v == 0 or (dec == 0 and abs(v) <= 10) or (dec == 0 and 1990 <= abs(v) <= 2035):
            continue
        out.append((tok, v, dec, nxt[:1] == "%"))
    return out


def corpus(*objs) -> list[float]:
    """Every number in the given data (dicts, lists, strings), as sorted absolute values."""
    vals: set[float] = set()
    for o in objs:
        s = o if isinstance(o, str) else json.dumps(o, default=str)
        for m in re.finditer(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s):
            try:
                v = abs(float(m.group(0)))
            except ValueError:
                continue
            if v:
                vals.add(v)
    return sorted(vals)


def _near(x: float, dec: int, cp: list[float]) -> bool:
    tol = max(0.5 * 10 ** -dec + 1e-9, x * 0.006)
    i = bisect.bisect_left(cp, x - tol)
    return i < len(cp) and cp[i] <= x + tol


def traced(v: float, dec: int, pct: bool, cp: list[float]) -> bool:
    x = abs(v)
    return _near(x, dec, cp) or (pct and _near(x / 100, dec + 2, cp))


def fact_check(text: str, cp: list[float]) -> dict:
    nums = numbers(text)
    bad = [tok for tok, v, d, pct in nums if not traced(v, d, pct, cp)]
    return {"checked": len(nums), "traced": len(nums) - len(bad), "unverified": bad[:8]}


def session_corpus(s: dict, ctx, results: list[str]) -> list[float]:
    from app.agents.research_desk import _compact_walk, _deriv_news

    rp = tp.RISK_MODES[tp.mode_of(s)]
    ctx_nums = {"budget": s["budget"], "risk_pct": rp["risk_pct"], "risk_usd": s["budget"] * rp["risk_pct"] / 100,
                "cap_pct": rp["max_position_pct"], "cap_usd": s["budget"] * rp["max_position_pct"] / 100,
                "price": ctx.price, "rules": [tp.MIN_RR, tp.MAX_COST_SHARE * 100, 1.5, 20, 10],
                "margin": s.get("margin"), "margin_share_pct": tp.MARGIN_RISK[tp.mode_of(s)] * 100,
                "margin_risk_usd": (s.get("margin") or 0) * tp.MARGIN_RISK[tp.mode_of(s)]}
    walks = [_compact_walk(w, sn) for w, sn in ctx.walks.values()]
    raw = [w for w, _ in ctx.walks.values()]
    extra = [ctx.plans, getattr(ctx, "evidence", None), [{"o": m.get("observation"), "b": m.get("bullets")}
                                                         for m in (ctx.schools or [])]]
    pb = PLAYBOOK.read_text() if PLAYBOOK.exists() else ""
    return corpus(ctx_nums, walks, raw, _deriv_news(ctx.snap), ctx.snap.get("regime"), *extra, pb, *results)


# ------------------------------------------------------------------------------------------- groups

def data_quality(s: dict, ctx) -> list[dict]:
    g, tf, snap = "Data quality", s["tf"], ctx.snap
    out = []
    close_ms = snap.get("as_of_close_time") or snap.get("as_of") or 0
    age = time.time() * 1000 - close_ms
    lim = 2 * TF_MS.get(tf, 3_600_000)
    out.append(check(g, "Fresh candles", "pass" if age <= lim else "fail",
                     f"last closed {tf} candle {age / 60000:.0f} min old (limit {lim / 60000:.0f} min)", critical=True))
    bars = snap.get("bars_used") or 0
    out.append(check(g, "Enough history", "pass" if bars >= 300 else "warn" if bars >= 120 else "fail",
                     f"{bars} candles analysed (300+ wanted)"))
    px, close = ctx.price, float(snap["price"])
    drift = abs(px / close - 1) * 100 if close else 0
    out.append(check(g, "Live price near the analysed close", "pass" if drift <= 1 else "warn" if drift <= 3 else "fail",
                     f"live {px:.6g} vs close {close:.6g} ({drift:.2f}% apart)", critical=drift > 3))
    atr = float((snap.get("indicators") or {}).get("atr14") or 0)
    if close:
        ap = atr / close * 100
        out.append(check(g, "Coin moves enough to trade", "pass" if ap >= 0.15 else "fail",
                         f"ATR {ap:.3f}% of the price" + ("" if ap >= 0.15 else " — pegged or dead: fees beat any move"),
                         critical=ap < 0.15))
    ob = snap.get("orderbook") or {}
    if ob.get("spread_bps") is not None:
        sp = float(ob["spread_bps"])
        out.append(check(g, "Liquid market", "pass" if sp <= 10 else "warn", f"spread {sp:.1f} bps"))
    return out


async def research_coverage(s: dict, ctx, tools: dict, agents: list[dict]) -> tuple[list[dict], list[dict]]:
    """Coverage checks; missing research is run by the validator (gap filling) and recorded."""
    g, tf = "Research coverage", s["tf"]
    out, filled = [], []
    htf = HIGHER.get(tf, tf)
    if htf != tf and htf not in ctx.walks:
        try:
            await ctx.walk(htf)
            filled.append({"name": "chart_analysis", "args": {"timeframe": htf}, "ok": True})
        except Exception as e:  # noqa: BLE001
            filled.append({"name": "chart_analysis", "args": {"timeframe": htf}, "ok": False, "error": str(e)[:80]})
    if htf in ctx.walks and htf != tf:
        a = ctx.walks[tf][0]["synthesis"]["direction"]
        b = ctx.walks[htf][0]["synthesis"]["direction"]
        out.append(check(g, "Higher timeframe checked", "pass" if a == b or "neutral" in (a, b) else "warn",
                         f"{tf} reads {a}, {htf} reads {b}" + ("" if a == b else " — timeframes disagree")))
    else:
        out.append(check(g, "Higher timeframe checked", "pass" if tf == "1d" else "warn",
                         "daily chart is the top timeframe here" if tf == "1d" else "higher timeframe unavailable"))
    if ctx.schools is None:
        r = await tools["method_views"].fn()
        filled.append({"name": "method_views", "args": {}, "ok": "error" not in r})
    out.append(check(g, "Ten classic methods reviewed", "pass" if ctx.schools else "warn",
                     f"{len(ctx.schools or [])} method views"))
    ctx.evidence = await tools["strategy_evidence"].fn()
    filled.append({"name": "strategy_evidence", "args": {}, "ok": True})
    d = (ctx.snap.get("derivatives") or {})
    out.append(check(g, "Futures positioning", "pass" if d.get("available") else "warn",
                     "funding, open interest and ratios read" if d.get("available") else
                     "no perpetual futures data for this coin: positioning is unknown"))
    news = ctx.snap.get("news") or []
    out.append(check(g, "News read", "pass", f"{len(news)} headlines in the window"))
    from app.quant import brain

    out.append(check(g, "Whole-market regime", "pass" if brain.peek() else "warn",
                     ((brain.peek() or {}).get("regime") or {}).get("label") or "quant regime still computing"))
    if getattr(ctx, "fund", None) is None and "tokenomics" in tools:
        r = await tools["tokenomics"].fn()
        filled.append({"name": "tokenomics", "args": {}, "ok": r.get("available") is not False})
    fund = getattr(ctx, "fund", None) or {}
    out.append(check(g, "Fundamentals read", "pass" if fund.get("tokenomics") else "warn",
                     f"supply, valuation and standing for {fund.get('name') or s.get('symbol')}"
                     + (f"; unknown: {'; '.join(fund['unavailable'][:2])}" if fund.get("unavailable") else "")
                     if fund.get("tokenomics") else "; ".join(fund.get("unavailable", [])[:2]) or "fundamentals unavailable"))
    ai = [a for a in agents if a.get("by") == "ai" and a["id"] not in ("validator",)]
    if ai:
        # a lead's specialists researching with tools counts as the lead's research
        idle = [a["name"] for a in ai if not a["tools_used"] and not any(x["tools_used"] for x in a.get("subs", []))]
        out.append(check(g, "Every agent researched with tools", "pass" if not idle else "warn",
                         "all agents called tools" if not idle else f"answered without tools: {', '.join(idle)}"))
        kn = sum(1 for a in ai for t in [*a["tools_used"], *(u for x in a.get("subs", []) for u in x["tools_used"])]
                 if t["name"] == "search_knowledge")
        out.append(check(g, "Knowledge base consulted", "pass" if kn else "warn", f"{kn} knowledge searches"))
    return out, filled


def trade_checks(s: dict, ctx, plan: dict, side: str) -> list[dict]:
    g = "Trade checks"
    walk = ctx.walks[s["tf"]][0]
    syn = walk["synthesis"]
    want = "bullish" if side == "LONG" else "bearish"
    other = "bearish" if side == "LONG" else "bullish"
    out = []
    agree = syn.get("agreement") or 0
    st = "pass" if syn["direction"] == want and agree >= 0.6 else "fail" if syn["direction"] == other else "warn"
    out.append(check(g, "Side matches the chart read", st,
                     f"chart reads {syn['direction']} ({agree:.0%} agreement, {syn.get('confidence')} confidence)"))
    htf = HIGHER.get(s["tf"], s["tf"])
    hd = ctx.walks[htf][0]["synthesis"]["direction"] if htf in ctx.walks else None
    bp = next((x for x in walk["steps"] if x["key"] == "big_picture"), {})
    hv = hd or bp.get("verdict_word")
    out.append(check(g, "Side matches the higher timeframe", "pass" if hv == want else "fail" if hv == other else "warn",
                     f"{htf if hd else 'higher timeframes'}: {hv or 'unknown'}"))
    if syn["direction"] == other and hv == other:
        out.append(check(g, "Not against the whole picture", "fail",
                         f"both the {s['tf']} read and the higher timeframe are {other}", critical=True))
    reg = walk.get("regime")
    out.append(check(g, "Regime not stressed", "warn" if reg in STRESS else "pass", f"regime {reg}"))
    if not plan or "stop" not in plan:
        out.append(check(g, "Plan computed", "fail", (plan or {}).get("error") or "no plan", critical=True))
        return out
    out.append(check(g, "Order valid on Binance", "pass" if plan.get("ok") else "fail",
                     plan.get("error") or f"{plan['qty']} units, ${plan['notional_usd']} position", critical=True))
    rr = plan.get("rr_net") or 0
    out.append(check(g, "Reward/risk >= 2 after fees", "pass" if rr >= tp.MIN_RR else "fail",
                     f"{rr}R to the target after fees", critical=True))
    cs = plan.get("cost_share_of_risk_pct") or 0
    out.append(check(g, "Costs <= 20% of the risk", "pass" if cs <= tp.MAX_COST_SHARE * 100 else "fail",
                     f"fees and slippage are {cs}% of the risk", critical=True))
    atr = float((ctx.snap.get("indicators") or {}).get("atr14") or 0)
    if atr:
        k = abs(plan["entry"] - plan["stop"]) / atr
        out.append(check(g, "Stop is a real invalidation (1-4 ATR)", "pass" if 1 <= k <= 4 else "warn",
                         f"stop {k:.1f} ATR from the entry"))
    if plan.get("liquidation"):
        x = plan.get("liquidation_vs_stop_x") or 0
        out.append(check(g, "Liquidation far beyond the stop", "pass" if x >= 1.5 else "fail",
                         f"liquidation {x}x the stop distance away (1.5x minimum)", critical=x < 1))
    if plan.get("sizing") == "margin":
        lim = tp.MARGIN_RISK[tp.mode_of(s)] * 100
        got = plan.get("risk_pct_of_margin") or 0
        out.append(check(g, "Risk within the style", "pass" if got <= lim + 0.05 else "fail",
                         f"a stop-out loses {got}% of the ${plan.get('margin_input')} margin ({tp.mode_of(s)} allows "
                         f"{lim:g}%)", critical=True))
    else:
        lim = tp.RISK_MODES[tp.mode_of(s)]["risk_pct"]
        out.append(check(g, "Risk within the mode", "pass" if plan["risk_pct_of_budget"] <= lim + 0.05 else "fail",
                         f"{plan['risk_pct_of_budget']}% of the budget at the stop ({tp.mode_of(s)} allows {lim:g}%)",
                         critical=True))
    f = (ctx.snap.get("derivatives") or {}).get("funding_annualized_pct")
    if f is not None:
        crowded = (side == "LONG" and f > 30) or (side == "SHORT" and f < -30)
        out.append(check(g, "Positioning not crowded on our side", "warn" if crowded else "pass",
                         f"funding {f:+.1f}% a year" + (" — the crowd is already there" if crowded else "")))
    if side == "SHORT":
        out.append(check(g, "Shorts research", "warn", "shorts failed the daily system's 2024 validation (about -0.6R)"))
    return out + fundamental_checks(s, ctx, side) + market_checks(s, ctx, plan, side)


def market_checks(s: dict, ctx, plan: dict, side: str) -> list[dict]:
    """How the trade meets the real market: the whole market's tide, what stands between the entry and the target, a
    stop parked on resting liquidity, carrying costs, fresh high-impact news and the spread. Warnings only — each is a
    common way a sound chart read still loses — so they lower the evidence score but never block on their own."""
    g = "Market & execution"
    out: list[dict] = []
    snap = ctx.snap
    from app.quant import brain

    reg = (brain.peek() or {}).get("regime") or {}
    t, br = reg.get("btc_trend"), reg.get("breadth_pct")
    if t and br is not None:
        against = (side == "LONG" and t == "bear" and br < 45) or (side == "SHORT" and t == "bull" and br > 55)
        out.append(check(g, "With the market's tide", "warn" if against else "pass",
                         f"BTC trend {t}, {br:.0f}% of coins above their 50-day average"
                         + (f" — a {side.lower()} against a {'risk-off' if side == 'LONG' else 'risk-on'} market"
                            if against else "")))
    if not plan or "stop" not in plan:
        return out
    e, stop, tgt = plan["entry"], plan["stop"], plan["target"]
    risk = abs(e - stop)
    if risk:
        opp = "resistance" if side == "LONG" else "support"
        between = [lv for lv in (snap.get("price_action") or {}).get("levels") or []
                   if lv.get("type") == opp and (lv.get("touches") or 0) >= 2 and min(e, tgt) < lv["price"] < max(e, tgt)]
        near = min(between, key=lambda lv: abs(lv["price"] - e), default=None)
        r = abs(near["price"] - e) / risk if near else None
        out.append(check(g, "Clear path to the target", "warn" if r is not None and r < 1 else "pass",
                         f"{opp} at {near['price']:.6g} ({near.get('touches')} touches) only {r:.1f}R away, before the "
                         f"target" if r is not None and r < 1 else
                         (f"first {opp} at {near['price']:.6g} is {r:.1f}R away" if near else
                          f"no tested {opp} between the entry and the target")))
    atr = float((snap.get("indicators") or {}).get("atr14") or 0)
    if atr:
        pools = [x["price"] for x in (snap.get("smc") or {}).get("liquidity") or []
                 if x.get("status") == "resting" and isinstance(x.get("price"), (int, float))]
        hit = min(pools, key=lambda p: abs(p - stop), default=None)
        on = hit is not None and abs(hit - stop) < 0.15 * atr
        out.append(check(g, "Stop not parked on resting liquidity", "warn" if on else "pass",
                         f"stop {stop:.6g} is within {abs(hit - stop) / atr:.2f} ATR of resting liquidity at {hit:.6g} — "
                         "the obvious place for a stop hunt; put it beyond the pool" if on else
                         "the stop sits away from resting liquidity pools"))
    fpd = plan.get("funding_usd_per_day")
    rew = plan.get("reward_usd") or 0
    if isinstance(fpd, (int, float)) and rew > 0:
        days = HOLD_DAYS.get(s["tf"], 1)
        cost = max(0.0, -fpd) * days  # negative = the position pays
        share = cost / rew * 100
        out.append(check(g, "Funding cost over the hold", "warn" if share > 10 else "pass",
                         f"paying about ${cost:.2f} over a typical {days:g}-day hold, {share:.0f}% of the target profit"
                         if cost else f"funding pays this side (about ${fpd * days:+.2f} over a {days:g}-day hold)"))
    now = time.time()
    hot = []
    for n in snap.get("news") or []:
        if n.get("impact") != "high":
            continue
        ts = n.get("published_at") or n.get("ts")
        try:
            sec = float(ts) / (1000 if float(ts) > 1e11 else 1)
        except (TypeError, ValueError):
            try:
                from datetime import datetime

                sec = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
        if now - sec <= 86_400:
            hot.append(n.get("title") or "untitled")
    out.append(check(g, "No fresh high-impact news", "warn" if hot else "pass",
                     f"{len(hot)} high-impact headline(s) in the last 24h: {hot[0][:90]}" if hot else
                     "no high-impact headline in the last 24h"))
    dp = getattr(ctx, "depth", None) or {}
    if dp and plan.get("stop_pct"):
        legs = [dp.get("buy_slippage_bps"), dp.get("sell_slippage_bps")]
        if None in legs:
            out.append(check(g, "Book can take the size", "warn",
                             f"the visible order book is too thin for a ${plan.get('notional_usd')} market order"))
        else:
            rt = sum(legs)  # in and out
            share = rt / (plan["stop_pct"] * 100) * 100
            out.append(check(g, "Book can take the size", "warn" if share > 10 else "pass",
                             f"${plan.get('notional_usd')} in and out slips about {rt:.1f} bps, {share:.1f}% of the stop "
                             "distance (live order book)"))
    sp = (snap.get("orderbook") or {}).get("spread_bps")
    if isinstance(sp, (int, float)) and plan.get("stop_pct"):
        share = sp / (plan["stop_pct"] * 100) * 100
        out.append(check(g, "Spread small against the stop", "warn" if share > 5 else "pass",
                         f"spread {sp:.1f} bps is {share:.1f}% of the stop distance"))
    return out


def fundamental_checks(s: dict, ctx, side: str) -> list[dict]:
    """Supply and liquidity warnings from the coin's fundamentals (docs/FUNDAMENTALS.md). Warnings only: the bands are
    general market practice, not measured by this project, so they lower confidence but never block a trade."""
    g = "Trade checks"
    f = getattr(ctx, "fund", None) or {}
    tok, st = f.get("tokenomics") or {}, f.get("standing") or {}
    out = []
    x = tok.get("fdv_to_market_cap")
    if isinstance(x, (int, float)) and side == "LONG" and s["tf"] in ("4h", "1d"):
        out.append(check(g, "Supply overhang on a multi-day long", "warn" if x >= 2 else "pass",
                         f"FDV is {x}x the market cap" + (" — heavy dilution ahead fights a long held for days" if x >= 2
                                                           else "")))
    vol, vm = st.get("volume_24h_usd"), st.get("volume_to_market_cap_pct")
    if isinstance(vol, (int, float)):
        thin = vol < 10_000_000 or (isinstance(vm, (int, float)) and vm < 1)
        churn = isinstance(vm, (int, float)) and vm > 25
        out.append(check(g, "Liquid market", "warn" if thin or churn else "pass",
                         f"${vol / 1e6:,.0f}M traded in 24h" + (f", {vm}% of the market cap" if vm is not None else "")
                         + (" — thin: expect slippage and gaps" if thin else
                            " — speculative churn that tends to mean-revert" if churn else "")))
    return out


def evidence(s: dict, ctx, side: str) -> list[dict]:
    from app.agents.room import track_record

    g = "Evidence & base rates"
    want = "bullish" if side == "LONG" else "bearish"
    steps = {x["key"]: x for x in ctx.walks[s["tf"]][0]["steps"]}
    fam = [steps[k]["title"].split(". ", 1)[-1] for k in FAMILIES if steps.get(k, {}).get("verdict_word") == want]
    out = [check(g, "Independent evidence families agree", "pass" if len(fam) >= 3 else "warn" if fam else "fail",
                 f"{len(fam)} for the {side.lower()}: {', '.join(fam[:5]) or 'none'}")]
    ev = getattr(ctx, "evidence", None) or {}
    setups = [x for x in ev.get("setups_on_this_chart", []) if x["direction"] == side]
    best = next((x for x in setups if x.get("passed_all_checks") and x["stage"] == "confirmed"), None)
    out.append(check(g, "Engine has a validated setup", "pass" if best else "warn",
                     f"{best['strategy'].replace('_', ' ')} confirmed, all checks passed" if best else
                     (f"{len(setups)} setup(s) for this side, none confirmed and validated" if setups else
                      "no engine setup for this side: discretionary trade")))
    ref = best or (setups[0] if setups else None)
    bt = (ref or {}).get("backtest")
    if ref:
        if not bt:
            out.append(check(g, "Strategy backtest", "warn", f"no backtest for {ref['strategy']} on {s['tf']} yet"))
        else:
            st = "fail" if bt["status"] == "suspended" else "pass" if (bt.get("expectancy_r") or 0) > 0 else "warn"
            out.append(check(g, "Strategy backtest", st, f"{ref['strategy']} on {s['tf']}: {bt['status']}, "
                             f"{bt.get('expectancy_r', 0):+.2f}R per trade over {bt.get('n')} trades"))
        odds = ref.get("past_cases")
        if odds and odds.get("n"):
            out.append(check(g, "Past cases", "pass" if odds.get("n", 0) >= 30 else "warn",
                             f"{odds.get('basis', 'similar setups')}: {odds.get('n')} cases"))
    rec = (track_record(s["tf"]) or {}).get("schools") or {}
    useful = [m["title"] for m in (ctx.schools or []) if m.get("verdict_word") == want and (rec.get(m["key"]) or {}).get("useful")]
    out.append(check(g, "Methods with a measured edge agree", "pass" if useful else "warn",
                     ", ".join(useful) if useful else "no method with a proven out-of-sample edge supports this side"))
    out += model_checks(ctx.symbol, side)
    return out


def model_checks(symbol: str, side: str) -> list[dict]:
    """The trained models' view of this side, counted only for models accepted on years they never saw."""
    from app.ml import forecast

    g = "Evidence & base rates"
    r = forecast.for_symbol(symbol)
    if not r.get("available") or not r.get("covered"):
        return [check(g, "Trained models", "warn", r.get("note") or "no model forecast for this coin")]
    by = {m["target"]: m for m in r["models"]}
    out = []
    m = by.get("long_tp_first" if side == "LONG" else "short_tp_first")
    if m and m["value"] is not None:
        edge = m["value"] - (m["base_rate"] or 0)
        if m["status"] == "accepted":
            st = "pass" if edge >= 0.03 else "warn" if edge > -0.03 else "fail"
        else:
            st = "warn"
        out.append(check(g, "Trained model: target before stop", st,
                         f"{m['value']:.0%} chance the {side.lower()} hits its target first vs {m['base_rate']:.0%} "
                         f"usually ({m['status']} model)"))
    d = by.get("drop_risk_7d")
    if d and d["value"] is not None and d["status"] == "accepted" and side == "LONG":
        hi = d["value"] >= (d["base_rate"] or 0) * 1.5
        out.append(check(g, "Trained model: drop risk", "warn" if hi else "pass",
                         f"{d['value']:.0%} chance of a 2-ATR drop within 7 days vs {d['base_rate']:.0%} usually"))
    return out


def _price_in(text: str, s: dict) -> float | None:
    """The first number in the text that looks like a price for this coin (within 30% of the analysed price)."""
    ref = (s.get("research") or {}).get("price")
    if not ref:
        return None
    for _tok, v, _dec, pct in numbers(text):
        if not pct and 0.7 * ref <= v <= 1.3 * ref:
            return v
    return None


def consistency(s: dict, decision: dict, pre: dict, plan: dict | None = None) -> list[dict]:
    g = "Consistency"
    out = []
    act = decision.get("action")
    voters = ("technician", "flow", "fundamental", "quant") if s["tf"] in ("4h", "1d") else ("technician", "flow", "quant")
    views = [a["view"] for a in s["agents"] if a["id"] in voters and a.get("view")]
    if act in ("LONG", "SHORT"):
        against = "bearish" if act == "LONG" else "bullish"
        n = sum(v == against for v in views)
        out.append(check(g, "Decision backed by the analysts", "fail" if views and n == len(views) else "warn" if n else "pass",
                         f"analysts: {', '.join(views) or 'no view'}", critical=bool(views) and n == len(views)))
        crit = [c["name"] for c in pre.get("checks", []) if c["critical"]]
        out.append(check(g, "Decision respects the validators", "fail" if crit else "pass",
                         "critical: " + ", ".join(crit) if crit else "no critical failure", critical=bool(crit)))
    leads = [a for a in s["agents"] if a["id"] in ("researcher", "technician", "flow", "fundamental", "quant")
             and a.get("view") in ("bullish", "bearish") and (a.get("confidence") or 0) >= 0.6]
    sides = {a["view"] for a in leads}
    if act in ("LONG", "SHORT"):
        out.append(check(g, "Desk not split", "warn" if len(sides) == 2 else "pass",
                         "confident leads disagree: " + ", ".join(f"{a['name']} {a['view']}" for a in leads)
                         if len(sides) == 2 else "no confident lead argues the other side"))
        px = _price_in(decision.get("invalidation") or "", s)
        entry = (plan or {}).get("entry") or (s.get("research") or {}).get("price")
        if px and entry:
            wrong = (act == "LONG" and px >= entry) or (act == "SHORT" and px <= entry)
            out.append(check(g, "Invalidation on the right side", "fail" if wrong else "pass",
                             f"invalidation {px:.6g} is {'above' if px > entry else 'below'} the {entry:.6g} entry"
                             + (f" — a {act.lower()} is wrong {'below' if act == 'LONG' else 'above'} the entry, not "
                                f"{'above' if act == 'LONG' else 'below'}" if wrong else "")))
    conf = decision.get("confidence")
    if isinstance(conf, (int, float)) and act in ("LONG", "SHORT"):
        from app.agents import calibration

        b = next((x for x in calibration.reliability().get("buckets", [])
                  if x["lo"] <= conf < x["hi"] or (conf >= x["lo"] and x["hi"] >= 1)), None)
        if b and b.get("verdict") in ("overconfident", "underconfident"):
            out.append(check(g, "Confidence matches the desk's record", "warn",
                             f"calls at {b['range']} confidence won {b['win_rate']:.0%} of {b['n']} graded times "
                             f"({b['verdict']})"))
    if isinstance(conf, (int, float)) and conf > 0.85:
        out.append(check(g, "Confidence within the desk's cap", "warn", f"confidence {conf:.0%} is above the 85% cap"))
    ev = pre.get("evidence_score", 100)
    if isinstance(conf, (int, float)) and act in ("LONG", "SHORT"):
        over = conf > 0.75 and ev < 70
        out.append(check(g, "Confidence fits the evidence", "warn" if over else "pass",
                         f"confidence {conf:.0%} with trade evidence {ev}/100"))
    return out


# ------------------------------------------------------------------------------------------- report

RESEARCH_GROUPS = ("Data quality", "Research coverage", "Fact-check", "Consistency")
EVIDENCE_GROUPS = ("Trade checks", "Market & execution", "Evidence & base rates")


def _score(cs: list[dict]) -> int | None:
    w = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    return round(100 * sum(w[c["status"]] for c in cs) / len(cs)) if cs else None


def _grade(x: int | None) -> str:
    return "—" if x is None else "A" if x >= 85 else "B" if x >= 70 else "C" if x >= 55 else "D"


def report(checks: list[dict], filled: list[dict] | None = None, facts: dict | None = None) -> dict:
    """Two scores, because they answer different questions: how well was the research done (data, coverage, every
    number traced, consistency) and how strong is the evidence for this trade (trade checks, base rates)."""
    groups = []
    for gname in GROUPS:
        cs = [c for c in checks if c["group"] == gname]
        if cs:
            groups.append({"group": gname, "checks": cs, "passed": sum(c["status"] == "pass" for c in cs), "total": len(cs),
                           "kind": "research" if gname in RESEARCH_GROUPS else "evidence"})
    # each group weighs the same, so five base-rate checks are not drowned out by ten trade checks
    def by_group(names: tuple[str, ...]) -> int | None:
        gs = [x for x in (_score([c for c in checks if c["group"] == n]) for n in names) if x is not None]
        return round(sum(gs) / len(gs)) if gs else None

    score = by_group(RESEARCH_GROUPS)
    ev = by_group(EVIDENCE_GROUPS)
    crit = [c for c in checks if c["critical"]]
    fails = sum(c["status"] == "fail" for c in checks)
    warns = sum(c["status"] == "warn" for c in checks)
    summary = (f"Research quality {score if score is not None else '—'}/100 ({_grade(score)}), trade evidence "
               f"{ev if ev is not None else '—'}/100 ({_grade(ev)}): {len(checks) - fails - warns} checks passed, {warns} "
               f"warnings, {fails} failed" + (f"; critical: {', '.join(c['name'] for c in crit)}" if crit else
                                             "; no critical failure")
               + (f". The validator filled {len(filled)} research gap(s) itself." if filled else "."))
    return {"score": score if score is not None else 0, "grade": _grade(score), "evidence_score": ev if ev is not None else 0,
            "evidence_grade": _grade(ev), "summary": summary, "groups": groups, "checks": checks,
            "critical": [c["name"] for c in crit], "filled": filled or [], "fact_check": facts or {},
            "gate": "blocked" if crit else "open"}


def fact_checks(s: dict, cp: list[float], only: tuple[str, ...] | None = None) -> tuple[list[dict], dict]:
    """Fact-check every agent's text and, as one line per team, its specialists'. Returns the checks and the per-agent
    table."""
    out, table = [], {}
    for a in s["agents"]:
        if a["id"] == "validator" or not a.get("text") or (only and a["id"] not in only):
            continue
        fc = fact_check(a["text"], cp)
        table[a["id"]] = fc
        if fc["checked"]:
            share = fc["traced"] / fc["checked"]
            st = "pass" if share == 1 else "warn" if share >= 0.7 or len(fc["unverified"]) < 3 else "fail"
            out.append(check("Fact-check", f"{a['name']}: numbers traced to data", st,
                             f"{fc['traced']} of {fc['checked']} numbers traced"
                             + (f"; unverified: {', '.join(fc['unverified'])}" if fc["unverified"] else ""),
                             critical=a["id"] == "head" and st == "fail"))
        # never critical: the lead's own text is what the trader reads and the lead was told to verify its team
        subs = [fact_check(x["text"], cp) for x in a.get("subs", []) if x.get("text")]
        n, ok = sum(f["checked"] for f in subs), sum(f["traced"] for f in subs)
        if n:
            bad = [u for f in subs for u in f["unverified"]]
            out.append(check("Fact-check", f"{a['name']}'s team: numbers traced to data",
                             "pass" if ok == n else "warn" if ok / n >= 0.7 or len(bad) < 3 else "fail",
                             f"{ok} of {n} numbers traced across {len(subs)} specialists"
                             + (f"; unverified: {', '.join(bad[:6])}" if bad else "")))
    return out, table
