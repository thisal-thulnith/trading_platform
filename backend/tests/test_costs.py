"""AI cost tracking: tokens priced per call, summed per feature and per desk run."""
from types import SimpleNamespace as NS

from app.agents import costs


def test_openai_call_priced_and_summed(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "LOG", tmp_path / "u.jsonl")
    r = NS(model="gpt-5.5", usage=NS(prompt_tokens=10_000, completion_tokens=2_000,
                                     prompt_tokens_details=NS(cached_tokens=4_000),
                                     completion_tokens_details=NS(reasoning_tokens=1_500)))
    costs.FEATURE.set("desk")
    costs.RUN.set("abc")
    costs.track_openai(r, "gpt-5.5")
    costs.track_openai(r, "gpt-5.5")
    # one call: 6k*5 + 4k*0.5 + 2k*30 per 1M = 0.03 + 0.002 + 0.06 = $0.092
    rc = costs.run_cost("abc")
    assert rc["calls"] == 2 and abs(rc["usd"] - 0.184) < 1e-9 and rc["output"] == 4_000
    s = costs.summary(3)
    assert s["today"]["calls"] == 2 and s["by_feature"][0]["feature"] == "desk"
    assert s["desk_runs"][0]["run"] == "abc" and abs(s["avg_desk_run_usd"] - 0.184) < 1e-9


def test_unknown_model_counts_tokens_without_a_price(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "LOG", tmp_path / "u.jsonl")
    costs.RUN.set(None)
    costs.track_anthropic(NS(model="mystery-1", usage=NS(input_tokens=100, output_tokens=50, cache_read_input_tokens=20,
                                                         cache_creation_input_tokens=0)), "mystery-1")
    s = costs.summary(1)
    assert s["today"]["calls"] == 1 and s["today"]["unpriced_calls"] == 1 and s["today"]["input"] == 120


def test_tracking_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "LOG", tmp_path / "u.jsonl")
    costs.track_openai(object(), "gpt-5.5")   # no usage: ignored
    costs.track_openai(NS(usage=NS(prompt_tokens=None)), "gpt-5.5")  # broken usage: logged, not raised


def test_credit_left_counts_only_spend_after_it_was_set(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "LOG", tmp_path / "u.jsonl")
    monkeypatch.setattr(costs, "CREDIT", tmp_path / "c.json")
    r = NS(model="gpt-5.5", usage=NS(prompt_tokens=10_000, completion_tokens=2_000,
                                     prompt_tokens_details=NS(cached_tokens=4_000), completion_tokens_details=None))
    costs.RUN.set("r1")
    costs.track_openai(r, "gpt-5.5")          # before the balance was entered: not counted
    costs.set_credit(10.0)
    costs.track_openai(r, "gpt-5.5")          # $0.092
    c = costs.summary()["credit"]
    assert c["amount"] == 10.0 and abs(c["used"] - 0.092) < 1e-9 and abs(c["left"] - 9.908) < 1e-9
    assert c["desk_runs_left"] == int(9.908 // 0.184) and not c["low"]
    costs.set_credit(None)
    assert costs.summary()["credit"] is None
