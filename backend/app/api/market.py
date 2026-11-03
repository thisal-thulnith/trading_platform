"""Public market/analysis endpoints (read-only)."""
from datetime import UTC, datetime

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.analysis.indicators import REGISTRY
from app.analysis.indicators.core import to_json_series
from app.analysis.indicators.volume import volume_profile
from app.data.hub import hub, now_ms
from app.data.ingest import backfill
from app.data.store import load_candles
from app.data.timeframes import TIMEFRAMES, tf_ms
from app.db import session_factory
from app.engine import engine
from app.models import AnalysisSnapshot, ModelRecord, NewsItem, Signal, SignalEvent, UniverseSnapshot
from app.signals.manager import signal_dict
from app.strategies.library import STRATEGIES

router = APIRouter(prefix="/api")


def _sym(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("-", "")
    if not s.isalnum() or len(s) > 20:
        raise HTTPException(400, "invalid symbol")
    return s


def _tf(tf: str) -> str:
    if tf not in TIMEFRAMES:
        raise HTTPException(400, f"timeframe must be one of {list(TIMEFRAMES)}")
    return tf


@router.get("/markets")
async def markets():
    out = []
    async with session_factory()() as db:
        for sym in hub.symbols:
            df = await load_candles(db, sym, "1h", limit=25)
            price = hub.last_price.get(sym) or (float(df["close"].iloc[-1]) if len(df) else None)
            ch = float(price / df["close"].iloc[0] - 1) * 100 if len(df) >= 25 and price else None
            vol = float(df["quote_volume"].iloc[-24:].sum()) if len(df) else None
            out.append({"symbol": sym, "base": sym[:-4], "price": price, "change_24h_pct": ch, "quote_volume_24h": vol,
                        "freshness": hub.freshness(sym), "has_perp": sym in engine.perps,
                        "funding_rate": (hub.mark.get(sym) or {}).get("funding_rate"),
                        "radar": {tf: engine.radar.get((sym, tf)) for tf in ("15m", "1h", "4h")}})
    return {"markets": out, "universe": hub.universe_meta, "timeframes": list(TIMEFRAMES)}


@router.get("/universe/history")
async def universe_history(limit: int = 10):
    async with session_factory()() as db:
        rows = (await db.execute(select(UniverseSnapshot).order_by(UniverseSnapshot.id.desc()).limit(limit))).scalars()
        return [{"created_at": r.created_at.isoformat(), "selected": r.selected, "rankings": r.rankings} for r in rows]


@router.get("/candles/{symbol}")
async def candles(symbol: str, tf: str = "1h", limit: int = Query(500, le=5000), end: int | None = None):
    symbol, tf = _sym(symbol), _tf(tf)
    async with session_factory()() as db:
        df = await load_candles(db, symbol, tf, limit=limit, end_ms=end)
    stale = len(df) == 0 or (end is None and now_ms() - int(df["open_time"].iloc[-1]) > 3 * tf_ms(tf))
    if (len(df) < min(limit, 300) or stale) and end is None:
        try:
            await backfill(engine.client, symbol, tf, max(limit, 500))
        except Exception as e:
            if len(df) == 0:
                raise HTTPException(503, f"market data unavailable: {e}") from e
        async with session_factory()() as db:
            df = await load_candles(db, symbol, tf, limit=limit)
    rows = df.to_dict("records")
    live = hub.live.get((symbol, tf))
    if live and end is None and (not rows or live["open_time"] >= rows[-1]["open_time"]):
        if rows and rows[-1]["open_time"] == live["open_time"]:
            rows[-1] = live
        else:
            rows.append(live)
    return {"symbol": symbol, "timeframe": tf, "source": "binance_spot", "candles": [
        {"time": int(r["open_time"]) // 1000, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
         "volume": r["volume"], "taker_buy": r.get("taker_buy_base", 0)} for r in rows],
        "freshness": hub.freshness(symbol)}


@router.get("/indicators")
async def indicator_catalog():
    return [i.meta() for i in REGISTRY.values()]


@router.get("/indicators/{ind_id}/{symbol}")
async def indicator_series(ind_id: str, symbol: str, tf: str = "1h", limit: int = Query(1000, le=5000),
                           params: str = ""):
    if ind_id not in REGISTRY:
        raise HTTPException(404, "unknown indicator")
    symbol, tf = _sym(symbol), _tf(tf)
    kw = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
    warm = 250
    async with session_factory()() as db:
        df = await load_candles(db, symbol, tf, limit=limit + warm)
    live = hub.live.get((symbol, tf))
    if live is not None and len(df) and live["open_time"] > int(df["open_time"].iloc[-1]):
        import pandas as pd
        df = pd.concat([df, pd.DataFrame([live])], ignore_index=True)
    if len(df) < 30:
        raise HTTPException(404, "no data")
    try:
        out = REGISTRY[ind_id].compute(df, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    times = df["open_time"].to_numpy()
    return {"id": ind_id, "meta": REGISTRY[ind_id].meta(), "params": kw,
            "series": {k: to_json_series(v[-limit:], times[-limit:]) for k, v in out.items()}}


@router.get("/volume_profile/{symbol}")
async def vprofile(symbol: str, tf: str = "1h", bars: int = Query(200, le=2000), bins: int = Query(48, le=200)):
    symbol, tf = _sym(symbol), _tf(tf)
    async with session_factory()() as db:
        df = await load_candles(db, symbol, tf, limit=bars)
    vp = volume_profile(df, bins)
    vp["from_time"] = int(df["open_time"].iloc[0]) // 1000 if len(df) else None
    return vp


@router.get("/analysis/{symbol}")
async def analysis(symbol: str, tf: str = "1h", fresh: bool = False):
    symbol, tf = _sym(symbol), _tf(tf)
    snap = engine.latest.get((symbol, tf))
    if snap is None or fresh or now_ms() - snap["generated_at"] > tf_ms(tf):
        try:
            snap = await engine.analyze(symbol, tf)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    return snap


@router.post("/analysis/{symbol}/run")
async def analysis_run(symbol: str, tf: str = "1h"):
    """Create a new immutable, versioned analysis snapshot."""
    symbol, tf = _sym(symbol), _tf(tf)
    try:
        return await engine.analyze(symbol, tf, persist=True)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/analysis/{symbol}/history")
async def analysis_history(symbol: str, tf: str = "1h", limit: int = 30):
    symbol = _sym(symbol)
    async with session_factory()() as db:
        rows = (await db.execute(select(AnalysisSnapshot).where(AnalysisSnapshot.symbol == symbol,
                                                                AnalysisSnapshot.timeframe == tf)
                                 .order_by(AnalysisSnapshot.created_at.desc()).limit(limit))).scalars()
        return [{"id": r.id, "created_at": r.created_at.isoformat(), "as_of": r.as_of, "engine_version": r.engine_version,
                 "decision": (r.payload.get("decision") or {}).get("action"), "bias": r.payload["confluence"]["bias"],
                 "regime": r.payload["regime"]["regime"]} for r in rows]


@router.get("/snapshots/{snapshot_id}")
async def snapshot(snapshot_id: str):
    async with session_factory()() as db:
        r = await db.get(AnalysisSnapshot, snapshot_id)
    if not r:
        raise HTTPException(404, "snapshot not found")
    return {**r.payload, "snapshot_id": r.id, "created_at": r.created_at.isoformat()}


@router.get("/snapshots/{a}/compare/{b}")
async def compare(a: str, b: str):
    async with session_factory()() as db:
        ra, rb = await db.get(AnalysisSnapshot, a), await db.get(AnalysisSnapshot, b)
    if not ra or not rb:
        raise HTTPException(404, "snapshot not found")
    pa, pb = ra.payload, rb.payload
    ida = {x["id"]: x for x in pa.get("annotations", [])}
    idb = {x["id"]: x for x in pb.get("annotations", [])}
    return {
        "a": {"id": a, "as_of": pa["as_of"], "decision": pa.get("decision"), "bias": pa["confluence"]["bias"],
              "regime": pa["regime"]["regime"]},
        "b": {"id": b, "as_of": pb["as_of"], "decision": pb.get("decision"), "bias": pb["confluence"]["bias"],
              "regime": pb["regime"]["regime"]},
        "annotations_added": [idb[k] for k in idb.keys() - ida.keys()],
        "annotations_removed": [ida[k] for k in ida.keys() - idb.keys()],
        "annotations_changed": [{"id": k, "before": ida[k].get("status"), "after": idb[k].get("status")}
                                for k in ida.keys() & idb.keys() if ida[k].get("status") != idb[k].get("status")],
        "family_changes": {f: {"a": pa["confluence"]["families"].get(f, {}).get("score"),
                               "b": pb["confluence"]["families"].get(f, {}).get("score")}
                           for f in set(pa["confluence"]["families"]) | set(pb["confluence"]["families"])},
    }


@router.get("/replay/{symbol}")
async def replay(symbol: str, at: int, tf: str = "1h"):
    """Analysis reconstructed with only data available at `at` (epoch seconds): candles with
    open_time + tf <= at. Derivatives/books/news are excluded because their history is incomplete."""
    symbol, tf = _sym(symbol), _tf(tf)
    end_ms = at * 1000
    last_open = (end_ms // tf_ms(tf)) * tf_ms(tf) - tf_ms(tf)
    try:
        snap = await engine.analyze(symbol, tf, end_ms=last_open + tf_ms(tf))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    snap["replay"] = {"at": at, "last_closed_open_time": snap["as_of"] // 1000,
                      "excluded": ["derivatives", "orderbook", "news", "ml (if model trained after `at`)"]}
    return snap


@router.get("/radar")
async def radar(tf: str | None = None):
    rows = [r for (s, t), r in engine.radar.items() if tf is None or t == tf]
    rows.sort(key=lambda r: -r["score"])
    return {"rows": rows, "note": "Ranked by validated setup stage and quality, not indicator count."}


@router.get("/signals")
async def signals(state: str | None = None, symbol: str | None = None, limit: int = Query(100, le=500),
                  active: bool = False):
    async with session_factory()() as db:
        q = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if state:
            q = q.where(Signal.state.in_(state.split(",")))
        if active:
            q = q.where(Signal.state.in_(["FORMING", "PRE_SIGNAL", "CONFIRMED", "ACTIVE"]))
        if symbol:
            q = q.where(Signal.symbol == _sym(symbol))
        rows = (await db.execute(q)).scalars().all()
    out = []
    for s in rows:
        d = signal_dict(s, hub.last_price.get(s.symbol))
        p = d.pop("payload") or {}
        d["summary"] = {"missing": p.get("missing"), "regime": p.get("regime"),
                        "rr_tp1": (p.get("validation") or {}).get("rr_tp1"),
                        "blocked_reasons": p.get("blocked_reasons"), "invalidation": p.get("invalidation"),
                        "conditions": p.get("conditions")}
        d["last_price"] = hub.last_price.get(s.symbol)
        out.append(d)
    return out


@router.get("/signals/{signal_id}")
async def signal_detail(signal_id: str):
    async with session_factory()() as db:
        s = await db.get(Signal, signal_id)
        if not s:
            raise HTTPException(404, "signal not found")
        evs = (await db.execute(select(SignalEvent).where(SignalEvent.signal_id == signal_id)
                                .order_by(SignalEvent.id))).scalars().all()
    d = signal_dict(s, hub.last_price.get(s.symbol))
    d["events"] = [{"ts": e.ts.isoformat(), "from": e.from_state, "to": e.to_state, "price": e.price, "reason": e.reason,
                    "snapshot_id": e.snapshot_id, "data": e.data} for e in evs]
    d["last_price"] = hub.last_price.get(s.symbol)
    return d


@router.get("/performance")
async def performance():
    """Forward-test (live paper) performance of every signal ever confirmed. Nothing is deleted."""
    from app.backtest.metrics import breakdown, trade_stats

    async with session_factory()() as db:
        rows = (await db.execute(select(Signal).where(Signal.confirmed_at.is_not(None)))).scalars().all()
        counts = dict((await db.execute(select(Signal.state, func.count()).group_by(Signal.state))).all())
    resolved = [s for s in rows if s.r_multiple is not None]
    trades = [{"r": s.r_multiple, "strategy": s.strategy, "regime": (s.payload or {}).get("regime"),
               "symbol": s.symbol, "timeframe": s.timeframe, "direction": s.direction} for s in resolved]
    outcomes: dict[str, int] = {}
    for s in rows:
        outcomes[s.outcome or s.state] = outcomes.get(s.outcome or s.state, 0) + 1
    lat = [((s.confirmed_at - s.created_at).total_seconds()) for s in rows if s.confirmed_at and s.created_at]
    # calibration of quality score vs realised win (simple reliability by score bucket)
    buckets = []
    for lo in range(0, 100, 20):
        b = [s for s in resolved if lo <= (s.score or 0) < lo + 20]
        if b:
            buckets.append({"score_range": [lo, lo + 20], "n": len(b), "win_rate": sum(1 for s in b if s.r_multiple > 0) / len(b),
                            "avg_r": float(np.mean([s.r_multiple for s in b]))})
    return {"confirmed": len(rows), "resolved": len(resolved), "states": counts, "outcomes": outcomes,
            "stats": trade_stats([t["r"] for t in trades]), "by_strategy": breakdown(trades, "strategy"),
            "by_regime": breakdown(trades, "regime"), "by_symbol": breakdown(trades, "symbol"),
            "by_timeframe": breakdown(trades, "timeframe"), "quality_calibration": buckets,
            "median_forming_to_confirm_s": float(np.median(lat)) if lat else None,
            "equity_r": np.cumsum([s.r_multiple for s in sorted(resolved, key=lambda s: s.closed_at)]).tolist(),
            "disclaimer": "Hypothetical forward-test results from simulated fills; not real trading performance."}


@router.get("/strategies")
async def strategies():
    return {"strategies": [s.spec.d() for s in STRATEGIES.values()], "live_stats": engine.strategy_stats,
            "research_gate": engine.research_gate}


@router.get("/models")
async def models():
    async with session_factory()() as db:
        rows = (await db.execute(select(ModelRecord).order_by(ModelRecord.created_at.desc()).limit(50))).scalars()
        return [{"id": r.id, "name": r.name, "version": r.version, "status": r.status, "target": r.target,
                 "created_at": r.created_at.isoformat(), "params": r.params,
                 "metrics": {k: r.metrics.get(k) for k in ("criteria", "summary", "economic", "reliability", "folds",
                                                            "feature_importance")}} for r in rows]


@router.get("/news")
async def news(coin: str | None = None, limit: int = Query(100, le=500), impact: str | None = None):
    async with session_factory()() as db:
        q = select(NewsItem).order_by(NewsItem.published_at.desc().nullslast()).limit(limit * 3 if coin else limit)
        if impact:
            q = q.where(NewsItem.impact == impact)
        rows = (await db.execute(q)).scalars().all()
    items = [{"id": n.id, "source": n.source, "url": n.url, "title": n.title, "summary": n.summary[:300],
              "published_at": n.published_at.isoformat() if n.published_at else None, "coins": n.coins,
              "sentiment": n.sentiment, "event_type": n.event_type, "credibility": n.credibility, "impact": n.impact,
              "corroborations": n.corroborations} for n in rows if not coin or coin.upper() in (n.coins or [])]
    return {"items": items[:limit], "fear_greed": engine.fng,
            "method": "RSS headlines; lexicon sentiment (heuristic); credibility = source prior + corroboration."}


@router.get("/system/health")
async def system_health():
    c = engine.client
    return {
        "time": datetime.now(UTC).isoformat(), "uptime_s": (now_ms() - hub.started_ms) / 1000,
        "engine_running": engine.running, "universe": hub.symbols,
        "providers": [c.spot.status.as_dict(), c.futures.status.as_dict()],
        "streams": [s.d() for s in hub.streams.values()],
        "freshness": {s: hub.freshness(s) for s in hub.symbols},
        "data_quality": {"gaps_repaired": hub.gaps_repaired, "candles_rejected": hub.candles_rejected},
        "last_scans": hub.last_scan, "bus_dropped": __import__("app.bus", fromlist=["bus"]).bus.dropped,
        "tasks": {"total": len(engine.tasks), "failed": [repr(t.exception())[:200] for t in engine.tasks
                                                          if t.done() and not t.cancelled() and t.exception()]},
    }


@router.get("/liquidations/{symbol}")
async def liquidations(symbol: str):
    return {"items": list(hub.liquidations.get(_sym(symbol), [])),
            "note": "Binance broadcasts at most one liquidation per symbol per second (lower bound)."}


@router.get("/orderbook/{symbol}")
async def orderbook(symbol: str):
    b = hub.books.get(_sym(symbol))
    if not b:
        raise HTTPException(404, "no book snapshot yet")
    return b


@router.get("/methodology")
async def methodology():
    return {"sections": [
        {"title": "Data", "body": "Binance public spot klines (WebSocket + REST backfill) and USDⓈ-M futures public data. "
                                  "Only closed candles are used for analysis; the forming candle is display-only."},
        {"title": "Signals", "body": "Strategies emit setups at forming / pre-signal / confirmed stages. Confirmation "
                                     "requires all strategy conditions plus validation gates (net R:R ≥ 1.2, stop 0.25–6 ATR, "
                                     "fresh data, non-abstain regime, ≥3 independent evidence families, no strategy degradation)."},
        {"title": "Outcome tracking", "body": "Confirmed signals are tracked on closed candles of their own timeframe. "
                                              "Limit entry inside the zone; if stop and target fall in the same candle the stop is assumed first. "
                                              "1/3 exits at TP1/TP2/TP3 with stop to breakeven after TP1. Fees 0.05% taker and 2 bps slippage per fill."},
        {"title": "Integrity", "body": "Signals and their events are append-only; outcomes are written once. Expired, invalidated "
                                       "and losing signals are kept and counted."},
        {"title": "Performance", "body": "R multiple = net PnL / initial risk. Hypothetical results, not real trading. "
                                         "Small samples (<30) are flagged; 95% CIs are bootstrapped."},
        {"title": "Limitations", "body": "SMC/Elliott detections are descriptive heuristics without peer-reviewed predictive "
                                         "evidence. Liquidation clusters are estimates. News sentiment is a lexicon heuristic."},
    ]}


@router.get("/follow_all")
async def follow_all(capital: float = Query(100.0, gt=0, le=1e9), risk_pct: float = Query(1.0, gt=0, le=10)):
    """Hypothetical small account that took every filled signal (fixed-fractional risk, compounding)."""
    from app.portfolio.follow_all import FollowTrade, simulate_follow_all

    async with session_factory()() as db:
        rows = (await db.execute(select(Signal).where(Signal.entry_fill.is_not(None),
                                                      Signal.entered_at.is_not(None)))).scalars().all()
    trades = [FollowTrade(id=s.id, symbol=s.symbol, timeframe=s.timeframe, strategy=s.strategy, direction=s.direction,
                          state=s.state, entered_at=s.entered_at, closed_at=s.closed_at, fill=s.entry_fill,
                          stop=s.stop or 0, entry_low=s.entry_low or 0, entry_high=s.entry_high or 0,
                          r_multiple=s.r_multiple) for s in rows]
    out = simulate_follow_all(trades, dict(hub.last_price), capital, risk_pct)
    out["disclaimer"] = ("Hypothetical: simulated fills on the platform's own signals, not real trading. Past or live "
                         "paper results do not predict future returns.")
    return out
