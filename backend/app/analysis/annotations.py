"""Convert structured analysis into editable chart objects (times in UTC seconds for the chart).

Object ids are deterministic (derived from concept + anchor candles), so re-running analysis yields the same
id for the same detection; the frontend uses this to keep user edits separate from fresh AI output."""

GREEN, RED, BLUE, AMBER, PURPLE, GREY = "#22c55e", "#ef4444", "#3b82f6", "#f59e0b", "#a855f7", "#94a3b8"


def _s(ms: int | None) -> int | None:
    return None if ms is None else int(ms) // 1000


def _zone(id, concept, t1, t2, top, bot, color, label, status, explain):
    return {"id": id, "kind": "zone", "concept": concept, "time1": _s(t1), "time2": _s(t2), "price1": top,
            "price2": bot, "color": color, "label": label, "status": status, "explain": explain, "origin": "ai"}


MAX_OB_PER_SIDE = 3
MAX_BREAKERS = 2
MAX_FVG = 5
MAX_LIQ_PER_SIDE = 3
MAX_EXT_STRUCTURE = 6
MAX_MSS_INTERNAL = 3


def _declutter(smc: dict, price: float) -> dict:
    """Keep the chart readable: show only the most relevant objects. Full detail stays in the snapshot."""
    s = dict(smc)
    obs = [o for o in smc.get("order_blocks", []) if o["status"] != "invalidated"]
    s["order_blocks"] = [o for d in ("bullish", "bearish")
                         for o in sorted([x for x in obs if x["direction"] == d], key=lambda o: -o["formed_time"])[:MAX_OB_PER_SIDE]]
    s["breakers"] = sorted([b for b in smc.get("breakers", []) if b["status"] != "invalidated"],
                           key=lambda b: -b["formed_time"])[:MAX_BREAKERS]
    s["mitigation_blocks"] = [m for m in smc.get("mitigation_blocks", []) if m["status"] != "invalidated"][-1:]
    s["fvgs"] = [g for g in smc.get("fvgs", []) if g["status"] in ("open", "partially_filled", "active")][-MAX_FVG:]
    resting = [lq for lq in smc.get("liquidity", []) if lq["status"] == "resting"]
    above = sorted([lq for lq in resting if lq["price"] > price], key=lambda lq: lq["price"])
    below = sorted([lq for lq in resting if lq["price"] <= price], key=lambda lq: -lq["price"])

    def uniq(levels):
        out, seen = [], []
        for lq in levels:
            if all(abs(lq["price"] - p) / price > 0.0008 for p in seen):
                out.append(lq)
                seen.append(lq["price"])
        return out[:MAX_LIQ_PER_SIDE]

    s["liquidity"] = uniq(above) + uniq(below)
    ext = [e for e in smc.get("structure", []) if e["scope"] == "external"][-MAX_EXT_STRUCTURE:]
    mss = [e for e in smc.get("structure", []) if e["scope"] == "internal" and e.get("mss")][-MAX_MSS_INTERNAL:]
    s["structure"] = ext + mss
    s["sweeps"] = smc.get("sweeps", [])[-6:]
    return s


def build_annotations(snap: dict, tf: str) -> list[dict]:
    out: list[dict] = []
    smc = snap.get("smc") or {}
    smc = {} if "error" in smc else _declutter(smc, snap["price"])
    lim = "Pattern geometry only; does not verify institutional intent."
    for ob in smc.get("order_blocks", []) + smc.get("breakers", []) + smc.get("mitigation_blocks", []):
        if ob["status"] == "invalidated" and ob["type"] == "order_block":
            continue
        bull = ob["direction"] == "bullish"
        name = {"order_block": "OB", "breaker": "Breaker", "mitigation_block": "Mitigation"}[ob["type"]]
        out.append(_zone(ob["id"], ob["type"], ob["time"], ob.get("invalidated_time"), ob["top"], ob["bottom"],
                         GREEN if bull else RED, f"{'Bull' if bull else 'Bear'} {name} ({tf})", ob["status"], {
                             "criteria": ("Last opposite-colour candle before the displacement leg that produced a "
                                          f"{ob['event_type']} ({ob['scope']} structure)") if ob["type"] == "order_block"
                             else "Order block that failed (close beyond far side) and flipped polarity",
                             "supporting_candles": [_s(ob["time"])], "timeframe": tf,
                             "detected_at": _s(ob["formed_time"]),
                             "invalidation": ob["bottom"] if bull else ob["top"],
                             "relevance": "Potential reaction zone if price revisits", "limitations": lim}))
    for g in smc.get("fvgs", []):
        if g["status"] in ("filled", "invalidated") or g.get("status") == "inverted":
            continue
        bull = g["direction"] == "bullish"
        nm = "FVG" if g["type"] == "fvg" else "IFVG"
        out.append(_zone(g["id"], g["type"], g["time"], g.get("filled_time"), g["top"], g["bottom"],
                         "#10b98155" if bull else "#f8717155", f"{nm} {'↑' if bull else '↓'} {g['fill_pct']:.0%}",
                         g["status"], {"criteria": "3-candle imbalance: candle 3 low > candle 1 high (bullish) or "
                                       "mirror; min size 0.1×ATR", "timeframe": tf,
                                       "detected_at": _s(g["formed_time"]),
                                       "size_atr": round(g.get("size_atr", 0), 2), "fill_pct": g["fill_pct"],
                                       "limitations": "Gaps are often partially or fully filled; not guaranteed."}))
    for v in smc.get("liquidity_voids", []):
        if v["status"] == "open":
            out.append(_zone(v["id"], "liquidity_void", v["time"], None, v["top"], v["bottom"], "#64748b44",
                             "Liquidity void", "open", {"criteria": "range ≥ 2.5×ATR, body/range ≥ 0.8",
                                                        "timeframe": tf}))
    for e in smc.get("structure", []):
        if e["scope"] == "internal" and not e.get("mss"):
            continue
        lbl = ("MSS" if e.get("mss") else e["type"]) + ("" if e["scope"] == "external" else " (i)")
        out.append({"id": e["id"], "kind": "trendline", "concept": "structure_break", "time1": _s(e["swing_time"]),
                    "price1": e["level"], "time2": _s(e["break_time"]), "price2": e["level"],
                    "color": GREEN if e["direction"] == "bull" else RED, "label": lbl, "status": "confirmed",
                    "style": "dashed", "origin": "ai",
                    "explain": {"criteria": f"Candle close beyond confirmed {e['scope']} swing "
                                f"{'high' if e['direction'] == 'bull' else 'low'}",
                                "displacement": e["displacement"], "timeframe": tf,
                                "detected_at": _s(e["break_time"])}})
    for s in smc.get("sweeps", []):
        out.append({"id": s["id"], "kind": "marker", "concept": "liquidity_sweep", "time1": _s(s["time"]),
                    "price1": s["price"], "position": "above" if s["side"] == "buy" else "below",
                    "shape": "arrowDown" if s["bias"] == "bearish" else "arrowUp",
                    "color": AMBER, "label": ("Sweep " if s["kind"] == "sweep" else "Run ") + s["source"],
                    "status": s["kind"], "origin": "ai",
                    "explain": {"criteria": "Wick beyond resting liquidity level then close back inside (sweep) "
                                "or close through (run)", "level": s["price"], "side": s["side"],
                                "stop_hunt_candidate": s["stop_hunt_candidate"], "timeframe": tf,
                                "limitations": "Resting stop orders are inferred, not observed."}})
    for lq in smc.get("liquidity", []):
        if lq["status"] != "resting":
            continue
        out.append({"id": lq["id"], "kind": "hline", "concept": lq["type"], "time1": _s(lq["time"]),
                    "price1": lq["price"], "color": "#eab30899", "label": f"{'BSL' if 'buy' in lq['type'] else 'SSL'} "
                    f"{lq['source']}", "status": "resting", "style": "dotted", "origin": "ai",
                    "explain": {"criteria": "Unswept swing/reference extreme where stops are likely to rest",
                                "source": lq["source"], "timeframe": tf}})
    for eq in smc.get("equal_levels", []):
        if eq["status"] == "resting":
            out.append({"id": eq["id"], "kind": "trendline", "concept": eq["type"], "time1": _s(eq["time1"]),
                        "price1": eq["price"], "time2": _s(eq["time2"]), "price2": eq["price"], "color": AMBER,
                        "label": "EQH" if eq["type"] == "equal_highs" else "EQL", "status": "resting",
                        "origin": "ai", "explain": {"criteria": "Two swing extremes within 0.1×ATR", "timeframe": tf}})
    pdz = smc.get("premium_discount")
    if pdz:
        out.append(_zone(pdz["id"] + "p", "premium_zone", pdz["time1"], None, pdz["range_high"], pdz["equilibrium"],
                         "#ef444414", "Premium", pdz["zone"], {"criteria": "Upper half of current external dealing range"}))
        out.append(_zone(pdz["id"] + "d", "discount_zone", pdz["time1"], None, pdz["equilibrium"], pdz["range_low"],
                         "#22c55e14", "Discount", pdz["zone"], {"criteria": "Lower half of current external dealing range"}))
        o = pdz["ote"]
        out.append(_zone(pdz["id"] + "o", "ote", pdz["time1"], None, o["top"], o["bottom"], "#a855f722",
                         f"OTE {o['direction']}", "active", {"criteria": "62–79% retracement of the latest external leg"}))
    idm = smc.get("inducement")
    if idm:
        out.append({"id": idm["id"], "kind": "hline", "concept": "inducement", "time1": _s(idm["time"]),
                    "price1": idm["price"], "color": PURPLE, "label": f"IDM ({idm['status']})", "style": "dotted",
                    "status": idm["status"], "origin": "ai",
                    "explain": {"criteria": "First internal swing after the last external break, outside the "
                                "active order block — a likely liquidity target before continuation"}})
    pa = snap.get("price_action") or {}
    for lvl in pa.get("levels", [])[:6]:
        out.append(_zone(lvl["id"], "support_resistance", lvl["first_time"], None, lvl["high"], lvl["low"],
                         "#3b82f622", f"{lvl['type'].title()} ×{lvl['touches']}", lvl["type"],
                         {"criteria": "Cluster of swing pivots within 0.5×ATR", "touches": lvl["touches"],
                          "strength": lvl["strength"]}))
    for tl in pa.get("trendlines", []):
        out.append({"id": tl["id"], "kind": "trendline", "concept": tl["kind"], "time1": _s(tl["time1"]),
                    "price1": tl["price1"], "time2": _s(tl["time2"]), "price2": tl["price2"], "extend": True,
                    "color": BLUE, "label": tl["kind"].replace("_", " "), "status": tl["status"], "origin": "ai",
                    "explain": {"criteria": "Line through the two most recent confirming swing pivots"}})
    fib = pa.get("fib")
    if fib:
        out.append({"id": fib["id"], "kind": "fib", "concept": "fibonacci", "time1": _s(fib["time1"]),
                    "price1": fib["price1"], "time2": _s(fib["time2"]), "price2": fib["price2"],
                    "levels": [float(k) for k in fib["retracements"]], "color": GREY, "label": "Fib (last leg)",
                    "status": "active", "origin": "ai", "explain": {"criteria": "Last confirmed external swing leg"}})
    ew = (snap.get("elliott") or {}).get("counts", [])
    for c in ew[:2]:
        out.append({"id": c["id"], "kind": "wave", "concept": "elliott_" + c["pattern"], "role": c["role"],
                    "points": [{"time": _s(w["time"]), "price": w["price"], "label": w["label"]} for w in c["waves"]],
                    "color": "#38bdf8" if c["role"] == "primary" else "#64748b",
                    "label": f"EW {c['role']}: {c['pattern']} (score {c['score']})", "status": c["role"],
                    "origin": "ai", "invalidation": c.get("invalidation"),
                    "explain": {"criteria": c["explanation"], "rules": c["rules_passed"],
                                "guidelines": c["guidelines"], "calibrated": False,
                                "limitations": "Subjective; alternative counts exist."}})
    return out
