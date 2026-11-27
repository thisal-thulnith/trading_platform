"""Research desk API: start a research session on a coin, poll it, and get a fast agent-side plan for the add-trade form."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans
from app.agents import knowledge, research_desk
from app.auth.security import current_user_optional
from app.config import get_settings
from app.db import get_session
from app.risk.portfolio import RISK_MODES

router = APIRouter(prefix="/api/agent")


class ResearchIn(BaseModel):
    symbol: str  # the coin the user picked; the desk never picks one by itself
    tf: str = "4h"
    side: str = "auto"         # auto | long | short
    budget: float = 100.0
    mode: str = "auto"           # auto: the Risk Officer chooses the risk style from the evidence
    venue: str = "futures"
    refresh: bool = False
    margin: float | None = None  # the margin the trader puts on this trade; the agents size everything from it
    force: bool = False          # run the AI even when the readiness gate says the setup is not ready


class PlanIn(BaseModel):
    symbol: str
    side: str = "auto"
    tf: str = "4h"
    budget: float = 100.0
    mode: str = "growth"
    venue: str = "futures"
    entry: float | None = None
    margin: float | None = None
    stop: float | None = None
    target: float | None = None


def _check(tf: str, side: str, budget: float, mode: str, venue: str) -> None:
    if tf not in research_desk.TFS:
        raise HTTPException(400, f"tf must be one of {research_desk.TFS}")
    if side.upper() not in ("AUTO", "LONG", "SHORT"):
        raise HTTPException(400, "side must be auto, long or short")
    if mode not in RISK_MODES and mode != "auto":
        raise HTTPException(400, "mode must be auto, conservative, growth or aggressive")
    if venue not in ("spot", "futures"):
        raise HTTPException(400, "venue must be spot or futures")
    if not 10 <= budget <= 1_000_000:
        raise HTTPException(400, "budget must be 10..1,000,000")


@router.post("/research")
async def research(body: ResearchIn, user=Depends(current_user_optional), db: AsyncSession = Depends(get_session)):
    _check(body.tf, body.side, body.budget, body.mode, body.venue)
    sym = research_desk._sym(body.symbol)
    if body.margin is not None and not 1 <= body.margin <= 1_000_000:
        raise HTTPException(400, "margin must be 1..1,000,000 USDT")
    p = plans.plan_for(user)
    enforced = get_settings().plans_enforced
    if enforced and body.tf not in p.timeframes:
        raise HTTPException(403, f"The {p.name} plan covers {', '.join(p.timeframes)}; {body.tf} needs Pro or Desk.")
    key = plans.usage_key(user)
    n = await plans.used(db, key, "ai_desk")
    quota = {"key": key, "plan": p.name, "limit": p.ai_analyses_per_day, "left": max(0, p.ai_analyses_per_day - n),
             "head_votes": p.head_votes, "enforced": enforced}
    return research_desk.start(sym, body.tf, body.side, body.budget, body.mode, body.venue, body.refresh, body.margin,
                               body.force, quota)


@router.get("/readiness")
async def readiness_get(symbol: str, tf: str = "4h", side: str = "auto", venue: str = "futures"):
    """Is there a trade-quality setup on this chart right now? Code only (no AI, no cost): a 0-100 score with its
    parts, any hard block, and what to wait for. The desk runs the AI only when this says ready."""
    from app.agents import readiness

    if tf not in research_desk.TFS:
        raise HTTPException(400, f"tf must be one of {research_desk.TFS}")
    sd = side.upper()
    return await readiness.check(research_desk._sym(symbol), tf, sd if sd in ("LONG", "SHORT") else None, venue)


@router.get("/research/{sid}")
async def research_get(sid: str):
    v = research_desk.session_view(sid)
    if v is None:
        raise HTTPException(404, "session not found (sessions expire after a restart)")
    return v


@router.post("/plan")
async def plan(body: PlanIn):
    _check(body.tf, body.side, body.budget, body.mode, body.venue)
    if body.entry is not None and body.entry <= 0:
        raise HTTPException(400, "entry must be positive")
    try:
        return await research_desk.quick_plan(body.symbol, body.side, body.tf, body.budget, body.mode, body.venue,
                                              body.entry, body.stop, body.target, body.margin)
    except HTTPException:
        raise
    except research_desk.AIUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"could not analyse {body.symbol}: {e}") from e


@router.get("/status")
async def ai_status():
    """Is the AI available right now (key set, last call not failing)? The desk shows this before anyone presses Analyse."""
    from app.agents.llm import probe

    st = await probe()
    return {**st, "ready": bool(st.get("configured")) and not st.get("error")}


@router.get("/knowledge")
async def knowledge_search(q: str | None = None, k: int = 4):
    return knowledge.search(q, k) if q else knowledge.topics()


@router.get("/journal")
async def journal_stats(symbol: str | None = None, tf: str | None = None):
    """The desk's graded record (taken calls and waited counterfactuals)."""
    from app.agents import journal

    return journal.stats(research_desk._sym(symbol) if symbol else None, tf)


def _worth(r: dict, q: dict | None) -> str:
    """Why a coin is worth the agents' time, in one line."""
    sig, best = r["signal"], r.get("best") or {}
    if sig["action"] in ("BUY", "SELL"):
        why = f"live {sig['action']} setup: {sig.get('strategy') or best.get('strategy', '').replace('_', ' ')}"
    elif best:
        why = f"{best['direction'].lower()} setup forming ({best['strategy'].replace('_', ' ')}, {best['stage'].replace('_', ' ')})"
    else:
        why = f"no setup yet; read {r['direction']}"
    if r.get("agree"):
        why += " · methods agree"
    if q:
        why += f" · trend rank {q['rank']}/{q['of']}"
    if r.get("regime") in ("liquidity_stress", "news_instability"):
        why += f" · ⚠ {r['regime'].replace('_', ' ')}"
    return why


@router.get("/shortlist")
async def shortlist(tf: str = "4h", n: int = 15):
    """Coins worth analysing, chosen by code only (no AI): one price call screens every USDT coin, the streamed
    universe plus the most liquid and active ones are analysed in depth, and the best are listed with the reason. The
    AI agents run only when the user picks a coin and presses Analyse."""
    from app.agents.scout import scan_state
    from app.quant import brain

    if tf not in research_desk.TFS:
        raise HTTPException(400, f"tf must be one of {research_desk.TFS}")
    st = scan_state(tf)
    q = {r["symbol"]: r for r in (brain.peek() or {}).get("ranks", [])}
    rows = sorted(st.get("rows") or [], key=lambda r: (r["signal"]["action"] == "WAIT", not r.get("best"), -r["score"]))
    out = [{"symbol": r["symbol"], "price": r["price"], "score": r["score"], "action": r["signal"]["action"],
            "direction": r["direction"], "regime": r["regime"], "agree": r.get("agree"), "why": _worth(r, q.get(r["symbol"])),
            "reasons": r["reasons"]} for r in rows[: max(1, min(n, 30))]]
    deep = len(st.get("rows") or [])
    building = st["status"] == "running" and not deep
    return {"status": st["status"], "done": st.get("done"), "total": st.get("total"), "timeframe": tf, "rows": out,
            "note": ("Building the shortlist with code only (no AI)…" if building else
                     f"Screened {st.get('all_coins') or st.get('all') or 'all'} USDT coins with one price call and analysed "
                     f"the {deep} most liquid and active in depth with code only (no AI). Pick a coin, enter your margin "
                     "and press Analyse — only then do the AI agents run.")}


_DASH: dict[str, tuple[float, dict]] = {}


async def _series() -> dict:
    """Chart series for the dashboard (cached with the sources): BTC daily closes (90 days), fear & greed (30 days) and
    the total stablecoin supply (90 days)."""
    import httpx

    from app.agents import fundamentals as F

    async def btc():
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.binance.com/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1d", "limit": 90})
            r.raise_for_status()
            return [{"t": int(k[0]), "v": float(k[4])} for k in r.json()]

    async def fng():
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.alternative.me/fng/", params={"limit": 30})
            r.raise_for_status()
            return [{"t": int(x["timestamp"]) * 1000, "v": int(x["value"])} for x in reversed(r.json()["data"])]

    async def stables():
        st = await F._cached("llama_stables", 6 * 3600, lambda: F._get(f"{F.STABLES}/stablecoincharts/all"))
        return [{"t": int(x["date"]) * 1000, "v": float((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0)}
                for x in st[-90:]]

    out = {}
    for k, fn, ttl in (("btc", btc, 900), ("fear_greed", fng, 3600), ("stablecoins", stables, 6 * 3600)):
        try:
            out[k] = await F._cached(f"dash_{k}", ttl, fn)
        except Exception:  # noqa: BLE001 - a missing chart is left empty
            out[k] = []
    return out


async def _spark(symbol: str, tf: str, n: int = 48) -> list[float]:
    from app.data.hub import now_ms
    from app.data.store import closed_only, load_candles
    from app.db import session_factory

    try:
        async with session_factory()() as db:
            df = closed_only(await load_candles(db, symbol, tf, limit=n + 2), tf, now_ms())
        return [round(float(x), 8) for x in df["close"].tail(n)]
    except Exception:  # noqa: BLE001
        return []


_HIST: dict[str, list[tuple[float, list[dict]]]] = {}  # past boards' coins, for the delayed view of a plan


@router.get("/dashboard")
async def dashboard(tf: str = "4h", n: int = 12, user=Depends(current_user_optional)):
    """The dashboard for the caller's plan: the board (below), with the coins a plan does not include locked and a
    plan's delivery delay applied — a delayed plan sees the board as it was `delay_min` minutes ago, so the same
    signal never reaches every user at the same moment (docs/PLATFORM_PLAN.md)."""
    import time

    p = plans.plan_for(user)
    enforced = get_settings().plans_enforced
    if enforced and tf not in p.timeframes:
        raise HTTPException(403, f"The {p.name} plan covers {', '.join(p.timeframes)}; {tf} needs Pro or Desk.")
    board = await _board(tf, n)
    coins = board["coins"]
    note = None
    if enforced and p.delay_min:
        cut = time.time() - p.delay_min * 60
        past = [c for t, c in _HIST.get(tf, []) if t <= cut]
        if past:
            coins = past[-1]
            note = f"Signals as of {p.delay_min} minutes ago on the {p.name} plan."
        else:
            coins = [{**c, "locked": True} for c in coins]
            note = f"New signals appear {p.delay_min} minutes after the first delivery on the {p.name} plan."
    if enforced and p.signals_visible is not None:
        coins = [c if i < p.signals_visible and not c.get("locked") else
                 {"locked": True, "verdict": c["verdict"], "score": c["score"], "side": None, "symbol": "",
                  "price": 0, "spark": [], "summary": "", "wait_for": [], "blocks": []}
                 for i, c in enumerate(coins)]
    return {**board, "coins": coins, "plan": {"key": p.key, "name": p.name, "delay_min": p.delay_min,
                                               "signals_visible": p.signals_visible, "note": note}}


async def _board(tf: str, n: int = 12) -> dict:
    """Everything the dashboard shows, in one call and with no AI: the whole market (regime, breadth, fear & greed,
    dominance, stablecoin supply, options), the shortlist's coins scored by the readiness gate (ready first) with the
    capacity of each signal, and the desk's graded record. Cached two minutes per timeframe."""
    import asyncio
    import time

    from app.agents import calibration, fundamentals, journal, market_extras, readiness
    from app.agents.scout import scan_state
    from app.news.feeds import fear_greed
    from app.quant import brain

    if tf not in research_desk.TFS:
        raise HTTPException(400, f"tf must be one of {research_desk.TFS}")
    hit = _DASH.get(tf)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    st = scan_state(tf)
    rows = sorted(st.get("rows") or [], key=lambda r: -r["score"])[: max(1, min(n, 20))]
    sem = asyncio.Semaphore(4)

    async def one(r: dict) -> dict | None:
        async with sem:
            try:
                x = await readiness.check(r["symbol"], tf)
            except Exception:  # noqa: BLE001 - a coin that fails is left out
                return None
        cap = await market_extras.signal_capacity(r["symbol"], x["side"]) if x["side"] else {}
        return {"symbol": r["symbol"], "price": r["price"], "verdict": x["verdict"], "score": x["score"], "side": x["side"],
                "spark": await _spark(r["symbol"], tf), "capacity_usd": cap.get("capacity_usd"),
                "summary": x["summary"], "wait_for": x["wait_for"][:2], "blocks": x["blocks"][:1],
                "parts": [{k: p[k] for k in ("name", "points", "of")} for p in x["parts"]]}

    async def safe(coro, default=None):
        try:
            return await coro
        except Exception:  # noqa: BLE001 - every panel is optional
            return default

    ready, macro, fg, opts, q, series, mk = await asyncio.gather(
        asyncio.gather(*(one(r) for r in rows)), safe(fundamentals.macro(), {}), safe(fear_greed()),
        safe(market_extras.options_market(), {}), safe(brain.get(), None), safe(_series(), {}),
        safe(fundamentals.markets(), []))
    logos: dict[str, dict] = {}
    for m in sorted(mk or [], key=lambda m: -(m.get("market_cap") or 0)):  # tickers repeat: keep the biggest coin
        logos.setdefault((m.get("symbol") or "").upper(), m)
    for c in ready:
        if c:
            m = logos.get(fundamentals.base_of(c["symbol"])) or {}
            c.update(logo=m.get("image"), name=m.get("name"), change_24h_pct=m.get("price_change_percentage_24h"))
    order = {"ready": 0, "forming": 1, "not_now": 2}
    coins = sorted([x for x in ready if x], key=lambda x: (order[x["verdict"]], -x["score"]))
    reg = (q or {}).get("regime") or {}
    btc_opts = (opts or {}).get("btc") or {}
    rec = journal.stats()
    rel = calibration.reliability()
    out = {
        "tf": tf, "as_of": int(time.time() * 1000), "scan": {"status": st["status"], "done": st.get("done"), "total": st.get("total")},
        "market": {"regime": reg.get("label"), "plain": reg.get("plain"), "btc_trend": reg.get("btc_trend"),
                   "btc_price": reg.get("btc_price"), "btc_30d_pct": reg.get("btc_30d_pct"), "breadth_pct": reg.get("breadth_pct"),
                   "vol_state": reg.get("vol_state"), "fear_greed": fg,
                   "btc_dominance_pct": macro.get("btc_dominance_pct"), "total_market_cap_usd": macro.get("total_market_cap_usd"),
                   "total_market_cap_change_24h_pct": macro.get("total_market_cap_change_24h_pct"),
                   "stablecoin_supply_usd": macro.get("stablecoin_supply_usd"),
                   "stablecoin_supply_change_30d_pct": macro.get("stablecoin_supply_change_30d_pct"),
                   "top100_up_30d_pct": macro.get("top100_up_30d_pct"),
                   "top100_beating_btc_30d_pct": macro.get("top100_beating_btc_30d_pct"),
                   "btc_dvol": btc_opts.get("dvol"), "btc_put_call": btc_opts.get("put_call_oi_ratio")},
        "coins": coins, "series": series,
        "counts": {k: sum(x["verdict"] == k for x in coins) for k in order},
        "record": {"all": rec["all"], "taken": rec["taken"], "recent": rec["recent"], "note": rec["note"],
                   "brier": rel.get("brier"), "calibration_note": rel.get("note")},
    }
    if coins:  # an empty board (the shortlist still building) is not cached
        _DASH[tf] = (time.time(), out)
        h = _HIST.setdefault(tf, [])
        h.append((time.time(), coins))
        del h[:-40]
    return out


@router.get("/daily")
async def daily_picks(tf: str = "4h", budget: float = 1000.0, refresh: bool = False):
    """Today's 5 trades (app/agents/daily.py, a lab system): chosen and validated by code once a UTC day, each sized for
    this budget — margin, leverage, quantity, and the dollars at the stop and the target. The AI desk is never started
    from here: AI runs only when the user presses a button (it spends the user's credits)."""
    from app.agents import daily

    if tf not in research_desk.TFS:
        raise HTTPException(400, f"tf must be one of {research_desk.TFS}")
    if not 10 <= budget <= 1_000_000:
        raise HTTPException(400, "budget must be 10..1,000,000")
    from app.ml import live_status

    d = await daily.today(tf, budget, refresh)
    # each setup checked against the 1h candles since it was published: no "take now" for a trade that is already over
    made_s = int(d.get("made_at", 0) / 1000)
    # the main signal for the same coin, so a setup that fights the platform's own signal says so
    from app.ml import unified as un

    ranks = {r["symbol"]: r for r in un.ranked()}
    picks_today = {x["symbol"] for x in (un.candidates().get("picks") or [])}
    for p in d.get("picks", []):
        r = ranks.get(p["symbol"])
        if r is None:
            p["main"] = {"covered": False}
        else:
            clear = bool(r["pred_r"] > 0 or p["symbol"] in picks_today)
            side = r["plan"].split("_")[0]
            p["main"] = {"covered": True, "clear": clear, "side": side if clear else None, "rank": r["rank"],
                         "agrees": clear and side == p["side"]}
    for p in d.get("picks", []):
        try:
            p["live_state"] = live_status.judge(p["side"], float(p["entry"]), float(p["stop"]), float(p["target"]),
                                                await live_status.bars_1h(p["symbol"]), made_s)
        except Exception:  # noqa: BLE001 - the check is advisory
            p["live_state"] = None
    return d


@router.get("/sandbox")
async def sandbox_record(start_equity: float = 10_000.0, recent: int = 15):
    """The paper account that takes every daily pick (backtest-to-execution sandbox): equity curve, win rate, average R
    and drawdown from the picks graded on the candles, with the method's backtest next to it."""
    from app.agents import daily

    return daily.sandbox(start_equity, max(1, min(recent, 1000)))


@router.get("/market")
async def market_overview():
    """The whole market at a glance (no coins, no AI): regime, fear & greed, dominance and total cap, stablecoin supply,
    breadth, BTC options, and the chart series. Cached by its sources."""
    import asyncio

    from app.agents import fundamentals, market_extras
    from app.news.feeds import fear_greed
    from app.quant import brain

    async def safe(coro, default=None):
        try:
            return await coro
        except Exception:  # noqa: BLE001 - every panel is optional
            return default

    macro, fg, opts, q, series = await asyncio.gather(
        safe(fundamentals.macro(), {}), safe(fear_greed()), safe(market_extras.options_market(), {}),
        safe(brain.get(), None), safe(_series(), {}))
    reg = (q or {}).get("regime") or {}
    btc_opts = (opts or {}).get("btc") or {}
    return {"regime": reg.get("label"), "btc_trend": reg.get("btc_trend"), "breadth_pct": reg.get("breadth_pct"),
            "vol_state": reg.get("vol_state"), "fear_greed": fg, "btc_dominance_pct": macro.get("btc_dominance_pct"),
            "total_market_cap_usd": macro.get("total_market_cap_usd"),
            "total_market_cap_change_24h_pct": macro.get("total_market_cap_change_24h_pct"),
            "stablecoin_supply_usd": macro.get("stablecoin_supply_usd"),
            "stablecoin_supply_change_30d_pct": macro.get("stablecoin_supply_change_30d_pct"),
            "top100_up_30d_pct": macro.get("top100_up_30d_pct"), "btc_dvol": btc_opts.get("dvol"),
            "btc_put_call": btc_opts.get("put_call_oi_ratio"), "series": series}


@router.get("/screener")
async def screener():
    """Every liquid coin the quant brain ranks (~120), with the computer-side numbers side by side: trend score and rank
    among them, its quintile's measured chance of being up next week, 7d and 28d momentum, funding and basis with a
    crowding note, plus name, logo, price, 24h change and market cap from CoinGecko. Code only; refreshed every 30 min."""
    from app.agents import fundamentals
    from app.quant import brain

    q = await brain.get()
    if not q:
        raise HTTPException(503, "The quant brain is warming up — try again in a minute")
    try:
        mk = await fundamentals.markets()
    except Exception:  # noqa: BLE001 - names and logos are optional
        mk = []
    try:
        from app.agents.scout import all_coins

        tick = {c["symbol"]: c for c in await all_coins()}  # Binance's own price and 24h change for every pair
    except Exception:  # noqa: BLE001
        tick = {}
    by: dict[str, dict] = {}
    for m in sorted(mk, key=lambda m: -(m.get("market_cap") or 0)):
        by.setdefault((m.get("symbol") or "").upper(), m)
    rows = []
    for r in q["ranks"]:
        m = by.get(fundamentals.base_of(r["symbol"])) or {}
        d = r.get("derivs") or {}
        t = tick.get(r["symbol"]) or {}
        rows.append({"symbol": r["symbol"], "name": m.get("name"), "logo": m.get("image"),
                     "price": t.get("price") or m.get("current_price"),
                     "change_24h_pct": t.get("change_24h_pct") if t else m.get("price_change_percentage_24h"),
                     "volume_24h_usd": t.get("quote_volume_24h"), "market_cap": m.get("market_cap"),
                     "rank": r["rank"], "of": r["of"], "score": r["score"], "quintile": r.get("quintile"), "trend": r.get("trend"),
                     "p_up_week": r.get("p_up_week"), "p_n": r.get("p_n"), "mom_7d_pct": r.get("mom_7d_pct"),
                     "mom_28d_pct": r.get("mom_28d_pct"), "funding_ann_pct": d.get("funding_ann_pct"), "basis_pct": d.get("basis_pct"),
                     "funding_note": r.get("funding_note")})
    return {"computed_at": q.get("computed_at"), "regime": (q.get("regime") or {}).get("label"), "method": q.get("method"),
            "rows": rows}


@router.get("/signal")
async def final_signal(symbol: str, tf: str = "4h"):
    """The final signal for one coin (app/signal/fusion.py): every component — momentum, carry, regime, order flow,
    fundamentals, the chart setup, the AI desk and any promoted model — fused by the evidence each has earned, with the
    decision for its setup (take / wait / veto) and every component's score, weight and reason."""
    from app.signal import fusion

    if tf not in research_desk.TFS:
        raise HTTPException(400, f"tf must be one of {research_desk.TFS}")
    return await fusion.signal(research_desk._sym(symbol), tf)


@router.get("/evidence")
async def evidence_registry():
    """How much each signal component counts and why: the literature prior, what the platform measured (t-statistic,
    sample) and the resulting weight; models count only after passing their acceptance test."""
    from app.signal import evidence

    return evidence.registry(0)


@router.get("/machine")
async def machine_view(symbol: str, tf: str = "4h"):
    """The machine side for one coin, compact: the final fused signal; the quant lab's factors that rank coins (its
    value as a percentile of today's universe, the side its measured IC favours, and how strong that evidence is);
    order flow; funding across exchanges; the regime; and which models are accepted. Code only, no AI."""
    import asyncio

    from app.agents import market_extras
    from app.signal import evidence, fusion

    sym = research_desk._sym(symbol)
    sig, flow, fund = await asyncio.gather(fusion.signal(sym, tf), market_extras.order_flow(sym, tf),
                                           market_extras.funding_across(sym), return_exceptions=True)
    out: dict = {"symbol": sym, "tf": tf, "signal": sig if isinstance(sig, dict) else None}
    factors, lab_rank = [], None
    try:
        from app.quant.lab import latest_report, live_ranking

        lr, rep = fusion._lab_ranking(live_ranking), latest_report()
        meta = {f["name"]: f for f in rep.get("factors") or []}
        rows = lr.get("rows") or []
        me = next((r for r in rows if r["symbol"] == sym), None)
        if me:
            lab_rank = {"rank": me["rank"], "of": len(rows), "side": me.get("side"), "promoted": lr.get("promoted"),
                        "data_through": lr.get("data_through")}
            for name, val in (me.get("factors") or {}).items():
                m = meta.get(name) or {}
                if not m.get("passes") or val is None:
                    continue
                vals = sorted(v for v in ((r.get("factors") or {}).get(name) for r in rows) if v is not None)
                pct = round(100 * sum(v <= val for v in vals) / len(vals)) if vals else None
                ic = m.get("ic7") or 0
                favours = None if pct is None else ("long" if (pct >= 50) == (ic > 0) else "short")
                factors.append({"name": name, "doc": m.get("doc"), "value": val, "percentile": pct, "ic7": ic,
                                "t": m.get("t_ic7"), "favours": favours})
            factors.sort(key=lambda f: -abs(f["t"] or 0))
    except Exception as e:  # noqa: BLE001 - the lab may not be built
        out["lab_error"] = str(e)[:120]
    out["factors"], out["lab_rank"] = factors, lab_rank
    if isinstance(flow, dict):
        out["order_flow"] = {k: (flow.get("last_24") or {}).get(k) for k in ("cvd_pct_of_volume", "taker_buy_pct", "divergence")}
    if isinstance(fund, dict):
        out["funding"] = {k: fund.get(k) for k in ("oi_weighted_annualized_pct", "spread_pp", "most_crowded", "total_oi_usd")}
    reg = evidence.registry()
    out["models"] = {k: v.get("accepted") for k, v in reg["components"].items() if "accepted" in v}
    return out


@router.get("/quant_sandbox")
async def quant_sandbox_view():
    """The quant lab's portfolio paper-traded forward (decided at the close, filled at the next open, costs on turnover):
    equity curve, drawdown, Sharpe, current weights, the pending rebalance and the settings it follows."""
    from app.signal import quant_sandbox

    return {**quant_sandbox.summary("composite"), "books": quant_sandbox.books()}


@router.get("/lob/status")
async def lob_status():
    """The order-book and trade recorder: symbols, rows today, gaps, disk use against its cap, and whether it is on
    (AIT_LOB_RECORD in backend/.env)."""
    from app.config import get_settings
    from app.quant import lob

    return {"enabled": get_settings().lob_record, **lob.status()}


@router.get("/forecast")
async def forecast(symbol: str):
    """The trained forecast models for one coin (no AI key needed): each forecast with the model's measured
    out-of-sample accuracy and status."""
    from app.ml import forecast as fc

    return fc.for_symbol(research_desk._sym(symbol))


@router.get("/forecast/report")
async def forecast_report(tier: str = "core"):
    """How the forecast models were trained and how accurate each was on years it never saw."""
    from app.ml import forecast as fc

    if tier not in fc.TIERS:
        raise HTTPException(400, f"tier must be one of {list(fc.TIERS)}")
    return fc.report(tier) or {"available": False, "note": "not trained yet"}


@router.get("/patterns")
async def patterns(symbol: str):
    """Chart patterns learned from every coin's history: the closest past charts and what happened next, and the
    learned pattern this chart belongs to, each with its measured accuracy (no AI key needed)."""
    from app.ml import patterns as pt

    return pt.for_symbol(research_desk._sym(symbol))


@router.get("/patterns/library")
async def pattern_library():
    """The learned pattern library and how well patterns and analogs predicted years they never saw."""
    from app.ml import patterns as pt

    return pt.report() or {"available": False, "note": "not trained yet"}


async def _live(symbol: str) -> dict | None:
    """Current price and the last day / week range from the platform's live 1h candles."""
    from app.ml import live_status

    # the same 1h candles (and cache) the board and the live checks use: one price for one signal on every page
    c = await live_status.bars_1h(symbol)
    if not c:
        return None
    import time

    if time.time() - c[-1]["time"] > 2 * 3600:   # the newest 1h candle must be the current or the last hour
        return None
    d, wk = c[-24:], c[-168:]
    return {"price": c[-1]["close"], "time": c[-1]["time"], "high_24h": max(x["high"] for x in d),
            "low_24h": min(x["low"] for x in d), "high_7d": max(x["high"] for x in wk), "low_7d": min(x["low"] for x in wk),
            "change_24h_pct": round((c[-1]["close"] / d[0]["open"] - 1) * 100, 2)}


@router.get("/signal/today")
async def signal_today(margin: float = 100.0):
    """Today's signal (no AI key needed): the coin where the trained models and the learned chart patterns agree most,
    with the full plan for this margin, how the same daily pick did on years it never saw, and the runners-up."""
    from app.ml import signal_plan as sp
    from app.ml import unified as un

    if not 1 <= margin <= 1_000_000:
        raise HTTPException(400, "margin must be between 1 and 1,000,000 USDT")
    uc = un.candidates()
    if uc.get("available") and uc["top"]:
        top = (uc["picks"] or uc["top"])[0]
        from app.ml import live_status

        plan = await live_status.apply(un.plan_for(top["symbol"], None, margin, await _live(top["symbol"])))
        return {**uc, "pick_symbol": top["symbol"], "pick_side": plan.get("side"), "plan": plan,
                **await _three(plan, margin)}
    cand = sp.candidates()
    if not cand.get("available"):
        return {"available": False, "note": "The models or the signal backtest are not ready yet."}
    sym, side = cand["pick_symbol"], cand["pick_side"]
    plan = sp.plan_for(sym, side, margin, await _live(sym))
    return {**cand, "plan": plan}


def _final(plan: dict, v: dict | None) -> dict:
    """Machine and agents side by side: a plain reading, not a new model (the combination is not backtested; the
    signal test scores it forward)."""
    from app.ml import signal_test

    side, st = plan.get("side"), signal_test.stance(plan.get("side"), v)
    machine_on = plan.get("status") == "trade"
    if st == "none":
        return {"stance": st, "tone": "neutral", "label": "Agents not run",
                "text": f"Machine: {side} ({'trade' if machine_on else 'watch only'}). Run the AI agents for a second, "
                        "separate opinion."}
    if st == "agree":
        return {"stance": st, "tone": "good", "label": f"Both say {side}",
                "text": f"The machine and the AI agents both say {side}." + (
                    "" if machine_on else " The machine's own status is watch only.")}
    if st == "wait":
        return {"stance": st, "tone": "warn", "label": "Machine yes, agents wait",
                "text": f"The machine says {side}; the AI agents say wait"
                        + (f" until: {v['conditions'][0]}" if v.get("conditions") else "") + ". Smaller size, or wait."}
    return {"stance": st, "tone": "bad", "label": "They disagree",
            "text": f"The machine says {side}; the AI agents say {v['action']}. Conflicting signals: skip this one."}


async def _three(plan: dict, margin: float) -> dict:
    """The three separate parts for one coin: machine, simulation, agents, and the side-by-side reading."""
    from app.ml import signal_test
    from app.ml import simulate as sm

    out: dict = {"simulation": None, "agents": None, "final": None}
    if not plan.get("covered") or plan.get("engine") != "unified":
        return out
    sw = plan["plans"]["swing"]
    out["simulation"] = sm.simulate(plan["symbol"], plan["plan_id"], sw["leverage"]["stop_pct"], margin)
    v = signal_test.verdicts().get(plan["symbol"])
    if v:
        age_h = (datetime.now(UTC) - datetime.fromisoformat(v["at"])).total_seconds() / 3600
        v = {**v, "age_hours": round(age_h, 1), "stale": age_h > signal_test.AGENT_FRESH_H}
    out["agents"] = v
    out["final"] = _final(plan, v if v and not v["stale"] else None)
    return out


@router.get("/costs")
async def ai_costs(days: int = 14):
    """What the AI agents cost: tokens and an estimated $ per day, per feature and per desk run."""
    from app.agents import costs

    return costs.summary(max(1, min(days, 90)))


class CreditIn(BaseModel):
    amount: float | None = None   # the balance on the provider's billing page; empty clears it


@router.post("/costs/credit")
async def ai_credit(body: CreditIn):
    from app.agents import costs

    if body.amount is not None and not 0 <= body.amount <= 100_000:
        raise HTTPException(400, "amount must be 0..100,000 USD")
    costs.set_credit(body.amount)
    return costs.summary()


@router.get("/signal/history")
async def signal_history(date: str | None = None):
    """What the platform said on a past day (UTC), and how it turned out: the signals saved live that day, and the
    machine's out-of-sample ranking that day with each plan's real result."""
    from datetime import date as _date

    from app.ml import signal_history as sh

    try:
        on = _date.fromisoformat(date) if date else (datetime.now(UTC) - timedelta(days=1)).date()
    except ValueError as e:
        raise HTTPException(400, "date must be YYYY-MM-DD") from e
    return await sh.day(on)


@router.get("/signal/board")
async def signal_board():
    """Every coin the machine scores today, ranked, with live-price levels, the other methods' view, the agents' latest
    verdict and a confidence level from how signals ranked like it did on unseen years. No AI is run here."""
    from app.agents.scout import all_coins
    from app.ml import board

    try:
        prices = {c["symbol"]: c["price"] for c in await all_coins()}
    except Exception:  # noqa: BLE001 - fall back to the model's close
        prices = {}
    return await board.board(prices)


@router.get("/signal/plan")
async def signal_plan(symbol: str, margin: float = 100.0, side: str | None = None):
    """The same full plan for any coin the user picks."""
    from app.ml import signal_plan as sp

    if side is not None and side.upper() not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    if not 1 <= margin <= 1_000_000:
        raise HTTPException(400, "margin must be between 1 and 1,000,000 USDT")
    from app.ml import unified as un

    sym = research_desk._sym(symbol)
    live = await _live(sym)
    p = un.plan_for(sym, side.upper() if side else None, margin, live)
    # the unified engine covers liquid coins; thinner ones keep the fixed plan (wide-tier models)
    if not p.get("covered"):
        return sp.plan_for(sym, side.upper() if side else None, margin, live)
    from app.ml import live_status

    p = await live_status.apply(p)
    return {**p, **await _three(p, margin)}


@router.get("/signal/backtest")
async def signal_backtest():
    """How the daily pick did, trade by trade on the daily candles, on the years it never saw."""
    from app.ml import signal_plan as sp
    from app.ml import unified as un

    rep = un.report()
    return {"unified": {k: v for k, v in rep.items() if k != "last_picks"} if rep else None,
            "fixed_plan": sp.backtest_report()} if rep else (sp.backtest_report() or {"available": False})


class SignalTestIn(BaseModel):
    symbol: str
    side: str | None = None
    margin: float = 100.0


@router.get("/signal/test")
async def signal_test_report():
    """The forward test: every signal saved when it was given and graded on the live candles after it."""
    from app.ml import signal_test

    return await signal_test.report()


@router.post("/signal/test/refresh")
async def signal_test_refresh():
    """Save today's signals if not saved yet and grade the open ones now (paper only; nothing is sent to an exchange)."""
    from app.ml import signal_test

    saved = await signal_test.record_today()
    graded = await signal_test.update()
    return {"saved": saved, "graded": graded}


@router.post("/signal/test/add")
async def signal_test_add(body: SignalTestIn):
    """Add one coin's plan to the test, frozen at today's live price."""
    from app.ml import signal_test

    if body.side is not None and body.side.upper() not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    if not 1 <= body.margin <= 1_000_000:
        raise HTTPException(400, "margin must be between 1 and 1,000,000 USDT")
    res = await signal_test.add(research_desk._sym(body.symbol), body.side.upper() if body.side else None, body.margin)
    if not res["ok"]:
        raise HTTPException(400, res["note"])
    await signal_test.update()
    return res


@router.delete("/signal/test/{sid}")
async def signal_test_remove(sid: str):
    from app.ml import signal_test

    if not await signal_test.remove(sid):
        raise HTTPException(400, "Only signals you added can be removed; the platform's own stay in the record.")
    return {"ok": True}
