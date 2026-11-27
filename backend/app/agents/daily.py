"""Today's 5 trades: once a day, the best five setups across the market, each validated and sized from the trader's
budget — and the sandbox that trades every pick on paper so the method's live record can be compared with its backtest.

How a day's picks are made (code only; the AI desk reviews them when a key is set):
  1. The pool: the shortlist (app/agents/scout.py) — one price call screens every USDT coin, the most liquid and active
     are analysed in depth. Only coins with a real engine setup on the 4h chart go on.
  2. The free readiness gate (app/agents/readiness.py) scores each; a hard block (stale data, a side against both
     timeframes, a suspended strategy) removes it.
  3. The selector ranks what is left. It is the same function the historical simulation and the evolution loop use
     (app/research/selector.py), fed the same setup features (app/ml/meta_label.py), so what is traded live is exactly
     what was tested. Ready setups come before forming ones.
  4. Each pick gets the measured plan (stop beyond structure, target at the first level beyond 2R after costs) and the
     validators' trade, market and execution checks; a critical failure drops it and the next one moves up.
  5. A pick the order book cannot absorb (capacity under $25,000) is dropped: thin coins move against a crowd.
  6. Its historical odds (past cases of that strategy, side and timeframe from the backtests) and its capacity in the
     order book are attached. Size, margin, leverage and the dollars at the stop and target come from the plan maths
     for the trader's budget, at the risk style the evidence supports.

The sandbox (backtest-to-execution): a ready pick ("Take now") enters the paper book at the price it was published; a
forming one ("Wait for trigger") waits as pending and enters only when the readiness gate turns ready — at that
moment's price, with freshly measured levels and checks — or lapses after 48 hours without a trade. Open picks are
graded on the closed candles that follow (target first, stop first, or expiry after 42 candles) and compounded on a
paper account at the pick's risk: the live record, side by side with the backtest's expectation. A trigger, a target
and a stop each raise an alert.
"""
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.agents import journal
from app.logging import log
from app.risk import plan as tp

TF = "4h"
PENDING_HOURS = 48  # a "wait for trigger" pick that does not trigger within this long lapses without a trade
K = 5
POOL = 24
DIR = Path("data/daily")
PICKS = DIR / "picks.jsonl"
_today: dict = {"day": None, "data": None}
_lock = asyncio.Lock()
STYLE_RISK = {"conservative": 1.0, "growth": 2.0, "aggressive": 3.0}
MIN_CAPACITY_USD = 25_000  # a pick the order book cannot absorb (a quarter of the size within 10 bps) is not published


def _day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class _Ctx:
    """What the validators read, for one coin (a light stand-in for the desk's session context)."""

    def __init__(self, tf: str, walks: dict, price: float):
        self.walks, self.price = walks, price
        self.tf = tf
        self.fund = self.depth = self.schools = self.evidence = None

    @property
    def snap(self) -> dict:
        return self.walks[self.tf][1]


def _style(r: dict) -> str:
    """The risk style the evidence supports (the Risk Officer's rule, in code): growth only for a ready setup that
    scores 85 or more with the higher timeframe agreeing; conservative otherwise. Aggressive is never chosen by code."""
    htf = next((p for p in r.get("parts", []) if p["name"] == "Higher timeframe"), {})
    if r["verdict"] == "ready" and r["score"] >= 85 and htf.get("points", 0) >= 15:
        return "growth"
    return "conservative"


async def _candidate(symbol: str, tf: str) -> dict | None:
    """One coin's best setup with everything the ranking, the plan and the validators need, or None."""
    from app.agents import readiness
    from app.api.chat import _walk
    from app.data.hub import now_ms
    from app.data.store import closed_only, load_candles
    from app.db import session_factory

    r = await readiness.check(symbol, tf)
    if r["blocks"] or not r["side"]:
        return None
    walk, snap = await _walk(symbol, tf)
    side, cand = readiness._side_of(walk, snap, r["side"])
    if not cand:
        return None
    walks = {tf: (walk, snap)}
    htf = readiness.HIGHER.get(tf, tf)
    if htf != tf:
        try:
            walks[htf] = await _walk(symbol, htf)
        except Exception:  # noqa: BLE001
            pass
    sel, feats = None, None
    try:
        from app.ml.meta_label import live_setup_features

        async with session_factory()() as db:
            df = closed_only(await load_candles(db, symbol, tf, limit=600), tf, now_ms())
            btc = None if symbol == "BTCUSDT" else closed_only(await load_candles(db, "BTCUSDT", tf, limit=600), tf, now_ms())
        if len(df) >= 250:
            feats = live_setup_features(df, cand, snap, tf, btc=btc)
            from app.research.selector import promoted_params, score_setup

            pp = promoted_params(tf)
            if str(pp.get("source", "")).startswith("promoted"):  # never rank live with an untested selector
                sel = round(float(score_setup(feats, pp)), 4)
    except ImportError:
        pass  # the selector is not installed yet: the readiness score ranks alone
    except Exception as e:  # noqa: BLE001
        log.warning("daily_features_failed", symbol=symbol, error=str(e))
    return {"symbol": symbol, "tf": tf, "side": side, "price": float(snap["price"]), "readiness": r, "candidate": cand,
            "selector_score": sel, "walks": walks, "regime": (snap.get("regime") or {}).get("regime")}


async def _validate(c: dict, budget: float, style: str) -> tuple[dict, dict]:
    """The plan for one pick at the trader's budget, and the validators' report on it."""
    from app.agents import market_extras
    from app.agents import validators as V
    from app.agents.research_desk import filters_for

    snap = c["walks"][c["tf"]][1]
    lv = tp.levels({**snap, "price": c["price"]}, c["side"])
    fund = (snap.get("derivatives") or {}).get("funding_annualized_pct")
    plan = tp.build(c["symbol"], c["side"], c["price"], lv["default"]["stop"], lv["default"]["target"], budget, style,
                    "futures", await filters_for(c["symbol"]), fund)
    plan.update(risk_style=style, stop_why=lv["default"]["stop_why"], target_why=lv["default"]["target_why"])
    ctx = _Ctx(c["tf"], c["walks"], c["price"])
    if plan.get("notional_usd"):
        try:
            ctx.depth = await market_extras.depth_slippage(c["symbol"], float(plan["notional_usd"]))
        except Exception:  # noqa: BLE001
            pass
    s = {"tf": c["tf"], "mode": style, "budget": budget, "margin": None, "venue": "futures", "symbol": c["symbol"]}
    checks = V.data_quality(s, ctx) + V.trade_checks(s, ctx, plan, c["side"])
    return plan, V.report(checks)


async def build(tf: str = TF, budget: float = 1000.0, k: int = K) -> dict:
    """Make today's picks (code only, a few seconds per coin)."""
    from app.agents import market_extras
    from app.agents.scout import wait_scan
    from app.quant.odds import setup_odds

    t0 = time.time()
    st = await wait_scan(tf)
    rows = sorted(st.get("rows") or [], key=lambda r: -r["score"])[:POOL]
    sem = asyncio.Semaphore(4)

    async def one(r: dict) -> dict | None:
        async with sem:
            try:
                return await _candidate(r["symbol"], tf)
            except Exception as e:  # noqa: BLE001 - one coin failing must not stop the day
                log.warning("daily_candidate_failed", symbol=r["symbol"], error=str(e))
                return None

    cands = [c for c in await asyncio.gather(*(one(r) for r in rows)) if c]
    # the final signal: every component fused by the evidence it has earned (app/signal/fusion.py)
    from app.signal import fusion

    async def sig(c: dict) -> dict | None:
        async with sem:
            try:
                return await fusion.signal(c["symbol"], tf, c["readiness"], c["candidate"])
            except Exception as e:  # noqa: BLE001
                log.warning("daily_signal_failed", symbol=c["symbol"], error=str(e))
                return None

    for c, sg in zip(cands, await asyncio.gather(*(sig(c) for c in cands))):
        c["signal"] = sg or {}
    picks, dropped = [], []
    for c in [c for c in cands if c["signal"].get("decision") == "veto"]:
        dropped.append({"symbol": c["symbol"], "why": c["signal"].get("decision_why", "vetoed")})
    cands = [c for c in cands if c["signal"].get("decision") != "veto"]
    order = {"ready": 0, "forming": 1, "not_now": 2}
    dec = {"take": 0, "wait": 1}
    cands.sort(key=lambda c: (dec.get(c["signal"].get("decision"), 2), order[c["readiness"]["verdict"]],
                              -(c["signal"].get("alignment") or 0), -(c["selector_score"] or 0), -c["readiness"]["score"]))
    for c in cands:
        if len(picks) >= k:
            break
        style = _style(c["readiness"])
        plan, rep = await _validate(c, budget, style)
        if rep["critical"] or not plan.get("ok"):
            dropped.append({"symbol": c["symbol"], "why": ", ".join(rep["critical"]) or plan.get("error") or "plan failed"})
            continue
        cap = await market_extras.signal_capacity(c["symbol"], c["side"])
        if (cap.get("capacity_usd") or 0) < MIN_CAPACITY_USD:
            dropped.append({"symbol": c["symbol"], "why": f"too thin: the book takes about ${cap.get('capacity_usd') or 0:,.0f} "
                                                          f"within 10 bps (minimum ${MIN_CAPACITY_USD:,})"})
            continue
        cand = c["candidate"]
        odds = None
        try:
            odds = await setup_odds(cand["strategy"], c["side"], tf, c["regime"])
        except Exception:  # noqa: BLE001
            pass
        picks.append({
            "rank": len(picks) + 1, "symbol": c["symbol"], "tf": tf, "side": c["side"], "price": c["price"],
            "strategy": cand["strategy"], "stage": cand.get("stage"), "regime": c["regime"],
            "readiness": {k_: c["readiness"][k_] for k_ in ("verdict", "score", "summary", "wait_for")},
            "selector_score": c["selector_score"], "style": style,
            "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"], "rr": plan.get("rr_net"),
            "stop_why": plan["stop_why"], "target_why": plan["target_why"],
            "validation": {"score": rep["score"], "evidence_score": rep["evidence_score"], "gate": rep["gate"],
                           "passed": sum(ch["status"] == "pass" for ch in rep["checks"]), "total": len(rep["checks"]),
                           "warnings": [f"{ch['name']}: {ch['detail']}" for ch in rep["checks"] if ch["status"] == "warn"][:6]},
            "odds": odds, "capacity_usd": cap.get("capacity_usd"),
            "signal": {k: c["signal"].get(k) for k in ("score", "direction", "strength", "decision", "decision_why", "coverage",
                                                         "agreement", "alignment")}
                      | {"contributions": (c["signal"].get("contributions") or [])[:8]},
        })
    out = {"day": _day(), "tf": tf, "made_at": int(time.time() * 1000), "seconds": round(time.time() - t0, 1),
           "pool": len(rows), "with_setup": len(cands), "picks": picks, "dropped": dropped[:8],
           "selector": _selector_source(tf), "ai": None}
    _record(out)
    return out


def _selector_source(tf: str) -> str:
    try:
        from app.research.selector import promoted_params

        src = str(promoted_params(tf).get("source", "default"))
    except ImportError:
        return "readiness score (the selector is not installed)"
    if src.startswith("promoted"):
        return f"the evolved selector ({src.split(':', 1)[-1]}), validated out of sample"
    return ("readiness score — no selector has passed the out-of-sample test yet (the default one lost to random picks "
            "in the backtest), so none ranks live picks")


def _record(day: dict) -> None:
    """Keep the day's picks and add new ones to the sandbox's book (graded later on the candles)."""
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{day['day']}_{day['tf']}.json").write_text(json.dumps(day, default=str))
    # picks made again the same day replace that day's still-PENDING ones (never taken); a pick already published as
    # "take now" (open) stays in the book — it was a trade, and the record must keep it
    now = {p["symbol"] for p in day["picks"]}
    keep = [r for r in rows() if not (r["day"] == day["day"] and r["tf"] == day["tf"] and r["status"] == "pending"
                                      and r["symbol"] not in now)]
    PICKS.write_text("".join(json.dumps(r) + "\n" for r in keep))
    have = {(r["day"], r["symbol"], r["tf"]) for r in keep}
    with PICKS.open("a") as f:
        for p in day["picks"]:
            if (day["day"], p["symbol"], p["tf"]) in have:
                continue
            f.write(json.dumps({"day": day["day"], "ts": day["made_at"], "symbol": p["symbol"], "tf": p["tf"], "side": p["side"],
                                "entry": p["entry"], "stop": p["stop"], "target": p["target"], "rr": p["rr"],
                                "style": p["style"], "strategy": p["strategy"], "rank": p["rank"],
                                "readiness": p["readiness"]["score"], "selector_score": p["selector_score"],
                                "status": "open" if (p.get("signal") or {}).get("decision", "take" if p["readiness"].get("verdict") == "ready" else "wait") == "take" else "pending",
                                "outcome": None, "r": None, "closed_ts": None}) + "\n")


def rows() -> list[dict]:
    if not PICKS.exists():
        return []
    out = []
    for line in PICKS.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


async def today(tf: str = TF, budget: float = 1000.0, refresh: bool = False) -> dict:
    """Today's picks (made once per UTC day, or again on refresh), with the plan sized for this budget."""
    async with _lock:
        if refresh or _today["day"] != _day() or not _today["data"] or _today["data"]["tf"] != tf:
            f = DIR / f"{_day()}_{tf}.json"
            if f.exists() and not refresh:
                _today.update(day=_day(), data=json.loads(f.read_text()))
            else:
                _today.update(day=_day(), data=await build(tf, budget))
        day = _today["data"]
    return {**day, "picks": [await _sized(p, budget) for p in day["picks"]], "budget": budget}


async def _sized(p: dict, budget: float) -> dict:
    """The pick's plan for this trader's budget: size, leverage, margin, liquidation and the dollars at each level."""
    from app.agents.research_desk import filters_for

    plan = tp.build(p["symbol"], p["side"], p["entry"], p["stop"], p["target"], budget, p["style"], "futures",
                    await filters_for(p["symbol"]))
    keep = ("ok", "error", "qty", "notional_usd", "leverage", "margin_usd", "liquidation", "risk_usd", "reward_usd",
            "risk_pct_of_budget", "stop_pct", "target_pct", "rr_net", "fees_usd", "orders", "warnings", "leverage_options")
    return {**p, "plan": {k: plan.get(k) for k in keep}}


def _save(rs: list[dict]) -> None:
    PICKS.write_text("".join(json.dumps(r) + "\n" for r in rs))


async def _alert(title: str, body: str, key: str, symbol: str, tf: str, category: str = "pre_signal") -> None:
    try:
        from app.db import session_factory
        from app.notify import notify

        async with session_factory()() as db:
            await notify(db, category, title, body, key, symbol=symbol, timeframe=tf)
            await db.commit()
    except Exception as e:  # noqa: BLE001 - an alert must never break the book
        log.warning("daily_alert_failed", error=str(e))


async def watch_triggers() -> int:
    """Pending picks: enter the ones whose setup turned ready (at the current price, with fresh levels and checks), lapse
    the ones older than PENDING_HOURS. Returns how many entered."""
    from app.agents import readiness

    rs = rows()
    pend = [r for r in rs if r["status"] == "pending"]
    if not pend:
        return 0
    n, changed = 0, False
    for r in pend:
        if time.time() * 1000 - r["ts"] > PENDING_HOURS * 3_600_000:
            r.update(status="lapsed", outcome="not triggered", closed_ts=int(time.time() * 1000))
            changed = True
            continue
        try:
            x = await readiness.check(r["symbol"], r["tf"], r["side"])
            if x["verdict"] != "ready" or x["side"] != r["side"]:
                continue
            c = await _candidate(r["symbol"], r["tf"])
            if not c or c["side"] != r["side"]:
                continue
            from app.signal import fusion

            sg = await fusion.signal(r["symbol"], r["tf"], c["readiness"], c["candidate"])
            if sg.get("decision") != "take":  # the trigger alone is not enough: the fused evidence must agree
                continue
            style = _style(c["readiness"])
            plan, rep_ = await _validate(c, 1000.0, style)
        except Exception as e:  # noqa: BLE001
            log.warning("daily_trigger_failed", symbol=r["symbol"], error=str(e))
            continue
        if rep_["critical"] or not plan.get("ok"):
            continue
        r.update(status="open", ts=int(time.time() * 1000), entry=plan["entry"], stop=plan["stop"], target=plan["target"],
                 rr=plan.get("rr_net"), style=style, triggered=True)
        n += 1
        changed = True
        await _alert(f"{r['symbol'].replace('USDT', '/USDT')}: take now — {r['side'].lower()} triggered",
                     f"Entry about {plan['entry']:.6g}, stop {plan['stop']:.6g}, target {plan['target']:.6g} "
                     f"({plan.get('rr_net')}R). Open Today for the size for your budget.",
                     f"trigger:{r['day']}:{r['symbol']}:{r['tf']}", r["symbol"], r["tf"])
    if changed:
        _save(rs)
    return n


async def grade() -> int:
    """Grade open picks on the closed candles after them (the journal's rule). Returns how many closed."""
    from app.data.hub import now_ms
    from app.data.store import closed_only, load_candles
    from app.db import session_factory

    rs = rows()
    todo = [r for r in rs if r["status"] == "open"]
    if not todo:
        return 0
    n = 0
    async with session_factory()() as db:
        for r in todo:
            try:
                df = closed_only(await load_candles(db, r["symbol"], r["tf"], limit=journal.MAX_BARS.get(r["tf"], 42) + 5,
                                                    start_ms=r["ts"] - journal.TF_MS.get(r["tf"], 0)), r["tf"], now_ms())
            except Exception as e:  # noqa: BLE001 - one coin's data must not stop the grading
                log.warning("daily_grade_failed", symbol=r["symbol"], error=str(e))
                continue
            bars = df[["open_time", "high", "low", "close"]].to_dict("records") if len(df) else []
            o = journal.outcome(r, bars)
            if r.get("r_15") is None:  # shadow forward test of H7 (docs/research): the same pick with a +1.5R target
                d = 1 if r["side"] == "LONG" else -1
                o15 = journal.outcome({**r, "target": r["entry"] + d * 1.5 * abs(r["entry"] - r["stop"]), "rr": 1.5}, bars)
                if o15:
                    r.update(r_15=o15["r"], outcome_15=o15["outcome"])
            if o:
                r.update(status="closed", **o)
                n += 1
                await _alert(f"{r['symbol'].replace('USDT', '/USDT')}: {'target hit' if o['outcome'] == 'target' else 'stopped out' if o['outcome'] == 'stop' else 'expired'} ({o['r']:+}R)",
                             f"Today's pick from {r['day']}: {r['side'].lower()} from {r['entry']:.6g}.",
                             f"close:{r['day']}:{r['symbol']}:{r['tf']}", r["symbol"], r["tf"],
                             "target_reached" if o["outcome"] == "target" else "stop_reached" if o["outcome"] == "stop" else "setup_invalidated")
    if n:
        _save(rs)
    return n


def _monthly(closed: list[dict]) -> list[dict]:
    """Closed picks by the month they closed: trades, wins and the sum of R."""
    out: dict[str, dict] = {}
    for r in closed:
        m = datetime.fromtimestamp((r["closed_ts"] or r["ts"]) / 1000, UTC).strftime("%Y-%m")
        x = out.setdefault(m, {"month": m, "trades": 0, "wins": 0, "r": 0.0})
        x["trades"] += 1
        x["wins"] += (r["r"] or 0) > 0
        x["r"] = round(x["r"] + (r["r"] or 0), 2)
    return sorted(out.values(), key=lambda x: x["month"])


def _shadow(rs: list[dict]) -> dict:
    """The forward test of hypothesis H7 (a +1.5R target instead of +2R) on the same picks, graded alongside the real
    exits. Shadow only: it never changes a live pick. Judge it on 200+ trades (docs/research/HYPOTHESES_2026-11-11.md)."""
    g = [r["r_15"] for r in rs if r.get("r_15") is not None]
    return {"trades": len(g), "win_rate": round(sum(x > 0 for x in g) / len(g), 3) if g else None,
            "avg_r": round(sum(g) / len(g), 3) if g else None,
            "note": "Forward test of a +1.5R target on the same picks (hypothesis H7); shadow only, judged after 200+ trades."}


def sandbox(start_equity: float = 10_000.0, recent: int = 15) -> dict:
    """The paper account that took every pick at its risk style (1% or 2% of equity at the stop), compounded in the
    order the trades closed — the live record of the method, next to its backtest."""
    rs = rows()
    closed = sorted([r for r in rs if r["status"] == "closed"], key=lambda r: r["closed_ts"] or 0)
    eq, peak, dd = start_equity, start_equity, 0.0
    curve = [{"t": closed[0]["ts"], "v": start_equity}] if closed else []
    for r in closed:
        eq *= 1 + (r["r"] or 0) * STYLE_RISK.get(r.get("style"), 1.0) / 100
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
        curve.append({"t": r["closed_ts"], "v": round(eq, 2)})
    rr = [r["r"] for r in closed if r["r"] is not None]
    out = {"start_equity": start_equity, "equity": round(eq, 2), "return_pct": round((eq / start_equity - 1) * 100, 2),
           "max_drawdown_pct": round(dd * 100, 2), "picks": len(rs), "open": sum(r["status"] == "open" for r in rs),
           "pending": sum(r["status"] == "pending" for r in rs), "lapsed": sum(r["status"] == "lapsed" for r in rs),
           "closed": len(closed), "win_rate": round(sum(x > 0 for x in rr) / len(rr), 3) if rr else None,
           "avg_r": round(sum(rr) / len(rr), 3) if rr else None,
           "target_first": sum(r["outcome"] == "target" for r in closed), "stop_first": sum(r["outcome"] == "stop" for r in closed),
           "expired": sum(r["outcome"] == "expired" for r in closed), "curve": curve,
           "recent": [{k: r.get(k) for k in ("day", "symbol", "side", "style", "entry", "stop", "target", "status", "outcome", "r",
                                             "triggered", "closed_ts")}
                      for r in sorted(rs, key=lambda r: r["ts"], reverse=True)[:recent]],
           "monthly": _monthly(closed),
           "shadow_15r": _shadow(rs)}
    try:
        from app.research.evolution import latest_report

        out["backtest"] = _backtest_summary(latest_report(TF))
    except Exception as e:  # noqa: BLE001 - the research module may not be installed or run yet
        log.warning("daily_backtest_failed", error=str(e))
        out["backtest"] = None
    out["note"] = ("No pick has closed yet: the record starts when the first stop or target is reached." if not closed else
                   f"{len(closed)} closed picks; judge the method on 50+." if len(closed) < 50 else f"{len(closed)} closed picks.")
    return out


def _book(x: dict | None) -> dict | None:
    if not x:
        return None
    return {"trades": x.get("n_trades"), "win_rate": x.get("win_rate"), "expectancy_r": x.get("expectancy_r"),
            "ci95": x.get("expectancy_ci95") or x.get("expectancy_r_p05_p95"), "total_r": x.get("total_r"),
            "cagr_pct": None if x.get("cagr") is None else round(x["cagr"] * 100, 1),
            "max_drawdown_pct": None if x.get("max_drawdown") is None else round(x["max_drawdown"] * 100, 1)}


def _curve(x: dict | None, step: int = 3) -> list[dict]:
    pts = (x or {}).get("equity_curve") or []
    out = []
    for i, p in enumerate(pts):
        if i % step == 0 or i == len(pts) - 1:
            out.append({"t": int(datetime.fromisoformat(p["date"]).replace(tzinfo=UTC).timestamp() * 1000), "v": p["equity"]})
    return out


def _backtest_summary(rep: dict) -> dict:
    """The historical simulation of the picking method, as the page shows it: the selected five against taking every
    setup and against five random ones, over the whole history and over the out-of-sample window."""
    last = rep.get("last_run") or {}
    books = {w: {k: _book((rep.get(w) or {}).get(k)) for k in ("selected", "take_all", "random_5")} for w in ("history", "oos")}
    h = rep.get("history") or {}
    sel, ta = h.get("selected") or {}, h.get("take_all") or {}
    return {"tf": rep.get("tf"), "params_source": rep.get("params_source"), "decision": last.get("decision"),
            "reason": last.get("reason"), "oos_from": (last.get("settings") or {}).get("cut"), "books": books,
            "period": [sel.get("start"), sel.get("end")], "curve_selected": _curve(sel), "curve_take_all": _curve(ta)}
