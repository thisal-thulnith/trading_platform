"""Exit-rule & filter study on cached setups, with an honest in-sample / out-of-sample split.

    uv run python -m app.maintenance.exit_study gen   --tf 1h   # slow: one snapshot per bar, cached to data/setups/
    uv run python -m app.maintenance.exit_study study --tf 1h   # fast: re-simulates every profile x filter

Protocol (pre-registered): setups are split by time, first 70 % in-sample (IS), last 30 % out-of-sample (OOS).
Profiles and filters are ranked on IS only. The chosen configuration is then evaluated ONCE on OOS — that number,
not the IS number, is what gets reported.
"""
import argparse
import json
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from app.backtest.engine import BacktestConfig, generate_setups, resample_closed
from app.backtest.metrics import trade_stats
from app.signals.simulator import TradeSim

DB = Path("data/aitrade.db")
OUT = Path("data/setups")
HTF = {"15m": ["1h", "4h"], "1h": ["4h", "1d"], "4h": ["1d"]}
FEE, SLIP = 0.0005, 2.0


def load_df(symbol: str, tf: str) -> pd.DataFrame:
    base = "15m" if tf == "15m" else "1h"
    with sqlite3.connect(DB) as c:
        df = pd.read_sql_query("select open_time, open, high, low, close, volume from candles where symbol=? and "
                               "timeframe=? and market='spot' order by open_time", c, params=(symbol, base))
    if tf == "4h":
        df = resample_closed(df, "1h", "4h").reset_index(drop=True)
    return df


def symbols(tf: str, min_bars: int) -> list[str]:
    base = "15m" if tf == "15m" else "1h"
    with sqlite3.connect(DB) as c:
        return [r[0] for r in c.execute("select symbol from candles where timeframe=? and market='spot' group by symbol "
                                        "having count(*) >= ?", (base, min_bars))]


def _gen_one(args: tuple[str, str]) -> tuple[str, int]:
    sym, tf = args
    df = load_df(sym, tf)
    cfg = BacktestConfig(symbols=[sym], timeframe=tf, htf=HTF[tf], window=300)
    setups = generate_setups(df, sym, cfg)
    (OUT / f"{tf}_{sym}.json").write_text(json.dumps(setups))
    return sym, len(setups)


def gen(tf: str, workers: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    syms = symbols(tf, 5000 if tf != "15m" else 5000)
    todo = [(s, tf) for s in syms if not (OUT / f"{tf}_{s}.json").exists()]
    print(f"{tf}: {len(syms)} symbols, {len(todo)} to generate")
    with ProcessPoolExecutor(workers) as ex:
        for sym, n in ex.map(_gen_one, todo):
            print(f"  {sym}: {n} setups", flush=True)


# ---------------------------------------------------------------- exit profiles

def apply_profile(s: dict, prof: dict) -> dict:
    """Return a setup with the profile's entry/targets. 'edge' = single limit at the zone edge nearest price; risk is
    measured from that limit, so a normal stop-out costs ~1R instead of up to 2R+ when fills land at the far edge."""
    s = dict(s)
    long = s["direction"] == "LONG"
    if prof["entry"] == "edge":
        edge = s["entry_high"] if long else s["entry_low"]
        s["entry_low"] = s["entry_high"] = edge
    if prof["targets"] != "orig":
        ref = s["entry_high"] if long else s["entry_low"]
        risk = abs(ref - s["stop"])
        sgn = 1 if long else -1
        s["targets"] = [ref + sgn * m * risk for m in prof["targets"]]
    return s


def simulate(df: pd.DataFrame, setups: list[dict], prof: dict) -> list[dict]:
    o, h, lo, c, tm = (df[k].to_numpy() for k in ("open", "high", "low", "close", "open_time"))
    out, busy = [], {}
    for s0 in setups:
        key = f"{s0['strategy']}"
        if busy.get(key, -1) >= s0["bar"]:
            continue
        s = apply_profile(s0, prof)
        sim = TradeSim(s["direction"], s["entry_low"], s["entry_high"], s["stop"], list(s["targets"]),
                       expiry_bars=s["expiry_bars"], max_hold_bars=prof.get("hold", max(3 * s["hold_bars"], 24)),
                       fee=FEE, slip_bps=SLIP)
        j = s["bar"] + 1
        while j < len(df) and not sim.terminal:
            sim.on_bar(int(tm[j]), o[j], h[j], lo[j], c[j])
            j += 1
        busy[key] = j - 1
        if sim.fill is not None and sim.terminal:
            out.append({**s0, "r": sim.realized_r, "entry_time": sim.fill_time, "outcome": sim.outcome})
    return out


PROFILES = {"current (zone, 3 TPs)": {"entry": "zone", "targets": "orig"},
            "edge, 3 TPs": {"entry": "edge", "targets": "orig"}}
for tps in ([0.5], [0.75], [1.0], [1.5], [2.0], [0.5, 1.5], [0.75, 2.0], [1.0, 2.0], [1.0, 3.0], [0.5, 1.0, 2.0]):
    PROFILES[f"edge, TP {'/'.join(str(x) for x in tps)}R"] = {"entry": "edge", "targets": tps}

FILTERS = {
    "all": lambda s: True,
    "confluence agrees": lambda s: (s["bias"] > 0) == (s["direction"] == "LONG"),
    "quality>=50": lambda s: s["quality"] >= 50,
    "trending regime": lambda s: "trend" in (s["regime"] or "") or "bull" in (s["regime"] or "")
    or "bear" in (s["regime"] or ""),
    "confluence agrees + quality>=50": lambda s: (s["bias"] > 0) == (s["direction"] == "LONG") and s["quality"] >= 50,
}


def fmt(st: dict) -> str:
    if not st.get("n"):
        return "n=0"
    ci = st.get("expectancy_ci95") or [float("nan")] * 2
    return (f"n={st['n']:4d} win={st['win_rate'] * 100:3.0f}% E={st['expectancy_r']:+.3f}R "
            f"CI[{ci[0]:+.2f},{ci[1]:+.2f}] PF={st.get('profit_factor', 0):.2f}")


def study(tf: str, min_n: int = 60) -> dict:
    files = sorted(OUT.glob(f"{tf}_*.json"))
    data = {f.stem.split("_", 1)[1]: json.loads(f.read_text()) for f in files}
    dfs = {sym: load_df(sym, tf) for sym in data}
    times = sorted(s["time"] for ss in data.values() for s in ss)
    split = times[int(len(times) * 0.7)]
    print(f"{tf}: {len(data)} symbols, {len(times)} setups, IS/OOS split at {pd.to_datetime(split, unit='ms')}")
    rows = []
    for pname, prof in PROFILES.items():
        trades = [t for sym, ss in data.items() for t in simulate(dfs[sym], ss, prof)]
        for fname, flt in FILTERS.items():
            sel = [t for t in trades if flt(t)]
            is_ = [t for t in sel if t["time"] < split]
            oos = [t for t in sel if t["time"] >= split]
            strat_is = {}
            for t in is_:
                strat_is.setdefault(t["strategy"], []).append(t["r"])
            good = {k for k, v in strat_is.items() if len(v) >= 30 and sum(v) / len(v) > 0}
            for sname, keep in (("all strategies", None), ("IS-positive strategies", good)):
                a = [t["r"] for t in is_ if keep is None or t["strategy"] in keep]
                b = [t["r"] for t in oos if keep is None or t["strategy"] in keep]
                rows.append({"profile": pname, "filter": fname, "strategies": sname,
                             "kept": sorted(keep) if keep is not None else "all",
                             "is": trade_stats(a), "oos": trade_stats(b)})
    ranked = sorted([r for r in rows if r["is"].get("n", 0) >= min_n], key=lambda r: -r["is"]["expectancy_r"])
    print("\nTop 10 by IN-SAMPLE expectancy (OOS shown for honesty):")
    for r in ranked[:10]:
        print(f"  [{r['profile']} | {r['filter']} | {r['strategies']}]\n     IS  {fmt(r['is'])}\n     OOS {fmt(r['oos'])}")
    print("\nHighest IS win rate with n>=min_n:")
    for r in sorted([r for r in rows if r["is"].get("n", 0) >= min_n], key=lambda r: -r["is"]["win_rate"])[:5]:
        print(f"  [{r['profile']} | {r['filter']} | {r['strategies']}]\n     IS  {fmt(r['is'])}\n     OOS {fmt(r['oos'])}")
    chosen = ranked[0] if ranked else None
    res = {"timeframe": tf, "split_ms": split, "n_setups": len(times), "symbols": sorted(data), "chosen": chosen,
           "rows": rows}
    (OUT / f"study_{tf}.json").write_text(json.dumps(res, default=str))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gen", "study"])
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    gen(a.tf, a.workers) if a.cmd == "gen" else study(a.tf)
