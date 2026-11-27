"""Hypothesis-round features and filters (2026-11-11) on synthetic data: market context known at setup time, the
optional selector filters (off by default), re-labelled exits and the statistics. No cache files, no network."""
import math

import numpy as np
import pandas as pd
import pytest

from app.ml import meta_label as ml
from app.research import context as cx
from app.research import hypotheses as hy
from app.research import selector
from app.research.selector import (
    DEFAULT_PARAMS,
    NEUTRAL_PARAMS,
    clean_params,
    failed_filters,
    pick_daily,
    promoted_params,
    score_setup,
    selector_verdict,
)
from tests.conftest import make_ohlcv
from tests.test_research_selector import mk

DAY = cx.DAY_MS
H4 = 4 * 3_600_000


def daily(closes, start_day: int = 19_000) -> pd.DataFrame:
    return pd.DataFrame({"open_time": (start_day + np.arange(len(closes))) * DAY, "close": np.asarray(closes, float)})


# ---------------------------------------------------------------- context: daily panel

def test_btc_trend_up_down_mixed_and_missing():
    up = cx.DailyPanel({"BTCUSDT": daily(np.linspace(100, 300, 260))})
    t = (19_000 + 260) * DAY  # midnight after the last day: the last day is complete
    assert up.btc_trend(t) == 1.0
    down = cx.DailyPanel({"BTCUSDT": daily(np.linspace(300, 100, 260))})
    assert down.btc_trend(t) == -1.0
    # above the 200-day mean but below the 50-day mean -> mixed
    c = np.r_[np.linspace(100, 300, 230), np.linspace(300, 250, 30)]
    assert cx.DailyPanel({"BTCUSDT": daily(c)}).btc_trend(t) == 0.0
    # fewer than 200 closes, or the needed day missing (stale data) -> NaN
    assert math.isnan(cx.DailyPanel({"BTCUSDT": daily(np.linspace(100, 300, 150))}).btc_trend(
        (19_000 + 150) * DAY))
    assert math.isnan(up.btc_trend(t + 3 * DAY))
    assert math.isnan(cx.DailyPanel({}).btc_trend(t))


def test_daily_context_uses_only_completed_days():
    c = np.linspace(300, 100, 260)
    d = daily(c)
    t_mid = (19_000 + 259) * DAY + 8 * 3_600_000  # 08:00 on the last day: that day is not complete
    p1 = cx.DailyPanel({"BTCUSDT": d})
    spiked = d.copy()
    spiked.loc[len(spiked) - 1, "close"] = 10_000.0  # the running day spikes: must not be seen at t_mid
    p2 = cx.DailyPanel({"BTCUSDT": spiked})
    assert p1.btc_trend(t_mid) == p2.btc_trend(t_mid) == -1.0
    assert p2.btc_trend((19_000 + 260) * DAY) == 1.0  # once closed, it is used


def test_xs_momentum_percentile():
    n = 40
    panel = cx.DailyPanel({s: daily(100 * (1 + g) ** (np.arange(n) / 28)) for s, g in
                           (("AAA", 0.30), ("BBB", 0.10), ("CCC", -0.10), ("DDD", -0.30))})
    t = (19_000 + n) * DAY
    mom, pct, k = panel.xs_momentum("AAA", t)
    assert k == 4 and pct == pytest.approx(1.0) and mom == pytest.approx(0.30, rel=1e-6)
    assert panel.xs_momentum("DDD", t)[1] == pytest.approx(0.0)
    assert panel.xs_momentum("BBB", t)[1] == pytest.approx(2 / 3)
    # fewer than 29 days of history -> NaN
    assert math.isnan(panel.xs_momentum("AAA", (19_000 + 20) * DAY)[0])


def test_funding_percentile_trailing_90d():
    times = np.arange(300) * 8 * 3_600_000
    rates = np.full(300, 1e-4)
    rates[-1] = 5e-4
    f = pd.DataFrame({"time": times, "rate": rates})
    assert cx.funding_pct(f, int(times[-1]) + 1) == pytest.approx(1.0)
    rates2 = rates.copy()
    rates2[-1] = -5e-4
    assert cx.funding_pct(pd.DataFrame({"time": times, "rate": rates2}), int(times[-1]) + 1) == pytest.approx(0.0)
    # the print after t is not used; fewer than 30 prints -> NaN
    assert cx.funding_pct(f, int(times[-2]) + 1) == pytest.approx(0.5)
    assert math.isnan(cx.funding_pct(f.head(10), int(times[9]) + 1))
    assert math.isnan(cx.funding_pct(None, 0))


# ---------------------------------------------------------------- context: pending earlier setups

def _candles(lows, highs=None, opens=None, start=0, step=H4) -> pd.DataFrame:
    n = len(lows)
    highs = highs if highs is not None else [x + 10 for x in lows]
    opens = opens if opens is not None else [x + 5 for x in lows]
    return pd.DataFrame({"open_time": start + np.arange(n) * step, "open": opens, "high": highs, "low": lows,
                         "close": opens})


def test_pending_end_time_fill_invalidate_expire():
    # long, edge 100, stop 90: bars 0..; setup created at the close of bar 0 (time = open_time[1])
    lows = [105, 104, 103, 99, 101, 102]
    df = _candles(lows)
    s = {"time": H4, "direction": "LONG", "entry_low": 95, "entry_high": 100, "stop": 90, "expiry_bars": 10}
    tm, o, h, lo = (df[k].to_numpy(float) for k in ("open_time", "open", "high", "low"))
    assert cx.pending_end_time(s, tm.astype(np.int64), o, h, lo, H4) == 3 * H4 + H4  # filled on bar 3
    assert cx.pending_end_time({**s, "expiry_bars": 2}, tm.astype(np.int64), o, h, lo, H4) == 2 * H4 + H4
    # still waiting at the end of the data -> None (live: pending now)
    assert cx.pending_end_time(s, tm[:3].astype(np.int64), o[:3], h[:3], lo[:3], H4) is None
    # short: the stop traded before the entry -> invalidated on that bar
    s2 = {"time": H4, "direction": "SHORT", "entry_low": 120, "entry_high": 125, "stop": 130, "expiry_bars": 10}
    df2 = _candles([100, 100, 100], highs=[110, 112, 131], opens=[105, 105, 105])
    tm2, o2, h2, lo2 = (df2[k].to_numpy(float) for k in ("open_time", "open", "high", "low"))
    assert cx.pending_end_time(s2, tm2.astype(np.int64), o2, h2, lo2, H4) == 2 * H4 + H4


def test_pending_counts_strictly_earlier_and_by_side():
    earlier = [{"time": 1000, "direction": "LONG", "pending_end": None},
               {"time": 2000, "direction": "LONG", "pending_end": 5000},
               {"time": 2500, "direction": "LONG", "pending_end": 3000},   # ended at t=3000: not pending at 3000
               {"time": 2000, "direction": "SHORT", "pending_end": 9000},
               {"time": 3000, "direction": "LONG", "pending_end": None}]   # same time: not earlier
    assert cx.pending_counts(3000, earlier) == {"pending_long": 2.0, "pending_short": 1.0}


def test_live_context_matches_historical_pending():
    """Live (data up to t only) and history (all data, then counted at t) give the same pending counts."""
    from app.research.simulate import _pending_contexts

    df = make_ohlcv(80, seed=3, step_ms=H4)
    c = df["close"].to_numpy()
    setups = []
    for b in (40, 42, 44, 46, 50):
        e = float(c[b]) * 0.97  # limits 3% below: some fill, some wait
        setups.append({"bar": b, "time": int(df.open_time.iloc[b]) + H4, "direction": "LONG", "entry_low": e * 0.99,
                       "entry_high": e, "stop": e * 0.95, "expiry_bars": 10})
    hist = _pending_contexts(setups, df, H4)
    for s, h in zip(setups, hist):
        live_df = df.iloc[: s["bar"] + 1]
        live = cx.live_context("AAA", live_df, "4h", recent_setups=[x for x in setups if x["time"] < s["time"]])
        assert live["pending_long"] == h["pending_long"] and live["pending_short"] == h["pending_short"]
    assert max(h["pending_long"] for h in hist) >= 1  # the synthetic case actually exercises pending setups


# ---------------------------------------------------------------- meta_label: context features

def test_setup_row_context_features_directional_and_optional():
    from tests.test_meta_label import _setup

    df = make_ohlcv(400, seed=4)
    base = ml.build_features(df, None).reindex(columns=ml.base_feature_columns(True)).iloc[-1].to_dict()
    ctx = {"btc_trend": 1.0, "mom_28d": 0.2, "xs_mom_pct": 0.8, "funding_pct_90d": 0.95, "pending_long": 2.0,
           "pending_short": 0.0}
    s_long = ml.apply_exit_profile(_setup(df, len(df) - 1, "LONG"), ml.PROFILE)
    s_short = ml.apply_exit_profile(_setup(df, len(df) - 1, "SHORT"), ml.PROFILE)
    plain = ml.setup_row(base, float(df.close.iloc[-1]), s_long)
    assert not set(ml.CONTEXT_FEATURES) & set(plain)  # no context -> the row (and the ML dataset) is unchanged
    lo = ml.setup_row(base, float(df.close.iloc[-1]), s_long, context=ctx)
    sh = ml.setup_row(base, float(df.close.iloc[-1]), s_short, context=ctx)
    assert set(lo) - set(plain) == set(ml.CONTEXT_FEATURES)
    assert lo["btc_trend_dir"] == 1.0 and sh["btc_trend_dir"] == -1.0
    assert lo["xs_mom_dir_pct"] == pytest.approx(0.8) and sh["xs_mom_dir_pct"] == pytest.approx(0.2)
    assert lo["funding_pct_dir"] == pytest.approx(0.95) and sh["funding_pct_dir"] == pytest.approx(0.05)
    assert lo["pending_same_side"] == 2.0 and sh["pending_same_side"] == 0.0 and sh["pending_opp_side"] == 2.0
    nan = ml.setup_row(base, float(df.close.iloc[-1]), s_long, context={"btc_trend": None})
    assert all(math.isnan(nan[k]) for k in ml.CONTEXT_FEATURES)


def test_live_setup_features_accepts_context_and_matches_history():
    from app.analysis.analyzer import build_snapshot
    from app.backtest.engine import resample_closed
    from tests.test_meta_label import _setup

    df = make_ohlcv(900, seed=8)
    win = df.tail(300).reset_index(drop=True)
    snap = build_snapshot("AAA", "1h", win, {"4h": resample_closed(df, "1h", "4h").tail(200).reset_index(drop=True)},
                          light=True)
    s = _setup(df, len(df) - 1)
    cand = {k: s[k] for k in ("strategy", "direction", "entry_low", "entry_high", "stop", "targets", "expiry_bars")}
    cand |= {"expected_hold_bars": 12, "validation": {"quality_score": 60.0}}
    ctx = {"btc_trend": -1.0, "xs_mom_pct": 0.1, "funding_pct_90d": 0.5, "pending_long": 3, "pending_short": 1}
    old = ml.live_setup_features(df, cand, snap, "1h")  # existing call: unchanged keys
    new = ml.live_setup_features(df, cand, snap, "1h", context=ctx)
    assert set(new) - set(old) == set(ml.CONTEXT_FEATURES)
    assert all(new[k] == old[k] or (math.isnan(new[k]) and math.isnan(old[k])) for k in old
               if isinstance(old[k], float))
    assert new["btc_trend_dir"] == -1.0 and new["pending_same_side"] == 3.0
    assert new["xs_mom_dir_pct"] == pytest.approx(0.1)


# ---------------------------------------------------------------- selector filters

def test_default_params_unchanged_by_context_and_filters_off():
    row = mk("A")
    ctx_row = {**row, "btc_trend_dir": -1.0, "xs_mom_dir_pct": 0.0, "funding_pct_dir": 1.0, "pending_same_side": 0.0}
    assert score_setup(ctx_row) == score_setup(row)
    assert failed_filters(ctx_row, DEFAULT_PARAMS) == []
    assert selector_verdict(row)["eligible"] is True
    assert DEFAULT_PARAMS["order"] == "score" and all(
        DEFAULT_PARAMS[k] in (0, 0.0, False, None) for k in selector.FILTER_KEYS)


@pytest.mark.parametrize("filt,good,bad", [
    ({"min_pending_same_side": 2}, {"pending_same_side": 2.0}, {"pending_same_side": 1.0}),
    ({"btc_trend_filter": True}, {"btc_trend_dir": 1.0}, {"btc_trend_dir": 0.0}),
    ({"min_xs_mom_dir_pct": 0.6667}, {"xs_mom_dir_pct": 2 / 3}, {"xs_mom_dir_pct": 0.5}),
    ({"max_funding_dir_pct": 0.9}, {"funding_pct_dir": 0.5}, {"funding_pct_dir": 0.9}),
    ({"stop_atr_band": [1.0, 3.0]}, {"stop_atr": 1.0}, {"stop_atr": 3.5}),
    ({"strategy_allow": ["trend_pullback"]}, {"strategy": "trend_pullback"}, {"strategy": "breakout_retest"}),
])
def test_each_filter_keeps_good_rejects_bad_and_missing(filt, good, bad):
    p = clean_params({**NEUTRAL_PARAMS, **filt})
    assert failed_filters(mk("A", **good), p) == []
    assert failed_filters(mk("A", **bad), p) == [next(iter(filt))]
    key = next(iter(good))
    if key != "strategy":  # missing / NaN feature -> rejected (fail-closed)
        missing = {k: v for k, v in mk("A").items() if k != key}
        assert failed_filters(missing, p) == [next(iter(filt))]
        assert failed_filters(mk("A", **{key: float("nan")}), p) == [next(iter(filt))]
    v_bad = selector_verdict(mk("A", **bad), p)
    assert not v_bad["eligible"] and v_bad["score"] <= -selector.FILTER_PENALTY
    assert pick_daily([mk("A", **bad), mk("B", **good)], p)[0]["symbol"] == "B"
    assert len(pick_daily([mk("A", **bad)], p)) == 0


def test_neutral_time_order_picks_earliest_distinct_symbols():
    rows = [mk("Z", hour=1), mk("A", hour=9), mk("M", hour=3), mk("Z", hour=5), mk("B", hour=2, direction="SHORT")]
    picks = pick_daily(rows, NEUTRAL_PARAMS, k=3)
    assert [p["symbol"] for p in picks] == ["Z", "B", "M"]
    # the default (score) order is unchanged: highest quality first
    rows2 = [mk("A", quality=50), mk("B", quality=70, hour=9)]
    assert pick_daily(rows2, DEFAULT_PARAMS)[0]["symbol"] == "B"
    assert pick_daily(rows2, NEUTRAL_PARAMS)[0]["symbol"] == "A"


def test_promoted_params_round_trip_with_filters(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(selector, "RESEARCH_DIR", tmp_path)
    params = clean_params({**NEUTRAL_PARAMS, "min_pending_same_side": 2, "strategy_allow": ["b", "a"]})
    (tmp_path / "selector_4h.json").write_text(json.dumps({"status": "promoted", "run_id": "hyp-x-H1",
                                                           "params": params}))
    pp = promoted_params("4h")
    assert pp["source"] == "promoted:hyp-x-H1"
    assert pp["min_pending_same_side"] == 2 and pp["order"] == "time" and pp["strategy_allow"] == ["a", "b"]
    assert promoted_params("1h")["source"] == "default"


# ---------------------------------------------------------------- re-labelled exits

def test_label_with_targets_partial_and_single():
    from app.research.simulate import label_with_targets

    t0 = 1_700_000_000_000
    bars = [(100, 101, 99, 100), (100, 100.5, 99.9, 100), (100, 101.6, 99.95, 101.5), (101.5, 102.2, 101, 102)]
    df = pd.DataFrame([{"open_time": t0 + i * H4, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
                       for i, (o, h, lo, c) in enumerate(bars)])
    s = {"bar": 0, "time": t0 + H4, "strategy": "trend_pullback", "direction": "LONG", "entry_low": 99.5,
         "entry_high": 100.0, "stop": 99.0, "targets": [103, 104, 105], "expiry_bars": 5, "hold_bars": 10}
    one = label_with_targets(df, [s], 0.0, 0.0, (1.5,))[0]
    assert one["outcome"] == "TARGET_HIT" and one["r"] == pytest.approx(1.5)
    two = label_with_targets(df, [s], 0.0, 0.0, (1.0, 2.0))[0]
    assert two["outcome"] == "TARGET_HIT" and two["r"] == pytest.approx(1.5)  # half at +1R, half at +2R
    base = label_with_targets(df, [s], 0.0, 0.0, None)[0]  # edge_tp2r
    assert base["outcome"] == "TARGET_HIT" and base["r"] == pytest.approx(2.0)


# ---------------------------------------------------------------- statistics

def test_holm_adjustment():
    adj = hy.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    assert adj["a"] == pytest.approx(0.04)
    assert adj["c"] == pytest.approx(0.09)
    assert adj["b"] == pytest.approx(0.09)  # monotone: max(0.09, 2 * 0.04 = 0.08)
    assert adj["d"] == pytest.approx(0.5)


def test_paired_bootstrap_detects_better_and_worse_books():
    rng = np.random.default_rng(1)
    base = [{"day": d, "r": float(rng.normal(0, 1))} for d in range(200) for _ in range(2)]
    better = [{"day": t["day"], "r": t["r"] + 0.5} for t in base]
    worse = [{"day": t["day"], "r": t["r"] - 0.5} for t in base]
    up = hy.paired_bootstrap(better, base, 0, 199, n_boot=2000)
    dn = hy.paired_bootstrap(worse, base, 0, 199, n_boot=2000)
    assert up["delta"] == pytest.approx(0.5) and up["p"] < 0.01
    assert dn["p"] > 0.99
    assert hy.paired_bootstrap([], base, 0, 199)["p"] == 1.0


def test_strategy_subset_needs_positive_mean_and_min_n():
    rows = ([{"strategy": "a", "filled": True, "r": 0.2}] * 60 + [{"strategy": "b", "filled": True, "r": -0.1}] * 60
            + [{"strategy": "c", "filled": True, "r": 1.0}] * 10 + [{"strategy": "a", "filled": False, "r": 0.0}] * 500)
    assert hy.strategy_subset(rows) == ["a"]


def test_hypothesis_params_are_neutral_plus_one_filter():
    for h, spec in hy.HYPOTHESES.items():
        p = hy.hypothesis_params(spec["filter"])
        changed = {k for k in selector.FILTER_KEYS if p[k] != NEUTRAL_PARAMS[k]}
        assert changed <= set(spec["filter"]), h
        assert p["order"] == "time" and p["w_quality"] == 0.0


# ---------------------------------------------------------------- the whole round on synthetic rows

def _synthetic_rows(seed: int, edge: float, cost: float = 0.0) -> list[dict]:
    """~500 days x 6 setups; setups with >= 2 pending same-side earlier setups win (+edge), the rest are noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(500):
        for j, sym in enumerate(("A", "B", "C", "D", "E", "F")):
            stacked = rng.random() < 0.3
            win = rng.random() < (0.36 + (edge if stacked else 0.0))
            r = (2.0 if win else -1.0) - cost
            rows.append(mk(sym, day=d, hour=1 + 3 * j, r=r, hold_h=20, pending_same_side=3.0 if stacked else 0.0,
                           btc_trend_dir=float(rng.choice([-1.0, 1.0])), xs_mom_dir_pct=float(rng.random()),
                           funding_pct_dir=float(rng.random()), stop_atr=float(rng.uniform(0.5, 3.5)),
                           strategy=("fvg_continuation", "trend_pullback")[j % 2]))
    return rows


def test_full_round_promotes_only_a_rule_that_passes_everything(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(selector, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(hy, "HOLDOUT_DIR", tmp_path / "holdout")
    monkeypatch.setattr(hy, "N_BOOT", 500)
    for name in ("newcoins", "early"):
        (tmp_path / "holdout" / name).mkdir(parents=True)
        (tmp_path / "holdout" / name / "4h_1d_X.json").write_text("{}")
    seeds = {"4h": 1, "1h": 2, "newcoins": 3, "early": 4}

    def rows(self, cost_mult=1.0, targets_r=None):
        return _synthetic_rows(seeds[self.name] + (10 if targets_r else 0), edge=0.25,
                               cost=0.05 * (cost_mult - 1))

    monkeypatch.setattr(hy.DataSet, "rows", rows)
    rec = hy.run(write=True)
    assert set(rec["results"]) >= {f"H{i}" for i in range(1, 9)}
    assert rec["decisions"]["H1"]["passed"] and rec["decision"].startswith("promoted H")
    for h in ("H7", "H8"):  # exits can never be promoted through the selector
        assert not rec["decisions"][h]["promotable"]
    log = [json.loads(x) for x in (tmp_path / hy.LOG_NAME).read_text().splitlines()]
    assert [x["kind"] for x in log] == ["registration", "result"]
    doc = json.loads((tmp_path / "selector_4h.json").read_text())
    assert doc["status"] == "promoted" and doc["run_id"].startswith("hyp-2026-11-11-H")
    pp = promoted_params("4h")
    assert pp["source"] == doc["run_id"].join(["promoted:", ""]) and pp["order"] == "time"
    assert "| H1 |" in hy.format_result(rec)


def test_full_round_promotes_nothing_on_noise(tmp_path, monkeypatch):
    monkeypatch.setattr(selector, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(hy, "HOLDOUT_DIR", tmp_path / "holdout")
    monkeypatch.setattr(hy, "N_BOOT", 500)
    seeds = {"4h": 5, "1h": 6}
    monkeypatch.setattr(hy.DataSet, "rows", lambda self, cost_mult=1.0, targets_r=None: _synthetic_rows(
        seeds.get(self.name, 7), edge=0.0, cost=0.05 * (cost_mult - 1)))
    rec = hy.run(write=True)
    assert rec["decision"] == "nothing promoted"
    assert all(not d["gate"]["G8_fresh_delta_>0_both"] for d in rec["decisions"].values())  # fresh sets missing
    assert not (tmp_path / "selector_4h.json").exists()


def test_recent_engine_setups_replays_only_the_previous_bars():
    df = make_ohlcv(400, seed=11, step_ms=H4, drift=0.002)
    out = cx.recent_engine_setups(df, "AAA", "4h", lookback_bars=6)
    t_now = int(df.open_time.iloc[-1]) + H4
    first_scanned = int(df.open_time.iloc[len(df) - 1 - 6]) + H4
    for s in out:
        assert first_scanned <= s["time"] < t_now
        assert {"direction", "entry_low", "entry_high", "stop", "expiry_bars"} <= set(s)
    ctx = cx.live_context("AAA", df, "4h", recent_setups=out)
    assert ctx["pending_long"] >= 0 and ctx["pending_short"] >= 0 and math.isnan(ctx["btc_trend"])
