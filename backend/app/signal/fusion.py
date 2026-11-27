"""The final signal: every part of the platform, fused by the evidence each part has earned.

Components (each a score from -1 = bearish to +1 = bullish, or absent when there is no data):
  momentum     the coin's cross-sectional trend score among ~120 coins (quant brain)            (score - 50) / 50
  carry        funding per year: crowded longs pay and crash more often (BIS 2023)               -tanh(funding / 60%)
  regime       the BTC trend every coin trades in                                                bull +0.6, bear -0.6
  order_flow   cumulative volume delta over the last 24 candles                                  tanh(CVD % / 5)
  fundamentals supply still to come (FDV / market cap)                                           -0.5 at 2x, -0.2 at 1.5x
  setup        the chart engine's best setup and its stage                                       ±1 / ±0.6 / ±0.3
  ai_desk      the AI desk's decision and confidence, when it ran                                ±confidence
  quant_factors the quant lab's factor composite rank in today's universe (relative)            rank scaled to ±1
  quant_model  the quant lab's promoted model rank, when one is promoted                         rank scaled to ±1

Fusion: F = Σ wᵢ·sᵢ / Σ wᵢ over the components with data, the weights from the evidence registry
(app/signal/evidence.py: literature prior shrunk toward what this platform measured; zero when unproven or failing).
Also reported: coverage (how much of the total evidence weight had data), agreement (the share of weight on F's side)
and every component's contribution, so the signal explains itself.

The decision for a trade idea (a setup with a side) is deliberately strict:
  take    the readiness gate says ready, the fused evidence leans the setup's way (≥ 0.05) and ≥ 55% of the weight agrees
  wait    the evidence does not oppose it but the trigger or the agreement is missing
  veto    the fused evidence points the other way (≤ -0.10): the setup is dropped
With no proven component the fused score is small and the answer is "wait" — that is the honest output, not a failure.
"""
import asyncio
import math

from app.logging import log
from app.signal import evidence

STAGE = {"confirmed": 1.0, "pre_signal": 0.6, "forming": 0.3}
TAKE_ALIGN, VETO_ALIGN, TAKE_AGREE = 0.05, -0.10, 0.55


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None


def components(ctx: dict) -> dict[str, dict]:
    """Score each component from what is known about the coin (pure; ctx is gathered by `gather`)."""
    out: dict[str, dict] = {}
    q = ctx.get("rank") or {}
    if _num(q.get("score")) is not None:
        out["momentum"] = {"s": round((q["score"] - 50) / 50, 3), "why": f"trend score {q['score']:.0f} of 100 ({q.get('trend')}), rank {q.get('rank')} of {q.get('of')}"}
    f = _num(ctx.get("funding_ann_pct"))
    if f is not None:
        out["carry"] = {"s": round(-math.tanh(f / 60), 3), "why": f"funding {f:+.0f}% a year" + (" — crowded longs" if f > 30 else " — crowded shorts" if f < -30 else "")}
    bt = ctx.get("btc_trend")
    if bt:
        out["regime"] = {"s": {"bull": 0.6, "bear": -0.6}.get(bt, 0.0), "why": f"BTC trend {bt}"}
    cvd = _num(ctx.get("cvd_pct"))
    if cvd is not None:
        out["order_flow"] = {"s": round(math.tanh(cvd / 5), 3), "why": f"taker CVD {cvd:+.1f}% of volume over 24 candles"}
    fdv = _num(ctx.get("fdv_to_mcap"))
    if fdv is not None:
        out["fundamentals"] = {"s": -0.5 if fdv >= 2 else -0.2 if fdv >= 1.5 else 0.0, "why": f"FDV {fdv}x market cap"}
    side, stage = ctx.get("setup_side"), ctx.get("setup_stage")
    if side in ("LONG", "SHORT"):
        out["setup"] = {"s": (1 if side == "LONG" else -1) * STAGE.get(stage, 0.3),
                        "why": f"{(ctx.get('setup_strategy') or 'setup').replace('_', ' ')} {side.lower()}, {stage}"}
    d = ctx.get("desk")
    if d and d.get("action") in ("LONG", "SHORT", "WAIT"):
        c = _num(d.get("confidence")) or 0.5
        out["ai_desk"] = {"s": {"LONG": c, "SHORT": -c, "WAIT": 0.0}[d["action"]], "why": f"AI desk: {d['action']} at {c:.0%}"}
    qf = ctx.get("quant_factors")
    if qf and _num(qf.get("rank")) and _num(qf.get("of")):
        out["quant_factors"] = {"s": round(1 - 2 * (qf["rank"] - 1) / max(qf["of"] - 1, 1), 3),
                                "why": f"factor composite rank {qf['rank']} of {qf['of']} (relative: top = outperforms the others)"}
    qm = ctx.get("quant_model")
    if qm and _num(qm.get("rank")) and _num(qm.get("of")):
        out["quant_model"] = {"s": round(1 - 2 * (qm["rank"] - 1) / max(qm["of"] - 1, 1), 3), "why": f"quant model rank {qm['rank']} of {qm['of']}"}
    return out


def fuse(comps: dict[str, dict], weights: dict[str, float], setup_side: str | None = None,
         readiness_verdict: str | None = None) -> dict:
    """Combine the component scores by their evidence weights and decide (pure)."""
    total_w = sum(w for w in weights.values() if w > 0) or 1.0
    used = {k: v for k, v in comps.items() if weights.get(k, 0) > 0}
    wsum = sum(weights[k] for k in used)
    F = sum(weights[k] * used[k]["s"] for k in used) / wsum if wsum else 0.0
    signed = [(weights[k], used[k]["s"]) for k in used if used[k]["s"] != 0]
    agree = (sum(w for w, s in signed if (s > 0) == (F > 0)) / sum(w for w, _ in signed)) if signed and F else 0.0
    contrib = sorted(({"component": k, "score": v["s"], "weight": weights.get(k, 0.0),
                       "contribution": round(weights.get(k, 0.0) * v["s"] / wsum, 4) if wsum and k in used else 0.0,
                       "why": v["why"]} for k, v in comps.items()), key=lambda c: -abs(c["contribution"]))
    out = {"score": round(F, 4), "direction": "bullish" if F > 0.02 else "bearish" if F < -0.02 else "neutral",
           "strength": round(min(1.0, abs(F) / 0.5) * 100), "coverage": round(wsum / total_w, 3), "agreement": round(agree, 3),
           "contributions": contrib}
    if setup_side in ("LONG", "SHORT"):
        align = F * (1 if setup_side == "LONG" else -1)
        if align <= VETO_ALIGN:
            dec, why = "veto", f"the fused evidence points against a {setup_side.lower()} ({F:+.2f})"
        elif readiness_verdict == "ready" and align >= TAKE_ALIGN and agree >= TAKE_AGREE:
            dec, why = "take", f"setup ready and {agree:.0%} of the evidence weight agrees ({F:+.2f})"
        else:
            missing = [m for m, ok in (("the trigger", readiness_verdict == "ready"), ("evidence on its side", align >= TAKE_ALIGN),
                                       ("enough agreement", agree >= TAKE_AGREE)) if not ok]
            dec, why = "wait", "waiting for " + ", ".join(missing)
        out.update(decision=dec, decision_why=why, alignment=round(align, 4))
    return out


async def gather(symbol: str, tf: str = "4h", readiness: dict | None = None, candidate: dict | None = None,
                 desk: dict | None = None) -> dict:
    """Collect what the components need for one coin (each source optional)."""
    from app.agents import fundamentals, market_extras
    from app.quant import brain

    ctx: dict = {"desk": desk}
    try:
        await brain.get()  # computed on first use after a restart, then cached (30 min)
    except Exception as e:  # noqa: BLE001
        log.warning("signal_brain_failed", error=str(e))
    q = brain.coin(symbol)
    if q:
        ctx["rank"] = q
        ctx["funding_ann_pct"] = (q.get("derivs") or {}).get("funding_ann_pct")
    ctx["btc_trend"] = ((brain.peek() or {}).get("regime") or {}).get("btc_trend")
    if candidate:
        ctx.update(setup_side=candidate.get("direction"), setup_stage=candidate.get("stage"), setup_strategy=candidate.get("strategy"))
    elif readiness and readiness.get("side"):
        ctx.update(setup_side=readiness["side"], setup_stage="confirmed" if readiness.get("verdict") == "ready" else "forming")

    async def flow():
        try:
            return ((await market_extras.order_flow(symbol, tf)).get("last_24") or {}).get("cvd_pct_of_volume")
        except Exception:  # noqa: BLE001
            return None

    async def fdv():
        try:
            return ((await fundamentals.coin(symbol)).get("tokenomics") or {}).get("fdv_to_market_cap")
        except Exception:  # noqa: BLE001
            return None

    ctx["cvd_pct"], ctx["fdv_to_mcap"] = await asyncio.gather(flow(), fdv())
    try:
        from app.quant.lab import live_ranking  # the quant lab's cross-sectional ranking of today's universe

        lr = _lab_ranking(live_ranking)
        row = next((r for r in lr.get("rows", []) if r.get("symbol") == symbol), None)
        if row:
            key = "quant_model" if lr.get("promoted") else "quant_factors"
            ctx[key] = {"rank": row.get("rank"), "of": len(lr.get("rows", []))}
    except Exception:  # noqa: BLE001 - the lab is not built or has no data
        pass
    return ctx


_lab: dict = {"t": 0.0, "data": None}


def _lab_ranking(fn) -> dict:
    """The lab's ranking, cached ten minutes (it reads the local panel; ~1 s)."""
    import time

    if not _lab["data"] or time.time() - _lab["t"] > 600:
        _lab.update(t=time.time(), data=fn())
    return _lab["data"]


async def signal(symbol: str, tf: str = "4h", readiness: dict | None = None, candidate: dict | None = None,
                 desk: dict | None = None) -> dict:
    """The final signal for one coin: the fused score, its direction and strength, the decision for its setup, and
    every component's score, weight and reason."""
    if readiness is None:
        try:
            from app.agents import readiness as rd

            readiness = await rd.check(symbol, tf)
        except Exception as e:  # noqa: BLE001
            log.warning("signal_readiness_failed", symbol=symbol, error=str(e))
    ctx = await gather(symbol, tf, readiness, candidate, desk)
    reg = evidence.registry() if (evidence._mem["data"] or {}).get("components", {}).get("momentum", {}).get("n") else evidence.registry(0)
    w = {k: v["weight"] for k, v in reg["components"].items()}
    out = fuse(components(ctx), w, ctx.get("setup_side"), (readiness or {}).get("verdict"))
    out.update(symbol=symbol, tf=tf, setup_side=ctx.get("setup_side"), readiness=(readiness or {}).get("score"))
    return out
