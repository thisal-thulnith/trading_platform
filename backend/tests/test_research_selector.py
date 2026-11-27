"""Daily-5 selector, simulator and evolution gate on small synthetic rows (no cache files, no network)."""
import json
import math
import random

import pytest

from app.research import evolution, selector
from app.research.selector import DEFAULT_PARAMS, pick_daily, promoted_params, score_setup
from app.research.simulate import (
    DAY_MS,
    block_bootstrap_ci,
    quick_metrics,
    run_book,
    selector_picker,
    simulate_rows,
    take_all_picker,
)

H = 3_600_000
T0 = 20_000 * DAY_MS  # an arbitrary UTC midnight


def mk(symbol: str, day: int = 0, hour: int = 1, r: float = 1.0, hold_h: int = 24, filled: bool = True,
       direction: str = "LONG", strategy: str = "trend_pullback", **feats) -> dict:
    t = T0 + day * DAY_MS + hour * H
    row = {"quality": 55.0, "htf_agree": 1.0, "opp_level_r": 3.0, "stop_atr": 1.0, "regime_aligned": 1.0,
           "n_support": 4.0, "n_oppose": 0.0, "entry_gap_atr": 0.2, "funding_dir": 0.0}
    row.update(feats)
    row.update({"symbol": symbol, "strategy": strategy, "direction": direction, "time": t, "day": t // DAY_MS,
                "filled": filled, "r": r if filled else 0.0, "label": int(r > 1.5) if filled else None,
                "outcome": ("TARGET_HIT" if r > 1.5 else "STOP_HIT") if filled else "EXPIRED",
                "fill_time": t if filled else None, "exit_time": t + hold_h * H if filled else None,
                "release_time": t + hold_h * H})
    return row


@pytest.fixture
def research_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(selector, "RESEARCH_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------- score_setup

def test_score_ordering():
    base = mk("A")
    assert score_setup(mk("A", quality=70)) > score_setup(base) > score_setup(mk("A", quality=40))
    assert score_setup(mk("A", htf_agree=1.0)) > score_setup(mk("A", htf_agree=-1.0))
    assert score_setup(mk("A", opp_level_r=2.0)) > score_setup(mk("A", opp_level_r=0.2))
    # stop outside the [0.6, 2.5] ATR band is penalised on both sides
    assert score_setup(mk("A", stop_atr=0.3)) < score_setup(base) > score_setup(mk("A", stop_atr=4.0))
    assert score_setup(mk("A", regime_aligned=-1.0)) < score_setup(mk("A", regime_aligned=0.0)) < score_setup(base)
    assert score_setup(mk("A", entry_gap_atr=3.0)) < score_setup(base)
    assert score_setup(mk("A", funding_dir=0.001)) < score_setup(base)  # this side pays 10 bp


def test_score_neutral_on_missing_and_accepts_live_feature_dict():
    assert score_setup({}) == 0.0
    assert math.isfinite(score_setup({"quality": float("nan"), "htf_agree": None}))
    # live_setup_features() output: no "strategy"/"direction"/"symbol", but strat_* one-hots and "dir"
    explicit = mk("A", direction="SHORT", strategy="breakout_retest")
    live = {k: v for k, v in explicit.items() if k not in ("strategy", "direction", "symbol")}
    live.update({"dir": -1, **{f"strat_{s}": float(s == "breakout_retest") for s in selector.STRATEGY_IDS}})
    params = {**DEFAULT_PARAMS, "strategy_bias": {**DEFAULT_PARAMS["strategy_bias"], "breakout_retest": 0.7}}
    assert score_setup(live, params) == pytest.approx(score_setup(explicit, params))
    assert score_setup(live, params) == pytest.approx(score_setup(live) + 0.7)


# ---------------------------------------------------------------- pick_daily

def test_pick_daily_one_per_symbol_k_and_direction_limits():
    rows = [mk("A", quality=70), mk("A", quality=69, hour=5), mk("B", quality=65), mk("C", quality=60),
            mk("D", quality=58, direction="SHORT"), mk("E", quality=57, direction="SHORT"),
            mk("F", quality=56), mk("G", quality=30)]  # G is below min_quality 40
    picks = pick_daily(rows, DEFAULT_PARAMS, k=5)
    assert len(picks) == 5
    syms = [p["symbol"] for p in picks]
    assert len(set(syms)) == len(syms) and "G" not in syms
    assert picks[0]["symbol"] == "A" and picks[0]["quality"] == 70  # the better of A's two setups
    assert [p["selector_rank"] for p in picks] == [1, 2, 3, 4, 5]
    assert sum(p["direction"] == "LONG" for p in picks) <= DEFAULT_PARAMS["max_per_direction"]
    assert [p["selector_score"] for p in picks] == sorted((p["selector_score"] for p in picks), reverse=True)
    assert len(pick_daily(rows, DEFAULT_PARAMS, k=2)) == 2
    assert pick_daily([mk("G", quality=30)], DEFAULT_PARAMS) == []


def test_pick_daily_deterministic_ties():
    rows = [mk(s) for s in "EDCBA"]
    params = {**DEFAULT_PARAMS, "max_per_direction": 5}
    expect = [p["symbol"] for p in pick_daily(rows, params, k=3)]
    assert expect == ["A", "B", "C"]
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert [p["symbol"] for p in pick_daily(shuffled, params, k=3)] == expect


# ---------------------------------------------------------------- simulator

def test_book_position_limits_and_r_accounting():
    # 7 overlapping setups on day 0 (7 symbols), A again on day 0 while open, A on day 2 after release
    rows = [mk(s, hour=1 + i, r=2.0, hold_h=40) for i, s in enumerate("ABCDEFG")]
    rows += [mk("A", hour=12, r=-1.0), mk("A", day=2, hour=3, r=-1.0, hold_h=10)]
    book = run_book(rows, take_all_picker, max_open=5, risk=0.01)
    assert len(book["trades"]) == 6  # A..E on day 0, A again on day 2
    assert book["skipped"] == 3      # F, G (5 open) and the second A (symbol open)
    assert [t["symbol"] for t in book["trades"]] == ["A", "B", "C", "D", "E", "A"]
    m = quick_metrics(book)
    assert m["total_r"] == pytest.approx(5 * 2.0 - 1.0)
    # five +2R trades at 1% of 1.0 each, then -1R at 1% of 1.10
    assert book["curve"][-1] == pytest.approx(1.10 - 0.011)
    assert m["win_rate"] == pytest.approx(5 / 6) and m["hit_2r_rate"] == pytest.approx(5 / 6)


def test_book_compounds_on_realised_equity():
    rows = [mk("A", day=0, r=2.0, hold_h=2), mk("A", day=1, r=2.0, hold_h=2)]
    book = run_book(rows, take_all_picker, max_open=5, risk=0.01)
    assert book["trades"][1]["risk_amt"] == pytest.approx(0.0102)
    assert book["curve"][-1] == pytest.approx(1.02 * 1.02)


def test_unfilled_pick_blocks_symbol_until_expiry_and_is_not_a_trade():
    rows = [mk("A", hour=1, filled=False, hold_h=40), mk("A", hour=10, r=2.0), mk("A", day=2, hour=1, r=2.0)]
    book = run_book(rows, take_all_picker)
    assert book["unfilled"] == 1 and book["skipped"] == 1
    assert len(book["trades"]) == 1 and book["trades"][0]["day"] == T0 // DAY_MS + 2


def test_selector_book_respects_daily_k():
    rows = [mk(chr(65 + i), hour=1, r=1.0, hold_h=2, quality=50 + i) for i in range(10)]
    params = {**DEFAULT_PARAMS, "max_per_direction": 5}
    book = run_book(rows, selector_picker(params, 5), max_open=10)
    assert len(book["trades"]) == 5
    assert {t["symbol"] for t in book["trades"]} == {"F", "G", "H", "I", "J"}  # the 5 highest quality


def test_simulate_rows_outputs_and_block_bootstrap():
    rng = random.Random(1)
    rows = [mk(s, day=d, hour=rng.randint(0, 23), r=rng.choice([2.0, -1.0, -1.0]), hold_h=12,
               quality=rng.uniform(40, 70)) for d in range(60) for s in "ABCDEF"]
    res = simulate_rows(rows, DEFAULT_PARAMS, n_random=5)
    for key in ("selected", "take_all", "random_5", "drop_best_month", "settings"):
        assert key in res
    s = res["selected"]
    assert s["n_trades"] > 0 and len(s["equity_curve"]) >= 60 and s["monthly_returns"] and s["per_year"]
    lo, hi = s["expectancy_ci95"]
    assert lo <= s["expectancy_r"] <= hi
    assert res["take_all"]["n_trades"] >= s["n_trades"]
    assert block_bootstrap_ci([0, 1], [1.0, 2.0], 0, 1) is None  # < 5 trades


# ---------------------------------------------------------------- evolution + promotion

def _oos_reversal_rows() -> list[dict]:
    """Train (first 60%): high quality loses, low quality wins. OOS: the reverse. Anything the evolution learns on
    train is worse out of sample."""
    rng = random.Random(3)
    rows = []
    for d in range(200):
        for s in "ABCDEFGH":
            q = rng.uniform(40, 70)
            good = q < 55 if d < 120 else q >= 55
            rows.append(mk(s, day=d, hour=rng.randint(0, 20), quality=q, hold_h=6,
                           r=2.0 if (good and rng.random() < 0.6) else -1.0,
                           htf_agree=rng.choice([-1.0, 0.0, 1.0]), stop_atr=rng.uniform(0.4, 3.0)))
    return rows


def test_evolution_does_not_promote_when_oos_is_worse(research_dir):
    rows = _oos_reversal_rows()
    rows2 = [{**r, "r": r["r"] - 0.1 if r["filled"] else 0.0} for r in rows]
    rec = evolution.run("4h", pop_size=6, generations=2, seed=1, n_random=3, rows=rows, rows_cost_x2=rows2)
    assert rec["decision"] == "kept_incumbent"
    assert not all(rec["criteria"].values()) and rec["reason"].startswith("failed:")
    assert not selector.selector_path("4h").exists()
    lines = (research_dir / "evolution_4h.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["decision"] == "kept_incumbent"
    assert promoted_params("4h")["source"] == "default"
    # train uses only setups resolved before the cut; OOS starts at the cut
    assert rec["settings"]["n_train"] + rec["settings"]["n_oos"] <= len(rows)


def test_decide_gate():
    good = {"expectancy_r": 0.10, "expectancy_ci95": [0.02, 0.18], "n_trades": 200}
    stress = {"costs_x2": {"expectancy_r": 0.05}, "drop_best_month": {"expectancy_r": 0.06},
              "other_seed": {"beats_default_and_take_all_oos": True}}
    _, ok = evolution.decide(good, {"expectancy_r": 0.0}, {"expectancy_r": 0.01}, stress, False)
    assert ok
    worse = {**good, "expectancy_r": -0.01}
    crit, ok = evolution.decide(worse, {"expectancy_r": 0.0}, {"expectancy_r": 0.01}, stress, False)
    assert not ok and not crit["oos_beats_default_expectancy"] and not crit["oos_beats_take_all_expectancy"]
    _, ok = evolution.decide(good, {"expectancy_r": 0.0}, {"expectancy_r": 0.2}, stress, False)  # take-all better
    assert not ok
    _, ok = evolution.decide({**good, "expectancy_ci95": [-0.01, 0.2]}, {"expectancy_r": 0.0},
                             {"expectancy_r": 0.0}, stress, False)
    assert not ok
    _, ok = evolution.decide(good, {"expectancy_r": 0.0}, {"expectancy_r": 0.0},
                             {**stress, "costs_x2": {"expectancy_r": -0.01}}, False)
    assert not ok
    _, ok = evolution.decide(good, {"expectancy_r": 0.0}, {"expectancy_r": 0.0}, stress, True)  # is the default
    assert not ok


def test_promoted_params_falls_back_to_defaults(research_dir):
    p = promoted_params("4h")
    assert p["source"] == "default"
    assert {k: v for k, v in p.items() if k in DEFAULT_PARAMS} == DEFAULT_PARAMS
    selector.selector_path("4h").write_text(json.dumps({"status": "rejected", "params": {"w_quality": 9}}))
    assert promoted_params("4h")["source"] == "default"
    selector.selector_path("4h").write_text("{not json")
    assert promoted_params("4h")["source"] == "default"
    selector.selector_path("4h").write_text(json.dumps({"status": "promoted", "run_id": "r1",
                                                         "params": {"w_quality": 1.5, "max_per_direction": 4}}))
    p = promoted_params("4h")
    assert p["source"] == "promoted:r1" and p["w_quality"] == 1.5 and p["max_per_direction"] == 4
    assert p["w_htf"] == DEFAULT_PARAMS["w_htf"]
    assert promoted_params("1h")["source"] == "default"
