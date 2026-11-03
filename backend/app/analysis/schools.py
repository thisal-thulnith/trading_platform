"""Classic schools of technical analysis, each reduced to deterministic rules on OHLCV candles.

Wyckoff, Dow Theory, Gann angles, Market Profile, Harmonic patterns, ICT Power of 3 / sessions, NeoWave (simplified
Neely rules), Hurst cycles, Quasimodo and Bill Williams fractals. Every function returns a step in the same shape as
analysis/walkthrough.py (`_step`), so the War Room agents can present them like any other concept.

These are mechanical approximations of discretionary methods: where a school is subjective (Gann chart scale,
NeoWave counting) the rule used is stated in the observation. Their directional value is measured, not assumed:
see app/maintenance/school_study.py and docs/STRATEGIES.md#schools-of-thought.
"""
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.analysis.indicators.volume import volume_profile
from app.analysis.walkthrough import _p, _step

COLORS = {"wyckoff": "#c084fc", "dow": "#60a5fa", "gann": "#fbbf24", "profile": "#34d399", "harmonic": "#f472b6",
          "po3": "#22d3ee", "neowave": "#a3e635", "hurst": "#fb7185", "quasimodo": "#f97316", "fractals": "#e879f9"}


# --------------------------------------------------------------------------------------------- helpers

def _t(df: pd.DataFrame, i: int) -> int:
    return int(df["open_time"].iloc[i]) // 1000


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    h, lo, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    v = float(tr.rolling(n).mean().iloc[-1])
    return v if np.isfinite(v) and v > 0 else float((h - lo).mean()) or 1e-9


def fractals(df: pd.DataFrame, n: int = 2) -> list[tuple[int, str, float]]:
    """Bill Williams fractals: a high (low) above (below) the n bars on each side — strictly on the left, ties allowed
    on the right so an equal-high double top still counts once. Confirmed only."""
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    out = []
    for i in range(n, len(df) - n):
        if h[i] > h[i - n:i].max() and h[i] >= h[i + 1:i + n + 1].max():
            out.append((i, "high", float(h[i])))
        if lo[i] < lo[i - n:i].min() and lo[i] <= lo[i + 1:i + n + 1].min():
            out.append((i, "low", float(lo[i])))
    return out


def zigzag(df: pd.DataFrame, k: float = 2.5) -> list[tuple[int, str, float]]:
    """Alternating swing points; a reversal must exceed k x ATR. The last point is provisional (still extending)."""
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    th = k * _atr(df)
    piv: list[list] = []
    up = None
    hi_i, lo_i = 0, 0
    for i in range(1, len(df)):
        if h[i] > h[hi_i]:
            hi_i = i
        if lo[i] < lo[lo_i]:
            lo_i = i
        if up is None:
            if h[hi_i] - lo[lo_i] >= th:
                up = hi_i > lo_i
                piv.append([lo_i, "low", lo[lo_i]] if up else [hi_i, "high", h[hi_i]])
                piv.append([hi_i, "high", h[hi_i]] if up else [lo_i, "low", lo[lo_i]])
            continue
        last = piv[-1]
        if up:
            if h[i] >= last[2]:
                piv[-1] = [i, "high", h[i]]
            elif last[2] - lo[i] >= th:
                piv.append([i, "low", lo[i]])
                up = False
        else:
            if lo[i] <= last[2]:
                piv[-1] = [i, "low", lo[i]]
            elif h[i] - last[2] >= th:
                piv.append([i, "high", h[i]])
                up = True
    return [(int(i), k_, float(p)) for i, k_, p in piv]


def _wave(df, pts, labels, color, concept, label, role=None, invalidation=None) -> dict:
    return {"id": f"school_{concept}_wave", "kind": "wave", "origin": "ai", "concept": concept, "color": color,
            "label": label, "role": role, "invalidation": invalidation,
            "points": [{"time": _t(df, i), "price": p, "label": lab} for (i, _, p), lab in zip(pts, labels)]}


def _hline(id_, price, color, label, concept, time1=None, style="dashed") -> dict:
    return {"id": id_, "kind": "hline", "origin": "ai", "concept": concept, "time1": time1, "price1": price,
            "color": color, "label": label, "style": style}


def _zone(id_, t1, t2, p1, p2, color, label, concept) -> dict:
    return {"id": id_, "kind": "zone", "origin": "ai", "concept": concept, "time1": t1, "time2": t2,
            "price1": max(p1, p2), "price2": min(p1, p2), "color": color + "2e", "label": label}


def _marker(id_, t, price, up, color, label, concept) -> dict:
    return {"id": id_, "kind": "marker", "origin": "ai", "concept": concept, "time1": t, "price1": price,
            "shape": "arrowUp" if up else "arrowDown", "position": "below" if up else "above", "color": color,
            "label": label}


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


# --------------------------------------------------------------------------------------------- 1. Wyckoff

def wyckoff(df: pd.DataFrame) -> dict:
    w = df.tail(240).reset_index(drop=True)
    n, a, c = len(w), _atr(w), w["close"].to_numpy()
    col = COLORS["wyckoff"]
    plain = ("Wyckoff sees a cycle driven by large operators (the 'Composite Man'): Accumulation (quiet buying in a "
             "range) → Markup → Distribution (quiet selling in a range) → Markdown. A Spring (dip below range support "
             "that snaps back) or an Upthrust (poke above resistance that fails) is the trap before the real move.")
    if n < 120:
        return _step("wyckoff", "Wyckoff cycle", plain, "Not enough candles for a Wyckoff read.", learn="wyckoff")
    look = 80
    sup, res = float(w["low"].iloc[-look:-5].min()), float(w["high"].iloc[-look:-5].max())
    prior = (c[-look] - c[max(0, n - 200)]) / a
    width = (res - sup) / a
    rec = w.iloc[-10:]
    spring_i = next((i for i in range(n - 10, n) if w["low"].iloc[i] < sup), None) if c[-1] > sup else None
    ut_i = next((i for i in range(n - 10, n) if w["high"].iloc[i] > res), None) if c[-1] < res else None
    up_v = w["volume"].iloc[-40:][w["close"].iloc[-40:] > w["open"].iloc[-40:]].mean()
    dn_v = w["volume"].iloc[-40:][w["close"].iloc[-40:] <= w["open"].iloc[-40:]].mean()
    vr = float(up_v / dn_v) if dn_v and np.isfinite(up_v) else 1.0
    if c[-1] > res:
        phase, v = "Markup — price closed above the trading range (Sign of Strength)", 1
    elif c[-1] < sup:
        phase, v = "Markdown — price closed below the trading range (Sign of Weakness)", -1
    elif spring_i is not None and rec["close"].iloc[-1] > sup:
        phase, v = "Accumulation, Phase C — a Spring swept range support and price snapped back inside", 1
    elif ut_i is not None:
        phase, v = "Distribution, Phase C — an Upthrust poked above resistance and failed back inside", -1
    elif prior < -3:
        phase, v = "Possible Accumulation (Phase B) — a sideways range after a downtrend; waiting for a Spring", 0
    elif prior > 3:
        phase, v = "Possible Distribution (Phase B) — a sideways range after an uptrend; waiting for an Upthrust", 0
    else:
        phase, v = "Re-accumulation or re-distribution — range without a clear prior trend", 0
    bullets = [f"Trading range of the last {look} bars: support {_p(sup)} – resistance {_p(res)} ({width:.1f} ATR wide)",
               f"Trend before the range: {prior:+.1f} ATR ({'down' if prior < 0 else 'up'})",
               f"Effort vs result: volume on up-candles is {vr:.2f}× volume on down-candles "
               + ("(buyers putting in effort)" if vr > 1.15 else "(sellers putting in effort)" if vr < 0.87 else "(balanced)")]
    objs = [_zone("school_wyckoff_tr", _t(w, n - look), _t(w, n - 1), res, sup, col, "Wyckoff trading range", "wyckoff")]
    if spring_i is not None and v == 1 and c[-1] <= res:
        objs.append(_marker("school_wyckoff_spring", _t(w, spring_i), float(w["low"].iloc[spring_i]), True, col,
                            "Spring", "wyckoff"))
    if ut_i is not None and v == -1 and c[-1] >= sup:
        objs.append(_marker("school_wyckoff_ut", _t(w, ut_i), float(w["high"].iloc[ut_i]), False, col, "Upthrust",
                            "wyckoff"))
    return _step("wyckoff", "Wyckoff cycle", plain, f"{phase}.", v, objects=objs, bullets=bullets, learn="wyckoff")


# --------------------------------------------------------------------------------------------- 2. Dow Theory

def _hl_labels(piv):
    labs, last = [], {"high": None, "low": None}
    for _, k, p in piv:
        prev = last[k]
        labs.append(("H" if k == "high" else "L") if prev is None else
                    ("HH" if p > prev else "LH") if k == "high" else ("HL" if p > prev else "LL"))
        last[k] = p
    return labs


def dow(df: pd.DataFrame) -> dict:
    w = df.tail(300).reset_index(drop=True)
    col = COLORS["dow"]
    plain = ("Dow Theory is where market structure comes from: an uptrend makes higher highs and higher lows, a "
             "downtrend lower highs and lower lows, and a trend is assumed to continue until it clearly breaks. It "
             "separates the primary trend from secondary reactions (pullbacks) and wants volume to confirm the trend.")
    major = fractals(w, 5)
    minor = fractals(w.tail(60).reset_index(drop=True), 2)
    hs = [p for p in major if p[1] == "high"][-2:]
    ls = [p for p in major if p[1] == "low"][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return _step("dow", "Dow Theory trend", plain, "Not enough confirmed swings for a primary trend.", learn="dow")
    hh, hl = hs[1][2] > hs[0][2], ls[1][2] > ls[0][2]
    price = float(w["close"].iloc[-1])
    if hh and hl:
        prim, v = "uptrend (higher highs and higher lows)", 1
        if price < ls[1][2]:
            prim, v = "uptrend BROKEN — price closed below the last higher low", 0
    elif not hh and not hl:
        prim, v = "downtrend (lower highs and lower lows)", -1
        if price > hs[1][2]:
            prim, v = "downtrend BROKEN — price closed above the last lower high", 0
    else:
        prim, v = "no primary trend (mixed highs and lows)", 0
    mh = [p for p in minor if p[1] == "high"][-2:]
    ml = [p for p in minor if p[1] == "low"][-2:]
    sec = 0
    if len(mh) == 2 and len(ml) == 2:
        sec = 1 if mh[1][2] > mh[0][2] and ml[1][2] > ml[0][2] else -1 if mh[1][2] < mh[0][2] and ml[1][2] < ml[0][2] else 0
    last50 = w.tail(50)
    with_t = last50["volume"][(last50["close"] > last50["open"]) == (v >= 0)].mean()
    against = last50["volume"][(last50["close"] > last50["open"]) != (v >= 0)].mean()
    bullets = [f"Primary swings: highs {_p(hs[0][2])} → {_p(hs[1][2])}, lows {_p(ls[0][2])} → {_p(ls[1][2])}",
               "Secondary (short-term) swings: " + {1: "rising", -1: "falling", 0: "mixed"}[sec]
               + (" — a secondary reaction against the primary trend" if v and sec == -v else "")]
    if v and np.isfinite(with_t) and np.isfinite(against) and against:
        bullets.append(f"Volume confirmation: candles with the trend carry {with_t / against:.2f}× the volume of candles "
                       "against it" + (" ✓" if with_t > against else " ✗ (not confirming)"))
    pts = sorted(hs + ls)[-4:]
    objs = [_wave(w, pts, _hl_labels(sorted(major)[-len(pts) - 2:])[-len(pts):], col, "dow", "Dow swings")]
    return _step("dow", "Dow Theory trend", plain, f"Primary trend: {prim}.", v, objects=objs, bullets=bullets,
                 learn="dow")


# --------------------------------------------------------------------------------------------- 3. Gann

def gann(df: pd.DataFrame) -> dict:
    w = df.tail(200).reset_index(drop=True)
    n, col = len(w), COLORS["gann"]
    plain = ("Gann treated price and time as one geometry. From a major high or low he drew angles: the 1×1 "
             "(one price unit per time unit, '45°') is the balance line; staying above a rising 1×1 shows strength, "
             "losing it shows weakness. Time counts from the pivot (45, 90, 144, 180 bars) mark possible turns.")
    lo_i, hi_i = int(w["low"].idxmin()), int(w["high"].idxmax())
    # scale: the 1x1 moves at the rate of the major swing between the window's extremes (price per bar)
    unit = (float(w["high"].iloc[hi_i] - w["low"].iloc[lo_i]) / abs(hi_i - lo_i) if hi_i != lo_i
            else float((w["high"] - w["low"]).median()))
    from_low = lo_i > hi_i
    i0 = lo_i if from_low else hi_i
    p0 = float(w["low"].iloc[i0] if from_low else w["high"].iloc[i0])
    bars = n - 1 - i0
    sgn = 1 if from_low else -1
    line = {k: p0 + sgn * m * unit * bars for k, m in (("2×1", 2), ("1×1", 1), ("1×2", 0.5))}
    price = float(w["close"].iloc[-1])
    if from_low:
        v = 1 if price >= line["1×1"] else 0 if price >= line["1×2"] else -1
        where = ("above the rising 1×1 — strong" if v == 1 else "between 1×1 and 1×2 — momentum fading" if v == 0
                 else "below the 1×2 — the rally from the low has lost its angle")
    else:
        v = -1 if price <= line["1×1"] else 0 if price <= line["1×2"] else 1
        where = ("below the falling 1×1 — weak" if v == -1 else "between 1×1 and 1×2 — selling pressure fading"
                 if v == 0 else "above the 1×2 — the decline from the high has lost its angle")
    near = [x for x in (45, 90, 144, 180) if abs(bars - x) <= 3]
    bullets = [f"Anchor: {'major low' if from_low else 'major high'} {_p(p0)}, {bars} bars ago",
               "Scale: 1×1 = " + _p(unit) + " per bar, the rate of the last major swing (Gann angles depend on scale; "
               "this is the rule we use)",
               ", ".join(f"{k} at {_p(val)}" for k, val in line.items())]
    if near:
        bullets.append(f"Time: {bars} bars from the pivot is near Gann count {near[0]} — watch for a turn")
    t0 = _t(w, i0)
    t1 = _t(w, n - 1)
    objs = [{"id": f"school_gann_{k}", "kind": "trendline", "origin": "ai", "concept": "gann", "time1": t0,
             "price1": p0, "time2": t1, "price2": val, "color": col, "label": f"Gann {k}",
             "style": "solid" if k == "1×1" else "dashed"} for k, val in line.items()]
    return _step("gann", "Gann angles & time", plain, f"Price is {where}.", v, objects=objs, bullets=bullets,
                 learn="gann")


# --------------------------------------------------------------------------------------------- 4. Market Profile

def market_profile(df: pd.DataFrame) -> dict:
    w = df.tail(200).reset_index(drop=True)
    col = COLORS["profile"]
    plain = ("Auction Market Theory: price moves to find where trade is accepted. The Point of Control (POC) is the "
             "price with the most volume; the Value Area holds ~70% of it. Accepted above value = buyers in control, "
             "below value = sellers; inside value the market rotates back toward the POC.")
    vp = volume_profile(w)
    poc, vah, val = vp.get("poc"), vp.get("vah"), vp.get("val")
    if poc is None or vah is None or val is None:
        return _step("profile", "Market Profile (value)", plain, "Volume profile unavailable.", learn="market-profile")
    closes = w["close"].tail(3).to_numpy()
    price = float(closes[-1])
    if (closes > vah).all():
        obs, v = "Accepted ABOVE value (3 closes over the Value Area High) — buyers control the auction", 1
    elif (closes < val).all():
        obs, v = "Accepted BELOW value (3 closes under the Value Area Low) — sellers control the auction", -1
    elif price > vah:
        obs, v = "Testing above value but not yet accepted — a failure back inside targets the POC", 0
    elif price < val:
        obs, v = "Testing below value but not yet accepted — a failure back inside targets the POC", 0
    else:
        obs, v = "Inside the Value Area — balanced auction; rotation toward the POC is the base case", 0
    bullets = [f"POC {_p(poc)} · Value Area {_p(val)} – {_p(vah)} (last {len(w)} bars)",
               f"Price is {(price / poc - 1) * 100:+.2f}% from the POC"]
    t0 = _t(w, 0)
    objs = [_zone("school_profile_va", t0, _t(w, len(w) - 1), vah, val, col, "Value Area", "profile"),
            _hline("school_profile_poc", poc, col, f"POC {_p(poc)}", "profile", t0, "solid")]
    return _step("profile", "Market Profile (value)", plain, obs + ".", v, objects=objs, bullets=bullets,
                 learn="market-profile")


# --------------------------------------------------------------------------------------------- 5. Harmonics

HARMONICS = {  # B retrace of XA, C retrace of AB, D retrace of XA (measured from A)
    "Gartley": ((0.55, 0.68), (0.382, 0.886), (0.75, 0.82)),
    "Bat": ((0.382, 0.52), (0.382, 0.886), (0.85, 0.92)),
    "Butterfly": ((0.75, 0.82), (0.382, 0.886), (1.2, 1.65)),
    "Crab": ((0.382, 0.618), (0.382, 0.886), (1.55, 1.7)),
}


def harmonics(df: pd.DataFrame) -> dict:
    w = df.tail(300).reset_index(drop=True)
    col, a = COLORS["harmonic"], _atr(w)
    plain = ("Harmonic patterns (Gartley, Bat, Butterfly, Crab) are X-A-B-C-D swings whose legs hit exact Fibonacci "
             "ratios. Point D marks a Potential Reversal Zone (PRZ). Rules are strict: if the ratios don't match, "
             "there is no pattern.")
    zz = zigzag(w, 2.0)
    price = float(w["close"].iloc[-1])
    for end in (len(zz), len(zz) - 1):  # the latest 5 swings, or the 5 before a still-extending swing
        if end < 5:
            continue
        X, A, B, C, D = zz[end - 5:end]
        xa, ab, bc = abs(A[2] - X[2]), abs(B[2] - A[2]), abs(C[2] - B[2])
        if min(xa, ab, bc) <= 0:
            continue
        rb, rc, rd = ab / xa, bc / ab, abs(A[2] - D[2]) / xa
        for name, (b, cc, d) in HARMONICS.items():
            if b[0] <= rb <= b[1] and cc[0] <= rc <= cc[1] and d[0] <= rd <= d[1]:
                bull = D[1] == "low"
                prz = (A[2] - (1 if A[2] > X[2] else -1) * d[0] * xa, A[2] - (1 if A[2] > X[2] else -1) * d[1] * xa)
                live = abs(price - D[2]) <= 2 * a and ((price > min(prz) - a) if bull else (price < max(prz) + a))
                v = (1 if bull else -1) if live else 0
                obs = (f"{'Bullish' if bull else 'Bearish'} {name} completed at D {_p(D[2])}"
                       + (" — price is still in the reversal zone." if live else " — price has already left the PRZ."))
                objs = [_wave(w, [X, A, B, C, D], ["X", "A", "B", "C", "D"], col, "harmonic", name),
                        _zone("school_harmonic_prz", _t(w, C[0]), _t(w, len(w) - 1), *prz, col, f"{name} PRZ", "harmonic")]
                return _step("harmonic", "Harmonic patterns", plain, obs, v, objects=objs, learn="harmonics",
                             bullets=[f"AB = {rb:.3f} XA, BC = {rc:.3f} AB, AD = {rd:.3f} XA (all inside {name} ratios)"])
    # no completed pattern: is X-A-B-C forming one?
    if len(zz) >= 4:
        X, A, B, C = zz[-4:]
        xa, ab = abs(A[2] - X[2]), abs(B[2] - A[2])
        if xa > 0 and ab > 0:
            rb, rc = ab / xa, abs(C[2] - B[2]) / ab
            for name, (b, cc, d) in HARMONICS.items():
                if b[0] <= rb <= b[1] and cc[0] <= rc <= cc[1]:
                    s = 1 if A[2] > X[2] else -1
                    prz = (A[2] - s * d[0] * xa, A[2] - s * d[1] * xa)
                    obs = (f"A {name} may be forming: X-A-B-C fit, the D completion (PRZ) would be {_p(min(prz))} – "
                           f"{_p(max(prz))}. No trade until price reaches it.")
                    objs = [_wave(w, [X, A, B, C], ["X", "A", "B", "C"], col, "harmonic", f"{name} (forming)",
                                  role="alternate"),
                            _zone("school_harmonic_prz", _t(w, C[0]), _t(w, len(w) - 1), *prz, col,
                                  f"Projected {name} PRZ", "harmonic")]
                    return _step("harmonic", "Harmonic patterns", plain, obs, 0, objects=objs, learn="harmonics",
                                 bullets=[f"AB = {rb:.3f} XA, BC = {rc:.3f} AB"])
    return _step("harmonic", "Harmonic patterns", plain, "No swing sequence matches Gartley, Bat, Butterfly or Crab "
                 "ratios right now — no harmonic signal.", 0, learn="harmonics")


# --------------------------------------------------------------------------------------------- 6. ICT Power of 3

SESSIONS = [("Asia", 0, 7), ("London", 7, 12), ("New York", 12, 21), ("Off-hours", 21, 24)]
KILLZONES = [("London open killzone", 7, 10), ("New York open killzone", 12, 15)]


def session_now(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    h = now.hour + now.minute / 60
    s = next(n for n, a, b in SESSIONS if a <= h < b)
    kz = next((n for n, a, b in KILLZONES if a <= h < b), None)
    return {"session": s, "killzone": kz, "utc": now.strftime("%H:%M")}


def po3(df: pd.DataFrame, tf: str, now: datetime | None = None) -> dict:
    col = COLORS["po3"]
    plain = ("ICT Power of 3 splits a day into Accumulation (a quiet range, usually Asia), Manipulation (a fake move "
             "that grabs liquidity beyond that range or the daily open) and Distribution (the real move the other way). "
             "Timing matters: the London and New York 'killzones' are when manipulation and expansion usually happen.")
    sn = session_now(now)
    d = df.copy()
    d["dt"] = pd.to_datetime(d["open_time"], unit="ms", utc=True)
    intraday = tf in ("1m", "5m", "15m", "30m", "1h")
    key = d["dt"].dt.date if tf != "1d" else d["dt"].dt.strftime("%G-%V")
    days = list(dict.fromkeys(key))
    if len(days) < 2:
        return _step("po3", "ICT Power of 3 & sessions", plain, "Not enough days of data.", learn="ict-po3")
    today = d[key == days[-1]]
    prev = d[key == days[-2]]
    period = "week" if tf == "1d" else "day"
    open_ = float(today["open"].iloc[0])
    price = float(d["close"].iloc[-1])
    t_open = int(today["open_time"].iloc[0]) // 1000
    objs = [_hline("school_po3_open", open_, col, f"{period.title()} open {_p(open_)}", "po3", t_open, "solid")]
    if intraday:
        asia = today[today["dt"].dt.hour < 7]
        after = today[today["dt"].dt.hour >= 7]
        if len(asia):
            lo_, hi_ = float(asia["low"].min()), float(asia["high"].max())
            objs.append(_zone("school_po3_asia", t_open, int(asia["open_time"].iloc[-1]) // 1000, hi_, lo_, col,
                              "Asia range (accumulation)", "po3"))
        else:
            lo_, hi_ = float(prev["low"].min()), float(prev["high"].max())
        ref = "Asia range"
        for name, hr in (("London", 7), ("New York", 12)):
            row = today[today["dt"].dt.hour == hr]
            if len(row):
                objs.append({"id": f"school_po3_{name}", "kind": "vline", "origin": "ai", "concept": "po3",
                             "time1": int(row["open_time"].iloc[0]) // 1000, "color": col + "99", "label": name,
                             "style": "dotted"})
    else:
        after = today
        lo_, hi_ = float(prev["low"].min()), float(prev["high"].max())
        ref = f"previous {period}'s range"
        objs += [_hline("school_po3_pdh", hi_, col, f"Prev {period} high", "po3", int(prev["open_time"].iloc[0]) // 1000),
                 _hline("school_po3_pdl", lo_, col, f"Prev {period} low", "po3", int(prev["open_time"].iloc[0]) // 1000)]
    # manipulation = whichever side of the range is taken FIRST; running through the other side later is the expansion
    lo_hits = after.index[after["low"] < lo_].tolist()
    hi_hits = after.index[after["high"] > hi_].tolist()
    first = None if not (lo_hits or hi_hits) else "low" if (lo_hits and (not hi_hits or lo_hits[0] < hi_hits[0])) else "high"
    if first == "low" and price > open_:
        obs, v = f"Bullish Power of 3: manipulation swept below the {ref} ({_p(lo_)}) first, and price is now distributing above the {period} open", 1
    elif first == "high" and price < open_:
        obs, v = f"Bearish Power of 3: manipulation swept above the {ref} ({_p(hi_)}) first, and price is now distributing below the {period} open", -1
    elif first:
        obs, v = (f"The {ref} {'low' if first == 'low' else 'high'} was swept, but price has not reclaimed the {period} "
                  f"open — the manipulation may still be the real move"), 0
    elif not len(after):
        obs, v = f"Still in accumulation (the {ref} is forming) — manipulation hasn't happened yet", 0
    else:
        obs, v = (f"No manipulation sweep yet: the {ref} {_p(lo_)} – {_p(hi_)} is intact. A sweep of one side that "
                  f"reverses through the {period} open would set up the move"), 0
    bullets = [f"Now: {sn['session']} session ({sn['utc']} UTC)" + (f" — inside the {sn['killzone']}" if sn["killzone"] else ""),
               f"{period.title()} open {_p(open_)}; price is {'above' if price > open_ else 'below'} it "
               f"({'premium' if price > open_ else 'discount'} of the {period})"]
    return _step("po3", "ICT Power of 3 & sessions", plain, obs + ".", v, objects=objs, bullets=bullets, learn="ict-po3")


# --------------------------------------------------------------------------------------------- 7. NeoWave

def neowave(df: pd.DataFrame) -> dict:
    w = df.tail(300).reset_index(drop=True)
    col = COLORS["neowave"]
    plain = ("NeoWave (Glenn Neely) makes Elliott Wave objective with hard rules. An impulse needs: wave 2 not "
             "retracing all of wave 1, wave 3 never the shortest, wave 4 not overlapping wave 1, one wave extended, and "
             "neighbouring waves similar in price or time (rule of similarity). Anything that fails is corrective.")
    zz = zigzag(w, 2.0)
    if len(zz) < 6:
        return _step("neowave", "NeoWave structure", plain, "Not enough monowaves for a count.", learn="neowave")
    pts = zz[-6:]
    legs = [pts[i + 1][2] - pts[i][2] for i in range(5)]
    L = [abs(x) for x in legs]
    T = [pts[i + 1][0] - pts[i][0] for i in range(5)]
    d = _sign(legs[0])
    p0, p1, p4 = pts[0][2], pts[1][2], pts[4][2]
    rules = {
        "wave 2 retraces < 100% of wave 1": L[1] < L[0],
        "wave 3 is not the shortest": L[2] > min(L[0], L[4]),
        "wave 4 does not overlap wave 1": (p4 > p1) if d > 0 else (p4 < p1),
        "one wave is extended (≥1.618× the next largest)": sorted([L[0], L[2], L[4]])[-1] >= 1.618 * sorted([L[0], L[2], L[4]])[-2],
        "rule of similarity (neighbours ≥ ⅓ in price or time)": all(
            min(L[i], L[i + 1]) >= max(L[i], L[i + 1]) / 3 or min(T[i], T[i + 1]) >= max(T[i], T[i + 1]) / 3 for i in range(4)),
    }
    passed = [k for k, ok in rules.items() if ok]
    failed = [k for k, ok in rules.items() if not ok]
    price = float(w["close"].iloc[-1])
    contracting = all(L[i + 1] < L[i] * 0.9 for i in range(4))
    if not failed:
        retr = abs(price - pts[5][2]) / L[4] if L[4] else 0
        v = d if retr < 0.382 else 0
        obs = (f"Valid {'bullish' if d > 0 else 'bearish'} impulse (all 5 Neely rules pass). "
               + ("Price is still near the end of wave 5 — the trend is intact, but a completed impulse is followed by "
                  "a correction." if v else "Price has retraced over 38.2% of wave 5 — the post-impulse correction has started."))
        labels, role = ["0", "1", "2", "3", "4", "5"], None
    elif contracting:
        v = 0
        obs = ("Contracting triangle (a-b-c-d-e): every swing is smaller than the last. Triangles are continuation "
               "patterns — the breakout of the e-wave usually follows the move that came before the triangle.")
        labels, role = ["", "a", "b", "c", "d", "e"], "alternate"
    else:
        v = 0
        obs = f"Corrective / non-impulsive structure — {len(failed)} of 5 impulse rules fail, so no trend count is valid."
        labels, role = ["", "a", "b", "c", "d", "e"], "alternate"
    bullets = [("✓ " if k in passed else "✗ ") + k for k in rules]
    objs = [_wave(w, pts, labels, col, "neowave", "NeoWave count", role=role,
                  invalidation=p0 if not failed else None)]
    return _step("neowave", "NeoWave structure", plain, obs, v, objects=objs, bullets=bullets, learn="neowave")


# --------------------------------------------------------------------------------------------- 8. Hurst

def hurst(df: pd.DataFrame) -> dict:
    w = df.tail(400).reset_index(drop=True)
    col = COLORS["hurst"]
    plain = ("Hurst found that prices move in nested time cycles (a long cycle contains two shorter ones, etc.). "
             "Cycles are measured trough to trough: shortly after a cycle trough prices tend to rise, and they tend "
             "to fall into the next expected trough. It times moves; it does not use price structure.")
    if len(w) < 200:
        return _step("hurst", "Hurst cycles", plain, "Not enough candles to measure cycles.", learn="hurst")
    y = np.log(w["close"].to_numpy())
    x = np.arange(len(y))
    y = y - np.polyval(np.polyfit(x, y, 1), x)
    best = None
    for P in range(12, 121):
        cs, sn = np.cos(2 * np.pi * x / P), np.sin(2 * np.pi * x / P)
        a_, b_ = np.linalg.lstsq(np.c_[cs, sn], y, rcond=None)[0]
        fit = a_ * cs + b_ * sn
        r2 = 1 - np.var(y - fit) / np.var(y) if np.var(y) else 0
        if best is None or r2 > best[1]:
            best = (P, r2, a_, b_)
    P, r2, a_, b_ = best
    phi = np.arctan2(-b_, a_)  # fit = R cos(2πx/P + phi)
    n = len(y) - 1
    ang = (2 * np.pi * n / P + phi) % (2 * np.pi)
    since = ((ang - np.pi) % (2 * np.pi)) / (2 * np.pi) * P  # bars since the last trough (cos = -1)
    frac = since / P
    if r2 < 0.08:
        v, obs = 0, f"No dominant cycle (best fit {P} bars explains only {r2:.0%} of the swings) — timing is unreliable"
    elif frac < 0.4:
        v, obs = 1, f"Rising phase: {since:.0f} bars after the last {P}-bar cycle trough"
    elif frac > 0.6:
        v, obs = -1, f"Falling phase: heading into the next {P}-bar cycle trough in ~{P - since:.0f} bars"
    else:
        v, obs = 0, f"Near the {P}-bar cycle crest — the up-phase is mature, the down-phase hasn't started"
    tr = int(round(n - since))
    bullets = [f"Dominant cycle ≈ {P} bars (explains {r2:.0%} of detrended movement)",
               f"Last trough ≈ {n - tr} bars ago; next trough expected in ≈ {max(0, P - since):.0f} bars",
               f"Half-cycle ≈ {P // 2} bars (Hurst's principle of harmonicity)"]
    objs = []
    for k, i in enumerate((tr, tr - P)):
        if 0 <= i < len(w):
            objs.append({"id": f"school_hurst_tr{k}", "kind": "vline", "origin": "ai", "concept": "hurst",
                         "time1": _t(w, i), "color": col, "label": "Cycle trough", "style": "dotted"})
    return _step("hurst", "Hurst cycles", plain, obs + ".", v, objects=objs, bullets=bullets, learn="hurst",
                 caution="Cycles drift in length; treat the timing window as approximate.")


# --------------------------------------------------------------------------------------------- 9. Quasimodo

def quasimodo(df: pd.DataFrame) -> dict:
    w = df.tail(300).reset_index(drop=True)
    col = COLORS["quasimodo"]
    plain = ("The Quasimodo (over-and-under) is a reversal shape: price sweeps the previous high (liquidity grab), "
             "then breaks the previous low (change of character). The left shoulder becomes the QM level — when price "
             "returns to it, traders look for the reversal. The pattern is invalid if price goes back beyond the head.")
    zz = zigzag(w, 2.0)
    price = float(w["close"].iloc[-1])
    a = _atr(w)
    for end in (len(zz), len(zz) - 1):
        if end < 4:
            continue
        ls, m1, head, m2 = zz[end - 4:end]
        if ls[1] == "high" and head[2] > ls[2] and m2[2] < m1[2]:  # bearish: higher high, then lower low
            level, v0, inval = ls[2], -1, head[2]
        elif ls[1] == "low" and head[2] < ls[2] and m2[2] > m1[2]:  # bullish: lower low, then higher high
            level, v0, inval = ls[2], 1, head[2]
        else:
            continue
        broken = price > inval if v0 < 0 else price < inval
        near = abs(price - level) <= 1.5 * a
        v = 0 if broken else v0
        obs = (f"{'Bearish' if v0 < 0 else 'Bullish'} Quasimodo: the head {_p(head[2])} swept the left shoulder, then "
               f"structure broke at {_p(m1[2])}. QM level {_p(level)}"
               + (" — INVALIDATED, price went back beyond the head." if broken else
                  " — price is at the QM level now." if near else " — waiting for price to return to it."))
        objs = [_wave(w, [ls, m1, head, m2], ["LS", "L" if v0 < 0 else "H", "Head", "LL" if v0 < 0 else "HH"], col,
                      "quasimodo", "Quasimodo"),
                _hline("school_qm_level", level, col, f"QM level {_p(level)}", "quasimodo", _t(w, ls[0]), "solid")]
        return _step("quasimodo", "Quasimodo (over & under)", plain, obs, v, objects=objs, learn="quasimodo",
                     bullets=[f"Invalidation beyond the head: {_p(inval)}"])
    return _step("quasimodo", "Quasimodo (over & under)", plain, "No Quasimodo in the latest swings (no sweep of the "
                 "prior extreme followed by a structure break).", 0, learn="quasimodo")


# --------------------------------------------------------------------------------------------- 10. Fractals

def bw_fractals(df: pd.DataFrame) -> dict:
    w = df.tail(200).reset_index(drop=True)
    col = COLORS["fractals"]
    plain = ("Bill Williams viewed markets as ordered chaos. A fractal is a 5-bar turning point (a high with two lower "
             "highs each side, or the mirror for lows) — a mechanical swing point. His Alligator (three smoothed "
             "averages) shows whether the market is trending ('eating') or sleeping; a break of the last fractal in the "
             "Alligator's direction is the entry signal.")
    fr = fractals(w, 2)
    med = (w["high"] + w["low"]) / 2
    smma = {n: med.ewm(alpha=1 / n, adjust=False).mean() for n in (13, 8, 5)}
    jaw, teeth, lips = float(smma[13].iloc[-9]), float(smma[8].iloc[-6]), float(smma[5].iloc[-4])
    ao = float(med.rolling(5).mean().iloc[-1] - med.rolling(34).mean().iloc[-1])
    ups = [f for f in fr if f[1] == "high"]
    dns = [f for f in fr if f[1] == "low"]
    if not ups or not dns:
        return _step("fractals", "Fractals & Alligator", plain, "No confirmed fractals yet.", learn="fractals")
    up, dn = ups[-1], dns[-1]
    price = float(w["close"].iloc[-1])
    awake_up, awake_dn = lips > teeth > jaw, lips < teeth < jaw
    if price > up[2] and awake_up:
        obs, v = f"Buy fractal broken ({_p(up[2])}) with the Alligator awake and pointing up", 1
    elif price < dn[2] and awake_dn:
        obs, v = f"Sell fractal broken ({_p(dn[2])}) with the Alligator awake and pointing down", -1
    elif not (awake_up or awake_dn):
        obs, v = "The Alligator is sleeping (lines intertwined) — no trend, fractal breaks are unreliable", 0
    else:
        obs, v = (f"Alligator {'up' if awake_up else 'down'}, waiting for a break of the "
                  f"{'up' if awake_up else 'down'} fractal at {_p(up[2] if awake_up else dn[2])}"), 0
    bullets = [f"Last up fractal {_p(up[2])}, last down fractal {_p(dn[2])}",
               f"Alligator: lips {_p(lips)}, teeth {_p(teeth)}, jaw {_p(jaw)}",
               f"Awesome Oscillator {'positive' if ao > 0 else 'negative'} ({'momentum up' if ao > 0 else 'momentum down'})"]
    objs = [_marker(f"school_fr_{i}", _t(w, i), p, k == "low", col, "fractal", "fractals") for i, k, p in fr[-8:]]
    objs += [_hline("school_fr_up", up[2], col, f"Up fractal {_p(up[2])}", "fractals", _t(w, up[0])),
             _hline("school_fr_dn", dn[2], col, f"Down fractal {_p(dn[2])}", "fractals", _t(w, dn[0]))]
    return _step("fractals", "Fractals & Alligator", plain, obs + ".", v, objects=objs, bullets=bullets,
                 learn="fractals")


# --------------------------------------------------------------------------------------------- all

SCHOOLS = ["wyckoff", "dow", "gann", "profile", "harmonic", "po3", "neowave", "hurst", "quasimodo", "fractals"]


def run_schools(df: pd.DataFrame, tf: str, now: datetime | None = None) -> list[dict]:
    """All school steps for one closed-candle frame. A failing analyser returns a neutral step, never an error."""
    fns = {"wyckoff": wyckoff, "dow": dow, "gann": gann, "profile": market_profile, "harmonic": harmonics,
           "po3": lambda d: po3(d, tf, now), "neowave": neowave, "hurst": hurst, "quasimodo": quasimodo,
           "fractals": bw_fractals}
    out = []
    for k in SCHOOLS:
        try:
            s = fns[k](df)
        except Exception as e:  # noqa: BLE001
            s = _step(k, k.title(), "", f"Analysis unavailable ({type(e).__name__}).", 0)
        s["timeframe"] = tf
        out.append(s)
    return out


def school_vote(steps: list[dict]) -> dict:
    bull = [s["key"] for s in steps if s["verdict"] > 0]
    bear = [s["key"] for s in steps if s["verdict"] < 0]
    net = (len(bull) - len(bear)) / max(len(steps), 1)
    return {"bull": bull, "bear": bear, "neutral": [s["key"] for s in steps if s["verdict"] == 0], "net": round(net, 2),
            "direction": "bullish" if net >= 0.2 else "bearish" if net <= -0.2 else "neutral"}
