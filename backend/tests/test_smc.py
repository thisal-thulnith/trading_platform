import numpy as np
import pandas as pd

from app.analysis.price_action import find_pivots, zigzag
from app.analysis.smc import _ctx, analyze_smc, fair_value_gaps, market_structure
from tests.conftest import make_ohlcv

H = 3_600_000


def bars(rows):
    """rows: list of (o,h,l,c)."""
    n = len(rows)
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"open_time": np.arange(n) * H + 1_700_000_000_000 // H * H, "open": a[:, 0],
                         "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": np.ones(n) * 10,
                         "taker_buy_base": np.ones(n) * 5})


def test_pivot_confirmation_delay():
    df = make_ohlcv(200)
    for p in find_pivots(df, 3, 3):
        assert p.confirmed_idx == p.idx + 3
        if p.kind == "high":
            assert df["high"].iloc[p.idx] == df["high"].iloc[p.idx - 3 : p.idx + 4].max()


def test_zigzag_alternates():
    z = zigzag(make_ohlcv(400), 5, 5)
    assert all(a.kind != b.kind for a, b in zip(z, z[1:]))


def test_bullish_fvg_detection():
    rows = [(100, 101, 99, 100)] * 20 + [(100, 101, 99.5, 100.5), (100.5, 106, 100.4, 105.8),
                                          (105.8, 107, 102, 106.5), (106.5, 107, 105, 106)]
    df = bars(rows)
    gaps = [g for g in fair_value_gaps(_ctx(df)) if g["type"] == "fvg" and g["direction"] == "bullish"]
    assert gaps, "expected bullish FVG"
    g = gaps[-1]
    assert g["bottom"] == 101 and g["top"] == 102  # high[i-2] .. low[i]


def test_bos_then_choch():
    # up-swing to a high, pull back, break the high (BOS up), then dump below the last higher low (CHoCH)
    seq = []
    p = 100.0
    path = [1] * 8 + [-1] * 5 + [1] * 10 + [-1] * 4 + [1] * 3 + [-1] * 14
    for d in path:
        o = p
        p = p + d
        seq.append((o, max(o, p) + 0.2, min(o, p) - 0.2, p))
    df = bars(seq)
    x = _ctx(df)
    ms = market_structure(x, find_pivots(df, 2, 2), "internal")
    kinds = [(e["type"], e["direction"]) for e in ms["events"]]
    assert ("BOS", "bull") in kinds or ("CHoCH", "bull") in kinds
    assert ("CHoCH", "bear") in kinds
    assert ms["trend"] == "bearish"
    for e in ms["events"]:
        assert e["break_idx"] > e["swing_idx"] + 2  # break only after pivot confirmation


def test_structure_never_repaints():
    df = make_ohlcv(600, seed=3)
    full_df = df
    for cut in (300, 450, 599):
        part = df.iloc[:cut].reset_index(drop=True)
        for L in (3, 8):
            f = market_structure(_ctx(full_df), find_pivots(full_df, L, L), "x")["events"]
            p = market_structure(_ctx(part), find_pivots(part, L, L), "x")["events"]
            f_before = [e for e in f if e["break_idx"] < cut]
            assert [e["id"] for e in p] == [e["id"] for e in f_before]


def test_fvg_creation_never_repaints():
    df = make_ohlcv(500, seed=11)
    full = {g["id"] for g in fair_value_gaps(_ctx(df), max_keep=10_000) if g["type"] == "fvg"}
    part_df = df.iloc[:350].reset_index(drop=True)
    part = {g["id"] for g in fair_value_gaps(_ctx(part_df), max_keep=10_000) if g["type"] == "fvg"}
    assert part <= full


def test_analyze_smc_shape():
    full = analyze_smc(make_ohlcv(500, seed=3), H)
    assert full["premium_discount"] is not None
    assert full["trend_external"] in ("bullish", "bearish", "undetermined")
    for ob in full["order_blocks"] + full["breakers"]:
        assert ob["top"] >= ob["bottom"]
        assert ob["status"] in ("active", "mitigated", "invalidated")
    for s in full["sweeps"]:
        assert s["kind"] in ("sweep", "run")
