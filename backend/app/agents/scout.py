"""Scout: scans every USDT coin on Binance spot, not only the live-streamed universe.

Stage 1 (all coins, one request): 24h tickers → drop stablecoins, leveraged tokens and illiquid pairs.
Stage 2 (deep): the most liquid / active coins (plus the whole universe) get the full analysis — strategies,
validation, confluence and the ten schools — and a plain-language signal (BUY / SELL / WAIT).

Deep scans run as a background job (progress is polled); results are cached per timeframe for 5 minutes.
Coins outside the streamed universe are refreshed from Binance REST before analysis so they are never stale.
"""
import asyncio
import time

from app.analysis.schools import run_schools, school_vote, session_now
from app.config import get_settings
from app.data.hub import hub, now_ms
from app.data.ingest import backfill
from app.data.store import closed_only, load_candles
from app.db import session_factory
from app.logging import log
from app.strategies.exits import apply_exit_profile

STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "AEUR", "EURI", "USDE", "USD1", "PYUSD", "XUSD",
          "BFUSD", "RLUSD", "UST", "USTC", "PAXG", "WBTC", "WBETH", "BNSOL", "GBP", "TRY", "BRL"}
MIN_QUOTE_VOL = 2_000_000  # USDT traded in 24h; thinner books make every level unreliable
CACHE_S = 300

_coins: tuple[float, list[dict]] | None = None
_fresh: dict[tuple[str, str], float] = {}
JOBS: dict[str, dict] = {}  # tf -> {"status", "done", "total", "started", "result"}


async def all_coins() -> list[dict]:
    """Every tradeable USDT spot pair with 24h stats, most liquid first (cached 10 min)."""
    global _coins
    if _coins and time.time() - _coins[0] < 600:
        return _coins[1]
    from app.engine import engine

    q = get_settings().quote_asset
    info, tick = await asyncio.gather(engine.client.exchange_info("spot"), engine.client.ticker_24h_all("spot"))
    trading = {x["symbol"]: x["baseAsset"] for x in info["symbols"]
               if x["quoteAsset"] == q and x["status"] == "TRADING" and x.get("isSpotTradingAllowed", True)}
    out = []
    for t in tick:
        base = trading.get(t["symbol"])
        if not base or base in STABLE or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        out.append({"symbol": t["symbol"], "base": base, "price": float(t["lastPrice"]),
                    "change_24h_pct": float(t["priceChangePercent"]), "quote_volume_24h": float(t["quoteVolume"]),
                    "range_24h_pct": (float(t["highPrice"]) / float(t["lowPrice"]) - 1) * 100 if float(t["lowPrice"]) else 0,
                    "in_universe": t["symbol"] in hub.symbols})
    out.sort(key=lambda r: -r["quote_volume_24h"])
    _coins = (time.time(), out)
    return out


async def ensure_fresh(symbol: str, tf: str) -> None:
    """Coins outside the streamed universe get their candles (and higher timeframes) caught up over REST."""
    if symbol in hub.symbols:
        return
    from app.engine import HTF_MAP, engine

    for t in [tf, *HTF_MAP.get(tf, [])]:
        if time.time() - _fresh.get((symbol, t), 0) < 60:
            continue
        await backfill(engine.client, symbol, t, 500)
        _fresh[(symbol, t)] = time.time()


def _plain_check(ch: dict) -> str:
    """A failed validation check in beginner words."""
    n = ch["name"]
    if n == "exit_profile_quality":
        return f"a cleaner setup ({ch['detail'].split(' for ')[0].replace(' vs minimum ', ' of the ')} needed)"
    if n.startswith("research lab"):
        return "this strategy has lost money in backtests on this timeframe, so it is switched off"
    if n.startswith("strategy suspended"):
        return "this strategy is losing in live paper trading, so it is paused"
    return n.replace("_", " ")


def plain_signal(snap: dict, tf: str) -> dict:
    """The best candidate as a beginner-readable signal."""
    profile = get_settings().exit_profiles.get(tf)
    cands = [apply_exit_profile(c, profile) for c in snap.get("candidates", []) if "error" not in c]
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    cands.sort(key=lambda c: (-stage_w.get(c["stage"], 0), -c["validation"]["quality_score"]))
    if not cands:
        return {"action": "WAIT", "text": "No setup on this chart right now. Waiting is a position too."}
    c = cands[0]
    entry = (c["entry_low"] + c["entry_high"]) / 2
    risk = abs(entry - c["stop"])
    tgt = c["targets"][0]
    ready = c["stage"] == "confirmed" and c["validation"]["passed"]
    name = c["strategy"].replace("_", " ")
    base = {"strategy": name, "direction": c["direction"], "entry": entry, "stop": c["stop"], "target": tgt,
            "rr": abs(tgt - entry) / risk if risk else 0, "stop_pct": risk / entry * 100 if entry else 0,
            "target_pct": abs(tgt - entry) / entry * 100 if entry else 0, "quality": c["validation"]["quality_score"],
            "stage": c["stage"]}
    if ready:
        word = "BUY" if c["direction"] == "LONG" else "SELL"
        return {**base, "action": word,
                "text": f"{word} near {entry:,.6g}. Exit with a small loss at {c['stop']:,.6g} "
                        f"({base['stop_pct']:.1f}% away) if wrong; take profit at {tgt:,.6g} ({base['target_pct']:.1f}%)."}
    failed = [_plain_check(ch) for ch in c["validation"]["checks"] if not ch["passed"]]
    wait = ", ".join(m.replace("_", " ") for m in c.get("missing") or []) or ("; ".join(failed[:2]) if failed else "confirmation")
    return {**base, "action": "WAIT",
            "text": f"Possible {'BUY' if c['direction'] == 'LONG' else 'SELL'} ({name}) is forming, but not ready. "
                    f"Waiting for: {wait}."}


async def _deep(sym: str, tf: str) -> dict | None:
    from app.engine import engine, radar_row

    await ensure_fresh(sym, tf)
    snap = engine.latest.get((sym, tf)) if sym in hub.symbols else None
    snap = snap or await engine.analyze(sym, tf)
    if sym not in hub.symbols:  # keep the live radar to the streamed universe
        engine.latest.pop((sym, tf), None)
        engine.radar.pop((sym, tf), None)
    async with session_factory()() as db:
        df = closed_only(await load_candles(db, sym, tf, limit=500), tf, now_ms())
    vote = school_vote(run_schools(df, tf)) if len(df) >= 120 else school_vote([])
    rr = radar_row(snap)
    conf = snap["confluence"]
    core = conf["direction"]
    sig = plain_signal(snap, tf)
    score = rr["score"] + abs(conf["bias"]) * 30 + abs(vote["net"]) * 20
    reasons = []
    if rr["best"]:
        b = rr["best"]
        reasons.append(f"{b['strategy'].replace('_', ' ')} {b['direction']} setup ({b['stage']}, quality "
                       f"{b['quality']:.0f}{', passed checks' if b['passed'] else ''})")
    else:
        reasons.append("no strategy setup")
    reasons.append(f"indicator confluence {core} (bias {conf['bias']:+.2f})")
    reasons.append(f"council {vote['direction']} ({len(vote['bull'])}↑/{len(vote['bear'])}↓)")
    setup_dir = {"LONG": "bullish", "SHORT": "bearish"}.get((rr["best"] or {}).get("direction"))
    views = [d for d in (setup_dir, core, vote["direction"]) if d and d != "neutral"]
    agree = len(views) >= 2 and len(set(views)) == 1
    if agree:
        score += 10
        reasons.append("setup, confluence and council agree")
    elif len(set(views)) > 1:
        score -= 15
        reasons.append("methods conflict")
    if rr["regime"] in ("liquidity_stress", "news_instability"):
        reasons.append(f"⚠ {rr['regime'].replace('_', ' ')}")
    return {"symbol": sym, "price": snap["price"], "score": round(score, 1), "direction": core, "agree": agree,
            "regime": rr["regime"], "best": rr["best"], "council": vote, "reasons": reasons, "signal": sig,
            "in_universe": sym in hub.symbols}


async def _run(tf: str, deep_n: int) -> None:
    job = JOBS[tf]
    try:
        coins = await all_coins()
        liquid = [c for c in coins if c["quote_volume_24h"] >= MIN_QUOTE_VOL]
        # activity = liquidity rank + how much it is moving; the universe is always included
        ranked = sorted(liquid, key=lambda c: -(min(c["quote_volume_24h"] / 5e7, 4) + abs(c["change_24h_pct"]) / 5
                                                + c["range_24h_pct"] / 8))
        pick = list(dict.fromkeys([*hub.symbols, *(c["symbol"] for c in ranked[:deep_n])]))
        job.update(total=len(pick), screened=len(liquid), all=len(coins))
        sem = asyncio.Semaphore(6)

        async def one(s: str) -> dict | None:
            async with sem:
                try:
                    return await _deep(s, tf)
                except Exception as e:  # noqa: BLE001
                    log.warning("scout_deep_failed", symbol=s, error=f"{type(e).__name__}: {e}")
                    return None
                finally:
                    job["done"] += 1

        rows = [r for r in await asyncio.gather(*(one(s) for s in pick)) if r]
        rows.sort(key=lambda r: -r["score"])
        job["result"] = {"timeframe": tf, "rows": rows, "session": session_now(), "all_coins": len(coins),
                         "screened": len(liquid), "deep": len(rows), "finished": time.time(),
                         "note": f"Scout screened all {len(coins)} USDT coins, kept {len(liquid)} with ≥ $2M daily "
                                 f"volume and deep-analysed {len(rows)} of them. Ranking = setup "
                                 "stage/quality + strength of the read + agreement between methods. It picks what to "
                                 "study first, not what will go up."}
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        log.error("scout_failed", error=f"{type(e).__name__}: {e}")
        job["status"] = "error"
        job["error"] = str(e)


def scan_state(tf: str, deep_n: int = 150, refresh: bool = False) -> dict:
    """Start (or reuse) the scan for a timeframe and report progress / the latest result."""
    job = JOBS.get(tf)
    stale = not job or job["status"] == "error" or (
        job["status"] == "done" and time.time() - job["result"]["finished"] > CACHE_S)
    if refresh and job and job["status"] == "done":
        stale = True
    if stale:
        prev = job.get("result") if job else None
        JOBS[tf] = job = {"status": "running", "done": 0, "total": 0, "started": time.time(), "result": prev}
        asyncio.get_running_loop().create_task(_run(tf, deep_n))
    return {"status": job["status"], "done": job["done"], "total": job["total"], "screened": job.get("screened"),
            "all": job.get("all"), **(job["result"] or {"rows": []})}


async def wait_scan(tf: str, timeout: float = 120) -> dict:
    st = scan_state(tf)
    t0 = time.time()
    while st["status"] == "running" and not st["rows"] and time.time() - t0 < timeout:
        await asyncio.sleep(1)
        st = scan_state(tf)
    return st
