"""Leverage study: what 20x leverage and a "90 % win rate" do to the validated daily-trend edge, measured on the data.

    uv run python -m app.maintenance.leverage_study        # writes data/history/leverage_study.json

Questions, fixed before looking at the results:
  1. Liquidation. For every trade the system account took 2020-now (growth mode), the worst move against the entry
     before the exit (MAE). An isolated long is liquidated when price falls about 1/L - MMR below entry. Share of
     trades liquidated at each leverage, share of WINNERS liquidated before they paid, and the expectancy left.
  2. The 20x trader. Same trades, every one opened with 20 % of the account as margin at 20x (4x the account).
  3. Win rate vs payoff. Same signal and stop (2.5 ATR), fixed targets 0.25R .. 3R. Win rate and expectancy after
     costs per period — the price of a high win rate.
  4. Tight stops that fit inside a 20x liquidation distance (fixed 0.5-3 % stops), target 2R, on 1d and 4h candles.
     "20 % per trade at 20x" is a 1 % move: 1 % target / 0.5 % stop on 4h candles is in this set.
Costs (Binance USD-M, no BNB discount): taker 0.05 % + slippage 0.05 % per side. MMR: lowest Binance tier, approx.
(BTC 0.4 %, ETH 0.5 %, others 1 %); the exchange's own figure is authoritative.
"""
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.maintenance.edge_study import HIST, TRAIN_END, VALID_END, features, load
from app.risk.portfolio import rules_for
from app.risk.portfolio import run as run_pf
from app.strategies import daily_trend as dt

TAKER, SLIP = 0.0005, 0.0005
LEVERAGES = (1, 2, 3, 5, 10, 20, 50)
TARGETS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
TIGHT_STOPS = (0.005, 0.01, 0.02, 0.03)
PERIODS = {"train 2020-23": (None, TRAIN_END), "valid 2024": (TRAIN_END, VALID_END), "TEST 2025-now": (VALID_END, None)}


def mmr(sym: str) -> float:
    return 0.004 if sym == "BTCUSDT" else 0.005 if sym == "ETHUSDT" else 0.01


def liq_distance(lev: float, sym: str) -> float:
    """Fall from entry that liquidates an isolated long at this leverage (approximate Binance formula)."""
    return 1 / lev - mmr(sym)


@dataclass
class Trade:
    sym: str
    t: pd.Timestamp
    r: float
    stop_pct: float
    mae: float  # worst fall below entry before the exit, as a share of entry
    move: float  # exit price / entry - 1
    bars: int


def simulate(df: pd.DataFrame, sym: str, sig: np.ndarray, stop_px: np.ndarray, rr: float, max_hold: int) -> list[Trade]:
    """Long trades: entry at the next open, fixed target rr x risk, stop first when both are touched in one candle."""
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    out, i, n = [], 0, len(df)
    while i < n - 1:
        if not sig[i]:
            i += 1
            continue
        e, s = o[i + 1] * (1 + SLIP), stop_px[i]
        risk = e - s
        if not np.isfinite(risk) or risk <= 0:
            i += 1
            continue
        tp, j, mae, exit_px = e + rr * risk, i + 1, 0.0, None
        while j < n:
            mae = max(mae, (e - lo[j]) / e)
            if lo[j] <= s:
                exit_px = s * (1 - SLIP)
                break
            if h[j] >= tp:
                exit_px = tp
                break
            if j - i >= max_hold:
                exit_px = c[j] * (1 - SLIP)
                break
            j += 1
        else:
            break
        pnl = exit_px - e - 2 * TAKER * e
        out.append(Trade(sym, df.t.iloc[i], pnl / risk, risk / e, mae, exit_px / e - 1, j - i))
        i = j + 1
    return out


def stats(r: np.ndarray) -> dict:
    if not len(r):
        return {"n": 0}
    se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
    return {"n": int(len(r)), "win": round(float((r > 0).mean()), 3), "E": round(float(r.mean()), 3),
            "t": round(float(r.mean() / se), 2) if se and se > 0 else None, "total": round(float(r.sum()), 1)}


def by_period(trades: list[Trade], fn=lambda t: t.r) -> dict:
    out = {}
    for name, (a, b) in PERIODS.items():
        sel = [fn(t) for t in trades if (a is None or t.t >= a) and (b is None or t.t < b)]
        out[name] = stats(np.array(sel))
    return out


def liquidation_study(frames: dict[str, pd.DataFrame]) -> dict:
    """The system account's own trades (growth mode, 2020-now) under leverage."""
    rules = rules_for("growth")
    res = run_pf(frames, rules, pd.Timestamp("2020-01-01", tz="UTC"))
    rows = []
    for t in res.trades:
        f = frames[t["symbol"]]
        held = f.loc[t["opened"]:t["closed"]]
        prev = f.index[f.index.get_loc(t["opened"]) - 1]  # the stop is 2.5 x ATR of the signal day
        rows.append({"sym": t["symbol"], "r": t["r"], "mae": float(1 - held.low.min() / t["entry"]),
                     "move": t["exit"] / t["entry"] - 1, "stop_pct": float(dt.STOP_ATR * f.loc[prev].atr / t["entry"]),
                     "opened": str(t["opened"].date())})
    r = np.array([x["r"] for x in rows])
    out = {"trades": len(rows), "base": stats(r), "big_wins": {
        "ge_5R": int((r >= 5).sum()), "ge_10R": int((r >= 10).sum()), "ge_20R": int((r >= 20).sum()),
        "best_R": round(float(r.max()), 1)}, "median_stop_pct": round(float(np.median([x["stop_pct"] for x in rows]) * 100), 1),
        "median_mae_of_winners_pct": round(float(np.median([x["mae"] for x in rows if x["r"] > 0]) * 100), 1),
        "by_leverage": {}}
    for lev in LEVERAGES:
        liq = np.array([x["mae"] >= liq_distance(lev, x["sym"]) for x in rows])
        # risk-sized: notional = risk / stop; the margin (notional / L) is lost on liquidation
        adj = np.array([-(1 / lev) / x["stop_pct"] if q else x["r"] for x, q in zip(rows, liq)])
        wins = r > 0
        # the 20x trader: 20 % of the account as margin on every trade (notional = 0.2 * L * equity)
        eq, peak, dd = 100.0, 100.0, 0.0
        for x, q in zip(rows, liq):
            margin = 0.2 * eq
            notional = margin * lev
            eq += -margin if q else notional * x["move"] - 2 * TAKER * notional
            eq = max(eq, 0.0)
            peak, dd = max(peak, eq), max(dd, (peak - eq) / peak if peak else 0)
            if eq < 5:
                break
        # risk-sized account at 2 % per trade, trade by trade (ignores overlap: a fair approximation of the order)
        eq2 = 100.0
        for a in adj:
            eq2 *= max(0.0, 1 + 0.02 * a)
        out["by_leverage"][f"{lev}x"] = {
            "liq_distance_alt_pct": round(liq_distance(lev, "XUSDT") * 100, 1),
            "liquidated_pct": round(float(liq.mean()) * 100, 1),
            "winners_liquidated_pct": round(float(liq[wins].mean()) * 100, 1) if wins.any() else None,
            "E_risk_sized": round(float(adj.mean()), 3), "win_risk_sized": round(float((adj > 0).mean()), 3),
            "end_usd_risk_sized_2pct": round(eq2, 2),
            "end_usd_margin20pct": round(eq, 2), "max_drawdown_margin20pct": round(dd * 100, 1)}
    return out


def winrate_sweep(frames: dict[str, pd.DataFrame]) -> list[dict]:
    rows = []
    for rr in TARGETS:
        trades: list[Trade] = []
        for sym, f in frames.items():
            d = f.reset_index()
            stop = (d.open.shift(-1) * (1 + SLIP) - dt.STOP_ATR * d.atr).to_numpy()
            sig = (d.signal & (dt.STOP_ATR * d.atr / d.close >= dt.MIN_STOP_PCT)).to_numpy()
            trades += simulate(d, sym, sig, stop, rr, dt.MAX_HOLD_DAYS)
        rows.append({"target_R": rr, "stop": "2.5 ATR", **by_period(trades)})
        print("target", rr, rows[-1]["train 2020-23"], rows[-1]["valid 2024"], rows[-1]["TEST 2025-now"], flush=True)
    return rows


def tight_stops(frames_1d: dict[str, pd.DataFrame]) -> list[dict]:
    rows = []
    for pct in TIGHT_STOPS:
        trades: list[Trade] = []
        for sym, f in frames_1d.items():
            d = f.reset_index()
            stop = (d.open.shift(-1) * (1 + SLIP) * (1 - pct)).to_numpy()
            trades += simulate(d, sym, d.signal.to_numpy(), stop, 2.0, dt.MAX_HOLD_DAYS)
        rows.append({"tf": "1d", "stop_pct": pct * 100, "target_R": 2.0, "cost_share_of_risk_pct": round(2 * (TAKER + SLIP) / pct * 100),
                     "liquidated_at_20x_pct": 0.0 if pct < liq_distance(20, "XUSDT") else None, **by_period(trades)})
        print("1d stop", pct, rows[-1]["train 2020-23"], rows[-1]["valid 2024"], rows[-1]["TEST 2025-now"], flush=True)
    # 4h: 20-candle breakout while BTC is above its 200-day average (previous finished day)
    syms = sorted(p.stem for p in (HIST / "4h").glob("*.csv"))
    btc_d = load("1d", "BTCUSDT")
    data = {s: features(load("4h", s), load("1d", s), btc_d) for s in syms}
    for pct in TIGHT_STOPS + ("atr",):
        trades = []
        for sym, df in data.items():
            d = df.reset_index(drop=True)
            e = d.open.shift(-1) * (1 + SLIP)
            stop = (e - dt.STOP_ATR * d.atr if pct == "atr" else e * (1 - pct)).to_numpy()
            sig = ((d.close > d.hh20) & (d.btc_up200 == 1)).fillna(False).to_numpy()
            trades += simulate(d, sym, sig, stop, 2.0, 90)
        row = {"tf": "4h", "stop_pct": "2.5 ATR" if pct == "atr" else pct * 100, "target_R": 2.0, **by_period(trades)}
        if pct != "atr":
            row["cost_share_of_risk_pct"] = round(2 * (TAKER + SLIP) / pct * 100)
        if pct == 0.005:
            row["note"] = "a 1 % move: the '20 % per trade at 20x' trade"
        rows.append(row)
        print("4h stop", pct, row["train 2020-23"], row["valid 2024"], row["TEST 2025-now"], flush=True)
    return rows


def main() -> dict:
    btc_ok = dt.btc_filter(load("1d", "BTCUSDT"))
    frames = {p.stem: dt.prepare(load("1d", p.stem), btc_ok) for p in sorted((HIST / "1d").glob("*.csv"))}
    out = {"protocol": __doc__, "liquidation": liquidation_study(frames), "winrate_vs_target": winrate_sweep(frames),
           "tight_stops": tight_stops(frames)}
    (HIST / "leverage_study.json").write_text(json.dumps(out, default=str, indent=1))
    print(json.dumps(out["liquidation"], indent=1, default=str))
    return out


if __name__ == "__main__":
    main()
