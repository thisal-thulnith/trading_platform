"""LLM layer: Claude (official Anthropic SDK) or OpenAI (official OpenAI SDK). Optional: every caller has a
deterministic fallback. Provider choice: AIT_LLM_PROVIDER=auto picks Anthropic when its key is set, else OpenAI.

Guardrails:
- The model only sees structured, already-computed evidence and is told never to invent numbers.
- Chart manipulation happens exclusively through strictly-typed tools whose outputs are validated
  server-side before reaching the frontend; no free-form code execution.
- Server-side refusal fallback is enabled (beta `server-side-fallback-2026-07-01`, fallbacks="default").
"""
import json
import time

import anthropic
import openai

from app.config import get_settings
from app.logging import log

FALLBACK_BETA = "server-side-fallback-2026-07-01"
STATUS: dict = {"error": None, "at": None, "ok_at": None}  # the last provider failure, so pages can say why AI is off


def note_failure(err: str) -> None:
    STATUS.update(error=str(err)[:300], at=time.time())


def note_success() -> None:
    STATUS.update(error=None, at=None, ok_at=time.time())


def status() -> dict:
    """Is an LLM configured, which one, and is it currently failing (e.g. out of credits)?"""
    p = provider()
    s = get_settings()
    return {"configured": p is not None, "provider": p,
            "model": (s.openai_model if p == "openai" else s.llm_model) if p else None,
            "error": STATUS["error"], "error_at": STATUS["at"], "ok_at": STATUS["ok_at"],
            "hint": None if p else "set AIT_OPENAI_API_KEY or AIT_ANTHROPIC_API_KEY in backend/.env"}

NARRATE_SYSTEM = """You are the Chief Analyst of a crypto market-intelligence platform. You receive JSON reports \
from specialised analysis agents. Every number in them was computed deterministically from exchange data.

Rules:
- Use only numbers that appear in the reports. Never invent prices, probabilities, indicator values or outcomes.
- Lead with the platform's decision (LONG / SHORT / NO_TRADE) and why, then the strongest supporting evidence, \
then the strongest counter-arguments, then what would change the view (invalidation / missing conditions).
- Treat SMC and Elliott Wave labels as descriptive heuristics, not facts. Say when evidence is mixed.
- If the ML agent is unavailable, say no validated model is in use; do not speculate about probabilities.
- This is decision support, not financial advice. Be concise: at most ~250 words, markdown allowed."""


def provider() -> str | None:
    s = get_settings()
    if s.llm_provider == "openai" or (s.llm_provider == "auto" and not s.anthropic_api_key):
        return "openai" if s.openai_api_key else None
    return "anthropic" if s.anthropic_api_key else None


def client() -> anthropic.AsyncAnthropic | None:
    s = get_settings()
    if provider() != "anthropic":
        return None
    return anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, max_retries=2, timeout=60.0)


def openai_client() -> openai.AsyncOpenAI | None:
    s = get_settings()
    if provider() != "openai":
        return None
    return openai.AsyncOpenAI(api_key=s.openai_api_key, max_retries=2, timeout=90.0)


async def complete(system: str, user: str, max_tokens: int = 4000, json_mode: bool = False) -> str | None:
    """One-shot text completion on whichever provider is configured. json_mode asks OpenAI for a JSON object
    (Claude is instructed in the prompt; callers parse defensively either way)."""
    if provider() == "openai":
        try:
            r = await openai_client().chat.completions.create(
                model=get_settings().openai_model, max_completion_tokens=max_tokens,
                **({"response_format": {"type": "json_object"}} if json_mode else {}),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        except openai.APIError as e:
            log.warning("llm_openai_failed", error=str(e))
            note_failure(str(e))
            return None
        note_success()
        return (r.choices[0].message.content or "").strip() or None
    c = client()
    if c is None:
        return None
    try:
        r = await create(c, max_tokens=max_tokens,
                         system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                         messages=[{"role": "user", "content": user}])
    except anthropic.APIError as e:
        log.warning("llm_anthropic_failed", error=str(e))
        note_failure(str(e))
        return None
    note_success()
    if r.stop_reason == "refusal":
        return None
    return "".join(b.text for b in r.content if b.type == "text").strip() or None


def compact_reports(reports: dict) -> dict:
    keep = {}
    for k, r in reports.items():
        keep[k] = {"status": r["status"], "summary": r["summary"],
                   "findings": [f["text"] for f in r.get("findings", [])[:8]]}
        if k in ("strategy", "risk", "validation"):
            keep[k]["data"] = r.get("data")
    return keep


async def create(c: anthropic.AsyncAnthropic, **kw):
    s = get_settings()
    return await c.beta.messages.create(model=s.llm_model, betas=[FALLBACK_BETA], fallbacks="default",
                                        output_config={"effort": s.llm_effort}, **kw)


async def narrate(snap: dict, reports: dict) -> str | None:
    if provider() is None:
        return None
    payload = {"symbol": snap["symbol"], "timeframe": snap["timeframe"], "price": snap["price"],
               "decision": snap.get("decision"), "confluence": {k: snap["confluence"][k] for k in
                                                                ("bias", "direction", "families")},
               "reports": compact_reports(reports)}
    return await complete(NARRATE_SYSTEM, json.dumps(payload, default=str))


WALK_SYSTEM = """You are a senior crypto market analyst teaching a beginner. You receive a structured, step-by-step \
analysis that was computed deterministically from exchange data.

Write a short, friendly analyst commentary (about 150-220 words, markdown) that connects the steps into one story: \
higher-timeframe context → structure → where liquidity and zones are → what would confirm each scenario → the plan. \
Rules: use only numbers present in the input; never invent prices or probabilities; say clearly when evidence is mixed; \
treat SMC and Elliott labels as heuristics; end with what to watch next. Not financial advice."""


async def narrate_walkthrough(walk: dict) -> str | None:
    if provider() is None:
        return None
    payload = {"symbol": walk["symbol"], "timeframe": walk["timeframe"], "price": walk["price"],
               "synthesis": walk["synthesis"], "decision": walk["decision"],
               "steps": [{"title": s["title"], "observation": s["observation"], "verdict": s["verdict_word"],
                          "bullets": s["bullets"][:6]} for s in walk["steps"]]}
    return await complete(WALK_SYSTEM, json.dumps(payload, default=str))
