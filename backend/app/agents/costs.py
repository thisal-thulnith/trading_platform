"""AI API cost: every LLM call's tokens, priced, so the dashboard can show what the agents cost.

Each call appends one line to data/llm_usage.jsonl (time, provider, model, feature, desk run, tokens, $). The price is
an ESTIMATE from the token counts the provider returns and the list price per 1M tokens below; the bill on the
provider's own dashboard is the truth (it may add taxes, long-context surcharges or credits).

GPT-5.5 list price (OpenAI model page, checked 2026-11-26): $5.00 input, $0.50 cached input, $30.00 output per 1M
tokens; reasoning tokens are billed as output (they are inside completion_tokens). Override with AIT_LLM_PRICE_INPUT,
AIT_LLM_PRICE_CACHED and AIT_LLM_PRICE_OUTPUT in backend/.env if the price changes or another model is used.
"""
from __future__ import annotations

import contextvars
import json
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.logging import log

LOG = Path(__file__).resolve().parents[2] / "data" / "llm_usage.jsonl"
CREDIT = Path(__file__).resolve().parents[2] / "data" / "llm_credit.json"   # the balance the user entered, and when
# $ per 1M tokens: (input, cached input, output)
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4": (2.50, 0.25, 15.00),
    # Anthropic list prices (2026-11): cache reads are the 'cached' column; cache writes are counted at the input price
    "claude-opus-5-5": (4.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 0.10, 10.00),
}
FEATURE: contextvars.ContextVar[str] = contextvars.ContextVar("llm_feature", default="other")
RUN: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_run", default=None)
LABELS = {"desk": "Research desk (AI agents)", "chat": "Chat assistant", "intel": "Intel desk", "live": "Live room",
          "system": "System desk", "coach": "Portfolio coach", "other": "Other"}


def price(model: str) -> tuple[float, float, float] | None:
    from app.config import get_settings

    s = get_settings()
    over = (getattr(s, "llm_price_input", None), getattr(s, "llm_price_cached", None), getattr(s, "llm_price_output", None))
    if all(v is not None for v in over):
        return over  # type: ignore[return-value]
    m = (model or "").lower()
    return next((v for k, v in sorted(PRICES.items(), key=lambda kv: -len(kv[0])) if m.startswith(k)), None)


def cost(model: str, inp: int, cached: int, out: int) -> float | None:
    p = price(model)
    if p is None:
        return None
    return ((inp - cached) * p[0] + cached * p[1] + out * p[2]) / 1_000_000


def _write(row: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        log.warning("llm_usage_write_failed", error=str(e))


def _record(provider: str, model: str, inp: int, cached: int, out: int, reasoning: int = 0) -> None:
    c = cost(model, inp, cached, out)
    _write({"ts": time.time(), "provider": provider, "model": model, "feature": FEATURE.get(), "run": RUN.get(),
            "in": inp, "cached": cached, "out": out, "reasoning": reasoning,
            "usd": None if c is None else round(c, 6)})


def track_openai(r, model: str) -> None:
    """After a chat.completions call. Never raises: cost tracking must not break an answer."""
    try:
        u = getattr(r, "usage", None)
        if u is None:
            return
        pd = getattr(u, "prompt_tokens_details", None)
        cd = getattr(u, "completion_tokens_details", None)
        _record("openai", getattr(r, "model", None) or model, int(u.prompt_tokens or 0),
                int(getattr(pd, "cached_tokens", 0) or 0), int(u.completion_tokens or 0),
                int(getattr(cd, "reasoning_tokens", 0) or 0))
    except Exception as e:  # noqa: BLE001
        log.warning("llm_usage_track_failed", error=str(e))


def track_anthropic(r, model: str) -> None:
    """After a messages.create call: input + cache writes + cache reads are all input; cache reads are 'cached'."""
    try:
        u = getattr(r, "usage", None)
        if u is None:
            return
        read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        _record("anthropic", getattr(r, "model", None) or model, int(u.input_tokens or 0) + read + write, read,
                int(u.output_tokens or 0))
    except Exception as e:  # noqa: BLE001
        log.warning("llm_usage_track_failed", error=str(e))


def _rows() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _sum(rows: list[dict]) -> dict:
    known = [r["usd"] for r in rows if r.get("usd") is not None]
    return {"calls": len(rows), "input": sum(r["in"] for r in rows), "cached": sum(r["cached"] for r in rows),
            "output": sum(r["out"] for r in rows), "usd": round(sum(known), 4),
            "unpriced_calls": len(rows) - len(known)}


def set_credit(amount: float | None) -> dict:
    """The provider balance as the user reads it on the billing page (the API cannot read it with a normal key).
    Usage is counted from this moment on; None clears it."""
    if amount is None:
        CREDIT.unlink(missing_ok=True)
        return {}
    v = {"amount": round(float(amount), 2), "set_at": time.time()}
    CREDIT.parent.mkdir(parents=True, exist_ok=True)
    CREDIT.write_text(json.dumps(v))
    return v


def credit(rows: list[dict] | None = None, avg_run: float | None = None) -> dict | None:
    """Balance left = the entered balance minus the estimated spend since it was entered."""
    if not CREDIT.exists():
        return None
    try:
        c = json.loads(CREDIT.read_text())
    except ValueError:
        return None
    rows = _rows() if rows is None else rows
    used = _sum([r for r in rows if r["ts"] >= c["set_at"]])["usd"]
    left = round(c["amount"] - used, 4)
    return {"amount": c["amount"], "set_at": datetime.fromtimestamp(c["set_at"], UTC).isoformat(), "used": round(used, 4),
            "left": left, "used_pct": round(min(1.0, used / c["amount"]), 4) if c["amount"] > 0 else None,
            "desk_runs_left": int(max(0.0, left) // avg_run) if avg_run else None, "low": left < max(1.0, 0.1 * c["amount"])}


def run_cost(run_id: str) -> dict:
    return _sum([r for r in _rows() if r.get("run") == run_id])


def summary(days: int = 14) -> dict:
    rows = _rows()
    now = datetime.now(UTC)
    day = lambda r: datetime.fromtimestamp(r["ts"], UTC).date()  # noqa: E731
    today = now.date()
    since = lambda d: [r for r in rows if day(r) >= today - timedelta(days=d - 1)]  # noqa: E731
    by_day: dict = defaultdict(list)
    for r in since(days):
        by_day[str(day(r))].append(r)
    by_feature: dict = defaultdict(list)
    for r in since(30):
        by_feature[r.get("feature") or "other"].append(r)
    runs: dict = defaultdict(list)
    for r in rows:
        if r.get("run"):
            runs[r["run"]].append(r)
    desk_runs = sorted(({"run": k, "at": max(x["ts"] for x in v), **_sum(v)} for k, v in runs.items()),
                        key=lambda x: -x["at"])
    per_run = [x["usd"] for x in desk_runs if x["usd"]]
    from app.agents.llm import provider
    from app.config import get_settings

    s = get_settings()
    p = provider()
    model = (s.openai_model if p == "openai" else s.llm_model) if p else None
    pr = price(model) if model else None
    return {
        "model": model, "provider": p, "price_per_1m": None if pr is None else {"input": pr[0], "cached": pr[1], "output": pr[2]},
        "today": _sum(since(1)), "last_7_days": _sum(since(7)), "last_30_days": _sum(since(30)), "all_time": _sum(rows),
        "since": datetime.fromtimestamp(rows[0]["ts"], UTC).isoformat() if rows else None,
        "by_day": [{"day": str(today - timedelta(days=i)), **_sum(by_day.get(str(today - timedelta(days=i)), []))}
                   for i in range(days - 1, -1, -1)],
        "by_feature": sorted(({"feature": k, "label": LABELS.get(k, k), **_sum(v)} for k, v in by_feature.items()),
                             key=lambda x: -x["usd"]),
        "desk_runs": desk_runs[:10],
        "avg_desk_run_usd": round(sum(per_run) / len(per_run), 4) if per_run else None,
        "credit": credit(rows, sum(per_run) / len(per_run) if per_run else None),
        "note": "Estimate from the token counts the provider returns and the list price; the provider's billing page is "
                "the exact amount.",
    }
