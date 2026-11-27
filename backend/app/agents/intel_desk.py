"""Intelligent desk: the desk agents as LLM agents with tools, run as one sequential conversation in the background.

Each agent gets the toolbox (app/agents/desk_tools.py), its role, the conclusions of the agents before it, and a task.
It decides which tools to call, reasons over the results and answers in plain English, ending with a JSON object
(text, level, picks, confidence). The deterministic messages (system_desk.brief / my_trades.coach) stay as the
facts line under every agent, so a reader can check the reasoning against the numbers.

Guardrails: every number must come from a tool result; the agents cannot place orders, change risk rules or override
the risk engine's sizes; a run is cached per (day, settings) for an hour; without an LLM key the desk is 'unavailable'
and the deterministic agents carry the page alone.
"""
import asyncio
import hashlib
import json
import time

import pandas as pd

from app.agents import desk_tools
from app.agents.llm import note_failure, note_success, provider
from app.agents.toolkit import run_agent
from app.config import get_settings
from app.logging import log

CACHE_S = 3600
RETRY_S = 300  # a desk that could not run (no credits, bad key, rate limit) is retried after this long
_runs: dict[str, dict] = {}
_sem = asyncio.Semaphore(2)
FATAL = ("no credits", "insufficient_quota", "quota", "billing", "invalid api key", "invalid_api_key", "authentication",
         "error code: 401", "error code: 429")


def _fatal(err: str) -> bool:
    """A provider error that every later agent would hit too: stop the desk instead of paying for six more failures."""
    e = err.lower()
    return any(k in e for k in FATAL)


def _short(err: str) -> str:
    import re

    msg = re.sub(r"^Error code: \d+ - ", "", err).strip()
    return (msg.split(". ")[0].rstrip(".") + ".")[:160]

GUARD = """You are {name} — {role} — on the desk of a crypto trading platform that runs a researched daily-trend system \
(buy a daily close above the 20-day high while BTC is above its 200-day average; stop 2.5 ATR; sell a third at 2R; \
trail the rest; strict risk limits). The reader is a beginner who trades on Binance.

Work like a real analyst: call the tools you need — several, when that gives a better answer — then answer.
Hard rules:
1. Every number, price, percentage, probability or outcome you state must appear in a tool result in this conversation. \
Never invent, round aggressively or extrapolate numbers. If a tool has no data, say so.
2. You cannot place orders, change the risk rules, or override the risk engine's sizes and limits. The system's orders \
are final; you explain, judge, rank and warn. Never promise returns.
3. Plain English, concrete, 3-7 sentences. No hype. Say plainly when evidence is mixed or missing. Write numbers the way \
a person says them (58% win rate, $4.04 margin, 7x, 6.9% below its high) — never raw field names such as \
liquidation_pct_below or avg_r, and never win rates as decimals like 0.625.
4. Finish with exactly one JSON object on its own line:
{{"text": "<your answer for the reader>", "level": "info" | "good" | "warn", "picks": [{{"symbol": "XXXUSDT", "reason": "<one line>"}}], "confidence": <0.0-1.0>}}
"picks" is for ranked coins (empty when not applicable); "level" is warn when the reader must act or be careful."""

SYSTEM_AGENTS = [
    ("regime", "Regime", "market-state specialist: is the system's edge alive today?",
     "Decide whether today's market is one where the system's edge exists. Use market_regime and research_facts('system'). "
     "Say what state BTC and the coins are in, how long it has lasted, and what that meant for the system in the research."),
    ("scout", "Scout", "coin selector: builds the top list of coins for the coming days, already analysed",
     "Build the desk's top list: up to 8 coins ranked best-first for the coming days. Call top_coins for the researched "
     "universe and for universe='all', then coin_analysis on every coin you consider (at least the top 6). Weigh: a live or "
     "near setup, trend state and quant rank, 60-day momentum, crowding/funding, and the coin's own breakout record. "
     "Only researched coins can become system orders; mention strong outside coins as 'outside the system'. Put the ranked "
     "list in picks with a one-line reason each, and say which are live setups, near, or far."),
    ("risk", "Risk Officer", "sizing and limits for the user's budget",
     "Explain what the user's budget, mode and venue mean today: risk per trade in dollars, the position and margin a ticket "
     "needs, the limits that bind, and whether the budget is too small for the coins in play (budget_plan, account_state). "
     "Be exact about the dollars."),
    ("leverage", "Leverage", "margin, liquidation and the safe leverage for each ticket",
     "For the Scout's top 3 researched picks call futures_ticket (use the requested leverage from account_state when there "
     "is one) and report the safe leverage, margin and liquidation distance versus the stop. Cite research_facts('leverage') "
     "on what high leverage did to this system. Warn if the requested leverage is above the safe one."),
    ("auditor", "Auditor", "checks the live record against the research and keeps accuracy honest",
     "Grade the live replay in account_state against research_facts('system') for the same mode: trades, win rate, average R, "
     "drawdown — consistent or not, and where the edge could be breaking. Use research_facts('win_rate') to say what "
     "accuracy is realistic and what a higher win rate would cost."),
    ("carry", "Carry", "market-neutral funding income",
     "Judge today's carry opportunity: funding_now and research_facts('carry'). Which coins clear the bar, what it would earn "
     "on this account per year, and how that compares with 2020-23."),
    ("manager", "Manager", "the desk's decision for the next daily open",
     "Give the decision for the next daily open using account_state and the other agents' conclusions below: the orders "
     "the system produced (you cannot add or remove any), the coins to watch from the Scout's list, the one risk to respect, "
     "and what would change the picture tomorrow. If there are no orders, say so and why in one sentence."),
]

PORTFOLIO_AGENTS = [
    ("planner", "Planner", "turns the user's budget into the settings the system invests with",
     "Use budget_plan (no arguments) and research_facts('system'). Tell the user exactly what their budget means: risk per "
     "trade, how many trades at once, typical position, margin and leverage, which coins are too small and what fixes it, "
     "and what the research periods would have done to their money. No promises."),
    ("manager", "Position Manager", "the next action for every open trade",
     "Call my_portfolio. For every open trade, call coin_analysis on its symbol and give the next action with the reason "
     "(stop, target, trailing stop, time limit, today's market context). If there are no open trades, say what the user "
     "should do when a ticket appears."),
    ("coach", "Coach", "the user's record and discipline against the plan",
     "From my_portfolio and research_facts('system'): judge the record (how many trades, wins, average R, realised P&L) "
     "against what the research expects, warn about small samples, and check discipline: leverage above the safe leverage "
     "for the stop, risk above the plan, open risk above the limit, missing stops. Be direct and kind."),
]


def _key(kind: str, parts: tuple) -> str:
    return kind + ":" + hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def _view(run: dict) -> dict:
    return {k: run.get(k) for k in ("status", "model", "started_at", "finished_at", "error", "agents", "top_coins",
                                    "tool_calls", "seconds")}


def _fallback_view(reason: str) -> dict:
    return {"status": "unavailable", "error": reason, "agents": [], "top_coins": [], "tool_calls": 0}


async def _run(run: dict, agents: list[tuple], tools: list, cards: dict[str, dict], context: str) -> None:
    from app.agents import costs

    costs.FEATURE.set("intel")
    async with _sem:
        t0 = time.time()
        said: list[str] = []
        picks_by_agent: dict[str, list] = {}
        fatal: str | None = None
        for aid, name, role, task in agents:
            if fatal:
                run["agents"].append({"agent": aid, "name": name, "role": role, "text": None, "level": "info", "picks": [],
                                      "confidence": None, "tools_used": [], "steps": 0, "error": f"skipped: {fatal}"})
                continue
            system = GUARD.format(name=name, role=role)
            prev = ("\n\nConclusions of the agents before you (use them, do not repeat them):\n" + "\n".join(said)) if said else ""
            try:
                res = await run_agent(system, f"Context: {context}\n\nYour task: {task}{prev}", tools)
            except Exception as e:  # noqa: BLE001 - one agent failing must not kill the desk
                log.warning("intel_agent_failed", agent=aid, error=str(e))
                res = None
                err = str(e)
                if _fatal(err):
                    fatal = _short(err)
                    note_failure(err)
            else:
                err = None
                if res is not None:
                    note_success()
            data = (res.data if res else None) or {}
            text = (data.get("text") or (res.text if res else "") or "").strip()
            picks = [p for p in data.get("picks", []) if isinstance(p, dict) and p.get("symbol")]
            picks_by_agent[aid] = picks
            entry = {"agent": aid, "name": name, "role": role, "text": text or None,
                     "level": data.get("level") if data.get("level") in ("info", "good", "warn") else "info",
                     "picks": picks, "confidence": data.get("confidence"),
                     "tools_used": [{"name": u["name"], "args": u["args"], "ok": u["ok"]} for u in (res.tools_used if res else [])],
                     "steps": res.steps if res else 0, "error": err if err else (None if text else "no answer")}
            run["agents"].append(entry)
            run["tool_calls"] += len(entry["tools_used"])
            if text:
                said.append(f"- {name}: {text}" + (f" Picks: {', '.join(p['symbol'] for p in picks)}." if picks else ""))
        scout = picks_by_agent.get("scout") or []
        run["top_coins"] = [{**cards.get(p["symbol"].upper() if p["symbol"].upper().endswith("USDT") else p["symbol"].upper() + "USDT",
                                       {"symbol": p["symbol"], "verdict": "outside the researched universe"}),
                             "reason": p.get("reason"), "rank": i + 1} for i, p in enumerate(scout)]
        run.update(status="ready", finished_at=pd.Timestamp.now("UTC").isoformat(), seconds=round(time.time() - t0, 1))
        if run["agents"] and all(a["error"] for a in run["agents"]):
            # nothing came back: say so plainly and retry soon (credits may be added, the provider may recover)
            first = next((a["error"] for a in run["agents"] if a["error"] and a["error"] != "no answer"), "no answer")
            run.update(status="unavailable", error=f"{run['model']}: {fatal or _short(first)}", t=time.time() - CACHE_S + RETRY_S)
        log.info("intel_desk_done", kind=run["kind"], status=run["status"], agents=len(run["agents"]),
                 tool_calls=run["tool_calls"], seconds=run["seconds"], error=run.get("error"))


def _idle() -> dict:
    return {"status": "idle", "agents": [], "top_coins": [], "tool_calls": 0, "error": None}


def _start(key: str, kind: str, agents: list[tuple], tools: list, cards: dict, context: str, start: bool = True) -> dict:
    run = _runs.get(key)
    if run and (run["status"] == "running" or desk_tools.fresh(run["t"], CACHE_S)):
        return _view(run)
    if not start:   # the user's rule: AI never runs by itself (it spends their credits); a button sends start=True
        return _idle()
    if provider() is None:
        return _fallback_view("no LLM key configured (AIT_OPENAI_API_KEY or AIT_ANTHROPIC_API_KEY)")
    s = get_settings()
    run = {"kind": kind, "status": "running", "t": time.time(), "started_at": pd.Timestamp.now("UTC").isoformat(),
           "finished_at": None, "model": s.openai_model if provider() == "openai" else s.llm_model, "error": None,
           "agents": [], "top_coins": [], "tool_calls": 0, "seconds": None}
    _runs[key] = run
    asyncio.create_task(_run(run, agents, tools, cards, context))
    return _view(run)


def system_desk(acct: dict, frames: dict, filters: dict | None, funding: dict | None, start: bool = False) -> dict:
    """Start (or return) the intelligent system desk for this account view."""
    key = _key("system", (acct["as_of"], acct["mode"], acct["venue"], acct["rules"]["capital"],
                          (acct.get("futures") or {}).get("leverage_requested")))
    run = _runs.get(key)
    if run and (run["status"] == "running" or desk_tools.fresh(run["t"], CACHE_S)):
        return _view(run)
    cards = desk_tools.analysis_cards(acct, frames, funding)
    tools = desk_tools.build_tools(acct, frames, cards, filters=filters, funding=funding)
    ctx = (f"data as of {acct['as_of']} (last finished daily candle); risk mode {acct['mode']}; venue {acct['venue']}; "
           f"budget ${acct['rules']['capital']:,.0f}; paper account only.")
    return _start(key, "system", SYSTEM_AGENTS, tools, cards, ctx, start)


def portfolio_desk(acct: dict, frames: dict, portfolio: dict, filters: dict | None, funding: dict | None,
                   start: bool = False) -> dict:
    """Start (or return) the intelligent portfolio desk (Planner, Position Manager, Coach)."""
    sig = [(c["id"], c.get("partial_done"), c.get("stop")) for c in portfolio["open"]] + [c["id"] for c in portfolio["closed"]]
    p = portfolio["budget_plan"]
    key = _key("portfolio", (acct["as_of"], p["budget_usd"], p["mode"], p["venue"], p["leverage_wanted"], sig))
    run = _runs.get(key)
    if run and (run["status"] == "running" or desk_tools.fresh(run["t"], CACHE_S)):
        return _view(run)
    cards = desk_tools.analysis_cards(acct, frames, funding)
    tools = desk_tools.build_tools(acct, frames, cards, extra={"portfolio": portfolio}, filters=filters, funding=funding)
    ctx = (f"data as of {acct['as_of']}; the user's budget ${p['budget_usd']:,.0f}, mode {p['mode']}, venue {p['venue']}, "
           f"wanted leverage {p['leverage_wanted']}x; {len(portfolio['open'])} open and {len(portfolio['closed'])} closed "
           f"recorded trades. The platform never places orders; the user does.")
    return _start(key, "portfolio", PORTFOLIO_AGENTS, tools, cards, ctx, start)
