"""Coin simulator service: builds the point-in-time market once (cached per panel version), runs one coin or every
coin, keeps background jobs and the last all-coins result on disk (data/sim/)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from app.logging import log
from app.sim.engine import (
    START,
    Market,
    SimConfig,
    bh,
    fuse,
    hold_curve,
    monte_carlo,
    null_test,
    run_coin,
    stats,
    trade_stats,
)

DIR = Path("data/sim")
COMPONENTS = ("momentum", "carry", "regime", "order_flow", "quant_factors")
_mkt: dict = {"key": None, "m": None}
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _panel_key() -> str:
    from app.quant.lab import data as qd

    return "|".join(str(p.stat().st_mtime_ns) if p.exists() else "-" for p in (qd.PANEL, qd.FUNDING_DAILY))


def market() -> Market:
    """Every coin's prices, costs, funding and point-in-time component scores (built once per panel version, ~15 s)."""
    with _lock:
        key = _panel_key()
        if _mkt["m"] is not None and _mkt["key"] == key:
            return _mkt["m"]
        from app.quant.lab import data as qd
        from app.quant.lab.factors import (
            FACTORS,
            btc_regime,
            build_wide,
            compute_factors,
            cost_rate,
            forward_returns,
            rank_ic,
            universe,
        )
        from app.quant.lab.research import screen
        from app.sim import engine as E

        t0 = time.time()
        w = build_wide(qd.load_panel(), qd.load_funding_daily())
        facs = compute_factors(w)
        univ = universe(w)
        fwd7 = forward_returns(w, 7)
        ic7 = pd.DataFrame({f: rank_ic(facs[f], fwd7, univ) for f in FACTORS})
        years = sorted(set(w.dates.year))
        signs = E.pit_signs(ic7, years, screen)
        comp = E.composite({f: facs[f] for f in FACTORS}, univ, signs)
        comps = E.components(w, facs, univ, comp, btc_regime(w))
        m = Market(dates=w.dates, symbols=w.symbols, o=w.open.to_numpy(), h=w.high.to_numpy(), l=w.low.to_numpy(),
                   c=w.close.to_numpy(), univ=univ.to_numpy(), cost=cost_rate(w).to_numpy(),
                   fund=w.funding.to_numpy(), atr=E.atr14(w).to_numpy(),
                   comps={k: v.to_numpy() for k, v in comps.items()},
                   signs={str(y): s for y, s in signs.items()})
        _mkt.update(key=key, m=m)
        log.info("sim_market_built", coins=len(w.symbols), days=len(w.dates), secs=round(time.time() - t0, 1))
        return m


def weights(mode: str) -> dict[str, float]:
    from app.signal import evidence

    if mode == "prior":
        return {k: evidence.PRIORS[k][0] for k in COMPONENTS}
    reg = evidence.registry()
    return {k: float(reg["components"].get(k, {}).get("weight", 0.0)) for k in COMPONENTS}


def _range(m: Market, cfg: SimConfig) -> tuple[int, int]:
    t0 = int(m.dates.searchsorted(pd.Timestamp(cfg.start)))
    t1 = int(m.dates.searchsorted(pd.Timestamp(cfg.end), side="right")) - 1 if cfg.end else len(m.dates) - 1
    return t0, max(t0, t1)


def py(o):
    """numpy scalars and NaN -> plain JSON values."""
    if isinstance(o, dict):
        return {k: py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [py(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _ms(d) -> int:
    return int(pd.Timestamp(d).timestamp() * 1000)


def _thin(n: int, k: int = 500) -> range:
    return range(0, n, max(1, n // k))


def coins() -> list[dict]:
    m = market()
    has = np.isfinite(m.c)
    out = []
    for j, s in enumerate(m.symbols):
        idx = np.flatnonzero(has[:, j])
        if not len(idx):
            continue
        out.append({"symbol": s, "first": str(m.dates[idx[0]].date()), "last": str(m.dates[idx[-1]].date()),
                    "tradeable_days": int(m.univ[:, j].sum()), "listed": bool(idx[-1] >= len(m.dates) - 3)})
    return sorted(out, key=lambda x: (-x["tradeable_days"], x["symbol"]))


def _one(m: Market, F: np.ndarray, j: int, t0: int, t1: int, cfg: SimConfig, ret: np.ndarray, coin_fn=None) -> dict:
    res = coin_fn(m, j, t0, t1) if coin_fn else run_coin(
        m.o[:, j], m.h[:, j], m.l[:, j], m.c[:, j], m.univ[:, j], m.cost[:, j], m.fund[:, j], m.atr[:, j], F[:, j], t0, t1, cfg)
    hold = hold_curve(m.o[:, j], m.c[:, j], m.cost[:, j], t0, t1)
    live = np.flatnonzero(np.isfinite(m.c[t0:t1 + 1, j]))
    a, b = (t0 + int(live[0]), t0 + int(live[-1])) if len(live) else (t0, t0)
    test = null_test(res["expo"], ret[:, j], m.cost[:, j], a, b, cfg.n_null, cfg.seed + j)
    return {"res": res, "hold": hold, "a": a, "b": b, "test": test}


def run_symbol(symbol: str, cfg: SimConfig) -> dict:
    m = market()
    if symbol not in m.symbols:
        raise KeyError(symbol)
    j = m.symbols.get_loc(symbol)
    w = weights(cfg.weights)
    F = fuse({k: v[:, [j]] for k, v in m.comps.items()}, w)
    Ffull = np.full_like(m.c, np.nan)
    Ffull[:, j] = F[:, 0]
    ret = np.full_like(m.c, np.nan)
    ret[1:, j] = m.c[1:, j] / m.c[:-1, j] - 1
    t0, t1 = _range(m, cfg)
    o = _one(m, Ffull, j, t0, t1, cfg, ret)
    res, hold, a, b = o["res"], o["hold"], o["a"], o["b"]
    d = m.dates
    eq = res["eq"]
    daily = np.diff(eq[a:b + 1]) / np.where(eq[a:b] > 0, eq[a:b], np.nan)
    curve = [{"t": _ms(d[i]), "sim": round(float(eq[i]), 2), "hold": None if not np.isfinite(hold[i]) else round(float(hold[i]), 2),
              "f": None if not np.isfinite(F[i, 0]) else round(float(F[i, 0]), 3), "pos": float(res["expo"][i])}
             for i in (a + k for k in _thin(b - a + 1)) if np.isfinite(eq[i])]
    trades = [{"side": t["side"], "entry": str(d[t["entry_t"]].date()), "exit": str(d[t["exit_t"]].date()) if t["exit_t"] is not None else None,
               "entry_px": round(t["entry_px"], 8), "exit_px": round(t["exit_px"], 8), "lev": t["lev"], "why": t["why"],
               "ret_pct": round(t["ret"] * 100, 2), "cost": round(t["cost"], 2), "funding": round(t["funding"], 2),
               "days": (t["exit_t"] if t["exit_t"] is not None else b) - t["entry_t"] + 1} for t in res["trades"]]
    last = {k: (None if not np.isfinite(v[b, j]) else round(float(v[b, j]), 3)) for k, v in m.comps.items()}
    return py({
        "symbol": symbol, "config": cfg.__dict__, "weights": w, "from": str(d[a].date()), "to": str(d[b].date()),
        "sim": {**stats(eq[a:b + 1], d[a:b + 1]), **trade_stats(res["trades"]), "costs": round(res["costs"], 2),
                "funding": round(res["funding"], 2), "ruined": res["ruined"],
                "exposure_pct": round(float(np.mean(res["expo"][a:b + 1] != 0)) * 100, 1)},
        "hold": stats(hold[a:b + 1], d[a:b + 1]),
        "test": {**o["test"], "verdict": _verdict(o["test"]["p"], None)},
        "mc": monte_carlo(daily, cfg.mc_paths, cfg.mc_days, cfg.seed),
        "today": {"f": None if not np.isfinite(F[b, 0]) else round(float(F[b, 0]), 3), "components": last,
                  "position": res["expo"][b]},
        "curve": curve, "trades": trades[::-1][:300], "start_equity": START, "notes": NOTES,
    })


def _verdict(p: float | None, fdr: bool | None) -> str:
    if p is None:
        return "not enough history or no trades to test"
    if fdr:
        return "beats random timing after the multiple-testing correction"
    if p < 0.05:
        return "beats random timing on this coin alone (p < 0.05), but not after correcting for testing every coin" if fdr is False \
            else "beats random timing (p < 0.05) — one coin; with many coins tested some pass by luck"
    return "no better than the same trades at random times"


def run_all(cfg: SimConfig, progress=None, F: np.ndarray | None = None, coin_fn=None) -> dict:
    """Every coin. F: a dates x symbols signal to trade instead of the fused one (benchmarks); coin_fn(m, j, t0, t1):
    a whole per-coin strategy instead of run_coin (e.g. a DCA bot)."""
    m = market()
    w = weights(cfg.weights)
    if F is None:
        F = fuse(m.comps, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = np.vstack([np.full((1, m.c.shape[1]), np.nan), m.c[1:] / m.c[:-1] - 1])
    t0, t1 = _range(m, cfg)
    cols = [j for j in range(len(m.symbols)) if m.univ[t0:t1 + 1, j].any()]
    rows, pnl, active = [], np.zeros(t1 - t0 + 1), np.zeros(t1 - t0 + 1)
    all_trades: list[float] = []
    hits = on_days = 0
    for n, j in enumerate(cols):
        o = _one(m, F, j, t0, t1, cfg, ret, coin_fn)
        res, hold, a, b = o["res"], o["hold"], o["a"], o["b"]
        eq = res["eq"]
        s, hs, ts = stats(eq[a:b + 1], m.dates[a:b + 1]), stats(hold[a:b + 1], m.dates[a:b + 1]), trade_stats(res["trades"])
        if not s:
            continue
        # pooled book: equal capital across the coins the signal holds that day
        x = res["expo"]
        with np.errstate(invalid="ignore", divide="ignore"):
            dr = np.diff(eq) / eq[:-1]
        on = (x[1:] != 0) | (x[:-1] != 0)
        seg = slice(t0, t1)
        k = np.isfinite(dr[seg]) & on[seg]
        pnl[1:][k] += dr[seg][k]
        active[1:][k] += 1
        all_trades += [t["ret"] for t in res["trades"] if t.get("why") != "open"]
        held = x[t0:t1] != 0  # direction accuracy: held through the next day, and the day went its way
        nxt = ret[t0 + 1:t1 + 1, j]
        ok_d = held & np.isfinite(nxt) & (nxt != 0)
        hits += int(((np.sign(x[t0:t1]) * nxt) > 0)[ok_d].sum())
        on_days += int(ok_d.sum())
        rows.append({"symbol": m.symbols[j], "from": str(m.dates[a].date()), "to": str(m.dates[b].date()),
                     "return_pct": s["return_pct"], "hold_pct": hs.get("return_pct"), "sharpe": s["sharpe"],
                     "hold_sharpe": hs.get("sharpe"), "max_dd_pct": s["max_dd_pct"], "trades": ts["trades"],
                     "win_rate": ts.get("win_rate"), "avg_trade_pct": ts.get("avg_trade_pct"),
                     "ruined": res["ruined"], "p": o["test"]["p"],
                     "position": float(x[b]), "f": None if not np.isfinite(F[b, j]) else round(float(F[b, j]), 3)})
        if progress and n % 20 == 0:
            progress(n / len(cols))
    for r, ok in zip(rows, bh([r["p"] for r in rows]), strict=True):
        r["fdr"] = ok
        r["excess_pct"] = None if r["hold_pct"] is None else round(r["return_pct"] - r["hold_pct"], 2)
    pooled = np.where(active > 0, pnl / np.maximum(active, 1), 0.0)
    eq = START * np.cumprod(1 + pooled)
    d = m.dates[t0:t1 + 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        ew = np.nanmean(np.where(m.univ[t0:t1 + 1], np.nan_to_num(ret[t0:t1 + 1]), np.nan), axis=1)
    ew_eq = START * np.cumprod(1 + np.nan_to_num(ew))
    tested = [r for r in rows if r["p"] is not None]
    exc = [r["excess_pct"] for r in rows if r["excess_pct"] is not None]
    shp = [r["sharpe"] for r in rows if r["sharpe"] is not None]
    summary = {
        "coins": len(rows), "tested": len(tested), "p05": sum(r["p"] < 0.05 for r in tested),
        "expected_by_luck": round(0.05 * len(tested), 1), "fdr_pass": sum(r["fdr"] for r in rows),
        "beat_hold": sum(1 for x in exc if x > 0), "median_excess_pct": float(np.median(exc)) if exc else None,
        "median_sharpe": float(np.median(shp)) if shp else None, "ruined": sum(r["ruined"] for r in rows),
        "holding_now": sum(1 for r in rows if r["position"] != 0),
        "trades": len(all_trades), "win_rate": round(100 * float(np.mean([t > 0 for t in all_trades])), 1) if all_trades else None,
        "avg_trade_pct": round(100 * float(np.mean(all_trades)), 2) if all_trades else None,
        "day_hit_rate": round(100 * hits / on_days, 1) if on_days else None,
        "exposure_pct": round(100 * float((active > 0).mean()), 1),
    }
    return py({
        "config": cfg.__dict__, "weights": w, "from": str(d[0].date()), "to": str(d[-1].date()), "summary": summary,
        "pooled": {**stats(eq, d), "avg_coins_held": round(float(active[active > 0].mean()), 1) if (active > 0).any() else 0},
        "ew_hold": stats(ew_eq, d),
        "curve": [{"t": _ms(d[i]), "sim": round(float(eq[i]), 2), "hold": round(float(ew_eq[i]), 2), "n": int(active[i])}
                  for i in _thin(len(d))],
        "mc": monte_carlo(pooled[active > 0] if (active > 0).sum() > 60 else pooled, cfg.mc_paths, cfg.mc_days, cfg.seed),
        "signs": m.signs, "rows": sorted(rows, key=lambda r: (r["p"] is None, r["p"] or 1)), "notes": NOTES,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "key": cfg.key(),
    })


NOTES = [
    "Signal rebuilt each day from data known that day; the factor composite's signs are re-chosen every 1 January "
    "from earlier data only.",
    "Weights 'live' are today's evidence weights. The factor composite earned its 0.3 on this same history, so a "
    "'live' replay is partly in-sample; 'prior' uses literature priors only.",
    "Fundamentals, chart setups and the AI desk have no history for every coin and are not in the replay.",
    "Decided at the close, filled at the next open; fee + slippage by liquidity on every fill; funding charged on perp "
    "positions; isolated-margin liquidation ends the run.",
    "Daily candles: a stop and a liquidation inside the same day are ordered by distance from entry.",
    "Test: the same positions at random times (circular shift), 200 times; across many coins the false-discovery "
    "rate is controlled at 10% (Benjamini-Hochberg).",
    "Monte Carlo: block bootstrap of the simulated daily returns — it repeats the past's character, it does not "
    "forecast a new regime.",
]


# ---------------------------------------------------------------- jobs and cache

def _path(key: str) -> Path:
    return DIR / f"all_{key}.json"


def cached_all(cfg: SimConfig) -> dict | None:
    try:
        r = json.loads(_path(cfg.key()).read_text())
        return r if r.get("panel") == _panel_key() else None
    except (OSError, json.JSONDecodeError):
        return None


def latest_all() -> dict | None:
    files = sorted(DIR.glob("all_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if DIR.exists() else []
    for p in files:
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


# pre-registered forward tests (docs/research/WINRATE_PREREG_2026-11-13.md, P2): the same simulator on new data only
FORWARD = {"entry_010": SimConfig(start="2026-11-13", entry=0.10, n_null=50, mc_paths=100),
           "entry_020": SimConfig(start="2026-11-13", entry=0.20, n_null=50, mc_paths=100)}


def run_forward() -> dict:
    """Advance the forward tests (only once the panel has data after their start date). Called by the daily job."""
    m = market()
    out = {}
    for name, cfg in FORWARD.items():
        if m.dates[-1] <= pd.Timestamp(cfg.start):
            out[name] = {"status": "waiting", "start": cfg.start, "data_through": str(m.dates[-1].date())}
            continue
        r = run_all(cfg)
        out[name] = {"status": "running", "start": cfg.start, "data_through": str(m.dates[-1].date()),
                     "summary": r["summary"], "pooled": {k: v for k, v in r["pooled"].items() if k != "by_year"},
                     "entry": cfg.entry}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "forward.json").write_text(json.dumps(py(out), default=str))
    return out


def forward() -> dict | None:
    try:
        return json.loads((DIR / "forward.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def run_all_cached(cfg: SimConfig, progress=None) -> dict:
    hit = cached_all(cfg)
    if hit:
        return hit
    res = run_all(cfg, progress)
    res["panel"] = _panel_key()
    DIR.mkdir(parents=True, exist_ok=True)
    _path(cfg.key()).write_text(json.dumps(res, default=str))
    return res


def start_job(cfg: SimConfig) -> str:
    for jid, j in _jobs.items():
        if j["key"] == cfg.key() and j["status"] == "running":
            return jid
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "key": cfg.key(), "status": "running", "progress": 0.0, "started": time.time(), "result": None, "error": None}
    _jobs[jid] = job

    def work():
        try:
            job["result"] = run_all_cached(cfg, lambda p: job.update(progress=round(p, 3)))
            job.update(status="done", progress=1.0)
        except Exception as e:  # noqa: BLE001
            log.warning("sim_job_failed", error=str(e))
            job.update(status="error", error=str(e))

    threading.Thread(target=work, daemon=True).start()
    for old in [k for k, v in _jobs.items() if v["status"] != "running" and time.time() - v["started"] > 3600]:
        _jobs.pop(old, None)
    return jid


def job(jid: str) -> dict | None:
    j = _jobs.get(jid)
    return None if j is None else {k: v for k, v in j.items() if k != "started"}
