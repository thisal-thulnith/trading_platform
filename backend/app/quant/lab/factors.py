"""Phase 2a — factor library, tradeable universe, forward returns, cost model and IC statistics.

Every factor value on date D uses only rows dated <= D (the daily kline of D closes at 23:59:59 UTC; funding
settlements are assigned to their UTC day). All operations are backward-looking rolling windows or ``shift(+n)``.
Forward returns are the only forward-looking objects and are used only as targets/evaluation.

Size proxy: Binance publishes no historical market cap / circulating supply, so "size" is log(30-day average quote
volume). It is a liquidity-size proxy, not market capitalisation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ANN = 365

# taker fee per side + slippage per side by 30-day average quote volume (USD) bucket
TAKER_FEE = 0.0005
SLIPPAGE_BUCKETS = ((100e6, 0.0002), (20e6, 0.0005), (5e6, 0.0010), (0.0, 0.0025))


@dataclass
class Wide:
    """Dates x symbols matrices on a complete daily calendar (NaN where a symbol has no row)."""
    close: pd.DataFrame
    high: pd.DataFrame
    quote_volume: pd.DataFrame
    taker_buy_quote: pd.DataFrame
    funding: pd.DataFrame          # daily sum of funding settlements (NaN where no perp)
    open: pd.DataFrame | None = None   # first trade of the UTC day (00:00) - used only by execution models
    low: pd.DataFrame | None = None
    volume: pd.DataFrame | None = None  # base-asset volume (quote_volume / volume = the day's VWAP)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def symbols(self) -> pd.Index:
        return self.close.columns

    def truncate(self, end) -> Wide:
        end = pd.Timestamp(end)
        return Wide(**{k: (None if getattr(self, k) is None else getattr(self, k).loc[:end])
                       for k in ("close", "high", "quote_volume", "taker_buy_quote", "funding", "open", "low",
                                 "volume")})


def build_wide(panel: pd.DataFrame, funding: pd.DataFrame | None = None, start=None, end=None) -> Wide:
    p = panel
    if start is not None:
        p = p[p["date"] >= str(pd.Timestamp(start).date())]
    if end is not None:
        p = p[p["date"] <= str(pd.Timestamp(end).date())]
    p = p.assign(date=pd.to_datetime(p["date"]))
    idx = pd.date_range(p["date"].min(), p["date"].max(), freq="D")
    cols = sorted(p["symbol"].unique())

    def piv(c: str) -> pd.DataFrame:
        return p.pivot(index="date", columns="symbol", values=c).reindex(index=idx, columns=cols).astype("float64")

    if funding is not None and len(funding):
        fd = funding.assign(date=pd.to_datetime(funding["date"]))
        fd = fd[fd["symbol"].isin(cols)]
        fw = fd.pivot(index="date", columns="symbol", values="funding").reindex(index=idx, columns=cols)
    else:
        fw = pd.DataFrame(np.nan, index=idx, columns=cols)
    opt = {c: piv(c) for c in ("open", "low", "volume") if c in p.columns}
    return Wide(close=piv("close"), high=piv("high"), quote_volume=piv("quote_volume"),
                taker_buy_quote=piv("taker_buy_quote"), funding=fw.astype("float64"), **opt)


# --- building blocks --------------------------------------------------------------------------------------------
def daily_returns(w: Wide) -> pd.DataFrame:
    """Simple close-to-close return of day D (NaN across gaps)."""
    return w.close / w.close.shift(1) - 1


def adv(w: Wide, n: int = 30) -> pd.DataFrame:
    return w.quote_volume.rolling(n, min_periods=max(5, int(n * 2 / 3))).mean()


def history_days(w: Wide) -> pd.DataFrame:
    return w.close.notna().cumsum()


def universe(w: Wide, min_history: int = 90, min_adv: float = 5e6) -> pd.DataFrame:
    """Tradeable on D: has a close and volume on D, >= min_history daily rows so far, 30d avg quote volume >= min_adv."""
    return (w.close.notna() & (w.quote_volume > 0) & (history_days(w) >= min_history) & (adv(w) >= min_adv))


def forward_returns(w: Wide, h: int) -> pd.DataFrame:
    """Return from close D to close D+h. A symbol delisted within the window exits at its last traded close."""
    cf = w.close.ffill()
    return cf.shift(-h) / w.close - 1


def cost_rate(w: Wide) -> pd.DataFrame:
    """One-way cost per unit of traded notional: taker fee + slippage by liquidity bucket (forward-filled)."""
    a = adv(w).ffill()
    out = pd.DataFrame(TAKER_FEE + SLIPPAGE_BUCKETS[-1][1], index=a.index, columns=a.columns)
    for lo, slip in reversed(SLIPPAGE_BUCKETS[:-1]):
        out = out.mask(a >= lo, TAKER_FEE + slip)
    return out


def btc_regime(w: Wide, symbol: str = "BTCUSDT", n: int = 200) -> pd.Series:
    """True when BTC closes above its 200-day average (known at D)."""
    c = w.close[symbol]
    return (c > c.rolling(n, min_periods=n).mean()).where(c.rolling(n, min_periods=n).mean().notna())


def _rolling_beta_corr(x: pd.DataFrame, y: pd.Series, n: int, min_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    yb = pd.DataFrame(np.repeat(y.to_numpy()[:, None], x.shape[1], axis=1), index=x.index, columns=x.columns)
    m = x.notna() & yb.notna()
    xv, yv = x.where(m, 0.0), yb.where(m, 0.0)
    r = lambda d: d.rolling(n, min_periods=1).sum()  # noqa: E731
    cnt = r(m.astype(float))
    sx, sy, sxy, sxx, syy = r(xv), r(yv), r(xv * yv), r(xv * xv), r(yv * yv)
    cov = sxy / cnt - sx * sy / cnt**2
    vx = sxx / cnt - (sx / cnt) ** 2
    vy = syy / cnt - (sy / cnt) ** 2
    ok = cnt >= min_n
    beta = (cov / vy).where(ok & (vy > 0))
    vx, vy = vx.clip(lower=0), vy.clip(lower=0)
    corr = (cov / np.sqrt(vx * vy)).where(ok & (vx > 0) & (vy > 0))
    return beta, corr


FACTOR_DOC = {
    "mom_7": "7-day return", "mom_14": "14-day return", "mom_28": "28-day return", "mom_90": "90-day return",
    "vadj_mom_28": "28-day return / (30-day daily vol x sqrt(28))",
    "rev_1": "minus the 1-day return (reversal)", "rev_5": "minus the 5-day return (reversal)",
    "rvol_30": "30-day realised volatility (annualised)",
    "dist_high_20": "close / 20-day high - 1", "dist_high_90": "close / 90-day high - 1",
    "vol_trend": "log(7-day avg quote volume / 30-day avg quote volume)",
    "taker_imb_7": "2 x taker-buy quote volume / quote volume - 1, 7-day",
    "amihud_30": "log10 of 30-day mean |return| / quote volume (illiquidity)",
    "beta_btc_60": "60-day beta to BTC daily returns", "corr_btc_60": "60-day correlation with BTC",
    "funding_7": "7-day mean of daily summed funding (perp)",
    "funding_chg_7": "funding_7 minus funding_7 seven days earlier",
    "size": "log(30-day average quote volume) - size proxy, no market-cap history available",
}
FACTORS = list(FACTOR_DOC)


def compute_factors(w: Wide) -> dict[str, pd.DataFrame]:
    """All factors as dates x symbols frames, each strictly from data dated <= D."""
    c, qv = w.close, w.quote_volume
    ret = daily_returns(w)
    lret = np.log(c / c.shift(1))
    f: dict[str, pd.DataFrame] = {}
    for n in (7, 14, 28, 90):
        f[f"mom_{n}"] = c / c.shift(n) - 1
    vol30 = lret.rolling(30, min_periods=20).std()
    f["vadj_mom_28"] = f["mom_28"] / (vol30 * np.sqrt(28))
    f["rev_1"] = -ret
    f["rev_5"] = -(c / c.shift(5) - 1)
    f["rvol_30"] = vol30 * np.sqrt(ANN)
    for n in (20, 90):
        f[f"dist_high_{n}"] = c / w.high.rolling(n, min_periods=int(n * 0.75)).max() - 1
    f["vol_trend"] = np.log(qv.rolling(7, min_periods=5).mean() / qv.rolling(30, min_periods=20).mean())
    tb = w.taker_buy_quote.rolling(7, min_periods=5).sum()
    tq = qv.rolling(7, min_periods=5).sum()
    f["taker_imb_7"] = (2 * tb / tq - 1).where(tq > 0)
    illiq = (ret.abs() / qv.where(qv > 0)).rolling(30, min_periods=20).mean()
    f["amihud_30"] = np.log10(illiq.where(illiq > 0))
    if "BTCUSDT" in c.columns:
        f["beta_btc_60"], f["corr_btc_60"] = _rolling_beta_corr(ret, ret["BTCUSDT"], 60, 40)
    else:
        f["beta_btc_60"] = f["corr_btc_60"] = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    fr = w.funding
    f["funding_7"] = fr.rolling(7, min_periods=5).mean()
    f["funding_chg_7"] = f["funding_7"] - f["funding_7"].shift(7)
    f["size"] = np.log(qv.rolling(30, min_periods=20).mean().where(lambda x: x > 0))
    for k in f:
        f[k] = f[k].replace([np.inf, -np.inf], np.nan)
    return {k: f[k] for k in FACTORS}


# --- statistics -------------------------------------------------------------------------------------------------
def cs_rank(df: pd.DataFrame, mask: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cross-sectional percentile rank in (0,1] among names in ``mask``; NaN elsewhere."""
    x = df.where(mask) if mask is not None else df
    return x.rank(axis=1, pct=True)


def rank_ic(factor: pd.DataFrame, fwd: pd.DataFrame, mask: pd.DataFrame, min_names: int = 10) -> pd.Series:
    """Daily cross-sectional Spearman correlation between a factor and forward returns over ``mask``."""
    m = mask & factor.notna() & fwd.notna()
    a = factor.where(m).rank(axis=1)
    b = fwd.where(m).rank(axis=1)
    a = a.sub(a.mean(axis=1), axis=0)
    b = b.sub(b.mean(axis=1), axis=0)
    num = (a * b).sum(axis=1)
    den = np.sqrt((a * a).sum(axis=1) * (b * b).sum(axis=1))
    ic = num / den
    return ic.where(m.sum(axis=1) >= min_names)


def newey_west_t(x: pd.Series | np.ndarray, lags: int) -> float:
    """t-statistic of the mean with a Newey-West (Bartlett) HAC variance."""
    v = np.asarray(pd.Series(x).dropna(), dtype=float)
    n = len(v)
    if n < 10:
        return float("nan")
    e = v - v.mean()
    s = e @ e / n
    for k in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(v.mean() / np.sqrt(s / n)) if s > 0 else float("nan")


def nw_lags(h: int) -> int:
    return max(5, 2 * h)
