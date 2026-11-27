"""Event-driven backtester.

Phase 1 (expensive, cached): walk forward bar by bar; at each closed bar build the *same* analysis snapshot
the live system builds (from a rolling window of past bars only, higher timeframes resampled from completed
buckets only) and record every confirmed+validated setup.
Phase 2 (cheap): simulate those setups with TradeSim under a cost model; repeated for stress scenarios.
Phase 3: portfolio layer applies capital constraints (risk per trade, max concurrent positions).
"""
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from app.analysis.analyzer import build_snapshot
from app.backtest.metrics import breakdown, equity_metrics, max_drawdown, trade_stats
from app.data.timeframes import tf_ms
from app.signals.simulator import TradeSim
from app.strategies.evaluate import enrich, evaluate_strategies
from app.strategies.exits import apply_exit_profile, min_quality


@dataclass
class BacktestConfig:
    symbols: list[str]
    timeframe: str = "1h"
    strategies: list[str] | None = None
    htf: list[str] = field(default_factory=lambda: ["4h"])
    window: int = 300
    step: int = 1
    start_ms: int | None = None
    end_ms: int | None = None
    initial_equity: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_concurrent: int = 5
    fee: float = 0.0005
    slip_bps: float = 2.0
    require_validation: bool = True
    oos_fraction: float = 0.3
    exit_profile: str | None = None

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def resample_closed(df: pd.DataFrame, base_tf: str, htf: str) -> pd.DataFrame:
    """Aggregate base candles into higher-timeframe candles, keeping only *completed* buckets."""
    step = tf_ms(htf)
    b = tf_ms(base_tf)
    g = (df["open_time"] // step) * step
    agg = df.groupby(g).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                            close=("close", "last"), volume=("volume", "sum"), n=("open", "size"))
    agg = agg[agg["n"] == step // b].drop(columns="n")
    agg.index.name = "open_time"
    return agg.reset_index()


def generate_setups(df: pd.DataFrame, symbol: str, cfg: BacktestConfig, progress=None, extras=None) -> list[dict]:
    """`extras(snap, candidate) -> dict`, if given, adds fields from the same as-of snapshot to each recorded setup
    (used by the meta-labeling research to keep HTF trend and level context without re-running the analysis)."""
    setups = []
    seen: set[str] = set()
    step_ms = tf_ms(cfg.timeframe)
    n = len(df)
    for t in range(cfg.window, n - 1, cfg.step):
        win = df.iloc[t - cfg.window + 1 : t + 1].reset_index(drop=True)
        hist = df.iloc[max(0, t - cfg.window * 8) : t + 1]
        htf = {h: resample_closed(hist, cfg.timeframe, h).tail(200).reset_index(drop=True) for h in cfg.htf}
        try:
            snap = enrich(build_snapshot(symbol, cfg.timeframe, win, htf, light=True), win)
        except ValueError:
            continue
        now = int(win["open_time"].iloc[-1]) + step_ms
        for c in evaluate_strategies(snap, now_ms=now, only=cfg.strategies):
            if c.get("stage") != "confirmed":
                continue
            if cfg.require_validation and not c["validation"]["passed"]:
                continue
            key = f"{c['strategy']}|{c['direction']}|{c['anchor_id']}"
            if key in seen:
                continue
            seen.add(key)
            setups.append({"symbol": symbol, "bar": t, "time": now, "strategy": c["strategy"],
                           "direction": c["direction"], "entry_low": c["entry_low"], "entry_high": c["entry_high"],
                           "stop": c["stop"], "targets": c["targets"], "expiry_bars": c["expiry_bars"],
                           "hold_bars": c["expected_hold_bars"], "regime": snap["regime"]["regime"],
                           "quality": c["validation"]["quality_score"], "bias": snap["confluence"]["bias"]})
            if extras is not None:
                setups[-1].update(extras(snap, c))
        if progress and t % 100 == 0:
            progress(t / n)
    return setups


def with_exit_profile(setups: list[dict], name: str | None) -> list[dict]:
    """Apply an exit profile to generated setups (quality floor, then entry/targets) — same rules as live."""
    mq = min_quality(name)
    return [apply_exit_profile(s, name) for s in setups if mq is None or s["quality"] >= mq]


def simulate(df: pd.DataFrame, setups: list[dict], fee: float, slip_bps: float) -> list[dict]:
    o, h, low, c, tm = (df[k].to_numpy() for k in ("open", "high", "low", "close", "open_time"))
    trades = []
    busy_until: dict[str, int] = {}
    for s in setups:
        key = f"{s['symbol']}|{s['strategy']}"
        if busy_until.get(key, -1) >= s["bar"]:
            continue  # one position per symbol/strategy at a time
        sim = TradeSim(s["direction"], s["entry_low"], s["entry_high"], s["stop"], list(s["targets"]),
                       expiry_bars=s["expiry_bars"], max_hold_bars=max(3 * s["hold_bars"], 24), fee=fee, slip_bps=slip_bps)
        j = s["bar"] + 1
        while j < len(df) and not sim.terminal:
            sim.on_bar(int(tm[j]), o[j], h[j], low[j], c[j])
            j += 1
        busy_until[key] = j - 1
        if sim.fill is None:
            trades.append({**s, "outcome": sim.outcome or "OPEN_AT_END", "r": None, "filled": False})
            continue
        trades.append({**s, "outcome": sim.outcome or "OPEN_AT_END", "r": sim.realized_r if sim.terminal else None,
                       "filled": True, "entry_time": sim.fill_time, "exit_time": sim.exit_time, "fill": sim.fill,
                       "exit": sim.exit_price, "mfe_r": sim.mfe_r, "mae_r": sim.mae_r, "bars_held": sim.bars_held})
    return trades


def portfolio(trades: list[dict], cfg: BacktestConfig, start_ms: int, end_ms: int) -> dict:
    """Chronological capital allocation: fixed-fractional risk on equity at entry, max concurrent positions."""
    closed = sorted([t for t in trades if t.get("r") is not None], key=lambda t: t["entry_time"])
    equity = cfg.initial_equity
    open_pos: list[tuple[int, float]] = []  # (exit_time, pnl)
    curve = [(start_ms, equity)]
    taken = []
    for t in closed:
        for ex, pnl in sorted([p for p in open_pos if p[0] <= t["entry_time"]]):
            equity += pnl
            curve.append((ex, equity))
        open_pos = [p for p in open_pos if p[0] > t["entry_time"]]
        if len(open_pos) >= cfg.max_concurrent:
            continue
        risk_amt = equity * cfg.risk_per_trade_pct / 100
        open_pos.append((t["exit_time"], risk_amt * t["r"]))
        taken.append(t)
    for ex, pnl in sorted(open_pos):
        equity += pnl
        curve.append((ex, equity))
    curve.append((end_ms, equity))
    times = np.array([c[0] for c in curve], dtype=np.int64)
    eq = np.array([c[1] for c in curve])
    return {"equity_curve": [{"time": int(a) // 1000, "value": float(b)} for a, b in curve], "taken": taken,
            "times": times, "eq": eq}


def daily_equity(curve_times: np.ndarray, curve_eq: np.ndarray, start_ms: int, end_ms: int) -> tuple:
    days = np.arange(start_ms // 86_400_000 * 86_400_000, end_ms + 86_400_000, 86_400_000)
    idx = np.searchsorted(curve_times, days, side="right") - 1
    idx = np.clip(idx, 0, len(curve_eq) - 1)
    return days, curve_eq[idx]


def run_backtest(data: dict[str, pd.DataFrame], cfg: BacktestConfig, progress=None) -> dict:
    t0 = time.time()
    all_setups: list[dict] = []
    for sym, df in data.items():
        all_setups += generate_setups(df, sym, cfg, progress)
    all_setups = with_exit_profile(all_setups, cfg.exit_profile)
    scenarios = {"base": (cfg.fee, cfg.slip_bps), "fees_x2": (cfg.fee * 2, cfg.slip_bps),
                 "slippage_x3": (cfg.fee, cfg.slip_bps * 3), "stress": (cfg.fee * 2, cfg.slip_bps * 5)}
    results: dict = {}
    start = min(int(df["open_time"].iloc[cfg.window]) for df in data.values())
    end = max(int(df["open_time"].iloc[-1]) for df in data.values())
    for name, (fee, slip) in scenarios.items():
        trades = []
        for sym, df in data.items():
            trades += simulate(df, [s for s in all_setups if s["symbol"] == sym], fee, slip)
        pf = portfolio(trades, cfg, start, end)
        days, deq = daily_equity(pf["times"], pf["eq"], start, end)
        rs = [t["r"] for t in pf["taken"]]
        res = {"trades": trade_stats(rs), "equity": equity_metrics(deq, days, 365),
               "max_drawdown": max_drawdown(deq)[0], "fill_rate": (sum(1 for t in trades if t["filled"]) / len(trades))
               if trades else None, "setups": len(trades)}
        if name == "base":
            res["by_strategy"] = breakdown(pf["taken"], "strategy")
            res["by_regime"] = breakdown(pf["taken"], "regime")
            res["by_symbol"] = breakdown(pf["taken"], "symbol")
            res["by_direction"] = breakdown(pf["taken"], "direction")
            split = start + (end - start) * (1 - cfg.oos_fraction)
            res["in_sample"] = trade_stats([t["r"] for t in pf["taken"] if t["entry_time"] < split])
            res["out_of_sample"] = trade_stats([t["r"] for t in pf["taken"] if t["entry_time"] >= split])
            res["equity_curve"] = pf["equity_curve"]
            res["trade_list"] = [{k: v for k, v in t.items() if k != "targets"} | {"targets": t["targets"]}
                                 for t in trades][-500:]
            exposure = sum((t["exit_time"] - t["entry_time"]) for t in pf["taken"]) / max(end - start, 1)
            res["exposure"] = exposure / max(len(data), 1)
            res["turnover_trades_per_month"] = len(pf["taken"]) / max((end - start) / (30 * 86_400_000), 1e-9)
        results[name] = res
    bh = {}
    for sym, df in data.items():
        a, b = df["close"].iloc[cfg.window], df["close"].iloc[-1]
        bh[sym] = float(b / a - 1)
    return {"config": asdict(cfg), "config_hash": cfg.hash(), "scenarios": results, "benchmark_buy_hold": bh,
            "period": {"start": start, "end": end}, "runtime_s": round(time.time() - t0, 1),
            "notes": ["Simulated results, not real trading performance.",
                      "Entries are limit fills inside the setup zone; same-bar stop/target ambiguity resolved as stop.",
                      "Funding payments not included for spot-data backtests.",
                      "Strategy parameters are fixed priors (not optimised on this data)."]}
