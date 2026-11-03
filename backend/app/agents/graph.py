"""Multi-agent analysis graph (LangGraph).

Principle: deterministic code computes every number; agents are typed wrappers that interpret structured
evidence. Only the Chief Analyst narrative may use an LLM, and it receives the validated JSON reports, never
raw freedom to invent values. The graph runs without any LLM provider (template narrative fallback).
"""
import asyncio
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

AGENTS = ["market_data", "technical", "smc", "elliott", "derivatives", "ml_forecast", "news", "regime", "strategy",
          "risk", "validation", "counter_argument", "chart_annotation", "portfolio", "research", "chief_analyst"]


class Finding(BaseModel):
    text: str
    direction: int = 0  # +1/-1/0
    ref: str | None = None
    basis: str = "derived"


class AgentReport(BaseModel):
    agent: str
    status: str = "ok"  # ok | degraded | unavailable | error
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    data_as_of: int | None = None
    duration_ms: float = 0.0
    error: str | None = None


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class State(TypedDict):
    snap: dict
    portfolio: dict | None
    perf: dict | None
    reports: Annotated[dict, _merge]
    narrative: str | None
    use_llm: bool


def _timed(name: str):
    def deco(fn):
        async def node(state: State) -> dict:
            t0 = time.perf_counter()
            try:
                rep = fn(state)
                if asyncio.iscoroutine(rep):
                    rep = await asyncio.wait_for(rep, timeout=30)
            except Exception as e:  # noqa: BLE001
                rep = AgentReport(agent=name, status="error", summary="agent failed", error=f"{type(e).__name__}: {e}")
            rep.duration_ms = (time.perf_counter() - t0) * 1000
            rep.data_as_of = rep.data_as_of or state["snap"].get("as_of")
            return {"reports": {name: rep.model_dump()}}
        node.__name__ = name
        return node
    return deco


def _ev(snap, family):
    return [Finding(text=f"{e['name']}: {e['detail']}", direction=e["direction"], ref=e.get("ref"), basis=e["basis"])
            for e in snap["evidence"] if e["family"] == family]


@_timed("market_data")
def market_data(s):
    snap = s["snap"]
    fr = snap.get("data_freshness") or {}
    stale = fr.get("stale", False)
    return AgentReport(agent="market_data", status="degraded" if stale else "ok",
                       summary=f"{snap['bars_used']} closed {snap['timeframe']} candles, data hash {snap['data_hash'][:10]}…"
                       + (" — LIVE FEED STALE" if stale else ""),
                       data={"freshness": fr, "price": snap["price"], "as_of_close_time": snap["as_of_close_time"]})


@_timed("technical")
def technical(s):
    snap = s["snap"]
    ind = snap["indicators"]
    f = _ev(snap, "trend") + _ev(snap, "momentum") + _ev(snap, "volume_flow")
    fam = snap["confluence"]["families"]
    return AgentReport(agent="technical", summary=f"trend {fam.get('trend', {}).get('score', 0):+.2f}, momentum "
                       f"{fam.get('momentum', {}).get('score', 0):+.2f}, RSI {ind.get('rsi14') or 0:.1f}, ADX {ind.get('adx') or 0:.1f}",
                       findings=f, data={"indicators": ind, "mtf": snap.get("mtf")})


@_timed("smc")
def smc(s):
    snap = s["snap"]
    sm = snap["smc"]
    if "error" in sm:
        return AgentReport(agent="smc", status="unavailable", summary=sm["error"])
    active_ob = [o for o in sm["order_blocks"] if o["status"] != "invalidated"]
    open_fvg = [g for g in sm["fvgs"] if g["status"] in ("open", "partially_filled")]
    return AgentReport(agent="smc", summary=f"external {sm['trend_external']}, internal {sm['trend_internal']}; "
                       f"{len(active_ob)} active OBs, {len(open_fvg)} open FVGs, {len(sm['sweeps'])} recent sweeps; "
                       f"price in {(sm.get('premium_discount') or {}).get('zone', 'n/a')}",
                       findings=_ev(snap, "structure") + _ev(snap, "liquidity"),
                       data={"premium_discount": sm.get("premium_discount"), "inducement": sm.get("inducement"),
                             "session": sm.get("session")})


@_timed("elliott")
def elliott(s):
    ew = s["snap"].get("elliott") or {}
    cs = ew.get("counts", [])
    if not cs:
        return AgentReport(agent="elliott", status="degraded", summary="no rule-valid wave count at current scales")
    p = cs[0]
    return AgentReport(agent="elliott", summary=f"primary: {p['direction']} {p['pattern']} (wave {p.get('current_wave')}), "
                       f"invalidation {p.get('invalidation')}; {len(cs) - 1} alternate(s). Subjective, uncalibrated.",
                       findings=[Finding(text=c["explanation"], ref=c["id"], basis="inferred") for c in cs],
                       data={"counts": cs})


@_timed("derivatives")
def derivatives(s):
    d = s["snap"].get("derivatives") or {}
    if not d.get("available"):
        return AgentReport(agent="derivatives", status="unavailable", summary="no perpetual market data for this symbol")
    return AgentReport(agent="derivatives", summary=f"funding {100 * (d.get('funding_rate') or 0):.4f}% "
                       f"(z {d.get('funding_z') or 0:.1f}), OI 4h {d.get('oi_change_4h_pct') or 0:+.2f}%, "
                       f"basis {d.get('basis_bps') or 0:.1f} bps, L/S {d.get('long_short_ratio')}",
                       findings=_ev(s["snap"], "derivatives"), data=d)


@_timed("ml_forecast")
def ml_forecast(s):
    ml = s["snap"].get("ml")
    if not ml:
        return AgentReport(agent="ml_forecast", status="unavailable",
                           summary="no ACCEPTED model for this timeframe (models that fail walk-forward validation are never served)")
    return AgentReport(agent="ml_forecast", summary=f"{ml['model']} v{ml['version']}: P(up)={ml['p_up']:.2f} over {ml['horizon']}"
                       + (" — DRIFT ALERT" if ml.get("drift_alert") else ""), findings=_ev(s["snap"], "ml"), data=ml)


@_timed("news")
def news(s):
    items = s["snap"].get("news") or []
    fg = s["snap"].get("fear_greed")
    hi = [n for n in items if n.get("impact") == "high"]
    return AgentReport(agent="news", status="ok" if items or fg else "degraded",
                       summary=f"{len(items)} relevant headlines (24h), {len(hi)} high-impact"
                       + (f"; Fear&Greed {fg['value']} ({fg['label']})" if fg else ""),
                       findings=[Finding(text=f"{n['source']}: {n['title']}", direction=(1 if n['sentiment'] > 0.3 else -1 if
                                         n['sentiment'] < -0.3 else 0), basis="inferred") for n in items[:6]],
                       data={"fear_greed": fg})


@_timed("regime")
def regime(s):
    r = s["snap"]["regime"]
    return AgentReport(agent="regime", summary=f"{r['regime']} ({'; '.join(r.get('reasons', []))})",
                       data={"regime": r["regime"], "secondary": r.get("secondary"), "suited": r.get("suited_strategies"),
                             "features": r.get("features")})


@_timed("strategy")
def strategy(s):
    cands = [c for c in s["snap"].get("candidates", []) if "error" not in c]
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    cands.sort(key=lambda c: (-stage_w.get(c["stage"], 0), -c["validation"]["quality_score"]))
    return AgentReport(agent="strategy", summary=f"{len(cands)} candidate setup(s)"
                       + (f"; best: {cands[0]['strategy']} {cands[0]['direction']} ({cands[0]['stage']})" if cands else ""),
                       findings=[Finding(text=f"{c['strategy']} {c['direction']} [{c['stage']}] missing: "
                                         f"{', '.join(c['missing']) or 'none'}", direction=1 if c["direction"] == "LONG" else -1,
                                         ref=c["anchor_id"]) for c in cands[:6]],
                       data={"candidates": cands[:6]})


@_timed("risk")
def risk(s):
    from app.risk.sizing import RiskProfile, size_position

    cands = [c for c in s["snap"].get("candidates", []) if "error" not in c and c["stage"] in ("confirmed", "pre_signal")]
    pf = s.get("portfolio") or {}
    eq = pf.get("equity", 1000.0)
    rp = (pf.get("risk") or {})
    out = []
    for c in cands[:3]:
        entry = (c["entry_low"] + c["entry_high"]) / 2
        sz = size_position(RiskProfile(equity=eq, risk_per_trade_pct=rp.get("risk_per_trade_pct", 1.0),
                                       max_leverage=rp.get("max_leverage", 3.0)), s["snap"]["symbol"], c["direction"],
                           entry, c["stop"], open_positions=len(pf.get("open_positions", [])))
        out.append({"strategy": c["strategy"], "direction": c["direction"], "sizing": sz})
    return AgentReport(agent="risk", summary=f"sized {len(out)} setup(s) for equity {eq:,.2f}" if out else "no setup to size",
                       data={"sizing": out, "equity": eq})


@_timed("validation")
def validation(s):
    cands = [c for c in s["snap"].get("candidates", []) if "error" not in c and c["stage"] == "confirmed"]
    passed = [c for c in cands if c["validation"]["passed"]]
    return AgentReport(agent="validation", summary=f"{len(passed)}/{len(cands)} confirmed setup(s) passed all gates",
                       findings=[Finding(text=f"{c['strategy']}: {ch['name']} — {ch['detail']}",
                                         direction=0) for c in cands for ch in c["validation"]["checks"] if not ch["passed"]],
                       data={"decision": s["snap"].get("decision")})


@_timed("counter_argument")
def counter_argument(s):
    cands = [c for c in s["snap"].get("candidates", []) if "error" not in c and c["stage"] in ("confirmed", "pre_signal")]
    conf = s["snap"]["confluence"]
    args = []
    for c in cands[:3]:
        args += [f"[{c['strategy']} {c['direction']}] {a}" for a in c["validation"]["counter_arguments"]]
    if not cands:
        opp = "bearish" if conf["bias"] > 0 else "bullish"
        args = [f"{e['name']}: {e['detail']}" for e in s["snap"]["evidence"]
                if e["direction"] == (-1 if conf["bias"] > 0 else 1)][:5]
        summary = f"strongest {opp} counter-evidence to the prevailing {conf['direction']} read"
    else:
        summary = f"{len(args)} reason(s) the leading setup(s) may fail"
    return AgentReport(agent="counter_argument", summary=summary, findings=[Finding(text=a, direction=0) for a in args])


@_timed("chart_annotation")
def chart_annotation(s):
    ann = s["snap"].get("annotations", [])
    kinds: dict[str, int] = {}
    for a in ann:
        kinds[a["concept"]] = kinds.get(a["concept"], 0) + 1
    return AgentReport(agent="chart_annotation", summary=f"{len(ann)} editable chart objects generated",
                       data={"by_concept": kinds})


@_timed("portfolio")
def portfolio(s):
    pf = s.get("portfolio")
    if not pf:
        return AgentReport(agent="portfolio", status="unavailable", summary="no paper portfolio configured")
    sym = s["snap"]["symbol"]
    exp = pf.get("exposure", {}).get(sym, 0)
    corr = pf.get("correlation", {})
    return AgentReport(agent="portfolio", summary=f"equity {pf['equity']:,.2f}, drawdown {pf['current_drawdown_pct']:.1f}%, "
                       f"{len(pf['open_positions'])} open; exposure to {sym}: {exp:,.2f}"
                       + (f"; {corr['warning']}" if corr.get("warning") else ""), data={"exposure": pf.get("exposure")})


@_timed("research")
def research(s):
    perf = s.get("perf") or {}
    bs = perf.get("by_strategy", {})
    reg = s["snap"]["regime"]["regime"]
    br = perf.get("by_regime", {}).get(reg)
    notes = [f"{k}: {v['n']} trades, expectancy {v['expectancy_r']:+.2f}R" for k, v in bs.items() if v.get("n")]
    return AgentReport(agent="research", status="ok" if notes else "degraded",
                       summary=(f"forward-test in current regime ({reg}): {br['n']} trades, {br['expectancy_r']:+.2f}R"
                                if br and br.get("n") else "insufficient forward-test history in this regime"),
                       findings=[Finding(text=n) for n in notes[:8]])


def template_narrative(snap: dict, reports: dict) -> str:
    d = snap.get("decision") or {}
    conf = snap["confluence"]
    lines = [f"**{snap['symbol']} {snap['timeframe']} — {d.get('action', 'NO_TRADE')}**",
             f"Regime: {reports['regime']['summary']}.",
             f"Confluence bias {conf['bias']:+.2f} ({conf['independent_families_bull']} bullish vs "
             f"{conf['independent_families_bear']} bearish independent families).",
             f"Structure: {reports['smc']['summary']}.", f"Technicals: {reports['technical']['summary']}.",
             f"Derivatives: {reports['derivatives']['summary']}.", f"ML: {reports['ml_forecast']['summary']}.",
             f"Setups: {reports['strategy']['summary']}.", f"Validation: {reports['validation']['summary']}."]
    if d.get("reasons"):
        lines.append("Decision rationale: " + "; ".join(d["reasons"]) + ".")
    ca = reports["counter_argument"]["findings"][:3]
    if ca:
        lines.append("Counter-arguments: " + "; ".join(f["text"] for f in ca) + ".")
    lines.append("All values are computed deterministically from exchange data; this is decision support, not advice.")
    return "\n".join(lines)


async def chief(state: State) -> dict:
    t0 = time.perf_counter()
    reports = state["reports"]
    text = template_narrative(state["snap"], reports)
    status = "ok"
    try:
        from app.agents.llm import narrate
        llm_text = await narrate(state["snap"], reports) if state.get("use_llm", True) else None
        if llm_text:
            text = llm_text
        else:
            status = "degraded"
    except Exception as e:  # noqa: BLE001
        status = "degraded"
        text += f"\n\n_(LLM narrative unavailable: {type(e).__name__})_"
    rep = AgentReport(agent="chief_analyst", status=status, summary=text.split("\n")[0], data={"narrative": text},
                      duration_ms=(time.perf_counter() - t0) * 1000, data_as_of=state["snap"].get("as_of"))
    return {"reports": {"chief_analyst": rep.model_dump()}, "narrative": text}


PARALLEL = [market_data, technical, smc, elliott, derivatives, ml_forecast, news, regime]
SECOND = [strategy, risk, validation, counter_argument, chart_annotation, portfolio, research]


def build_graph():
    g = StateGraph(State)
    for fn in PARALLEL + SECOND:
        g.add_node(fn.__name__, fn)
    g.add_node("chief_analyst", chief)
    g.add_node("join", lambda s: {})
    for fn in PARALLEL:
        g.add_edge(START, fn.__name__)
        g.add_edge(fn.__name__, "join")
    for fn in SECOND:
        g.add_edge("join", fn.__name__)
        g.add_edge(fn.__name__, "chief_analyst")
    g.add_edge("chief_analyst", END)
    return g.compile()


_graph = None


async def run_agents(snap: dict, portfolio: dict | None = None, perf: dict | None = None, use_llm: bool = True) -> dict:
    global _graph
    if _graph is None:
        _graph = build_graph()
    t0 = time.perf_counter()
    out = await _graph.ainvoke({"snap": snap, "portfolio": portfolio, "perf": perf, "reports": {}, "narrative": None,
                                "use_llm": use_llm})
    return {"reports": out["reports"], "narrative": out["narrative"], "duration_ms": (time.perf_counter() - t0) * 1000,
            "snapshot_id": snap.get("snapshot_id"), "as_of": snap.get("as_of"),
            "agents": AGENTS}
