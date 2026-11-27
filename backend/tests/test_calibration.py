"""Desk calibration: confidence buckets, the shrunken adjustment and lead weights, on synthetic journal rows."""
import tempfile
from pathlib import Path

from app.agents import calibration as C
from app.agents import journal


def _row(conf, outcome, side="LONG", decision=None, leads=None, r=None, status="closed", **kw):
    row = {"symbol": "SOLUSDT", "tf": "4h", "decision": decision or side, "side": side, "entry": 100.0,
           "stop": 96.0 if side == "LONG" else 104.0, "target": 110.0 if side == "LONG" else 90.0, "rr": 2.5,
           "status": status, "outcome": outcome if status == "closed" else None,
           "r": r if r is not None else (2.5 if outcome == "target" else -1.0 if outcome == "stop" else 0.3)}
    if conf is not None:
        row["confidence"] = conf
    if leads is not None:
        row["leads"] = leads
    return row | kw


def _calls(conf, wins, losses, **kw):
    return [_row(conf, "target", **kw) for _ in range(wins)] + [_row(conf, "stop", **kw) for _ in range(losses)]


def _patch(monkeypatch):
    monkeypatch.setattr(journal, "PATH", Path(tempfile.mkdtemp()) / "journal.jsonl")


def test_not_enough_graded_calls_keeps_the_raw_confidence(monkeypatch):
    _patch(monkeypatch)
    assert C.adjust(0.72) == {"raw": 0.72, "calibrated": 0.72, "n": 0, "graded": 0, "bucket": None, "weight": 0.0,
                              "basis": "not enough graded calls yet (0 of 10); raw confidence kept"}
    rows = _calls(0.75, 3, 2) + [_row(0.8, None, status="open"), _row(0.7, "expired")]
    a = C.adjust(0.75, rows)
    assert a["calibrated"] == 0.75 and a["n"] == 5 and a["basis"].startswith("not enough graded calls yet")
    rel = C.reliability(rows)
    assert rel["graded"] == 5 and rel["skipped"] == {"open": 1, "expired": 1, "no_confidence": 0}
    assert "Not enough graded calls" in rel["note"]
    assert C.reliability([])["brier"] is None and C.lead_skill([])["weights"] == {k: 1.0 for k in journal.LEADS}


def test_reliability_flags_a_calibrated_and_an_overconfident_bucket():
    good = _calls(0.65, 13, 7)      # predicted 0.65, actual 0.65
    bad = _calls(0.85, 4, 8)        # predicted 0.85, actual 0.33
    rel = C.reliability(good + bad + [_row(0.55, "target")])
    b = {x["range"]: x for x in rel["buckets"]}
    assert set(b) == {"0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8+"}       # "<0.5" only appears when used
    assert b["0.6-0.7"]["n"] == 20 and b["0.6-0.7"]["win_rate"] == 0.65
    assert b["0.6-0.7"]["calibrated"] and b["0.6-0.7"]["verdict"] == "calibrated"
    assert b["0.8+"]["n"] == 12 and not b["0.8+"]["calibrated"] and b["0.8+"]["verdict"] == "overconfident"
    assert b["0.8+"]["avg_r"] == round((4 * 2.5 - 8) / 12, 2)
    assert b["0.5-0.6"]["n"] == 1 and not b["0.5-0.6"]["calibrated"] and b["0.5-0.6"]["verdict"] == "too few calls"
    assert b["0.7-0.8"]["n"] == 0 and b["0.7-0.8"]["win_rate"] is None
    calls = [(0.65, 1)] * 13 + [(0.65, 0)] * 7 + [(0.85, 1)] * 4 + [(0.85, 0)] * 8 + [(0.55, 1)]
    assert rel["brier"] == round(sum((p - w) ** 2 for p, w in calls) / len(calls), 4)
    assert rel["graded"] == 33 and "0.8+" in rel["note"]


def test_adjust_shrinks_toward_the_monotone_record():
    rows = _calls(0.65, 13, 7) + _calls(0.85, 4, 8)
    hi = C.adjust(0.85, rows)
    # 0.8+ won 33% but 0.6-0.7 won 65%: the monotone fit pools them (17 of 32) and the 12 calls get weight 12/32
    fit = 17 / 32
    assert hi["bucket"] == "0.8+" and hi["n"] == 12 and hi["weight"] == round(12 / 32, 3)
    assert hi["calibrated"] == round(0.85 + 12 / 32 * (fit - 0.85), 3) and hi["calibrated"] < 0.85
    mid = C.adjust(0.62, rows)
    assert mid["calibrated"] == round(0.62 + 20 / 40 * (fit - 0.62), 3)
    empty = C.adjust(0.75, rows)                                    # nothing graded at 0.7-0.8 yet
    assert empty["calibrated"] == 0.75 and empty["n"] == 0 and "no graded calls" in empty["basis"]
    assert C.adjust(72, rows)["raw"] == 0.72                        # a percentage is read as 0-1
    # a well-calibrated record barely moves the confidence
    ok = C.adjust(0.65, _calls(0.65, 13, 7))
    assert ok["calibrated"] == 0.65


def test_wait_rows_are_counterfactuals_not_confidence_calls():
    rows = _calls(0.65, 6, 4)
    waits = [_row(0.9, "target", decision="WAIT"), _row(0.9, "stop", decision="WAIT"), _row(0.9, "target", decision="WAIT"),
             _row(0.9, "expired", decision="WAIT")]
    guarded = [_row(0.7, "stop", decision="WAIT", head_action="LONG")]   # the head said LONG, a guard made it WAIT
    rel = C.reliability(rows + waits + guarded)
    assert rel["graded"] == 11 and {b["range"]: b["n"] for b in rel["buckets"]}["0.8+"] == 0
    w = rel["wait_counterfactual"]
    assert w == {"calls": 4, "graded": 3, "plan_side_won": 2, "plan_side_lost": 1, "missed_winner_rate": 0.667,
                 "avg_r_if_taken": round((2.5 + 2.5 - 1) / 3, 2), "expired": 1, "meaning": w["meaning"]}


def test_lead_weights_rise_and_fall_with_the_record():
    right = {"technician": {"view": "bullish", "confidence": 0.7}, "flow": {"view": "bearish", "confidence": 0.6},
             "skeptic": {"view": "neutral", "confidence": 0.5}}
    # LONG at target: price went up -> technician right, flow wrong; SHORT at stop: price went up too
    rows = [_row(0.7, "target", leads=right) for _ in range(20)] + \
           [_row(0.7, "stop", side="SHORT", leads=right) for _ in range(10)]
    sk = C.lead_skill(rows)
    t, f = sk["leads"]["technician"], sk["leads"]["flow"]
    assert t == {"n": 30, "right": 30, "hit_rate": 1.0, "weight": 1.5}                     # 1 + 0.5*30/50*2 = 1.6 -> cap
    assert f["n"] == 30 and f["right"] == 0 and f["weight"] == round(1 - 0.5 * 30 / 50 * 2, 3)
    assert sk["leads"]["skeptic"] == {"n": 0, "right": 0, "hit_rate": None, "weight": 1.0}  # neutral is skipped
    assert sk["weights"]["head"] == 1.0 and sk["graded"] == 30
    # a short record moves the weight only a little; a bullish lead on a LONG that hit the stop was wrong
    few = C.lead_skill([_row(0.7, "stop", leads={"researcher": {"view": "bullish", "confidence": 0.8}})] * 2)
    assert few["leads"]["researcher"]["right"] == 0 and few["weights"]["researcher"] == round(1 - 0.5 * 2 / 22 * 2, 3)
    # long bad record hits the floor
    worst = C.lead_skill([_row(0.7, "stop", leads={"risk": {"view": "bullish"}})] * 200)
    assert worst["weights"]["risk"] == 0.25
    # WAIT counterfactuals count for the leads unless left out
    wait = [_row(0.6, "target", decision="WAIT", leads={"flow": {"view": "bullish"}})]
    assert C.lead_skill(wait)["leads"]["flow"]["n"] == 1 and C.lead_skill(wait, include_wait=False)["graded"] == 0


def test_old_rows_without_lead_views_or_confidence_are_skipped(monkeypatch):
    _patch(monkeypatch)
    old = [_row(None, "target") for _ in range(12)]                     # written before confidence and leads existed
    journal._save(old)
    rel = C.reliability()
    assert rel["graded"] == 0 and rel["skipped"]["no_confidence"] == 12
    assert C.adjust(0.7)["basis"].startswith("not enough graded calls yet")
    sk = C.lead_skill()
    assert sk["graded"] == 0 and all(w == 1.0 for w in sk["weights"].values())
    assert journal.stats()["all"]["graded"] == 12                       # the old summary still reads them


def test_record_keeps_the_head_confidence_and_each_leads_view(monkeypatch):
    _patch(monkeypatch)
    plan = {"ok": True, "symbol": "SOLUSDT", "side": "LONG", "entry": 100.0, "stop": 96.0, "target": 110.0,
            "rr_net": 2.4}
    agents = [{"id": "researcher", "view": "bullish", "confidence": 0.7},
              {"id": "technician", "view": "neutral", "confidence": 0.5},
              {"id": "flow", "view": None, "confidence": None},                 # no view: left out
              {"id": "validator", "view": "bullish", "confidence": 0.9},        # not an AI lead
              {"id": "head", "view": "bullish", "confidence": 0.68}]
    s = {"tf": "4h", "plan": plan, "agents": agents, "brain": "ai", "validation": {"score": 80},
         "decision": {"action": "WAIT", "confidence": 0.68, "guard": "Guard changed LONG to WAIT: x"}}
    row = journal.record(s)
    assert row["confidence"] == 0.68 and row["head_action"] == "LONG" and row["decision"] == "WAIT"
    assert row["leads"] == {"researcher": {"view": "bullish", "confidence": 0.7},
                            "technician": {"view": "neutral", "confidence": 0.5},
                            "head": {"view": "bullish", "confidence": 0.68}}
    assert journal.rows()[0]["leads"] == row["leads"] and journal.record(s) is None    # one entry per candle
