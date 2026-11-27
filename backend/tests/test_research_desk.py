import asyncio

import pytest

from app.agents import knowledge, research_desk
from app.models import MyTrade
from app.portfolio import my_trades as mt
from app.risk import futures as fut
from app.risk import plan as tp

# ------------------------------------------------------------------------------------------- plan math

def test_long_plan_sizes_by_risk_and_uses_the_lowest_leverage_that_fits():
    p = tp.build("SOLUSDT", "LONG", 100.0, 96.0, 110.0, 100.0, "growth", "futures", fut.Filters(0.01, 0.01, 0.01, 5.0))
    assert p["ok"] and p["side"] == "LONG"
    per_unit = 4.0 + 100.0 * (2 * fut.TAKER + tp.SLIP)
    assert p["qty"] == fut.round_step(2.0 / per_unit, 0.01)                    # growth: 2% of $100 at risk
    assert p["risk_usd"] <= 2.0 + 1e-9
    # the margin cap is 40% of the budget in growth mode: $42 notional fits at 2x, not 1x
    assert p["leverage"] == 2 and p["margin_usd"] <= 40.0
    assert p["leverage"] <= p["leverage_safe_max"] == fut.safe_leverage(0.04, "SOLUSDT")
    assert p["liquidation"] < p["stop"] < p["entry"] < p["target"]
    assert p["rr_net"] > 2 and len(p["orders"]) == 4 and "Isolated, leverage 2x" in p["orders"][0]


def test_short_plan_mirrors_the_long():
    p = tp.build("SOLUSDT", "SHORT", 100.0, 104.0, 90.0, 100.0, "growth", "futures", fut.Filters(0.01, 0.01, 0.01, 5.0))
    assert p["ok"] and p["target"] < p["entry"] < p["stop"] < p["liquidation"]
    assert any("Shorts failed" in w for w in p["warnings"]) and "SELL / SHORT" in p["orders"][1]


def test_plan_leverage_stays_within_safe_and_margin_within_the_cap():
    for mode, cap in (("conservative", 30), ("growth", 40), ("aggressive", 50)):
        for stop_pct in (0.004, 0.01, 0.03, 0.08, 0.2):
            p = tp.build("ETHUSDT", "LONG", 100.0, 100.0 * (1 - stop_pct), None, 1000.0, mode, "futures",
                         fut.Filters(0.001, 0.001, 0.01, 20))
            assert p["ok"], p["error"]
            assert 1 <= p["leverage"] <= p["leverage_safe_max"]
            assert p["margin_usd"] <= 1000 * cap / 100 + 0.01
            assert p["liquidation_pct"] >= 1.5 * stop_pct * 100 - 0.05   # liquidation well beyond the stop
            assert p["leverage"] == 1 or p["notional_usd"] / (p["leverage"] - 1) > 1000 * cap / 100  # lowest that fits


def test_plan_refuses_bad_inputs_and_too_small_orders():
    assert not tp.build("SOLUSDT", "LONG", 100, 101, 110, 100, "growth")["ok"]           # stop on the wrong side
    assert "short needs futures" in tp.build("SOLUSDT", "SHORT", 100, 104, 90, 100, "growth", "spot")["error"]
    assert not tp.build("SOLUSDT", "LONG", 100, 99.95, 110, 100, "growth")["ok"]          # stop too tight
    tiny = tp.build("BTCUSDT", "LONG", 100.0, 90.0, 120.0, 20.0, "conservative", "futures", fut.Filters(0.001, 0.001, 0.1, 50))
    assert not tiny["ok"] and "minimum" in tiny["error"] and tiny["orders"] == []
    warn = tp.build("SOLUSDT", "LONG", 100.0, 99.0, 100.5, 100.0, "growth")             # 0.5R target
    assert warn["ok"] and any("below the research minimum" in w for w in warn["warnings"])


def _snap(price=100.0):
    return {"price": price, "indicators": {"atr14": 2.0},
            "smc": {"swings": [{"kind": "low", "price": 96.0, "label": "HL"}, {"kind": "high", "price": 104.0, "label": "LH"}],
                    "liquidity": [{"type": "buy_side_liquidity", "price": 112.0, "status": "resting", "source": "swing_high"},
                                  {"type": "sell_side_liquidity", "price": 90.0, "status": "resting", "source": "PDL"}]},
            "price_action": {"levels": [{"price": 103.0, "type": "resistance", "touches": 3},
                                        {"price": 94.0, "type": "support", "touches": 4}]},
            "candidates": [], "as_of": 1_700_000_000_000, "bars_used": 500,
            "as_of_close_time": int(__import__("time").time() * 1000) - 60_000}


def test_levels_offer_structure_and_atr_stops_and_a_2r_or_better_target():
    lv = tp.levels(_snap(), "LONG")
    keys = {s["key"] for s in lv["stop_options"]}
    assert {"structure", "atr"} <= keys
    assert lv["default"]["stop"] == 95.5   # swing low 96 minus a quarter ATR, 2.25 ATR away -> structural pick
    assert lv["default"]["target"] == 112.0  # 103 is under 2R (0.67R); the liquidity at 112 is 2.67R
    sh = tp.levels(_snap(), "SHORT")
    assert sh["default"]["stop"] == 104.5 and sh["default"]["target"] < 100


# ------------------------------------------------------------------------------------------- shorts in my trades

def test_short_trade_pnl_partial_and_plan_state():
    t = MyTrade(symbol="SOLUSDT", venue="futures", side="SHORT", leverage=3.0, entry=100.0, qty=1.0, stop=104.0,
                target=92.0, risk_usd=4.1, budget_usd=100.0, mode="growth", partial_done=False, status="open",
                source="system", opened_at=__import__("datetime").datetime(2024, 6, 1, tzinfo=__import__("datetime").UTC))
    assert mt.unrealized(t, 95.0) > 4.8 and mt.unrealized(t, 105.0) < -5.0
    c = mt.card(t, 95.0, mt.plan_state(t, None))
    assert "4.0% above entry" in mt.plan_state(t, None)["text"]
    assert c["pnl_pct"] == 5.0 and c["liquidation"] > 100 and c["open_risk_usd"] == 4.0
    import pandas as pd

    idx = pd.date_range("2024-05-30", periods=6, freq="D", tz="UTC")
    f = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 2.0}, index=idx)
    f.loc[pd.Timestamp("2024-06-02", tz="UTC"), "low"] = 91.0
    s = mt.plan_state(t, f)
    assert s["action"] == "partial" and "buy back a third" in s["text"]
    f.loc[pd.Timestamp("2024-06-03", tz="UTC"), "high"] = 104.5
    assert mt.plan_state(t, f)["action"] == "close"


@pytest.mark.parametrize("body,msg", [
    ({"side": "SHORT", "venue": "spot", "entry": 100, "qty": 1, "stop": 104}, "needs futures"),
    ({"side": "SHORT", "venue": "futures", "entry": 100, "qty": 1, "stop": 96}, "above the entry"),
    ({"side": "LONG", "venue": "futures", "entry": 100, "qty": 1, "stop": 96, "target": 90}, "above the entry"),
])
async def test_add_validates_side(body, msg):
    with pytest.raises(ValueError, match=msg):
        await mt.add(None, None, {"symbol": "SOLUSDT", **body})


# ------------------------------------------------------------------------------------------- knowledge

def test_knowledge_search_finds_definitions_and_research():
    r = knowledge.search("order block mitigation", 3)
    assert r["results"] and any("rder block" in x["section"] or "Order block" in x["text"] for x in r["results"])
    assert knowledge.search("liquidation leverage 20x", 3)["results"]
    assert knowledge.topics()["sections"] > 50


# ------------------------------------------------------------------------------------------- the desk

def _walk(price=100.0):
    steps = [{"key": k, "title": k.title(), "observation": f"{k} observation.", "bullets": [f"{k} detail"],
              "verdict_word": "bullish"} for k in ("big_picture", "regime", "structure", "levels", "liquidity",
                                                    "derivatives", "news", "scenarios")]
    return {"symbol": "SOLUSDT", "timeframe": "4h", "price": price, "regime": "trend_up", "steps": steps,
            "synthesis": {"direction": "bullish", "confidence": "medium", "agreement": 0.7, "supporting": [],
                          "conflicting": [], "story": ["up"]}}


def _patch_data(monkeypatch, prov="openai", snap=None):
    import tempfile
    from pathlib import Path

    from app.agents import journal

    base = snap or (_snap() | {"decision": {"action": "NO_TRADE", "reasons": ["x"]}})

    async def fake_walk(self, tf):
        self.walks.setdefault(tf, (_walk(), base))
        return self.walks[tf]

    async def filters(sym):
        return fut.Filters(0.01, 0.01, 0.01, 5.0)

    async def price(sym, fb):
        return fb

    monkeypatch.setattr(research_desk.Ctx, "walk", fake_walk)
    monkeypatch.setattr(research_desk, "filters_for", filters)
    monkeypatch.setattr(research_desk, "live_price", price)
    monkeypatch.setattr(research_desk, "provider", lambda: prov)
    monkeypatch.setattr(research_desk, "note_success", lambda: None)
    monkeypatch.setattr(research_desk, "note_failure", lambda e: None)
    monkeypatch.setattr(journal, "PATH", Path(tempfile.mkdtemp()) / "journal.jsonl")
    import app.api.chat as chat

    async def no_schools(sym, tf):  # keep the tests off the real candle database
        return []

    monkeypatch.setattr(chat, "_schools", no_schools)
    from app.agents import fundamentals

    async def fund(sym):  # keep the tests off CoinGecko and DefiLlama
        return FUND | {"symbol": sym}

    async def macro():
        return {"btc_dominance_pct": 59.8, "stablecoin_supply_change_30d_pct": -4.8, "top100_up_30d_pct": 18.0,
                "unavailable": []}

    monkeypatch.setattr(fundamentals, "coin", fund)
    monkeypatch.setattr(fundamentals, "macro", macro)
    from app.agents import readiness

    async def ready(sym, tf, side=None, venue="futures"):  # the gate passes unless a test says otherwise
        return {"symbol": sym, "tf": tf, "verdict": "ready", "score": 80, "side": "LONG", "parts": [], "blocks": [],
                "wait_for": [], "summary": "ready"}

    monkeypatch.setattr(readiness, "check", ready)


FUND = {"symbol": "SOLUSDT", "base": "SOL", "name": "Solana", "unavailable": [],
        "tokenomics": {"market_cap_usd": 49e9, "fdv_usd": 147e9, "fdv_to_market_cap": 3.0},
        "standing": {"market_cap_rank": 7, "volume_24h_usd": 3.1e9, "volume_to_market_cap_pct": 6.33,
                     "vs_btc_pct_points": {"30d": -14.0}},
        "usage": {"kind": "chain", "tvl_usd": 5.05e9, "market_cap_to_annual_fees": 233.7}}


async def _wait(v):
    for _ in range(300):
        v = research_desk.session_view(v["id"])
        if v["status"] != "running":
            return v
        await asyncio.sleep(0.02)
    return v


def _by(v, aid):
    return next(a for a in v["agents"] if a["id"] == aid)


async def test_without_ai_the_desk_says_unavailable_and_shows_nothing_in_its_place(monkeypatch):
    _patch_data(monkeypatch, prov=None)
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True))
    assert v["status"] == "unavailable" and "No AI key" in v["error"]
    assert v["decision"] is None and v["plan"] is None and v["events"] == []
    assert all(a["status"] == "skipped" and a["text"] is None for a in v["agents"])
    assert "rules" not in {a["id"] for a in v["agents"]}


async def test_add_trade_plan_comes_from_the_ai_trade_planner(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0))
    r = await research_desk.quick_plan("sol", "auto", "4h", 100.0, "growth", "futures", margin=20.0)
    assert r["by"] == "Pilot · Trade Planner" and r["symbol"] == "SOLUSDT" and r["plan"]["ok"] and r["plan"]["sizing"] == "margin"
    assert [t["name"] for t in r["tools_used"]][-2:] == ["key_levels", "trade_plan"] and r["opinion"]
    assert r["validation"]["groups"]


async def test_add_trade_plan_without_ai_is_unavailable(monkeypatch):
    _patch_data(monkeypatch, prov=None)
    with pytest.raises(research_desk.AIUnavailable, match="No AI key"):
        await research_desk.quick_plan("SOL", "auto", "4h", 100.0, "growth", "futures", margin=20.0)


def _scripted(target_r: float, decision: str = "LONG", invent: bool = False):
    """A fake LLM agent: really calls the platform tools it was given (with the live callback), then answers."""
    import json

    from app.agents.toolkit import AgentRun, call_tool

    async def run(system, task, tools, max_steps=6, max_tokens=2500, on_tool=None):
        name = system.split(" — ")[0].split("the desk's ")[-1]  # the agent's title
        used: list[dict] = []
        names = {t.name for t in tools}
        for n, args in (("validation_report", {}), ("chart_analysis", {"timeframe": "1d"}), ("tokenomics", {}),
                        ("search_knowledge", {"query": "market structure"})):
            if n in names:
                await call_tool(tools, n, args, used, on_tool)
        text = f"{name} analysis."
        if invent and name == "Chief Technical Strategist":
            text = "Fixed after review." if "could not trace" in task else "Target 4321.5 looks likely."
        data = {"text": text, "view": "bullish", "confidence": 0.7, "key_points": ["a"]}
        if name in ("Risk Officer", "Trade Planner") and "key_levels" in names:
            lv = json.loads(await call_tool(tools, "key_levels", {"side": "LONG"}, used, on_tool))
            stop = lv["default"]["stop"]
            await call_tool(tools, "trade_plan", {"side": "LONG", "stop": stop,
                                                  "target": 100 + target_r * (100 - stop)}, used, on_tool)
        if name == "Head Trader":
            data = {"text": "We go.", "decision": decision, "view": "bullish", "confidence": 0.66,
                    "conditions": ["holds above 96"], "invalidation": "a close below 95.5"}
        return AgentRun(json.dumps(data), data, used, 2, "fake")
    return run


async def test_ai_desk_researches_debates_and_plans_with_live_cursor_events(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0))
    v = await _wait(research_desk.start("SOLUSDT", "1h", "auto", 100.0, "growth", "futures", refresh=True))
    assert v["status"] == "done" and v["brain"] == "ai" and v["error"] is None
    assert all(_by(v, a)["by"] == "ai" for a in research_desk.AI_IDS) and "rules" not in {a["id"] for a in v["agents"]}
    assert [t["name"] for t in _by(v, "risk")["tools_used"]] == ["search_knowledge", "key_levels", "trade_plan"]
    assert _by(v, "technician").get("rebuttal")            # the leading analyst answered the Skeptic
    assert v["decision"]["action"] == "LONG" and v["decision"]["by"] == "Apex · Head Trader" and v["validation"]["gate"] == "open"
    p = v["plan"]
    assert p["stop"] == 95.5 and p["target"] == 113.5 and p["ok"] and p["rr_net"] > 2
    ev = v["events"]
    assert any(e["agent"] == "researcher" and e.get("click") == "tf:1d" for e in ev)      # opened the 1d chart
    assert any(e["agent"] == "risk" and e["kind"] == "draw" and e["draw"]["stop"] == 95.5 for e in ev)
    assert any(e["agent"] == "head" and e.get("el") == "panel:validators" for e in ev)    # read the validators
    assert [e["i"] for e in ev] == list(range(len(ev))) and all(b["t"] >= a["t"] for a, b in zip(ev, ev[1:]))


async def test_invented_numbers_are_sent_back_and_revised(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0, invent=True))
    v = await _wait(research_desk.start("SOLUSDT", "1h", "auto", 100.0, "growth", "futures", refresh=True))
    t = _by(v, "technician")
    assert "4321.5" not in t["text"] and "revised after the fact-check" in t["revised"]
    assert "Vega" in _by(v, "validator")["text"]


async def test_guard_turns_a_weak_plan_into_wait(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(1.0))   # the Risk Manager plans a 1R target
    v = await _wait(research_desk.start("SOLUSDT", "1h", "long", 100.0, "growth", "futures", refresh=True))
    assert v["decision"]["action"] == "WAIT" and "Guard changed LONG to WAIT" in v["decision"]["guard"]
    assert v["plan"]["rr_net"] < 1.5


async def test_expired_key_makes_the_desk_unavailable(monkeypatch):
    _patch_data(monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("Error code: 401 - Your API key has expired. Please renew it.")

    monkeypatch.setattr(research_desk, "run_agent", boom)
    v = await _wait(research_desk.start("SOLUSDT", "15m", "auto", 100.0, "growth", "futures", refresh=True))
    assert v["status"] == "unavailable" and "expired" in v["error"]
    assert v["decision"] is None and v["plan"] is None
    assert all(_by(v, a)["status"] == "skipped" and not _by(v, a)["text"] for a in research_desk.AI_IDS)


async def test_every_lead_runs_its_specialists_first(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0))
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True))
    assert v["status"] == "done"
    for aid, specs in research_desk.SUBS.items():
        subs = _by(v, aid)["subs"]
        assert [x["id"] for x in subs] == [x["id"] for x in specs]
        assert all(x["status"] == "done" and x["text"] for x in subs), aid
    led = _by(v, "fundamental")
    assert led["name"] == "Ledger" and led["by"] == "ai" and led["title"] == "Fundamentals Lead"
    assert any(t["name"] == "tokenomics" for x in led["subs"] for t in x["tools_used"])
    labels = [e.get("label") or "" for e in v["events"] if e["agent"] == "fundamental"]
    assert any(lb.startswith("Mint: ") for lb in labels)                      # a specialist moves its lead's cursor
    names = {c["name"]: c for c in v["validation"]["checks"]}
    assert names["Fundamentals read"]["status"] == "pass"
    assert names["Supply overhang on a multi-day long"]["status"] == "warn"   # FDV 3x the market cap on a 4h long


async def test_specialists_can_be_switched_off(monkeypatch):
    from app.config import get_settings

    _patch_data(monkeypatch)
    monkeypatch.setattr(get_settings(), "desk_subagents", False)
    calls: list[str] = []
    fake = _scripted(3.0)

    async def counting(system, task, tools, **kw):
        calls.append(system.split(" — ")[0])
        return await fake(system, task, tools, **kw)

    monkeypatch.setattr(research_desk, "run_agent", counting)
    v = await _wait(research_desk.start("SOLUSDT", "1h", "auto", 100.0, "growth", "futures", refresh=True))
    assert v["status"] == "done" and all(not a["subs"] for a in v["agents"])
    assert len(calls) == len(research_desk.AI_IDS) + 1                        # the leads and the reply to the Red Team


def test_fundamental_checks_warn_but_never_block():
    from app.agents import validators as V

    class C:
        fund = FUND | {"standing": {"volume_24h_usd": 4e6, "volume_to_market_cap_pct": 0.4}}

    out = V.fundamental_checks({"tf": "1d"}, C(), "LONG")
    assert {c["name"]: c["status"] for c in out} == {"Supply overhang on a multi-day long": "warn", "Liquid market": "warn"}
    assert not any(c["critical"] for c in out)
    assert V.fundamental_checks({"tf": "15m"}, C(), "LONG")[0]["name"] == "Liquid market"  # dilution is not a scalp issue


async def test_fundamentals_derive_ratios_from_the_sources(monkeypatch):
    from app.agents import fundamentals as F

    F._cache.clear()
    row = {"id": "uniswap", "symbol": "uni", "name": "Uniswap", "market_cap": 4e9, "fully_diluted_valuation": 6e9,
           "circulating_supply": 600e6, "total_supply": 900e6, "max_supply": 1e9, "total_volume": 2e8, "current_price": 6.5,
           "market_cap_rank": 30, "ath": 44.9, "ath_change_percentage": -85.5, "ath_date": "2021-05-03T00:00:00Z",
           "price_change_percentage_30d_in_currency": -20.0}
    btc = {"id": "bitcoin", "symbol": "btc", "market_cap": 2e12, "price_change_percentage_30d_in_currency": -12.0}
    fake_uni = {"id": "x", "symbol": "uni", "market_cap": 1e6}   # a smaller coin with the same ticker

    async def get(url, params=None):
        if url.endswith("/coins/markets"):
            return [btc, row, fake_uni]
        if "/coins/uniswap" in url:
            return {"categories": ["Decentralized Exchange (DEX)"], "genesis_date": None}
        if url.endswith("/v2/chains"):
            return []
        if url.endswith("/protocols"):
            return [{"symbol": "UNI", "slug": "uniswap-v3", "parentProtocol": "parent#uniswap", "category": "Dexs",
                     "tvl": 2e9, "change_7d": -10.0},
                    {"symbol": "UNI", "slug": "uniswap-v2", "parentProtocol": "parent#uniswap", "category": "Dexs",
                     "tvl": 2e9, "change_7d": 0.0}]
        if "/summary/fees/uniswap" in url:
            return {"total30d": 60e6, "total1y": 900e6} if params["dataType"] == "dailyFees" else {"total30d": 6e6}
        raise AssertionError(url)

    monkeypatch.setattr(F, "_get", get)
    c = await F.coin("UNIUSDT")
    assert c["coingecko_id"] == "uniswap" and c["tokenomics"]["fdv_to_market_cap"] == 1.5
    assert c["tokenomics"]["circulating_pct_of_max_or_total"] == 60.0 and c["tokenomics"]["supply_cap"] == "fixed max supply"
    assert c["standing"]["vs_btc_pct_points"]["30d"] == -8.0 and c["standing"]["volume_to_market_cap_pct"] == 5.0
    u = c["usage"]
    assert u["kind"] == "protocol" and u["tvl_usd"] == 4e9 and u["tvl_change_7d_pct"] == -5.0 and u["market_cap_to_tvl"] == 1.0
    assert u["fees_annualized_from_30d_usd"] == 730e6 and u["market_cap_to_annual_fees"] == 5.5
    assert u["market_cap_to_annual_revenue"] == 54.8
    assert F.base_of("1000PEPEUSDT") == "PEPE" and F.base_of("BTCUSDT") == "BTC"
    F._cache.clear()


def test_journal_grades_stop_first_target_first_and_expiry():
    from app.agents import journal

    row = {"side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "rr": 1.9, "tf": "1h", "ts": 0}
    bars = [{"open_time": 3_600_000 * i, "high": 101.0, "low": 99.0, "close": 100.0} for i in range(1, 6)]
    assert journal.outcome(row, bars) is None                               # still open
    hit = [*bars, {"open_time": 3_600_000 * 6, "high": 111.0, "low": 99.0, "close": 110.0}]
    assert journal.outcome(row, hit) == {"outcome": "target", "r": 1.9, "closed_ts": 3_600_000 * 6}
    both = [*bars, {"open_time": 3_600_000 * 6, "high": 111.0, "low": 94.0, "close": 100.0}]
    assert journal.outcome(row, both)["outcome"] == "stop"                  # both in one candle: the worse first
    short = {**row, "side": "SHORT", "stop": 105.0, "target": 90.0}
    assert journal.outcome(short, [{"open_time": 3_600_000, "high": 106.0, "low": 99.0, "close": 104.0}])["r"] == -1.0
    flat = [{"open_time": 3_600_000 * i, "high": 101.0, "low": 99.0, "close": 102.5} for i in range(1, 80)]
    assert journal.outcome(row, flat) == {"outcome": "expired", "r": 0.5, "closed_ts": 3_600_000 * 72}


def test_leverage_options_show_the_cost_of_10x_and_20x():
    p = tp.build("SOLUSDT", "LONG", 100.0, 96.0, 110.0, 100.0, "growth", "futures", fut.Filters(0.01, 0.01, 0.01, 5.0))
    opts = {o["key"]: o for o in p["leverage_options"]}
    safe = opts["safe"]
    assert safe["leverage"] == p["leverage"] and safe["loss_at_stop_usd"] == p["risk_usd"] and safe["risk_level"] == "safe"
    # same size at 20x: liquidation 4% away = the 4% stop, so Binance closes it first and the whole margin is lost
    m20 = opts["min_margin_20"]
    assert m20["liquidated_before_stop"] and m20["risk_level"] == "extreme" and m20["margin_usd"] < safe["margin_usd"]
    assert m20["loss_at_stop_usd"] == m20["margin_usd"]       # isolated: never more than the margin
    m10 = opts["min_margin_10"]   # at 10x the liquidation (9%) is beyond the stop: same dollar loss as the safe plan
    assert not m10["liquidated_before_stop"] and m10["loss_at_stop_usd"] == safe["loss_at_stop_usd"]
    # same margin at 10x: 5x the size, so 5x the loss and the profit
    h10 = opts["high_return_10"]
    assert abs(h10["qty"] / safe["qty"] - 10 / safe["leverage"]) < 0.05
    assert h10["profit_at_target_usd"] > 4 * safe["profit_at_target_usd"] and h10["risk_level"] in ("risky", "very risky")
    assert tp.build("SOLUSDT", "LONG", 100, 96, 110, 100, "growth", "spot")["leverage_options"] == []


# ------------------------------------------------------------------------------------------- validators

def test_fact_check_traces_real_numbers_and_flags_invented_ones():
    from app.agents import validators as V

    cp = V.corpus({"price": 85.81, "stop": 89.11, "agreement": 0.69, "liq": 127.857}, "risk $1.99")
    ok = V.fact_check("Entry near 85.8, stop 89.11 (69% agree), liquidation 127.86, risk $1.99 on 4h; 3 methods.", cp)
    assert ok == {"checked": 5, "traced": 5, "unverified": []}   # 4h and '3 methods' are not facts to check
    bad = V.fact_check("Target 97.5 with a 74% win rate since 2020.", cp)
    assert bad["unverified"] == ["97.5", "74"] and bad["traced"] == 0


def test_trade_checks_fail_a_plan_below_2r_and_a_side_against_everything():
    from app.agents import validators as V

    class C:
        pass

    ctx = C()
    w = _walk()
    ctx.walks = {"4h": (w, _snap()), "1d": (w, _snap())}
    ctx.snap = _snap()
    ctx.price = 100.0
    s = {"tf": "4h", "mode": "growth", "budget": 100.0}
    weak = tp.build("SOLUSDT", "LONG", 100.0, 96.0, 104.0, 100.0, "growth")
    names = {c["name"]: c for c in V.trade_checks(s, ctx, weak, "LONG")}
    assert names["Reward/risk >= 2 after fees"]["critical"] and names["Side matches the chart read"]["status"] == "pass"
    short = tp.build("SOLUSDT", "SHORT", 100.0, 104.0, 90.0, 100.0, "growth")
    names = {c["name"]: c for c in V.trade_checks(s, ctx, short, "SHORT")}
    assert names["Not against the whole picture"]["critical"] and names["Shorts research"]["status"] == "warn"
    rep = V.report(list(names.values()))
    assert rep["gate"] == "blocked" and 0 <= rep["score"] < 100 and "Not against the whole picture" in rep["critical"]


async def test_validators_block_a_trade_on_stale_data(monkeypatch):
    _patch_data(monkeypatch)
    stale = _snap() | {"as_of_close_time": 1_700_000_000_000, "decision": {"action": "NO_TRADE", "reasons": []}}

    async def old_walk(self, tf):
        self.walks.setdefault(tf, (_walk(), stale))
        return self.walks[tf]

    monkeypatch.setattr(research_desk.Ctx, "walk", old_walk)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0))
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True))
    val = v["validation"]
    assert "Fresh candles" in val["critical"] and val["gate"] == "blocked"
    assert v["decision"]["action"] == "WAIT" and "validators failed" in v["decision"]["guard"]
    vag = next(a for a in v["agents"] if a["id"] == "validator")
    assert vag["by"] == "validators" and "Research quality" in vag["text"]
    assert any(f["name"] == "strategy_evidence" for f in val["filled"])   # the validator filled research gaps itself
    assert {g["group"] for g in val["groups"]} >= {"Data quality", "Research coverage", "Trade checks", "Consistency"}
    assert val["fact_check"]["head"] == {"checked": 0, "traced": 0, "unverified": []}   # every agent's text was checked


def test_failed_plan_keeps_its_coin_and_a_pegged_coin_is_blocked():
    from app.agents import validators as V

    bad = tp.build("UUSDT", "LONG", 1.0006, 1.0003, None, 100.0, "growth")
    assert not bad["ok"] and bad["symbol"] == "UUSDT" and bad["side"] == "LONG" and "stop" not in bad

    class C:
        pass

    ctx = C()
    ctx.snap = _snap(1.0) | {"indicators": {"atr14": 0.0004}}
    ctx.price = 1.0
    names = {c["name"]: c for c in V.data_quality({"tf": "1h"}, ctx)}
    assert names["Coin moves enough to trade"]["critical"]


# ------------------------------------------------------------------------------------------- margin sizing

def test_margin_sizing_picks_the_leverage_from_the_risk_style():
    f = fut.Filters(0.01, 0.01, 0.01, 5.0)
    for mode, share in tp.MARGIN_RISK.items():
        p = tp.build("SOLUSDT", "LONG", 100.0, 96.0, 110.0, 0, mode, "futures", f, margin=20.0)
        assert p["ok"] and p["sizing"] == "margin" and p["margin_input"] == 20.0
        assert p["risk_pct_of_margin"] <= share * 100 + 0.05          # the loss at the stop stays within the style
        assert 1 <= p["leverage"] <= p["leverage_safe_max"]
        nxt = (p["leverage"] + 1) * 20.0 * (4.0 + 100 * (2 * fut.TAKER + tp.SLIP)) / 100 / 20.0
        assert p["leverage"] == p["leverage_safe_max"] or nxt > share   # one more x would break the limit
        assert abs(p["margin_usd"] - 20.0) < 0.6 and p["reward_pct_of_margin"] > p["risk_pct_of_margin"]
    g = tp.build("SOLUSDT", "LONG", 100.0, 96.0, 110.0, 0, "growth", "futures", f, margin=20.0)
    opts = {o["key"]: o for o in g["leverage_options"]}
    assert opts["safe"]["leverage"] == g["leverage"] and opts["lev_20"]["loss_pct_of_budget"] > 40
    assert opts["lev_20"]["risk_level"] in ("very risky", "extreme") and opts["lev_1"]["risk_level"] == "safe"


def test_margin_sizing_on_spot_and_too_small_margin():
    wide = tp.build("SOLUSDT", "LONG", 100.0, 70.0, 160.0, 0, "conservative", "spot", None, margin=50.0)
    assert wide["ok"] and wide["leverage"] == 1 and wide["notional_usd"] < 50 and wide["notes"]   # only part used
    assert wide["risk_pct_of_margin"] <= 10.05
    small = tp.build("BTCUSDT", "LONG", 74000.0, 72000.0, 78000.0, 0, "growth", "futures",
                     fut.Filters(0.001, 0.001, 0.1, 50.0), margin=3.0)
    assert not small["ok"] and "Put at least $" in small["error"]


async def test_desk_sizes_from_the_margin(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0))
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True, margin=25.0))
    assert v["status"] == "done" and v["margin"] == 25.0
    p = v["plan"]
    assert p["sizing"] == "margin" and p["margin_input"] == 25.0 and p["risk_pct_of_margin"] <= 20.05
    names = {c["name"] for c in v["validation"]["checks"]}
    assert "Risk within the style" in names and "Risk within the mode" not in names



def test_a_liquidation_never_loses_more_than_the_margin():
    p = tp.build("SOLUSDT", "SHORT", 82.88, 86.41, 75.2, 0, "growth", "futures", fut.Filters(0.01, 0.01, 0.01, 5.0), margin=20.0)
    for o in p["leverage_options"]:
        assert o["loss_at_stop_usd"] <= o["margin_usd"] + 0.01 and o["loss_pct_of_budget"] <= 100.05
    o20 = next(o for o in p["leverage_options"] if o["leverage"] == 20)
    assert o20["liquidated_before_stop"] and o20["risk_level"] == "extreme"


def test_market_checks_catch_execution_risks(monkeypatch):
    import time as _t

    from app.agents import validators as V
    from app.quant import brain

    monkeypatch.setattr(brain, "peek", lambda: {"regime": {"btc_trend": "bear", "breadth_pct": 30.0}})

    class C:
        snap = _snap() | {"orderbook": {"spread_bps": 12.0},
                          "news": [{"title": "Exchange hacked", "impact": "high", "published_at": _t.time() * 1000 - 3_600_000},
                                   {"title": "Old news", "impact": "high", "published_at": _t.time() * 1000 - 4 * 86_400_000}]}

    plan = {"entry": 100.0, "stop": 90.6, "target": 120.0, "stop_pct": 9.8, "reward_usd": 10.0, "funding_usd_per_day": -0.5}
    got = {c["name"]: c for c in V.market_checks({"tf": "4h"}, C(), plan, "LONG")}
    assert got["With the market's tide"]["status"] == "warn"                     # a long in a risk-off market
    assert got["Clear path to the target"]["status"] == "warn" and "103" in got["Clear path to the target"]["detail"]
    assert got["Stop not parked on resting liquidity"]["status"] == "pass"      # 0.3 ATR beyond the pool at 90
    assert got["Funding cost over the hold"]["status"] == "warn"                 # $1.50 of a $10 target
    assert got["No fresh high-impact news"]["status"] == "warn" and "hacked" in got["No fresh high-impact news"]["detail"]
    assert got["Spread small against the stop"]["status"] == "pass"
    assert not any(c["critical"] for c in got.values())
    C.depth = {"buy_slippage_bps": 60.0, "sell_slippage_bps": 60.0}
    thin = {c["name"]: c for c in V.market_checks({"tf": "4h"}, C(), plan, "LONG")}
    assert thin["Book can take the size"]["status"] == "warn"                   # 120 bps round trip: 12% of the stop
    C.depth = None
    on_pool = {c["name"]: c for c in V.market_checks({"tf": "4h"}, C(), plan | {"stop": 90.1}, "LONG")}
    assert on_pool["Stop not parked on resting liquidity"]["status"] == "warn"


def test_consistency_catches_a_wrong_side_invalidation_and_a_split_desk():
    from app.agents import validators as V

    s = {"tf": "1h", "research": {"price": 100.0}, "agents": [
        {"id": "technician", "name": "Vega", "view": "bullish", "confidence": 0.7},
        {"id": "flow", "name": "Tide", "view": "bearish", "confidence": 0.65}]}
    d = {"action": "LONG", "confidence": 0.9, "invalidation": "a close above 104.5"}
    got = {c["name"]: c for c in V.consistency(s, d, {"checks": []}, {"entry": 100.0})}
    assert got["Invalidation on the right side"]["status"] == "fail"
    assert got["Desk not split"]["status"] == "warn"
    assert got["Confidence within the desk's cap"]["status"] == "warn"
    ok = {c["name"]: c for c in V.consistency(s, d | {"invalidation": "a close below 95.5"}, {"checks": []}, {"entry": 100.0})}
    assert ok["Invalidation on the right side"]["status"] == "pass"


def test_specialists_are_fact_checked_per_team():
    from app.agents import validators as V

    s = {"agents": [{"id": "fundamental", "name": "Ledger", "text": "No numbers here.",
                     "subs": [{"text": "FDV is 3.0x the market cap."}, {"text": "TVL fell 77.7% this week."}]}]}
    out, _ = V.fact_checks(s, V.corpus({"fdv_to_market_cap": 3.0}))
    team = next(c for c in out if c["name"] == "Ledger's team: numbers traced to data")
    assert team["status"] != "pass" and "77.7" in team["detail"] and not team["critical"]


async def test_the_ai_runs_only_when_the_free_gate_finds_a_setup(monkeypatch):
    from app.agents import readiness

    _patch_data(monkeypatch)
    calls = []

    async def fake(*a, **k):
        calls.append(1)
        raise AssertionError("the AI must not run")

    async def forming(sym, tf, side=None, venue="futures"):
        return {"symbol": sym, "tf": tf, "verdict": "forming", "score": 55, "side": "LONG", "parts": [], "blocks": [],
                "wait_for": ["a close above 103"], "summary": "forming"}

    monkeypatch.setattr(readiness, "check", forming)
    monkeypatch.setattr(research_desk, "run_agent", fake)
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True))
    assert v["status"] == "not_ready" and v["readiness"]["wait_for"] == ["a close above 103"] and not calls
    assert all(a["status"] == "skipped" for a in v["agents"]) and v["decision"] is None
    monkeypatch.setattr(research_desk, "run_agent", _scripted(3.0))                       # "Analyse anyway"
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True, force=True))
    assert v["status"] == "done" and v["readiness"] is None and v["decision"]


def test_readiness_scores_a_confirmed_aligned_setup_ready_and_blocks_the_wrong_side(monkeypatch):
    from app.agents import readiness
    from app.engine import engine
    from app.quant import brain

    monkeypatch.setattr(brain, "coin", lambda s: {"rank": 10, "of": 116, "trend": "up"})
    monkeypatch.setattr(engine, "research_gate", {"trend_pullback|4h": {"status": "active", "expectancy_r": 0.2, "n": 80}})
    cand = {"strategy": "trend_pullback", "direction": "LONG", "stage": "confirmed", "entry_low": 99.5, "entry_high": 100.5,
            "stop": 95.5, "targets": [112.0], "validation": {"passed": True, "quality_score": 80, "checks": []}}
    snap = _snap() | {"candidates": [cand], "regime": {"regime": "trend_up"},
                      "price_action": {"levels": [{"price": 112.0, "type": "resistance", "touches": 3}]}}
    walk = _walk()
    r = readiness.score("SOLUSDT", "4h", walk, snap, walk)
    assert r["verdict"] == "ready" and r["side"] == "LONG" and r["score"] >= readiness.READY and not r["blocks"]
    bear = {**walk, "synthesis": {**walk["synthesis"], "direction": "bearish"}}
    r2 = readiness.score("SOLUSDT", "4h", bear, snap, bear, want="LONG")
    assert r2["verdict"] == "not_now" and r2["blocks"] and any("4h" in w or "1d" in w for w in r2["wait_for"])
    r3 = readiness.score("SOLUSDT", "4h", walk, snap | {"candidates": [cand | {"stage": "forming",
                         "validation": {"passed": False, "quality_score": 40, "checks": []}}]}, walk)
    assert r3["verdict"] in ("forming", "not_now") and r3["wait_for"]


async def test_dashboard_lists_ready_coins_first(monkeypatch):
    from app.agents import fundamentals, market_extras, readiness, scout
    from app.api import agent as api
    from app.news import feeds
    from app.quant import brain

    api._DASH.clear()
    monkeypatch.setattr(scout, "scan_state", lambda tf: {"status": "done", "rows": [
        {"symbol": "AUSDT", "price": 1.0, "score": 50}, {"symbol": "BUSDT", "price": 2.0, "score": 40}]})
    verdicts = {"AUSDT": ("forming", 55), "BUSDT": ("ready", 80)}

    async def check(sym, tf, side=None, venue="futures"):
        v, sc = verdicts[sym]
        return {"verdict": v, "score": sc, "side": "LONG", "summary": v, "wait_for": [], "blocks": [],
                "parts": [{"name": "Engine setup", "points": 30, "of": 30, "detail": "x"}]}

    async def none(*a, **k):
        return {}

    async def fg():
        return {"value": 29, "label": "Fear"}

    async def q(*a, **k):
        return {"regime": {"label": "Risk-off: broad downtrend", "btc_trend": "bear", "breadth_pct": 30.0}}

    monkeypatch.setattr(readiness, "check", check)
    monkeypatch.setattr(fundamentals, "macro", none)
    monkeypatch.setattr(market_extras, "options_market", none)
    monkeypatch.setattr(feeds, "fear_greed", fg)
    monkeypatch.setattr(brain, "get", q)

    async def cap(sym, side, **k):
        return {"capacity_usd": 50_000.0}

    async def series():
        return {}

    monkeypatch.setattr(market_extras, "signal_capacity", cap)
    monkeypatch.setattr(api, "_series", series)
    api._HIST.clear()
    d = await api._board("4h")
    assert [c["symbol"] for c in d["coins"]] == ["BUSDT", "AUSDT"] and d["counts"] == {"ready": 1, "forming": 1, "not_now": 0}
    assert d["market"]["regime"].startswith("Risk-off") and d["market"]["fear_greed"]["value"] == 29
    assert d["coins"][0]["capacity_usd"] == 50_000.0

    # plans: Desk sees the live board; Starter sees it 15 minutes late and only its top coins
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "anonymous_plan", "starter")
    monkeypatch.setattr(get_settings(), "plans_enforced", True)  # packages are off by default; this tests them on

    class Admin:
        role, settings = "admin", {}

    live = await api.dashboard("4h", user=Admin())
    assert live["plan"]["key"] == "desk" and live["coins"][0]["symbol"] == "BUSDT" and not live["coins"][0].get("locked")
    starter = await api.dashboard("4h", user=None)
    assert starter["plan"]["key"] == "starter" and all(c.get("locked") for c in starter["coins"])  # nothing 15 min old yet
    api._HIST["4h"] = [(0.0, d["coins"])]                                                        # a board from long ago
    starter = await api.dashboard("4h", user=None)
    assert starter["coins"][0]["symbol"] == "BUSDT" and "15 minutes ago" in starter["plan"]["note"]
    with pytest.raises(Exception, match="1h needs Pro"):
        await api.dashboard("1h", user=None)


async def test_a_used_up_plan_stops_the_ai_but_not_the_free_gate(monkeypatch):
    _patch_data(monkeypatch)
    calls = []

    async def fake(*a, **k):
        calls.append(1)
        raise AssertionError("the AI must not run")

    monkeypatch.setattr(research_desk, "run_agent", fake)
    quota = {"key": "u1", "plan": "Starter", "limit": 2, "left": 0, "head_votes": 1, "enforced": True}
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 100.0, "growth", "futures", refresh=True, quota=quota))
    assert v["status"] == "quota" and "2 AI analyses a day" in v["error"] and not calls
    assert v["readiness"]["verdict"] == "ready"           # the free check still ran and is shown


async def test_the_risk_officer_chooses_the_risk_style_from_the_budget(monkeypatch):
    import json

    from app.agents.toolkit import AgentRun, call_tool

    _patch_data(monkeypatch)
    base = _scripted(3.0)

    async def run(system, task, tools, **kw):
        if "the desk's Risk Officer" in system and any(t.name == "trade_plan" for t in tools):
            used: list[dict] = []
            lv = json.loads(await call_tool(tools, "key_levels", {"side": "LONG"}, used))
            await call_tool(tools, "trade_plan", {"side": "LONG", "stop": lv["default"]["stop"], "target": 113.5,
                                                  "style": "growth"}, used)
            data = {"text": "Growth: the setup is validated.", "view": "bullish", "confidence": 0.7}
            return AgentRun(json.dumps(data), data, used, 2, "fake")
        return await base(system, task, tools, **kw)

    monkeypatch.setattr(research_desk, "run_agent", run)
    v = await _wait(research_desk.start("SOLUSDT", "4h", "auto", 1000.0, "auto", "futures", refresh=True))
    p = v["plan"]
    assert v["status"] == "done" and p["risk_style"] == "growth" and p["risk_style_by"].startswith("Sentinel")
    assert p["sizing"] == "budget" and p["risk_pct_of_budget"] <= 2.0 + 0.05       # growth: 2% of the $1,000 budget
    assert any(c["name"] == "Risk within the mode" and c["status"] == "pass" for c in v["validation"]["checks"])


def test_every_agent_tool_exists_and_the_quant_lead_has_its_team():
    from types import SimpleNamespace

    from app.agents import research_desk as rd

    tools = rd.build_tools(SimpleNamespace(symbol="BTCUSDT", s={"budget": 100, "tf": "4h"}, snap={}, plans={}))
    wanted = {t for a in rd.AGENTS for t in a["tools"]} | {t for v in rd.SUBS.values() for x in v for t in x["tools"]}
    assert wanted <= set(tools)
    assert "quant" in rd.ANALYSTS and {x["id"] for x in rd.SUBS["quant"]} == {"gauge", "prism", "mosaic"}
    assert {"tested_signal", "signal_test_record"} <= set(rd.BY_ID["head"]["tools"])
