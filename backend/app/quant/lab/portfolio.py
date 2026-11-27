"""Phase 3b — portfolio construction and a holdings-based daily backtest with costs and funding.

Timing (``PortfolioConfig.execution``; the Market must be prepared with the same ``fill``):
- ``"close"`` (legacy, lab-1/lab-2): scores computed from data through the close of D are traded AT the close of D,
  and the position earns close D -> close D+1. Optimistic: it trades on the very price the signal is computed from.
- ``"next_open"``: the decision made with data through the close of D is filled at the OPEN of D+1 and the position
  earns open D+1 -> open D+2. The signal-day close is never a fill price. (On Binance the D+1 open is the first trade
  after the D close, so in a 24/7 market this is close to the signal price — see docs/research/EXECUTION_PREREG.)
- ``"next_vwap"``: filled at the VWAP of day D+1 (quote volume / base volume, clipped to [low, high]), i.e. worked
  over the 24 hours after the signal; the position earns VWAP D+1 -> VWAP D+2.
``lag`` adds whole days of delay between the signal and the decision (stress test). If a coin has no candle on the fill
day (delisted/halted) it cannot be bought; an existing position exits at its last close.

Orders (``order_type``): ``"taker"`` pays ``factors.cost_rate`` (taker fee + liquidity slippage) on every unit traded.
``"maker"`` (next_open only) places a limit at the fill-day open price L: a buy fills only if that day's low trades at
least ``maker_through_bps`` below L (low <= L (1 - x)), a sell only if the high trades that far above L; a filled order
executes at L and pays ``maker_fee`` (no slippage); an unfilled order is cancelled at the end of the day, nothing is
traded for that coin (a new slot stays empty, an exit is retried at the next rebalance). Exits of coins with no candle
on the fill day are forced at the taker rate. With ``next_vwap`` a maker order is assumed worked passively to the VWAP
(always filled, maker fee).

Rebalancing: every ``rebalance`` days counted from the simulation start, or, when ``anchor`` is set, on the calendar
days with (D - anchor) % rebalance == 0 (so a live book and its backtest rebalance on the same days). A no-trade band
(``band`` > 0) leaves a holding unchanged when |target - current| <= band; names leaving the target book (target 0)
are always closed. Costs are charged on every unit of notional traded x cost_mult.
Longs are spot (no funding). Shorts are USDT-M perps: only coins with a perp on D can be shorted, and a short
receives (pays) the funding settled on D+1 when it is positive (negative).

Weights: top-k by score (bottom-k for shorts) after a liquidity/capacity filter, inverse 30-day volatility, a per-coin
cap and a capacity cap (max ``max_adv_participation`` of 30-day ADV at ``aum``), scaled to a portfolio volatility
target estimated from the trailing 60-day covariance (never above the gross limits: no leverage on the long side),
and moved toward the target by at most ``turnover_limit`` of one-way notional per rebalance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from app.quant.lab.factors import ANN, Wide, adv, cost_rate, daily_returns, universe


@dataclass
class PortfolioConfig:
    k: int = 10
    side: str = "long"                 # "long" | "long_short"
    rebalance: int = 7                 # days between rebalances
    weighting: str = "inv_vol"         # "inv_vol" | "equal"
    select: str = "top_k"              # "top_k" | "positive" (all names with score > 0) | "all"
    vol_target: float | None = 0.5     # annualised; None disables
    vol_target_ls: float = 0.3         # used instead when side == "long_short"
    max_gross_long: float = 1.0
    max_gross_short: float = 1.0
    coin_cap: float = 0.25
    aum: float = 1_000_000.0
    max_adv_participation: float = 0.02
    turnover_limit: float | None = 1.0  # max sum |dw| per rebalance (None = unlimited)
    dust: float = 0.01                 # after a turnover-limited trade, close leftover non-target positions below this
    cost_mult: float = 1.0
    lag: int = 0
    cov_window: int = 60
    # execution (defaults reproduce lab-1/lab-2; omitted from to_dict() at their defaults so old trial keys hold)
    execution: str = "close"           # "close" | "next_open" | "next_vwap"
    order_type: str = "taker"          # "taker" | "maker"
    maker_fee: float = 0.0002
    maker_through_bps: float = 10.0
    band: float = 0.0                  # no-trade band in weight units (0 = off)
    anchor: str | None = None          # calendar anchor of the rebalance schedule (None = simulation start)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["order_type"] == "taker":
            d.pop("maker_fee")
            d.pop("maker_through_bps")
        for k, v in EXEC_DEFAULTS.items():
            if d.get(k) == v:
                d.pop(k)
        return d


EXEC_DEFAULTS = {"execution": "close", "order_type": "taker", "band": 0.0, "anchor": None}


@dataclass
class Market:
    dates: pd.DatetimeIndex
    symbols: pd.Index
    r_next: np.ndarray       # return close D -> close D+1 (0 after delisting / when missing)
    ret: np.ndarray          # return of day D (for covariance), NaN when missing
    vol: np.ndarray          # 30-day annualised vol
    adv: np.ndarray
    cost: np.ndarray
    funding_next: np.ndarray  # funding settled on D+1 (0 when none)
    has_perp: np.ndarray      # a perp existed on D (funding observed in the last 3 days)
    univ: np.ndarray
    regime: pd.Series = field(default=None)
    fill: str = "close"
    tradable: np.ndarray | None = None    # [t]: the coin has a real price on the fill day of decision t
    fill_px: np.ndarray | None = None     # [t]: fill price of decision t (open/VWAP of t+1, else the last close)
    low_next: np.ndarray | None = None    # [t]: low / high of the fill day (maker fill rule)
    high_next: np.ndarray | None = None
    mark: str = "fill"                    # "fill": mark fill-to-fill; "close": mark at closes, trade at the fill price
    r_pre: np.ndarray | None = None       # [t]: close t -> fill price on t+1 (old holdings, mark="close")
    r_post: np.ndarray | None = None      # [t]: fill price on t+1 -> close t+1 (new holdings, mark="close")
    r_cc: np.ndarray | None = None        # [t]: close t -> close t+1 (no trade, mark="close")


FILLS = ("close", "next_open", "next_vwap")


def fill_prices(w: Wide, fill: str) -> pd.DataFrame:
    """Price at which a decision is filled on day D (open or VWAP of D); NaN where the coin has no candle on D."""
    if fill == "next_open":
        if w.open is None:
            raise ValueError("next_open execution needs opens in the panel")
        return w.open.where(w.open > 0)
    if fill == "next_vwap":
        if w.volume is None or w.low is None:
            raise ValueError("next_vwap execution needs base volume and lows in the panel")
        vw = (w.quote_volume / w.volume.where(w.volume > 0)).clip(lower=w.low, upper=w.high, axis=None)
        return vw.where(vw > 0)
    raise ValueError(fill)


def prepare_market(w: Wide, univ: pd.DataFrame | None = None, fill: str = "close", mark: str = "fill") -> Market:
    """``mark="close"`` (next_open/next_vwap only): holdings are marked at daily closes and a rebalance is executed at
    the fill price inside the next day (old holdings earn close -> fill, new holdings fill -> close). Marking
    fill-to-fill is equivalent for opens (open D+1 ~ close D) but smooths returns for VWAP fills (averaged prices),
    which understates volatility; use mark="close" for VWAP."""
    if fill not in FILLS or mark not in ("fill", "close") or (fill == "close" and mark != "fill"):
        raise ValueError((fill, mark))
    cf = w.close.ffill()
    ret = daily_returns(w)
    vol = np.log(w.close / w.close.shift(1)).rolling(30, min_periods=20).std() * np.sqrt(ANN)
    u = universe(w) if univ is None else univ
    fund = w.funding
    has_perp = fund.notna().rolling(3, min_periods=1).max().fillna(0).astype(bool)
    extra: dict = {"fill": fill}
    if fill == "close":
        r_next = (cf.shift(-1) / cf - 1).fillna(0.0)
    else:
        P = fill_prices(w, fill)
        # fallback when the coin has no candle on the fill day: its last close before that day (= exit at last close)
        Pf = P.where(P.notna(), cf.shift(1)).ffill()
        nxt = Pf.shift(-2)
        # the holding of the last decision before the end of the panel is marked at the last close (live use only;
        # backtests stop one decision earlier so this fallback never enters a backtest return)
        nxt = nxt.where(nxt.notna() | Pf.shift(-1).isna(), cf.shift(-1))
        r_next = (nxt / Pf.shift(-1) - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if mark == "close":
            F = Pf.shift(-1)                                  # fill price of decision t (on day t+1)
            c_next = cf.shift(-1)
            extra.update(mark="close", r_pre=(F / cf - 1).fillna(0.0).to_numpy(copy=True),
                         r_post=(c_next / F - 1).fillna(0.0).to_numpy(copy=True),
                         r_cc=(c_next / cf - 1).fillna(0.0).to_numpy(copy=True))
        extra.update(tradable=P.shift(-1).notna().to_numpy(copy=True), fill_px=Pf.shift(-1).to_numpy(copy=True),
                     low_next=(w.low.shift(-1).to_numpy(copy=True) if w.low is not None else None),
                     high_next=w.high.shift(-1).to_numpy(copy=True))
    return Market(dates=w.dates, symbols=w.symbols, r_next=r_next.to_numpy(), ret=ret.to_numpy(),
                  vol=vol.to_numpy(), adv=adv(w).ffill().to_numpy(), cost=cost_rate(w).to_numpy(),
                  funding_next=fund.shift(-1).fillna(0.0).to_numpy(), has_perp=has_perp.to_numpy(),
                  univ=u.to_numpy(), **extra)


def maker_fills(mkt: Market, t: int, dw: np.ndarray, through_bps: float) -> np.ndarray:
    """Which limit orders of decision t fill: buys need the fill-day low <= L(1-x), sells the high >= L(1+x)."""
    x = through_bps / 1e4
    L = mkt.fill_px[t]
    with np.errstate(invalid="ignore"):
        buy_ok = np.nan_to_num(mkt.low_next[t], nan=np.inf) <= L * (1 - x)
        sell_ok = np.nan_to_num(mkt.high_next[t], nan=-np.inf) >= L * (1 + x)
    return np.where(dw > 0, buy_ok, np.where(dw < 0, sell_ok, True))


def _cap_normalize(raw: np.ndarray, caps: np.ndarray, total: float) -> np.ndarray:
    """Scale positive ``raw`` to sum ``total`` subject to per-name caps (excess redistributed; leftover = cash)."""
    if raw.sum() <= 0:
        return np.zeros_like(raw)
    w = raw / raw.sum() * total
    for _ in range(50):
        over = w > caps + 1e-12
        if not over.any():
            break
        excess = (w[over] - caps[over]).sum()
        w[over] = caps[over]
        free = (w < caps - 1e-12) & (raw > 0)
        if not free.any():
            break
        w[free] += excess * raw[free] / raw[free].sum()
    return np.minimum(w, caps)


def target_weights(score: np.ndarray, t: int, mkt: Market, cfg: PortfolioConfig,
                   long_ok: np.ndarray | None = None, short_ok: np.ndarray | None = None) -> np.ndarray:
    """Target weight vector for day t from a score row (NaN = not eligible).

    ``long_ok`` / ``short_ok`` (optional boolean rows) restrict which names may be held long / short (abstention)."""
    n = len(score)
    w = np.zeros(n)
    elig = np.isfinite(score) & mkt.univ[t] & np.isfinite(mkt.vol[t]) & (mkt.vol[t] > 0)
    caps = np.minimum(cfg.coin_cap, cfg.max_adv_participation * np.nan_to_num(mkt.adv[t]) / cfg.aum)
    if cfg.select == "top_k":
        # capacity filter: a name whose capacity cap is below half an equal slot cannot hold a real position
        elig &= caps >= 0.5 / max(cfg.k, 1) * cfg.max_gross_long
    idx = np.flatnonzero(elig)
    if len(idx) == 0:
        return w
    lidx = idx if long_ok is None else idx[long_ok[idx]]
    s = score[lidx]
    idx_all, idx = idx, lidx
    if cfg.select == "top_k":
        longs = idx[np.argsort(-s, kind="stable")[: cfg.k]]
    elif cfg.select == "positive":
        longs = idx[s > 0]
    else:
        longs = idx
    shorts = np.array([], dtype=int)
    if cfg.side == "long_short":
        sidx = idx_all[mkt.has_perp[t, idx_all]]
        if short_ok is not None:
            sidx = sidx[short_ok[sidx]]
        ss = score[sidx]
        shorts = sidx[np.argsort(ss, kind="stable")[: cfg.k]]
        shorts = np.setdiff1d(shorts, longs)
    for names, total, sign in ((longs, cfg.max_gross_long, 1.0), (shorts, cfg.max_gross_short, -1.0)):
        if len(names) == 0:
            continue
        raw = 1.0 / mkt.vol[t, names] if cfg.weighting == "inv_vol" else np.ones(len(names))
        w[names] = sign * _cap_normalize(raw, caps[names], total)
    target = cfg.vol_target_ls if cfg.side == "long_short" else cfg.vol_target
    if target:
        nz = np.flatnonzero(w)
        lo = max(0, t - cfg.cov_window + 1)
        r = np.nan_to_num(mkt.ret[lo: t + 1, nz])
        if len(r) >= 20:
            cov = np.cov(r, rowvar=False) * ANN
            pv = float(np.sqrt(max(w[nz] @ np.atleast_2d(cov) @ w[nz], 0.0)))
            if pv > 0:
                w *= min(1.0, target / pv)
    return w


@dataclass
class BacktestResult:
    returns: pd.Series        # net daily return, labelled by the date it is realised (D+1)
    gross: pd.Series          # before costs and funding
    costs: pd.Series
    funding: pd.Series
    turnover: pd.Series       # sum |dw| traded on D
    exposure: pd.DataFrame    # long, short gross
    n_positions: pd.Series
    orders: pd.Series | None = None   # number of orders placed on D (rebalance days)
    filled: pd.Series | None = None   # of which filled (maker); equals orders for taker
    final_weights: np.ndarray | None = None


def is_rebalance_day(dates: pd.DatetimeIndex, t: int, cfg: PortfolioConfig, since: int | None) -> bool:
    if cfg.anchor is not None:
        return (dates[t] - pd.Timestamp(cfg.anchor)).days % cfg.rebalance == 0
    return since is None or since >= cfg.rebalance


def trade_step(w: np.ndarray, row: np.ndarray, t: int, mkt: Market, cfg: PortfolioConfig,
               long_ok: np.ndarray | None = None, short_ok: np.ndarray | None = None) -> dict | None:
    """One rebalance at decision t: target, no-trade band, turnover limit, dust rule, fills and costs.

    Returns ``{w, dw, cost, turnover, orders, filled, target}`` or None when nothing can be scored."""
    if mkt.tradable is not None:
        row = np.where(mkt.tradable[t], row, np.nan)   # no candle on the fill day: cannot be bought
    if not np.isfinite(row).any():
        return None
    tgt = target_weights(row, t, mkt, cfg, long_ok, short_ok)
    dw = tgt - w
    if cfg.band > 0:
        hold = (np.abs(dw) <= cfg.band) & ~((tgt == 0) & (w != 0))   # exits always trade
        dw[hold] = 0.0
    to = float(np.abs(dw).sum())
    if cfg.turnover_limit is not None and to > cfg.turnover_limit and w.any():
        dw *= cfg.turnover_limit / to
    if cfg.dust:
        # positions being exited that are already small are closed outright (no dust tail)
        wn = w + dw
        tiny = (tgt == 0) & (wn != 0) & (np.abs(wn) < cfg.dust)
        dw[tiny] -= wn[tiny]
    rate = np.nan_to_num(mkt.cost[t], nan=0.003)
    orders = int((dw != 0).sum())
    if cfg.order_type == "maker":
        if mkt.fill == "close":
            raise ValueError("maker orders need a next-day fill model (next_open or next_vwap)")
        gone = ~mkt.tradable[t]                      # forced exit at the last close, taker rate
        if mkt.fill == "next_open":
            ok = maker_fills(mkt, t, dw, cfg.maker_through_bps) | gone
            dw = np.where(ok, dw, 0.0)
        rate = np.where(gone, rate, cfg.maker_fee)
    cost = float(np.abs(dw) @ rate) * cfg.cost_mult
    return {"w": w + dw, "dw": dw, "cost": cost, "turnover": float(np.abs(dw).sum()), "orders": orders,
            "filled": int((dw != 0).sum()), "target": tgt}


def _mask_row(m, t):
    return None if m is None else (m[t] if isinstance(m, np.ndarray) else m.iloc[t].to_numpy())


def simulate(scores: np.ndarray | pd.DataFrame, mkt: Market, cfg: PortfolioConfig,
             start=None, end=None, long_ok=None, short_ok=None) -> BacktestResult:
    """Daily holdings simulation between ``start`` and ``end`` (dates), trading only where scores exist.

    ``long_ok`` / ``short_ok``: optional dates x symbols boolean matrices restricting each side (abstention)."""
    if cfg.execution != mkt.fill:
        raise ValueError(f"config execution {cfg.execution!r} needs a market prepared with fill={cfg.execution!r}")
    S = scores.to_numpy() if isinstance(scores, pd.DataFrame) else scores
    T, N = S.shape
    d = mkt.dates
    last = T - 1 if mkt.fill == "close" else T - 2   # next-day fills need the price two days ahead
    t0 = 0 if start is None else int(d.searchsorted(pd.Timestamp(start)))
    t1 = last if end is None else min(last, int(d.searchsorted(pd.Timestamp(end), side="right")) - 1)
    w = np.zeros(N)
    out_r, out_g, out_c, out_f, out_to, out_l, out_s, out_n, out_o, out_fl, idx = ([] for _ in range(11))
    since = None
    for t in range(t0, t1):
        ts = t - cfg.lag
        cost = 0.0
        to = 0.0
        n_ord = n_fill = 0
        g1 = 0.0
        rebal = is_rebalance_day(d, t, cfg, since) and ts >= 0
        if mkt.mark == "close":
            if rebal:   # old holdings ride from the close of t to the fill inside t+1, then the book trades
                g1 = float(w @ mkt.r_pre[t])
                w = w * (1.0 + mkt.r_pre[t]) / (1.0 + g1) if g1 > -1 else np.zeros(N)
            rn = mkt.r_post[t] if rebal else mkt.r_cc[t]
        else:
            rn = mkt.r_next[t]
        if rebal:
            st = trade_step(w, S[ts], t, mkt, cfg, _mask_row(long_ok, ts), _mask_row(short_ok, ts))
            if st is not None:
                w, cost, to, n_ord, n_fill = st["w"], st["cost"], st["turnover"], st["orders"], st["filled"]
                since = 0
        g = float(w @ rn)
        fnd = float(-np.minimum(w, 0) @ mkt.funding_next[t])  # shorts receive positive funding
        g = (1.0 + g1) * (1.0 + g) - 1.0 if g1 else g
        net = g + fnd - cost
        out_r.append(net)
        out_g.append(g)
        out_c.append(cost)
        out_f.append(fnd)
        out_to.append(to)
        out_l.append(float(w[w > 0].sum()))
        out_s.append(float(-w[w < 0].sum()))
        out_n.append(int((w != 0).sum()))
        out_o.append(n_ord)
        out_fl.append(n_fill)
        idx.append(d[t + 1])
        gd = float(w @ rn)
        w = w * (1.0 + rn) / (1.0 + gd) if 1.0 + gd > 0 else np.zeros(N)
        if since is not None:
            since += 1
    ix = pd.DatetimeIndex(idx)
    return BacktestResult(returns=pd.Series(out_r, ix), gross=pd.Series(out_g, ix), costs=pd.Series(out_c, ix),
                          funding=pd.Series(out_f, ix), turnover=pd.Series(out_to, ix),
                          exposure=pd.DataFrame({"long": out_l, "short": out_s}, index=ix),
                          n_positions=pd.Series(out_n, ix), orders=pd.Series(out_o, ix),
                          filled=pd.Series(out_fl, ix), final_weights=w)


def live_book(scores: np.ndarray, mkt: Market, cfg: PortfolioConfig, start) -> dict:
    """Replay the book from ``start`` to the last date of ``mkt`` and decide the last date (live use).

    Past rebalances use the known fills (maker rule on the following day's range); the holding over the last fill
    period is marked at the last close. Returns the drifted holdings before today's decision, today's target and the
    book after today's orders (all orders assumed filled), and whether today is a rebalance day."""
    T, N = scores.shape
    res = simulate(scores, mkt, cfg, start=start) if mkt.fill == "close" else None
    if res is None:
        # run through decision T-2 (its holding period ends at today's last close via the fallback price)
        d = mkt.dates
        w = np.zeros(N)
        since = None
        t0 = int(d.searchsorted(pd.Timestamp(start)))
        for t in range(t0, T - 1):
            ts = t - cfg.lag
            if is_rebalance_day(d, t, cfg, since) and ts >= 0:
                st = trade_step(w, scores[ts], t, mkt, cfg)
                if st is not None:
                    w = st["w"]
                    since = 0
            g = float(w @ mkt.r_next[t])
            w = w * (1.0 + mkt.r_next[t]) / (1.0 + g) if 1.0 + g > 0 else np.zeros(N)
            if since is not None:
                since += 1
        held = w
    else:
        held = res.final_weights
        since = None
    t = T - 1
    today = is_rebalance_day(mkt.dates, t, cfg, since if cfg.anchor is None else None)
    out = {"held": held, "rebalance_today": bool(today), "target": None, "book": held, "orders": np.zeros(N)}
    if today and t - cfg.lag >= 0:
        live_cfg = PortfolioConfig(**{**asdict(cfg), "order_type": "taker"}) if mkt.fill == "close" else cfg
        st = trade_step(held, scores[t - cfg.lag], t, _assume_filled(mkt), live_cfg)
        if st is not None:
            out.update(target=st["target"], book=st["w"], orders=st["dw"])
    return out


def _assume_filled(mkt: Market) -> Market:
    """Today's orders are not filled yet: every coin is tradable at tomorrow's open and every limit is assumed filled
    (the orders are what the book should place; their fills are known only tomorrow)."""
    if mkt.fill == "close":
        return mkt
    from dataclasses import replace
    t = len(mkt.dates) - 1
    tradable, px, hi = mkt.tradable.copy(), mkt.fill_px.copy(), mkt.high_next.copy()
    lo = mkt.low_next.copy() if mkt.low_next is not None else np.zeros_like(px)
    tradable[t], px[t], lo[t], hi[t] = True, 1.0, 0.0, np.inf
    return replace(mkt, tradable=tradable, fill_px=px, low_next=lo, high_next=hi)


def metrics(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 2 or r.std() == 0:
        return {"days": int(len(r)), "sharpe": 0.0, "ann_return": 0.0, "ann_vol": 0.0, "cagr": 0.0,
                "max_drawdown": 0.0}
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    years = len(r) / ANN
    return {"days": int(len(r)), "ann_return": round(float(r.mean() * ANN), 4),
            "ann_vol": round(float(r.std() * np.sqrt(ANN)), 4),
            "sharpe": round(float(r.mean() / r.std() * np.sqrt(ANN)), 3),
            "cagr": round(float(eq.iloc[-1] ** (1 / years) - 1), 4) if eq.iloc[-1] > 0 else -1.0,
            "max_drawdown": round(float(dd.min()), 4), "hit_rate": round(float((r > 0).mean()), 3),
            "skew": round(float(r.skew()), 3), "kurtosis": round(float(r.kurt() + 3), 3)}


def summarize(res: BacktestResult) -> dict:
    m = metrics(res.returns)
    m["avg_turnover_per_day"] = round(float(res.turnover.mean()), 4)
    m["avg_cost_per_year"] = round(float(res.costs.mean() * ANN), 4)
    m["avg_funding_per_year"] = round(float(res.funding.mean() * ANN), 4)
    m["avg_long"] = round(float(res.exposure["long"].mean()), 3)
    m["avg_short"] = round(float(res.exposure["short"].mean()), 3)
    m["avg_positions"] = round(float(res.n_positions.mean()), 1)
    if res.orders is not None and res.orders.sum() > 0:
        m["orders"] = int(res.orders.sum())
        m["fill_rate"] = round(float(res.filled.sum() / res.orders.sum()), 4)
    return m


# --- baselines --------------------------------------------------------------------------------------------------
def baseline_scores(w: Wide, mkt: Market, kind: str) -> tuple[np.ndarray, PortfolioConfig]:
    T, N = mkt.univ.shape
    if kind == "ew_universe":
        return np.where(mkt.univ, 1.0, np.nan), PortfolioConfig(select="all", weighting="equal", vol_target=None,
                                                               coin_cap=1.0, turnover_limit=None, rebalance=7,
                                                               max_adv_participation=1e9)
    if kind == "btc_hold":
        s = np.full((T, N), np.nan)
        s[:, list(mkt.symbols).index("BTCUSDT")] = 1.0
        return s, PortfolioConfig(k=1, select="top_k", weighting="equal", vol_target=None, coin_cap=1.0,
                                  turnover_limit=None, rebalance=10**9, max_adv_participation=1e9)
    if kind == "trend20":
        mom = (w.close / w.close.shift(20) - 1).to_numpy()
        return np.where(mkt.univ, mom, np.nan), PortfolioConfig(select="positive", weighting="equal",
                                                                vol_target=None, coin_cap=1.0, turnover_limit=None,
                                                                rebalance=7, max_adv_participation=1e9)
    raise ValueError(kind)


def equity_curve(r: pd.Series, every: int = 7) -> list[list]:
    eq = (1 + r.fillna(0)).cumprod()
    eq = eq.iloc[::every] if len(eq) > every else eq
    return [[str(i.date()), round(float(v), 4)] for i, v in eq.items()]
