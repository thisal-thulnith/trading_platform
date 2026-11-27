"""Market context known at setup time, computed ONE way for history and for live (hypothesis round 2026-11-11,
docs/research/HYPOTHESES_2026-11-11.md).

The context dict is side-agnostic market state for (symbol, t_ms). `app.ml.meta_label.setup_row(..., context=...)` /
`live_setup_features(..., context=...)` turn it into per-setup, direction-aware features (CONTEXT_FEATURES there).

    btc_trend        +1 BTC's last completed daily close above its 50-day AND 200-day simple averages, -1 below both,
                     0 in between; NaN with fewer than 200 daily closes or no completed day at t
    mom_28d          the symbol's return over the last 28 completed days (close[D] / close[D-28] - 1)
    xs_mom_pct       mid-rank percentile (0..1) of mom_28d among the panel's symbols that have it at t (needs >= 3)
    funding_pct_90d  mid-rank percentile (0..1) of the last settled funding print among this symbol's prints of the
                     previous 90 days (needs >= 30 prints)
    pending_long / pending_short
                     number of this symbol's earlier engine setups (created strictly before t) on that side whose limit
                     is still waiting at t: not filled, not invalidated (stop traded first), not expired. Waiting rules
                     are TradeSim's (app/signals/simulator.py) on the edge entry of the setup's exit profile.

Time rule: a daily bar with open_time D*86400000 is known from (D+1)*86400000 on, so at t the last usable day is
D = floor(t / 1d) - 1, and it must exist in the data (no stale fallback). A 4h/1h setup's t is the close of its
setup bar (= the next bar's open_time), as in generate_setups and live_setup_features.

Live callers: build one DailyPanel per run from the daily candles of the universe (the backtest universe for 4h is
the 12 coins of data/meta_setups/4h_*: AAVE ADA ALGO APT AVAX BNB BTC DOGE ETH LINK SOL XRP; BTCUSDT must be in it),
then call `live_context(...)` per setup and pass the result as `context=` to live_setup_features. See
docs/DAILY_PICKS.md ("Hypothesis round") for the exact call.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np
import pandas as pd

DAY_MS = 86_400_000
DAILY_DIR = Path("data/history/1d")
MOM_DAYS = 28
SMA_FAST, SMA_SLOW = 50, 200
FUNDING_WINDOW_MS = 90 * DAY_MS
MIN_FUNDING_PRINTS = 30
MIN_XS = 3
NAN = math.nan


def load_daily(symbols: Iterable[str], root: Path | None = None) -> dict[str, pd.DataFrame]:
    """Daily candles from data/history/1d/{SYMBOL}.csv (open_time, close), for the symbols that have a file."""
    root = root or DAILY_DIR
    out = {}
    for s in symbols:
        p = root / f"{s}.csv"
        if p.exists():
            out[s] = pd.read_csv(p, usecols=["open_time", "close"])
    return out


def _midrank(values: np.ndarray, x: float) -> float:
    """Mid-rank percentile of x within values (x included in values): (below + 0.5 * (equal - 1)) / (n - 1)."""
    n = len(values)
    if n < 2:
        return NAN
    below = float(np.sum(values < x))
    equal = float(np.sum(values == x))
    return (below + 0.5 * (equal - 1)) / (n - 1)


class DailyPanel:
    """Daily closes of a universe, with per-day SMA50/SMA200 and 28-day momentum precomputed (whole days only)."""

    def __init__(self, daily: Mapping[str, pd.DataFrame], btc_symbol: str = "BTCUSDT"):
        self.btc_symbol = btc_symbol
        self.days: dict[str, np.ndarray] = {}
        self.close: dict[str, np.ndarray] = {}
        self.mom: dict[str, np.ndarray] = {}
        self.sma_f: dict[str, np.ndarray] = {}
        self.sma_s: dict[str, np.ndarray] = {}
        for sym, df in daily.items():
            d = df[["open_time", "close"]].drop_duplicates("open_time").sort_values("open_time")
            d = d[d["open_time"] % DAY_MS == 0]
            day = (d["open_time"].to_numpy() // DAY_MS).astype(np.int64)
            c = d["close"].to_numpy(dtype=float)
            s = pd.Series(c)
            self.days[sym], self.close[sym] = day, c
            # positional lookbacks are only valid when the days are contiguous; mark gaps as NaN
            contiguous_28 = np.r_[np.full(min(MOM_DAYS, len(day)), False),
                                  (day[MOM_DAYS:] - day[:-MOM_DAYS]) == MOM_DAYS] if len(day) > MOM_DAYS else \
                np.full(len(day), False)
            m = np.full(len(c), NAN)
            if len(c) > MOM_DAYS:
                m[MOM_DAYS:] = c[MOM_DAYS:] / c[:-MOM_DAYS] - 1
            self.mom[sym] = np.where(contiguous_28, m, NAN)
            self.sma_f[sym] = s.rolling(SMA_FAST).mean().to_numpy()
            self.sma_s[sym] = s.rolling(SMA_SLOW).mean().to_numpy()

    def _idx(self, sym: str, t_ms: int) -> int | None:
        """Index of the last completed day at t_ms (day floor(t/1d) - 1), or None if that day is missing."""
        days = self.days.get(sym)
        if days is None or not len(days):
            return None
        want = int(t_ms) // DAY_MS - 1
        i = int(np.searchsorted(days, want))
        return i if i < len(days) and days[i] == want else None

    def btc_trend(self, t_ms: int) -> float:
        sym = self.btc_symbol
        i = self._idx(sym, t_ms)
        if i is None:
            return NAN
        c, f, s = self.close[sym][i], self.sma_f[sym][i], self.sma_s[sym][i]
        if not (np.isfinite(f) and np.isfinite(s)):
            return NAN
        if c > f and c > s:
            return 1.0
        if c < f and c < s:
            return -1.0
        return 0.0

    def momentum(self, sym: str, t_ms: int) -> float:
        i = self._idx(sym, t_ms)
        return float(self.mom[sym][i]) if i is not None else NAN

    def xs_momentum(self, sym: str, t_ms: int) -> tuple[float, float, int]:
        """(mom_28d of sym, its mid-rank percentile among the panel at t, number of symbols ranked)."""
        mine = self.momentum(sym, t_ms)
        if not math.isfinite(mine):
            return NAN, NAN, 0
        vals = np.array([v for s in self.days if math.isfinite(v := self.momentum(s, t_ms))])
        if len(vals) < MIN_XS:
            return mine, NAN, int(len(vals))
        return mine, float(_midrank(vals, mine)), int(len(vals))


def funding_pct(funding: pd.DataFrame | None, t_ms: int) -> float:
    """Mid-rank percentile of the last settled funding print at t_ms within the prints of the previous 90 days."""
    if funding is None or not len(funding):
        return NAN
    tm = funding["time"].to_numpy()
    i = int(np.searchsorted(tm, t_ms, side="right"))
    if i == 0:
        return NAN
    j = int(np.searchsorted(tm, tm[i - 1] - FUNDING_WINDOW_MS, side="left"))
    vals = funding["rate"].to_numpy(dtype=float)[j:i]
    if len(vals) < MIN_FUNDING_PRINTS:
        return NAN
    return float(_midrank(vals, float(vals[-1])))


# ---------------------------------------------------------------- pending (unfilled) earlier setups

def edge_entry(setup: Mapping) -> float:
    """The edge limit price the exit profiles use (entry_high for longs, entry_low for shorts)."""
    return float(setup["entry_high"] if setup["direction"] == "LONG" else setup["entry_low"])


def pending_end_time(setup: Mapping, open_time: np.ndarray, o: np.ndarray, h: np.ndarray, low: np.ndarray,
                     tf_ms: int) -> int | None:
    """Close time of the bar on which the setup's limit stops waiting (filled, invalidated or expired), using
    TradeSim's waiting rules on the bars that open at/after the setup time. None if it is still waiting at the end of
    the given bars (live: still pending now)."""
    t0 = int(setup["time"])
    e = edge_entry(setup)
    stop = float(setup["stop"])
    long = setup["direction"] == "LONG"
    expiry = int(setup.get("expiry_bars") or 0)
    j = int(np.searchsorted(open_time, t0, side="left"))
    waited = 0
    while j < len(open_time):
        waited += 1
        if long:
            ended = o[j] <= stop or low[j] <= e or low[j] <= stop
        else:
            ended = o[j] >= stop or h[j] >= e or h[j] >= stop
        if ended or waited >= expiry:
            return int(open_time[j]) + tf_ms
        j += 1
    return None


def pending_counts(t_ms: int, earlier: Iterable[Mapping]) -> dict[str, float]:
    """pending_long / pending_short at t_ms from earlier setups that carry "time", "direction" and "pending_end"
    (pending_end_time, None = still waiting). Setups created at/after t_ms are ignored."""
    n = {"LONG": 0, "SHORT": 0}
    for s in earlier:
        if int(s["time"]) < t_ms and (s.get("pending_end") is None or int(s["pending_end"]) > t_ms):
            n[s["direction"]] += 1
    return {"pending_long": float(n["LONG"]), "pending_short": float(n["SHORT"])}


def annotate_pending(setups: list[dict], candles: pd.DataFrame, tf_ms: int) -> list[dict]:
    """Copies of setups with "pending_end" added (see pending_end_time). candles: the symbol's bars (open_time, open,
    high, low) covering the setups; live, the closed bars up to now."""
    tm = candles["open_time"].to_numpy()
    o, h, lo = (candles[k].to_numpy(dtype=float) for k in ("open", "high", "low"))
    return [{**s, "pending_end": pending_end_time(s, tm, o, h, lo, tf_ms)} for s in setups]


def market_context(symbol: str, t_ms: int, panel: DailyPanel | None = None, funding: pd.DataFrame | None = None,
                   earlier_setups: Iterable[Mapping] | None = None) -> dict[str, float]:
    """The context dict for one setup of `symbol` created at t_ms. Missing inputs give NaN entries (and filters
    that need them reject the setup: fail-closed). earlier_setups: the symbol's engine setups with "pending_end"
    (annotate_pending); None -> pending counts NaN."""
    ctx: dict[str, float] = {"btc_trend": NAN, "mom_28d": NAN, "xs_mom_pct": NAN, "funding_pct_90d": NAN,
                             "pending_long": NAN, "pending_short": NAN}
    if panel is not None:
        ctx["btc_trend"] = panel.btc_trend(t_ms)
        ctx["mom_28d"], ctx["xs_mom_pct"], _ = panel.xs_momentum(symbol, t_ms)
    ctx["funding_pct_90d"] = funding_pct(funding, t_ms)
    if earlier_setups is not None:
        ctx.update(pending_counts(t_ms, earlier_setups))
    return ctx


def live_context(symbol: str, df: pd.DataFrame, tf: str, panel: DailyPanel | None = None,
                 funding: pd.DataFrame | None = None, recent_setups: list[dict] | None = None) -> dict[str, float]:
    """Context for a live setup on the last closed bar of df (t = last open_time + tf, as live_setup_features uses).
    recent_setups: the symbol's engine setups (generate_setups records: time, direction, entry_low, entry_high, stop,
    expiry_bars) from at least the last 12 bars (the longest expiry), created before t."""
    from app.data.timeframes import tf_ms as _tf_ms

    step = _tf_ms(tf)
    t_ms = int(df["open_time"].iloc[-1]) + step
    earlier = None
    if recent_setups is not None:
        earlier = annotate_pending([s for s in recent_setups if int(s["time"]) < t_ms], df, step)
    return market_context(symbol, t_ms, panel, funding, earlier)


def recent_engine_setups(df: pd.DataFrame, symbol: str, tf: str, lookback_bars: int = 14) -> list[dict]:
    """The engine's confirmed, validated setups on the `lookback_bars` closed bars before the last one in df, built
    exactly like app.backtest.engine.generate_setups (same window, HTFs from up to 8 windows of history) but scanning
    only those bars. Costs ~20 ms per bar per symbol on 4h. Caveat: generate_setups de-duplicates by
    strategy|direction|anchor over its whole history; here only inside the scanned bars, so live counts can be
    slightly higher than the historical ones. Keep this loop in sync with generate_setups."""
    from app.analysis.analyzer import build_snapshot
    from app.backtest.engine import resample_closed
    from app.data.timeframes import tf_ms as _tf_ms
    from app.ml.meta_label import HTF, WINDOW
    from app.strategies.evaluate import enrich, evaluate_strategies

    n = len(df)
    step_ms = _tf_ms(tf)
    out: list[dict] = []
    seen: set[str] = set()
    for t in range(max(WINDOW, n - 1 - lookback_bars), n - 1):
        win = df.iloc[t - WINDOW + 1: t + 1].reset_index(drop=True)
        hist = df.iloc[max(0, t - WINDOW * 8): t + 1]
        htf = {h: resample_closed(hist, tf, h).tail(200).reset_index(drop=True) for h in HTF[tf]}
        try:
            snap = enrich(build_snapshot(symbol, tf, win, htf, light=True), win)
        except ValueError:
            continue
        now = int(win["open_time"].iloc[-1]) + step_ms
        for c in evaluate_strategies(snap, now_ms=now):
            if c.get("stage") != "confirmed" or not c["validation"]["passed"]:
                continue
            key = f"{c['strategy']}|{c['direction']}|{c['anchor_id']}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"symbol": symbol, "bar": t, "time": now, "strategy": c["strategy"],
                        "direction": c["direction"], "entry_low": c["entry_low"], "entry_high": c["entry_high"],
                        "stop": c["stop"], "expiry_bars": c["expiry_bars"]})
    return out
