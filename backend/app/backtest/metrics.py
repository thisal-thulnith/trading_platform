"""Performance metrics. All metrics report sample size; small samples are flagged."""
import math

import numpy as np


def max_drawdown(equity: np.ndarray) -> tuple[float, int, int]:
    if len(equity) == 0:
        return 0.0, 0, 0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1
    i = int(np.argmin(dd))
    j = int(np.argmax(equity[: i + 1])) if i > 0 else 0
    return float(dd[i]), j, i


def probabilistic_sharpe(sr: float, n: int, skew: float, kurt: float, sr_benchmark: float = 0.0) -> float | None:
    """Bailey & López de Prado (2012) PSR on per-period Sharpe."""
    if n < 3:
        return None
    den = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / den
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def trade_stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "small_sample": True}
    a = np.array(rs)
    wins, losses = a[a > 0], a[a <= 0]
    streak = best = 0
    for r in a:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    rng = np.random.default_rng(0)
    boots = rng.choice(a, size=(2000, n), replace=True).mean(axis=1) if n >= 5 else np.array([a.mean()])
    q05 = np.quantile(a, 0.05)
    return {
        "n": n, "small_sample": n < 30,
        "win_rate": float((a > 0).mean()),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "expectancy_r": float(a.mean()),
        "expectancy_ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "total_r": float(a.sum()),
        "max_consecutive_losses": int(best),
        "cvar5_r": float(a[a <= q05].mean()),
        "best_r": float(a.max()), "worst_r": float(a.min()),
    }


def equity_metrics(equity: np.ndarray, times_ms: np.ndarray, bars_per_year: float) -> dict:
    if len(equity) < 2:
        return {}
    rets = np.diff(equity) / equity[:-1]
    mu, sd = rets.mean(), rets.std(ddof=1)
    down = rets[rets < 0]
    dsd = math.sqrt((down ** 2).mean()) if len(down) else 0.0
    years = (times_ms[-1] - times_ms[0]) / (365 * 86_400_000)
    total = equity[-1] / equity[0] - 1
    mdd, _, _ = max_drawdown(equity)
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 and equity[-1] > 0 else None
    sr = mu / sd if sd > 0 else 0.0
    skew = float(((rets - mu) ** 3).mean() / sd ** 3) if sd > 0 else 0.0
    kurt = float(((rets - mu) ** 4).mean() / sd ** 4) if sd > 0 else 3.0
    return {
        "net_return": float(total), "cagr": float(cagr) if cagr is not None else None, "years": years,
        "sharpe": float(sr * math.sqrt(bars_per_year)), "sortino": float(mu / dsd * math.sqrt(bars_per_year)) if dsd else None,
        "max_drawdown": mdd, "calmar": float(cagr / -mdd) if cagr is not None and mdd < 0 else None,
        "psr": probabilistic_sharpe(sr, len(rets), skew, kurt),
        "periods": len(rets),
    }


def breakdown(trades: list[dict], key: str) -> dict:
    out: dict = {}
    for t in trades:
        out.setdefault(str(t.get(key)), []).append(t["r"])
    return {k: trade_stats(v) for k, v in out.items()}
