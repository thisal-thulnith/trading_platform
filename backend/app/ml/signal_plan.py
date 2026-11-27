"""Today's signal: the coin where the trained models and the learned chart patterns point the same way, turned into a
complete trade plan (entry, stop, two targets, hold time, leverage with liquidation and dollar results for the user's
margin, the evidence with tested numbers, the risks, and when not to trade).

How it is built, and why it can be trusted only as far as its test:
1. backtest(): on every day of 2020-2026 the liquid coins are ranked with the models' out-of-sample scores (models
   trained only on earlier years) and the pattern features (earlier years only). The top coin for a long and for a short
   is traded with the plan below, candle by candle, on the daily high/low. Several ways of combining the evidence
   ("recipes") are compared on 2020-2024; the winner is judged on 2025-2026, which played no part in choosing it.
2. today(): today's top coin under that recipe, with the full plan.
3. plan_for(): the same plan for any coin the user picks.

The swing plan is the one the models were trained on: stop 1.5 ATR, first target 1.5 ATR (close half, stop to entry),
second target 3 ATR, held up to 14 days. The fast plan (stop 0.5 ATR, one target 1.5 ATR, up to 5 days) is the
higher-leverage option, and it is backtested the same way.

Run: uv run python -m app.ml.signal_plan        (backtest; seconds)
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from app.ml import forecast as fc
from app.risk import futures as fut

REPORT = fc.MODELS / "signal_backtest.json"
FEE = 0.001          # taker fee both sides, share of the position
PLANS = {
    "swing": {"stop_atr": 1.5, "tp1_atr": 1.5, "tp2_atr": 3.0, "days": 14,
              "label": "Swing: stop 1.5 ATR, TP1 1.5 ATR (close half, stop to entry), TP2 3 ATR, up to 14 days"},
    "fast": {"stop_atr": 0.5, "tp1_atr": 1.5, "tp2_atr": None, "days": 5,
             "label": "Fast: stop 0.5 ATR, one target 1.5 ATR, up to 5 days"},
}
# Ways to combine the evidence, written for a LONG; a SHORT swaps long/short and flips the other signs.
# (column, sign): + means a higher value favours the trade.
RECIPES: dict[str, list[tuple[str, int]]] = {
    "model_target": [("pred_long_tp_first", 1)],
    "model_net": [("pred_long_tp_first", 1), ("pred_short_tp_first", -1)],
    "patterns_net": [("analog_long_tp_first", 1), ("analog_short_tp_first", -1)],
    "model_and_patterns": [("pred_long_tp_first", 1), ("pred_short_tp_first", -1),
                           ("analog_long_tp_first", 1), ("analog_short_tp_first", -1)],
    "everything": [("pred_long_tp_first", 1), ("pred_short_tp_first", -1), ("analog_long_tp_first", 1),
                   ("analog_short_tp_first", -1), ("pred_outperform_7d", 1), ("pred_direction_7d", 1),
                   ("pred_drop_risk_7d", -1)],
}


def _side_cols(recipe: str, side: str) -> list[tuple[str, int]]:
    if side == "LONG":
        return RECIPES[recipe]
    swap = {"long_tp_first": "short_tp_first", "short_tp_first": "long_tp_first"}
    out = []
    for col, sgn in RECIPES[recipe]:
        for a, b in swap.items():
            if col.endswith(a):
                out.append((col[: -len(a)] + b, sgn))
                break
        else:
            out.append((col, -sgn))
    return out


def score(df: pd.DataFrame, recipe: str, side: str, by: str = "date") -> pd.Series:
    """Mean within-day percentile rank of the recipe's columns (oriented), 0..1; NaN-safe."""
    parts = []
    for col, sgn in _side_cols(recipe, side):
        if col not in df:
            continue
        parts.append((df[col] * sgn).groupby(df[by]).rank(pct=True))
    return pd.concat(parts, axis=1).mean(axis=1) if parts else pd.Series(np.nan, index=df.index)


# ------------------------------------------------------------------------------------------- trade simulation

def simulate(c, h, lo, atr, t: int, j: int, side: str, plan: dict) -> tuple[float, str, int]:
    """One trade entered at the close of day t on coin j; returns (R, outcome, days held). R is in units of the stop
    distance, after fees. A day touching both the stop and a target counts as the stop."""
    e, a = c[t, j], atr[t, j]
    if not (np.isfinite(e) and np.isfinite(a) and a > 0):
        return np.nan, "no_data", 0
    d = 1 if side == "LONG" else -1
    sd = plan["stop_atr"] * a
    stop, tp1 = e - d * sd, e + d * plan["tp1_atr"] * a
    tp2 = e + d * plan["tp2_atr"] * a if plan["tp2_atr"] else None
    fee_r = FEE * e / sd
    half = tp2 is not None
    banked, phase, last = 0.0, 0, e
    n = c.shape[0]
    for k in range(1, plan["days"] + 1):
        i = t + k
        if i >= n:
            return np.nan, "open", k
        hi, lw, cl = h[i, j], lo[i, j], c[i, j]
        if not np.isfinite(cl):
            continue
        last = cl
        adverse = (lw <= stop) if d == 1 else (hi >= stop)
        fav1 = (hi >= tp1) if d == 1 else (lw <= tp1)
        if phase == 0:
            if adverse:
                return -1.0 - fee_r, "stop", k
            if fav1:
                if not half:
                    return plan["tp1_atr"] / plan["stop_atr"] - fee_r, "target", k
                banked, phase, stop = 0.5 * plan["tp1_atr"] / plan["stop_atr"], 1, e
                back = (lw <= e) if d == 1 else (hi >= e)
                fav2 = (hi >= tp2) if d == 1 else (lw <= tp2)
                if back:
                    return banked - fee_r, "tp1_then_entry", k
                if fav2:
                    return banked + 0.5 * plan["tp2_atr"] / plan["stop_atr"] - fee_r, "tp2", k
                continue
        else:
            if (lw <= e) if d == 1 else (hi >= e):
                return banked - fee_r, "tp1_then_entry", k
            if (hi >= tp2) if d == 1 else (lw <= tp2):
                return banked + 0.5 * plan["tp2_atr"] / plan["stop_atr"] - fee_r, "tp2", k
    left = 0.5 if phase == 1 else 1.0
    return banked + left * d * (last - e) / sd - fee_r, "timeout", plan["days"]


def _stats(rs: list[tuple[float, str, int]]) -> dict:
    r = np.array([x[0] for x in rs if np.isfinite(x[0])])
    if not len(r):
        return {"trades": 0}
    oc = pd.Series([x[1] for x in rs if np.isfinite(x[0])]).value_counts(normalize=True)
    return {"trades": int(len(r)), "avg_r": round(float(r.mean()), 3), "median_r": round(float(np.median(r)), 3),
            "win_rate": round(float((r > 0).mean()), 3), "sum_r": round(float(r.sum()), 1),
            "outcomes": {k: round(float(v), 3) for k, v in oc.items()},
            "avg_days": round(float(np.mean([x[2] for x in rs if np.isfinite(x[0])])), 1)}


# ------------------------------------------------------------------------------------------- backtest

def _oos(tier: str) -> pd.DataFrame | None:
    p = fc._oos_path(tier)
    if not p.exists():
        return None
    z = np.load(p, allow_pickle=False)
    df = pd.DataFrame({k: z[k] for k in z.files})
    df["date"] = pd.to_datetime(df["date"])
    return df


TREND_MA = 50          # the market trend: BTC's daily close against its 50-day average
CONF_LEVELS = (None, 0.5, 0.8)   # trade only when the pick's own model score beats this share of past picks
MIN_SELECT_TRADES = 150
# A coin whose 14-day ATR is above this share of its price just pumped or crashed: a stop 1.5 ATR away would be 40%+
# and a short's TP2 can fall below zero (INIT on 2026-11-23: ATR 60%, stop +90%, TP2 negative). Such coins are not
# picked, in the backtest and live alike.
MAX_ATR_PCT = 25.0


def market_trend(btc_close: pd.Series) -> pd.Series:
    """True on days BTC closes above its 50-day average (longs allowed), False below (shorts allowed)."""
    return btc_close > btc_close.rolling(TREND_MA, min_periods=TREND_MA).mean()


def _own_col(side: str) -> str:
    return "pred_long_tp_first" if side == "LONG" else "pred_short_tp_first"


def backtest(tier: str = "core") -> dict:
    """Every combination of recipe x trend rule x confidence level, traded on the daily candles. The one used live is
    chosen on 2020-2024 only (best average R with at least 150 trades); 2025-2026 is reported as it came out."""
    t0 = time.time()
    df = _oos(tier)
    if df is None:
        return {"error": f"no out-of-sample predictions: retrain with `python -m app.ml.forecast train` ({tier})"}
    w = fc.load_wide()
    c, h, lo = (x.to_numpy(dtype=np.float64) for x in (w.close, w.high, w.low))
    atr = fc._atr(w).to_numpy(dtype=np.float64)
    trend = market_trend(w.close["BTCUSDT"])
    di = pd.Index(w.close.index).get_indexer(df["date"])
    si = pd.Index(w.close.columns).get_indexer(df["symbol"])
    df = df.assign(ti=di, sj=si)
    df = df[(df.ti >= 0) & (df.sj >= 0)].reset_index(drop=True)
    atr_pct = atr[df.ti.to_numpy(), df.sj.to_numpy()] / c[df.ti.to_numpy(), df.sj.to_numpy()] * 100
    df = df[~(atr_pct > MAX_ATR_PCT)].reset_index(drop=True)      # pumped / crashed coins are never picked
    df["year"] = df["date"].dt.year
    df["up"] = trend.reindex(df["date"]).fillna(False).to_numpy()
    SEL, HOLD = set(fc.SELECT_YEARS), set(fc.HOLDOUT_YEARS)
    out: dict = {"tier": tier, "made_at": pd.Timestamp.now("UTC").isoformat(), "plans": PLANS, "max_atr_pct": MAX_ATR_PCT,
                 "select_years": sorted(SEL), "holdout_years": sorted(HOLD), "trend_ma": TREND_MA,
                 "rule": f"chosen on {min(SEL)}-{max(SEL)} only: best average R with {MIN_SELECT_TRADES}+ trades",
                 "variants": {}, "baseline": {}}

    def split(rs, years_of):
        return (_stats([x for x, y in zip(rs, years_of) if y in SEL]),
                _stats([x for x, y in zip(rs, years_of) if y in HOLD]))

    # baseline: an average coin on the same kind of day (all days, or only days the trend allows that side)
    rng = np.random.default_rng(0)
    samp = df.iloc[rng.choice(len(df), size=min(20_000, len(df)), replace=False)]
    for side in ("LONG", "SHORT"):
        for pname, plan in PLANS.items():
            rs = [simulate(c, h, lo, atr, r.ti, r.sj, side, plan) for r in samp.itertuples()]
            allowed = samp["up"].to_numpy() if side == "LONG" else ~samp["up"].to_numpy()
            for flt, m in (("none", np.ones(len(samp), bool)), ("trend", allowed)):
                se, ho = split([x for x, k in zip(rs, m) if k], samp["year"].to_numpy()[m])
                out["baseline"][f"{side}_{pname}_{flt}"] = {"select": se, "holdout": ho}
    chosen = {}
    for side in ("LONG", "SHORT"):
        own = _own_col(side)
        for recipe in RECIPES:
            sc = score(df, recipe, side)
            ok = sc.dropna()
            top = df.loc[ok.groupby(df.loc[ok.index, "date"]).idxmax().to_numpy()]
            sims = {p: [simulate(c, h, lo, atr, r.ti, r.sj, side, plan) for r in top.itertuples()]
                    for p, plan in PLANS.items()}
            allowed = top["up"].to_numpy() if side == "LONG" else ~top["up"].to_numpy()
            for flt in ("none", "trend"):
                for q in CONF_LEVELS:
                    m = allowed.copy() if flt == "trend" else np.ones(len(top), bool)
                    cut = None
                    if q is not None:
                        cut = float(top.loc[top["year"].isin(SEL), own].quantile(q))
                        m &= (top[own] >= cut).to_numpy()
                    years = top["year"].to_numpy()[m]
                    for pname in PLANS:
                        rs = [x for x, k in zip(sims[pname], m) if k]
                        se, ho = split(rs, years)
                        yearly = {int(y): _stats([x for x, yy in zip(rs, years) if yy == y]).get("avg_r")
                                  for y in sorted(set(years))}
                        key = f"{side}_{pname}"
                        v = {"recipe": recipe, "filter": flt, "confidence": q, "cut": cut, "select": se, "holdout": ho,
                             "yearly_avg_r": yearly}
                        out["variants"].setdefault(key, []).append(v)
                        if se.get("trades", 0) >= MIN_SELECT_TRADES and \
                                se.get("avg_r", -9) > chosen.get(key, {}).get("select", {}).get("avg_r", -9):
                            chosen[key] = v
    for key, v in chosen.items():
        side, pname = key.split("_")
        base = out["baseline"][f"{key}_{v['filter']}"]
        sel, hold = v["select"].get("avg_r", -9), v["holdout"].get("avg_r", -9)
        beats = hold > base["holdout"].get("avg_r", 9) and sel > base["select"].get("avg_r", 9)
        # an edge must make money on both periods AND beat an average coin traded on the same days: otherwise the
        # market trend, not the coin choice, did the work
        v["verdict"] = ("tested_edge" if sel >= 0.05 and hold >= 0.05 and beats else
                        "weak" if hold > 0 else "no_edge")
        v["beats_average_coin"] = bool(beats)
        v["baseline_holdout"], v["baseline_select"] = base["holdout"], base["select"]
    out["chosen"] = chosen
    out["seconds"] = round(time.time() - t0, 1)
    REPORT.write_text(json.dumps(out, indent=1, default=str))
    _cache["bt"] = out
    return out


_cache: dict = {"bt": None}


def backtest_report() -> dict | None:
    if _cache["bt"] is None and REPORT.exists():
        _cache["bt"] = json.loads(REPORT.read_text())
    return _cache["bt"]


# ------------------------------------------------------------------------------------------- live plan

def _today_frame() -> pd.DataFrame | None:
    """Today's rows (forecast raw scores + pattern analog rates), one per covered coin."""
    from app.ml import patterns as pt

    td, pd_ = fc.today(), pt.today()
    if not td:
        return None
    recs = []
    for s, r in td["rows"].items():
        raw = r.get("raw") or {}
        rec = {"symbol": s, "tier": r["tier"], "date": r["date"], "close": r["close"], "atr_pct": r.get("atr_pct")}
        rec |= {f"pred_{k}": raw.get(k, r.get(k)) for k in fc.TARGETS}
        an = ((pd_ or {}).get("rows", {}).get(s) or {}).get("analog") or {}
        rec |= {f"analog_{k}": an.get(k, np.nan) for k in pt.OUTCOMES}
        recs.append(rec)
    return pd.DataFrame(recs)


def _fmt(x: float) -> float:
    """Round a price to 4 significant digits past the leading ones (works for BTC and for 0.0001 coins)."""
    if not x or not np.isfinite(x):
        return x
    mag = int(np.floor(np.log10(abs(x))))
    return round(x, max(0, 4 - mag))


def leverage_table(entry: float, stop: float, tp1: float, tp2: float | None, side: str, symbol: str,
                   margin: float, levels=(1, 2, 3, 5, 10, 15, 20)) -> dict:
    stop_pct = abs(entry - stop) / entry
    safe = fut.safe_leverage(stop_pct, symbol)
    rows = []
    for lev in levels:
        pos = margin * lev
        liq = fut.liquidation_price(entry, lev, symbol, side)
        liq_dist = abs(liq - entry) / entry
        status = ("safe" if lev <= safe else "tight" if liq_dist > stop_pct else "liquidated_before_stop")
        fee = pos * FEE
        sl_usd = -min(pos * stop_pct, margin) - fee
        tp1_usd = pos * abs(tp1 - entry) / entry - fee
        row = {"leverage": lev, "position_usd": round(pos, 2), "liquidation": _fmt(liq), "status": status,
               "stop_usd": round(-margin if status == "liquidated_before_stop" else sl_usd, 2),
               "tp1_usd": round(tp1_usd, 2)}
        if tp2 is not None:
            row["tp2_usd"] = round(pos * abs(tp2 - entry) / entry - fee, 2)            # whole position at TP2
            row["plan_usd"] = round(pos * 0.5 * (abs(tp1 - entry) + abs(tp2 - entry)) / entry - fee, 2)  # half/half
        rows.append(row)
    return {"safe_max": safe, "stop_pct": round(stop_pct * 100, 2), "rows": rows}


def _levels(entry: float, atr: float, side: str, plan: dict) -> dict:
    d = 1 if side == "LONG" else -1
    stop = entry - d * plan["stop_atr"] * atr
    tp1 = entry + d * plan["tp1_atr"] * atr
    tp2 = entry + d * plan["tp2_atr"] * atr if plan["tp2_atr"] else None
    pct = lambda x: round((x / entry - 1) * 100, 1)  # noqa: E731
    return {"entry": _fmt(entry), "stop": _fmt(stop), "stop_pct": pct(stop), "tp1": _fmt(tp1), "tp1_pct": pct(tp1),
            "tp2": _fmt(tp2) if tp2 else None, "tp2_pct": pct(tp2) if tp2 else None, "days": plan["days"]}


def _evidence(row: dict, side: str, tier: str) -> list[dict]:
    """Each piece of evidence in words, with the usual rate and how well that source was tested."""
    from app.ml import patterns as pt

    rep, prep, ptd = fc.report(tier) or {}, pt.report() or {}, pt.today() or {}
    me, other = ("long_tp_first", "short_tp_first") if side == "LONG" else ("short_tp_first", "long_tp_first")
    cal = (fc.today() or {}).get("rows", {}).get(row["symbol"], {})
    out = []

    def model(tgt: str, good_high: bool, text: str):
        m = (rep.get("models") or {}).get(tgt)
        v = cal.get(tgt)
        if not m or v is None:
            return
        h = m.get("holdout") or {}
        base = h.get("base_rate")
        good = (v > base) if good_high else (v < base)
        out.append({"source": "trained model", "text": text, "value": round(v, 3), "usual": base,
                    "supports": bool(good) if base is not None and abs(v - base) >= 0.01 else None,
                    "tested": f"AUC {h.get('auc')} on 2025-26" + ("" if m.get("beats_volatility_only", True)
                                                                 or m.get("chosen") == "volatility_only"
                                                                 else ", mostly from volatility")
                    + ("; volatility-only model, says nothing about direction" if m.get("chosen") == "volatility_only"
                       else ""),
                    "status": m.get("status")})

    model(me, True, f"Chance the {side.lower()} hits 3 ATR before 1.5 ATR stop (14 days)")
    model(other, False, "Chance the opposite trade wins")
    model("outperform_7d", side == "LONG", "Chance it beats the median coin over 7 days")
    model("drop_risk_7d", side == "SHORT", "Chance of a 2-ATR drop within 7 days")
    model("direction_7d", side == "LONG", "Chance it is higher in 7 days")
    pr = (ptd.get("rows") or {}).get(row["symbol"]) or {}
    an, base = pr.get("analog") or {}, ptd.get("base_rates") or {}
    ev = (prep.get("evaluation") or {})
    for k, good_high, text in ((me, True, f"Similar past charts: the {side.lower()} hit its target first"),
                               (other, False, "Similar past charts: the opposite trade hit its target first")):
        if k in an:
            a = (ev.get(k) or {}).get("analog") or {}
            out.append({"source": "chart patterns", "text": text, "value": an[k], "usual": base.get(k),
                        "supports": (an[k] > base.get(k, 0)) if good_high else (an[k] < base.get(k, 1)),
                        "tested": f"AUC {a.get('auc')}; top tenth came true {round((a.get('top_decile_rate') or 0) * 100)}%"
                                  f" vs {round((a.get('base_rate') or 0) * 100)}% usual", "status": "accepted"})
    if pr.get("pattern"):
        out.append({"source": "chart patterns", "text": f"Learned pattern: {pr['pattern']['name']} (seen "
                    f"{pr['pattern']['count']:,} times); the 50 closest charts moved a median "
                    f"{pr.get('analog_median_fwd7_pct')}% in 7 days", "value": None, "usual": None, "supports": None,
                    "tested": "direction from chart shape alone tested near a coin flip (AUC ~0.51): shown, not counted",
                    "status": "info"})
    return out


def _trend() -> dict | None:
    return (fc.today() or {}).get("market")


def gates(side: str, row: dict, chosen: dict) -> list[str]:
    """Why the tested rule would NOT take this trade today (empty = it would)."""
    out = []
    mk = _trend()
    if chosen.get("filter") == "trend" and mk:
        if side == "LONG" and mk["trend"] != "up":
            out.append(f"Against the market trend: BTC {mk['btc_close']:,.0f} is below its 50-day average "
                       f"{mk['btc_ma50']:,.0f}. The tested rule only buys when BTC is above it.")
        if side == "SHORT" and mk["trend"] != "down":
            out.append(f"Against the market trend: BTC {mk['btc_close']:,.0f} is above its 50-day average "
                       f"{mk['btc_ma50']:,.0f}. The tested rule only shorts when BTC is below it.")
    if chosen.get("cut") is not None:
        v = row.get(_own_col(side))
        if v is None or not np.isfinite(v) or v < chosen["cut"]:
            out.append(f"The model's confidence for this {side.lower()} is below the tested level "
                       f"(top {round((1 - chosen['confidence']) * 100)}% of past picks).")
    if chosen.get("verdict") == "no_edge":
        out.append("This side found no edge on the unseen years (2025-26).")
    a = row.get("atr_pct")
    if a is not None and np.isfinite(a) and a > MAX_ATR_PCT:
        out.append(f"Extreme volatility: its average daily move (ATR) is {a:.0f}% after a pump or crash in the last two "
                   f"weeks, so ATR-based stops and targets are meaningless. Not traded (cap {MAX_ATR_PCT:.0f}%).")
    return out


def _tested(ch: dict) -> dict:
    return {k: ch.get(k) for k in ("recipe", "filter", "confidence", "verdict", "holdout", "select", "baseline_holdout",
                                   "baseline_select", "beats_average_coin", "yearly_avg_r")}


def plan_for(symbol: str, side: str | None = None, margin: float = 100.0, live: dict | None = None,
             frame: pd.DataFrame | None = None) -> dict:
    """The full plan for one coin. `live` = {"price", "high_24h", "low_24h", "high_7d", "low_7d"} from the live candles.
    Without a side, the side with the higher score today is used."""
    bt = backtest_report()
    frame = frame if frame is not None else _today_frame()
    if frame is None or bt is None:
        return {"available": False, "note": "The models or the signal backtest are not ready yet."}
    hit = frame[frame["symbol"] == symbol]
    if hit.empty:
        return {"available": True, "covered": False, "symbol": symbol,
                "note": "Not covered: the models score coins with 90+ days of history and $1M+ daily volume."}
    row = hit.iloc[0].to_dict()
    tier = row["tier"]
    tf = frame[frame["tier"] == tier]
    scores = {}
    for s in ("LONG", "SHORT"):
        rc = bt["chosen"][f"{s}_swing"]["recipe"]
        scores[s] = float(score(tf.assign(date=0), rc, s, by="date").loc[hit.index[0]])
    if side is None:
        mk = _trend()
        side = ("LONG" if mk["trend"] == "up" else "SHORT") if mk else max(scores, key=scores.get)
    rank = int((score(tf.assign(date=0), bt["chosen"][f"{side}_swing"]["recipe"], side, by="date")
                >= scores[side]).sum())
    close, atr = float(row["close"]), float(row["atr_pct"] or 0) / 100 * float(row["close"])
    price = float((live or {}).get("price") or close)
    moved_atr = (price - close) / atr if atr else 0.0
    d = 1 if side == "LONG" else -1
    notes = []
    if d * moved_atr > 0.25:   # the price already ran our way since the models' data: wait for a pullback
        entry, entry_type = close + d * 0.33 * atr, "limit"
        notes.append(f"Price moved {abs(price / close - 1) * 100:.1f}% {'up' if moved_atr > 0 else 'down'} since the "
                     f"models' data ({row['date']} close {_fmt(close)}). Don't chase: wait for the limit.")
    else:
        entry, entry_type = price, "market"
        if d * moved_atr < -0.25:
            notes.append(f"Price moved {abs(price / close - 1) * 100:.1f}% against the idea since the models' data: "
                         f"the plan is re-based on today's price; check it is not still falling hard.")
    plans = {}
    for pname, plan in PLANS.items():
        lv = _levels(entry, atr, side, plan)
        ch = bt["chosen"][f"{side}_{pname}"]
        lt = leverage_table(entry, lv["stop"], lv["tp1"], lv["tp2"], side, symbol, margin)
        risk_at_safe = next((r["stop_usd"] for r in lt["rows"] if r["leverage"] == max(1, min(lt["safe_max"], 20))), None)
        h = ch["holdout"]
        plans[pname] = {**lv, "label": plan["label"], "leverage": lt, "tested": _tested(ch),
                        "expected_usd_per_trade_at_safe": (round(-risk_at_safe * h.get("avg_r", 0), 2)
                                                           if risk_at_safe and h.get("trades") else None)}
    sw = plans["swing"]
    cancel = (f"Cancel if price reaches {sw['stop']} before you are filled, or if not filled within 2 days "
              f"(the models re-score every day).")
    risks = []
    cal = (fc.today() or {}).get("rows", {}).get(symbol, {})
    drop, drop_base = cal.get("drop_risk_7d"), ((fc.report(tier) or {}).get("models", {}).get("drop_risk_7d", {})
                                                .get("holdout", {}).get("base_rate"))
    if side == "LONG" and drop is not None and drop_base and drop > drop_base + 0.02:
        risks.append(f"Drop risk {round(drop * 100)}% is above the usual {round(drop_base * 100)}%: keep the stop.")
    if tier != "core":
        risks.append("Thinner coin ($1-5M a day): wider spreads, scored by the less accurate wide models.")
    h = sw["tested"]["holdout"]
    if h.get("trades"):
        oc = h.get("outcomes", {})
        risks.append(f"In 2025-26 tests this kind of pick hit the stop {round(oc.get('stop', 0) * 100)}% of the time; "
                     f"it reached TP2 {round(oc.get('tp2', 0) * 100)}%.")
    if sw["tested"]["verdict"] == "weak" and not sw["tested"].get("beats_average_coin"):
        risks.append("For this side the market trend did most of the work: in tests an average coin traded on the same "
                     "days did as well as the pick.")
    if tier != "core":
        gate_list = ["Thinner coin: the signal backtest covers liquid coins ($5M+ a day) only."]
    else:
        gate_list = []
    gate_list += gates(side, row, bt["chosen"][f"{side}_swing"])
    fast_gates = gates(side, row, bt["chosen"][f"{side}_fast"])
    return {"available": True, "covered": True, "symbol": symbol, "side": side, "tier": tier,
            "status": "trade" if not gate_list else "watch", "gates": gate_list, "fast_gates": fast_gates,
            "market": _trend(),
            "as_of": row["date"], "model_close": _fmt(close), "price": _fmt(price),
            "atr": _fmt(atr), "atr_pct": row["atr_pct"], "entry_type": entry_type,
            "score": round(scores[side], 3), "rank": rank, "of": int(len(tf)), "scores": {k: round(v, 3) for k, v in scores.items()},
            "plans": plans, "evidence": _evidence(row, side, tier), "notes": notes, "risks": risks, "cancel_if": cancel,
            "margin": margin, "live": live or None}


def candidates(n: int = 5) -> dict:
    """Today's top liquid coins per side, whether the tested rule would trade each side today, and the pick."""
    bt, frame = backtest_report(), _today_frame()
    if bt is None or frame is None:
        return {"available": False}
    out = {"available": True, "as_of": (fc.today() or {}).get("as_of"), "market": _trend(), "sides": {},
           "rule": bt.get("rule")}
    core = frame[(frame["tier"] == "core") & ~(frame["atr_pct"].astype(float) > MAX_ATR_PCT)]
    for side in ("LONG", "SHORT"):
        ch = bt["chosen"][f"{side}_swing"]
        sc = score(core.assign(date=0), ch["recipe"], side, by="date")
        top = core.assign(score=sc).sort_values("score", ascending=False).head(n)
        g = gates(side, top.iloc[0].to_dict(), ch) if len(top) else ["no coins"]
        out["sides"][side] = {**_tested(ch), "status": "trade" if not g else "watch", "gates": g,
                              "top": [{"symbol": r.symbol, "score": round(float(r.score), 3)} for r in top.itertuples()]}
    rank = {"tested_edge": 2, "weak": 1, "no_edge": 0}
    sides = out["sides"]
    live = [s for s in sides if sides[s]["status"] == "trade"]
    pool = live or list(sides)
    with_trend = lambda s: not any(g.startswith("Against the market trend") for g in sides[s]["gates"])  # noqa: E731
    out["pick_side"] = max(pool, key=lambda s: (s in live, with_trend(s), rank.get(sides[s]["verdict"], 0),
                                                sides[s]["holdout"].get("avg_r", -9)))
    out["pick_symbol"] = sides[out["pick_side"]]["top"][0]["symbol"]
    out["status"] = "trade" if live else "no_trade"
    out["strong"] = bool(live) and sides[out["pick_side"]]["verdict"] == "tested_edge"
    return out


if __name__ == "__main__":
    from app.ml import signal_plan as sp

    res = sp.backtest("core")
    print(f"backtest {res.get('seconds')}s · {res.get('rule')}")
    for k, v in res["chosen"].items():
        h, b = v["holdout"], v["baseline_holdout"]
        print(f"{k:12s} {v['recipe']:18s} filter={v['filter']:5s} conf={v['confidence']} {v['verdict']:12s} "
              f"2020-24 {v['select'].get('trades')} trades {v['select'].get('avg_r')}R | 2025-26 {h.get('trades')} trades "
              f"{h.get('avg_r')}R win {h.get('win_rate')} {h.get('outcomes')} | average coin {b.get('avg_r')}R "
              f"| by year {v['yearly_avg_r']}")
