"""Live, LLM-driven agent team.

A lead agent (the Chief) reasons like a senior trader: it reads Scout's scan of every coin and the time of day,
chooses the coin, then — based on that market's condition — decides WHICH specialists to consult and what to ask
each. Every specialist is an LLM with its own method knowledge (SMC, ICT, Wyckoff, Elliott/NeoWave, Gann, Market
Profile, Harmonics, Hurst, Quasimodo, Bill Williams, Dow, momentum, derivatives). Specialists answer in their own
words, address each other, choose what to draw and which indicators to add. The Chief then runs a debate on the
biggest disagreement, the Skeptic attacks the leading view, Guard sizes the risk, and the Chief makes the call.

What keeps it honest:
- Agents reason over tools' measured data (the analysis engine); prompts forbid numbers that are not in the data.
- Drawings must reference real chart objects by id and indicators come from an allow-list; anything else is dropped.
- A trade is only possible on a setup that is confirmed and passed every validation check; the LLM can choose
  "no trade" freely, but can never invent an entry, stop or target. Guard's sizing is arithmetic, not prose.

Sessions run in the background and append timeline events (same format as app/agents/room.py, plus "think")
that the frontend plays while the agents are still working. Without an LLM key the rule-based room is used.
"""
import asyncio
import json
import re
import time
import uuid
from datetime import UTC, datetime

from app.agents.llm import complete, provider
from app.agents.room import AGENTS, BY_ID, Script, build_room, record_line, track_record
from app.analysis.schools import session_now
from app.config import get_settings
from app.logging import log
from app.risk.sizing import RiskProfile, size_position
from app.strategies.exits import apply_exit_profile

KNOWLEDGE = {
    "atlas": "Top-down analysis. Higher-timeframe trend and regime decide which side you trade; you only take lower-"
             "timeframe entries in the direction of the higher timeframe unless it is clearly breaking. EMA 50/200, "
             "ADX, volatility regime. You know trend days vs range days need different playbooks.",
    "sage": "Smart Money Concepts market structure: swing highs/lows, BOS (continuation break), CHoCH/MSS (first break "
            "against the trend), internal vs external structure, strong vs weak highs/lows, support/resistance "
            "flips. A trend is intact until its protected low/high breaks.",
    "hunter": "Liquidity: equal highs/lows, buy-side and sell-side liquidity pools, stop hunts, sweeps and grabs, "
              "inducement, premium vs discount of the dealing range (50% equilibrium), OTE 62–79%. Price is drawn to "
              "liquidity; a sweep followed by displacement the other way is a classic reversal trigger.",
    "mason": "Order blocks (last opposite candle before displacement), breakers, mitigation blocks, fair value gaps / "
             "imbalances, inverse FVGs, consequent encroachment (50% of a gap). Fresh, unmitigated zones in the "
             "direction of structure are the best; mitigated zones are weak.",
    "wave": "Elliott Wave: 5-wave impulses (wave 3 never shortest, wave 4 not overlapping wave 1), ABC/zigzag/flat/"
            "triangle corrections, Fibonacci relationships (wave 3 = 1.618×wave 1, wave 2 retraces 50–61.8%). Counts "
            "are probabilistic; always state the invalidation.",
    "pulse": "Momentum and volume: RSI regimes (40–80 bull, 20–60 bear), divergences, MACD crosses and histogram, "
             "relative volume, volume confirming breakouts, exhaustion. Momentum is timing, not direction.",
    "flow": "Derivatives and news: funding rate (crowded longs/shorts), open interest change with price (new money vs "
            "short covering), liquidations, basis, long/short ratios, and how headline risk can override technicals.",
    "composite": "Wyckoff: the Composite Man; accumulation phases A–E (PS, SC, AR, ST, Spring, Test, SOS, LPS), "
                 "distribution (PSY, BC, AR, ST, UTAD, SOW, LPSY), markup/markdown, effort vs result (volume vs "
                 "spread), cause and effect.",
    "dow": "Dow Theory: primary, secondary and minor trends; HH/HL and LH/LL; a trend persists until a definitive "
           "reversal; volume must confirm; accumulation, public participation and distribution phases.",
    "angle": "Gann: the 1×1, 2×1 and 1×2 angles from major pivots, price/time squaring, Square of 9, time counts "
             "(45, 90, 144, 180 bars), anniversaries. Angles depend on chart scale, so treat them as a guide.",
    "auction": "Auction Market Theory / Market Profile: POC, value area (70%), VAH/VAL, balance vs imbalance, "
               "acceptance vs rejection, the 80% rule (re-entering value tends to traverse it), single prints, "
               "poor highs/lows, naked POCs as magnets.",
    "fib": "Harmonic patterns: Gartley (B 0.618, D 0.786), Bat (B 0.382–0.5, D 0.886), Butterfly (B 0.786, "
           "D 1.27–1.618), Crab (D 1.618), Cypher, Shark; PRZ confluence; strict ratios — no ratio, no pattern.",
    "session": "ICT time concepts: Power of 3 / AMD (accumulation, manipulation, distribution), Asia range, London "
               "and New York killzones, Judas swing, midnight/daily open, silver bullet windows, weekly profiles. "
               "Time of day decides whether a move is likely manipulation or the real expansion.",
    "neo": "NeoWave (Glenn Neely): monowaves, rule of similarity and balance, retracement rules, objective impulse "
           "vs corrective classification, complex corrections, diametric and neutral triangles, post-pattern "
           "confirmation. Stricter than classic Elliott.",
    "cycle": "Hurst cycles: nested cycles (principle of harmonicity), troughs, cycle phasing, translation (right/left "
             "translated crests show trend), FLD (future line of demarcation), timing windows for the next low.",
    "qm": "Quasimodo / over-and-under: left shoulder, head sweeps it, break of the opposite swing (CHoCH), entry on "
          "the return to the left-shoulder level, invalidation beyond the head; QM as the bridge between a sweep "
          "and a structure shift.",
    "chaos": "Bill Williams trading chaos: 5-bar fractals, the Alligator (jaw/teeth/lips — sleeping vs eating), "
             "Awesome Oscillator saucers/zero-line crosses, Accelerator, the 'zones', fractal breakouts beyond the "
             "Alligator's teeth.",
    "skeptic": "Risk of ruin, base rates, overfitting, confirmation bias, crowded trades, regime change, fake-outs. "
               "Your job is to find why the leading idea fails.",
}
SPECIALISTS = [a["id"] for a in AGENTS if a["id"] not in ("chief", "guard", "skeptic", "scout", "quant")]
INDICATORS = {"ema50": ("ema", {"length": 50, "source": "close"}), "ema200": ("ema", {"length": 200, "source": "close"}),
              "rsi": ("rsi", {"length": 14}), "macd": ("macd", {"fast": 12, "slow": 26, "signal": 9}),
              "vwap": ("vwap", {"anchor": "D", "bands": 0}), "ao": ("ao", {"fast": 5, "slow": 34}),
              "bbands": ("bbands", {"length": 20, "mult": 2}), "obv": ("obv", {}), "adx": ("adx", {"length": 14}),
              "supertrend": ("supertrend", {"length": 10, "mult": 3}),
              "ichimoku": ("ichimoku", {"tenkan": 9, "kijun": 26, "senkou": 52}), "volume_profile": ("vp", {})}

RULES = """Hard rules:
- Use ONLY numbers that appear in the DATA you are given. Never invent a price, level or percentage. The only \
number you create is a p_up forecast when asked: your honest probability, scored later against what happens.
- Think like an experienced discretionary trader: weigh evidence, say what matters most right now and why, admit \
uncertainty, and say what would prove you wrong.
- Speak naturally in first person, like a colleague in a trading room (2–4 short sentences). You may address \
others as @Name. No markdown headings, no lists.
- Reply with ONE JSON object only."""

SESSIONS: dict[str, dict] = {}
_BY_KEY: dict[tuple[str, str], str] = {}


def parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


async def ask(system: str, data: dict, task: str, max_tokens: int = 900) -> dict | None:
    out = await complete(system, f"DATA:\n{json.dumps(data, default=str)}\n\nTASK:\n{task}", max_tokens=max_tokens,
                         json_mode=True)
    return parse_json(out)


def _clip(s, n=600) -> str:
    return str(s or "").strip()[:n]


def _sign(word: str | None) -> int:
    return {"bullish": 1, "bearish": -1}.get(str(word or "").lower(), 0)


# ------------------------------------------------------------------------------------------- data for the agents

def agent_material(walk: dict, snap: dict, schools: list[dict], rec: dict, agent_id: str) -> dict:
    """The measured evidence one specialist may use, plus the chart objects it is allowed to draw."""
    steps = {s["key"]: s for s in walk["steps"]}
    ann = {a["id"]: a for a in snap.get("annotations", [])}
    by_school = {s["key"]: s for s in schools}
    a = BY_ID[agent_id]
    out, objects = [], {}
    for c in a["concepts"]:
        st = steps.get(c) or by_school.get(c)
        if not st:
            continue
        out.append({"topic": st["title"], "observation": st["observation"], "details": st["bullets"][:6],
                    "rule_based_verdict": st["verdict_word"],
                    **({"track_record": record_line(rec, c, walk["timeframe"])} if c in by_school else {})})
        for o in [ann[i] for i in st.get("focus_ids", []) if i in ann] + st["objects"]:
            if o.get("id") and len(objects) < 12:
                objects[o["id"]] = o
    return {"evidence": out, "drawable": objects}


def market_context(walk: dict, snap: dict, scan_row: dict | None) -> dict:
    steps = {s["key"]: s for s in walk["steps"]}
    reg = snap["regime"]
    ind = snap["indicators"]
    cands = [c for c in snap.get("candidates", []) if "error" not in c]
    return {"symbol": walk["symbol"], "timeframe": walk["timeframe"], "price": walk["price"],
            "session": session_now(), "regime": reg["regime"], "regime_reasons": reg["reasons"],
            "volatility_percentile": reg["features"].get("vol_percentile"), "adx": reg["features"].get("adx"),
            "atr_pct": (ind.get("atr14") or 0) / walk["price"] * 100 if walk["price"] else None,
            "higher_timeframes": steps.get("big_picture", {}).get("bullets", []),
            "setups_on_chart": [f"{c['strategy']} {c['direction']} ({c['stage']})" for c in cands][:5],
            "news_risk": bool(snap.get("decision", {}).get("reasons") and any(
                "news" in r for r in snap["decision"]["reasons"])),
            "scan": scan_row and {k: scan_row.get(k) for k in ("score", "reasons", "direction")}}


def roster() -> list[dict]:
    return [{"id": i, "name": BY_ID[i]["name"], "method": BY_ID[i]["role"]} for i in SPECIALISTS]


# ------------------------------------------------------------------------------------------- the session

class Live:
    def __init__(self, sid: str):
        self.sid = sid
        self.sc = Script()
        self.transcript: list[dict] = []
        self.s = SESSIONS[sid]

    def flush(self):
        self.s["events"] = self.sc.events
        self.s["duration_ms"] = self.sc.t

    def think(self, agent: str, text: str):
        self.sc.add(agent, "think", 300, text=text)
        self.flush()

    def say(self, agent: str, text: str, tone: str = "neutral", reply_to: str | None = None):
        self.sc.say(agent, text, tone, reply_to)
        self.transcript.append({"agent": BY_ID[agent]["name"], "said": text})
        self.flush()


def _tone(v: int) -> str:
    return {1: "bull", -1: "bear", 0: "neutral"}[v]


async def run_live(sid: str, symbol: str | None, tf: str) -> None:
    from app.agents import costs

    costs.FEATURE.set("live")
    from app.agents import forecast_hub
    from app.agents.scout import wait_scan
    from app.api.chat import _schools, _walk
    from app.quant import brain as quant
    from app.quant import ledger

    s = SESSIONS[sid]
    lv = Live(sid)
    try:
        # ------------------------------------------------ 1. the Chief chooses the coin
        lv.think("chief", "Reading Scout's scan of every coin…")
        scan, qd = await asyncio.gather(wait_scan(tf), quant.get())
        rows = scan.get("rows", [])
        qmap = {r["symbol"]: r for r in (qd or {}).get("ranks", [])}

        def qinfo(sym: str) -> dict | None:
            q = qmap.get(sym)
            return {"trend_rank": f"{q['rank']} of {q['of']}", "trend": q["trend"], "mom_28d_pct": q["mom_28d_pct"],
                    "crowding": q["funding_note"]} if q else None

        compact = [{"symbol": r["symbol"], "signal": r["signal"]["action"], "setup": r["reasons"][0],
                    "trend": r["direction"], "council": r["council"]["direction"], "methods_agree": r.get("agree"),
                    "regime": r["regime"], "score": r["score"], "quant": qinfo(r["symbol"])} for r in rows[:25]]
        lv.say("scout", f"I screened all {scan.get('all_coins') or len(rows)} USDT coins and fully analysed "
                        f"{len(rows)}. Here's my shortlist, Chief.", "neutral", "chief")
        for r in rows[:5]:
            lv.sc.click("scout", f"scan:{r['symbol']}", 500)
        if qd:
            pr = qd["proof"].get("long_short") or {}
            top = ", ".join(r["symbol"].replace("USDT", "") for r in qd["ranks"][:3])
            lv.say("quant", f"Market check: {qd['regime']['plain']} I ranked {qd['coins']} liquid coins by trend — "
                            f"strongest now: {top}. Honest note: over the last {qd['proof'].get('weeks')} weeks my trend "
                            f"ranking returned {pr.get('total_pct')}% long-short after costs (t = {pr.get('t_stat')}), "
                            "so I'm context, not a proven edge.", "neutral", "chief")
        lv.flush()
        if symbol is None:
            pick = await ask(f"You are Chief, head trader of a crypto trading desk.\n{RULES}",
                             {"session_time": session_now(), "timeframe": tf, "scan": compact,
                              "market_regime": (qd or {}).get("regime")},
                             'Choose the ONE coin most worth analysing right now and explain why like a trader '
                             '(time of day, setup quality, whether methods agree, regime). JSON: {"symbol": "...", '
                             '"say": "...", "also_considered": ["...", "..."]}')
            valid = {r["symbol"] for r in rows}
            symbol = (pick or {}).get("symbol") if (pick or {}).get("symbol") in valid else (rows[0]["symbol"] if rows else "BTCUSDT")
            for c in ((pick or {}).get("also_considered") or [])[:2]:
                if c in valid:
                    lv.sc.click("chief", f"scan:{c}", 600)
            lv.sc.click("chief", f"scan:{symbol}", 700)
            lv.say("chief", _clip((pick or {}).get("say")) or f"We'll work on {symbol}: it tops the scan.",
                   "neutral", "scout")
        s["symbol"] = symbol
        scan_row = next((r for r in rows if r["symbol"] == symbol), None)

        # ------------------------------------------------ 2. tools: measure the chart
        lv.think("chief", f"Pulling the data on {symbol} {tf}…")
        walk, snap = await _walk(symbol, tf)
        schools = await _schools(symbol, tf)
        rec = track_record(tf)
        s["price"] = walk["price"]
        ctx = market_context(walk, snap, scan_row)
        cands = [apply_exit_profile(c, get_settings().exit_profiles.get(tf)) for c in snap.get("candidates", [])
                 if "error" not in c]
        stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
        cands.sort(key=lambda c: (-stage_w.get(c["stage"], 0), -c["validation"]["quality_score"]))
        pre = await forecast_hub.build(symbol, tf, snap, [], cands[0] if cands else None)
        qc, od = pre["quant"], pre["odds"]
        try:
            base_up = (await ledger.scores())["base_rate_up"]
        except Exception:  # noqa: BLE001 - a missing ledger must not stop the analysis
            base_up = 0.5
        ctx["quant"] = {"trend_rank": f"{qc['rank']} of {qc['of']}", "trend": qc["trend"], "mom_7d_pct": qc["mom_7d_pct"],
                        "mom_28d_pct": qc["mom_28d_pct"], "funding": qc["derivs"], "crowding": qc["funding_note"]} if qc else None
        ctx["best_setup_past_odds"] = od
        ctx["market_regime"] = pre["market"]
        if qc or od:
            bits = []
            if qc:
                bits.append(f"{symbol} ranks {qc['rank']} of {qc['of']} on trend ({qc['trend']}; 7-day {qc['mom_7d_pct']}%, "
                            f"28-day {qc['mom_28d_pct']}%).")
                if qc["funding_note"]:
                    bits.append(qc["funding_note"] + ".")
            if od and od.get("n"):
                bits.append(f"Best setup here is {od['setup']}: in {od['of10']} of 10 similar past cases (n = {od['n']}) "
                            f"the target came before the stop, average {od['avg_r']}R.")
            lv.say("quant", " ".join(bits), "neutral", "chief")

        # ------------------------------------------------ 3. the Chief plans who to ask
        lv.think("chief", "Deciding which methods fit this market…")
        plan = await ask(f"You are Chief, head trader.\n{RULES}",
                         {"market": ctx, "specialists": roster(),
                          "measured_track_records_4h_or_tf": {k: v["oos"].get("hit_rate") for k, v in
                                                              (rec.get("schools") or {}).items()}},
                         "Plan the analysis like an experienced trader: given THIS market's regime, volatility, time "
                         "of day and setups, choose the 5–8 specialists whose methods fit best (e.g. trend markets → "
                         "structure/Dow/Elliott; ranges → Wyckoff/Market Profile/liquidity; around session opens → "
                         "ICT Power of 3). Give each a specific question. JSON: {\"say\": \"your plan to the team\", "
                         "\"ask\": [{\"id\": \"agent id\", \"question\": \"...\"}]}", 1200)
        asks = [x for x in (plan or {}).get("ask", []) if isinstance(x, dict) and x.get("id") in SPECIALISTS]
        if not asks:
            asks = [{"id": i, "question": "What do you see and what matters most?"} for i in
                    ("atlas", "sage", "hunter", "mason", "pulse", "session")]
        seen = set()
        asks = [x for x in asks if not (x["id"] in seen or seen.add(x["id"]))][:8]
        lv.say("chief", _clip((plan or {}).get("say")) or "Let's go through the key methods for this market.")

        # ------------------------------------------------ 4. specialists reason, draw and talk
        views: dict[str, dict] = {}
        now_s = int(snap["as_of"]) // 1000
        for q in asks:
            aid = q["id"]
            mat = agent_material(walk, snap, schools, rec, aid)
            lv.think(aid, "Looking at the chart…")
            ans = await ask(
                f"You are {BY_ID[aid]['name']}, {BY_ID[aid]['role']} on a crypto trading desk.\nYour expertise: "
                f"{KNOWLEDGE[aid]}\n{RULES}",
                {"market": ctx, "your_measured_evidence": mat["evidence"],
                 "chart_objects_you_can_draw": [{"id": k, "kind": o["kind"], "label": o.get("label")}
                                                for k, o in mat["drawable"].items()],
                 "indicators_you_can_add": list(INDICATORS), "conversation_so_far": lv.transcript[-12:],
                 "chief_asks_you": q.get("question"),
                 "base_rate_price_higher_after_12_candles_pct": round(base_up * 100)},
                'Analyse with your method like a human expert. Decide what to show on the chart (up to 3 object '
                'ids) and which indicators (0–2) you want to look at. If someone said something you disagree with, '
                'address them. Then give p_up: your probability (0-100) that price closes HIGHER 12 candles from now; it is '
                'scored against reality and sets your future weight, so be honest and stay near the base rate unless '
                'your evidence is strong. JSON: {"say": "...", "verdict": "bullish|bearish|neutral", "confidence": '
                '0-100, "p_up": 0-100, "draw": ["id"], "indicators": ["name"], "reply_to": "agent name or null"}')
            if not ans:
                continue
            for name in [i for i in ans.get("indicators") or [] if i in INDICATORS][:2]:
                ind, params = INDICATORS[name]
                if ind == "vp":
                    lv.sc.click(aid, "vp", 600)
                    lv.sc.add(aid, "vp", 200)
                else:
                    lv.sc.click(aid, "indicators", 600)
                    lv.sc.click(aid, f"ind:{ind}", 650)
                    lv.sc.add(aid, "indicator", 250, id=ind, params=params)
            for oid in [d for d in ans.get("draw") or [] if d in mat["drawable"]][:3]:
                lv.sc.draw(aid, mat["drawable"][oid], now_s, f"live_{aid}_{oid}")
            v = _sign(ans.get("verdict"))
            pu = ans.get("p_up")
            pu = min(max(float(pu) / 100, 0.01), 0.99) if isinstance(pu, (int, float)) else ledger.verdict_to_p(
                v, ans.get("confidence") if isinstance(ans.get("confidence"), (int, float)) else None)
            views[aid] = {"verdict": v, "confidence": ans.get("confidence"), "p_up": pu, "said": _clip(ans.get("say"))}
            reply = next((a["id"] for a in AGENTS if a["name"] == ans.get("reply_to")), None)
            lv.say(aid, _clip(ans.get("say")) or "Nothing decisive from my side.", _tone(v), reply)

        # ------------------------------------------------ 5. debate the biggest disagreement
        bulls = [k for k, v in views.items() if v["verdict"] > 0]
        bears = [k for k, v in views.items() if v["verdict"] < 0]
        if bulls and bears:
            lv.think("chief", "There's a disagreement — let's have it out…")
            d = await ask(f"You are Chief, head trader.\n{RULES}",
                          {"views": {BY_ID[k]["name"]: v for k, v in views.items()}, "conversation": lv.transcript},
                          'Name the most important disagreement and ask the two sides to answer each other. JSON: '
                          '{"say": "...", "bull_id": "one of ' + ",".join(bulls) + '", "bear_id": "one of '
                          + ",".join(bears) + '"}')
            b_id = (d or {}).get("bull_id") if (d or {}).get("bull_id") in bulls else bulls[0]
            r_id = (d or {}).get("bear_id") if (d or {}).get("bear_id") in bears else bears[0]
            lv.say("chief", _clip((d or {}).get("say")) or f"@{BY_ID[b_id]['name']} and @{BY_ID[r_id]['name']}, "
                   "you disagree — convince each other.", "warn")
            for me, other in ((r_id, b_id), (b_id, r_id)):
                lv.think(me, f"Answering {BY_ID[other]['name']}…")
                ans = await ask(f"You are {BY_ID[me]['name']}, {BY_ID[me]['role']}.\nYour expertise: {KNOWLEDGE[me]}"
                                f"\n{RULES}",
                                {"your_evidence": agent_material(walk, snap, schools, rec, me)["evidence"],
                                 "conversation": lv.transcript[-14:]},
                                f"Respond directly to @{BY_ID[other]['name']}'s argument. Concede what is right, defend "
                                'what you still believe, and update p_up if you changed your mind. JSON: {"say": "...", '
                                '"verdict": "bullish|bearish|neutral", "p_up": 0-100}')
                if ans:
                    views[me]["verdict"] = _sign(ans.get("verdict")) if ans.get("verdict") else views[me]["verdict"]
                    if isinstance(ans.get("p_up"), (int, float)):
                        views[me]["p_up"] = min(max(float(ans["p_up"]) / 100, 0.01), 0.99)
                    lv.say(me, _clip(ans.get("say")), _tone(views[me]["verdict"]), other)

        # ------------------------------------------------ 6. skeptic
        lv.think("skeptic", "Looking for the hole in this…")
        sk = await ask(f"You are Skeptic, the desk's devil's advocate.\nYour expertise: {KNOWLEDGE['skeptic']}\n{RULES}",
                       {"conversation": lv.transcript,
                        "counter_arguments_measured": [a for c in cands for a in c["validation"]["counter_arguments"]][:6],
                        "methods_track_record": "none of the classic schools beat chance on their own in our tests"},
                       'Attack the leading view. JSON: {"say": "..."}')
        sk_say = _clip((sk or {}).get("say")) or None
        if sk_say:
            lv.say("skeptic", sk_say, "warn")

        # ------------------------------------------------ 7. guard: arithmetic, not opinion
        menu = []
        for i, c in enumerate(cands[:4]):
            entry = (c["entry_low"] + c["entry_high"]) / 2
            ok = c["stage"] == "confirmed" and c["validation"]["passed"]
            menu.append({"index": i, "setup": f"{c['strategy']} {c['direction']}", "stage": c["stage"],
                         "tradeable": ok, "entry": entry, "stop": c["stop"], "target": c["targets"][0],
                         "blocked_by": [ch["name"] for ch in c["validation"]["checks"] if not ch["passed"]][:3]})
        if menu:
            m = menu[0]
            sz = size_position(RiskProfile(equity=100, risk_per_trade_pct=1.0, max_leverage=5), symbol,
                               cands[0]["direction"], m["entry"], m["stop"])
            lv.sc.draw("guard", {"id": "live_trade", "kind": "position", "concept": "plan", "time1": now_s,
                                 "direction": cands[0]["direction"], "entry": m["entry"], "stop": m["stop"],
                                 "target": m["target"], "label": m["setup"]}, now_s, "live_trade")
            lv.say("guard", f"Best setup: {m['setup']} ({m['stage']}). Entry {m['entry']:,.6g}, stop {m['stop']:,.6g}, "
                            f"target {m['target']:,.6g}."
                   + (f" On $100 risking 1%: position ≈ ${sz['notional']:,.2f}, loss if stopped ≈ ${sz['risk_amount']:.2f}."
                      if sz.get("ok") else "")
                   + ("" if m["tradeable"] else f" Not tradeable yet — blocked by: {', '.join(m['blocked_by']) or 'stage'}."),
                   "neutral" if m["tradeable"] else "warn")
        else:
            lv.say("guard", "No setup exists on this chart, so there's nothing I can size. Cash is a position.")

        # ------------------------------------------------ 7b. the forecast hub: maths, not the loudest voice
        hub = await forecast_hub.build(symbol, tf, snap, [{"source": k, "p_up": v["p_up"], "brain": "llm"}
                                                         for k, v in views.items()],
                                       cands[0] if cands else None, sk_say)
        lv.say("quant", forecast_hub.plain(hub), "neutral", "chief")

        # ------------------------------------------------ 8. the call
        lv.think("chief", "Weighing everything…")
        fin = await ask(f"You are Chief, head trader. You decide.\n{RULES}",
                        {"conversation": lv.transcript, "setups": menu,
                         "views": {BY_ID[k]["name"]: v["verdict"] for k, v in views.items()},
                         "team_p_up_12_candles": hub["forecast"]["p_up"], "best_setup_past_odds": hub["odds"]},
                        'Make the final call. You may only TRADE a setup whose "tradeable" is true; otherwise say NO '
                        'TRADE and what you are waiting for. Respect the numbers: if the setup direction disagrees '
                        'with the team probability, or its past odds are poor, prefer NO TRADE or half size. Explain '
                        'the decision like a mentor to a beginner. JSON: '
                        '{"decision": "TRADE|NO_TRADE", "index": 0, "size": "full|half", "say": "..."}', 1000)
        idx = (fin or {}).get("index")
        trade_ok = (fin or {}).get("decision") == "TRADE" and isinstance(idx, int) and 0 <= idx < len(menu) \
            and menu[idx]["tradeable"]
        say = _clip((fin or {}).get("say"), 900)
        if (fin or {}).get("decision") == "TRADE" and not trade_ok:
            say += " (Guard overrode this: the setup has not passed every safety check, so it's NO TRADE.)"
        if trade_ok:
            m, c = menu[idx], cands[idx]
            risk = abs(m["entry"] - m["stop"])
            word = "BUY" if c["direction"] == "LONG" else "SELL"
            s["signal"] = {"action": word, "strategy": c["strategy"].replace("_", " "), "direction": c["direction"],
                           "entry": m["entry"], "stop": m["stop"], "target": m["target"],
                           "rr": abs(m["target"] - m["entry"]) / risk if risk else 0,
                           "stop_pct": risk / m["entry"] * 100, "target_pct": abs(m["target"] - m["entry"]) / m["entry"] * 100,
                           "text": say + (" The team suggests half size because views were mixed."
                                          if (fin or {}).get("size") == "half" else "")}
        else:
            s["signal"] = {"action": "WAIT", "text": say or "The team passed: nothing is ready to trade."}
        s["signal"].update({k: hub[k] for k in ("forecast", "odds", "quant", "case_against", "market",
                                                 "horizon_candles", "quant_proof")})
        s["signal"]["forecast_text"] = forecast_hub.plain(hub)
        s["signal"]["booked"] = await forecast_hub.book(sid, symbol, tf, walk["price"], hub)
        lv.say("chief", say or "No trade.", "bull" if trade_ok and cands[idx]["direction"] == "LONG" else
               "bear" if trade_ok else "neutral")
        s["synthesis"] = walk["synthesis"]
        s["status"] = "done"
    except Exception as e:  # noqa: BLE001
        log.error("live_room_failed", error=f"{type(e).__name__}: {e}")
        s["status"] = "error"
        s["error"] = str(e)
    finally:
        lv.flush()
        s["finished"] = time.time()


async def _rules_hub(sid: str, symbol: str, tf: str, walk: dict, snap: dict, room: dict) -> dict:
    """Rule-based agents get the same forecast treatment: each school's verdict becomes a scored P(up)."""
    from app.agents import forecast_hub
    from app.agents.room import SCHOOL_AGENTS
    from app.quant import ledger

    agent_for = {a["concepts"][0]: a["id"] for a in SCHOOL_AGENTS}
    items = [{"source": agent_for.get(st["key"], st["key"]), "p_up": ledger.verdict_to_p(st["verdict"], 60),
              "brain": "rules"} for st in room.get("schools") or []]
    cands = [c for c in snap.get("candidates", []) if "error" not in c]
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    cands.sort(key=lambda c: (-stage_w.get(c["stage"], 0), -c["validation"]["quality_score"]))
    counter = [a for c in cands for a in c["validation"]["counter_arguments"]][:1]
    hub = await forecast_hub.build(symbol, tf, snap, items, cands[0] if cands else None,
                                   counter[0] if counter else None)
    out = {k: hub[k] for k in ("forecast", "odds", "quant", "case_against", "market", "horizon_candles", "quant_proof")}
    out["forecast_text"] = forecast_hub.plain(hub)
    out["booked"] = await forecast_hub.book(sid, symbol, tf, walk["price"], hub)
    return out


async def start_session(symbol: str | None, tf: str) -> dict:
    """LLM session if a key is configured, otherwise the rule-based room wrapped as a finished session."""
    key = (symbol or "AUTO", tf)
    old = SESSIONS.get(_BY_KEY.get(key, ""))
    if old and (old["status"] == "running" or time.time() - old.get("finished", 0) < 300):
        return old
    sid = uuid.uuid4().hex[:12]
    base = {"id": sid, "tf": tf, "symbol": symbol, "agents": AGENTS, "events": [], "duration_ms": 0,
            "started": datetime.now(UTC).isoformat(), "disclaimer":
            "Educational decision support generated from deterministic analysis and AI reasoning. Not financial advice."}
    if provider() is None:
        from app.agents.scout import plain_signal, wait_scan
        from app.api.chat import _schools, _sym, _walk

        scout = None
        if symbol is None:
            scout = await wait_scan(tf)
            symbol = scout["rows"][0]["symbol"] if scout.get("rows") else "BTCUSDT"
        walk, snap = await _walk(symbol, tf)
        room = build_room(walk, snap, await _schools(_sym(symbol), tf), scout)
        signal = plain_signal(snap, tf)
        try:
            signal.update(await _rules_hub(sid, room["symbol"], tf, walk, snap, room))
        except Exception as e:  # noqa: BLE001 - the room still works without the forecast extras
            log.warning("rules_hub_failed", error=str(e))
        SESSIONS[sid] = {**base, **room, "symbol": room["symbol"], "signal": signal, "status": "done",
                         "brain": "rules", "finished": time.time()}
    else:
        SESSIONS[sid] = {**base, "status": "running", "brain": provider(), "model": get_settings().openai_model
                         if provider() == "openai" else get_settings().llm_model}
        asyncio.get_running_loop().create_task(run_live(sid, symbol, tf))
    _BY_KEY[key] = sid
    for k in [k for k, v in SESSIONS.items() if time.time() - v.get("finished", time.time()) > 3600]:
        SESSIONS.pop(k, None)
    return SESSIONS[sid]


def session_view(sid: str, after: int = 0) -> dict | None:
    s = SESSIONS.get(sid)
    if not s:
        return None
    return {k: v for k, v in s.items() if k != "events"} | {"events": s["events"][after:], "n_events": len(s["events"]),
                                                            "after": after}

