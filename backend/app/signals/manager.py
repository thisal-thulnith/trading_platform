"""Signal lifecycle persistence.

States: FORMING → PRE_SIGNAL → CONFIRMED → ACTIVE → terminal
Terminal: TARGET_HIT, STOP_HIT, TP1_THEN_STOP, TP2_THEN_STOP, TIME_EXIT, INVALIDATED, EXPIRED,
          CLOSED_MANUAL, CANCELLED, DATA_UNAVAILABLE
Rules: core levels freeze at confirmation; every transition is an append-only SignalEvent; terminal outcomes
are written once and never rewritten.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import bus
from app.config import get_settings
from app.data.timeframes import tf_ms
from app.models import Signal, SignalEvent
from app.notify import notify
from app.signals.simulator import TradeSim
from app.strategies.exits import apply_exit_profile

PRE_STATES = ("FORMING", "PRE_SIGNAL")
LIVE_STATES = ("CONFIRMED", "ACTIVE")
TERMINAL = {"TARGET_HIT", "STOP_HIT", "TP1_THEN_STOP", "TP2_THEN_STOP", "TIME_EXIT", "INVALIDATED", "EXPIRED",
            "CLOSED_MANUAL", "CANCELLED", "DATA_UNAVAILABLE"}
STAGE_TO_STATE = {"forming": "FORMING", "pre_signal": "PRE_SIGNAL", "confirmed": "CONFIRMED"}
RANK = {"FORMING": 0, "PRE_SIGNAL": 1, "CONFIRMED": 2}
MISSING_SCANS_TO_INVALIDATE = 2


def signal_dict(s: Signal, last_price: float | None = None) -> dict:
    d = {c.name: (getattr(s, c.name).isoformat() if isinstance(getattr(s, c.name), datetime) else getattr(s, c.name))
         for c in Signal.__table__.columns}
    d.update(pnl_fields(s, last_price))
    return d


def pnl_fields(s: Signal, last_price: float | None) -> dict:
    """Live / realised P&L of a signal in % of entry price (price move only, before position sizing) and in R.
    Realised values come from the simulator (partial exits, fees, slippage); live values use the latest price."""
    sgn = 1 if s.direction == "LONG" else -1
    out: dict = {"live_pnl_pct": None, "live_r": None, "realized_pnl_pct": None, "distance_to_entry_pct": None}
    fill = s.entry_fill
    risk = abs((fill or ((s.entry_low or 0) + (s.entry_high or 0)) / 2) - (s.stop or 0)) or None
    if s.state == "ACTIVE" and fill and last_price:
        out["live_pnl_pct"] = sgn * (last_price - fill) / fill * 100
        out["live_r"] = sgn * (last_price - fill) / risk if risk else None
    elif s.state in ("CONFIRMED", "PRE_SIGNAL", "FORMING") and last_price and s.entry_low:
        edge = s.entry_high if sgn > 0 else s.entry_low
        out["distance_to_entry_pct"] = (last_price / edge - 1) * 100
    if s.r_multiple is not None and fill and risk:
        out["realized_pnl_pct"] = s.r_multiple * risk / fill * 100  # net R converted back to % of entry
    return out


async def _event(db: AsyncSession, sig: Signal, to_state: str, reason: str, price: float | None = None,
                 snapshot_id: str | None = None, data: dict | None = None) -> None:
    ev = SignalEvent(signal_id=sig.id, from_state=sig.state, to_state=to_state, reason=reason, price=price,
                     snapshot_id=snapshot_id, data=data or {})
    db.add(ev)
    sig.state = to_state
    sig.updated_at = datetime.now(UTC)
    bus.publish("signal", {"id": sig.id, "symbol": sig.symbol, "timeframe": sig.timeframe, "strategy": sig.strategy,
                           "direction": sig.direction, "state": to_state, "reason": reason, "price": price})


def _pair(sym: str) -> str:
    return sym[:-4] + "/" + sym[-4:] if sym.endswith("USDT") else sym


async def process_scan(db: AsyncSession, symbol: str, tf: str, snap: dict, candidates: list[dict],
                       snapshot_id: str, sizing_fn=None) -> list[str]:
    """Reconcile strategy candidates from one scan with persisted signals. Returns ids touched."""
    now = datetime.now(UTC)
    price = snap["price"]
    touched = []
    seen_keys = set()
    existing = {s.dedupe_key: s for s in (await db.execute(
        select(Signal).where(Signal.symbol == symbol, Signal.timeframe == tf, Signal.state.in_(PRE_STATES + LIVE_STATES))
    )).scalars()}
    profile = get_settings().exit_profiles.get(tf)
    for c in candidates:
        if "error" in c or c["stage"] not in STAGE_TO_STATE:
            continue
        c = apply_exit_profile(c, profile)
        key = f"{symbol}|{tf}|{c['strategy']}|{c['direction']}|{c['anchor_id']}"
        seen_keys.add(key)
        v = c["validation"]
        target = STAGE_TO_STATE[c["stage"]]
        blocked = target == "CONFIRMED" and not v["passed"]
        if blocked:
            target = "PRE_SIGNAL"
        sig = existing.get(key)
        payload = {"conditions": c["conditions"], "missing": c["missing"], "validation": v,
                   "invalidation": c["invalidation"], "notes": c["notes"], "missing_scans": 0,
                   "regime": snap["regime"]["regime"], "confluence": snap["confluence"],
                   "expected_hold_bars": c["expected_hold_bars"], "expiry_bars": c["expiry_bars"],
                   "exit_profile": c.get("exit_profile") or "default",
                   "blocked_reasons": [ch["name"] + ": " + ch["detail"] for ch in v["checks"] if not ch["passed"]]
                   if blocked else []}
        if sig is None:
            # a setup first observed already confirmed is recorded as detected (PRE_SIGNAL) then confirmed,
            # so the timeline always shows a real transition
            initial = "PRE_SIGNAL" if target == "CONFIRMED" else target
            sig = Signal(symbol=symbol, timeframe=tf, strategy=c["strategy"], direction=c["direction"], state=initial,
                         reference_price=price, entry_low=c["entry_low"], entry_high=c["entry_high"], stop=c["stop"],
                         tp1=c["targets"][0], tp2=c["targets"][1], tp3=c["targets"][2], score=v["quality_score"],
                         snapshot_id=snapshot_id, payload=payload, dedupe_key=key,
                         expires_at=now + timedelta(milliseconds=c["expiry_bars"] * tf_ms(tf)))
            db.add(sig)
            await db.flush()
            db.add(SignalEvent(signal_id=sig.id, from_state=None, to_state=initial, price=price,
                               snapshot_id=snapshot_id, reason=f"{c['strategy']} setup detected at stage {c['stage']}",
                               data={"missing": c["missing"]}))
            if target == "CONFIRMED":
                await _confirm(db, sig, c, snap, snapshot_id, sizing_fn)
            else:
                await _notify_stage(db, sig, target, c, snapshot_id)
            touched.append(sig.id)
            continue
        if sig.state in LIVE_STATES:
            continue  # frozen; outcome tracking handles it
        sig.payload = {**(sig.payload or {}), **payload}
        if RANK.get(target, 0) > RANK.get(sig.state, 0):
            # levels may still evolve before confirmation
            sig.entry_low, sig.entry_high, sig.stop = c["entry_low"], c["entry_high"], c["stop"]
            sig.tp1, sig.tp2, sig.tp3 = c["targets"]
            sig.reference_price, sig.snapshot_id, sig.score = price, snapshot_id, v["quality_score"]
            if target == "CONFIRMED":
                await _confirm(db, sig, c, snap, snapshot_id, sizing_fn)
            else:
                await _event(db, sig, target, "all setup conditions met; waiting for: " + ", ".join(c["missing"]),
                             price, snapshot_id)
                await _notify_stage(db, sig, target, c, snapshot_id)
            touched.append(sig.id)
        elif blocked and not (sig.payload or {}).get("blocked_logged"):
            db.add(SignalEvent(signal_id=sig.id, from_state=sig.state, to_state=sig.state, price=price,
                               snapshot_id=snapshot_id, reason="trigger met but validation blocked confirmation (NO TRADE): "
                               + "; ".join(payload["blocked_reasons"])))
            sig.payload = {**sig.payload, "blocked_logged": True}
    # setups that vanished or expired
    for key, sig in existing.items():
        if sig.state not in PRE_STATES or key in seen_keys:
            continue
        exp = sig.expires_at.replace(tzinfo=UTC) if sig.expires_at and sig.expires_at.tzinfo is None else sig.expires_at
        if exp and now >= exp:
            await _event(db, sig, "EXPIRED", "setup did not confirm before expiry", price, snapshot_id)
            sig.outcome, sig.closed_at = "EXPIRED", now
            touched.append(sig.id)
            continue
        miss = (sig.payload or {}).get("missing_scans", 0) + 1
        sig.payload = {**(sig.payload or {}), "missing_scans": miss}
        if miss >= MISSING_SCANS_TO_INVALIDATE:
            await _event(db, sig, "INVALIDATED", "setup conditions no longer present", price, snapshot_id)
            sig.outcome, sig.closed_at = "INVALIDATED", now
            await notify(db, "setup_invalidated", f"{_pair(symbol)} {sig.direction} setup invalidated",
                         f"{sig.strategy} on {tf}: conditions no longer present.", f"inv:{sig.id}", symbol=symbol,
                         timeframe=tf, signal_id=sig.id, snapshot_id=snapshot_id)
            touched.append(sig.id)
    await db.commit()
    return touched


async def _notify_stage(db, sig, state, c, snapshot_id):
    pair = _pair(sig.symbol)
    if state == "FORMING":
        await notify(db, "opportunity_forming", f"{pair} — Potential {sig.direction} setup forming",
                     f"{c['strategy']} ({sig.timeframe}). Missing: {', '.join(c['missing'])}. "
                     f"Expires {sig.expires_at:%H:%M} UTC.", f"form:{sig.id}", symbol=sig.symbol,
                     timeframe=sig.timeframe, signal_id=sig.id, snapshot_id=snapshot_id)
    elif state == "PRE_SIGNAL":
        await notify(db, "pre_signal", f"{pair} — {sig.direction} setup approaching confirmation",
                     f"{c['strategy']} ({sig.timeframe}). Waiting for: {', '.join(c['missing']) or 'validation'}.",
                     f"pre:{sig.id}", symbol=sig.symbol, timeframe=sig.timeframe, signal_id=sig.id,
                     snapshot_id=snapshot_id)


async def _confirm(db, sig: Signal, c: dict, snap: dict, snapshot_id: str, sizing_fn) -> None:
    now = datetime.now(UTC)
    sig.confirmed_at = now
    sig.expires_at = now + timedelta(milliseconds=c["expiry_bars"] * tf_ms(sig.timeframe))
    sim = TradeSim(sig.direction, sig.entry_low, sig.entry_high, sig.stop, [sig.tp1, sig.tp2, sig.tp3],
                   expiry_bars=c["expiry_bars"], max_hold_bars=max(3 * c["expected_hold_bars"], 24))
    extra = {"sim": sim.d(), "sim_tf": sig.timeframe, "evidence": snap["evidence"],
             "counter_arguments": c["validation"]["counter_arguments"],
             "data_freshness_s": (int(now.timestamp() * 1000) - snap["as_of_close_time"]) / 1000,
             "expected_costs": {"fee_taker": sim.fee, "slippage_bps": sim.slip_bps}}
    if sizing_fn:
        extra["sizing_example"] = sizing_fn(sig)
    sig.payload = {**(sig.payload or {}), **extra}
    await _event(db, sig, "CONFIRMED", "all strategy conditions satisfied and validation passed",
                 sig.reference_price, snapshot_id)
    v = c["validation"]
    await notify(db, "confirmed_signal", f"{_pair(sig.symbol)} — CONFIRMED {sig.direction} ({sig.timeframe})",
                 f"{sig.strategy}: entry {sig.entry_low:.6g}–{sig.entry_high:.6g}, SL {sig.stop:.6g}, "
                 f"TP1 {sig.tp1:.6g} (R:R {v['rr_tp1']:.2f}). Quality {v['quality_score']}.",
                 f"conf:{sig.id}", symbol=sig.symbol, timeframe=sig.timeframe, signal_id=sig.id, snapshot_id=snapshot_id)


async def track_outcomes(db: AsyncSession, symbol: str, tf: str, candle: dict) -> list[str]:
    """Advance live signals of `symbol` whose own timeframe == tf using the just-closed candle."""
    sigs = (await db.execute(select(Signal).where(Signal.symbol == symbol, Signal.timeframe == tf,
                                                  Signal.state.in_(LIVE_STATES)))).scalars().all()
    touched = []
    for sig in sigs:
        p = sig.payload or {}
        if "sim" not in p:
            continue
        conf_ms = int(sig.confirmed_at.replace(tzinfo=UTC).timestamp() * 1000) if sig.confirmed_at else 0
        if candle["open_time"] < conf_ms - tf_ms(tf) // 2:
            continue  # candle predates confirmation
        sd = dict(p["sim"])
        sim = TradeSim(**sd)
        evs = sim.on_bar(candle["open_time"], candle["open"], candle["high"], candle["low"], candle["close"])
        for e in evs:
            st = e["event"]
            when = datetime.fromtimestamp(e["time"] / 1000, UTC)
            if st == "ENTERED":
                sig.entered_at, sig.entry_fill = when, e["price"]
                await _event(db, sig, "ACTIVE", e["reason"], e["price"])
                await notify(db, "entry_reached", f"{_pair(symbol)} {sig.direction} entry filled",
                             f"Filled at {e['price']:.6g}", f"entry:{sig.id}", symbol=symbol, timeframe=tf,
                             signal_id=sig.id, snapshot_id=sig.snapshot_id)
            elif st.startswith("TP") and st.endswith("_HIT"):
                db.add(SignalEvent(signal_id=sig.id, from_state=sig.state, to_state=sig.state, price=e["price"],
                                   reason=e["reason"], data={"event": st}))
                await notify(db, "target_reached", f"{_pair(symbol)} {st.replace('_', ' ')}", e["reason"],
                             f"{st}:{sig.id}", symbol=symbol, timeframe=tf, signal_id=sig.id,
                             snapshot_id=sig.snapshot_id)
        if sim.terminal:
            sig.outcome = sim.outcome
            sig.closed_at = datetime.fromtimestamp(sim.exit_time / 1000, UTC) if sim.exit_time else datetime.now(UTC)
            sig.exit_price = sim.exit_price
            sig.r_multiple = sim.realized_r if sim.fill else None
            sig.mfe_r, sig.mae_r = sim.mfe_r, sim.mae_r
            await _event(db, sig, sim.outcome, sim.events[-1]["reason"] if sim.events else sim.outcome, sim.exit_price)
            if sim.outcome in ("STOP_HIT", "TP1_THEN_STOP", "TP2_THEN_STOP"):
                await notify(db, "stop_reached", f"{_pair(symbol)} {sig.direction} stopped ({sim.outcome})",
                             f"Result {sig.r_multiple:+.2f}R", f"stop:{sig.id}", symbol=symbol, timeframe=tf,
                             signal_id=sig.id, snapshot_id=sig.snapshot_id)
        sig.payload = {**p, "sim": sim.d()}
        touched.append(sig.id)
    await db.commit()
    return touched


async def close_manual(db: AsyncSession, sig: Signal, price: float, reason: str) -> None:
    p = sig.payload or {}
    if sig.state in TERMINAL:
        raise ValueError("signal already closed")
    now = datetime.now(UTC)
    if "sim" in p:
        sim = TradeSim(**p["sim"])
        sim.close_manual(int(now.timestamp() * 1000), price, reason)
        sig.payload = {**p, "sim": sim.d()}
        sig.r_multiple = sim.realized_r if sim.fill else None
        outcome = sim.outcome
    else:
        outcome = "CANCELLED"
    sig.outcome, sig.closed_at, sig.exit_price = outcome, now, price
    await _event(db, sig, outcome, reason, price)
    await db.commit()
