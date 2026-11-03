"""Funding carry study: hold spot + short perp (price-neutral) on the coins paying the most funding.

    uv run python -m app.maintenance.carry_study

Rule (fixed before looking): every Monday rank coins by their average funding over the past 7 days; hold the top K
whose 7-day average is above ENTER_APR (keep a held coin while it stays above EXIT_APR). Half of each slot is the spot
coin, half is margin for a 1x short perp, so the account earns half the funding rate on the slot. Round-trip cost per
coin switch: spot fee 0.1 % x2 + perp fee 0.05 % x2 + slippage 0.05 % x4 = 0.5 % of the coin's notional.
Ignored (stated, not hidden): spot/perp basis wiggles, the rare exchange/ADL risk, and the need to top up perp margin
after big rallies. Periods: train 2020-23, valid 2024, test 2025-now.
"""
import json
from pathlib import Path

import pandas as pd

F = Path("data/history/funding")
OUT = Path("data/history/carry_study.json")
COST = 0.005


def daily_funding() -> pd.DataFrame:
    cols = {}
    for p in sorted(F.glob("*.csv")):
        d = pd.read_csv(p)
        d["day"] = pd.to_datetime(d.time, unit="ms", utc=True).dt.floor("D")
        cols[p.stem] = d.groupby("day").rate.sum()
    return pd.DataFrame(cols).sort_index()


def simulate(fd: pd.DataFrame, k: int, enter_apr: float, exit_apr: float, start: str, end: str | None) -> dict:
    trail = fd.rolling(7, min_periods=7).mean() * 365  # APR of the funding rate
    days = fd.index[(fd.index >= pd.Timestamp(start, tz="UTC")) & ((fd.index <= pd.Timestamp(end, tz="UTC"))
                                                                  if end else True)]
    held: set[str] = set()
    eq, curve, switches = 1.0, [], 0
    for day in days:
        prev = day - pd.Timedelta(days=1)
        if day.weekday() == 0 and prev in trail.index:
            t = trail.loc[prev].dropna()
            keep = {c for c in held if t.get(c, -1) > exit_apr}
            fresh = [c for c in t.sort_values(ascending=False).index if t[c] > enter_apr and c not in keep]
            new = set(list(keep) + fresh[: max(0, k - len(keep))])
            changed = len(new ^ held)
            eq -= eq * changed / k * 0.5 * COST  # each slot is 1/k of capital, notional is half the slot
            switches += changed
            held = new
        if held:
            r = fd.loc[day, list(held)].fillna(0).sum() / k * 0.5
            eq *= 1 + r
        curve.append(eq)
    s = pd.Series(curve, index=days)
    yrs = len(s) / 365
    dd = float(((s.cummax() - s) / s.cummax()).max() * 100)
    return {"end_per_100": round(100 * s.iloc[-1], 2), "yearly_pct": round((s.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
            "max_drawdown_pct": round(dd, 2), "switches": switches,
            "invested_days_pct": None}


if __name__ == "__main__":
    fd = daily_funding()
    periods = {"train 2020-23": ("2020-01-01", "2023-12-31"), "valid 2024": ("2024-01-01", "2024-12-31"),
               "TEST 2025-now": ("2025-01-01", None)}
    rows = []
    for k in (3, 5):
        for enter in (0.10, 0.20):
            row = {"k": k, "enter_apr": enter, "exit_apr": enter / 2}
            for name, (a, b) in periods.items():
                row[name] = simulate(fd, k, enter, enter / 2, a, b)
            rows.append(row)
            print(json.dumps(row))
    print("\nBTC+ETH average funding APR by year:")
    for y in range(2020, 2027):
        x = fd[fd.index.year == y][["BTCUSDT", "ETHUSDT"]].mean() * 365 * 100
        print(y, x.round(1).to_dict())
    OUT.write_text(json.dumps({"protocol": __doc__, "rows": rows}, indent=1))
