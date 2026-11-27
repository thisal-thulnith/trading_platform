"""Delta-neutral funding carry with the measured basis: long spot + short perp, 1x, half the capital on each leg.

Per held coin (fixed quantities from entry to exit, as a real two-leg position):
  value = spot quantity × spot price + perp margin − perp quantity × (perp price − perp entry) + funding received − costs
so the spot–perp basis moves the result exactly as it would live. Decided at the close, both legs filled at the next
open, fee + slippage by liquidity on both legs at entry and exit, funding settled daily on the perp notional. The 1x short
perp on isolated margin is liquidated if the perp price nearly doubles (the spot leg is then sold at the close).

Rules (pre-registered, docs/research/WINRATE_PREREG_2026-11-13.md, P3):
  majors: BTC and ETH, enter when the 7-day mean funding > 0.01%/day, exit when it falls to <= 0
  alts:   up to 10 liquid coins (cost bucket <= 0.16%), enter > 0.05%/day (highest first), exit <= 0
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from app.sim import service
from app.sim.engine import START, stats

MM = 0.005


@dataclass
class CarryConfig:
    name: str = "majors"
    symbols: tuple[str, ...] | None = ("BTCUSDT", "ETHUSDT")
    k: int = 2
    enter: float = 0.0001
    exit: float = 0.0
    max_cost: float = 1.0  # liquidity filter on the one-way cost rate
    start: str = "2021-01-01"
    end: str | None = None
    margin: str = "isolated"  # isolated: the short perp can be liquidated alone; unified: the spot leg is its collateral


PRESETS = {
    "majors": CarryConfig(),
    "alts": CarryConfig(name="alts", symbols=None, k=10, enter=0.0005, max_cost=0.0016),
    # exploratory (post-hoc, 2026-11-13): unified margin, the spot leg collateralises the short perp (Binance Portfolio
    # Margin / unified accounts) — a 1x hedge cannot be liquidated by a rally
    "majors_unified": CarryConfig(name="majors_unified", margin="unified"),
    "alts_unified": CarryConfig(name="alts_unified", symbols=None, k=10, enter=0.0005, max_cost=0.0016, margin="unified"),
}


def perp_wide(m) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Perp open, high, close aligned to the simulator's dates x symbols (spot units)."""
    from app.quant.lab import perps

    p = perps.load()
    p = p[p["symbol"].isin(set(m.symbols))].assign(date=lambda d: pd.to_datetime(d["date"]))

    def piv(col):
        return p.pivot(index="date", columns="symbol", values=col).reindex(index=m.dates, columns=m.symbols).to_numpy(float)

    return piv("open"), piv("high"), piv("close")


def run(cfg: CarryConfig, m=None, perp=None) -> dict:
    m = m or service.market()
    po, ph, pc = perp if perp is not None else perp_wide(m)
    t0, t1 = service._range(m, type("C", (), {"start": cfg.start, "end": cfg.end})())
    f7 = pd.DataFrame(m.fund).rolling(7, min_periods=5).mean().to_numpy()
    cost = np.nan_to_num(m.cost, nan=0.003)
    allowed = np.ones(len(m.symbols), bool) if cfg.symbols is None else np.isin(np.array(m.symbols), list(cfg.symbols))
    cash = START
    pos: dict[int, dict] = {}
    pending: tuple[set, set] | None = None  # (to open, to close) decided at yesterday's close
    eq = np.full(len(m.dates), np.nan)
    trades: list[dict] = []
    tot = {"funding": 0.0, "basis": 0.0, "costs": 0.0, "liquidations": 0}

    def value(j, p, t):
        s = m.c[t, j] if math.isfinite(m.c[t, j]) else p["last_s"]
        q = pc[t, j] if math.isfinite(pc[t, j]) else p["last_p"]
        p["last_s"], p["last_p"] = s, q
        return p["qs"] * s + p["N"] - p["qp"] * (q - p["pe"]) + p["fund"] - p["cost"]

    def close(j, t, s_px, p_px, why):
        nonlocal cash
        p = pos.pop(j)
        fee = cost[t, j] * (p["qs"] * s_px + p["qp"] * p_px)
        v = p["qs"] * s_px + p["N"] - p["qp"] * (p_px - p["pe"]) + p["fund"] - p["cost"] - fee
        if why == "liquidation":
            v = p["qs"] * s_px - p["cost"] - fee + p["fund"]  # the perp margin is lost
        cash += v
        basis = p["qs"] * (s_px - p["se"]) - p["qp"] * (p_px - p["pe"])
        tot["basis"] += basis
        tot["costs"] += fee
        trades.append({"symbol": m.symbols[j], "entry": str(m.dates[p["t"]].date()), "exit": str(m.dates[t].date()),
                       "days": t - p["t"], "why": why, "funding": round(p["fund"], 2), "basis": round(basis, 2),
                       "costs": round(p["cost"] + fee, 2), "net_pct": round((v / (2 * p["N"]) - 1) * 100, 3)})

    for t in range(t0, t1 + 1):
        # 1. fills at the open
        if pending is not None:
            to_open, to_close = pending
            for j in to_close:
                if j in pos:
                    s_px = m.o[t, j] if math.isfinite(m.o[t, j]) else pos[j]["last_s"]
                    p_px = po[t, j] if math.isfinite(po[t, j]) else pos[j]["last_p"]
                    close(j, t, s_px, p_px, "funding turned")
            total = cash + sum(value(j, p, t - 1) for j, p in pos.items())
            for j in to_open:
                if not (math.isfinite(m.o[t, j]) and math.isfinite(po[t, j]) and m.o[t, j] > 0 and po[t, j] > 0):
                    continue
                slot = min(total / cfg.k, cash)
                if slot <= 1:
                    continue
                N = slot / 2
                qs, qp = N / m.o[t, j], N / po[t, j]
                fee = cost[t, j] * 2 * N
                cash -= slot
                tot["costs"] += fee
                pos[j] = {"t": t, "N": N, "qs": qs, "qp": qp, "se": m.o[t, j], "pe": po[t, j], "fund": 0.0, "cost": fee,
                          "last_s": m.o[t, j], "last_p": po[t, j]}
            pending = None
        # 2. liquidation of the short perp leg, funding, delisting
        for j in list(pos):
            p = pos[j]
            liq = (p["N"] + p["qp"] * p["pe"]) / (p["qp"] * (1 + MM))
            if cfg.margin == "isolated" and math.isfinite(ph[t, j]) and ph[t, j] >= liq:
                tot["liquidations"] += 1
                close(j, t, m.c[t, j] if math.isfinite(m.c[t, j]) else p["last_s"], liq, "liquidation")
                continue
            if math.isfinite(m.fund[t, j]) and math.isfinite(pc[t, j]):
                f = p["qp"] * pc[t, j] * m.fund[t, j]
                p["fund"] += f
                tot["funding"] += f
            if not math.isfinite(pc[t, j]) and not math.isfinite(m.c[t, j]) and t > p["t"] + 3:
                close(j, t, p["last_s"], p["last_p"], "delisted")
        eq[t] = cash + sum(value(j, p, t) for j, p in pos.items())
        # 3. decide at the close
        if t < t1:
            f = f7[t]
            with np.errstate(invalid="ignore", divide="ignore"):
                gap = np.abs(pc[t] / m.c[t] - 1)
            # data validity: a perp more than 2% from spot is a different asset under the same ticker or a broken market
            ok = allowed & m.univ[t] & (cost[t] <= cfg.max_cost) & np.isfinite(pc[t]) & np.isfinite(f) & (np.nan_to_num(gap, nan=1) < 0.02)
            to_close = {j for j in pos if not (math.isfinite(f[j]) and f[j] > cfg.exit)}
            free = cfg.k - (len(pos) - len(to_close))
            cand = [j for j in np.flatnonzero(ok & (np.nan_to_num(f, nan=-1) > cfg.enter)) if j not in pos]
            cand = sorted(cand, key=lambda j: -f[j])[:max(0, free)]
            if to_close or cand:
                pending = (set(cand), to_close)
    d = m.dates[t0:t1 + 1]
    e = eq[t0:t1 + 1]
    return {"config": asdict(cfg), "dates": d, "eq": e, "trades": trades, "totals": tot, "open": [m.symbols[j] for j in pos]}


def report(res: dict, oos_from: str = "2024-01-01") -> dict:
    d, e = res["dates"], res["eq"]
    s = pd.Series(e, index=d)
    r = s.pct_change().dropna()

    def block(x: pd.Series) -> dict:
        if len(x) < 30:
            return {}
        rr = x.pct_change().dropna()
        wk = (1 + rr).resample("W").prod() - 1
        mo = (1 + rr).resample("ME").prod() - 1
        on_d, on_w, on_m = rr[rr != 0], wk[wk.abs() > 1e-9], mo[mo.abs() > 1e-9]
        st = stats(x.to_numpy(), x.index)
        return {**{k: st.get(k) for k in ("return_pct", "cagr_pct", "sharpe", "max_dd_pct", "by_year")},
                "days_won_pct": round(100 * float((on_d > 0).mean()), 1) if len(on_d) else None,
                "weeks_won_pct": round(100 * float((on_w > 0).mean()), 1) if len(on_w) else None,
                "months_won_pct": round(100 * float((on_m > 0).mean()), 1) if len(on_m) else None,
                "worst_week_pct": round(100 * float(wk.min()), 2), "worst_month_pct": round(100 * float(mo.min()), 2),
                "invested_days_pct": round(100 * float((rr != 0).mean()), 1)}

    full, oos = block(s), block(s[s.index >= oos_from])
    passed = bool(oos) and (oos["weeks_won_pct"] or 0) >= 85 and oos["max_dd_pct"] < 3 and (oos["cagr_pct"] or 0) > 4
    tr = res["trades"]
    return service.py({
        "name": res["config"]["name"], "config": res["config"], "full": full, "oos_from": oos_from, "oos": oos,
        "pass": passed, "pass_rule": "out of sample: >= 85% of weeks won, max drawdown < 3%, > 4% a year",
        "totals": {k: round(v, 2) if isinstance(v, float) else v for k, v in res["totals"].items()},
        "trades": len(tr), "trade_win_pct": round(100 * float(np.mean([t["net_pct"] > 0 for t in tr])), 1) if tr else None,
        "open_now": res["open"], "recent_trades": tr[-30:][::-1],
        "curve": [{"t": service._ms(i), "v": round(float(v), 2)} for i, v in s.iloc[:: max(1, len(s) // 400)].items()],
        "daily_returns_std_pct": round(float(r.std() * 100), 4) if len(r) else None,
    })


def run_all_presets() -> dict:
    m = service.market()
    perp = perp_wide(m)
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "books": {}}
    for name, cfg in PRESETS.items():
        out["books"][name] = report(run(cfg, m, perp))
    service.DIR.mkdir(parents=True, exist_ok=True)
    (service.DIR / "carry.json").write_text(json.dumps(out, default=str))
    return out


FORWARD_START = "2026-11-13"  # pre-registration date: forward books trade only data after it


def run_forward() -> dict:
    """The carry books paper-traded on new data only (daily job). The unified-margin books are post-hoc: they count only
    if they pass forward (>= 85% of weeks won, max drawdown < 3%, > 4% a year, judged after 180 days)."""
    from dataclasses import replace

    m = service.market()
    perp = perp_wide(m)
    out = {"start": FORWARD_START, "data_through": str(m.dates[-1].date()), "books": {}}
    for name, cfg in PRESETS.items():
        if m.dates[-1] <= pd.Timestamp(FORWARD_START):
            out["books"][name] = {"status": "waiting for data after the start date"}
            continue
        res = run(replace(cfg, start=FORWARD_START), m, perp)
        e = res["eq"]
        out["books"][name] = {"status": "running", "days": int(len(e) - 1), "equity": round(float(e[-1]), 2),
                              "return_pct": round((float(e[-1]) / START - 1) * 100, 3), "open": res["open"],
                              "funding": round(res["totals"]["funding"], 2), "costs": round(res["totals"]["costs"], 2)}
    service.DIR.mkdir(parents=True, exist_ok=True)
    (service.DIR / "carry_forward.json").write_text(json.dumps(service.py(out)))
    return out


def forward() -> dict | None:
    try:
        return json.loads((service.DIR / "carry_forward.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def latest() -> dict | None:
    try:
        return json.loads((service.DIR / "carry.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    r = run_all_presets()
    for name, b in r["books"].items():
        print(name, "PASS" if b["pass"] else "fail", "| full", {k: v for k, v in b["full"].items() if k != "by_year"})
        print("   oos ", {k: v for k, v in b["oos"].items() if k != "by_year"})
        print("   years", b["full"]["by_year"], "| totals", b["totals"], "| trades", b["trades"], "won", b["trade_win_pct"], "| open", b["open_now"])
