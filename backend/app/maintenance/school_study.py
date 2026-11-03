"""Does each school of thought (app/analysis/schools.py) actually call direction? Measured, not assumed.

    uv run python -m app.maintenance.school_study --tf 4h

Every `step` bars on every symbol, each school gives a verdict from closed candles only; we record the forward return
over `horizon` bars. A school "hits" when the sign of the forward return matches its verdict. Because markets drift,
the baseline is the hit rate of always calling the drift direction; a school is only useful if it beats that and its
signed mean return is positive. Split by time: first 70 % in-sample (IS), last 30 % out-of-sample (OOS).
Results go to data/setups/schools_{tf}.json and are shown by the War Room agents as their track record.
"""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime

import numpy as np

from app.analysis.schools import SCHOOLS, run_schools
from app.maintenance.exit_study import OUT, load_df, symbols

CFG = {"1h": {"step": 6, "horizon": 24}, "4h": {"step": 3, "horizon": 20}}
WINDOW = 400


def _one(args: tuple[str, str]) -> list[dict]:
    sym, tf = args
    df = load_df(sym, tf)
    step, hz = CFG[tf]["step"], CFG[tf]["horizon"]
    c = df["close"].to_numpy()
    rows = []
    for i in range(WINDOW, len(df) - hz, step):
        w = df.iloc[i - WINDOW:i].reset_index(drop=True)  # bars < i are closed at the open of bar i
        now = datetime.fromtimestamp(int(df["open_time"].iloc[i]) / 1000, UTC)
        steps = run_schools(w, tf, now)
        fwd = float(c[i - 1 + hz] / c[i - 1] - 1)
        rows.append({"t": int(df["open_time"].iloc[i]), "fwd": fwd, **{s["key"]: s["verdict"] for s in steps}})
    return rows


def _stats(rows: list[dict], key: str, overlap: float = 1.0) -> dict:
    sig = [(r[key], r["fwd"]) for r in rows if r[key] != 0]
    if not sig:
        return {"n": 0}
    v = np.array([s[0] for s in sig])
    f = np.array([s[1] for s in sig])
    signed = v * f
    up = float(np.mean([r["fwd"] > 0 for r in rows]))
    # forward windows overlap (horizon > step), so the effective sample is n / overlap; widen the CI accordingly
    se = float(signed.std(ddof=1) / np.sqrt(len(signed) / overlap)) if len(signed) > 1 else float("nan")
    return {"n": len(sig), "coverage": len(sig) / len(rows), "hit_rate": float(np.mean(signed > 0)),
            "baseline": max(up, 1 - up), "mean_signed_pct": float(signed.mean() * 100),
            "ci95_pct": [float((signed.mean() - 1.96 * se) * 100), float((signed.mean() + 1.96 * se) * 100)]}


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "n=0"
    return (f"n={s['n']:5d} hit={s['hit_rate']:.0%} (base {s['baseline']:.0%}) mean={s['mean_signed_pct']:+.2f}% "
            f"CI[{s['ci95_pct'][0]:+.2f},{s['ci95_pct'][1]:+.2f}]")


def study(tf: str, workers: int) -> dict:
    syms = symbols(tf, 5000)
    with ProcessPoolExecutor(workers) as ex:
        rows = [r for part in ex.map(_one, [(s, tf) for s in syms]) for r in part]
    for r in rows:  # combined vote: majority of non-neutral schools
        net = sum(r[k] for k in SCHOOLS)
        r["vote"] = 1 if net >= 2 else -1 if net <= -2 else 0
    ts = sorted(r["t"] for r in rows)
    split = ts[int(len(ts) * 0.7)]
    out = {"timeframe": tf, "horizon_bars": CFG[tf]["horizon"], "symbols": syms, "n_obs": len(rows), "split_ms": split,
           "schools": {}}
    print(f"{tf}: {len(syms)} symbols, {len(rows)} observations, horizon {CFG[tf]['horizon']} bars")
    for k in [*SCHOOLS, "vote"]:
        ov = CFG[tf]["horizon"] / CFG[tf]["step"]
        is_ = _stats([r for r in rows if r["t"] < split], k, ov)
        oos = _stats([r for r in rows if r["t"] >= split], k, ov)
        # 11 hypotheses per timeframe: demand significance in BOTH halves, not a lucky OOS
        useful = bool(is_.get("n", 0) >= 100 and oos.get("n", 0) >= 50 and is_["ci95_pct"][0] > 0
                      and oos["ci95_pct"][0] > 0)
        out["schools"][k] = {"is": is_, "oos": oos, "useful": useful}
        print(f"  {k:10s} IS {_fmt(is_)}\n  {'':10s} OOS {_fmt(oos)}{'  ✓ useful' if useful else ''}")
    (OUT / f"schools_{tf}.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h", choices=list(CFG))
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    study(a.tf, a.workers)
