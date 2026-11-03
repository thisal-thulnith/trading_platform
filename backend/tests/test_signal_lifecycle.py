from datetime import UTC

from sqlalchemy import select

from app.models import Notification, Signal, SignalEvent
from app.signals.manager import process_scan, track_outcomes

H = 3_600_000


def snap(price=100.0, as_of=1_700_000_000_000):
    return {"price": price, "as_of_close_time": as_of + H, "regime": {"regime": "weak_bull"},
            "confluence": {"bias": 0.4}, "evidence": [{"family": "trend", "name": "x", "direction": 1}]}


def cand(stage, passed=True, anchor="ob1"):
    return {"strategy": "smc_ob_retest", "direction": "LONG", "stage": stage, "anchor_id": anchor,
            "entry_low": 99.0, "entry_high": 100.0, "stop": 98.0, "targets": [102.0, 104.0, 106.0],
            "conditions": [], "missing": [] if stage == "confirmed" else ["trigger"], "invalidation": "below 98",
            "notes": [], "expiry_bars": 12, "expected_hold_bars": 10,
            "validation": {"passed": passed, "checks": [{"name": "rr", "passed": passed, "detail": "x"}],
                           "counter_arguments": [], "rr_tp1": 1.9, "quality_score": 70}}


async def test_lifecycle_forming_to_target(db):
    await process_scan(db, "BTCUSDT", "1h", snap(), [cand("forming")], "snap1")
    s = (await db.execute(select(Signal))).scalar_one()
    assert s.state == "FORMING"
    await process_scan(db, "BTCUSDT", "1h", snap(), [cand("pre_signal")], "snap2")
    await db.refresh(s)
    assert s.state == "PRE_SIGNAL"
    # trigger met but validation fails -> stays PRE_SIGNAL (NO TRADE)
    await process_scan(db, "BTCUSDT", "1h", snap(), [cand("confirmed", passed=False)], "snap3")
    await db.refresh(s)
    assert s.state == "PRE_SIGNAL"
    await process_scan(db, "BTCUSDT", "1h", snap(), [cand("confirmed")], "snap4")
    await db.refresh(s)
    assert s.state == "CONFIRMED" and s.confirmed_at is not None
    frozen = (s.entry_low, s.stop, s.tp1)
    # later scans with different levels must not modify a confirmed signal
    c2 = cand("confirmed")
    c2["stop"] = 90.0
    await process_scan(db, "BTCUSDT", "1h", snap(), [c2], "snap5")
    await db.refresh(s)
    assert (s.entry_low, s.stop, s.tp1) == frozen
    t0 = int(s.confirmed_at.replace(tzinfo=UTC).timestamp() * 1000)
    await track_outcomes(db, "BTCUSDT", "1h", {"open_time": t0, "open": 101, "high": 101, "low": 99.5, "close": 100})
    await db.refresh(s)
    assert s.state == "ACTIVE" and s.entry_fill is not None
    await track_outcomes(db, "BTCUSDT", "1h", {"open_time": t0 + H, "open": 100, "high": 107, "low": 100, "close": 106})
    await db.refresh(s)
    assert s.state == "TARGET_HIT" and s.outcome == "TARGET_HIT" and s.r_multiple > 0
    events = (await db.execute(select(SignalEvent).where(SignalEvent.signal_id == s.id))).scalars().all()
    states = [e.to_state for e in events]
    assert states[0] == "FORMING" and "CONFIRMED" in states and states[-1] == "TARGET_HIT"
    cats = {n.category for n in (await db.execute(select(Notification))).scalars()}
    assert {"opportunity_forming", "pre_signal", "confirmed_signal", "entry_reached", "target_reached"} <= cats


async def test_vanished_setup_invalidated_after_two_scans(db):
    await process_scan(db, "ETHUSDT", "1h", snap(), [cand("forming", anchor="a")], "s1")
    await process_scan(db, "ETHUSDT", "1h", snap(), [], "s2")
    s = (await db.execute(select(Signal))).scalar_one()
    assert s.state == "FORMING"
    await process_scan(db, "ETHUSDT", "1h", snap(), [], "s3")
    await db.refresh(s)
    assert s.state == "INVALIDATED" and s.outcome == "INVALIDATED"


async def test_notifications_deduplicated(db):
    for _ in range(3):
        await process_scan(db, "SOLUSDT", "1h", snap(), [cand("forming", anchor="z")], "s")
    n = (await db.execute(select(Notification).where(Notification.category == "opportunity_forming"))).scalars().all()
    assert len(n) == 1


async def test_first_seen_confirmed_has_real_transition(db):
    await process_scan(db, "XRPUSDT", "1h", snap(), [cand("confirmed", anchor="q")], "s")
    s = (await db.execute(select(Signal))).scalar_one()
    evs = (await db.execute(select(SignalEvent).where(SignalEvent.signal_id == s.id).order_by(SignalEvent.id))).scalars().all()
    assert [(e.from_state, e.to_state) for e in evs] == [(None, "PRE_SIGNAL"), ("PRE_SIGNAL", "CONFIRMED")]


def test_pnl_fields():
    from app.signals.manager import pnl_fields

    s = Signal(direction="SHORT", state="ACTIVE", entry_fill=100.0, stop=102.0, entry_low=99, entry_high=100)
    f = pnl_fields(s, 98.0)
    assert f["live_pnl_pct"] == 2.0 and f["live_r"] == 1.0
    s2 = Signal(direction="LONG", state="TARGET_HIT", entry_fill=100.0, stop=98.0, r_multiple=1.5, entry_low=99, entry_high=100)
    assert pnl_fields(s2, None)["realized_pnl_pct"] == 3.0
