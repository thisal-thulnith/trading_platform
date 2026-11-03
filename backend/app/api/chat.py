"""AI analyst chat + agent review endpoints.

The LLM can only (a) read deterministic analysis through tools and (b) emit chart actions through
strictly-typed tools that are validated here before being returned to the client."""
import json
import re

import anthropic
import openai
from fastapi import APIRouter, Depends, HTTPException

from app.agents.graph import run_agents
from app.agents.llm import FALLBACK_BETA, client, openai_client, provider
from app.api.schemas import ChatIn
from app.auth.security import chat_limiter, current_user_optional
from app.config import get_settings
from app.data.hub import hub
from app.data.timeframes import TIMEFRAMES
from app.db import session_factory
from app.engine import engine
from app.logging import log
from app.models import PaperAccount, User
from app.portfolio import paper

router = APIRouter(prefix="/api")

CHAT_SYSTEM = """You are the AI analyst inside a professional crypto market-intelligence terminal.

Hard rules:
- Every price, indicator value, probability or outcome you state must come from a tool result in this \
conversation. If you don't have it, call a tool or say you don't know. Never estimate numbers yourself.
- SMC (order blocks, FVGs, liquidity) and Elliott Wave labels are descriptive heuristics, not verified facts \
about institutional intent; say so when relevant. Distinguish observed exchange data from inferences.
- The platform prefers NO TRADE when evidence is mixed. Do not talk the user into trades; never promise returns.
- For "analyse / explain this market" questions call explain_step_by_step first and teach the reasoning in plain \
language (context → structure → liquidity/zones → scenarios → plan), defining any jargon briefly for beginners.
- Use chart tools to show what you're explaining (highlight the referenced annotation, draw the scenario). \
Only reference annotation ids returned by get_market_analysis.
- Be concise and structured (short markdown). This is decision support, not financial advice."""

SYM = {"type": "string", "description": "Binance USDT pair, e.g. BTCUSDT"}
TF = {"type": "string", "enum": list(TIMEFRAMES)}


def _tool(name, desc, props, req):
    return {"name": name, "description": desc, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": req, "additionalProperties": False}}


TOOLS = [
    _tool("get_market_analysis", "Deterministic analysis snapshot for a market: decision, regime, confluence by "
          "evidence family, indicators, SMC structure, liquidity, Elliott counts, derivatives, ML, setups and the ids "
          "of chart annotations.", {"symbol": SYM, "timeframe": TF}, ["symbol", "timeframe"]),
    _tool("run_agent_review", "Run the 15-agent review (technical, SMC, Elliott, derivatives, ML, news, regime, "
          "strategy, risk, validation, counter-argument, ...) and return each agent's structured report.",
          {"symbol": SYM, "timeframe": TF}, ["symbol", "timeframe"]),
    _tool("explain_step_by_step", "Human-style top-down walkthrough of a market: higher-timeframe context, regime, "
          "structure, levels, liquidity, zones, FVGs, premium/discount, Elliott, momentum, derivatives, news, bull/bear "
          "scenarios with triggers, trade plan and a weighted synthesis with confidence. Prefer this for 'analyse X' or "
          "'explain' questions.", {"symbol": SYM, "timeframe": TF}, ["symbol", "timeframe"]),
    _tool("compare_markets", "Compare several markets on the same timeframe.",
          {"symbols": {"type": "array", "items": {"type": "string"}}, "timeframe": TF}, ["symbols", "timeframe"]),
    _tool("find_best_setups", "Opportunity radar: markets ranked by validated setup stage and quality.",
          {"timeframe": {"type": "string", "enum": list(TIMEFRAMES) + ["all"]}}, ["timeframe"]),
    _tool("get_signals", "Recent platform signals for a symbol (or all), with lifecycle and outcome.",
          {"symbol": {"type": "string", "description": "symbol or 'all'"}}, ["symbol"]),
    _tool("price_scenario", "What happens structurally if price reaches a level: which liquidity, order blocks, "
          "structure levels and setup invalidations are crossed.", {"symbol": SYM, "timeframe": TF,
                                                                    "price": {"type": "number"}},
          ["symbol", "timeframe", "price"]),
    _tool("chart_highlight", "Highlight/focus an existing chart annotation by id.",
          {"annotation_id": {"type": "string"}, "note": {"type": "string"}}, ["annotation_id", "note"]),
    _tool("chart_draw_level", "Draw a horizontal level on the chart.",
          {"price": {"type": "number"}, "label": {"type": "string"}}, ["price", "label"]),
    _tool("chart_draw_trendline", "Draw a trendline between two (unix seconds, price) points.",
          {"time1": {"type": "integer"}, "price1": {"type": "number"}, "time2": {"type": "integer"},
           "price2": {"type": "number"}, "label": {"type": "string"}}, ["time1", "price1", "time2", "price2", "label"]),
    _tool("chart_show_scenario", "Show an entry/stop/target risk-reward box on the chart.",
          {"direction": {"type": "string", "enum": ["LONG", "SHORT"]}, "entry": {"type": "number"},
           "stop": {"type": "number"}, "target": {"type": "number"}, "label": {"type": "string"}},
          ["direction", "entry", "stop", "target", "label"]),
    _tool("chart_set_timeframe", "Switch the chart timeframe.", {"timeframe": TF}, ["timeframe"]),
    _tool("chart_hide_annotation", "Hide an AI annotation from the user's chart view (does not delete evidence).",
          {"annotation_id": {"type": "string"}}, ["annotation_id"]),
]


def _sym(s: str) -> str:
    s = re.sub(r"[^A-Z0-9]", "", s.upper())
    return s if s.endswith("USDT") else s + "USDT"


def compact_snapshot(snap: dict) -> dict:
    sm = snap.get("smc") or {}
    return {
        "symbol": snap["symbol"], "timeframe": snap["timeframe"], "price": snap["price"],
        "as_of_close_time": snap["as_of_close_time"], "decision": snap.get("decision"),
        "regime": {k: snap["regime"].get(k) for k in ("regime", "reasons", "secondary")},
        "confluence": {k: snap["confluence"][k] for k in ("bias", "direction", "families",
                                                         "independent_families_bull", "independent_families_bear")},
        "evidence": [{k: e[k] for k in ("family", "name", "direction", "detail", "basis", "ref")} for e in snap["evidence"]],
        "indicators": snap["indicators"], "mtf": snap.get("mtf"),
        "smc": {"trend_external": sm.get("trend_external"), "trend_internal": sm.get("trend_internal"),
                "premium_discount": sm.get("premium_discount"), "inducement": sm.get("inducement"),
                "recent_structure": (sm.get("structure") or [])[-6:], "sweeps": (sm.get("sweeps") or [])[-5:]},
        "elliott": [{k: c.get(k) for k in ("id", "role", "pattern", "direction", "current_wave", "invalidation",
                                           "projections", "score", "explanation")} for c in (snap.get("elliott") or {}).get("counts", [])],
        "derivatives": snap.get("derivatives"), "ml": snap.get("ml"),
        "setups": [{k: c.get(k) for k in ("strategy", "direction", "stage", "entry_low", "entry_high", "stop", "targets",
                                          "missing", "invalidation", "anchor_id")} | {"validation": {
                    k: c["validation"][k] for k in ("passed", "rr_tp1", "quality_score", "counter_arguments")} | {
                    "failed_checks": [ch["name"] for ch in c["validation"]["checks"] if not ch["passed"]]}}
                   for c in snap.get("candidates", []) if "error" not in c][:8],
        "annotations": [{k: a.get(k) for k in ("id", "concept", "label", "status", "price1", "price2", "time1", "time2")}
                        for a in snap.get("annotations", [])][:60],
        "news": (snap.get("news") or [])[:5],
    }


def scenario(snap: dict, price: float) -> dict:
    cur = snap["price"]
    lo, hi = sorted([cur, price])
    sm = snap.get("smc") or {}
    crossed = []
    for lq in sm.get("liquidity", []):
        if lq["status"] == "resting" and lo <= lq["price"] <= hi:
            crossed.append(f"takes {lq['type'].replace('_', ' ')} at {lq['price']:.6g} ({lq['source']})")
    for ob in sm.get("order_blocks", []) + sm.get("breakers", []):
        if ob["status"] != "invalidated" and ob["bottom"] <= hi and ob["top"] >= lo:
            crossed.append(f"trades through {ob['direction']} {ob['type'].replace('_', ' ')} {ob['bottom']:.6g}–{ob['top']:.6g}")
    for sw in (sm.get("swings") or [])[-6:]:
        if lo <= sw["price"] <= hi:
            crossed.append(f"breaks swing {sw['kind']} {sw['price']:.6g} ({sw['label']}) → potential "
                           f"{'BOS/CHoCH ' + ('bullish' if sw['kind'] == 'high' else 'bearish')}")
    inval = []
    for c in snap.get("candidates", []):
        if "error" in c:
            continue
        if (c["direction"] == "LONG" and price <= c["stop"]) or (c["direction"] == "SHORT" and price >= c["stop"]):
            inval.append(f"{c['strategy']} {c['direction']} setup invalidated (stop {c['stop']:.6g})")
    for cnt in (snap.get("elliott") or {}).get("counts", []):
        iv = cnt.get("invalidation")
        if iv and lo <= iv <= hi:
            inval.append(f"Elliott {cnt['role']} count ({cnt['pattern']}) invalidated at {iv:.6g}")
    return {"from": cur, "to": price, "move_pct": (price / cur - 1) * 100, "crossed": crossed, "invalidates": inval,
            "note": "Structural consequences only; not a forecast that price will get there."}


class ChatContext:
    def __init__(self, symbol: str | None, tf: str):
        self.symbol, self.tf = symbol, tf
        self.actions: list[dict] = []
        self.snaps: dict[tuple[str, str], dict] = {}

    async def snap(self, symbol: str, tf: str) -> dict:
        key = (_sym(symbol), tf)
        if key not in self.snaps:
            self.snaps[key] = await engine.analyze(key[0], tf)
        return self.snaps[key]

    def _price_ok(self, p: float) -> bool:
        ref = hub.last_price.get(self.symbol or "") or next(iter(self.snaps.values()), {}).get("price")
        return p > 0 and (ref is None or 0.3 * ref <= p <= 3 * ref)

    def _ann_ids(self) -> set[str]:
        return {a["id"] for s in self.snaps.values() for a in s.get("annotations", [])}

    async def run(self, name: str, a: dict) -> dict:
        if name == "get_market_analysis":
            return compact_snapshot(await self.snap(a["symbol"], a["timeframe"]))
        if name == "run_agent_review":
            s = await self.snap(a["symbol"], a["timeframe"])
            r = await run_agents(s, use_llm=False)
            return {k: {"status": v["status"], "summary": v["summary"], "findings": [f["text"] for f in v["findings"][:6]]}
                    for k, v in r["reports"].items() if k != "chief_analyst"}
        if name == "explain_step_by_step":
            w = await walkthrough(_sym(a["symbol"]), a["timeframe"], narrate=False)
            self.actions.append({"type": "walkthrough", "symbol": w["symbol"], "timeframe": w["timeframe"]})
            return {"synthesis": w["synthesis"], "decision": w["decision"],
                    "steps": [{"title": s["title"], "observation": s["observation"], "verdict": s["verdict_word"],
                               "bullets": s["bullets"][:6]} for s in w["steps"]]}
        if name == "compare_markets":
            out = {}
            for sym in a["symbols"][:5]:
                s = await self.snap(sym, a["timeframe"])
                out[s["symbol"]] = {"price": s["price"], "decision": s["decision"]["action"], "regime": s["regime"]["regime"],
                                    "bias": s["confluence"]["bias"], "families": {k: v["score"] for k, v in s["confluence"]["families"].items()},
                                    "trend_external": (s.get("smc") or {}).get("trend_external"),
                                    "rsi14": s["indicators"]["rsi14"], "adx": s["indicators"]["adx"]}
            return out
        if name == "find_best_setups":
            tf = None if a["timeframe"] == "all" else a["timeframe"]
            rows = sorted([r for (sy, t), r in engine.radar.items() if tf is None or t == tf], key=lambda r: -r["score"])
            return {"rows": rows[:10]}
        if name == "get_signals":
            from app.api.market import signals
            return {"signals": await signals(symbol=None if a["symbol"] == "all" else _sym(a["symbol"]), limit=15)}
        if name == "price_scenario":
            return scenario(await self.snap(a["symbol"], a["timeframe"]), float(a["price"]))
        # ---- chart actions (validated)
        if name == "chart_highlight" or name == "chart_hide_annotation":
            if a["annotation_id"] not in self._ann_ids():
                return {"error": "unknown annotation id; call get_market_analysis first and use a listed id"}
            self.actions.append({"type": "highlight" if name == "chart_highlight" else "hide",
                                 "annotation_id": a["annotation_id"], "note": a.get("note", "")[:300]})
            return {"ok": True}
        if name == "chart_draw_level":
            if not self._price_ok(a["price"]):
                return {"error": "price outside plausible range"}
            self.actions.append({"type": "draw", "object": {"kind": "hline", "price1": a["price"], "label": a["label"][:60],
                                                            "color": "#38bdf8", "origin": "ai_chat"}})
            return {"ok": True}
        if name == "chart_draw_trendline":
            if not (self._price_ok(a["price1"]) and self._price_ok(a["price2"])) or a["time2"] <= a["time1"]:
                return {"error": "invalid points"}
            self.actions.append({"type": "draw", "object": {"kind": "trendline", **{k: a[k] for k in ("time1", "price1", "time2", "price2")},
                                                            "label": a["label"][:60], "color": "#38bdf8", "origin": "ai_chat"}})
            return {"ok": True}
        if name == "chart_show_scenario":
            s = 1 if a["direction"] == "LONG" else -1
            if not all(self._price_ok(a[k]) for k in ("entry", "stop", "target")) or s * (a["entry"] - a["stop"]) <= 0 \
                    or s * (a["target"] - a["entry"]) <= 0:
                return {"error": "inconsistent scenario levels"}
            self.actions.append({"type": "draw", "object": {"kind": "position", "direction": a["direction"],
                                                            "entry": a["entry"], "stop": a["stop"], "target": a["target"],
                                                            "label": a["label"][:60], "origin": "ai_chat"}})
            rr = abs(a["target"] - a["entry"]) / abs(a["entry"] - a["stop"])
            return {"ok": True, "gross_rr": rr}
        if name == "chart_set_timeframe":
            self.actions.append({"type": "timeframe", "timeframe": a["timeframe"]})
            return {"ok": True}
        return {"error": f"unknown tool {name}"}


OPENAI_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                 "parameters": t["input_schema"], "strict": True}} for t in TOOLS]


async def openai_chat(body: ChatIn, ctx: ChatContext) -> dict | None:
    """Same tool loop as llm_chat, on OpenAI chat completions with function calling."""
    c = openai_client()
    if c is None:
        return None
    s = get_settings()
    hist = [m for m in body.history if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-10:]
    context = f"[UI context] chart symbol={body.symbol or 'none'} timeframe={body.timeframe}."
    messages = [{"role": "system", "content": CHAT_SYSTEM},
                *({"role": m["role"], "content": m["content"][:4000]} for m in hist),
                {"role": "user", "content": f"{context}\n\n{body.message}"}]
    for _ in range(8):
        try:
            r = await c.chat.completions.create(model=s.openai_model, messages=messages, tools=OPENAI_TOOLS,
                                                max_completion_tokens=8000)
        except openai.APIError as e:
            log.warning("chat_openai_error", error=str(e))
            return None
        msg = r.choices[0].message
        if not msg.tool_calls:
            text = (msg.content or "").strip()
            if r.choices[0].finish_reason == "length":
                text += "\n\n_(response truncated)_"
            return {"text": text, "actions": ctx.actions, "mode": "llm"}
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                out = await ctx.run(tc.function.name, json.loads(tc.function.arguments or "{}"))
                content = json.dumps(out, default=str)[:60_000]
            except Exception as e:  # noqa: BLE001
                content = json.dumps({"error": f"{type(e).__name__}: {e}"})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
    return {"text": "I ran out of tool steps; please narrow the question.", "actions": ctx.actions, "mode": "llm"}


async def llm_chat(body: ChatIn, ctx: ChatContext) -> dict | None:
    if provider() == "openai":
        return await openai_chat(body, ctx)
    c = client()
    if c is None:
        return None
    s = get_settings()
    hist = [m for m in body.history if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-10:]
    context = f"[UI context] chart symbol={body.symbol or 'none'} timeframe={body.timeframe}."
    messages = [*({"role": m["role"], "content": m["content"][:4000]} for m in hist),
                {"role": "user", "content": f"{context}\n\n{body.message}"}]
    for _ in range(8):
        try:
            r = await c.beta.messages.create(
                model=s.llm_model, max_tokens=8000, betas=[FALLBACK_BETA], fallbacks="default",
                output_config={"effort": s.llm_effort}, tools=TOOLS,
                system=[{"type": "text", "text": CHAT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=messages)
        except anthropic.APIError as e:
            log.warning("chat_llm_error", error=str(e))
            return None
        if r.stop_reason == "refusal":
            return {"text": "I can't help with that request.", "actions": ctx.actions, "mode": "llm"}
        if r.stop_reason != "tool_use":
            text = "".join(b.text for b in r.content if b.type == "text").strip()
            if r.stop_reason == "max_tokens":
                text += "\n\n_(response truncated)_"
            return {"text": text, "actions": ctx.actions, "mode": "llm"}
        messages.append({"role": "assistant", "content": r.content})
        results = []
        for b in r.content:
            if b.type != "tool_use":
                continue
            try:
                out = await ctx.run(b.name, dict(b.input))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str)[:60_000]})
            except Exception as e:  # noqa: BLE001
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": f"{type(e).__name__}: {e}"})
        messages.append({"role": "user", "content": results})
    return {"text": "I ran out of tool steps; please narrow the question.", "actions": ctx.actions, "mode": "llm"}


async def rule_chat(body: ChatIn, ctx: ChatContext) -> dict:
    """Deterministic fallback analyst (no LLM configured or provider down)."""
    m = body.message.lower()
    tf = body.timeframe or "1h"
    mentioned = [w for w in re.findall(r"\b([a-z]{2,6})\b", m) if w.upper() in hub.universe]
    sym = _sym(mentioned[0]) if mentioned and not body.symbol else (body.symbol or (_sym(mentioned[0]) if mentioned else None))
    if any(k in m for k in ("best setup", "best trade", "opportunit", "radar")):
        rows = (await ctx.run("find_best_setups", {"timeframe": "all"}))["rows"][:6]
        lines = [f"- **{r['symbol']} {r['timeframe']}** score {r['score']} · {r['regime']} · decision {r['decision']}"
                 + (f" · {r['best']['strategy']} {r['best']['direction']} ({r['best']['stage']})" if r["best"] else "")
                 for r in rows]
        return {"text": "Top markets by validated setup quality:\n" + "\n".join(lines or ["- radar warming up"]),
                "actions": ctx.actions, "mode": "rules"}
    if "compare" in m and len(mentioned) >= 2:
        out = await ctx.run("compare_markets", {"symbols": mentioned[:4], "timeframe": tf})
        lines = [f"- **{k}**: {v['decision']} · {v['regime']} · bias {v['bias']:+.2f} · structure {v['trend_external']} · "
                 f"RSI {v['rsi14'] or 0:.0f} · ADX {v['adx'] or 0:.0f}" for k, v in out.items()]
        return {"text": f"Comparison ({tf}):\n" + "\n".join(lines), "actions": ctx.actions, "mode": "rules"}
    if not sym:
        return {"text": "Open a market on the chart (or name one, e.g. BTC) and ask again. An LLM provider is not "
                        "configured, so I answer with deterministic analysis only.", "actions": [], "mode": "rules"}
    s = await ctx.snap(sym, tf)
    if "sweep" in m or "liquidity" in m:
        sw = [a for a in s["annotations"] if a["concept"] == "liquidity_sweep"]
        if not sw:
            return {"text": "No liquidity sweep detected in the analysed window.", "actions": [], "mode": "rules"}
        a = sw[-1]
        await ctx.run("chart_highlight", {"annotation_id": a["id"], "note": a["label"]})
        e = a["explain"]
        return {"text": f"Latest **{a['label']}** at {e['level']:.6g} ({e['side']}-side). Criteria: {e['criteria']}. "
                        f"Stop-hunt candidate: {e['stop_hunt_candidate']}. {e['limitations']}", "actions": ctx.actions, "mode": "rules"}
    if "elliott" in m or "wave" in m:
        cs = s["elliott"]["counts"]
        if not cs:
            return {"text": "No rule-valid Elliott count at the current scales.", "actions": [], "mode": "rules"}
        await ctx.run("chart_highlight", {"annotation_id": cs[0]["id"], "note": "primary count"})
        txt = "\n".join(f"- **{c['role']}** {c['pattern']} ({c['direction']}), wave {c.get('current_wave')}, invalidation "
                        f"{c.get('invalidation')}, score {c['score']} — {c['explanation']}" for c in cs)
        return {"text": "Elliott interpretations (subjective, uncalibrated):\n" + txt, "actions": ctx.actions, "mode": "rules"}
    if "disagree" in m or "bearish alternative" in m or "bullish alternative" in m or "against" in m:
        d = 1 if s["confluence"]["bias"] >= 0 else -1
        opp = [e for e in s["evidence"] if e["direction"] == -d]
        return {"text": "Evidence opposing the prevailing read:\n" + "\n".join(f"- [{e['family']}] {e['name']}: {e['detail']}"
                                                                               for e in opp[:10]) or "none", "actions": [], "mode": "rules"}
    nums = re.findall(r"\d[\d,]*\.?\d*", m)
    if ("break" in m or "what if" in m or "happens" in m) and nums:
        price = float(nums[-1].replace(",", ""))
        sc = scenario(s, price)
        await ctx.run("chart_draw_level", {"price": price, "label": "scenario level"})
        return {"text": f"If {sym} reaches {price:,.6g} ({sc['move_pct']:+.2f}%):\n" + "\n".join(f"- {x}" for x in sc["crossed"] + sc["invalidates"])
                + f"\n\n_{sc['note']}_", "actions": ctx.actions, "mode": "rules"}
    if "entry" in m or "signal" in m or "setup" in m:
        cands = [c for c in s["candidates"] if "error" not in c]
        if not cands:
            return {"text": f"No active setups on {sym} {tf}. Decision: NO TRADE — " + "; ".join(s["decision"]["reasons"]),
                    "actions": [], "mode": "rules"}
        c = max(cands, key=lambda c: c["validation"]["quality_score"])
        await ctx.run("chart_show_scenario", {"direction": c["direction"], "entry": (c["entry_low"] + c["entry_high"]) / 2,
                                              "stop": c["stop"], "target": c["targets"][0], "label": c["strategy"]})
        conds = "\n".join(f"- {'✓' if x['met'] else '✗'} {x['name']} ({x['kind']}): {x['detail']}" for x in c["conditions"])
        return {"text": f"**{c['strategy']} {c['direction']}** [{c['stage']}] entry {c['entry_low']:.6g}–{c['entry_high']:.6g}, "
                        f"stop {c['stop']:.6g}, targets {', '.join(f'{t:.6g}' for t in c['targets'])}. R:R(TP1) "
                        f"{c['validation']['rr_tp1']:.2f}.\n{conds}\nInvalidation: {c['invalidation']}.",
                "actions": ctx.actions, "mode": "rules"}
    w = await walkthrough(sym, tf, narrate=False)
    syn = w["synthesis"]
    lines = [f"**{sym} {tf} — {syn['direction']} read, {syn['confidence']} confidence** "
             f"(decision: {w['decision']['action'].replace('_', ' ')})", ""]
    lines += [f"- {x}" for x in syn["story"]]
    sc = next(s for s in w["steps"] if s["key"] == "scenarios")
    lines += ["", "**Scenarios**"] + [f"- {b}" for b in sc["bullets"]]
    lines += ["", "Press **▶ Step-by-step** above the chart to watch the full walkthrough on the chart."]
    ctx.actions.append({"type": "walkthrough", "symbol": sym, "timeframe": tf})
    return {"text": "\n".join(lines), "actions": ctx.actions, "mode": "rules"}


@router.post("/chat", dependencies=[Depends(chat_limiter)])
async def chat(body: ChatIn):
    sym = _sym(body.symbol) if body.symbol else None
    ctx = ChatContext(sym, body.timeframe or "1h")
    try:
        res = await llm_chat(body, ctx)
        if res is None:
            res = await rule_chat(body, ctx)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res["llm_configured"] = provider() is not None
    return res


@router.get("/agents/{symbol}")
async def agent_review(symbol: str, tf: str = "1h", u: User | None = Depends(current_user_optional)):
    from app.api.market import performance

    snap = await engine.analyze(_sym(symbol), tf)
    pf = None
    if u:
        async with session_factory()() as db:
            from sqlalchemy import select
            a = (await db.execute(select(PaperAccount).where(PaperAccount.user_id == u.id))).scalar()
            if a:
                pf = await paper.summary(db, a, hub.last_price)
    return await run_agents(snap, pf, await performance())


async def _walk(symbol: str, tf: str) -> tuple[dict, dict]:
    import asyncio

    from app.agents.scout import ensure_fresh
    from app.analysis.walkthrough import build_walkthrough
    from app.engine import HTF_MAP

    sym = _sym(symbol)
    if tf not in TIMEFRAMES:
        raise HTTPException(400, "bad timeframe")
    try:
        await ensure_fresh(sym, tf)
        snap = await engine.analyze(sym, tf)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    htf: dict[str, dict] = {}
    results = await asyncio.gather(*(engine.analyze(sym, h) for h in reversed(HTF_MAP.get(tf, []))), return_exceptions=True)
    for h, r in zip(reversed(HTF_MAP.get(tf, [])), results):
        if isinstance(r, dict):
            htf[h] = r
    return build_walkthrough(snap, htf), snap


@router.get("/walkthrough/{symbol}")
async def walkthrough(symbol: str, tf: str = "1h", narrate: bool = False):
    """Guided human-style analysis: top-down timeframes, every concept as a step with chart focus objects."""
    from app.agents.llm import narrate_walkthrough

    walk, _ = await _walk(symbol, tf)
    walk["narrative"] = await narrate_walkthrough(walk) if narrate else None
    walk["llm_configured"] = provider() is not None
    return walk


async def _schools(sym: str, tf: str) -> list[dict]:
    from app.analysis.schools import run_schools
    from app.data.hub import now_ms
    from app.data.store import closed_only, load_candles

    async with session_factory()() as db:
        df = closed_only(await load_candles(db, sym, tf, limit=500), tf, now_ms())
    return run_schools(df, tf) if len(df) >= 120 else []


@router.get("/coins")
async def coins():
    """Every tradeable USDT coin on Binance spot (stablecoins and leveraged tokens removed), most liquid first."""
    from app.agents.scout import all_coins

    try:
        return {"coins": await all_coins()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"coin list unavailable: {e}") from e


@router.get("/room/scan")
async def room_scan(tf: str = "4h", refresh: bool = False):
    """Scout: screens all USDT coins, deep-analyses the most liquid/active ones in the background. Poll for progress;
    `rows` holds the latest finished ranking (possibly from the previous run while a refresh is in progress)."""
    from app.agents.scout import scan_state

    if tf not in TIMEFRAMES:
        raise HTTPException(400, "bad timeframe")
    return scan_state(tf, refresh=refresh)


@router.post("/room/session")
async def room_session(symbol: str | None = None, tf: str = "4h"):
    """Start a live agent session (LLM team when a key is configured, rule-based team otherwise).
    symbol omitted = the Chief picks the coin from Scout's scan."""
    from app.agents.live_room import session_view, start_session

    if tf not in TIMEFRAMES:
        raise HTTPException(400, "bad timeframe")
    s = await start_session(_sym(symbol) if symbol else None, tf)
    return session_view(s["id"])


@router.get("/room/session/{sid}")
async def room_session_poll(sid: str, after: int = 0):
    """Events produced since `after` (agents keep working while the frontend plays what already exists)."""
    from app.agents.live_room import session_view

    v = session_view(sid, after)
    if v is None:
        raise HTTPException(404, "session expired")
    return v


@router.get("/room/{symbol}")
async def war_room(symbol: str, tf: str = "1h"):
    """Agent War Room: the analysis as a timeline of specialist agents moving cursors, drawing and debating.
    symbol=AUTO lets the Scout scan every coin and pick the best one first."""
    from app.agents.room import build_room
    from app.agents.scout import plain_signal, wait_scan

    scout = None
    if symbol.upper() == "AUTO":
        scout = await wait_scan(tf)
        if not scout["rows"]:
            raise HTTPException(503, "Scout is still scanning — try again in a minute")
        symbol = scout["rows"][0]["symbol"]
    walk, snap = await _walk(symbol, tf)
    room = build_room(walk, snap, await _schools(_sym(symbol), tf), scout)
    room["signal"] = plain_signal(snap, tf)
    return room


# ----------------------------------------------------------------- quant brain, forecast ledger, setup odds
@router.get("/quant")
async def quant_state(refresh: bool = False, top: int = 200):
    from app.quant import brain

    d = await brain.get(refresh)
    if not d:
        raise HTTPException(503, "Quant brain is warming up — try again in a few seconds")
    return {**d, "ranks": d["ranks"][:top]}


@router.get("/ledger")
async def ledger_state():
    from app.quant import ledger

    return await ledger.scores()


@router.get("/odds")
async def odds_table():
    from app.quant import odds

    return {"rows": await odds.table(), "min_n": odds.MIN_N}


@router.get("/system")
async def system_account(capital: float = 100.0, days: int = 365, mode: str = "conservative", venue: str = "spot",
                         leverage: int = 20):
    """The platform's own $100 paper account: daily trend breakout + system-managed risk. venue=futures adds Binance
    USDⓈ-M tickets with the leverage capped so the stop comes before the liquidation."""
    from app.portfolio import system

    if not 10 <= capital <= 1_000_000 or not 30 <= days <= 500:
        raise HTTPException(400, "capital 10..1,000,000 and days 30..500")
    if venue not in system.VENUES or not 1 <= leverage <= 125:
        raise HTTPException(400, "venue spot|futures and leverage 1..125")
    try:
        return await system.account(capital, days, mode, venue, leverage)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/system/desk")
async def system_desk(capital: float = 100.0, days: int = 365, mode: str = "conservative", venue: str = "spot",
                      leverage: int = 20):
    """The intelligent desk for the system account: LLM agents with tools, run in the background (first call starts it,
    later calls return progress, then the cached result for an hour)."""
    from app.agents import intel_desk
    from app.portfolio import system

    if not 10 <= capital <= 1_000_000 or not 30 <= days <= 500:
        raise HTTPException(400, "capital 10..1,000,000 and days 30..500")
    if venue not in system.VENUES or not 1 <= leverage <= 125:
        raise HTTPException(400, "venue spot|futures and leverage 1..125")
    try:
        acct = await system.account(capital, days, mode, venue, leverage)
        frames = await system._frames()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return intel_desk.system_desk(acct, frames, system._cache.get("filters") or {}, system._cache.get("funding") or {})
