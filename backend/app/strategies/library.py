"""Strategy library. Each strategy is deterministic and uses only the snapshot (closed bars)."""
from app.strategies.base import Condition as C
from app.strategies.base import SetupCandidate, Strategy, StrategySpec

TREND_REGIMES = {"strong_bull", "weak_bull", "strong_bear", "weak_bear", "breakout"}


def _targets(entry: float, stop: float, side: str, rs=(1.5, 2.5, 4.0), magnets: list[float] | None = None):
    r = abs(entry - stop)
    s = 1 if side == "LONG" else -1
    base = [entry + s * k * r for k in rs]
    if magnets:
        # use a liquidity magnet as TP1/TP2 when it lies between 1R and 4R
        valid = sorted([m for m in magnets if s * (m - entry) >= 1.0 * r and s * (m - entry) <= 4.0 * r],
                       key=lambda m: s * (m - entry))
        if valid:
            base[0] = valid[0]
            base = sorted(base, key=lambda x: s * (x - entry))
    return base


def _liquidity_magnets(snap: dict, side: str) -> list[float]:
    smc = snap.get("smc") or {}
    want = "buy_side_liquidity" if side == "LONG" else "sell_side_liquidity"
    return [lq["price"] for lq in smc.get("liquidity", []) if lq["type"] == want and lq["status"] == "resting"]


def _side_of(d: str) -> int:
    return 1 if d in ("bullish", "bull", "LONG") else -1


def _regime(snap) -> str:
    return snap["regime"]["regime"]


def _htf_ok(snap, s: int) -> tuple[bool, str]:
    mtf = snap.get("mtf") or {}
    if not mtf:
        return True, "no higher timeframe data"
    opp = [k for k, v in mtf.items() if v == -s]
    return (not opp), ("HTF opposing: " + ",".join(opp)) if opp else "HTF not opposing"


class TrendPullback(Strategy):
    spec = StrategySpec(
        "trend_pullback", "Trend pullback to EMA", "trend_following",
        "Trade in the direction of an established trend after a pullback into the EMA20–EMA50 band.",
        ["strong_bull", "weak_bull", "strong_bear", "weak_bear"], ["ohlcv"],
        "Close back above EMA20 (long) after a low inside the EMA20–EMA50 band, with RSI > 50.",
        "Targets 1.5R/2.5R/4R or nearest resting liquidity; stop to breakeven after TP1.",
        "Close below EMA50 (long) before trigger, or stop hit.",
        "Below the lowest low of the last 5 bars minus 0.3×ATR.", "R multiples / liquidity magnets", "B")

    def evaluate(self, snap):
        ind = snap["indicators"]
        e20, e50, a, c = ind["ema20"], ind["ema50"], ind["atr14"], ind["close"]
        if not all([e20, e50, a]):
            return []
        bars = snap["_recent"]
        out = []
        for side in ("LONG", "SHORT"):
            s = 1 if side == "LONG" else -1
            trend = s * (e20 - e50) > 0
            regime_ok = _regime(snap) in TREND_REGIMES and (_regime(snap).endswith("bull") if s > 0 else
                                                           _regime(snap).endswith("bear") or _regime(snap) == "breakout")
            lo5 = min(bars["low"][-5:]) if s > 0 else max(bars["high"][-5:])
            touched = (lo5 <= e20 and lo5 >= e50 - 0.3 * a) if s > 0 else (lo5 >= e20 and lo5 <= e50 + 0.3 * a)
            trig = s * (c - e20) > 0 and s * ((ind["rsi14"] or 50) - 50) > 0
            htf, htf_d = _htf_ok(snap, s)
            stop = lo5 - s * 0.3 * a
            entry = c
            cand = SetupCandidate(
                "trend_pullback", side,
                [C("trend regime", "context", regime_ok, _regime(snap)),
                 C("EMA20/EMA50 aligned", "context", trend, f"EMA20 {e20:.6g} vs EMA50 {e50:.6g}"),
                 C("higher timeframe not opposing", "context", htf, htf_d),
                 C("pullback into EMA band", "setup", touched, f"recent extreme {lo5:.6g}"),
                 C("close back beyond EMA20 with RSI confirmation", "trigger", trig,
                   f"close {c:.6g}, RSI {ind['rsi14'] or 0:.1f}")],
                min(entry, entry - s * 0.1 * a), max(entry, entry - s * 0.1 * a), stop,
                _targets(entry, stop, side, magnets=_liquidity_magnets(snap, side)),
                f"close {'below' if s > 0 else 'above'} EMA50 ({e50:.6g})", f"tp:{side}:{snap['as_of'] // 86_400_000}",
                expiry_bars=8, expected_hold_bars=30)
            out.append(cand)
        return out


class SMCOrderBlockRetest(Strategy):
    spec = StrategySpec(
        "smc_ob_retest", "SMC order block retest", "smc_continuation",
        "With external structure in favour, wait for price to return to an unmitigated order block in the "
        "discount (long) / premium (short) half of the range, then require a reaction.",
        ["strong_bull", "weak_bull", "strong_bear", "weak_bear", "range"], ["ohlcv"],
        "Price trades into the OB and the bar closes back beyond the OB edge, or internal CHoCH in favour.",
        "TP at nearest opposite liquidity, then 2.5R/4R.",
        "Close through the far side of the OB.",
        "Beyond the OB far edge ± 0.25×ATR.", "liquidity magnets / R multiples", "C")

    def evaluate(self, snap):
        smc = snap.get("smc") or {}
        if "error" in smc:
            return []
        ind = snap["indicators"]
        a, c = ind["atr14"], ind["close"]
        bars = snap["_recent"]
        out = []
        pdz = smc.get("premium_discount") or {}
        last_int = [e for e in smc.get("structure", []) if e["scope"] == "internal"]
        for ob in smc.get("order_blocks", []):
            if ob["status"] == "invalidated":
                continue
            s = _side_of(ob["direction"])
            side = "LONG" if s > 0 else "SHORT"
            dist = (c - ob["top"]) / a if s > 0 else (ob["bottom"] - c) / a
            if dist > 3:  # too far to be relevant
                continue
            trend_ok = smc.get("trend_external") == ("bullish" if s > 0 else "bearish")
            loc_ok = (pdz.get("zone") == ("discount" if s > 0 else "premium")) if pdz else True
            touched_now = (min(bars["low"][-3:]) <= ob["top"]) if s > 0 else (max(bars["high"][-3:]) >= ob["bottom"])
            first_touch = ob["touched_time"] is None or ob["touched_time"] >= bars["time"][-3]
            reaction = s * (c - (ob["top"] if s > 0 else ob["bottom"])) > 0 and touched_now
            choch = any(e["direction"] == ("bull" if s > 0 else "bear") and e["break_time"] >= bars["time"][-6]
                        for e in last_int)
            htf, htf_d = _htf_ok(snap, s)
            stop = (ob["bottom"] - 0.25 * a) if s > 0 else (ob["top"] + 0.25 * a)
            entry_hi, entry_lo = (ob["top"], (ob["top"] + ob["bottom"]) / 2) if s > 0 else \
                ((ob["top"] + ob["bottom"]) / 2, ob["bottom"])
            ref = c if reaction else (entry_hi if s > 0 else entry_lo)
            out.append(SetupCandidate(
                "smc_ob_retest", side,
                [C("external structure in favour", "context", trend_ok, smc.get("trend_external", "")),
                 C("higher timeframe not opposing", "context", htf, htf_d),
                 C("OB within 3 ATR", "setup", dist <= 3, f"{dist:.2f} ATR away"),
                 C(f"{'discount' if s > 0 else 'premium'} location", "setup", loc_ok, pdz.get("zone", "n/a")),
                 C("price tapped OB (first touch)", "setup", touched_now and first_touch,
                   f"zone {ob['bottom']:.6g}–{ob['top']:.6g}"),
                 C("reaction close or internal CHoCH", "trigger", reaction or choch,
                   "close back beyond OB edge" if reaction else ("internal CHoCH" if choch else "waiting"))],
                min(entry_lo, entry_hi), max(entry_lo, entry_hi), stop,
                _targets(ref, stop, side, (1.5, 2.5, 4.0), _liquidity_magnets(snap, side)),
                f"close {'below' if s > 0 else 'above'} {ob['bottom'] if s > 0 else ob['top']:.6g}", ob["id"],
                expiry_bars=12, expected_hold_bars=24, extra={"order_block": ob}))
        return out


class LiquiditySweepReversal(Strategy):
    spec = StrategySpec(
        "liquidity_sweep_reversal", "Liquidity sweep reversal", "liquidity_sweep_reversal",
        "After a wick through resting liquidity that closes back inside (sweep), wait for an internal "
        "structure shift in the opposite direction.",
        ["range", "weak_bull", "weak_bear", "high_volatility"], ["ohlcv"],
        "Internal CHoCH/BOS in the reversal direction within 10 bars of the sweep.",
        "TP1 = equilibrium / opposite liquidity, TP2/TP3 = 2.5R/4R.",
        "New extreme beyond the sweep wick.", "Beyond sweep extreme ± 0.2×ATR.", "liquidity / R multiples", "C")

    def evaluate(self, snap):
        smc = snap.get("smc") or {}
        if "error" in smc:
            return []
        ind = snap["indicators"]
        a, c = ind["atr14"], ind["close"]
        bars = snap["_recent"]
        out = []
        for sw in smc.get("sweeps", []):
            if sw["kind"] != "sweep" or sw["time"] < bars["time"][-10]:
                continue
            s = _side_of(sw["bias"])
            side = "LONG" if s > 0 else "SHORT"
            k = bars["time"].index(sw["time"]) if sw["time"] in bars["time"] else None
            if k is None:
                continue
            ext = min(bars["low"][k:]) if s > 0 else max(bars["high"][k:])
            not_violated = (ext >= min(bars["low"][k:k + 1])) if s > 0 else (ext <= max(bars["high"][k:k + 1]))
            shift = any(e["scope"] == "internal" and e["direction"] == ("bull" if s > 0 else "bear")
                        and e["break_time"] > sw["time"] for e in smc.get("structure", []))
            stop = ext - s * 0.2 * a
            out.append(SetupCandidate(
                "liquidity_sweep_reversal", side,
                [C("regime allows reversal", "context", _regime(snap) in self.spec.regimes, _regime(snap)),
                 C(f"sweep of {sw['source']}", "setup", True, f"level {sw['price']:.6g}"),
                 C("sweep extreme intact", "setup", not_violated, f"extreme {ext:.6g}"),
                 C("internal structure shift", "trigger", shift, "CHoCH/BOS after sweep" if shift else "waiting")],
                min(c, c - s * 0.15 * a), max(c, c - s * 0.15 * a), stop,
                _targets(c, stop, side, magnets=_liquidity_magnets(snap, side)),
                f"trade beyond {ext:.6g}", sw["id"], expiry_bars=10, expected_hold_bars=20,
                extra={"stop_hunt_candidate": sw["stop_hunt_candidate"]}))
        return out


class BreakoutRetest(Strategy):
    spec = StrategySpec(
        "breakout_retest", "Range breakout & retest", "breakout_retest",
        "Close outside the prior 20-bar range on above-average volume, then a retest of the broken level holds.",
        ["breakout", "strong_bull", "strong_bear", "low_volatility", "weak_bull", "weak_bear"], ["ohlcv"],
        "Retest: wick back to the level (±0.25 ATR) with a close on the breakout side.",
        "1.5R/2.5R/4R.", "Close back inside the range.", "Other side of the level − 0.6×ATR.", "R multiples", "B")

    def evaluate(self, snap):
        ind = snap["indicators"]
        a, c = ind["atr14"], ind["close"]
        bars = snap["_recent"]
        hi, lo = bars["high"], bars["low"]
        if len(hi) < 35 or not a:
            return []
        out = []
        for side in ("LONG", "SHORT"):
            s = 1 if side == "LONG" else -1
            # range defined on bars [-35, -10); breakout searched in the last 10 bars
            level = max(hi[-35:-10]) if s > 0 else min(lo[-35:-10])
            closes = bars["close"]
            vols = bars["volume"]
            bo_idx = next((i for i in range(len(closes) - 10, len(closes)) if s * (closes[i] - level) > 0.1 * a), None)
            near = s * (c - level) > -0.5 * a
            vol_ok = bo_idx is not None and vols[bo_idx] > 1.5 * (sum(vols[-35:-10]) / 25)
            retest = bo_idx is not None and any(
                (lo[j] <= level + 0.25 * a and closes[j] > level) if s > 0 else (hi[j] >= level - 0.25 * a and closes[j] < level)
                for j in range(bo_idx + 1, len(closes)))
            stop = level - s * 0.6 * a
            out.append(SetupCandidate(
                "breakout_retest", side,
                [C("regime", "context", _regime(snap) in self.spec.regimes, _regime(snap)),
                 C("price near/above range edge", "setup", near, f"level {level:.6g}"),
                 C("breakout close", "setup", bo_idx is not None, "yes" if bo_idx is not None else "not yet"),
                 C("breakout volume > 1.5× average", "setup", vol_ok, ""),
                 C("successful retest", "trigger", retest, "")],
                min(level, level + s * 0.25 * a), max(level, level + s * 0.25 * a), stop,
                _targets(c if retest else level, stop, side), f"close back {'below' if s > 0 else 'above'} {level:.6g}",
                f"bo:{side}:{round(level, 8)}", expiry_bars=10, expected_hold_bars=24))
        return out


class FVGContinuation(Strategy):
    spec = StrategySpec(
        "fvg_continuation", "FVG continuation", "smc_continuation",
        "In a trending structure, buy (sell) the first retrace into an open FVG created in the trend direction.",
        ["strong_bull", "weak_bull", "strong_bear", "weak_bear"], ["ohlcv"],
        "Price enters the FVG and closes back out in the trend direction.", "1.5R/2.5R/4R.",
        "Close through the far side of the FVG.", "Beyond FVG far edge ± 0.3×ATR.", "R multiples", "C")

    def evaluate(self, snap):
        smc = snap.get("smc") or {}
        if "error" in smc:
            return []
        ind = snap["indicators"]
        a, c = ind["atr14"], ind["close"]
        bars = snap["_recent"]
        out = []
        for g in smc.get("fvgs", [])[-6:]:
            if g["type"] != "fvg" or g["status"] in ("filled", "inverted"):
                continue
            s = _side_of(g["direction"])
            side = "LONG" if s > 0 else "SHORT"
            trend_ok = smc.get("trend_external") == ("bullish" if s > 0 else "bearish")
            entered = (min(bars["low"][-3:]) <= g["top"]) if s > 0 else (max(bars["high"][-3:]) >= g["bottom"])
            closed_out = s * (c - (g["top"] if s > 0 else g["bottom"])) > 0
            stop = (g["bottom"] - 0.3 * a) if s > 0 else (g["top"] + 0.3 * a)
            out.append(SetupCandidate(
                "fvg_continuation", side,
                [C("structure in favour", "context", trend_ok, smc.get("trend_external", "")),
                 C("regime trending", "context", _regime(snap) in TREND_REGIMES, _regime(snap)),
                 C("price retraced into FVG", "setup", entered, f"{g['bottom']:.6g}–{g['top']:.6g}"),
                 C("close back out of FVG", "trigger", entered and closed_out, "")],
                g["bottom"], g["top"], stop, _targets(c if closed_out else (g["top"] if s > 0 else g["bottom"]), stop, side),
                f"close {'below' if s > 0 else 'above'} {g['bottom'] if s > 0 else g['top']:.6g}", g["id"],
                expiry_bars=10, expected_hold_bars=20))
        return out


class VWAPReversion(Strategy):
    spec = StrategySpec(
        "vwap_reversion", "VWAP band reversion", "mean_reversion",
        "In ranging conditions fade 2σ excursions from session VWAP once price closes back inside the band.",
        ["range", "low_volatility"], ["ohlcv"], "Close back inside the 2σ band after a close outside, RSI extreme.",
        "TP1 at VWAP; TP2/TP3 at 2R/3R.", "Further close beyond the band.", "Beyond excursion extreme ± 0.2×ATR.",
        "VWAP", "B")

    def evaluate(self, snap):
        ind = snap["indicators"]
        bars = snap["_recent"]
        vw = snap.get("_vwap")
        if not vw or not ind["atr14"]:
            return []
        a, c = ind["atr14"], ind["close"]
        out = []
        for side in ("LONG", "SHORT"):
            s = 1 if side == "LONG" else -1
            band = vw["lower"] if s > 0 else vw["upper"]
            prev_out = any((cl < b) if s > 0 else (cl > b) for cl, b in zip(bars["close"][-4:-1], vw["band_hist"][s > 0]))
            back_in = (c > band) if s > 0 else (c < band)
            rsi = ind["rsi14"] or 50
            ext = min(bars["low"][-4:]) if s > 0 else max(bars["high"][-4:])
            stop = ext - s * 0.2 * a
            tgt = [vw["vwap"]] + _targets(c, stop, side, (2.0, 3.0))[:2]
            out.append(SetupCandidate(
                "vwap_reversion", side,
                [C("ranging regime", "context", _regime(snap) in self.spec.regimes, _regime(snap)),
                 C("close beyond 2σ band", "setup", prev_out, f"band {band:.6g}"),
                 C("RSI stretched", "setup", rsi < 35 if s > 0 else rsi > 65, f"RSI {rsi:.1f}"),
                 C("close back inside band", "trigger", prev_out and back_in, "")],
                min(c, c - s * 0.1 * a), max(c, c - s * 0.1 * a), stop, tgt, f"close beyond {ext:.6g}",
                f"vwap:{side}:{snap['as_of'] // 86_400_000}", expiry_bars=4, expected_hold_bars=12))
        return out


class VolatilityBreakout(Strategy):
    spec = StrategySpec(
        "volatility_breakout", "Volatility squeeze breakout", "volatility_breakout",
        "Bollinger Bands inside Keltner Channels (squeeze) followed by an expansion close beyond the band with volume.",
        ["low_volatility", "range", "breakout", "weak_bull", "weak_bear"], ["ohlcv"],
        "Squeeze released within 3 bars and close beyond the BB with RVOL > 1.5.", "1.5R/2.5R/4R.",
        "Close back inside the bands' basis.", "Opposite side of the squeeze range.", "R multiples", "B")

    def evaluate(self, snap):
        ind = snap["indicators"]
        bars = snap["_recent"]
        sq = snap.get("_squeeze_hist") or []
        if len(sq) < 6 or not ind["atr14"]:
            return []
        a, c = ind["atr14"], ind["close"]
        was_on = any(x == 1 for x in sq[-6:-1])
        released = sq[-1] == 0 and was_on
        pb = ind["bb_percent_b"] or 0.5
        rv = ind["rvol20"] or 0
        hi, lo = max(bars["high"][-10:-1]), min(bars["low"][-10:-1])
        out = []
        for side in ("LONG", "SHORT"):
            s = 1 if side == "LONG" else -1
            stop = lo if s > 0 else hi
            if s * (c - stop) <= 0.3 * a:
                stop = c - s * 1.5 * a
            out.append(SetupCandidate(
                "volatility_breakout", side,
                [C("regime", "context", _regime(snap) in self.spec.regimes, _regime(snap)),
                 C("recent squeeze", "setup", was_on or sq[-1] == 1, "BB inside KC"),
                 C("squeeze released", "trigger", released, ""),
                 C("close beyond band", "trigger", pb > 1 if s > 0 else pb < 0, f"%B {pb:.2f}"),
                 C("relative volume > 1.5", "trigger", rv > 1.5, f"RVOL {rv:.2f}")],
                min(c, c - s * 0.1 * a), max(c, c - s * 0.1 * a), stop, _targets(c, stop, side),
                f"close back inside {('below' if s > 0 else 'above')} basis", f"sq:{side}:{snap['as_of'] // 86_400_000}",
                expiry_bars=3, expected_hold_bars=20))
        return out


class FundingExtremeFade(Strategy):
    spec = StrategySpec(
        "funding_extreme_fade", "Funding extreme fade", "derivatives_positioning",
        "When perpetual funding is extreme (crowded positioning) and price structure shifts against the crowd, "
        "trade the unwind.", ["weak_bull", "weak_bear", "range", "high_volatility"], ["ohlcv", "funding", "open_interest"],
        "Internal CHoCH against the crowded side while funding |z| > 2.5.", "1.5R/2.5R/4R.",
        "Funding normalises before trigger.", "Beyond the last swing extreme ± 0.3×ATR.", "R multiples", "B",
        backtestable=False)

    def evaluate(self, snap):
        d = snap.get("derivatives") or {}
        smc = snap.get("smc") or {}
        if not d.get("available") or d.get("funding_rate") is None or "error" in smc:
            return []
        ind = snap["indicators"]
        a, c = ind["atr14"], ind["close"]
        fr = d["funding_rate"]
        s = -1 if fr > 0 else 1
        side = "LONG" if s > 0 else "SHORT"
        bars = snap["_recent"]
        shift = any(e["scope"] == "internal" and e["direction"] == ("bull" if s > 0 else "bear")
                    and e["break_time"] >= bars["time"][-6] for e in smc.get("structure", []))
        ext = min(bars["low"][-10:]) if s > 0 else max(bars["high"][-10:])
        stop = ext - s * 0.3 * a
        return [SetupCandidate(
            "funding_extreme_fade", side,
            [C("regime", "context", _regime(snap) in self.spec.regimes, _regime(snap)),
             C("funding extreme", "setup", bool(d.get("funding_extreme")),
               f"{fr * 100:.4f}% z={d.get('funding_z') or 0:.1f}"),
             C("OI elevated/rising", "setup", (d.get("oi_change_24h_pct") or 0) > 0, f"{d.get('oi_change_24h_pct')}"),
             C("structure shift against crowd", "trigger", shift, "")],
            min(c, c - s * 0.1 * a), max(c, c - s * 0.1 * a), stop, _targets(c, stop, side),
            f"trade beyond {ext:.6g}", f"fund:{side}:{snap['as_of'] // 86_400_000}", expiry_bars=8)]


STRATEGIES: dict[str, Strategy] = {s.spec.id: s for s in [
    TrendPullback(), SMCOrderBlockRetest(), LiquiditySweepReversal(), BreakoutRetest(), FVGContinuation(),
    VWAPReversion(), VolatilityBreakout(), FundingExtremeFade(),
]}
