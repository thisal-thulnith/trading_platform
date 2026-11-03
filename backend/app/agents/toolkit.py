"""Tool-use agent loop on the platform's LLM layer (OpenAI function calling or Claude tool use, whichever is configured).

An agent is a system prompt, a task and a list of Tools. The model decides which tools to call and in what order;
every tool is a deterministic platform function, so the model can only reason over measured data. The loop ends
when the model answers without a tool call (or after `max_steps`, when it is asked to conclude). Callers get the final
text, the parsed JSON object the agent was asked to finish with, and the list of tools used — shown to the user so
the reasoning is auditable.
"""
import asyncio
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.agents.llm import client as anthropic_client
from app.agents.llm import create, openai_client, provider
from app.config import get_settings
from app.logging import log

RESULT_CHARS = 12_000  # a tool result is clipped to this many characters before the model sees it


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class AgentRun:
    text: str
    data: dict | None
    tools_used: list[dict]
    steps: int
    model: str


def parse_json(text: str | None) -> dict | None:
    """The last {...} object in the text, if it parses."""
    if not text:
        return None
    for m in reversed(list(re.finditer(r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}", text, re.S))):
        try:
            v = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            return v
    return None


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


async def call_tool(tools: list[Tool], name: str, raw_args, used: list[dict]) -> str:
    tool = next((t for t in tools if t.name == name), None)
    args = _args(raw_args)
    rec = {"name": name, "args": args, "ok": True}
    used.append(rec)
    if tool is None:
        rec["ok"] = False
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        out = tool.fn(**args)
        if inspect.isawaitable(out):
            out = await out
    except TypeError as e:  # wrong arguments: tell the model instead of failing the run
        rec["ok"] = False
        out = {"error": f"bad arguments for {name}: {e}", "schema": tool.parameters}
    except Exception as e:  # noqa: BLE001 - a tool failure is data for the model, not a crash
        log.warning("desk_tool_failed", tool=name, error=str(e))
        rec["ok"] = False
        out = {"error": str(e)}
    s = json.dumps(out, default=str)
    return s if len(s) <= RESULT_CHARS else s[:RESULT_CHARS] + '... (clipped)"}'


async def run_agent(system: str, task: str, tools: list[Tool], max_steps: int = 6, max_tokens: int = 4000,
                    oai_client=None) -> AgentRun | None:
    """Run one agent to completion. Returns None when no LLM is configured. `oai_client` lets tests inject a fake
    OpenAI-shaped client."""
    prov = "openai" if oai_client is not None else provider()
    if prov is None:
        return None
    if prov == "openai":
        return await _run_openai(system, task, tools, max_steps, max_tokens, oai_client or openai_client())
    return await _run_anthropic(system, task, tools, max_steps, max_tokens)


async def _run_openai(system: str, task: str, tools: list[Tool], max_steps: int, max_tokens: int, client) -> AgentRun:
    s = get_settings()
    model = s.openai_model
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
             for t in tools]
    msgs: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    used: list[dict] = []
    text, steps = "", 0
    for step in range(max_steps + 1):
        steps = step + 1
        final = step == max_steps  # out of steps: conclude without tools
        r = await client.chat.completions.create(
            model=model, messages=msgs, max_completion_tokens=max_tokens,
            **({"tools": specs, "tool_choice": "auto"} if specs and not final else {}))
        m = r.choices[0].message
        calls = getattr(m, "tool_calls", None) or []
        if calls and not final:
            msgs.append({"role": "assistant", "content": m.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                        for c in calls]})
            results = await asyncio.gather(*(call_tool(tools, c.function.name, c.function.arguments, used) for c in calls))
            for c, res in zip(calls, results):
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": res})
            continue
        text = (m.content or "").strip()
        break
    return AgentRun(text, parse_json(text), used, steps, model)


async def _run_anthropic(system: str, task: str, tools: list[Tool], max_steps: int, max_tokens: int) -> AgentRun:
    c = anthropic_client()
    specs = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
    msgs: list = [{"role": "user", "content": task}]
    used: list[dict] = []
    text, steps = "", 0
    for step in range(max_steps + 1):
        steps = step + 1
        final = step == max_steps
        r = await create(c, max_tokens=max_tokens, messages=msgs,
                         system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                         **({"tools": specs} if specs and not final else {}))
        uses = [b for b in r.content if getattr(b, "type", "") == "tool_use"]
        if r.stop_reason == "tool_use" and uses and not final:
            msgs.append({"role": "assistant", "content": r.content})
            results = await asyncio.gather(*(call_tool(tools, b.name, b.input, used) for b in uses))
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id, "content": res}
                                                     for b, res in zip(uses, results)]})
            continue
        text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()
        break
    return AgentRun(text, parse_json(text), used, steps, get_settings().llm_model)
