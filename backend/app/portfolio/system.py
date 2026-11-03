"""System account: a $100 paper account that the platform runs by itself.

The strategy (strategies/daily_trend.py) picks the trades and the risk engine (risk/portfolio.py) decides every size,
limit and pause. This module only feeds them fresh daily candles and explains the result in plain words. With
venue="futures" the same trades are also written as Binance USDⓈ-M tickets (risk/futures.py) with the leverage capped so
the stop, not a liquidation, closes a loser. Paper only: no exchange order is ever placed.
"""
import asyncio
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from app.agents import desk_tools, llm, system_desk
from app.logging import log
from app.maintenance.funding_history import fetch as funding_fetch
from app.maintenance.history import COINS, fetch
from app.portfolio import budget, signals
from app.risk import futures as fut
from app.risk.portfolio import RISK_MODES, rules_for, run, summary
from app.strategies import daily_trend as dt

CACHE_S = 3600
DAYS = 600  # 200-day BTC average + a year of replay
PROOF = Path("data/history/portfolio_1d.json")
CARRY = Path("data/history/carry_study.json")
LEVERAGE_STUDY = Path("data/history/leverage_study.json")
VENUES = ("spot", "futures")
STUDY_PERIODS = ("train 2020-23", "valid 2024", "TEST 2025-now")
_cache: dict = {"t": 0.0, "frames": None, "filters_t": 0.0, "filters": {}}
_briefs: dict = {}
_lock = asyncio.Lock()


async def _frames() -> dict[str, pd.DataFrame]:
    async with _lock:
        if _cache["frames"] is not None and time.time() - _cache["t"] < CACHE_S:
            return _cache["frames"]
        start = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=DAYS)).value // 1_000_000)
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=30) as client:
            async def one(c: str):
                async with sem:
                    try:
                        df = await fetch(client, f"{c}USDT", "1d", start)
                    except httpx.HTTPError as e:
                        log.warning("system_fetch_failed", symbol=c, error=str(e))
                        return c, None
                    df["t"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
                    return c, df
            raw = dict(await asyncio.gather(*(one(c) for c in COINS)))
        if raw.get("BTC") is None:
            raise RuntimeError("BTC daily candles unavailable")
        btc_ok = dt.btc_filter(raw["BTC"])
        frames = {f"{c}USDT": dt.prepare(df, btc_ok) for c, df in raw.items() if df is not None and len(df) > 60}
        try:
            funding = await _funding()
        except Exception as e:  # noqa: BLE001 - carry is an extra; the trend account must still load
            log.warning("system_funding_failed", error=str(e))
            funding = {}
        _cache.update(t=time.time(), frames=frames, funding=funding)
        return frames


async def _filters() -> dict[str, fut.Filters]:
    """Binance USD-M lot/price/notional filters (public, no key), refreshed hourly; defaults when unreachable."""
    if time.time() - _cache["filters_t"] < CACHE_S and _cache["filters"]:
        return _cache["filters"]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            filters = await fut.fetch_filters(client, [f"{c}USDT" for c in COINS])
    except Exception as e:  # noqa: BLE001 - the ticket falls back to conservative default filters
        log.warning("system_filters_failed", error=str(e))
        filters = {}
    _cache.update(filters_t=time.time(), filters=filters)
    return filters


CARRY_ENTER_APR = 10.0  # % a year; below this the carry trade is not worth its costs (app/maintenance/carry_study.py)


async def _funding() -> dict[str, float]:
    """Average funding of the last 7 days per coin, as % a year (public Binance USD-M history)."""
    start = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=7)).value // 1_000_000)
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=30) as client:
        async def one(c: str):
            async with sem:
                try:
                    df = await funding_fetch(client, f"{c}USDT", start)
                except httpx.HTTPError:
                    return c, None
                return c, float(df.rate.mean() * 3 * 365 * 100) if len(df) else None
        return {f"{c}USDT": v for c, v in await asyncio.gather(*(one(c) for c in COINS)) if v is not None}


def _carry() -> dict:
    fund = _cache.get("funding") or {}
    top = sorted(fund.items(), key=lambda x: -x[1])[:5]
    study = json.loads(CARRY.read_text()) if CARRY.exists() else None
    best = next((r for r in (study or {}).get("rows", []) if r["k"] == 5 and r["enter_apr"] == 0.1), None)
    worth = [s for s, v in top if v >= CARRY_ENTER_APR]
    return {"top_7d_apr": [{"symbol": s, "apr_pct": round(v, 1)} for s, v in top], "enter_apr_pct": CARRY_ENTER_APR,
            "worth_it": worth, "account_apr_pct": round(sum(v for s, v in top if s in worth) / 5 / 2, 1),
            "research": {k: v for k, v in best.items() if k not in ("k", "enter_apr", "exit_apr")} if best else None}


def proof() -> dict | None:
    """Research results per risk mode (data/history/portfolio_1d.json); None until `edge_study portfolio` has run.
    An old single-mode file is ignored."""
    p = json.loads(PROOF.read_text()) if PROOF.exists() else None
    return p if p and "conservative" in p else None


def _leverage_study() -> dict | None:
    """app/maintenance/leverage_study.py results: liquidation by leverage, win rate vs target, tight stops."""
    if not LEVERAGE_STUDY.exists():
        return None
    s = json.loads(LEVERAGE_STUDY.read_text())
    for row in s["winrate_vs_target"] + s["tight_stops"]:
        n = sum(row[p]["n"] for p in STUDY_PERIODS if row[p].get("n"))
        if n:
            row["pooled"] = {"n": n, "win": round(sum(row[p]["win"] * row[p]["n"] for p in STUDY_PERIODS if row[p].get("n")) / n, 3),
                             "E": round(sum(row[p]["total"] for p in STUDY_PERIODS if row[p].get("n")) / n, 3)}
    return {k: s[k] for k in ("liquidation", "winrate_vs_target", "tight_stops")}


def _notes(proof: dict | None, mode: str, study: dict | None) -> list[str]:
    out = ["No system is 100 % accurate. Trend trades win about 4-5 in 10; winners pay more than losers because only "
           "a third is sold at 2R and the rest can run. A big win needs a big trend — the system cannot create one."]
    p = (proof or {}).get(mode)
    if p:
        tr, va, te, al = (p.get(k, {}) for k in ("train 2020-23", "valid 2024", "TEST 2025-now", "all 2020-now"))
        out.append(f"Research for {mode} mode on $100: 2020-23 -> ${tr.get('end_usd')} (worst drop "
                   f"{tr.get('max_drawdown_pct')} %); 2024 -> ${va.get('end_usd')}; untouched 2025-26 test -> "
                   f"${te.get('end_usd')} (worst drop {te.get('max_drawdown_pct')} %); all of 2020-now -> "
                   f"${al.get('end_usd')} ({al.get('yearly_pct')} %/year).")
    if study:
        lq = study["liquidation"]
        l20 = lq["by_leverage"]["20x"]
        safe = fut.safe_leverage(lq["median_stop_pct"] / 100, "XUSDT")
        out.append(f"Leverage: the system's stops sit about {lq['median_stop_pct']:.0f} % below entry, so the safe leverage "
                   f"is about {safe}x. At 20x, {l20['liquidated_pct']:.0f} % of its trades and "
                   f"{l20['winners_liquidated_pct']:.0f} % of its winners would have been liquidated before paying; $100 "
                   f"traded at 20x with 20 % margin per trade ended at ${l20['end_usd_margin20pct']:.0f}.")
        rows = {r["target_R"]: r["pooled"] for r in study["winrate_vs_target"] if "pooled" in r}
        if {0.25, 2.0, 3.0} <= set(rows):
            out.append(f"Win rate: no target reaches 90 %. The tightest target (0.25R) won {rows[0.25]['win']:.0%} of "
                       f"{rows[0.25]['n']} trades and earned {rows[0.25]['E']:+.2f}R each after costs; 2R won "
                       f"{rows[2.0]['win']:.0%} and earned {rows[2.0]['E']:+.2f}R; 3R won {rows[3.0]['win']:.0%} and earned "
                       f"{rows[3.0]['E']:+.2f}R. Accuracy is bought with payoff — the most accurate setting makes the least.")
        bw = lq["big_wins"]
        out.append(f"A +20 % trade is +10R at 2 % risk: it happened {bw['ge_10R']} times in {lq['trades']} trades 2020-now "
                   f"(best {bw['best_R']}R). Every stop tight enough for 20x (0.5-3 %) lost money in every period, on "
                   f"daily and on 4-hour candles.")
    out += ["Profit comes in strong bull markets; in 2024-26 coins barely trended and the trend edge was about flat. "
            "Higher risk modes grow faster in good years and fall harder in bad ones. No mode can promise 20 % or "
            "1000 %.",
            "Simulated paper account. Nothing is ever sent to an exchange."]
    return out


def _f(x: float, d: int = 2) -> float:
    return round(float(x), d)


HOW_TO = ["Binance Futures -> USDⓈ-M -> the contract below. Margin mode Isolated, leverage as the ticket says (never above it).",
          "Open: Buy/Long at the next daily open (00:00 UTC) with the ticket's quantity.",
          "Stop: STOP_MARKET, trigger at the stop price, same quantity, Reduce-Only on.",
          "Take profit: TAKE_PROFIT_MARKET at the 2R price for a third of the quantity, Reduce-Only on. When it fills, move "
          "the stop to entry and trail it 3 ATR below each daily close.",
          "Funding is paid every 8 hours while the position is open; the ticket estimates it from the last 7 days."]


ACCT_CACHE_S = 120
_accts: dict[tuple, tuple[float, dict]] = {}


async def account(capital: float = 100.0, replay_days: int = 365, mode: str = "conservative", venue: str = "spot",
                  leverage: int = 20) -> dict:
    """The account view, cached for two minutes per setting so the page, the desk and the portfolio share one replay."""
    key = (capital, replay_days, mode, venue, leverage)
    hit = _accts.get(key)
    if hit and time.time() - hit[0] < ACCT_CACHE_S:
        return hit[1]
    out = await _account(capital, replay_days, mode, venue, leverage)
    _accts[key] = (time.time(), out)
    return out


async def _account(capital: float, replay_days: int, mode: str, venue: str, leverage: int) -> dict:
    if mode not in RISK_MODES:
        raise ValueError(f"mode must be one of {sorted(RISK_MODES)}")
    if venue not in VENUES:
        raise ValueError(f"venue must be one of {VENUES}")
    if not 1 <= leverage <= 125:
        raise ValueError("leverage must be 1..125")
    frames = await _frames()
    rules = rules_for(mode, capital)
    last_day = max(f.index[-1] for f in frames.values())
    start = last_day - pd.Timedelta(days=replay_days)
    res = run(frames, rules, start)
    s = summary(res, rules)
    marks = {sym: float(f.close.iloc[-1]) for sym, f in frames.items()}
    equity = res.equity[-1]["equity"] if res.equity else capital
    peak = max((e["equity"] for e in res.equity), default=capital)

    open_rows = []
    for p in res.open:
        px = marks.get(p.symbol, p.entry)
        pnl = p.banked + p.qty * (px - p.entry) - p.entry_fee
        open_rows.append({"symbol": p.symbol, "opened": str(p.opened.date()), "entry": float(p.entry), "now": px,
                          "stop": float(p.stop), "target_2r": float(p.target), "half_sold_at_2r": bool(p.half_done),
                          "size_usd": _f(p.qty * px), "risk_usd": _f(p.open_risk()), "pnl_usd": _f(pnl),
                          "pnl_r": _f(pnl / p.risk_usd), "days": p.days,
                          "plan": ("A third sold at 2R; stop moved to entry and trails 3 ATR below the close."
                                   if p.half_done else "Sell a third at the 2R target, then trail the rest.")})

    btc = frames["BTCUSDT"]
    btc_ok = bool(btc.btc_ok.iloc[-1])
    # next open: signals from the last finished day, sized with the same rules the replay would use
    taken = {p.symbol for p in res.open}
    open_risk = sum(p.open_risk() for p in res.open)
    risk_usd = equity * res.risk_now_pct / 100
    orders, watch = [], []
    for sym, f in sorted(frames.items(), key=lambda x: -(x[1]["rank"].iloc[-1] if np.isfinite(x[1]["rank"].iloc[-1])
                                                          else -9)):
        row = f.iloc[-1]
        if sym in taken:
            continue
        if bool(row.signal):
            pl = dt.plan(float(row.close), float(row.atr))
            why = None
            if res.paused:
                why = "paused by the loss guard"
            elif pl is None:
                why = "stop too tight for the costs"
            elif len(res.open) + len(orders) >= rules.max_positions:
                why = "position limit reached"
            elif open_risk + risk_usd > equity * rules.max_open_risk_pct / 100 + 1e-9:
                why = "total risk limit reached"
            item = {"symbol": sym, "close": float(row.close), "momentum_60d_pct": _f(row["rank"] * 100, 1)}
            if pl and not why:
                per_unit = pl["risk_per_unit"] + row.close * (rules.fee + rules.fee + rules.slip)
                qty = min(risk_usd / per_unit, equity * rules.max_position_pct / 100 / row.close)
                item |= {"action": "BUY at next daily open", "stop": float(pl["stop"]), "target_2r": float(pl["target"]),
                         "size_usd": _f(qty * row.close), "risk_usd": _f(qty * per_unit),
                         "stop_pct": _f(pl["risk_per_unit"] / row.close * 100, 1)}
                orders.append(item)
                open_risk += qty * per_unit
            else:
                watch.append(item | {"action": "skip", "why": why})
        elif np.isfinite(row.hh) and row.close >= 0.95 * row.hh:
            watch.append({"symbol": sym, "close": float(row.close), "breakout_level": float(row.hh),
                          "distance_pct": _f((row.hh / row.close - 1) * 100, 1),
                          "action": "watch" if btc_ok else "watch (blocked while BTC is below its 200-day average)"})

    research = proof()
    study = _leverage_study()
    filters = await _filters() if venue == "futures" else {}
    plan = budget.plan(capital, mode, venue, leverage, frames, filters, research)
    dd_now = (peak - equity) / peak * 100 if peak else 0
    out = {
        "as_of": str(last_day.date()), "paper_only": True, "mode": mode, "modes": RISK_MODES, "venue": venue,
        "strategy": {"name": "Daily trend breakout", "rule": dt.__doc__},
        "rules": rules.d() | {"risk_now_pct": float(res.risk_now_pct)},
        "market": {"btc_above_200d": btc_ok, "btc_close": float(btc.close.iloc[-1]),
                   "plain": ("BTC is above its 200-day average: the system may buy breakouts." if btc_ok else
                             "BTC is below its 200-day average: the system stays in cash and buys nothing. "
                             "Not trading is the risk management.")},
        "account": {"start_usd": capital, "equity_usd": _f(equity), "cash_usd": _f(res.cash),
                    "drawdown_pct": _f(dd_now, 1), "replay_from": str(start.date()), "paused": bool(res.paused),
                    "open_risk_usd": _f(sum(p.open_risk() for p in res.open))} | s,
        "open_positions": open_rows,
        "next_orders": orders,
        "watchlist": watch[:12],
        "recent_trades": [{**t, "opened": str(t["opened"].date()), "closed": str(t["closed"].date()),
                           "entry": float(t["entry"]), "exit": float(t["exit"]), "pnl": _f(t["pnl"]), "r": _f(t["r"]),
                           "risk_usd": _f(t["risk_usd"]), "half_at_2r": bool(t["half_at_2r"])}
                          for t in res.trades[-20:][::-1]],
        "equity_curve": [{"day": str(e["day"].date()), "equity": _f(e["equity"])} for e in res.equity],
        "research_proof": research,
        "leverage_study": study,
        "budget_plan": plan,
        "honest_notes": _notes(research, mode, study),
        "carry": _carry(),
    }
    if venue == "futures":
        funding = _cache.get("funding") or {}
        tickets = [fut.ticket(o, leverage, filters.get(o["symbol"]), funding.get(o["symbol"]), rules.partial_frac)
                   for o in orders]
        fut_open = [fut.position_view(p.symbol, p.entry, p.stop, p.risk_per_unit, p.qty, row["now"], leverage)
                    for p, row in zip(res.open, open_rows)]
        out["futures"] = {"leverage_requested": leverage, "tickets": tickets, "open": fut_open,
                          "rules": fut.venue_rules(), "how_to": HOW_TO,
                          "filters_live": bool(filters)}
    # every researched coin analysed once; the ranked top list and the signal cards are built from those cards
    cards = desk_tools.analysis_cards(out, frames, _cache.get("funding") or {})
    out["top_list"] = signals.top_list(cards)
    out["signal_cards"] = signals.signal_cards(out, cards)
    out["agents"] = system_desk.brief(out, frames, mode)
    key = (out["as_of"], mode, capital, venue, leverage if venue == "futures" else None)
    if _briefs.get(key) is None:  # an empty or failed briefing is retried on the next call, not cached
        _briefs[key] = await system_desk.llm_briefing(out["agents"])
    out["briefing"] = _briefs[key]
    out["llm"] = llm.status()  # after the briefing attempt, so a provider failure (e.g. no credits) is reported
    return out
