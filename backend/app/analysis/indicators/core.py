"""Indicator registry and shared math primitives.

Conventions:
- Input is a DataFrame with columns open, high, low, close, volume (+ optional taker_buy_base, open_time ms).
- Outputs are float numpy arrays aligned to the input index; warm-up values are NaN.
- Every value at index i depends only on data at indices <= i (no look-ahead). This is enforced by
  tests/test_indicators.py::test_no_lookahead for every registered indicator.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class Param:
    name: str
    default: Any
    min: float | None = None
    max: float | None = None
    kind: str = "int"  # int | float | str | time


@dataclass
class Output:
    name: str
    pane: str = "overlay"  # overlay | separate
    style: str = "line"  # line | histogram | dots | band_upper | band_lower | area
    color: str | None = None


@dataclass
class Indicator:
    id: str
    name: str
    category: str  # trend | momentum | volatility | volume
    params: list[Param]
    outputs: list[Output]
    fn: Callable[..., dict[str, np.ndarray]]
    family: str = ""  # evidence family for redundancy control (e.g. "oscillator_momentum")
    description: str = ""
    levels: list[float] = field(default_factory=list)  # reference lines for separate panes

    def compute(self, df: pd.DataFrame, **params) -> dict[str, np.ndarray]:
        p = {x.name: x.default for x in self.params}
        for k, v in params.items():
            if k in p:
                spec = next(x for x in self.params if x.name == k)
                v = int(v) if spec.kind == "int" else float(v) if spec.kind == "float" else v
                if spec.min is not None and isinstance(v, (int, float)) and v < spec.min:
                    raise ValueError(f"{self.id}.{k} must be >= {spec.min}")
                if spec.max is not None and isinstance(v, (int, float)) and v > spec.max:
                    raise ValueError(f"{self.id}.{k} must be <= {spec.max}")
                p[k] = v
        out = self.fn(df, **p)
        return {k: np.asarray(v, dtype=float) for k, v in out.items()}

    def meta(self) -> dict:
        return {
            "id": self.id, "name": self.name, "category": self.category, "family": self.family,
            "description": self.description, "levels": self.levels,
            "params": [p.__dict__ for p in self.params], "outputs": [o.__dict__ for o in self.outputs],
        }


REGISTRY: dict[str, Indicator] = {}


def register(ind: Indicator) -> Indicator:
    if ind.id in REGISTRY:
        raise ValueError(f"duplicate indicator {ind.id}")
    REGISTRY[ind.id] = ind
    return ind


def indicator(id: str, name: str, category: str, params: list[Param], outputs: list[Output],
              family: str = "", description: str = "", levels: list[float] | None = None):
    def deco(fn):
        register(Indicator(id, name, category, params, outputs, fn, family or category, description,
                           levels or []))
        return fn
    return deco


def compute(id: str, df: pd.DataFrame, **params) -> dict[str, np.ndarray]:
    return REGISTRY[id].compute(df, **params)


# ---------------------------------------------------------------- primitives

def _s(x) -> pd.Series:
    return x if isinstance(x, pd.Series) else pd.Series(np.asarray(x, dtype=float))


def sma(x, n: int) -> pd.Series:
    return _s(x).rolling(n, min_periods=n).mean()


def _seeded_recursive(x: pd.Series, n: int, alpha: float) -> pd.Series:
    """EMA-style recursion seeded with the SMA of the first n valid values (textbook / TradingView RMA)."""
    v = x.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    valid = np.where(~np.isnan(v))[0]
    if len(valid) < n:
        return pd.Series(out, index=x.index)
    start = valid[0]
    # Require n consecutive valid values for the seed.
    seed_end = start + n - 1
    if np.isnan(v[start : seed_end + 1]).any():
        s = pd.Series(v)
        first_full = s.rolling(n, min_periods=n).mean().first_valid_index()
        if first_full is None:
            return pd.Series(out, index=x.index)
        seed_end = int(first_full)
    out[seed_end] = np.nanmean(v[seed_end - n + 1 : seed_end + 1])
    prev = out[seed_end]
    for i in range(seed_end + 1, len(v)):
        if not np.isnan(v[i]):
            prev = alpha * v[i] + (1 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=x.index)


def ema(x, n: int) -> pd.Series:
    return _seeded_recursive(_s(x), n, 2.0 / (n + 1))


def rma(x, n: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/n)."""
    return _seeded_recursive(_s(x), n, 1.0 / n)


def wma(x, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return _s(x).rolling(n, min_periods=n).apply(lambda a: np.dot(a, w) / w.sum(), raw=True)


def true_range(df: pd.DataFrame) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - low, (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - low.iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def stdev(x, n: int) -> pd.Series:
    return _s(x).rolling(n, min_periods=n).std(ddof=0)


def highest(x, n: int) -> pd.Series:
    return _s(x).rolling(n, min_periods=n).max()


def lowest(x, n: int) -> pd.Series:
    return _s(x).rolling(n, min_periods=n).min()


def to_json_series(arr: np.ndarray, times: np.ndarray) -> list[dict]:
    return [{"time": int(t) // 1000, "value": float(v)} for t, v in zip(times, arr) if np.isfinite(v)]
