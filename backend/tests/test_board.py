"""Signal board confidence: labels from the record of signals ranked like this one."""
from app.ml import board

CAL = {"buckets": [
    {"label": "#1", "lo": 1, "hi": 1, "unseen": {"n": 600, "win_rate": 0.4, "avg_r": 0.08},
     "by_side": {"LONG": {"n": 500, "win_rate": 0.33, "avg_r": -0.05}, "SHORT": {"n": 100, "win_rate": 0.5, "avg_r": 0.45}}},
    {"label": "#2–3", "lo": 2, "hi": 3, "unseen": {"n": 1000, "win_rate": 0.4, "avg_r": 0.05},
     "by_side": {"LONG": {"n": 900, "win_rate": 0.36, "avg_r": 0.03}, "SHORT": {"n": 400, "win_rate": 0.52, "avg_r": 0.5}}},
    {"label": "#4–10", "lo": 4, "hi": 10, "unseen": {"n": 4000, "win_rate": 0.41, "avg_r": 0.14}, "by_side": {}},
    {"label": "#11+", "lo": 11, "hi": 10_000, "unseen": {"n": 50000, "win_rate": 0.4, "avg_r": 0.01},
     "by_side": {"LONG": {"n": 29000, "avg_r": -0.22}, "SHORT": {"n": 28000, "avg_r": 0.25}}},
]}


def test_labels_and_the_machines_own_part():
    s = board.confidence(1, "SHORT", CAL)
    assert s["label"] == "High" and s["vs_rest_r"] == 0.2          # 0.45 - 0.25: the market's part removed
    lo = board.confidence(1, "LONG", CAL)
    assert lo["label"] == "Low" and lo["vs_rest_r"] == 0.17         # still better than other longs
    assert board.confidence(2, "LONG", CAL)["label"] == "Medium"


def test_thin_side_sample_falls_back_to_the_rank_bucket():
    c = board.confidence(1, "SHORT", {**CAL, "buckets": [{**CAL["buckets"][0], "by_side": {"SHORT": {"n": 10, "avg_r": 2.0}}},
                                                         *CAL["buckets"][1:]]})
    assert c["avg_r"] == 0.08 and "signals ranked #1" in c["basis"]
    assert board.confidence(5, "LONG", None)["label"] == "Unknown"


def test_no_side_is_claimed_when_the_models_own_forecast_is_negative():
    from app.ml import unified as un

    td = un.today()
    if not td:
        return
    for r in un.ranked()[:43]:
        p = un.plan_for(r["symbol"])
        if p.get("covered"):
            assert p["clear"] == (r["pred_r"] > 0 or p["status"] == "trade" or r["rank"] == 1)
            assert (p["unclear_note"] is None) == p["clear"]
