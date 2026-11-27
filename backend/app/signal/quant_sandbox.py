"""The quant sandbox: the quant lab's portfolio, paper-traded forward from today with realistic execution.

Each day the lab's daily panel gains a candle (the data job refreshes it after midnight UTC). Then:
  1. targets decided on day t (the lab's live ranking and portfolio weights, rebalanced on its schedule) are filled at
     the OPEN of day t+1 — never at the close the signal was computed on — paying taker fees and slippage on the
     turnover;
  2. the book is marked to market at each close (open-to-close on the fill day, close-to-close after);
  3. the equity curve, drawdown and positions are kept in data/quant_sandbox/state.json.
This is the forward test that decides whether the lab's backtest survives contact with live data. While no model is
promoted it trades the lab's unvalidated composite ranking (long-only, weekly) and says so; when a model is promoted it
switches to that model's portfolio settings.
"""
import json
import math
import time
from pathlib import Path

from app.logging import log

DIR = Path("data/quant_sandbox")
PATH = DIR / "state.json"  # the composite book (kept at its original path)
BOOKS = {
    "composite": "the lab's composite ranking (unvalidated), long-only, weekly",
    "C": "pre-registered candidate C: LightGBM 7-day, weekly, long-short top/bottom 10 (unvalidated, delay-robust in backtest)",
}


def _path(book: str) -> Path:
    return PATH if book == "composite" else DIR / f"state_{book}.json"


def _ranking(book: str):
    """The ranking function a book follows (None when that book's code is not available yet)."""
    if book == "composite":
        from app.quant.lab import live_ranking

        return lambda d: live_ranking(as_of=d)
    try:
        from app.quant.lab.candidate import candidate_book
    except ImportError:
        return None
    return lambda d: candidate_book(book, as_of=d)
FEE, SLIP = 0.0005, 0.0005  # taker fee and a conservative slippage per side, on turnover
START_EQUITY = 10_000.0


def _load(book: str = "composite") -> dict:
    try:
        return json.loads(_path(book).read_text())
    except (OSError, json.JSONDecodeError):
        return {"start_equity": START_EQUITY, "equity": START_EQUITY, "weights": {}, "pending": None, "last_date": None,
                "last_rebalance": None, "history": [], "trades": [], "costs_paid": 0.0, "started": None}


def _save(st: dict, book: str = "composite") -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    _path(book).write_text(json.dumps(st, default=str))


def _px(panel, date: str) -> dict[str, tuple[float, float]]:
    d = panel[panel["date"] == date]
    return {r.symbol: (float(r.open), float(r.close)) for r in d.itertuples() if r.open > 0 and r.close > 0}


def step_panel(st: dict, panel, ranking_fn) -> dict:
    """Advance the book over every panel date after st["last_date"] (pure apart from ranking_fn; testable)."""
    dates = sorted(str(x) for x in panel["date"].unique())
    if st["last_date"] is None:  # start: no history, decide the first targets on the latest day
        st["last_date"], st["started"] = dates[-1], dates[-1]
        st["history"].append({"date": dates[-1], "equity": st["equity"]})
    new = [d for d in dates if d > st["last_date"]]
    prev = _px(panel, st["last_date"]) if new else {}
    for d in new:
        px = _px(panel, d)
        filled_today = set()
        if st["pending"] and st["pending"]["signal_date"] < d:  # fill yesterday's decision at today's open
            tgt = st["pending"]["weights"]
            turnover = sum(abs(tgt.get(s, 0.0) - st["weights"].get(s, 0.0)) for s in set(tgt) | set(st["weights"]))
            cost = turnover * (FEE + SLIP) * st["equity"]
            st["equity"] -= cost
            st["costs_paid"] = round(st["costs_paid"] + cost, 2)
            st["trades"].append({"date": d, "turnover": round(turnover, 4), "cost_usd": round(cost, 2), "n": len(tgt)})
            filled_today = {s for s in tgt if tgt[s] != st["weights"].get(s, 0.0)}
            st["weights"] = {s: w for s, w in tgt.items() if w}
            st["last_rebalance"], st["pending"] = d, None
        ret = 0.0
        for s, w in st["weights"].items():
            if s not in px:
                continue  # no candle (delisted or halted): carried flat; a delisting exits at its last close
            o, c = px[s]
            base = o if s in filled_today else (prev.get(s, (o, o))[1])
            if base > 0 and math.isfinite(c / base):
                ret += w * (c / base - 1)
        st["equity"] = round(st["equity"] * (1 + ret), 2)
        st["history"].append({"date": d, "equity": st["equity"]})
        st["last_date"], prev = d, px
    # decide new targets on the latest day when the schedule says so
    lr = ranking_fn(st["last_date"])
    pf = lr.get("portfolio") or {}
    every = int(pf.get("rebalance_days") or 7)
    if "rebalance_today" in pf:  # a book with its own anchored calendar (candidate C): follow it exactly
        due = st["last_rebalance"] is None or bool(pf["rebalance_today"])
    else:
        due = st["last_rebalance"] is None or (_days(st["last_rebalance"], st["last_date"]) >= every)
    if due and not st["pending"]:
        w = {r["symbol"]: (r["weight"] if r.get("side") != "short" else -abs(r["weight"])) for r in lr.get("rows", [])
             if r.get("weight")}
        st["pending"] = {"signal_date": st["last_date"], "weights": w, "promoted": lr.get("promoted"),
                         "model": (lr.get("model") or {}).get("model") if lr.get("model") else "composite (unvalidated)"}
    st["settings"] = {"rebalance_days": every, "promoted": lr.get("promoted"), "portfolio": lr.get("portfolio"),
                      "note": lr.get("note"), "spec_sha": lr.get("spec_sha"), "next_rebalance": pf.get("next_rebalance")}
    return st


def _days(a: str, b: str) -> int:
    from datetime import date

    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def step() -> dict:
    """Advance every available book to the latest panel date (call after the lab's data update)."""
    from app.quant.lab.data import load_panel

    panel, out = load_panel(), {}
    for book in BOOKS:
        fn = _ranking(book)
        if fn is None:
            continue
        try:
            st = step_panel(_load(book), panel, fn)
        except Exception as e:  # noqa: BLE001 - one book failing must not stop the others
            log.warning("quant_sandbox_book_failed", book=book, error=str(e))
            continue
        _save(st, book)
        out[book] = st
        log.info("quant_sandbox_step", book=book, date=st["last_date"], equity=st["equity"])
    return out


def books() -> list[dict]:
    """Every book with its summary (books whose code is not built yet are listed as waiting)."""
    return [{"book": b, "about": about, "available": _ranking(b) is not None, **summary(b)} for b, about in BOOKS.items()]


def summary(book: str = "composite") -> dict:
    st = _load(book)
    h = st["history"]
    peak, dd = 0.0, 0.0
    for p in h:
        peak = max(peak, p["equity"])
        dd = max(dd, 1 - p["equity"] / peak if peak else 0)
    rets = [h[i]["equity"] / h[i - 1]["equity"] - 1 for i in range(1, len(h))]
    sd = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 2 else None
    sharpe = round((sum(rets) / len(rets)) / sd * math.sqrt(365), 2) if sd else None
    return {"started": st.get("started"), "last_date": st.get("last_date"), "start_equity": st["start_equity"],
            "equity": st["equity"], "return_pct": round((st["equity"] / st["start_equity"] - 1) * 100, 2),
            "max_drawdown_pct": round(dd * 100, 2), "sharpe_ann": sharpe, "days": max(0, len(h) - 1),
            "costs_paid": st.get("costs_paid", 0.0), "weights": dict(sorted(st["weights"].items(), key=lambda kv: -abs(kv[1]))),
            "pending": st.get("pending"), "last_rebalance": st.get("last_rebalance"), "settings": st.get("settings"),
            "curve": [{"t": int(time.mktime(time.strptime(p["date"], "%Y-%m-%d")) * 1000), "v": p["equity"]} for p in h],
            "trades": st["trades"][-12:],
            "note": "Paper-trades the book forward: decided at the close, filled at the next open, fees and slippage on "
                    "turnover (short-side funding not charged yet). Judge it after months, not days."}
