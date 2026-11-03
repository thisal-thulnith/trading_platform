"""Guided, human-style analysis walkthrough.

Turns deterministic snapshots (current timeframe + higher timeframes) into an ordered list of steps that an
experienced discretionary analyst would follow: top-down context → regime → structure → key levels →
liquidity → supply/demand zones → imbalances → premium/discount → Elliott → momentum/volume confirmation →
derivatives → news → scenarios → trade plan → verdict.

Each step carries: a beginner explanation (what the concept is), an observation (what we see right now, with
numbers computed by code), a verdict (+1 bullish / -1 bearish / 0 neutral), the chart objects to show while the
step is active, and a link into the Learn page. A synthesis layer then weighs the step verdicts (higher
timeframe and structure first, indicators last) into a story, a primary/alternate scenario and a confidence
label. No numbers are invented: every value comes from the snapshots.
"""
from app.analysis.price_action import stable_id
from app.config import get_settings
from app.risk.sizing import RiskProfile, size_position
from app.strategies.exits import apply_exit_profile

# How much each step's verdict counts in the synthesis (price-action first, indicators as confirmation)
STEP_WEIGHTS = {
    "big_picture": 2.0, "structure": 1.6, "liquidity": 1.0, "zones": 1.0, "premium_discount": 0.8,
    "imbalance": 0.5, "levels": 0.6, "momentum": 0.6, "derivatives": 0.5, "elliott": 0.3, "news": 0.3,
}
# Thresholds of `synthesize`; sent to the Analysis Board, which re-runs the same arithmetic client-side when the
# user drags concepts in/out or changes weights.
SYNTH_RULES = {"direction_threshold": 0.12, "high_agreement": 0.75, "medium_agreement": 0.6,
               "stress_regimes": ["liquidity_stress", "news_instability", "high_volatility"]}


def _p(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    d = 2 if a >= 1000 else 4 if a >= 1 else 6
    return f"{v:,.{d}f}"


def _pct(a, b) -> str:
    return f"{(a / b - 1) * 100:+.2f}%" if a and b else "—"


def _word(v: int) -> str:
    return {1: "bullish", -1: "bearish", 0: "neutral"}[v]


def _sec(ms):
    return None if ms is None else int(ms) // 1000


def _ann_ids(snap: dict, *concepts: str) -> list[str]:
    return [a["id"] for a in snap.get("annotations", []) if a.get("concept") in concepts]


def _step(key, title, plain, observation, verdict=0, focus=None, objects=None, learn=None, bullets=None,
          timeframe=None, caution=None):
    return {"key": key, "title": title, "plain": plain, "observation": observation, "verdict": verdict,
            "verdict_word": _word(verdict), "bullets": bullets or [], "focus_ids": focus or [], "objects": objects or [],
            "learn": learn or key, "timeframe": timeframe, "caution": caution}


# --------------------------------------------------------------------------------------------- steps

def step_big_picture(snap: dict, htf: dict[str, dict]) -> dict:
    bullets, score = [], 0
    for tf, h in htf.items():
        sm = h.get("smc") or {}
        trend = sm.get("trend_external", "undetermined")
        reg = h["regime"]["regime"].replace("_", " ")
        v = 1 if trend == "bullish" else -1 if trend == "bearish" else 0
        score += v
        e50 = h["indicators"].get("ema50")
        side = "above" if e50 and h["price"] > e50 else "below"
        bullets.append(f"{tf}: structure {trend}, regime {reg}, price {side} the 50-EMA ({_p(e50)})")
    if not htf:
        return _step("big_picture", "1. Big picture (higher timeframes)",
                     "Professionals start on a higher timeframe to know the dominant direction before looking for entries.",
                     "Higher-timeframe data not available.", 0)
    v = 1 if score > 0 else -1 if score < 0 else 0
    obs = {1: "Higher timeframes lean bullish — buying dips is favoured over selling rallies.",
           -1: "Higher timeframes lean bearish — selling rallies is favoured over buying dips.",
           0: "Higher timeframes disagree — no dominant direction, so expectations should be modest."}[v]
    return _step("big_picture", "1. Big picture (higher timeframes)",
                 "Start top-down. The higher timeframe is the 'tide'; lower-timeframe moves against it fail more often. "
                 "We read market structure and trend on the larger charts first.", obs, v, bullets=bullets, learn="timeframes")


def step_regime(snap: dict) -> dict:
    r = snap["regime"]
    name = r["regime"]
    plain = ("Markets alternate between trending, ranging and volatile phases. Each phase rewards different tactics: "
             "trend-following in trends, buying support/selling resistance in ranges, standing aside in chaos.")
    advice = {
        "strong_bull": "Strong uptrend: look for pullbacks to buy; avoid shorting strength.",
        "weak_bull": "Mild uptrend: trend is fragile — require good location before buying.",
        "strong_bear": "Strong downtrend: look for rallies to sell; avoid catching falling knives.",
        "weak_bear": "Mild downtrend: rallies may fail, but conviction is low.",
        "range": "Range: price is rotating between support and resistance — breakouts often fail.",
        "high_volatility": "High volatility: wide swings stop traders out easily — reduce size or wait.",
        "breakout": "Breakout: price just left its recent range — watch for follow-through or a failed breakout.",
        "liquidity_stress": "Thin liquidity: spreads/slippage are high — the platform abstains.",
        "news_instability": "Fresh high-impact news: moves can be erratic — the platform abstains.",
    }.get(name, "Regime undetermined.")
    return _step("regime", "2. What kind of market is this?", plain,
                 advice, 0,
                 bullets=r.get("reasons", []) + ([f"Also: {', '.join(r['secondary'])}"] if r.get("secondary") else []),
                 learn="regimes")


def step_structure(snap: dict) -> dict:
    sm = snap.get("smc") or {}
    swings = sm.get("swings", [])[-6:]
    objects = [{"id": stable_id("wt_sw", s["time"], s["kind"]), "kind": "label", "origin": "ai", "concept": "swing_label",
                "time1": _sec(s["time"]), "price1": s["price"], "label": s["label"],
                "color": "#22c55e" if s["label"] in ("HH", "HL") else "#ef4444" if s["label"] in ("LH", "LL") else "#94a3b8"}
               for s in swings]
    labels = [s["label"] for s in swings]
    trend = sm.get("trend_external", "undetermined")
    v = 1 if trend == "bullish" else -1 if trend == "bearish" else 0
    ext = [e for e in sm.get("structure", []) if e["scope"] == "external"]
    last = ext[-1] if ext else None
    bullets = [f"Recent swing sequence: {' → '.join(labels) or 'n/a'}"]
    if last:
        bullets.append(f"Last structural event: {('MSS' if last.get('mss') else last['type'])} "
                       f"{'up' if last['direction'] == 'bull' else 'down'} through {_p(last['level'])}"
                       + (" with a strong displacement candle" if last["displacement"] else ""))
    internal = sm.get("trend_internal")
    if internal and internal != trend:
        bullets.append(f"Short-term (internal) structure is {internal} — a pullback against the main structure")
    obs = {1: "Price is making higher highs and higher lows: the bullish structure is intact until the last higher low breaks.",
           -1: "Price is making lower highs and lower lows: the bearish structure is intact until the last lower high breaks.",
           0: "No clear sequence of higher or lower swings yet."}[v]
    return _step("structure", "3. Market structure (swings, BOS, CHoCH)",
                 "Trend is defined by swing points: higher highs + higher lows = uptrend, lower highs + lower lows = "
                 "downtrend. A Break of Structure (BOS) continues the trend; a Change of Character (CHoCH) is the first "
                 "sign it may be reversing.", obs, v, focus=_ann_ids(snap, "structure_break"), objects=objects,
                 bullets=bullets, learn="market-structure")


def step_levels(snap: dict) -> dict:
    pa = snap.get("price_action") or {}
    price = snap["price"]
    lv = pa.get("levels", [])
    sup = sorted([x for x in lv if x["price"] < price], key=lambda x: -x["price"])[:2]
    res = sorted([x for x in lv if x["price"] >= price], key=lambda x: x["price"])[:2]
    bullets = [f"Resistance {_p(x['price'])} ({_pct(x['price'], price)}, tested {x['touches']}×)" for x in res] + \
              [f"Support {_p(x['price'])} ({_pct(x['price'], price)}, tested {x['touches']}×)" for x in sup]
    tls = pa.get("trendlines", [])
    for t in tls:
        bullets.append(f"{t['kind'].replace('_', ' ')} ({t['status']}) now near {_p(t['value_now'])}")
    room_up = (res[0]["price"] / price - 1) if res else None
    room_dn = (1 - sup[0]["price"] / price) if sup else None
    v = 0
    if room_up is not None and room_dn is not None:
        v = 1 if room_dn < room_up * 0.5 else -1 if room_up < room_dn * 0.5 else 0
    obs = ("Price sits closer to support than resistance — risk is easier to define for longs." if v == 1 else
           "Price sits closer to resistance than support — risk is easier to define for shorts." if v == -1 else
           "Price is roughly mid-way between the nearest support and resistance.")
    return _step("levels", "4. Key support & resistance",
                 "Support and resistance are prices where the market reacted several times before. Traders place "
                 "orders around them, so they often cause reactions again — but every test also weakens them.",
                 obs, v, focus=_ann_ids(snap, "support_resistance", "rising_support", "falling_resistance"),
                 bullets=bullets, learn="support-resistance")


def step_liquidity(snap: dict) -> dict:
    sm = snap.get("smc") or {}
    price = snap["price"]
    resting = [x for x in sm.get("liquidity", []) if x["status"] == "resting"]
    above = sorted([x for x in resting if x["price"] > price], key=lambda x: x["price"])[:2]
    below = sorted([x for x in resting if x["price"] <= price], key=lambda x: -x["price"])[:2]
    sweeps = sm.get("sweeps", [])[-3:]
    bullets = [f"Buy-side liquidity (stops of shorts / breakout buyers) above at {_p(x['price'])} ({x['source']}, {_pct(x['price'], price)})"
               for x in above]
    bullets += [f"Sell-side liquidity (stops of longs) below at {_p(x['price'])} ({x['source']}, {_pct(x['price'], price)})"
                for x in below]
    v = 0
    if sweeps:
        s = sweeps[-1]
        verb = "swept and rejected" if s["kind"] == "sweep" else "broke through"
        bullets.append(f"Most recent: price {verb} {s['side']}-side liquidity at {_p(s['price'])} ({s['source']})"
                       + (" — stop-hunt pattern (sweep + strong reversal candle)" if s["stop_hunt_candidate"] else ""))
        v = 1 if s["bias"] == "bullish" else -1
    eq = [e for e in sm.get("equal_levels", []) if e["status"] == "resting"]
    for e in eq[-2:]:
        bullets.append(f"{'Equal highs' if e['type'] == 'equal_highs' else 'Equal lows'} at {_p(e['price'])} — an obvious stop cluster ('magnet')")
    obs = ("A sell-side sweep just happened: stops below were taken and price came back — a classic bullish trap for sellers."
           if v == 1 and sweeps and sweeps[-1]["kind"] == "sweep" else
           "A buy-side sweep just happened: stops above were taken and price came back — a classic bearish trap for buyers."
           if v == -1 and sweeps and sweeps[-1]["kind"] == "sweep" else
           "Liquidity was taken in the direction of the move (continuation)." if sweeps else
           "No recent sweep. The nearest liquidity pools are likely targets.")
    return _step("liquidity", "5. Liquidity — where are the stops?",
                 "Many traders put stop-losses just beyond obvious highs and lows. Those clusters of orders are "
                 "'liquidity'. Price is often drawn to them, and a quick spike through them followed by a reversal "
                 "(a 'sweep') can mark a turning point. Note: stop locations are inferred, not visible.",
                 obs, v, focus=_ann_ids(snap, "liquidity_sweep", "buy_side_liquidity", "sell_side_liquidity", "equal_highs",
                                        "equal_lows"), bullets=bullets, learn="liquidity",
                 caution="Resting stops are inferred from chart geometry; exchanges do not publish them.")


def step_zones(snap: dict) -> dict:
    sm = snap.get("smc") or {}
    price = snap["price"]
    a = snap["indicators"].get("atr14") or price * 0.01
    zones = [o for o in sm.get("order_blocks", []) + sm.get("breakers", []) if o["status"] != "invalidated"]
    near = sorted(zones, key=lambda o: min(abs(price - o["top"]), abs(price - o["bottom"])))[:4]
    bullets, v = [], 0
    for o in near:
        kind = {"order_block": "order block", "breaker": "breaker block", "mitigation_block": "mitigation block"}[o["type"]]
        side = "demand (buy zone)" if o["direction"] == "bullish" else "supply (sell zone)"
        dist = min(abs(price - o["top"]), abs(price - o["bottom"])) / a
        inside = o["bottom"] <= price <= o["top"]
        bullets.append(f"{o['direction'].title()} {kind} = {side} at {_p(o['bottom'])}–{_p(o['top'])} "
                       f"({'price is inside it' if inside else f'{dist:.1f} ATR away'}; {o['status']})")
    if near:
        o = near[0]
        if min(abs(price - o["top"]), abs(price - o["bottom"])) <= a or o["bottom"] <= price <= o["top"]:
            v = 1 if o["direction"] == "bullish" else -1
    obs = ("Price is at a demand zone — a place where buyers previously overwhelmed sellers." if v == 1 else
           "Price is at a supply zone — a place where sellers previously overwhelmed buyers." if v == -1 else
           "Price is not at a significant zone right now; zones mark where to look for reactions, not certainties.")
    return _step("zones", "6. Supply & demand zones (order blocks)",
                 "An order block is the last opposite-coloured candle before a strong move that broke structure. The idea: "
                 "big orders were filled there, so price may react when it returns. A failed order block flips into a "
                 "'breaker' with the opposite role.", obs, v,
                 focus=_ann_ids(snap, "order_block", "breaker", "mitigation_block"), bullets=bullets, learn="order-blocks",
                 caution="In this platform's backtests, order-block retest entries alone lost money — use zones as context.")


def step_imbalance(snap: dict) -> dict:
    sm = snap.get("smc") or {}
    price = snap["price"]
    gaps = [g for g in sm.get("fvgs", []) if g["status"] in ("open", "partially_filled", "active")]
    bullets = [f"{'Bullish' if g['direction'] == 'bullish' else 'Bearish'} {'FVG' if g['type'] == 'fvg' else 'inverse FVG'} "
               f"{_p(g['bottom'])}–{_p(g['top'])}, {g['fill_pct']:.0%} filled ({_pct((g['top'] + g['bottom']) / 2, price)})"
               for g in gaps[-4:]]
    above = [g for g in gaps if g["bottom"] > price]
    below = [g for g in gaps if g["top"] < price]
    v = 0
    obs = "Unfilled gaps act like magnets: price often comes back to fill them before continuing."
    if above and not below:
        v, obs = 1, "Open gaps are above price — they may pull price up to fill them."
    elif below and not above:
        v, obs = -1, "Open gaps are below price — they may pull price down to fill them."
    if not gaps:
        obs = "No open fair value gaps near price."
    return _step("imbalance", "7. Imbalances (fair value gaps)",
                 "A fair value gap is a 3-candle pattern where the middle candle moved so fast that the wicks of candles 1 "
                 "and 3 don't overlap — the market skipped prices. Those areas are often revisited.", obs, v,
                 focus=_ann_ids(snap, "fvg", "ifvg", "liquidity_void"), bullets=bullets, learn="fair-value-gaps")


def step_premium_discount(snap: dict) -> dict:
    pd = (snap.get("smc") or {}).get("premium_discount")
    if not pd:
        return _step("premium_discount", "8. Cheap or expensive? (premium / discount)",
                     "Within a swing range, the upper half is 'premium' (expensive) and the lower half 'discount' (cheap).",
                     "No clear dealing range.", 0, learn="premium-discount")
    v = 1 if pd["zone"] == "discount" else -1
    o = pd["ote"]
    return _step("premium_discount", "8. Cheap or expensive? (premium / discount)",
                 "Take the latest big swing (low to high). Above its midpoint (equilibrium) price is 'premium' — better for "
                 "selling; below it is 'discount' — better for buying. The 62–79% retracement is called the Optimal Trade "
                 "Entry (OTE) zone.",
                 f"Price is at {pd['position_pct']:.0f}% of the range {_p(pd['range_low'])}–{_p(pd['range_high'])}: "
                 f"{'discount (relatively cheap)' if v == 1 else 'premium (relatively expensive)'}.", v,
                 focus=[pd["id"] + "p", pd["id"] + "d", pd["id"] + "o"],
                 bullets=[f"Equilibrium (50%): {_p(pd['equilibrium'])}",
                          f"OTE zone ({o['direction']}): {_p(o['bottom'])}–{_p(o['top'])}"], learn="premium-discount")


def step_elliott(snap: dict) -> dict:
    cs = (snap.get("elliott") or {}).get("counts", [])
    if not cs:
        return _step("elliott", "9. Elliott Wave view", "Elliott Wave describes trends as 5-wave impulses and 3-wave corrections.",
                     "No count satisfies the hard rules at the current scales.", 0, learn="elliott-wave")
    p = cs[0]
    v = 0
    cw = str(p.get("current_wave", ""))
    if p["pattern"] in ("impulse", "leading_diagonal") and cw in ("4", "5"):
        v = 1 if p["direction"] == "bullish" else -1
    elif "correction" in cw:
        v = -1 if p["direction"] == "bullish" else 1
    bullets = [f"{c['role'].title()}: {c['direction']} {c['pattern'].replace('_', ' ')}, wave {c.get('current_wave') or 'complete'}, "
               f"invalid beyond {_p(c.get('invalidation'))} (fit {c['score']})" for c in cs[:3]]
    return _step("elliott", "9. Elliott Wave view",
                 "Elliott Wave theory says trends move in 5 waves (1-2-3-4-5) and correct in 3 (A-B-C), with strict rules "
                 "(e.g. wave 3 is never the shortest). Counts are subjective, so we show alternatives and the price that "
                 "would prove each count wrong.", p["explanation"], v, focus=[c["id"] for c in cs[:2]], bullets=bullets,
                 learn="elliott-wave", caution="Low weight: Elliott counts are subjective and unvalidated.")


def step_momentum(snap: dict) -> dict:
    ind = snap["indicators"]
    rsi, macd, rvol, cmf, ti = (ind.get(k) for k in ("rsi14", "macd_hist", "rvol20", "cmf20", "taker_imbalance14"))
    bullets, score = [], 0
    if rsi is not None:
        state = "overbought (stretched up)" if rsi > 70 else "oversold (stretched down)" if rsi < 30 else \
            "bullish momentum" if rsi > 55 else "bearish momentum" if rsi < 45 else "neutral"
        bullets.append(f"RSI {rsi:.0f}: {state}")
        score += 1 if 55 < rsi <= 75 else -1 if 25 <= rsi < 45 else 0
    if macd is not None:
        bullets.append(f"MACD histogram {'positive' if macd > 0 else 'negative'} — momentum {'rising' if macd > 0 else 'falling'}")
        score += 1 if macd > 0 else -1
    if rvol is not None:
        bullets.append(f"Volume is {rvol:.1f}× its 20-bar average ({'active' if rvol > 1.3 else 'quiet' if rvol < 0.7 else 'normal'})")
    if cmf is not None:
        bullets.append(f"Money flow (CMF) {cmf:+.2f}: {'buying pressure' if cmf > 0.05 else 'selling pressure' if cmf < -0.05 else 'balanced'}")
        score += 1 if cmf > 0.05 else -1 if cmf < -0.05 else 0
    if ti is not None:
        bullets.append(f"Aggressive (taker) buyers vs sellers: {ti:+.2f} {'buyers in control' if ti > 0.03 else 'sellers in control' if ti < -0.03 else ''}")
    v = 1 if score >= 2 else -1 if score <= -2 else 0
    obs = {1: "Momentum and volume confirm buyers are active.", -1: "Momentum and volume confirm sellers are active.",
           0: "Momentum indicators are mixed — no confirmation either way."}[v]
    return _step("momentum", "10. Momentum & volume (confirmation only)",
                 "Indicators like RSI and MACD are calculated from past prices, so they confirm what price already did — "
                 "they don't predict. Use them to check whether a move has energy behind it. Many indicators measure the "
                 "same thing, so three agreeing oscillators are one opinion, not three.", obs, v, bullets=bullets,
                 learn="indicators")


def step_derivatives(snap: dict) -> dict:
    d = snap.get("derivatives") or {}
    if not d.get("available"):
        return _step("derivatives", "11. Futures positioning", "Perpetual-futures data shows how leveraged traders are positioned.",
                     "No perpetual futures data for this market.", 0, learn="derivatives")
    fr = d.get("funding_rate") or 0
    bullets = [f"Funding {fr * 100:.4f}% per 8h ({'longs pay shorts — longs crowded' if fr > 0.0001 else 'shorts pay longs — shorts crowded' if fr < 0 else 'normal'})"]
    if d.get("oi_change_4h_pct") is not None:
        bullets.append(f"Open interest {d['oi_change_4h_pct']:+.2f}% over 4h ({'new positions opening' if d['oi_change_4h_pct'] > 0 else 'positions closing'})")
    if d.get("oi_price_regime"):
        bullets.append(f"Interpretation: {d['oi_price_regime']['label']}")
    if d.get("long_short_ratio"):
        bullets.append(f"Long/short account ratio {d['long_short_ratio']:.2f}")
    for f in d.get("squeeze_flags", []):
        bullets.append(f"⚠ {f.replace('_', ' ')}")
    v = 0
    if d.get("funding_extreme"):
        v = -1 if fr > 0 else 1
        obs = "Positioning is crowded on one side — crowded trades are vulnerable to sharp squeezes the other way."
    elif d.get("squeeze_flags"):
        v = 1 if d["squeeze_flags"][0].startswith("short") else -1
        obs = "Squeeze conditions are present."
    else:
        obs = "Futures positioning is not extreme — no strong signal from derivatives."
    return _step("derivatives", "11. Futures positioning (funding, open interest)",
                 "Perpetual futures pay 'funding' between longs and shorts to stay near spot price. High positive funding "
                 "means many leveraged longs; rising open interest means new money is entering. Extremes often precede "
                 "squeezes.", obs, v, bullets=bullets, learn="derivatives")


def step_news(snap: dict) -> dict:
    news = snap.get("news") or []
    fg = snap.get("fear_greed")
    hi = [n for n in news if n.get("impact") == "high"]
    s = sum(n.get("sentiment", 0) for n in news[:10])
    v = 1 if s > 1 else -1 if s < -1 else 0
    bullets = [f"[{n['impact']}] {n['title']} ({n['source']})" for n in news[:4]]
    if fg:
        bullets.insert(0, f"Crypto Fear & Greed index: {fg['value']} ({fg['label']})")
    return _step("news", "12. News & sentiment",
                 "News explains volatility but rarely gives a clean edge — by the time a headline is out, price has often "
                 "moved. Extreme fear or greed can mark emotional extremes.",
                 f"{len(news)} relevant headlines in 24h, {len(hi)} high-impact. "
                 + ("Headline tone is mostly negative." if v < 0 else "Headline tone is mostly positive." if v > 0 else "Headline tone is mixed."),
                 v, bullets=bullets, learn="news", caution="Headlines never trigger trades on this platform.")


# --------------------------------------------------------------------------------------------- synthesis

def synthesize(steps: list[dict], snap: dict, htf: dict[str, dict], weights: dict[str, float] | None = None) -> dict:
    w = weights or STEP_WEIGHTS
    r = SYNTH_RULES
    wsum = sum(w.get(s["key"], 0) for s in steps if s["verdict"] != 0)
    score = sum(w.get(s["key"], 0) * s["verdict"] for s in steps)
    total_w = sum(w.get(s["key"], 0) for s in steps)
    bias = score / total_w if total_w else 0
    agree = [s for s in steps if s["verdict"] != 0 and (s["verdict"] > 0) == (bias > 0)]
    disagree = [s for s in steps if s["verdict"] != 0 and (s["verdict"] > 0) != (bias > 0)]
    agreement = (sum(w.get(s["key"], 0) for s in agree) / wsum) if wsum else 0
    th = r["direction_threshold"]
    direction = "bullish" if bias > th else "bearish" if bias < -th else "neutral"
    big = next((s for s in steps if s["key"] == "big_picture"), None)
    struct = next((s for s in steps if s["key"] == "structure"), None)
    if direction == "neutral":
        confidence = "low"
    elif (agreement >= r["high_agreement"] and big and struct
          and big["verdict"] == struct["verdict"] == (1 if bias > 0 else -1)):
        confidence = "high"
    elif agreement >= r["medium_agreement"]:
        confidence = "medium"
    else:
        confidence = "low"
    regime = snap["regime"]["regime"]
    if regime in r["stress_regimes"] and confidence == "high":
        confidence = "medium"
    story = []
    if big:
        story.append(big["observation"])
    if struct:
        story.append(struct["observation"])
    for k in ("liquidity", "zones", "premium_discount"):
        s = next((x for x in steps if x["key"] == k), None)
        if s and s["verdict"] != 0:
            story.append(s["observation"])
    mom = next((x for x in steps if x["key"] == "momentum"), None)
    if mom:
        if direction != "neutral" and mom["verdict"] == (-1 if bias > 0 else 1):
            story.append("However, momentum is pointing the other way, so timing is uncertain — waiting for confirmation is wiser.")
        elif mom["verdict"] != 0:
            story.append("Momentum agrees with this read.")
    return {"bias": round(bias, 3), "direction": direction, "confidence": confidence, "agreement": round(agreement, 2),
            "supporting": [s["title"] for s in agree], "conflicting": [s["title"] for s in disagree], "story": story}


def step_scenarios(snap: dict, syn: dict) -> dict:
    price = snap["price"]
    sm = snap.get("smc") or {}
    swings = sm.get("swings", [])
    highs = sorted([s["price"] for s in swings if s["kind"] == "high" and s["price"] > price])
    lows = sorted([s["price"] for s in swings if s["kind"] == "low" and s["price"] < price], reverse=True)
    resting = [x for x in sm.get("liquidity", []) if x["status"] == "resting"]
    liq_up = sorted([x["price"] for x in resting if x["price"] > price])
    liq_dn = sorted([x["price"] for x in resting if x["price"] < price], reverse=True)
    up_trig = highs[0] if highs else (liq_up[0] if liq_up else None)
    dn_trig = lows[0] if lows else (liq_dn[0] if liq_dn else None)
    up_tgt = next((p for p in liq_up + highs if up_trig and p > up_trig * 1.001), None)
    dn_tgt = next((p for p in liq_dn + lows if dn_trig and p < dn_trig * 0.999), None)
    objs, bullets = [], []
    bull = {"name": "Bullish scenario", "trigger": up_trig, "target": up_tgt, "invalidation": dn_trig}
    bear = {"name": "Bearish scenario", "trigger": dn_trig, "target": dn_tgt, "invalidation": up_trig}
    if up_trig:
        bullets.append(f"🟢 Bullish: a candle close above {_p(up_trig)} ({_pct(up_trig, price)}) breaks structure up"
                       + (f" → next target {_p(up_tgt)}" if up_tgt else "") + (f"; fails below {_p(dn_trig)}" if dn_trig else ""))
        objs.append({"id": "wt_up_trig", "kind": "hline", "origin": "ai", "concept": "scenario", "time1": None, "price1": up_trig,
                     "color": "#22c55e", "label": f"Bull trigger {_p(up_trig)}", "style": "solid"})
        if up_tgt:
            objs.append({"id": "wt_up_tgt", "kind": "hline", "origin": "ai", "concept": "scenario", "time1": None, "price1": up_tgt,
                         "color": "#22c55e", "label": f"Bull target {_p(up_tgt)}", "style": "dashed"})
    if dn_trig:
        bullets.append(f"🔴 Bearish: a candle close below {_p(dn_trig)} ({_pct(dn_trig, price)}) breaks structure down"
                       + (f" → next target {_p(dn_tgt)}" if dn_tgt else "") + (f"; fails above {_p(up_trig)}" if up_trig else ""))
        objs.append({"id": "wt_dn_trig", "kind": "hline", "origin": "ai", "concept": "scenario", "time1": None, "price1": dn_trig,
                     "color": "#ef4444", "label": f"Bear trigger {_p(dn_trig)}", "style": "solid"})
        if dn_tgt:
            objs.append({"id": "wt_dn_tgt", "kind": "hline", "origin": "ai", "concept": "scenario", "time1": None, "price1": dn_tgt,
                         "color": "#ef4444", "label": f"Bear target {_p(dn_tgt)}", "style": "dashed"})
    primary = "bullish" if syn["direction"] == "bullish" else "bearish" if syn["direction"] == "bearish" else None
    obs = (f"Primary scenario: {primary} (confidence {syn['confidence']}). The other scenario is the alternate — "
           "if its trigger prints instead, the view is wrong and must change." if primary else
           "Evidence is balanced: let price choose. Whichever trigger breaks first defines the next move; until then, no position.")
    return {**_step("scenarios", "13. Scenarios — what would make me bullish or bearish?",
                    "Good analysts don't predict one outcome; they plan for both. For each side we define a trigger (what must "
                    "happen), a target (where it could go) and an invalidation (what proves it wrong).", obs,
                    1 if primary == "bullish" else -1 if primary == "bearish" else 0, objects=objs, bullets=bullets,
                    learn="scenarios"), "scenarios": [bull, bear]}


def step_plan(snap: dict, syn: dict) -> dict:
    profile = get_settings().exit_profiles.get(snap["timeframe"])
    cands = [apply_exit_profile(c, profile) for c in snap.get("candidates", []) if "error" not in c]
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    passed = [c for c in cands if c["stage"] == "confirmed" and c["validation"]["passed"]]
    pool = passed or sorted(cands, key=lambda c: (-stage_w.get(c["stage"], 0), -c["validation"]["quality_score"]))
    if not pool:
        return _step("plan", "14. Trade plan & risk",
                     "A trade needs an entry, a stop-loss (where the idea is wrong) and a target, sized so a loss costs a small, "
                     "fixed % of the account.", "No strategy setup exists right now → the plan is to wait. Not trading is a position.",
                     0, learn="risk-management")
    c = pool[0]
    entry = (c["entry_low"] + c["entry_high"]) / 2
    sz = size_position(RiskProfile(equity=1000, risk_per_trade_pct=1.0, max_leverage=3), snap["symbol"], c["direction"], entry, c["stop"])
    v = c["validation"]
    status = ("CONFIRMED — all conditions and safety checks passed" if c in passed else
              f"{c['stage'].replace('_', '-').upper()} — waiting for: {', '.join(c['missing']) or 'validation'}"
              if c["stage"] != "confirmed" else
              "BLOCKED by safety checks: " + "; ".join(ch["name"] for ch in v["checks"] if not ch["passed"]))
    bullets = [f"Setup: {c['strategy'].replace('_', ' ')} {c['direction']} — {status}",
               f"Entry zone {_p(c['entry_low'])}–{_p(c['entry_high'])}, stop {_p(c['stop'])}, targets "
               + ", ".join(_p(t) for t in c["targets"]),
               f"Reward/risk to first target after costs: {v['rr_tp1']:.2f}"]
    if sz.get("ok"):
        bullets.append(f"Example sizing for $1,000 at 1% risk: {sz['qty']:.6g} units (notional ${sz['notional']:,.2f}); "
                       f"a stop-out loses ≈ ${sz['risk_amount']:.2f}")
    if v["counter_arguments"]:
        bullets.append("Why it could fail: " + "; ".join(v["counter_arguments"][:3]))
    obj = {"id": "wt_plan", "kind": "position", "origin": "ai", "concept": "plan", "time1": _sec(snap["as_of"]),
           "direction": c["direction"], "entry": entry, "stop": c["stop"], "target": c["targets"][0],
           "label": f"{c['direction']} plan ({c['stage']})"}
    obs = ("A validated setup exists — this is how it would be traded." if c in passed else
           "This is the best setup being watched. It is NOT a trade until it confirms and passes every safety check.")
    return _step("plan", "14. Trade plan & risk", "Every trade needs an entry, a stop-loss placed where the idea is "
                 "proven wrong, and targets. Position size comes from the stop distance so that a loss costs a fixed small "
                 "percentage (here 1%) of the account — never 'all in'.", obs, 1 if c["direction"] == "LONG" else -1,
                 focus=[c["anchor_id"]] if c["anchor_id"] in {a["id"] for a in snap.get("annotations", [])} else [],
                 objects=[obj], bullets=bullets, learn="risk-management")


def step_verdict(snap: dict, syn: dict) -> dict:
    d = snap.get("decision") or {}
    action = d.get("action", "NO_TRADE")
    lines = [f"Overall read: {syn['direction']} with {syn['confidence']} confidence "
             f"({syn['agreement']:.0%} of weighted evidence agrees)."]
    if syn["conflicting"]:
        lines.append("Conflicting: " + ", ".join(t.split('. ', 1)[-1] for t in syn["conflicting"]))
    obs = (f"Decision: {action.replace('_', ' ')}. " + ("; ".join(d.get("reasons", [])[:3])))
    return _step("verdict", "15. Verdict", "The final decision combines everything, weighting the higher timeframe and "
                 "structure most and indicators least. When evidence is mixed, the professional answer is often 'no trade'.",
                 obs, 0 if action == "NO_TRADE" else 1 if action == "LONG" else -1, bullets=lines + syn["story"],
                 learn="decision-making")


def build_walkthrough(snap: dict, htf: dict[str, dict]) -> dict:
    steps = [step_big_picture(snap, htf), step_regime(snap), step_structure(snap), step_levels(snap),
             step_liquidity(snap), step_zones(snap), step_imbalance(snap), step_premium_discount(snap),
             step_elliott(snap), step_momentum(snap), step_derivatives(snap), step_news(snap)]
    syn = synthesize(steps, snap, htf)
    steps += [step_scenarios(snap, syn), step_plan(snap, syn), step_verdict(snap, syn)]
    for s in steps:
        s["timeframe"] = s["timeframe"] or snap["timeframe"]
    return {"symbol": snap["symbol"], "timeframe": snap["timeframe"], "price": snap["price"], "as_of": snap["as_of"],
            "higher_timeframes": list(htf), "synthesis": syn, "steps": steps, "decision": snap.get("decision"),
            "weights": STEP_WEIGHTS, "rules": SYNTH_RULES, "regime": snap["regime"]["regime"],
            "disclaimer": "Educational decision support generated from deterministic analysis. Not financial advice."}
