"""Order-flow and book features from recorded snapshots and trades (TECH_PLAN 2.4: entry timing). Pure functions.

Inputs are the frames `load()` returns: book rows (ts, bp1..bp20, bq1.., ap1.., aq1..) and trades as either per-second
aggregates (n, buy_qty, sell_qty, vwap) or raw aggregate trades (price, qty, is_buyer_maker). With per-second rows the
buy/sell quote volume is qty * the second's VWAP (both sides share one VWAP; the error is within the second's range).

Order-flow imbalance (Cont, Kukanov & Stoikov 2014, "The price impact of order book events"), between consecutive
snapshots n-1 -> n at level m:
    e_n = q^b_n 1{P^b_n >= P^b_{n-1}} - q^b_{n-1} 1{P^b_n <= P^b_{n-1}}
        - q^a_n 1{P^a_n <= P^a_{n-1}} + q^a_{n-1} 1{P^a_n >= P^a_{n-1}}
Positive = net buying pressure at the touch. CKS regress mid-price changes on OFI / average depth; `ofi_norm` is that
ratio. The multi-level sum over levels 1..k (`ofi_ml`) follows Xu, Kolm & Hassan's multi-level OFI with equal weights.
Caveat: the recorder samples every 10 s, so this is OFI between 10-s snapshots, a coarse version of the event-level
quantity (changes that cancel within 10 s are invisible).
Microprice here is the size-weighted mid (ask * q^b + bid * q^a) / (q^b + q^a), Stoikov's first-order approximation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    if "time" not in df:
        df = df.assign(time=pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True))
    return df


def _f(df: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def book_features(book: pd.DataFrame, levels: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    """Per snapshot: mid, spread, spread_bps, microprice, micro_dev_bps (microprice vs mid) and depth imbalance
    (bid depth - ask depth) / (bid depth + ask depth) over the top L levels, in [-1, 1]. Indexed by UTC time."""
    b = _sorted(book)
    bid, ask, bq, aq = _f(b, "bp1"), _f(b, "ap1"), _f(b, "bq1"), _f(b, "aq1")
    mid = (bid + ask) / 2
    with np.errstate(invalid="ignore", divide="ignore"):
        micro = (ask * bq + bid * aq) / (bq + aq)
        out = {"ts": b["ts"].to_numpy(), "mid": mid, "spread": ask - bid, "spread_bps": (ask - bid) / mid * 1e4,
               "microprice": micro, "micro_dev_bps": (micro - mid) / mid * 1e4}
        for L in levels:
            bd = np.nansum(np.column_stack([_f(b, f"bq{i}") for i in range(1, L + 1)]), axis=1)
            ad = np.nansum(np.column_stack([_f(b, f"aq{i}") for i in range(1, L + 1)]), axis=1)
            out[f"depth_imb_{L}"] = (bd - ad) / (bd + ad)
    return pd.DataFrame(out, index=pd.DatetimeIndex(b["time"], name="time"))


def ofi(book: pd.DataFrame, depth: int = 1, max_dt_ms: int = 30_000) -> pd.DataFrame:
    """Per snapshot n: OFI from snapshot n-1 to n at levels 1..depth (columns ofi_1..ofi_depth) and the level-1
    average depth (q^b + q^a) / 2. NaN for the first row and across gaps longer than `max_dt_ms`."""
    b = _sorted(book)
    dt = b["ts"].diff().to_numpy(dtype=float)
    bad = ~(dt <= max_dt_ms)  # first row (NaN) and gaps
    out = {"ts": b["ts"].to_numpy()}
    for m in range(1, depth + 1):
        pb, qb, pa, qa = _f(b, f"bp{m}"), _f(b, f"bq{m}"), _f(b, f"ap{m}"), _f(b, f"aq{m}")
        pb0, qb0, pa0, qa0 = (np.r_[np.nan, x[:-1]] for x in (pb, qb, pa, qa))
        e = (qb * (pb >= pb0) - qb0 * (pb <= pb0) - qa * (pa <= pa0) + qa0 * (pa >= pa0)).astype(float)
        e[bad] = np.nan
        out[f"ofi_{m}"] = e
    out["depth_1"] = (_f(b, "bq1") + _f(b, "aq1")) / 2
    return pd.DataFrame(out, index=pd.DatetimeIndex(b["time"], name="time"))


def trade_flow(trades: pd.DataFrame, freq: str = "1min") -> pd.DataFrame:
    """Per period: buy/sell base volume (by taker side), signed volume (buy - sell), signed quote volume, trade
    count, VWAP and buy share. Accepts per-second aggregates or raw aggregate trades."""
    t = _sorted(trades)
    if "buy_qty" in t:
        bq, sq, vw = _f(t, "buy_qty"), _f(t, "sell_qty"), _f(t, "vwap")
        x = pd.DataFrame({"buy_qty": bq, "sell_qty": sq, "buy_quote": np.nan_to_num(bq * vw),  # one VWAP per second:
                          "sell_quote": np.nan_to_num(sq * vw), "n_trades": _f(t, "n")},     # side quote ~ qty * vwap
                         index=pd.DatetimeIndex(t["time"]))
    else:
        q, p = _f(t, "qty"), _f(t, "price")
        sell = t["is_buyer_maker"].astype(str).str.lower().isin(["1", "true"]).to_numpy()
        x = pd.DataFrame({"buy_qty": np.where(sell, 0.0, q), "sell_qty": np.where(sell, q, 0.0),
                          "buy_quote": np.where(sell, 0.0, p * q), "sell_quote": np.where(sell, p * q, 0.0),
                          "n_trades": 1.0}, index=pd.DatetimeIndex(t["time"]))
    g = x.resample(freq, label="left", closed="left").sum()
    vol = g["buy_qty"] + g["sell_qty"]
    g["signed_qty"] = g["buy_qty"] - g["sell_qty"]
    g["signed_quote"] = g["buy_quote"] - g["sell_quote"]
    with np.errstate(invalid="ignore", divide="ignore"):
        g["vwap"] = ((g["buy_quote"] + g["sell_quote"]) / vol).where(vol > 0)
        g["buy_share"] = (g["buy_qty"] / vol).where(vol > 0)
    g.index.name = "time"
    return g


def minute_features(book: pd.DataFrame, trades: pd.DataFrame | None = None, levels: tuple[int, ...] = (5, 10, 20),
                    ofi_depth: int = 5, freq: str = "1min", max_dt_ms: int = 30_000) -> pd.DataFrame:
    """Per-period feature table: OFI (level 1, normalised by mean level-1 depth, and multi-level 1..ofi_depth), depth
    imbalance at each L, microprice, mid, spread (end-of-period and mean), snapshot count, plus trade flow when
    `trades` is given. Book state columns are the last snapshot in the period."""
    bf = book_features(book, levels)
    of = ofi(book, ofi_depth, max_dt_ms)
    r = lambda s: s.resample(freq, label="left", closed="left")  # noqa: E731
    lvl = [f"ofi_{m}" for m in range(1, ofi_depth + 1)]
    o = r(of[lvl]).sum(min_count=1)
    out = pd.DataFrame(index=o.index)
    out["ofi"] = o["ofi_1"]
    dep = r(of["depth_1"]).mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        out["ofi_norm"] = out["ofi"] / dep
    out[f"ofi_ml{ofi_depth}"] = o[lvl].sum(axis=1, min_count=1)
    last = r(bf).last()
    for c in ["mid", "microprice", "micro_dev_bps", "spread", "spread_bps"] + [f"depth_imb_{L}" for L in levels]:
        out[c] = last[c]
    out["spread_bps_mean"] = r(bf["spread_bps"]).mean()
    out["n_snapshots"] = r(bf["mid"]).count()
    if trades is not None and len(trades):
        tf = trade_flow(trades, freq)
        out = out.join(tf[["signed_qty", "signed_quote", "buy_qty", "sell_qty", "n_trades", "vwap", "buy_share"]],
                       how="outer")
    out.index.name = "time"
    return out
