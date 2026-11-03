"""Agent War Room: the analysis replayed as a team of specialist agents working one shared chart.

Each agent owns concepts, moves a visible cursor to the evidence it found, draws it, and talks — including replying
to and disagreeing with other agents. Nothing here is generated freely: every message is built from the deterministic
walkthrough steps, strategy candidates and validation checks, so the "conversation" can never invent a level.

Output is a timeline of events the frontend plays back:
  {"t": ms, "agent": id, "type": "move", "time": unix_s, "price": float}
  {"t": ms, "agent": id, "type": "draw", "object": ChartObject}
  {"t": ms, "agent": id, "type": "say", "text": str, "reply_to": agent_id | None, "tone": "bull"|"bear"|"neutral"|...}
  {"t": ms, "agent": id, "type": "focus", "ids": [annotation ids]}
  {"t": ms, "agent": id, "type": "click", "target": "tool:trendline" | "indicators" | "ind:rsi" | "vp" | "scan:BTCUSDT"}
  {"t": ms, "agent": id, "type": "indicator", "id": "rsi", "params": {...}}   (actually added to the chart)
  {"t": ms, "agent": id, "type": "vp"}                                        (volume profile switched on)
Like a person, an agent clicks the drawing tool in the toolbar, moves to the start point and drags the object out
(the frontend animates the drag), and opens the Indicators menu to add the indicators it needs.
"""
import json
from pathlib import Path

from app.analysis.schools import school_vote
from app.analysis.walkthrough import STEP_WEIGHTS
from app.config import get_settings
from app.risk.sizing import RiskProfile, size_position
from app.strategies.exits import apply_exit_profile

AGENTS = [
    {"id": "atlas", "name": "Atlas", "role": "Higher-timeframe strategist", "color": "#a78bfa",
     "concepts": ["big_picture", "regime"]},
    {"id": "sage", "name": "Sage", "role": "Market-structure analyst (BOS / CHoCH)", "color": "#38bdf8",
     "concepts": ["structure", "levels"]},
    {"id": "hunter", "name": "Hunter", "role": "Liquidity hunter (sweeps, premium/discount)", "color": "#f472b6",
     "concepts": ["liquidity", "premium_discount"]},
    {"id": "mason", "name": "Mason", "role": "Order blocks & fair value gaps", "color": "#fb923c",
     "concepts": ["zones", "imbalance"]},
    {"id": "wave", "name": "Wave", "role": "Elliott Wave counter", "color": "#2dd4bf", "concepts": ["elliott"]},
    {"id": "pulse", "name": "Pulse", "role": "Momentum & volume", "color": "#facc15", "concepts": ["momentum"]},
    {"id": "flow", "name": "Flow", "role": "Futures positioning & news", "color": "#4ade80",
     "concepts": ["derivatives", "news"]},
    # schools of thought (app/analysis/schools.py)
    {"id": "composite", "name": "Composite", "role": "Wyckoff — accumulation / distribution", "color": "#c084fc",
     "concepts": ["wyckoff"]},
    {"id": "dow", "name": "Dow", "role": "Dow Theory — primary vs secondary trend", "color": "#60a5fa",
     "concepts": ["dow"]},
    {"id": "angle", "name": "Angle", "role": "Gann angles & time counts", "color": "#fbbf24", "concepts": ["gann"]},
    {"id": "auction", "name": "Auction", "role": "Market Profile — POC & value area", "color": "#34d399",
     "concepts": ["profile"]},
    {"id": "fib", "name": "Fib", "role": "Harmonic patterns (Gartley, Bat, Butterfly, Crab)", "color": "#f472b6",
     "concepts": ["harmonic"]},
    {"id": "session", "name": "Session", "role": "ICT Power of 3 & killzones (time of day)", "color": "#22d3ee",
     "concepts": ["po3"]},
    {"id": "neo", "name": "Neo", "role": "NeoWave — rule-based Elliott", "color": "#a3e635", "concepts": ["neowave"]},
    {"id": "cycle", "name": "Cycle", "role": "Hurst cycles — timing", "color": "#fb7185", "concepts": ["hurst"]},
    {"id": "qm", "name": "QM", "role": "Quasimodo (over & under)", "color": "#f97316", "concepts": ["quasimodo"]},
    {"id": "chaos", "name": "Chaos", "role": "Bill Williams fractals & Alligator", "color": "#e879f9",
     "concepts": ["fractals"]},
    {"id": "scout", "name": "Scout", "role": "Market scanner — finds the best coin", "color": "#fde047",
     "concepts": ["scan"]},
    {"id": "quant", "name": "Quant", "role": "Quant brain — trend ranks, regime, odds", "color": "#22d3ee",
     "concepts": ["quant"]},
    {"id": "skeptic", "name": "Skeptic", "role": "Devil's advocate", "color": "#f87171", "concepts": []},
    {"id": "guard", "name": "Guard", "role": "Risk manager", "color": "#94a3b8", "concepts": ["plan"]},
    {"id": "chief", "name": "Chief", "role": "Head trader — makes the call", "color": "#e2e8f0",
     "concepts": ["scenarios", "verdict"]},
]
BY_ID = {a["id"]: a for a in AGENTS}
OWNER = {c: a["id"] for a in AGENTS for c in a["concepts"]}
SCHOOL_AGENTS = [a for a in AGENTS if a["concepts"] and a["concepts"][0] in
                 ("wyckoff", "dow", "gann", "profile", "harmonic", "po3", "neowave", "hurst", "quasimodo", "fractals")]
TONE = {1: "bull", -1: "bear", 0: "neutral"}
_RECORDS: dict[str, tuple[float, dict]] = {}


def track_record(tf: str) -> dict:
    """Measured hit rates of each school (app/maintenance/school_study.py), cached by file mtime."""
    f = Path("data/setups") / f"schools_{tf}.json"
    if not f.exists():
        return {}
    m = f.stat().st_mtime
    if tf not in _RECORDS or _RECORDS[tf][0] != m:
        _RECORDS[tf] = (m, json.loads(f.read_text()))
    return _RECORDS[tf][1]


def record_line(rec: dict, key: str, tf: str) -> str:
    r = (rec.get("schools") or {}).get(key)
    if not r or not r["oos"].get("n"):
        return f"No measured track record for this on {tf} yet."
    o = r["oos"]
    verdict = ("it has a measured edge" if r["useful"] else
               "that is NOT a proven edge, so treat my view as an opinion, not a signal")
    return (f"My record on unseen {tf} data: right {o['hit_rate']:.0%} of the time vs a {o['baseline']:.0%} baseline "
            f"(n={o['n']}) — {verdict}.")
WORD = {1: "bullish", -1: "bearish", 0: "neutral"}


def _sec(iso_or_ms) -> int:
    from datetime import datetime

    if isinstance(iso_or_ms, (int, float)):
        return int(iso_or_ms // 1000 if iso_or_ms > 1e11 else iso_or_ms)
    return int(datetime.fromisoformat(str(iso_or_ms).replace("Z", "+00:00")).timestamp())


# toolbar tool a person would pick for each chart object
TOOL_FOR = {"trendline": "trendline", "ray": "ray", "hline": "hline", "vline": "vline", "zone": "rect", "rect": "rect",
            "fib": "fib", "position": "position", "label": "label", "marker": "label", "wave": "trendline",
            "channel": "trendline"}
# indicators each agent adds to the chart before it explains (id, params, why)
AGENT_INDICATORS = {
    "atlas": [("ema", {"length": 50, "source": "close"}, "50 EMA"), ("ema", {"length": 200, "source": "close"},
                                                                     "200 EMA for the long-term trend")],
    "pulse": [("rsi", {"length": 14}, "RSI 14 for momentum"), ("macd", {"fast": 12, "slow": 26, "signal": 9},
                                                               "MACD to confirm momentum")],
    "session": [("vwap", {"anchor": "D", "bands": 0}, "daily VWAP — the day's fair price")],
    "chaos": [("ao", {"fast": 5, "slow": 34}, "Awesome Oscillator (Bill Williams' momentum)")],
}


def start_point(o: dict, now_s: int) -> tuple[int, float] | None:
    """Where a person would press the mouse to start drawing the object."""
    if o.get("points"):
        p = o["points"][0]
        return int(p["time"]), float(p["price"])
    if o.get("kind") == "position":
        return int(o.get("time1") or now_s), float(o["entry"])
    if o.get("price1") is None:
        return None
    return int(o.get("time1") or now_s), float(o["price1"])


class Script:
    def __init__(self):
        self.t = 0
        self.events: list[dict] = []

    def add(self, agent: str, typ: str, gap: int = 900, **kw) -> None:
        self.t += gap
        self.events.append({"t": self.t, "agent": agent, "type": typ, **kw})

    def say(self, agent: str, text: str, tone: str = "neutral", reply_to: str | None = None, gap: int | None = None):
        # reading time scales with message length so the playback feels like people talking
        self.add(agent, "say", gap if gap is not None else 700 + min(len(text) * 22, 3600), text=text, tone=tone,
                 reply_to=reply_to)

    def click(self, agent: str, target: str, gap: int = 650) -> None:
        self.add(agent, "click", gap, target=target)

    def draw(self, agent: str, o: dict, now_s: int, oid: str) -> bool:
        """Pick the tool, press at the start point, drag the object out (animated by the frontend, ~800 ms)."""
        st = start_point(o, now_s)
        if not st:
            return False
        self.click(agent, f"tool:{TOOL_FOR.get(o.get('kind'), 'trendline')}", 550)
        self.add(agent, "move", 650, time=st[0], price=st[1])
        self.add(agent, "draw", 300, object={**o, "id": oid, "origin": "ai"})
        self.t += 800
        return True

    def indicators(self, agent: str) -> None:
        for ind, params, why in AGENT_INDICATORS.get(agent, []):
            self.click(agent, "indicators", 600)
            self.click(agent, f"ind:{ind}", 700)
            self.add(agent, "indicator", 250, id=ind, params=params)
            self.say(agent, f"Adding the {why}.", gap=500)


def build_room(walk: dict, snap: dict, schools: list[dict] | None = None, scout: dict | None = None) -> dict:
    steps = {s["key"]: s for s in walk["steps"]}
    ann = {a["id"]: a for a in snap.get("annotations", [])}
    now_s = _sec(snap["as_of"])
    price = snap["price"]
    sc = Script()
    syn = walk["synthesis"]
    views: dict[str, int] = {}

    tf = walk["timeframe"]
    rec = track_record(tf)

    if scout:
        rows = scout["rows"]
        sc.say("chief", f"Scout, which coin should we work on today ({tf})?", gap=300)
        sess = scout.get("session") or {}
        n_all = scout.get("all_coins") or len(rows)
        sc.say("scout", f"I screened all {n_all} USDT coins and deep-analysed {len(rows)} on {tf}. It's "
                        f"{sess.get('utc', '')} UTC, "
                        f"{sess.get('session', '')} session" + (f", inside the {sess['killzone']}" if sess.get("killzone")
                                                                else "") + ". Top of my list:")
        for i, r in enumerate(rows[:5], 1):
            sc.click("scout", f"scan:{r['symbol']}", 700)
            sc.say("scout", f"{i}. **{r['symbol']}** — score {r['score']:.0f}: " + "; ".join(r["reasons"][:3]),
                   TONE[1 if r["direction"] == "bullish" else -1 if r["direction"] == "bearish" else 0], gap=900)
        top = rows[0]
        why = ("its setup, indicator confluence and council point the same way" if top.get("agree") else
               "it scores highest, but its methods do NOT fully agree — treat it with extra care")
        sc.click("scout", f"scan:{walk['symbol']}", 700)
        sc.say("scout", f"I pick **{walk['symbol']}**: {why}. Over to the team.", reply_to="chief")

    sc.say("chief", f"Team, we're on {walk['symbol']} {tf} at {price:,.6g}. Work top-down, show me the "
                    "evidence on the chart, and challenge each other. No trade unless the evidence lines up.", gap=300)

    # ---------------------------------------------------------------- phase 1: each specialist presents
    for a in AGENTS:
        if a in SCHOOL_AGENTS:
            continue
        concepts = [c for c in a["concepts"] if c in steps and c not in ("plan", "scenarios", "verdict")]
        for key in concepts:
            st = steps[key]
            title = st["title"].split(". ", 1)[-1]
            sc.say(a["id"], f"Checking {title.lower()}…", gap=600)
            if key == a["concepts"][0]:
                sc.indicators(a["id"])
            objs = [ann[i] for i in st["focus_ids"] if i in ann][:3] + st["objects"][:3]
            said: set[str] = set()
            for o in objs[:4]:
                if not sc.draw(a["id"], o, now_s, f"room_{a['id']}_{o.get('id', len(sc.events))}"):
                    continue
                if o.get("label") and o["label"] not in said:
                    said.add(o["label"])
                    sc.say(a["id"], f"Here — {o['label']}.", gap=400)
            if st["focus_ids"]:
                sc.add(a["id"], "focus", 100, ids=[i for i in st["focus_ids"] if i in ann])
            for b in st["bullets"][:2]:
                sc.say(a["id"], b)
            sc.say(a["id"], f"{st['observation']} My read: **{WORD[st['verdict']]}**.", TONE[st["verdict"]])
            if STEP_WEIGHTS.get(key):
                views[key] = st["verdict"]

    # ---------------------------------------------------------------- phase 1b: schools-of-thought council
    vote = None
    if schools:
        by_key = {st["key"]: st for st in schools}
        sc.say("chief", "Now the council: every school reads the same chart its own way. Tell me what you see and how "
                        "reliable your method has been.", gap=600)
        for a in SCHOOL_AGENTS:
            st = by_key.get(a["concepts"][0])
            if not st:
                continue
            sc.say(a["id"], f"{st['title']}: {st['plain'].split('. ')[0].rstrip('.')}.", gap=600)
            sc.indicators(a["id"])
            if st["key"] == "profile":
                sc.click(a["id"], "vp", 600)
                sc.add(a["id"], "vp", 200)
                sc.say(a["id"], "Switching on the volume profile so we can all see where volume traded.", gap=500)
            for o in st["objects"][:4]:
                sc.draw(a["id"], o, now_s, f"room_{o['id']}")
            for b in st["bullets"][:2]:
                sc.say(a["id"], b)
            sc.say(a["id"], f"{st['observation']} My read: **{WORD[st['verdict']]}**.", TONE[st["verdict"]])
            sc.say(a["id"], record_line(rec, st["key"], tf), "warn" if not (rec.get("schools") or {}).get(
                st["key"], {}).get("useful") else "neutral", gap=500)
        vote = school_vote(schools)
        core = syn["direction"]

        def names(keys: list[str]) -> str:
            return ", ".join(next(a["name"] for a in SCHOOL_AGENTS if a["concepts"][0] == k) for k in keys) or "none"
        sc.say("chief", f"Council vote: {len(vote['bull'])} bullish ({names(vote['bull'])}), {len(vote['bear'])} bearish "
                        f"({names(vote['bear'])}), {len(vote['neutral'])} neutral → **{vote['direction']}**.",
               TONE[1 if vote["direction"] == "bullish" else -1 if vote["direction"] == "bearish" else 0])
        if vote["direction"] != "neutral" and core != "neutral" and vote["direction"] != core:
            lead = next(a for a in SCHOOL_AGENTS if a["concepts"][0] in (vote["bull"] if vote["direction"] == "bullish"
                                                                          else vote["bear"]))
            sc.say(lead["id"], f"@Chief, the council reads {vote['direction']} while the core team reads {core}. "
                               "That conflict alone is a reason to size down or wait.", "warn", reply_to="chief")
            sc.say("skeptic", f"Agreed with {lead['name']} on caution — but remember, none of the council's methods "
                              "showed a reliable edge in our tests, so they can veto, not trigger, a trade.", "warn",
                   reply_to=lead["id"])
        elif vote["direction"] != "neutral" and vote["direction"] == core:
            sc.say("skeptic", f"Council and core team both lean {core}. Agreement is nice, but most of these methods "
                              "look at the same candles — it's not independent proof.", "warn")

    # ---------------------------------------------------------------- phase 2: debate
    bias_sign = 1 if syn["bias"] > 0 else -1 if syn["bias"] < 0 else 0
    weighted = sorted(views.items(), key=lambda kv: -STEP_WEIGHTS.get(kv[0], 0))
    leaders = [k for k, v in weighted if v == bias_sign and v != 0]
    dissent = [k for k, v in weighted if v == -bias_sign and v != 0]
    if bias_sign and leaders and dissent:
        lead, opp = leaders[0], dissent[0]
        la, oa = OWNER[lead], OWNER[opp]
        sc.say(oa, f"@{BY_ID[la]['name']}, I don't agree. {steps[opp]['observation']}", TONE[views[opp]], reply_to=la)
        wl, wo = STEP_WEIGHTS[lead], STEP_WEIGHTS[opp]
        sc.say(la, f"Fair point, but {steps[lead]['title'].split('. ', 1)[-1].lower()} carries more weight than "
                   f"{steps[opp]['title'].split('. ', 1)[-1].lower()} ({wl} vs {wo}). Your signal is about timing, mine is "
                   "about direction.", TONE[views[lead]], reply_to=oa)
        if len(dissent) > 1:
            names = ", ".join(BY_ID[OWNER[k]]["name"] for k in dissent[1:3])
            sc.say("skeptic", f"It's not just one voice: {names} also disagree. That's why agreement is only "
                              f"{syn['agreement']:.0%}.", "warn")
    elif not bias_sign:
        sc.say("skeptic", "The bulls and bears in this room are cancelling each other out. Mixed evidence is not an edge.",
               "warn")
    else:
        sc.say("skeptic", "Everyone agrees, which makes me nervous. Crowded reads fail too. Where is the invalidation?",
               "warn")

    cands = [c for c in snap.get("candidates", []) if "error" not in c]
    profile = get_settings().exit_profiles.get(walk["timeframe"])
    cands = [apply_exit_profile(c, profile) for c in cands]
    best = sorted(cands, key=lambda c: (-({"confirmed": 3, "pre_signal": 2, "forming": 1}.get(c["stage"], 0)),
                                        -c["validation"]["quality_score"]))
    counter = (best[0]["validation"]["counter_arguments"] if best else []) or []
    for ca in counter[:2]:
        sc.say("skeptic", f"Counter-argument: {ca}.", "warn")

    # ---------------------------------------------------------------- phase 3: risk manager
    trade = None
    if best:
        c = best[0]
        v = c["validation"]
        entry = (c["entry_low"] + c["entry_high"]) / 2
        ok = c["stage"] == "confirmed" and v["passed"]
        risk = abs(entry - c["stop"])
        tp = c["targets"][0]
        sz = size_position(RiskProfile(equity=100, risk_per_trade_pct=1.0, max_leverage=5), snap["symbol"],
                           c["direction"], entry, c["stop"])
        obj = {"id": "room_trade", "kind": "position", "origin": "ai", "concept": "plan", "time1": now_s,
               "direction": c["direction"], "entry": entry, "stop": c["stop"], "target": tp,
               "label": f"{c['direction']} {c['strategy'].replace('_', ' ')} ({c['stage']})"}
        sc.draw("guard", obj, now_s, "room_trade")
        rr = abs(tp - entry) / risk if risk else 0
        sc.say("guard", f"Best candidate: **{c['strategy'].replace('_', ' ')} {c['direction']}** — stage {c['stage']}. "
                        f"Entry {entry:,.6g}, stop {c['stop']:,.6g} ({risk / entry * 100:.2f}% away), target "
                        f"{tp:,.6g} ({abs(tp - entry) / entry * 100:.2f}% move, {rr:.1f}R)."
                        + (f" Exit profile: {profile}." if profile else ""), TONE[1 if c["direction"] == "LONG" else -1])
        if sz.get("ok"):
            sc.say("guard", f"On a $100 account risking 1%: position ≈ ${sz['notional']:,.2f}; if the stop hits we lose "
                            f"≈ ${sz['risk_amount']:.2f}, if the target hits we make ≈ ${sz['risk_amount'] * rr:.2f}.")
        if ok:
            trade = {"strategy": c["strategy"], "direction": c["direction"], "entry": entry, "stop": c["stop"],
                     "target": tp, "rr": rr, "exit_profile": profile or "default", "quality": v["quality_score"]}
        else:
            why = ([ch["name"].replace("_", " ") + " — " + ch["detail"] for ch in v["checks"] if not ch["passed"]][:2]
                   or [f"still waiting for: {', '.join(c['missing'])}"])
            sc.say("guard", "I'm blocking it for now: " + "; ".join(why) + ".", "warn")
    else:
        sc.say("guard", "No strategy setup on this chart, so there is nothing for me to size. Cash is a position.")

    # ---------------------------------------------------------------- phase 4: the call
    scen = steps.get("scenarios")
    if scen:
        for o in scen["objects"][:4]:
            sc.draw("chief", o, now_s, f"room_chief_{o.get('id')}")
        sc.say("chief", scen["observation"])
    verdict = (f"My call: **{trade['direction']}** — {trade['strategy'].replace('_', ' ')}, entry {trade['entry']:,.6g}, "
               f"stop {trade['stop']:,.6g}, target {trade['target']:,.6g}. Size from the stop, never all-in."
               if trade else
               f"My call: **NO TRADE** for now. The room is {syn['direction']} with {syn['confidence']} confidence "
               f"({syn['agreement']:.0%} agreement). We wait for one of the triggers on the chart.")
    if vote and trade and vote["direction"] != "neutral" and (vote["direction"] == "bullish") != (trade["direction"] == "LONG"):
        verdict += " The council disagrees with this direction, so I'd use half size."
    sc.say("chief", verdict, "bull" if trade and trade["direction"] == "LONG" else "bear" if trade else "neutral")
    return {"symbol": walk["symbol"], "timeframe": walk["timeframe"], "price": price, "agents": AGENTS,
            "events": sc.events, "duration_ms": sc.t, "synthesis": syn, "trade": trade, "council": vote,
            "schools": [{k: st[k] for k in ("key", "title", "verdict", "observation")} for st in schools or []],
            "track_record": {k: {"useful": v["useful"], "oos": v["oos"]} for k, v in (rec.get("schools") or {}).items()},
            "scout": scout,
            "disclaimer": walk["disclaimer"]}
