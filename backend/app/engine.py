"""24/7 engine: ingestion → closed-candle events → deterministic analysis → strategies → validation →
signal lifecycle → notifications; plus outcome tracking, paper trading, derivatives, books, news, health.

LLM reasoning is NOT invoked here (cost control); it is used on demand by the analyst chat / reports."""
import asyncio
import time
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import delete, select

from app.analysis.analyzer import build_snapshot, jsonable
from app.analysis.microstructure import derivatives_metrics
from app.bus import bus
from app.config import get_settings
from app.data.binance import BinanceClient
from app.data.hub import hub, now_ms
from app.data.ingest import (
    FuturesStream,
    KlineStream,
    backfill,
    poll_books,
    poll_derivatives,
    seed_oi_history,
)
from app.data.store import closed_only, load_candles
from app.data.timeframes import TIMEFRAMES, tf_ms
from app.data.universe import rank_universe
from app.db import session_factory
from app.logging import log
from app.ml.inference import predict
from app.models import (
    AnalysisSnapshot,
    DerivativesSnapshot,
    NewsItem,
    Notification,
    PaperAccount,
    Signal,
    UniverseSnapshot,
)
from app.news.feeds import collect_news, fear_greed
from app.notify import notify
from app.portfolio import paper
from app.risk.sizing import RiskProfile, size_position
from app.signals import manager
from app.strategies.evaluate import enrich, evaluate_strategies, strip_runtime

HTF_MAP = {"5m": ["15m", "1h"], "15m": ["1h", "4h"], "30m": ["2h", "4h"], "1h": ["4h", "1d"], "4h": ["1d", "1w"],
           "1d": ["1w"]}


class Engine:
    def __init__(self) -> None:
        self.s = get_settings()
        self.client = BinanceClient()
        self.tasks: list[asyncio.Task] = []
        self.kstream: KlineStream | None = None
        self.fstream: FuturesStream | None = None
        self.perps: set[str] = set()
        self.latest: dict[tuple[str, str], dict] = {}  # (symbol, tf) -> latest snapshot (in memory)
        self.radar: dict[tuple[str, str], dict] = {}
        self.oi_hist: dict[str, list[dict]] = {}
        self.fng: dict | None = None
        self.scan_sem = asyncio.Semaphore(4)
        self.running = False
        self.strategy_stats: dict[str, dict] = {}
        self.research_gate: dict[str, dict] = {}  # "strategy|tf" -> backtest evidence
        self.funding_applied: dict[str, int] = {}

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self.running = True
        await self.select_universe()
        self.tasks.append(asyncio.create_task(self._bootstrap()))

    async def stop(self) -> None:
        self.running = False
        for st in (self.kstream, self.fstream):
            if st:
                st.stop()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.close()

    async def _bootstrap(self) -> None:
        tfs = sorted(set(self.s.stream_timeframes) | set(self.s.scan_timeframes), key=lambda t: TIMEFRAMES[t])
        sem = asyncio.Semaphore(6)

        async def bf(sym, tf):
            async with sem:
                try:
                    await backfill(self.client, sym, tf, self.s.backfill_bars)
                except Exception as e:  # noqa: BLE001
                    log.warning("backfill_failed", symbol=sym, tf=tf, error=str(e))

        t0 = time.time()
        await asyncio.gather(*(bf(sym, tf) for sym in hub.symbols for tf in tfs))
        log.info("backfill_done", seconds=round(time.time() - t0, 1), symbols=len(hub.symbols))
        self.kstream = KlineStream(self.client, hub.symbols, tfs)
        self.fstream = FuturesStream([x for x in hub.symbols if x in self.perps])
        for coro in (self.kstream.run(), self.fstream.run(), self._candle_consumer(), self._deriv_loop(),
                     self._book_loop(), self._news_loop(), self._universe_loop(), self._paper_loop(),
                     self._health_loop(), self._initial_scan(), self._housekeeping_loop()):
            self.tasks.append(asyncio.create_task(coro))

    async def select_universe(self) -> None:
        try:
            r = await rank_universe(self.client)
            info = await self.client.exchange_info("perp")
            self.perps = {x["symbol"] for x in info["symbols"] if x["contractType"] == "PERPETUAL" and x["status"] == "TRADING"}
            hub.universe = r["selected"]
            hub.universe_meta = {k: v for k, v in r.items() if k != "selected"}
            async with session_factory()() as db:
                db.add(UniverseSnapshot(selected=r["selected"], rankings=r["rankings"]))
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.error("universe_failed_using_last_or_default", error=str(e))
            async with session_factory()() as db:
                last = (await db.execute(select(UniverseSnapshot).order_by(UniverseSnapshot.id.desc()).limit(1))).scalar()
            hub.universe = last.selected if last else [b for b in self.s.candidate_universe if b != "TON"]
        hub.symbols = [b + self.s.quote_asset for b in hub.universe]
        try:
            fi = await self.client.futures.get("/fapi/v1/fundingInfo", weight=1)
            hub.funding_interval_h = {x["symbol"]: int(x["fundingIntervalHours"]) for x in fi}
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- loops
    async def _candle_consumer(self) -> None:
        q = bus.subscribe("candle_closed")
        pending: dict[tuple[str, str], asyncio.Task] = {}
        while True:
            ev = (await q.get())["data"]
            sym, tf = ev["symbol"], ev["tf"]
            candle = hub.live.get((sym, tf))
            if candle and candle["open_time"] == ev["open_time"]:
                try:
                    async with session_factory()() as db:
                        await manager.track_outcomes(db, sym, tf, candle)
                except Exception as e:  # noqa: BLE001
                    log.error("track_failed", symbol=sym, error=str(e))
            if tf in self.s.scan_timeframes:
                key = (sym, tf)
                if key not in pending or pending[key].done():
                    pending[key] = asyncio.create_task(self.scan(sym, tf))

    async def _initial_scan(self) -> None:
        await asyncio.sleep(3)
        try:
            await self.refresh_research_gate()
            await self.refresh_strategy_stats()
        except Exception as e:  # noqa: BLE001
            log.warning("gate_init_failed", error=str(e))
        for tf in self.s.scan_timeframes:
            await asyncio.gather(*(self.scan(sym, tf) for sym in hub.symbols), return_exceptions=True)

    async def _deriv_loop(self) -> None:
        for sym in hub.symbols:
            if sym in self.perps:
                try:
                    self.oi_hist[sym] = await seed_oi_history(self.client, sym)
                except Exception:  # noqa: BLE001
                    self.oi_hist[sym] = []
        while True:
            try:
                await poll_derivatives(self.client, hub.symbols, self.perps)
                for sym, row in hub.deriv.items():
                    if row.get("open_interest"):
                        self.oi_hist.setdefault(sym, []).append({"ts": row["ts"], "oi": row["open_interest"],
                                                                 "oi_value": row.get("open_interest_value")})
                        self.oi_hist[sym] = self.oi_hist[sym][-600:]
            except Exception as e:  # noqa: BLE001
                log.warning("deriv_loop", error=str(e))
            await asyncio.sleep(self.s.derivatives_poll_s)

    async def _book_loop(self) -> None:
        while True:
            try:
                await poll_books(self.client, hub.symbols)
            except Exception as e:  # noqa: BLE001
                log.warning("book_loop", error=str(e))
            await asyncio.sleep(60)

    async def _news_loop(self) -> None:
        if not self.s.run_news:
            return
        while True:
            try:
                async with session_factory()() as db:
                    st = await collect_news(db)
                    hub.streams.setdefault("news", hub.stream("news"))
                    hub.stream("news").last_msg_ms = now_ms()
                    hub.stream("news").connected = st["feeds_ok"] > 0
                    hub.stream("news").messages += st["added"]
                    since = datetime.now(UTC) - timedelta(hours=2)
                    hi = (await db.execute(select(NewsItem).where(NewsItem.impact == "high",
                                                                  NewsItem.published_at >= since))).scalars().all()
                    for n in hi:
                        await notify(db, "high_impact_news", f"News: {n.title[:120]}",
                                     f"{n.source} · {n.event_type} · coins {', '.join(n.coins) or 'market-wide'} · "
                                     f"corroborated by {n.corroborations} source(s). Not a trade signal.",
                                     f"news:{n.cluster_key}", symbol=(n.coins[0] + self.s.quote_asset) if n.coins else None)
                    await db.commit()
                self.fng = await fear_greed() or self.fng
            except Exception as e:  # noqa: BLE001
                log.warning("news_loop", error=str(e))
            await asyncio.sleep(self.s.news_poll_s)

    async def _universe_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.universe_refresh_s)
            old = set(hub.symbols)
            await self.select_universe()
            if set(hub.symbols) != old:
                log.info("universe_changed", added=list(set(hub.symbols) - old), removed=list(old - set(hub.symbols)))
                for st in (self.kstream, self.fstream):
                    if st:
                        st.stop()
                for sym in set(hub.symbols) - old:
                    for tf in self.s.stream_timeframes:
                        await backfill(self.client, sym, tf, self.s.backfill_bars)
                tfs = sorted(set(self.s.stream_timeframes) | set(self.s.scan_timeframes), key=lambda t: TIMEFRAMES[t])
                self.kstream = KlineStream(self.client, hub.symbols, tfs)
                self.fstream = FuturesStream([x for x in hub.symbols if x in self.perps])
                self.tasks += [asyncio.create_task(self.kstream.run()), asyncio.create_task(self.fstream.run())]

    async def _paper_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            try:
                async with session_factory()() as db:
                    accts = (await db.execute(select(PaperAccount))).scalars().all()
                    for a in accts:
                        await paper.check_exits(db, a, hub.last_price)
                        for sym, m in hub.mark.items():
                            nft = m.get("next_funding_time")
                            prev = self.funding_applied.get(sym)
                            if prev and nft and nft > prev and now_ms() >= prev:
                                await paper.apply_funding(db, a, sym, m["funding_rate"], m["mark_price"])
                            if nft:
                                self.funding_applied[sym] = nft
                    if int(time.time()) % 300 < 2:
                        from app.models import EquityPoint
                        for a in accts:
                            opens = await paper.open_positions(db, a.id)
                            db.add(EquityPoint(account_id=a.id, equity=paper.equity_of(a, opens, hub.last_price)))
                        await db.commit()
            except Exception as e:  # noqa: BLE001
                log.warning("paper_loop", error=str(e))

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            for name, st in hub.streams.items():
                if name == "news":
                    continue
                age = (now_ms() - st.last_msg_ms) / 1000 if st.last_msg_ms else None
                if age is not None and age > 120:
                    async with session_factory()() as db:
                        await notify(db, "data_feed_failure", f"Data feed stale: {name}",
                                     f"No messages for {age:.0f}s. Reconnecting automatically; affected signals are "
                                     "not confirmed while data is stale.", f"feed:{name}:{now_ms() // 600_000}")
                        await db.commit()
            # DATA_UNAVAILABLE: live signals with no market data for > 6h
            async with session_factory()() as db:
                live = (await db.execute(select(Signal).where(Signal.state.in_(manager.LIVE_STATES)))).scalars().all()
                for sig in live:
                    fr = hub.freshness(sig.symbol)
                    if fr["age_s"] is not None and fr["age_s"] > 6 * 3600:
                        await manager._event(db, sig, "DATA_UNAVAILABLE", "no market data for > 6h", None)
                        sig.outcome, sig.closed_at = "DATA_UNAVAILABLE", datetime.now(UTC)
                await db.commit()

    async def _housekeeping_loop(self) -> None:
        """Prune analysis snapshots that no signal/notification references (keeps DB small)."""
        while True:
            await asyncio.sleep(3600)
            try:
                async with session_factory()() as db:
                    cutoff = datetime.now(UTC) - timedelta(days=3)
                    ref = set((await db.execute(select(Signal.snapshot_id))).scalars()) | set(
                        (await db.execute(select(Notification.snapshot_id))).scalars())
                    old = (await db.execute(select(AnalysisSnapshot.id).where(AnalysisSnapshot.created_at < cutoff))).scalars()
                    drop = [i for i in old if i not in ref]
                    if drop:
                        await db.execute(delete(AnalysisSnapshot).where(AnalysisSnapshot.id.in_(drop)))
                    await db.execute(delete(DerivativesSnapshot).where(DerivativesSnapshot.ts < now_ms() - 120 * 86_400_000))
                    await db.commit()
                await self.refresh_strategy_stats()
                await self.refresh_research_gate()
            except Exception as e:  # noqa: BLE001
                log.warning("housekeeping", error=str(e))

    async def refresh_research_gate(self) -> None:
        """Suspend confirmations for strategy/timeframe pairs whose most recent completed backtest shows
        statistically negative expectancy (bootstrap 95% CI upper bound < 0 with n >= 30)."""
        from app.models import BacktestRun

        async with session_factory()() as db:
            runs = (await db.execute(select(BacktestRun).where(BacktestRun.status == "done")
                                     .order_by(BacktestRun.created_at.desc()).limit(50))).scalars().all()
        gate: dict[str, dict] = {}
        for r in runs:
            tf = r.config.get("timeframe")
            by = ((r.results or {}).get("scenarios", {}).get("base", {}) or {}).get("by_strategy", {})
            for sname, st in by.items():
                key = f"{sname}|{tf}"
                if key in gate or st.get("n", 0) < 10:
                    continue  # newest run with a usable sample wins
                ci = st.get("expectancy_ci95") or [0, 0]
                # "supported" needs a clearly positive lower bound: several strategies are tested per run, so a
                # CI that merely excludes zero is not convincing evidence (multiple-testing caution).
                status = ("suspended" if st["n"] >= 30 and ci[1] < 0 else
                          "supported" if st["n"] >= 30 and ci[0] > 0.05 else "inconclusive")
                gate[key] = {"status": status, "n": st["n"], "expectancy_r": st["expectancy_r"], "ci": ci, "run": r.id}
        self.research_gate = gate

    async def refresh_strategy_stats(self) -> None:
        """Rolling forward-test expectancy per strategy (last 100 resolved signals) for degradation gating."""
        async with session_factory()() as db:
            rows = (await db.execute(select(Signal.strategy, Signal.r_multiple).where(Signal.r_multiple.is_not(None))
                                     .order_by(Signal.closed_at.desc()).limit(2000))).all()
        by: dict[str, list[float]] = {}
        for sname, r in rows:
            if len(by.setdefault(sname, [])) < 100:
                by[sname].append(r)
        self.strategy_stats = {k: {"n": len(v), "expectancy_r": sum(v) / len(v)} for k, v in by.items() if v}
        for k, v in self.strategy_stats.items():
            if v["n"] >= 30 and v["expectancy_r"] < 0:
                async with session_factory()() as db:
                    await notify(db, "model_degradation", f"Strategy {k} degraded",
                                 f"Rolling expectancy {v['expectancy_r']:+.2f}R over {v['n']} signals; new confirmations "
                                 "are suspended until performance recovers.", f"degr:{k}:{now_ms() // 86_400_000}")
                    await db.commit()

    # ---------------------------------------------------------------- analysis
    async def frames(self, symbol: str, tf: str, limit: int = 500, end_ms: int | None = None) -> tuple:
        now = end_ms or now_ms()
        async with session_factory()() as db:
            df = closed_only(await load_candles(db, symbol, tf, limit=limit, end_ms=end_ms), tf, now)
            htf = {}
            for h in HTF_MAP.get(tf, []):
                d = closed_only(await load_candles(db, symbol, h, limit=300, end_ms=end_ms), h, now)
                if len(d) < 60 and end_ms is None:
                    try:
                        await backfill(self.client, symbol, h, 400)
                        d = closed_only(await load_candles(db, symbol, h, limit=300), h, now)
                    except Exception:  # noqa: BLE001
                        pass
                htf[h] = d
            btc = closed_only(await load_candles(db, "BTCUSDT", tf, limit=limit, end_ms=end_ms), tf, now) \
                if symbol != "BTCUSDT" else None
        return df, htf, btc

    def deriv_for(self, symbol: str, df: pd.DataFrame, tf: str) -> dict | None:
        snap = hub.deriv.get(symbol)
        if not snap:
            return None
        snap = {**snap, **({k: hub.mark[symbol][k] for k in ("mark_price", "funding_rate")} if symbol in hub.mark else {})}
        snap["spot_price"] = hub.last_price.get(symbol) or float(df["close"].iloc[-1])
        bars_4h = max(1, tf_ms("4h") // tf_ms(tf))
        pc = {"4h": float(df["close"].iloc[-1] / df["close"].iloc[-1 - bars_4h] - 1) * 100} if len(df) > bars_4h else {}
        return derivatives_metrics(snap, self.oi_hist.get(symbol, []), [], pc)

    async def news_for(self, symbol: str) -> tuple[list[dict], bool]:
        base = symbol.replace(self.s.quote_asset, "")
        since = datetime.now(UTC) - timedelta(hours=24)
        async with session_factory()() as db:
            rows = (await db.execute(select(NewsItem).where(NewsItem.fetched_at >= since)
                                     .order_by(NewsItem.published_at.desc()).limit(300))).scalars().all()
        items = [{"title": n.title, "source": n.source, "sentiment": n.sentiment, "credibility": n.credibility,
                  "event_type": n.event_type, "impact": n.impact, "url": n.url, "coins": n.coins,
                  "published_at": n.published_at.isoformat() if n.published_at else None}
                 for n in rows if base in (n.coins or []) or (n.event_type == "macro")]
        cutoff = datetime.now(UTC) - timedelta(hours=3)

        def published(n):
            t = n.published_at or n.fetched_at
            return t.replace(tzinfo=UTC) if t.tzinfo is None else t

        # abstain only on *recently published* high-impact news that names this coin, or on official macro sources
        recent_hi = [n for n in rows if n.impact == "high" and published(n) >= cutoff
                     and (base in (n.coins or []) or (n.event_type == "macro" and n.credibility >= 0.9))]
        return items[:20], bool(recent_hi)

    async def analyze(self, symbol: str, tf: str, persist: bool = False, end_ms: int | None = None) -> dict:
        df, htf, btc = await self.frames(symbol, tf, end_ms=end_ms)
        if len(df) < 120:
            if end_ms is None:
                await backfill(self.client, symbol, tf, self.s.backfill_bars)
                df, htf, btc = await self.frames(symbol, tf)
            if len(df) < 120:
                raise ValueError(f"insufficient data for {symbol} {tf}: {len(df)} closed candles")
        live = end_ms is None
        news, news_risk = await self.news_for(symbol) if live else ([], False)
        ml = None
        try:
            ml = await predict(symbol, tf, df, btc)
        except Exception as e:  # noqa: BLE001
            log.warning("ml_predict_failed", error=str(e))
        book = {k: v for k, v in hub.books.get(symbol, {}).items() if k != "raw"} if live else None
        snap = build_snapshot(symbol, tf, df, htf, deriv=self.deriv_for(symbol, df, tf) if live else None,
                              book=book, news=news, ml=ml, news_risk=news_risk)
        snap["news"] = news
        snap["fear_greed"] = self.fng if live else None
        snap["liquidations"] = list(hub.liquidations.get(symbol, []))[-20:] if live else []
        snap["data_freshness"] = hub.freshness(symbol) if live else {"replay": True}
        snap = enrich(snap, df)
        stats = self.strategy_stats
        cands = evaluate_strategies(snap, now_ms=now_ms() if live else snap["as_of_close_time"], stats=stats)
        for c in cands:
            if "validation" not in c:
                continue
            st = stats.get(c.get("strategy"), {})
            if st.get("n", 0) >= 30 and st.get("expectancy_r", 0) < 0:
                c["validation"]["passed"] = False
                c["validation"]["checks"].append({"name": "strategy suspended (forward-test degraded)", "passed": False,
                                                  "detail": f"{st['expectancy_r']:+.2f}R"})
            gate = self.research_gate.get(f"{c['strategy']}|{tf}")
            if not gate:
                c["validation"]["checks"].append({
                    "name": "research lab: backtest evidence not negative", "passed": True,
                    "detail": f"no backtest on {tf} yet — experimental, unvalidated"})
            else:
                ok = gate["status"] != "suspended"
                c["validation"]["checks"].append({
                    "name": "research lab: backtest evidence not negative", "passed": ok,
                    "detail": f"{gate['status']}: {gate['expectancy_r']:+.2f}R, 95% CI [{gate['ci'][0]:+.2f}, {gate['ci'][1]:+.2f}], "
                              f"n={gate['n']} (run {gate['run'][:8]})"})
                if not ok:
                    c["validation"]["passed"] = False
        snap = strip_runtime(snap)
        snap["candidates"] = cands
        snap["decision"] = decide(snap, cands)
        snap = jsonable(snap)
        if persist:
            async with session_factory()() as db:
                rec = AnalysisSnapshot(symbol=symbol, timeframe=tf, as_of=snap["as_of"],
                                       engine_version=snap["engine_version"], data_hash=snap["data_hash"], payload=snap)
                db.add(rec)
                await db.commit()
                snap["snapshot_id"] = rec.id
        if live:
            self.latest[(symbol, tf)] = snap
            self.radar[(symbol, tf)] = radar_row(snap)
        return snap

    async def scan(self, symbol: str, tf: str) -> None:
        async with self.scan_sem:
            try:
                snap = await self.analyze(symbol, tf, persist=False)
                cands = [c for c in snap["candidates"] if "error" not in c]
                hub.last_scan[f"{symbol}:{tf}"] = now_ms()
                async with session_factory()() as db:
                    has_open = (await db.execute(select(Signal.id).where(
                        Signal.symbol == symbol, Signal.timeframe == tf,
                        Signal.state.in_(manager.PRE_STATES)).limit(1))).first()
                if not cands and not has_open:
                    return
                async with session_factory()() as db:
                    rec = AnalysisSnapshot(symbol=symbol, timeframe=tf, as_of=snap["as_of"],
                                           engine_version=snap["engine_version"], data_hash=snap["data_hash"], payload=snap)
                    db.add(rec)
                    await db.commit()
                    snap["snapshot_id"] = rec.id
                    await manager.process_scan(db, symbol, tf, snap, cands, rec.id, sizing_fn=example_sizing)
                bus.publish("scan", {"symbol": symbol, "tf": tf, "radar": self.radar.get((symbol, tf))})
            except Exception as e:  # noqa: BLE001
                log.error("scan_failed", symbol=symbol, tf=tf, error=f"{type(e).__name__}: {e}")


def decide(snap: dict, cands: list[dict]) -> dict:
    """Opportunity abstention engine: explicit NO TRADE unless a confirmed, validated setup exists."""
    confirmed = [c for c in cands if c.get("stage") == "confirmed" and c["validation"]["passed"]]
    if confirmed:
        best = max(confirmed, key=lambda c: c["validation"]["quality_score"])
        return {"action": best["direction"], "strategy": best["strategy"], "quality": best["validation"]["quality_score"],
                "reasons": [f"{c['name']} ✓" for c in best["conditions"]]}
    reasons = []
    regime = snap["regime"]["regime"]
    if regime in ("liquidity_stress", "news_instability", "high_volatility"):
        reasons.append(f"regime {regime}: abstain")
    blocked = [c for c in cands if c.get("stage") == "confirmed" and not c["validation"]["passed"]]
    for c in blocked[:3]:
        failed = [ch["name"] for ch in c["validation"]["checks"] if not ch["passed"]]
        reasons.append(f"{c['strategy']} {c['direction']} blocked: {', '.join(failed)}")
    pending = [c for c in cands if c.get("stage") in ("forming", "pre_signal")]
    if pending:
        reasons.append(f"{len(pending)} setup(s) forming/awaiting confirmation")
    if not reasons:
        reasons.append("no strategy conditions satisfied")
    if abs(snap["confluence"]["bias"]) < 0.15:
        reasons.append("evidence is mixed (confluence near neutral)")
    return {"action": "NO_TRADE", "reasons": reasons}


def radar_row(snap: dict) -> dict:
    cands = [c for c in snap.get("candidates", []) if "error" not in c]
    stage_w = {"confirmed": 3, "pre_signal": 2, "forming": 1}
    best = max(cands, key=lambda c: (stage_w.get(c["stage"], 0), c["validation"]["quality_score"]), default=None)
    score = 0.0
    if best:
        score = stage_w[best["stage"]] * 20 + best["validation"]["quality_score"] * 0.4
        if best["stage"] == "confirmed" and not best["validation"]["passed"]:
            score *= 0.5
    if snap["regime"]["regime"] in ("liquidity_stress", "news_instability"):
        score *= 0.3
    return {"symbol": snap["symbol"], "timeframe": snap["timeframe"], "price": snap["price"],
            "regime": snap["regime"]["regime"], "bias": snap["confluence"]["bias"],
            "direction": snap["confluence"]["direction"], "decision": snap["decision"]["action"],
            "best": {"strategy": best["strategy"], "direction": best["direction"], "stage": best["stage"],
                     "quality": best["validation"]["quality_score"], "passed": best["validation"]["passed"],
                     "missing": best["missing"]} if best else None,
            "score": round(score, 1), "as_of": snap["as_of"],
            "families_bull": snap["confluence"]["independent_families_bull"],
            "families_bear": snap["confluence"]["independent_families_bear"]}


def example_sizing(sig: Signal) -> dict:
    """Illustrative sizing for a $1,000 account at 1% risk (users see their own sizing in the UI)."""
    entry = (sig.entry_low + sig.entry_high) / 2
    return size_position(RiskProfile(equity=1000, risk_per_trade_pct=1.0, max_leverage=3), sig.symbol, sig.direction,
                         entry, sig.stop)


engine = Engine()
