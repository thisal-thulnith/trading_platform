"""Readiness gate: is there a trade-quality setup on this chart right now? Code only, no AI, so it costs nothing.

The AI desk is the expensive part of the platform (about 30 model calls per analysis). Most of the time there is no
trade: the platform's own research says the default setups lose after costs unless the evidence lines up. So before
the agents run, this gate scores the chart from the same measurements the agents would read and answers one of:

  ready     a setup the agents should study now (score >= READY, the setup at least a pre-signal, no hard block) —
            the desk runs
  forming   something is building; the desk says what to wait for (the trigger price or event) and does not run
  not_now   no setup worth paying for; waiting is the position

Score (0-100), each part from measured data:
  engine setup     30  confirmed and all checks passed (15 for a pre-signal, 8 for a forming one)
  chart read       15  the timeframe's evidence agrees with the side (>= 60% agreement)
  higher timeframe 15  the higher timeframe agrees (against it is a hard block)
  backtest         10  the setup's strategy has a positive, not-suspended backtest on this timeframe
  room to target   10  the first opposing level is at least 1R away (a 2R target is reachable)
  regime           10  not liquidity stress, news instability or high volatility
  trend rank       10  the coin's rank among ~120 coins supports the side (top 40% for longs, bottom 40% for shorts)
Hard blocks: stale data, the side against both the chart and the higher timeframe, a suspended strategy.

`watch()` re-checks the shortlist every 15 minutes and raises an alert when a coin turns ready, so the trader is told
when the time comes instead of paying the AI to say WAIT.
"""
import asyncio
import time

from app.logging import log
from app.risk import plan as tp

READY, FORMING = 70, 45
HIGHER = {"15m": "4h", "1h": "4h", "4h": "1d", "1d": "1d"}
STRESS = ("liquidity_stress", "news_instability", "high_volatility")
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
STAGE_PTS = {"confirmed": 15, "pre_signal": 15, "forming": 8}
_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_S = 120


def _side_of(walk: dict, snap: dict, want: str | None) -> tuple[str | None, dict | None]:
    """The side to judge and the engine setup for it: the trader's side, else the best setup's, else the chart's read."""
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    cands = sorted([c for c in snap.get("candidates", []) if "error" not in c],
                   key=lambda c: (-stage_w.get(c.get("stage"), 0), -(c.get("validation") or {}).get("quality_score", 0)))
    if want in ("LONG", "SHORT"):
        return want, next((c for c in cands if c["direction"] == want), None)
    if cands:
        return cands[0]["direction"], cands[0]
    d = walk["synthesis"]["direction"]
    return ({"bullish": "LONG", "bearish": "SHORT"}.get(d), None)


def score(symbol: str, tf: str, walk: dict, snap: dict, htf_walk: dict | None, want: str | None = None,
          venue: str = "futures", ml: dict | None = None) -> dict:
    """Score one chart. Pure function of the analysis, so it is cheap and testable."""
    from app.engine import engine
    from app.quant import brain

    side, setup = _side_of(walk, snap, want)
    if venue == "spot" and side == "SHORT":
        side, setup = "LONG", next((c for c in snap.get("candidates", []) if "error" not in c and c["direction"] == "LONG"), None)
    price = float(snap["price"])
    parts, blocks, wait = [], [], []

    def part(name: str, pts: float, of: int, detail: str) -> None:
        parts.append({"name": name, "points": round(pts), "of": of, "detail": detail})

    age = time.time() * 1000 - (snap.get("as_of_close_time") or snap.get("as_of") or 0)
    if age > 2 * TF_MS.get(tf, 3_600_000):
        blocks.append(f"stale data: the last closed {tf} candle is {age / 60000:.0f} min old")
    if not side:
        return {"symbol": symbol, "tf": tf, "price": price, "side": None, "score": 0, "verdict": "not_now",
                "parts": [], "blocks": blocks, "wait_for": ["a setup or a clear directional read on this chart"],
                "summary": "No setup and no clear direction on this chart: nothing worth an AI analysis now."}
    want_word = "bullish" if side == "LONG" else "bearish"
    other = "bearish" if side == "LONG" else "bullish"

    # engine setup
    if setup:
        st = setup.get("stage")
        passed = bool((setup.get("validation") or {}).get("passed"))
        pts = 30 if st == "confirmed" and passed else STAGE_PTS.get(st, 0)
        part("Engine setup", pts, 30, f"{setup['strategy'].replace('_', ' ')} {side.lower()} — {st}"
             + (", all checks passed" if passed else ""))
        if pts < 30:
            failed = [c["name"].replace("_", " ") for c in (setup.get("validation") or {}).get("checks", []) if not c["passed"]]
            miss = [m.replace("_", " ") for m in setup.get("missing") or []]
            wait.append(f"the {setup['strategy'].replace('_', ' ')} setup to confirm"
                        + (f" ({', '.join((miss or failed)[:3])})" if miss or failed else ""))
    else:
        part("Engine setup", 0, 30, f"no engine setup for a {side.lower()}")
        wait.append(f"an engine setup for a {side.lower()}")

    # chart read
    syn = walk["synthesis"]
    agree = syn.get("agreement") or 0
    ok = syn["direction"] == want_word
    part("Chart read", 15 if ok and agree >= 0.6 else 8 if ok else 0, 15,
         f"{tf} reads {syn['direction']} ({agree:.0%} of the evidence agrees)")

    # higher timeframe
    htf = HIGHER.get(tf, tf)
    hd = (htf_walk or {}).get("synthesis", {}).get("direction") if htf_walk else None
    if hd is None and htf == tf:
        hd = syn["direction"]
    part("Higher timeframe", 15 if hd == want_word else 7 if hd in ("neutral", None) else 0, 15, f"{htf}: {hd or 'unknown'}")
    if hd == other:
        wait.append(f"the {htf} chart to turn {want_word} (it reads {other})")
        if syn["direction"] == other:
            blocks.append(f"both the {tf} and the {htf} read {other}: a {side.lower()} fights the whole picture")

    # backtest of the setup's strategy
    gate = engine.research_gate.get(f"{setup['strategy']}|{tf}") if setup else None
    if gate:
        exp = gate.get("expectancy_r") or 0
        if gate.get("status") == "suspended":
            blocks.append(f"{setup['strategy'].replace('_', ' ')} is suspended on {tf} (its backtest lost)")
        part("Backtest", 10 if exp > 0 and gate.get("status") != "suspended" else 0, 10,
             f"{setup['strategy'].replace('_', ' ')} on {tf}: {gate.get('status')}, {exp:+.2f}R per trade over {gate.get('n')} trades")
    else:
        part("Backtest", 3 if setup else 0, 10, "no backtest evidence for this setup on this timeframe")

    # room to the target
    lv = tp.levels({**snap, "price": price}, side)
    first = next((t for t in lv["target_options"] if t.get("r") is not None), None)
    room = first["r"] if first else None
    part("Room to target", 10 if room is None or room >= 1 else 5 if room >= 0.6 else 0, 10,
         f"first opposing level {first['price']:.6g} is {room}R away" if first else "no opposing level before 2R")
    if room is not None and room < 1:
        wait.append(f"a clean break of {first['price']:.6g} ({first['why']}), only {room}R away")

    # regime
    reg = (snap.get("regime") or {}).get("regime") or walk.get("regime")
    part("Regime", 0 if reg in STRESS else 10, 10, f"regime {reg}")
    if reg in STRESS:
        wait.append(f"the {str(reg).replace('_', ' ')} regime to calm down")

    # trend rank among ~120 coins
    q = brain.coin(symbol)
    if q:
        pct = 1 - (q["rank"] - 1) / max(q["of"] - 1, 1)  # 1 = strongest
        good = pct >= 0.6 if side == "LONG" else pct <= 0.4
        part("Trend rank", 10 if good else 4 if 0.4 < pct < 0.6 else 0, 10, f"rank {q['rank']} of {q['of']} ({q.get('trend')})")
    else:
        part("Trend rank", 4, 10, "BTC is the benchmark the others are ranked against" if symbol == "BTCUSDT"
             else "not in the ~120-coin ranking")

    # the ML filter (app/ml/meta_label.py) counts only when a model passed the acceptance policy; a rejected model
    # must not move a signal, so without one the score is unchanged and the reason is reported
    if ml and ml.get("accepted_model") and ml.get("p") is not None:
        part("ML filter", 10 if ml.get("take") else 0, 10,
             f"P(target before stop) {ml['p']:.0%} vs the {ml['threshold']:.0%} threshold")
        if not ml.get("take"):
            wait.append("the ML filter to rate this setup above its threshold")
    total = sum(p["points"] for p in parts)
    if ml and ml.get("accepted_model"):
        total = round(total * 100 / 110)  # keep the 0-100 scale with the extra part
    mature = bool(setup) and setup.get("stage") in ("confirmed", "pre_signal")  # a forming setup is never ready
    verdict = ("not_now" if blocks else "ready" if total >= READY and mature else
               "forming" if total >= FORMING else "not_now")
    trig = _trigger(lv, side, price)
    if verdict != "ready" and trig and not (room is not None and room < 1):  # the room line already names that level
        wait.insert(0, trig)
    summary = {"ready": f"Trade-quality {side.lower()} setup ({total}/100): worth the AI desk's full analysis now.",
               "forming": f"A {side.lower()} is forming ({total}/100) but not ready — the AI is not run yet.",
               "not_now": f"No trade-quality setup ({total}/100)"
                          + (f": {blocks[0]}" if blocks else "") + " — waiting is the position."}[verdict]
    return {"symbol": symbol, "tf": tf, "price": price, "side": side, "score": total, "verdict": verdict,
            "parts": parts, "blocks": blocks, "wait_for": list(dict.fromkeys(wait))[:4], "summary": summary,
            "ml": {k: (ml or {}).get(k) for k in ("p", "threshold", "take", "accepted_model", "basis")} if ml else None,
            "levels": {"stop": lv["default"]["stop"], "target": lv["default"]["target"]}}


def _trigger(lv: dict, side: str, price: float) -> str | None:
    """The price that would turn a forming idea into a trade: a close beyond the nearest opposing level."""
    first = next((t for t in lv["target_options"]), None)
    if not first:
        return None
    word = "above" if side == "LONG" else "below"
    return f"a close {word} {first['price']:.6g} ({first['why']}, {first['pct']}% from {price:.6g})"


async def check(symbol: str, tf: str, side: str | None = None, venue: str = "futures") -> dict:
    """Readiness for one coin and timeframe, cached two minutes."""
    from app.api.chat import _walk

    key = (symbol, tf, side, venue)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    walk, snap = await _walk(symbol, tf)
    htf = HIGHER.get(tf, tf)
    hw = None
    if htf != tf:
        try:
            hw = (await _walk(symbol, htf))[0]
        except Exception as e:  # noqa: BLE001 - the score says the higher timeframe is unknown
            log.warning("readiness_htf_failed", symbol=symbol, error=str(e))
    r = score(symbol, tf, walk, snap, hw, side, venue, await _ml(symbol, tf, snap, side))
    _cache[key] = (time.time(), r)
    return r


async def _ml(symbol: str, tf: str, snap: dict, side: str | None) -> dict | None:
    """The meta-label filter's view of the best setup on this chart, or None when there is no setup or no data."""
    from app.data.hub import now_ms
    from app.data.store import closed_only, load_candles
    from app.db import session_factory

    _, cand = _side_of({"synthesis": {"direction": "neutral"}}, snap, side)
    if not cand:
        return None
    try:
        from app.ml.meta_label import live_setup_features  # inside the try: the filter is optional, never a crash
        from app.ml.meta_label import score as ml_score

        async with session_factory()() as db:
            df = closed_only(await load_candles(db, symbol, tf, limit=600), tf, now_ms())
            btc = None if symbol == "BTCUSDT" else closed_only(await load_candles(db, "BTCUSDT", tf, limit=600), tf, now_ms())
        if len(df) < 250:
            return None
        return ml_score(live_setup_features(df, cand, snap, tf, btc=btc), tf=tf)
    except Exception as e:  # noqa: BLE001 - the filter is optional
        log.warning("readiness_ml_failed", symbol=symbol, error=str(e))
        return None


WATCH_TFS = ("1h", "4h")
WATCHED: dict[tuple, str] = {}  # (symbol, tf) -> last verdict, so an alert fires only on the change to ready


async def watch_once() -> list[dict]:
    """Re-check the shortlisted coins; alert on every coin that just turned ready. Code only."""
    from app.agents.scout import JOBS
    from app.db import session_factory
    from app.notify import notify

    turned = []
    for tf in WATCH_TFS:
        rows = ((JOBS.get(tf) or {}).get("result") or {}).get("rows") or []
        for r in rows[:15]:
            try:
                x = await check(r["symbol"], tf)
            except Exception as e:  # noqa: BLE001
                log.warning("readiness_watch_failed", symbol=r["symbol"], error=str(e))
                continue
            prev = WATCHED.get((r["symbol"], tf))
            WATCHED[(r["symbol"], tf)] = x["verdict"]
            if x["verdict"] == "ready" and prev != "ready":
                turned.append(x)
                async with session_factory()() as db:
                    await notify(db, "pre_signal", f"{r['symbol'].replace('USDT', '/USDT')} {tf}: {x['side']} ready ({x['score']}/100)",
                                 x["summary"] + " Open the Research desk and press Analyse to run the AI agents.",
                                 f"ready:{r['symbol']}:{tf}:{int(time.time() // (TF_MS[tf] / 1000))}",
                                 symbol=r["symbol"], timeframe=tf)
                    await db.commit()
    return turned


async def watch(every_s: int = 900) -> None:
    while True:
        try:
            await watch_once()
        except Exception as e:  # noqa: BLE001 - the watcher must never die
            log.warning("readiness_watch_error", error=str(e))
        await asyncio.sleep(every_s)
