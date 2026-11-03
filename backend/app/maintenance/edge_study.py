"""Edge study: which rule-based setups survive a minimum 1:2 reward-to-risk after realistic costs?

    uv run python -m app.maintenance.edge_study            # full grid, prints the pre-registered verdict

Data: data/history/{4h,1d}/ (app.maintenance.history). Protocol, fixed before looking at results:
  train   2020-01-01 .. 2023-12-31   rank every configuration here only
  valid   2024-01-01 .. 2024-12-31   a configuration must also be positive here
  test    2025-01-01 .. today        looked at once, for the configurations that passed; this is the reported number
Execution: signal on a closed 4h candle, entry at the next candle's open, stop and target checked on every later
candle with the stop assumed first when both are touched in one candle. Costs per side: fee 0.10 % + slippage 0.05 %.
Daily filters use the previous completed day only.
"""
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HIST = Path("data/history")
FEE, SLIP = 0.001, 0.0005
TRAIN_END, VALID_END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")
MIN_STOP_PCT = 0.015  # stop at least 1.5 % away, so costs stay under ~20 % of the risk
MAX_HOLD = 90  # 4h candles (15 days)


def load(tf: str, sym: str) -> pd.DataFrame:
    df = pd.read_csv(HIST / tf / f"{sym}.csv")
    df["t"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
    return df


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def daily_features(d: pd.DataFrame) -> pd.DataFrame:
    """Daily trend state, stamped at the END of the day so a 4h candle only sees finished days."""
    f = pd.DataFrame({"avail": d.t + pd.Timedelta(days=1)})
    f["d_up"] = (d.close > d.close.rolling(50).mean()).astype(float)
    f["d_up200"] = (d.close > d.close.rolling(200).mean()).astype(float)
    e50, e200 = d.close.ewm(span=50).mean(), d.close.ewm(span=200).mean()
    f["d_stack"] = ((d.close > e50) & (e50 > e200)).astype(float) - ((d.close < e50) & (e50 < e200)).astype(float)
    return f


def features(h: pd.DataFrame, d: pd.DataFrame, btc_d: pd.DataFrame, bar: pd.Timedelta = pd.Timedelta(hours=4)
             ) -> pd.DataFrame:
    h = h.copy()
    h["atr"] = atr(h)
    h["ema20"] = h.close.ewm(span=20).mean()
    h["rsi"] = rsi(h.close)
    for n in (20, 55):
        h[f"hh{n}"] = h.high.rolling(n).max().shift()
        h[f"ll{n}"] = h.low.rolling(n).min().shift()
    mid, sd = h.close.rolling(20).mean(), h.close.rolling(20).std()
    h["bbw"] = 4 * sd / mid
    h["bbw_pct"] = h.bbw.rolling(180).rank(pct=True)
    h["low5"], h["high5"] = h.low.rolling(5).min(), h.high.rolling(5).max()
    h["vol_ratio"] = h.volume / h.volume.rolling(20).mean()
    h["close_t"] = h.t + bar
    df = pd.merge_asof(h.sort_values("close_t"), daily_features(d).sort_values("avail"), left_on="close_t",
                       right_on="avail", direction="backward")
    b = daily_features(btc_d).rename(columns={"d_up": "btc_up", "d_up200": "btc_up200", "d_stack": "btc_stack"})
    return pd.merge_asof(df, b.sort_values("avail"), left_on="close_t", right_on="avail", direction="backward",
                         suffixes=("", "_b"))


# ---------------------------------------------------------------- setups: boolean series + stop price per bar

def setup(df: pd.DataFrame, kind: str, side: int, p: dict) -> tuple[pd.Series, pd.Series]:
    a = df.atr
    if kind == "breakout":
        n = p["n"]
        sig = df.close > df[f"hh{n}"] if side > 0 else df.close < df[f"ll{n}"]
        stop = df.close - side * p["k"] * a
    elif kind == "pullback":
        dip = (df.low.rolling(4).min() < df.ema20) & (df.rsi.rolling(4).min() < p["rsi"]) if side > 0 else \
              (df.high.rolling(4).max() > df.ema20) & (df.rsi.rolling(4).max() > 100 - p["rsi"])
        reclaim = (df.close > df.ema20) & (df.close.shift() <= df.ema20.shift()) if side > 0 else \
                  (df.close < df.ema20) & (df.close.shift() >= df.ema20.shift())
        sig = dip & reclaim
        swing = df.low5 if side > 0 else df.high5
        stop = swing - side * 0.25 * a
        stop = np.minimum(stop, df.close - p["k"] * a) if side > 0 else np.maximum(stop, df.close + p["k"] * a)
    elif kind == "squeeze":
        squeezed = df.bbw_pct.shift().rolling(6).min() < p["q"]
        brk = df.close > df.hh20 if side > 0 else df.close < df.ll20
        sig = squeezed & brk & (df.vol_ratio > 1.2)
        stop = df.close - side * p["k"] * a
    else:
        raise ValueError(kind)
    return sig.fillna(False), pd.Series(stop, index=df.index)


def regime_ok(df: pd.DataFrame, f: str, side: int) -> pd.Series:
    if f == "none":
        return pd.Series(True, index=df.index)
    if f == "coin":
        return df.d_up == (1 if side > 0 else 0)
    if f == "btc":
        return df.btc_up200 == (1 if side > 0 else 0)
    if f == "both":
        return (df.d_stack == side) & (df.btc_up200 == (1 if side > 0 else 0))
    raise ValueError(f)


@dataclass
class Trade:
    sym: str
    t: pd.Timestamp
    side: int
    r: float
    bars: int
    risk_pct: float


def simulate(df: pd.DataFrame, sym: str, sig: pd.Series, stop: pd.Series, side: int, exit_: str,
             rr: float = 2.0, max_hold: int = MAX_HOLD) -> list[Trade]:
    o, h, lo, c, a = (df[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    sg, st = sig.to_numpy(), stop.to_numpy()
    out, i, n = [], 0, len(df)
    while i < n - 1:
        if not sg[i]:
            i += 1
            continue
        e = o[i + 1] * (1 + side * SLIP)
        s = st[i]
        risk = side * (e - s)
        if not np.isfinite(risk) or risk <= 0 or risk / e < MIN_STOP_PCT:
            i += 1
            continue
        tp = e + side * rr * risk
        half, be_stop, trail = False, s, None
        pnl, j = 0.0, i + 1
        while j < n:
            hi, lw = h[j], lo[j]
            hit_stop = (lw <= be_stop) if side > 0 else (hi >= be_stop)
            hit_tp = (hi >= tp) if side > 0 else (lw <= tp)
            if hit_stop:  # stop first when both are touched
                px = be_stop * (1 - side * SLIP)
                pnl += (0.5 if half else 1.0) * side * (px - e)
                break
            if hit_tp and not half:
                if exit_ == "fixed":
                    pnl += side * (tp - e)
                    break
                half, be_stop = True, e  # bank half at 2R, rest rides with stop at entry and a 3-ATR trail
                pnl += 0.5 * side * (tp - e)
            if half:
                trail = c[j] - side * 3 * a[j]
                be_stop = max(be_stop, trail) if side > 0 else min(be_stop, trail)
            if j - i >= max_hold:
                pnl += (0.5 if half else 1.0) * side * (c[j] * (1 - side * SLIP) - e)
                break
            j += 1
        else:
            break  # still open at the end of the data: not counted
        fees = 2 * FEE * e  # slippage is already inside the entry and exit prices
        out.append(Trade(sym, df.t.iloc[i], side, (pnl - fees) / risk, j - i, risk / e))
        i = j + 1  # one position per coin per configuration
    return out


def stats(r: list[float]) -> dict:
    if not r:
        return {"n": 0}
    x = np.array(r)
    se = x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else np.nan
    wins, losses = x[x > 0].sum(), -x[x < 0].sum()
    return {"n": len(x), "win": round(float((x > 0).mean()), 3), "E": round(float(x.mean()), 3),
            "t": round(float(x.mean() / se), 2) if se and se > 0 else None,
            "pf": round(float(wins / losses), 2) if losses else None, "total": round(float(x.sum()), 1)}


GRID = {
    "breakout": [{"n": n, "k": k} for n in (20, 55) for k in (1.5, 2.5)],
    "pullback": [{"rsi": r, "k": k} for r in (35, 45) for k in (1.0, 2.0)],
    "squeeze": [{"q": q, "k": k} for q in (0.1, 0.2) for k in (1.5, 2.5)],
}


BARS = {"4h": (pd.Timedelta(hours=4), MAX_HOLD), "1d": (pd.Timedelta(days=1), 60)}


def run(tf: str = "4h") -> dict:
    syms = sorted(p.stem for p in (HIST / tf).glob("*.csv"))
    btc_d = load("1d", "BTCUSDT")
    bar, hold = BARS[tf]
    data = {s: features(load(tf, s), load("1d", s), btc_d, bar) for s in syms}
    rows = []
    for kind, params in GRID.items():
        for p, side, flt, ex in itertools.product(params, (1, -1), ("none", "coin", "btc", "both"),
                                                  ("fixed", "half_trail")):
            trades: list[Trade] = []
            for s, df in data.items():
                sig, stop = setup(df, kind, side, p)
                trades += simulate(df, s, sig & regime_ok(df, flt, side), stop, side, ex, max_hold=hold)
            part = {"train": [t.r for t in trades if t.t < TRAIN_END],
                    "valid": [t.r for t in trades if TRAIN_END <= t.t < VALID_END],
                    "test": [t.r for t in trades if t.t >= VALID_END]}
            rows.append({"kind": kind, "p": p, "side": "LONG" if side > 0 else "SHORT", "filter": flt, "exit": ex,
                         **{k: stats(v) for k, v in part.items()}})
            print(f"{kind:9s} {json.dumps(p):22s} {rows[-1]['side']:5s} {flt:5s} {ex:10s} "
                  f"train {rows[-1]['train']}  valid {rows[-1]['valid']}", flush=True)
    passed = [r for r in rows if r["train"].get("n", 0) >= 100 and r["train"]["E"] > 0.05 and (r["train"]["t"] or 0) > 2
              and r["valid"].get("n", 0) >= 30 and r["valid"]["E"] > 0]
    res = {"timeframe": tf, "configs_tried": len(rows), "passed_train_and_valid": passed, "rows": rows,
           "protocol": __doc__}
    (HIST / f"edge_study_{tf}.json").write_text(json.dumps(res, default=str, indent=1))
    return res


def portfolio() -> dict:
    """$100 spot account trading the daily trend breakout under each risk mode, one fresh account per period."""
    from app.risk.portfolio import RISK_MODES, rules_for, summary
    from app.risk.portfolio import run as run_pf
    from app.strategies import daily_trend as dt

    btc_ok = dt.btc_filter(load("1d", "BTCUSDT"))
    frames = {p.stem: dt.prepare(load("1d", p.stem), btc_ok) for p in sorted((HIST / "1d").glob("*.csv"))}
    periods = {"train 2020-23": ("2020-01-01", "2023-12-31"), "valid 2024": ("2024-01-01", "2024-12-31"),
               "TEST 2025-now": ("2025-01-01", None), "all 2020-now": ("2020-01-01", None)}
    out: dict = {}
    for mode in RISK_MODES:
        rules = rules_for(mode)
        out[mode] = {"rules": rules.d()}
        for name, (a, b) in periods.items():
            res = run_pf(frames, rules, pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC") if b else None)
            out[mode][name] = summary(res, rules)
            print(mode, name, out[mode][name])
    (HIST / "portfolio_1d.json").write_text(json.dumps(out, default=str))
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        portfolio()
        raise SystemExit
    r = run(sys.argv[1] if len(sys.argv) > 1 else "4h")
    print(f"\n{r['configs_tried']} configurations tried; {len(r['passed_train_and_valid'])} passed train + validation.")
    for x in sorted(r["passed_train_and_valid"], key=lambda x: -x["train"]["E"]):
        print(f"  {x['kind']} {x['p']} {x['side']} {x['filter']} {x['exit']}\n     train {x['train']}\n"
              f"     valid {x['valid']}\n     TEST  {x['test']}")
