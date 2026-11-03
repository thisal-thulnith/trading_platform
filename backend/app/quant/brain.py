"""Quant brain: the numbers side of the intelligence. Agents reason; this module measures.

1. Cross-sectional trend score (CTREND-style, Fieberg et al. JFQA 2025): every liquid coin is scored on several
   price/volume trend signals, each converted to a cross-sectional rank, then averaged. Technical information is
   only used systematically across many coins, never as a single-chart opinion.
2. Market regime: BTC trend (50/200-day averages), BTC volatility percentile and breadth (share of coins above
   their 50-day average).
3. Derivatives: funding rate and perp-vs-index basis for every perp in one request (premiumIndex).
4. Honest proof on our own data: a weekly long-top-quintile / short-bottom-quintile walk-forward over the last
   ~13 months with 0.1% round-trip costs per leg turnover, plus the next-week hit rate per quintile, which is what
   maps a coin's score to a probability. Caveat: the coin list is today's (survivorship bias).
"""
import asyncio
import math
import time
import warnings

import numpy as np
import pandas as pd

from app.logging import log

TOP_N = 120          # most liquid coins scored
DAYS = 400           # daily bars per coin
WEEK = 7
COST = 0.001         # per unit of turnover (taker fee 0.05% x2 sides)
CACHE_S = 1800

_state: dict = {"t": 0.0, "data": None, "running": False}
_lock = asyncio.Lock()


def _signals(close: pd.DataFrame, qvol: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Raw trend signals, one DataFrame (dates x coins) each. Higher = more bullish trend."""
    sig = {}
    for n in (7, 14, 28):
        sig[f"mom_{n}d"] = close / close.shift(n) - 1
    for n in (20, 50):
        sig[f"px_vs_ma{n}"] = close / close.rolling(n, min_periods=n).mean() - 1
    sig["ma20_slope"] = close.rolling(20).mean().pct_change(5)
    sig["vol_trend"] = qvol.rolling(7).mean() / qvol.rolling(28).mean() - 1
    # risk-adjusted momentum: 28d return over 28d volatility
    ret = close.pct_change()
    sig["sharpe_28d"] = (close / close.shift(28) - 1) / (ret.rolling(28).std() * math.sqrt(28))
    return sig


def trend_score(close: pd.DataFrame, qvol: pd.DataFrame) -> pd.DataFrame:
    """Average of cross-sectional percentile ranks (0..1) across signals; NaN where too little history."""
    ranks = [s.rank(axis=1, pct=True) for s in _signals(close, qvol).values()]
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        stacked = np.nanmean(np.stack([r.to_numpy() for r in ranks]), axis=0)
    return pd.DataFrame(stacked, index=close.index, columns=close.columns)


def walk_forward(close: pd.DataFrame, score: pd.DataFrame, start: int = 60) -> dict:
    """Weekly rebalance: long top quintile, short bottom quintile, equal weight, next-week returns, net of costs."""
    rows, prev_long, prev_short = [], set(), set()
    q_hits: dict[int, list[int]] = {q: [] for q in range(5)}
    q_rets: dict[int, list[float]] = {q: [] for q in range(5)}
    idx = close.index
    for i in range(start, len(idx) - WEEK, WEEK):
        s = score.iloc[i].dropna()
        fwd = (close.iloc[i + WEEK] / close.iloc[i] - 1).reindex(s.index).dropna()
        s = s.reindex(fwd.index)
        if len(s) < 25:
            continue
        q = pd.qcut(s.rank(method="first"), 5, labels=False)
        for k in range(5):
            r = fwd[q == k]
            q_hits[k] += [int(x > 0) for x in r]
            q_rets[k].append(float(r.mean()))
        longs, shorts = set(s[q == 4].index), set(s[q == 0].index)
        turn = (len(longs ^ prev_long) / max(len(longs), 1) + len(shorts ^ prev_short) / max(len(shorts), 1)) / 2
        gross = float(fwd[list(longs)].mean() - fwd[list(shorts)].mean()) / 2  # 50/50 gross exposure
        long_only = float(fwd[list(longs)].mean())
        market = float(fwd.mean())
        rows.append({"t": int(idx[i].timestamp() * 1000), "ls": gross - COST * turn, "long": long_only - COST * turn / 2,
                     "market": market})
        prev_long, prev_short = longs, shorts
    if not rows:
        return {"weeks": 0}
    df = pd.DataFrame(rows)

    def stats(col: str) -> dict:
        r = df[col]
        sd = float(r.std()) or 1e-9
        eq = (1 + r).cumprod()
        return {"mean_week_pct": round(float(r.mean()) * 100, 2), "hit_rate": round(float((r > 0).mean()), 3),
                "sharpe_ann": round(float(r.mean()) / sd * math.sqrt(52), 2),
                "total_pct": round(float(eq.iloc[-1] - 1) * 100, 1),
                "max_dd_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 1),
                "t_stat": round(float(r.mean()) / (sd / math.sqrt(len(r))), 2)}

    eq = {c: (1 + df[c]).cumprod().round(4).tolist() for c in ("ls", "long", "market")}
    return {"weeks": len(df), "start": rows[0]["t"], "end": rows[-1]["t"], "long_short": stats("ls"),
            "long_top": stats("long"), "market": stats("market"),
            "equity": {"t": df["t"].tolist(), **eq},
            "quintiles": [{"q": k + 1, "up_rate": round(float(np.mean(q_hits[k])), 3) if q_hits[k] else None,
                           "n": len(q_hits[k]),
                           "mean_week_pct": round(float(np.nanmean(q_rets[k])) * 100, 2) if q_rets[k] else None}
                          for k in range(5)]}


def regime(close: pd.DataFrame) -> dict:
    btc = close.get("BTCUSDT")
    out: dict = {}
    if btc is not None and btc.notna().sum() > 200:
        b = btc.dropna()
        ma50, ma200 = b.rolling(50).mean().iloc[-1], b.rolling(200).mean().iloc[-1]
        vol = b.pct_change().rolling(20).std() * math.sqrt(365)
        vp = float((vol.dropna() <= vol.iloc[-1]).mean())
        px = float(b.iloc[-1])
        trend = "bull" if px > ma50 > ma200 else "bear" if px < ma50 < ma200 else "mixed"
        out = {"btc_price": px, "btc_ma50": float(ma50), "btc_ma200": float(ma200), "btc_trend": trend,
               "btc_vol_ann_pct": round(float(vol.iloc[-1]) * 100, 1), "btc_vol_pctile": round(vp, 2),
               "vol_state": "high" if vp > 0.8 else "low" if vp < 0.2 else "normal",
               "btc_30d_pct": round(float(b.iloc[-1] / b.iloc[-31] - 1) * 100, 1)}
    above = (close.iloc[-1] > close.rolling(50).mean().iloc[-1]).sum()
    valid = close.rolling(50).mean().iloc[-1].notna().sum()
    out["breadth_pct"] = round(float(above / valid) * 100, 1) if valid else None
    b = out.get("breadth_pct") or 50
    t = out.get("btc_trend", "mixed")
    out["label"] = ("Risk-on: broad uptrend" if t == "bull" and b > 55 else
                    "Risk-off: broad downtrend" if t == "bear" and b < 45 else
                    "Mixed: no clear market trend")
    out["plain"] = (f"BTC is in a {t} trend with {out.get('vol_state', 'normal')} volatility; "
                    f"{b:.0f}% of coins are above their 50-day average.")
    return out


def score_to_prob(pct: float, quintiles: list[dict]) -> tuple[float, int]:
    """Map a coin's trend percentile to the measured next-week up-rate of its quintile (with sample size)."""
    k = min(int(pct * 5), 4)
    q = quintiles[k] if k < len(quintiles) else {}
    return (q.get("up_rate") or 0.5, q.get("n") or 0)


async def _fetch(client, symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    sem = asyncio.Semaphore(8)

    async def one(sym: str):
        async with sem:
            try:
                k = await client.klines(sym, "1d", "spot", limit=DAYS)
                return sym, k
            except Exception as e:  # noqa: BLE001 - one bad coin must not stop the scan
                log.warning("quant_fetch_failed", symbol=sym, error=str(e))
                return sym, []

    res = await asyncio.gather(*(one(s) for s in symbols))
    closes, vols = {}, {}
    for sym, k in res:
        if len(k) < 60:
            continue
        idx = pd.to_datetime([x["open_time"] for x in k], unit="ms")
        closes[sym] = pd.Series([x["close"] for x in k], index=idx)
        vols[sym] = pd.Series([x["quote_volume"] for x in k], index=idx)
    close = pd.DataFrame(closes).sort_index()
    qvol = pd.DataFrame(vols).sort_index()
    today = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    close, qvol = close[close.index < today], qvol[qvol.index < today]  # closed daily candles only
    return close, qvol


async def _derivatives(client) -> dict[str, dict]:
    try:
        rows = await client.premium_index()
    except Exception as e:  # noqa: BLE001
        log.warning("premium_index_failed", error=str(e))
        return {}
    out = {}
    for r in rows:
        try:
            mark, index = float(r["markPrice"]), float(r["indexPrice"])
            fr = float(r["lastFundingRate"])
        except (KeyError, ValueError, TypeError):
            continue
        sym = r["symbol"]
        for pre in ("1000000", "1000"):  # perps quoted per 1000 units, e.g. 1000PEPEUSDT -> PEPEUSDT
            if sym.startswith(pre) and sym[len(pre):] not in out:
                sym = sym[len(pre):]
                break
        out[sym] = {"funding_8h_pct": round(fr * 100, 4), "funding_ann_pct": round(fr * 3 * 365 * 100, 1),
                            "basis_pct": round((mark / index - 1) * 100, 3) if index else None}
    return out


def _funding_note(d: dict | None) -> str | None:
    if not d:
        return None
    a = d["funding_ann_pct"]
    if a > 30:
        return f"Longs are paying a lot to hold ({a:.0f}%/yr): crowded long, crash risk"
    if a < -15:
        return f"Shorts are paying to hold ({a:.0f}%/yr): crowded short, squeeze risk"
    return None


async def compute() -> dict:
    from app.agents.scout import all_coins
    from app.engine import engine

    t0 = time.time()
    coins = (await all_coins())[:TOP_N]
    syms = [c["symbol"] for c in coins]
    if "BTCUSDT" not in syms:
        syms.append("BTCUSDT")
    (close, qvol), deriv = await asyncio.gather(_fetch(engine.client, syms), _derivatives(engine.client))
    score = trend_score(close, qvol)
    proof = walk_forward(close, score)
    quint = proof.get("quintiles", [])
    last = score.iloc[-1].dropna().sort_values(ascending=False)
    n = len(last)
    sig_last = {k: v.iloc[-1] for k, v in _signals(close, qvol).items()}
    ranks = []
    for i, (sym, s) in enumerate(last.items()):
        p, pn = score_to_prob(float(s), quint)
        d = deriv.get(sym)
        ranks.append({
            "symbol": sym, "rank": i + 1, "of": n, "score": round(float(s) * 100, 1),
            "quintile": min(int(float(s) * 5), 4) + 1,
            "trend": "strong up" if s >= 0.8 else "up" if s >= 0.6 else "strong down" if s < 0.2 else
                     "down" if s < 0.4 else "neutral",
            "p_up_week": round(p, 3), "p_n": pn,
            "mom_7d_pct": round(float(sig_last["mom_7d"].get(sym, np.nan)) * 100, 1),
            "mom_28d_pct": round(float(sig_last["mom_28d"].get(sym, np.nan)) * 100, 1),
            "derivs": d, "funding_note": _funding_note(d),
        })
    data = {"computed_at": int(time.time() * 1000), "runtime_s": round(time.time() - t0, 1),
            "coins": n, "regime": regime(close), "ranks": ranks, "proof": proof,
            "method": "Average cross-sectional rank of 8 trend signals (7/14/28-day momentum, price vs 20/50-day "
                      "average, 20-day average slope, volume trend, 28-day risk-adjusted momentum). Probabilities "
                      "are the measured next-week up-rate of each score quintile over the walk-forward.",
            "caveats": ["Walk-forward uses today's coin list (survivorship bias flatters results).",
                        "About one year of weekly data: results are noisy; treat as evidence, not proof.",
                        "Costs: 0.1% per unit of turnover; slippage and funding not included."]}
    log.info("quant_computed", coins=n, weeks=proof.get("weeks"), runtime_s=data["runtime_s"])
    return data


async def get(refresh: bool = False) -> dict | None:
    """Cached quant state. First call computes (about 20–40 s); later calls are instant, refreshed every 30 min."""
    if _state["data"] and not refresh and time.time() - _state["t"] < CACHE_S:
        return _state["data"]
    async with _lock:
        if _state["data"] and not refresh and time.time() - _state["t"] < CACHE_S:
            return _state["data"]
        try:
            _state["data"] = await compute()
            _state["t"] = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("quant_compute_failed", error=str(e))
    return _state["data"]


def peek() -> dict | None:
    return _state["data"]


def coin(symbol: str) -> dict | None:
    d = _state["data"]
    if not d:
        return None
    return next((r for r in d["ranks"] if r["symbol"] == symbol), None)
