import json

import pytest

from app.agents import daily


def _rows(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(daily, "PICKS", tmp_path / "picks.jsonl")
    monkeypatch.setattr(daily, "DIR", tmp_path)
    daily.PICKS.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_sandbox_compounds_closed_picks_at_their_risk(tmp_path, monkeypatch):
    base = {"tf": "4h", "side": "LONG", "entry": 100, "stop": 95, "target": 110, "rr": 2.0, "strategy": "x", "rank": 1,
            "readiness": 90, "selector_score": None}
    _rows(tmp_path, monkeypatch, [
        {**base, "day": "2026-11-01", "ts": 1, "symbol": "A", "style": "growth", "status": "closed", "outcome": "target", "r": 2.0, "closed_ts": 10},
        {**base, "day": "2026-11-02", "ts": 2, "symbol": "B", "style": "conservative", "status": "closed", "outcome": "stop", "r": -1.0, "closed_ts": 20},
        {**base, "day": "2026-11-03", "ts": 3, "symbol": "C", "style": "conservative", "status": "open", "outcome": None, "r": None, "closed_ts": None}])
    s = daily.sandbox(10_000)
    assert s["closed"] == 2 and s["open"] == 1 and s["win_rate"] == 0.5
    assert s["equity"] == pytest.approx(10_000 * 1.04 * 0.99, abs=0.01)      # +2R at 2%, then -1R at 1%
    assert s["max_drawdown_pct"] == pytest.approx(1.0, abs=0.01) and len(s["curve"]) == 3


def test_style_follows_the_evidence():
    strong = {"verdict": "ready", "score": 90, "parts": [{"name": "Higher timeframe", "points": 15}]}
    assert daily._style(strong) == "growth"
    assert daily._style({**strong, "score": 80}) == "conservative"
    assert daily._style({**strong, "parts": [{"name": "Higher timeframe", "points": 7}]}) == "conservative"
    assert daily._style({**strong, "verdict": "forming"}) == "conservative"


def test_record_never_duplicates_a_days_pick(tmp_path, monkeypatch):
    _rows(tmp_path, monkeypatch, [])
    day = {"day": "2026-11-11", "tf": "4h", "made_at": 5, "picks": [
        {"symbol": "XRPUSDT", "tf": "4h", "side": "SHORT", "entry": 1.05, "stop": 1.1, "target": 0.95, "rr": 2.0,
         "style": "growth", "strategy": "s", "rank": 1, "readiness": {"score": 95, "verdict": "ready"}, "selector_score": None}]}
    daily._record(day)
    daily._record(day)
    assert len(daily.rows()) == 1 and daily.rows()[0]["status"] == "open"


def test_picks_made_again_replace_pending_ones_but_never_a_published_trade(tmp_path, monkeypatch):
    _rows(tmp_path, monkeypatch, [])
    pick = {"tf": "4h", "side": "SHORT", "entry": 1.0, "stop": 1.1, "target": 0.8, "rr": 2.0, "style": "growth",
            "strategy": "s", "rank": 1, "selector_score": None}
    daily._record({"day": "2026-11-11", "tf": "4h", "made_at": 5, "picks": [
        {**pick, "symbol": "WAITUSDT", "readiness": {"score": 60, "verdict": "forming"}},
        {**pick, "symbol": "TAKENUSDT", "readiness": {"score": 90, "verdict": "ready"}}]})
    daily._record({"day": "2026-11-11", "tf": "4h", "made_at": 6, "picks": [
        {**pick, "symbol": "XRPUSDT", "readiness": {"score": 90, "verdict": "ready"}}]})
    assert sorted(r["symbol"] for r in daily.rows()) == ["TAKENUSDT", "XRPUSDT"]   # the pending one went, the trade stayed


def test_an_unpromoted_selector_never_ranks_live_picks():
    from app.research.selector import promoted_params

    if str(promoted_params("4h").get("source", "")).startswith("promoted"):
        pytest.skip("a selector has been promoted on this machine")
    assert daily._selector_source("4h").startswith("readiness score")


async def test_a_wait_for_trigger_pick_enters_only_when_it_triggers_and_lapses_otherwise(tmp_path, monkeypatch):
    import time as _t

    from app.agents import readiness

    _rows(tmp_path, monkeypatch, [])
    base = {"tf": "4h", "side": "SHORT", "entry": 1.0, "stop": 1.1, "target": 0.8, "rr": 2.0, "style": "conservative",
            "strategy": "s", "rank": 4, "selector_score": None}
    daily._record({"day": "2026-11-11", "tf": "4h", "made_at": int(_t.time() * 1000), "picks": [
        {**base, "symbol": "XLMUSDT", "readiness": {"score": 67, "verdict": "forming"}},
        {**base, "symbol": "XRPUSDT", "readiness": {"score": 95, "verdict": "ready"}}]})
    assert {r["symbol"]: r["status"] for r in daily.rows()} == {"XLMUSDT": "pending", "XRPUSDT": "open"}

    alerts = []

    async def ready(sym, tf, side=None, venue="futures"):
        return {"verdict": "ready", "side": "SHORT", "score": 80, "parts": [], "blocks": [], "wait_for": [], "summary": ""}

    async def cand(sym, tf):
        return {"side": "SHORT", "readiness": {"verdict": "ready", "score": 80, "parts": []}, "candidate": {}}

    async def validate(c, budget, style):
        return {"ok": True, "entry": 0.95, "stop": 1.0, "target": 0.85, "rr_net": 2.0}, {"critical": []}

    async def alert(title, body, key, symbol, tf, category="pre_signal"):
        alerts.append(title)

    monkeypatch.setattr(readiness, "check", ready)
    monkeypatch.setattr(daily, "_candidate", cand)
    monkeypatch.setattr(daily, "_validate", validate)
    monkeypatch.setattr(daily, "_alert", alert)
    from app.signal import fusion

    async def take(*a, **k):
        return {"decision": "take"}

    monkeypatch.setattr(fusion, "signal", take)
    assert await daily.watch_triggers() == 1
    x = next(r for r in daily.rows() if r["symbol"] == "XLMUSDT")
    assert x["status"] == "open" and x["entry"] == 0.95 and x["triggered"] and "take now" in alerts[0]

    # an old pending pick lapses without a trade
    rs = daily.rows()
    rs.append({**base, "symbol": "OLDUSDT", "day": "2026-11-01", "ts": 0, "readiness": 60, "status": "pending",
               "outcome": None, "r": None, "closed_ts": None})
    daily._save(rs)
    await daily.watch_triggers()
    old = next(r for r in daily.rows() if r["symbol"] == "OLDUSDT")
    assert old["status"] == "lapsed" and daily.sandbox()["lapsed"] == 1


def test_shadow_grades_a_15r_target_alongside_the_real_exit(tmp_path, monkeypatch):
    _rows(tmp_path, monkeypatch, [{"day": "d", "ts": 1, "symbol": "A", "tf": "4h", "side": "LONG", "entry": 100, "stop": 95,
                                   "target": 110, "rr": 2.0, "style": "conservative", "status": "closed", "outcome": "stop",
                                   "r": -1.0, "closed_ts": 9, "r_15": 1.5, "outcome_15": "target"}])
    sh = daily.sandbox()["shadow_15r"]
    assert sh["trades"] == 1 and sh["avg_r"] == 1.5 and sh["win_rate"] == 1.0
