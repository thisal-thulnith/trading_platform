"""Meta-label filter (app/ml/meta_label.py) on small synthetic data: features, labels, live/history parity, the
walk-forward threshold rule and fail-closed serving. No cache files, no network."""
import json
import math
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml import meta_label as ml
from tests.conftest import make_ohlcv

H = 3_600_000


def _setup(df: pd.DataFrame, i: int, direction: str = "LONG") -> dict:
    """A synthetic engine setup on bar i in the shape generate_setups records it (+ snapshot_extras fields): a limit
    zone just behind the close, a stop one ATR beyond the zone, three structural targets."""
    c = float(df["close"].iloc[i])
    atr = float((df["high"] - df["low"]).iloc[max(0, i - 13): i + 1].mean())
    s = 1 if direction == "LONG" else -1
    near, far = c - s * 0.2 * atr, c - s * 0.6 * atr
    lo, hi = min(near, far), max(near, far)
    stop = (lo - atr) if s > 0 else (hi + atr)
    edge = hi if s > 0 else lo
    risk = abs(edge - stop)
    step = int(df["open_time"].iloc[1] - df["open_time"].iloc[0])
    return {"symbol": "TESTUSDT", "bar": i, "time": int(df["open_time"].iloc[i]) + step, "strategy": "trend_pullback",
            "direction": direction, "entry_low": lo, "entry_high": hi, "stop": stop,
            "targets": [edge + s * k * risk for k in (2.5, 3.5, 4.5)], "expiry_bars": 10, "hold_bars": 20,
            "regime": "weak_bull", "quality": 55.0, "bias": 0.3 * s, "mtf": {"4h": 1, "1d": 0},
            "levels": [c + 3 * atr, c - 3 * atr, c + 6 * atr], "n_support": 4, "n_oppose": 1}


def _same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b, rel=1e-9, abs=1e-12)


# ---------------------------------------------------------------- feature columns and setup_row

def test_feature_columns_f1_without_taker_imbalance():
    cols = ml.base_feature_columns(True)
    assert "taker_imb" not in cols and "atr_pct" in cols and "rel_strength_24" in cols
    assert not {"btc_ret_6", "btc_ret_24", "rel_strength_24"} & set(ml.base_feature_columns(False))
    f = ml.build_features(make_ohlcv(300), None).reindex(columns=cols)  # BTC columns exist (NaN) for BTC itself
    assert list(f.columns) == cols and f["btc_ret_6"].isna().all()
    fc = ml.feature_columns()
    assert len(fc) == len(set(fc)) and not set(ml.CONTEXT_FEATURES) & set(fc)
    assert {"dir", "quality", "stop_atr", "opp_level_r", "htf_agree", "funding_dir", "strat_trend_pullback"} <= set(fc)
    assert ml.FEATURE_VERSION == "f1" and ml.PROFILE == "edge_tp2r"


def test_setup_row_directional_copies_and_setup_features():
    df = make_ohlcv(400, seed=2)
    base = ml.build_features(df, None).reindex(columns=ml.base_feature_columns(True)).iloc[-1].to_dict()
    price = float(df.close.iloc[-1])
    lo = ml.setup_row(base, price, ml.apply_exit_profile(_setup(df, 399, "LONG"), ml.PROFILE))
    sh = ml.setup_row(base, price, ml.apply_exit_profile(_setup(df, 399, "SHORT"), ml.PROFILE))
    assert set(lo) == set(sh) == set(ml.feature_columns())
    assert lo["dir"] == 1.0 and sh["dir"] == -1.0
    for k in ("ret_24", "macd_hist_atr", "dist_ema50_atr"):  # aligned copies flip with the side; raw ones do not
        assert lo[k] == sh[k] and _same(lo[f"{k}_dir"], -sh[f"{k}_dir"])
    assert lo["rsi14_dir"] == pytest.approx(base["rsi14"] - 50)
    assert lo["strat_trend_pullback"] == 1.0 and sum(v for k, v in lo.items() if k.startswith("strat_")) == 1.0
    assert lo["regime_weak_bull"] == 1.0 and lo["regime_aligned"] == 1.0 and sh["regime_aligned"] == -1.0
    assert lo["htf_agree"] == pytest.approx(0.5) and sh["htf_agree"] == pytest.approx(-0.5)
    assert lo["bias_dir"] == pytest.approx(0.3) and sh["bias_dir"] == pytest.approx(0.3)
    atr = base["atr_pct"] * price
    s = ml.apply_exit_profile(_setup(df, 399, "LONG"), ml.PROFILE)
    assert lo["stop_atr"] == pytest.approx((s["entry_high"] - s["stop"]) / atr)
    assert lo["entry_gap_atr"] == pytest.approx((price - s["entry_high"]) / atr) and lo["entry_gap_atr"] > 0
    # the nearest level above the long entry (levels[0], 3 ATR above the close) caps the room
    assert lo["opp_level_r"] == pytest.approx((s["levels"][0] - s["entry_high"]) / (s["entry_high"] - s["stop"]))
    assert sh["opp_level_r"] > 0
    assert math.isnan(lo["funding_dir"])  # no funding history given


def test_setup_row_funding_signed_per_side_and_stale_ignored():
    df = make_ohlcv(400, seed=2)
    base = ml.build_features(df, None).reindex(columns=ml.base_feature_columns(True)).iloc[-1].to_dict()
    s = ml.apply_exit_profile(_setup(df, 399, "LONG"), ml.PROFILE)
    t = s["time"]
    times = t - np.arange(12)[::-1] * 8 * H  # one print every 8h up to t
    fund = pd.DataFrame({"time": times, "rate": [1e-4] * 11 + [4e-4]})
    lo = ml.setup_row(base, 1.0, s, fund)
    sh = ml.setup_row(base, 1.0, {**s, "direction": "SHORT"}, fund)
    assert lo["funding_dir"] == pytest.approx(4e-4) and sh["funding_dir"] == pytest.approx(-4e-4)
    assert lo["funding_3d_dir"] == pytest.approx((8 * 1e-4 + 4e-4) / 9)  # prints in (t - 3d, t]
    stale = pd.DataFrame({"time": times - 5 * 86_400_000, "rate": [1e-4] * 12})
    assert math.isnan(ml.setup_row(base, 1.0, s, stale)["funding_dir"])


def test_setup_row_context_optional_and_nan_safe():
    df = make_ohlcv(400, seed=4)
    base = ml.build_features(df, None).reindex(columns=ml.base_feature_columns(True)).iloc[-1].to_dict()
    s = ml.apply_exit_profile(_setup(df, 399, "SHORT"), ml.PROFILE)
    plain = ml.setup_row(base, 1.0, s)
    assert not set(ml.CONTEXT_FEATURES) & set(plain)
    ctx = ml.setup_row(base, 1.0, s, context={"btc_trend": 1.0, "xs_mom_pct": 0.25, "pending_long": 1,
                                             "pending_short": 3, "funding_pct_90d": float("nan")})
    assert ctx["btc_trend_dir"] == -1.0 and ctx["xs_mom_dir_pct"] == pytest.approx(0.75)
    assert ctx["pending_same_side"] == 3.0 and ctx["pending_opp_side"] == 1.0 and math.isnan(ctx["funding_pct_dir"])
    empty = ml.setup_row(base, 1.0, s, context={})
    assert all(math.isnan(empty[k]) for k in ml.CONTEXT_FEATURES)
    # a setup without snapshot extras still gives a row (NaN for what is unknown)
    bare = {k: v for k, v in s.items() if k not in ("mtf", "levels", "n_support", "n_oppose")}
    row = ml.setup_row(base, 1.0, bare)
    assert math.isnan(row["htf_agree"]) and math.isnan(row["opp_level_r"]) and math.isnan(row["n_support"])


# ---------------------------------------------------------------- labels

def _bars(rows, t0=1_700_000_000_000, step=4 * H) -> pd.DataFrame:
    return pd.DataFrame([{"open_time": t0 + i * step, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
                         for i, (o, h, lo, c) in enumerate(rows)])


LONG = {"bar": 0, "time": 1_700_000_000_000 + 4 * H, "strategy": "trend_pullback", "direction": "LONG",
        "entry_low": 99.0, "entry_high": 100.0, "stop": 98.0, "targets": [103, 104, 105], "expiry_bars": 3,
        "hold_bars": 10}


def test_label_target_before_stop_is_one_with_costs():
    df = _bars([(101, 101, 100.5, 101), (101, 101, 99.8, 100.5), (100.5, 104.5, 100.2, 104)])
    free = ml.label_setups(df, [LONG], 0.0, 0.0)[0]
    assert free["outcome"] == "TARGET_HIT" and free["label"] == 1 and free["r"] == pytest.approx(2.0)
    assert free["entry_low"] == free["entry_high"] == 100.0 and free["targets"] == [104.0] * 3  # edge_tp2r
    fee, slip = 0.0005, 2.0
    paid = ml.label_setups(df, [LONG], fee, slip)[0]
    fill = 100.0 * (1 + slip / 1e4)
    expect = -fill * fee / 2 + (104 - fill) / 2 - 104 * fee / 2
    assert paid["label"] == 1 and paid["r"] == pytest.approx(expect) and paid["r"] < 2.0


def test_label_stop_and_time_exit_are_zero_unfilled_has_no_label():
    stop = _bars([(101, 101, 100.5, 101), (101, 101, 99.8, 100.5), (100.5, 100.6, 97.5, 98)])
    s = ml.label_setups(stop, [LONG], 0.0005, 2.0)[0]
    assert s["outcome"] == "STOP_HIT" and s["label"] == 0 and s["r"] < -1.0
    drift = [(101, 101, 100.5, 101), (101, 101, 99.8, 100.5)] + [(100.5, 101, 99.5, 100.5)] * 30
    t = ml.label_setups(_bars(drift), [LONG], 0.0, 0.0)[0]  # max hold = max(3 * 10, 24) = 30 bars
    assert t["outcome"] == "TIME_EXIT" and t["label"] == 0 and t["r"] == pytest.approx(0.25)
    never = ml.label_setups(_bars([(101, 102, 100.5, 101)] * 6), [LONG], 0.0, 0.0)[0]
    assert never["outcome"] == "EXPIRED" and not never["filled"] and never["label"] is None and never["r"] is None
    open_end = ml.label_setups(_bars([(101, 101, 100.5, 101), (101, 101, 99.8, 100.5)]), [LONG], 0.0, 0.0)[0]
    assert open_end["filled"] and open_end["label"] is None


def test_dataset_rows_drop_unfilled_and_unresolved():
    df = make_ohlcv(400, seed=5, step_ms=4 * H)
    setups = [_setup(df, i, "LONG" if i % 2 else "SHORT") for i in range(260, 395, 3)]
    lab = ml.label_setups(df, setups, 0.0005, 2.0)
    base = ml.build_features(df, None).reindex(columns=ml.base_feature_columns(True))
    rows = ml.dataset_rows("TESTUSDT", df, lab, base, None)
    done = [t for t in lab if t["filled"] and t["label"] is not None]
    assert 0 < len(rows) == len(done) < len(lab)
    assert all(r["label"] in (0, 1) and r["t1"] >= r["time"] for r in rows)
    assert {r["label"] for r in rows} <= {0, 1}


# ---------------------------------------------------------------- live == history

def test_live_setup_features_match_setup_row_for_the_same_bar():
    from app.analysis.analyzer import build_snapshot
    from app.backtest.engine import resample_closed

    df = make_ohlcv(900, seed=8)
    win = df.tail(ml.WINDOW).reset_index(drop=True)
    snap = build_snapshot("AAA", "1h", win, {h: resample_closed(df, "1h", h).tail(200).reset_index(drop=True)
                                             for h in ml.HTF["1h"]}, light=True)
    s = _setup(df, len(df) - 1, "SHORT")
    cand = {k: s[k] for k in ("strategy", "direction", "entry_low", "entry_high", "stop", "targets", "expiry_bars")}
    cand |= {"expected_hold_bars": 20, "validation": {"quality_score": 61.0, "supporting_families": ["a", "b", "c"],
                                                      "opposing_families": ["d"]}}
    live = ml.live_setup_features(df, cand, snap, "1h")
    # history: the same setup as generate_setups records it (+ extras), with the exit profile, at that bar
    hist_setup = {**s, "quality": 61.0, "regime": snap["regime"]["regime"], "bias": snap["confluence"]["bias"],
                  **ml.snapshot_extras(snap, cand)}
    base = ml.build_features(df, None).reindex(columns=ml.base_feature_columns(True))
    hist = ml.setup_row(base.iloc[s["bar"]].to_dict(), float(df.close.iloc[s["bar"]]),
                        ml.apply_exit_profile(hist_setup, ml.PROFILE))
    assert set(live) == set(hist)
    assert all(_same(live[k], hist[k]) for k in hist), [k for k in hist if not _same(live[k], hist[k])]
    assert live["n_support"] == 3.0 and live["n_oppose"] == 1.0 and live["dir"] == -1.0


def test_snapshot_extras_shape_matches_cache():
    snap = {"mtf": {"1d": np.int64(-1)}, "price_action": {"levels": [{"price": 1.5}, {"price": 2.0}]}}
    ex = ml.snapshot_extras(snap, {"validation": {"supporting_families": ["x"] * 4, "opposing_families": []}})
    assert ex == {"mtf": {"1d": -1}, "levels": [1.5, 2.0], "n_support": 4, "n_oppose": 0}
    assert json.loads(json.dumps({"a": np.float64(1.5), "b": np.arange(2)}, default=ml._jsonable)) == \
        {"a": 1.5, "b": [0, 1]}
    assert len(ml.code_fingerprint()) == 16 and ml.HTF["4h"] == ["1d"] and ml.WINDOW == 300


# ---------------------------------------------------------------- walk-forward threshold

def test_threshold_maximises_total_r_with_min_keep():
    p = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    r = np.array([-1.0, -1.0, 2.0, -1.0, 2.0, 2.0])
    assert ml.choose_threshold(p, r, min_keep=1) == pytest.approx(0.3)  # p >= 0.3 keeps +5R, the most
    assert ml.choose_threshold(p, r, min_keep=5) == pytest.approx(0.2)  # at least 5 kept: +4R beats +3R
    assert ml.choose_threshold(p, -np.abs(r), min_keep=6) == pytest.approx(0.1)  # must keep all: take every setup
    assert ml.choose_threshold(p, np.r_[np.zeros(5), 1.0], min_keep=1) == pytest.approx(0.1)  # ties: more trades


def test_fold_k_threshold_uses_only_earlier_folds():
    rng = np.random.default_rng(0)
    preds = [(rng.uniform(0.2, 0.5, 80), rng.normal(0, 1, 80)) for _ in range(5)]
    thr = ml.walk_forward_thresholds(preds, min_keep=10)
    assert thr[0] is None  # fold 0 is validation only
    for k in range(1, 5):
        expect = ml.choose_threshold(np.concatenate([p for p, _ in preds[:k]]),
                                     np.concatenate([r for _, r in preds[:k]]), 10)
        assert thr[k] == expect
        # changing fold k and later folds never moves threshold k
        changed = preds[:k] + [(rng.uniform(0, 1, 80), rng.normal(5, 1, 80)) for _ in range(k, 5)]
        assert ml.walk_forward_thresholds(changed, min_keep=10)[k] == thr[k]


def _synthetic_ds(n=3000, signal=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = 1_600_000_000_000 + np.sort(rng.choice(20 * n, n, replace=False)) * 4 * H
    x = rng.normal(size=(n, 3))
    p = 1 / (1 + np.exp(-(signal * x[:, 0] - 0.6)))
    y = (rng.uniform(size=n) < p).astype(int)
    r = np.where(y == 1, 1.9, -1.05)
    return pd.DataFrame({"f0": x[:, 0], "f1": x[:, 1], "f2": x[:, 2], "label": y, "r": r, "time": t,
                         "t1": t + 10 * 4 * H, "symbol": "TESTUSDT"})


def test_research_walk_forward_report_and_thresholds():
    ds = _synthetic_ds(signal=1.5)
    rep = ml.research(ds, ["f0", "f1", "f2"], tf="4h", min_train=300, min_test=100)
    folds = rep["folds"]
    assert len(folds) == 5 and folds[0]["threshold"] is None and "n_kept" not in folds[0]
    assert all(f["threshold"] is not None for f in folds[1:])
    assert all(a["test_end"] < b["test_start"] for a, b in zip(folds, folds[1:]))
    assert all(f["n_cal"] == f["n_train"] - int(f["n_train"] * 0.8) for f in folds)  # last 20 % calibrates
    assert set(rep["criteria"]) == {"beats_base_rate_logloss_>=70pct_folds", "mean_auc_>=0.52",
                                    "beats_logistic_baseline_auc", "filtered_n_>=30",
                                    "filtered_expectancy_ci_low_>0", "filtered_beats_all_setups", "ece_<=0.03"}
    assert rep["summary"]["mean_auc_gbm"] > 0.6 and abs(rep["summary"]["mean_auc_permuted"] - 0.5) < 0.1
    assert rep["oos_all"]["n"] == sum(f["n_test"] for f in folds[1:])
    noise = ml.research(_synthetic_ds(signal=0.0, seed=3), ["f0", "f1", "f2"], min_train=300, min_test=100)
    assert noise["status"] == "rejected" and not all(noise["criteria"].values())


def test_research_embargo_and_purge():
    ds = _synthetic_ds(n=1500, signal=1.0)
    rep = ml.research(ds, ["f0", "f1", "f2"], tf="4h", embargo_bars=24, min_train=200, min_test=50)
    assert rep["folds"]
    for f in rep["folds"]:
        t0 = f["test_start"]
        ok = ds[(ds.time < t0 - 24 * 4 * H) & (ds.t1 < t0)]
        assert f["n_train"] == len(ok)  # 24-bar embargo, and every training label resolved before the test block


# ---------------------------------------------------------------- report and serving

def test_rejected_run_saves_no_model_and_score_returns_p_none(tmp_path):
    assert ml.score({"dir": 1.0}, tf="4h", root=tmp_path)["p"] is None  # no run at all
    rep = {"status": "rejected", "threshold": 0.3, "features": ["f0"],
           "criteria": {"mean_auc_>=0.52": True, "ece_<=0.03": False, "filtered_expectancy_ci_low_>0": False}}
    ml.save_run(rep, _synthetic_ds(n=200), "4h", tmp_path)
    assert not list(tmp_path.glob("*.pkl"))
    saved = json.loads((tmp_path / "meta_label_4h_latest.json").read_text())
    assert saved["artifact"] is None and saved["name"] == "meta_label_4h"
    out = ml.score({"dir": 1.0}, tf="4h", root=tmp_path)
    assert out["p"] is None and out["accepted_model"] is False and out["take"] is None
    assert out["failed_criteria"] == ["ece_<=0.03", "filtered_expectancy_ci_low_>0"]
    assert "ece_<=0.03" in out["basis"]
    assert ml.accepted_artifact("4h", tmp_path) is None


def test_accepted_model_is_saved_and_served(tmp_path):
    ds = _synthetic_ds(n=1500, signal=2.0)
    rep = {"status": "accepted", "threshold": 0.4, "features": ["f0", "f1", "f2"],
           "criteria": {"a": True, "b": True}}
    ml.save_run(rep, ds, "1h", tmp_path)
    art = ml.accepted_artifact("1h", tmp_path)
    assert art is not None and art["code_fingerprint"] == ml.code_fingerprint()
    hi = ml.score({"f0": 3.0, "f1": 0.0, "f2": 0.0}, tf="1h", root=tmp_path)
    lo = ml.score({"f0": -3.0, "f1": 0.0, "f2": 0.0}, tf="1h", root=tmp_path)
    assert hi["accepted_model"] and hi["p"] > lo["p"] and hi["take"] and not lo["take"]
    # a model built on other engine code is not served (fail-closed)
    path = Path(json.loads((tmp_path / "meta_label_1h_latest.json").read_text())["artifact"])
    bundle = pickle.loads(path.read_bytes())
    path.write_bytes(pickle.dumps({**bundle, "code_fingerprint": "0" * 16}))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    assert ml.score({"f0": 3.0}, tf="1h", root=tmp_path)["p"] is None
