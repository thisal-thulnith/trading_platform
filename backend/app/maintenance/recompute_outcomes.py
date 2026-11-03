"""One-off audit correction after the simulator R-basis fix (2026-11-01).

Re-simulates every resolved signal from stored candles with the corrected TradeSim. When the outcome or R
differs, the new values are written AND an append-only SignalEvent records the old values and the reason, so the
history stays auditable. Usage: uv run python -m app.maintenance.recompute_outcomes [--dry-run]
"""
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.data.store import load_candles
from app.data.timeframes import tf_ms
from app.db import init_engine, session_factory
from app.models import Signal, SignalEvent
from app.signals.simulator import TradeSim


async def main(dry: bool) -> None:
    init_engine()
    async with session_factory()() as db:
        sigs = (await db.execute(select(Signal).where(Signal.confirmed_at.is_not(None), Signal.outcome.is_not(None)))).scalars().all()
        changed = 0
        for s in sigs:
            p = s.payload or {}
            if "sim" not in p:
                continue
            old = p["sim"]
            sim = TradeSim(s.direction, s.entry_low, s.entry_high, s.stop, [s.tp1, s.tp2, s.tp3],
                           expiry_bars=old["expiry_bars"], max_hold_bars=old["max_hold_bars"], fee=old["fee"],
                           slip_bps=old["slip_bps"])
            start = int(s.confirmed_at.replace(tzinfo=UTC).timestamp() * 1000) - tf_ms(s.timeframe) // 2
            df = await load_candles(db, s.symbol, s.timeframe, limit=2000, start_ms=start)
            for r in df.itertuples():
                if sim.terminal:
                    break
                sim.on_bar(int(r.open_time), r.open, r.high, r.low, r.close)
            if not sim.terminal:
                continue
            new_r = sim.realized_r if sim.fill else None
            if sim.outcome == s.outcome and (new_r is None) == (s.r_multiple is None) and \
                    (new_r is None or abs(new_r - s.r_multiple) < 1e-6):
                continue
            changed += 1
            print(f"{s.symbol} {s.timeframe} {s.strategy}: {s.outcome} {s.r_multiple} -> {sim.outcome} {new_r}")
            if dry:
                continue
            db.add(SignalEvent(signal_id=s.id, from_state=s.state, to_state=sim.outcome, price=sim.exit_price,
                               reason="AUDIT CORRECTION: outcome recomputed after simulator fix (R now measured against "
                                      "planned risk; no fill when a bar opens beyond the stop). "
                                      f"Previous: {s.outcome}, {s.r_multiple}R",
                               data={"previous_outcome": s.outcome, "previous_r": s.r_multiple,
                                     "corrected_at": datetime.now(UTC).isoformat()}))
            s.outcome, s.state, s.r_multiple = sim.outcome, sim.outcome, new_r
            s.exit_price, s.mfe_r, s.mae_r = sim.exit_price, sim.mfe_r, sim.mae_r
            s.payload = {**p, "sim": sim.d()}
        if not dry:
            await db.commit()
        print(f"{changed} of {len(sigs)} resolved signals corrected" + (" (dry run)" if dry else ""))


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
