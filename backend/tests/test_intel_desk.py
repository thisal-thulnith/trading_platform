import json
import types

from app.agents import desk_tools, intel_desk
from app.agents.toolkit import AgentRun, Tool, parse_json, run_agent
from tests.test_system_risk import _frames


def test_parse_json_takes_the_last_object():
    assert parse_json('x {"a": 1} y {"text": "ok", "level": "good", "picks": []}')["level"] == "good"
    assert parse_json('{"text": "nested {braces} inside", "picks": [{"symbol": "A", "reason": "r"}]}')["picks"][0]["symbol"] == "A"
    assert parse_json("no json") is None and parse_json(None) is None


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


def _call(cid, name, args):
    return types.SimpleNamespace(id=cid, function=types.SimpleNamespace(name=name, arguments=args))


def _resp(msg):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class FakeOpenAI:
    """Scripted chat.completions: first asks for two tools (one unknown), then answers with the JSON object."""

    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return _resp(_Msg(None, [_call("c1", "add", json.dumps({"a": 2, "b": 3})), _call("c2", "nope", "{}")]))
        tool_msgs = [m for m in kw["messages"] if m["role"] == "tool"]
        assert json.loads(tool_msgs[0]["content"]) == {"sum": 5} and "unknown tool" in tool_msgs[1]["content"]
        return _resp(_Msg('Sum is 5.\n{"text": "Sum is 5.", "level": "good", "picks": [], "confidence": 0.9}'))


ADD = Tool("add", "adds two numbers", lambda a, b: {"sum": a + b},
           {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]})


async def test_run_agent_calls_tools_then_answers():
    fake = FakeOpenAI()
    res = await run_agent("sys", "task", [ADD], oai_client=fake)
    assert res.data["level"] == "good" and res.steps == 2 and res.text.startswith("Sum is 5.")
    assert [u["name"] for u in res.tools_used] == ["add", "nope"] and res.tools_used[0]["ok"] and not res.tools_used[1]["ok"]
    assert fake.calls[0]["tools"][0]["function"]["name"] == "add" and fake.calls[1]["messages"][2]["role"] == "assistant"


async def test_run_agent_concludes_without_tools_when_out_of_steps():
    class Loop(FakeOpenAI):
        async def create(self, **kw):
            self.calls.append(kw)
            if "tools" in kw:
                return _resp(_Msg(None, [_call("c", "add", json.dumps({"a": 1, "b": 1}))]))
            return _resp(_Msg('{"text": "done", "level": "info", "picks": [], "confidence": 0.5}'))

    res = await run_agent("sys", "task", [ADD], max_steps=2, oai_client=Loop())
    assert res.data["text"] == "done" and res.steps == 3 and len(res.tools_used) == 2


async def test_bad_tool_arguments_are_reported_to_the_model_not_raised():
    class Bad(FakeOpenAI):
        async def create(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                return _resp(_Msg(None, [_call("c", "add", json.dumps({"a": 1}))]))
            assert "bad arguments" in [m for m in kw["messages"] if m["role"] == "tool"][0]["content"]
            return _resp(_Msg('{"text": "fixed", "level": "info", "picks": []}'))

    res = await run_agent("sys", "task", [ADD], oai_client=Bad())
    assert res.data["text"] == "fixed" and not res.tools_used[0]["ok"]


def _acct():
    return {"as_of": "2024-11-14", "mode": "growth", "venue": "spot",
            "market": {"btc_above_200d": True, "btc_close": 100.0, "plain": "up"},
            "account": {"equity_usd": 100.0, "return_pct": 0.0, "trades": 0, "drawdown_pct": 0.0, "paused": False,
                        "open_risk_usd": 0.0, "start_usd": 100.0, "replay_from": "2023-11-14"},
            "rules": {"capital": 100, "risk_now_pct": 2.0, "risk_pct": 2.0, "fee": 0.001, "slip": 0.0005,
                      "max_position_pct": 40.0, "partial_frac": 1 / 3, "max_positions": 5, "max_open_risk_pct": 10.0},
            "open_positions": [], "next_orders": [], "watchlist": [], "recent_trades": [],
            "carry": {"top_7d_apr": []}, "budget_plan": None, "research_proof": None}


def test_analysis_cards_and_tools_on_synthetic_frames(monkeypatch):
    monkeypatch.setattr(desk_tools, "breakout_records", lambda: {"C1USDT": {"trades": 9, "win_rate": 0.44, "avg_r": 0.3,
                                                                           "best_r": 4.0, "since": "2020-05-01", "last_3": []}})
    frames = _frames(n_coins=6)
    frames["BTCUSDT"] = frames["C0USDT"]
    acct = _acct()
    cards = desk_tools.analysis_cards(acct, frames, {"C1USDT": 12.0})
    assert set(cards) == set(frames) and cards["C1USDT"]["funding_apr_pct"] == 12.0 and cards["C1USDT"]["safe_leverage"] >= 1
    assert cards["C1USDT"]["breakout_record"]["trades"] == 9 and cards["C2USDT"]["breakout_record"] is None
    tools = {t.name: t for t in desk_tools.build_tools(acct, frames, cards)}
    top = tools["top_coins"].fn(n=3)
    assert len(top["coins"]) == 3 and top["coins"][0]["symbol"] in cards and "record" in top["coins"][0]
    assert "error" in tools["top_coins"].fn(universe="all")  # the quant brain has not computed in tests
    reg = tools["market_regime"].fn()
    assert reg["coins"] == len(cards) and "share_above_50d_pct" in reg and reg["btc_above_200d"]
    assert tools["coin_analysis"].fn("c1")["symbol"] == "C1USDT" and "error" in tools["coin_analysis"].fn("ZZZ")
    t = tools["futures_ticket"].fn("C1", 20)
    assert t["leverage_set"] <= 20 and t["stop"]["trigger"] < t["entry_ref"] < t["take_profit"]["trigger"]
    assert tools["my_portfolio"].fn()["note"] and tools["account_state"].fn()["paper_only"]
    assert "topics" in desk_tools.research_facts("nonsense")
    sys_facts = desk_tools.research_facts("system")
    assert "per_mode" in sys_facts or "error" in sys_facts


async def test_desk_run_builds_the_top_list_from_the_scouts_picks(monkeypatch):
    async def fake_run(system, task, tools, **kw):
        name = system.split("—")[0].replace("You are", "").strip()
        picks = ([{"symbol": "c1", "reason": "strongest"}, {"symbol": "XYZ", "reason": "outside"}] if name == "Scout" else [])
        return AgentRun(text="raw", data={"text": f"{name} says hi", "level": "good", "picks": picks, "confidence": 0.7},
                        tools_used=[{"name": "top_coins", "args": {}, "ok": True}], steps=2, model="fake")

    monkeypatch.setattr(intel_desk, "run_agent", fake_run)
    run = {"kind": "system", "status": "running", "agents": [], "top_coins": [], "tool_calls": 0, "t": 0}
    await intel_desk._run(run, intel_desk.SYSTEM_AGENTS, [], {"C1USDT": {"symbol": "C1USDT", "verdict": "near"}}, "ctx")
    assert run["status"] == "ready" and len(run["agents"]) == 7 and run["tool_calls"] == 7
    assert run["agents"][1]["text"] == "Scout says hi" and run["agents"][1]["tools_used"][0]["name"] == "top_coins"
    assert run["top_coins"][0]["symbol"] == "C1USDT" and run["top_coins"][0]["rank"] == 1
    assert run["top_coins"][1]["verdict"].startswith("outside") and run["top_coins"][0]["reason"] == "strongest"


async def test_desk_run_survives_one_failing_agent(monkeypatch):
    async def boom(system, task, tools, **kw):
        if "Scout" in system:
            raise RuntimeError("provider down")
        return AgentRun(text="fine", data={"text": "fine", "level": "info", "picks": []}, tools_used=[], steps=1, model="fake")

    monkeypatch.setattr(intel_desk, "run_agent", boom)
    run = {"kind": "system", "status": "running", "agents": [], "top_coins": [], "tool_calls": 0, "t": 0, "model": "fake"}
    await intel_desk._run(run, intel_desk.SYSTEM_AGENTS[:3], [], {}, "ctx")
    assert run["status"] == "ready" and run["agents"][1]["error"] == "provider down" and run["agents"][2]["text"] == "fine"


async def test_desk_stops_after_a_fatal_provider_error_and_retries_soon(monkeypatch):
    calls = []

    async def no_credits(system, task, tools, **kw):
        calls.append(system)
        raise RuntimeError("Error code: 429 - You have no credits remaining. Add credits to continue using the API at https://x")

    monkeypatch.setattr(intel_desk, "run_agent", no_credits)
    run = {"kind": "system", "status": "running", "agents": [], "top_coins": [], "tool_calls": 0, "t": 0, "model": "gpt-x"}
    await intel_desk._run(run, intel_desk.SYSTEM_AGENTS, [], {}, "ctx")
    assert len(calls) == 1 and len(run["agents"]) == 7  # one paid call, six skipped
    assert run["status"] == "unavailable" and run["error"] == "gpt-x: You have no credits remaining."
    assert run["agents"][1]["error"].startswith("skipped: You have no credits")
    assert not intel_desk.desk_tools.fresh(run["t"], intel_desk.CACHE_S - intel_desk.RETRY_S - 1)  # expires in ~5 min
