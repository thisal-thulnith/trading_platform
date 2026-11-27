"""Order-book recorder (app.quant.lob): storage, gaps, retention, budget and order-flow maths. No network."""
import gzip
import json

import numpy as np
import pandas as pd
import pytest

from app.quant.lob import features as F
from app.quant.lob import storage as st
from app.quant.lob.recorder import GapTracker, LobConfig, Recorder, SecondAggregator, depth_row, select_universe

H = st.HOUR_MS
T0 = 1_794_398_400_000  # 2026-11-11 12:00:00 UTC


def book_fields(bid=100.0, ask=100.1, bq=1.0, aq=1.0, src="w", u=1):
    bps = [f"{bid - 0.1 * i:.1f}" for i in range(20)]
    aps = [f"{ask + 0.1 * i:.1f}" for i in range(20)]
    return [u, src] + bps + [str(bq)] + ["1"] * 19 + aps + [str(aq)] + ["1"] * 19


def make_store(root, cap=10**12):
    budget = st.Budget(root, cap)
    return st.HourlyStore(root, budget), budget


def make_rec(tmp_path, clock, **kw):
    return Recorder(LobConfig(root=tmp_path, symbols=["BTCUSDT"], min_free_bytes=0, **kw), clock=clock)


# ---------------------------------------------------------------- storage: rotation, flush, compaction
def test_hourly_rotation_flush_and_manifest(tmp_path):
    store, _ = make_store(tmp_path)
    store.add("book", "BTCUSDT", T0 - 10_000, book_fields(u=1))     # 11:59:50 -> hour 11
    store.add("book", "BTCUSDT", T0, book_fields(u=2))              # 12:00:00 -> hour 12
    out = store.flush(T0 + 60_000)
    assert out["rows"] == 2 and out["refused"] == 0
    f11 = tmp_path / "book/BTCUSDT/2026-11-11/11.csv.gz"
    f12 = tmp_path / "book/BTCUSDT/2026-11-11/12.csv.gz"
    assert f11.exists() and f12.exists()
    assert store.pending_rows() == 0

    store.add("book", "BTCUSDT", T0 + 10_000, book_fields(u=3))     # second flush appends a gzip member
    store.flush(T0 + 120_000)
    raw = st.read_gz_tolerant(f12).decode()
    assert raw.count("ts,u,src") == 1 and raw.count("\n") == 3   # one header, two rows
    df = st.load("BTCUSDT", "book", T0 - H, T0 + H, tmp_path)
    assert list(df["u"]) == [1, 2, 3] and df["bp1"].iloc[0] == 100.0 and str(df["time"].dt.tz) == "UTC"
    assert len(st.load("btcusdt", "book", T0, T0 + 10_000, tmp_path)) == 1  # [start, end)

    man = json.loads((tmp_path / "_manifest/2026-11-11.json").read_text())
    assert man["book/BTCUSDT/2026-11-11/11.csv.gz"]["rows"] == 1
    assert man["book/BTCUSDT/2026-11-11/12.csv.gz"]["rows"] == 2
    assert man["book/BTCUSDT/2026-11-11/12.csv.gz"]["last_ts"] == T0 + 10_000

    # hour 11 is closed: compaction rewrites it as one member, rows unchanged; hour 12 is still open
    assert store.compact_closed(T0 + 200_000) >= 0
    assert json.loads((tmp_path / "_manifest/2026-11-11.json").read_text())[
        "book/BTCUSDT/2026-11-11/11.csv.gz"].get("compact")
    assert not st.Manifest(tmp_path).is_compact("book/BTCUSDT/2026-11-11/12.csv.gz")
    assert len(st.load("BTCUSDT", "book", T0 - H, T0 + H, tmp_path)) == 3


def test_reader_drops_truncated_tail(tmp_path):
    store, _ = make_store(tmp_path)
    for i in range(3):
        store.add("trades_raw", "ETHUSDT", T0 + i, [i, "10", "1", 0])
    store.flush(T0)
    p = tmp_path / "trades_raw/ETHUSDT/2026-11-11/12.csv.gz"
    partial = gzip.compress(b"999,9,10,1,0\n" * 50)[:-15]   # crash mid-flush
    with open(p, "ab") as fh:
        fh.write(partial)
    df = st.load("ETHUSDT", "trades_raw", T0, T0 + H, tmp_path)
    assert list(df["a"]) == [0, 1, 2]


def test_header_restored_when_first_member_lost(tmp_path):
    p = tmp_path / "trades_raw/ETHUSDT/2026-11-11/12.csv.gz"
    p.parent.mkdir(parents=True)
    p.write_bytes(gzip.compress(b"ts,a,pr")[:-4] + gzip.compress(f"{T0},5,10,1,1\n".encode()))
    # the broken first member stops decoding; a file whose *only* member is headerless still parses
    p.write_bytes(gzip.compress(f"{T0},5,10,1,1\n".encode()))
    df = st.load("ETHUSDT", "trades_raw", T0, T0 + H, tmp_path)
    assert list(df["a"]) == [5]


# ---------------------------------------------------------------- trades: per-second aggregation and id gaps
def test_second_aggregator_and_id_gap(tmp_path):
    now = [T0 + 10_000]
    rec = make_rec(tmp_path, lambda: now[0])
    rec.on_trade("BTCUSDT", 1, T0 + 100, 100.0, 2.0, False)   # taker buy
    rec.on_trade("BTCUSDT", 2, T0 + 900, 101.0, 1.0, True)    # taker sell
    rec.on_trade("BTCUSDT", 2, T0 + 950, 101.0, 1.0, True)    # duplicate id: ignored
    rec.on_trade("BTCUSDT", 5, T0 + 1500, 99.0, 3.0, True)    # ids 3-4 missing
    assert rec.c["trades_missed"] == 2 and rec.trade_gaps["BTCUSDT"] == 1
    rec.flush()
    df = st.load("BTCUSDT", "trades_1s", T0, T0 + 5000, tmp_path)
    assert list(df["ts"]) == [T0, T0 + 1000]
    r0 = df.iloc[0]
    assert (r0.n, r0.n_buy, r0.buy_qty, r0.sell_qty) == (2, 1, 2.0, 1.0)
    assert r0.vwap == pytest.approx((200 + 101) / 3) and (r0.high, r0.low, r0.last) == (101.0, 100.0, 101.0)
    assert r0.a_last == 2


def test_duplicate_seconds_merge_on_load(tmp_path):
    store, _ = make_store(tmp_path)
    a, b = SecondAggregator(), SecondAggregator()
    a.add(T0 + 10, 1, 100.0, 1.0, False)
    b.add(T0 + 20, 2, 102.0, 1.0, True)   # same second, arrived later via REST backfill
    for ts, fields in a.pop_before() + b.pop_before():
        store.add("trades_1s", "X", ts, fields)
    store.flush(T0)
    df = st.load("X", "trades_1s", T0, T0 + 1000, tmp_path)
    assert len(df) == 1
    assert (df.n[0], df.buy_qty[0], df.sell_qty[0], df["last"][0], df.high[0]) == (2, 1.0, 1.0, 102.0, 102.0)
    assert df.vwap[0] == pytest.approx(101.0)


# ---------------------------------------------------------------- gap detection
def test_gap_tracker_counts_once_per_episode():
    g = GapTracker(60_000)
    for t in (0, 10_000, 20_000):
        assert g.observe("k", t) is None
    assert not g.check("k", 70_000)            # 50 s since last row: fine
    assert g.check("k", 90_000)                # 70 s: open gap, counted
    assert g.check("k", 120_000) and g.count["k"] == 1
    assert g.observe("k", 130_000) == 110_000  # closes the gap, not counted twice
    assert g.count["k"] == 1 and "k" not in g.open
    assert g.observe("k", 300_000) == 170_000  # gap seen only on arrival
    assert g.count["k"] == 2


def test_recorder_book_sampling_and_gap(tmp_path):
    now = [T0]
    rec = make_rec(tmp_path, lambda: now[0])

    def push(ts):
        data = {"e": "depthUpdate", "E": ts + 1, "T": ts, "s": "BTCUSDT", "u": ts, "b": [["100.0", "2"]] * 20,
                "a": [["100.1", "1"]] * 20}
        rec.on_depth_message(json.dumps({"stream": "btcusdt@depth20@100ms", "data": data}), ts)

    push(T0 - 50)
    assert rec.sample_books(T0) == []                    # fresh: written from the socket
    now[0] = T0 + 10_000
    assert rec.sample_books(now[0]) == ["BTCUSDT"]       # stale: needs a REST snapshot
    push(T0 + 120_000)
    rec.sample_books(T0 + 120_000)
    assert rec.gaps.count[("book", "BTCUSDT")] == 1
    now[0] = T0 + 121_000
    s = rec.status()
    assert s["gaps_today"] == 1 and s["per_symbol"]["BTCUSDT"]["rows_today"]["book"] == 2


def test_depth_row_from_stream_and_rest():
    ts, f = depth_row({"T": 5, "E": 6, "u": 9, "b": [["1.0", "2"]], "a": [["1.1", "3"], ["1.2", "4"]]})
    assert ts == 5 and f[:3] == [9, "w", "1.0"] and len(f) == 2 + 80
    assert f[2 + 20] == "2" and f[2 + 40: 2 + 42] == ["1.1", "1.2"] and f[2 + 60] == "3" and f[3] == ""
    ts, f = depth_row({"lastUpdateId": 7, "E": 8, "T": 7, "bids": [["1", "1"]], "asks": [["2", "1"]]}, "r")
    assert ts == 7 and f[:2] == [7, "r"]


# ---------------------------------------------------------------- retention and budget
def _touch(root, rel, size):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_retention_age_then_oldest_first(tmp_path):
    now = T0 + 3 * 86_400_000  # 2026-11-14 12:00
    rels = ["book/A/2026-11-01/05.csv.gz", "book/A/2026-11-11/00.csv.gz", "trades_1s/A/2026-11-11/01.csv.gz",
            "book/B/2026-11-12/03.csv.gz", "book/A/2026-11-14/11.csv.gz"]   # last one is within the protect window
    for r in rels:
        _touch(tmp_path, r, 1000)
    man = st.Manifest(tmp_path)
    for r in rels:
        man.record(r, 1, 1000, 0, 0)
    man.save()
    # age limit 10 days: only the 2026-11-01 file goes
    assert st.apply_retention(tmp_path, now, 10, manifest=man) == [rels[0]]
    assert not (tmp_path / "book/A/2026-11-01").exists()
    # cap 3000 bytes, evict down to 90%: oldest first, never the current hours
    deleted = st.apply_retention(tmp_path, now, 10, cap_bytes=3000, evict_to_fit=True, manifest=man)
    assert deleted == rels[1:3]
    assert (tmp_path / rels[3]).exists() and (tmp_path / rels[4]).exists()
    assert "book/A/2026-11-11/00.csv.gz" not in st.Manifest(tmp_path).day("2026-11-11")
    # without evict_to_fit the cap alone never deletes
    assert st.apply_retention(tmp_path, now, None, cap_bytes=1, evict_to_fit=False) == []


def test_budget_refuses_writes_and_reports(tmp_path):
    now = [T0]
    rec = make_rec(tmp_path, lambda: now[0], cap_bytes=5000)
    rec.budget.used_bytes = 4990  # nearly full
    rec._book("BTCUSDT", T0, book_fields())
    out = rec.flush(T0 + 60_000)
    assert out["refused"] == 1 and out["rows"] == 0
    assert not (tmp_path / "book").exists()
    s = rec.status()
    assert s["disk"]["refusing_writes"] and s["disk"]["refused_rows_today"] == 1 and "cap" in s["disk"]["refusal_reason"]
    rec.budget.used_bytes = 0  # space freed (retention ran): writes resume
    rec._book("BTCUSDT", T0 + 10_000, book_fields())
    assert rec.flush(T0 + 120_000)["rows"] == 1 and not rec.status()["disk"]["refusing_writes"]


def test_budget_respects_machine_free_floor(tmp_path):
    b = st.Budget(tmp_path, cap_bytes=10**12, min_free_bytes=10**9)
    b._free_bytes = 10**9 + 10
    assert not b.can_write(100) and "free disk" in b.reason


# ---------------------------------------------------------------- universe
def test_select_universe_hysteresis():
    info = {"symbols": [{"symbol": s, "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"}
                        for s in ["AUSDT", "BUSDT", "CUSDT", "DUSDT", "币安USDT"]]
            + [{"symbol": "EUSDT", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT", "status": "TRADING"}]}
    tick = [{"symbol": s, "quoteVolume": str(v)} for s, v in
            [("AUSDT", 50), ("BUSDT", 40), ("CUSDT", 30), ("DUSDT", 20), ("EUSDT", 99), ("币安USDT", 98)]]
    assert select_universe(tick, info, 2) == ["AUSDT", "BUSDT"]
    assert select_universe(tick, info, 2, current=["AUSDT", "CUSDT"]) == ["AUSDT", "CUSDT"]  # C within top 3
    assert select_universe(tick, info, 2, current=["AUSDT", "DUSDT"]) == ["AUSDT", "BUSDT"]  # D fell out


# ---------------------------------------------------------------- features maths
def synthetic_book(rows, start=T0, step=10_000):
    """rows: (bid, bq, ask, aq) for level 1; deeper levels fixed at size 1."""
    recs = []
    for i, (bid, bq, ask, aq) in enumerate(rows):
        r = {"ts": start + i * step}
        for lv in range(1, 21):
            r[f"bp{lv}"], r[f"ap{lv}"] = bid - 0.1 * (lv - 1), ask + 0.1 * (lv - 1)
            r[f"bq{lv}"], r[f"aq{lv}"] = (bq, aq) if lv == 1 else (1.0, 1.0)
        recs.append(r)
    return pd.DataFrame(recs)


def test_ofi_cases():
    b = synthetic_book([
        (100.0, 5, 100.1, 5),   # 0 start
        (100.0, 8, 100.1, 5),   # 1 bid size +3            -> +3
        (100.1, 2, 100.2, 4),   # 2 bid up, ask up         -> +2 (new bid) + 5 (old ask) = +7
        (100.0, 6, 100.1, 9),   # 3 bid down, ask down     -> -2 (old bid) - 9 (new ask) = -11
        (100.0, 6, 100.1, 4),   # 4 ask size -5            -> +5
    ])
    o = F.ofi(b)
    assert np.isnan(o["ofi_1"].iloc[0])
    assert o["ofi_1"].iloc[1:].tolist() == [3, 7, -11, 5]
    # a gap longer than max_dt_ms breaks the chain
    b.loc[4, "ts"] += 100_000
    assert np.isnan(F.ofi(b)["ofi_1"].iloc[4])


def test_microprice_spread_and_depth_imbalance():
    b = synthetic_book([(100.0, 3.0, 101.0, 1.0)])
    f = F.book_features(b).iloc[0]
    assert f["mid"] == 100.5 and f["spread"] == 1.0
    assert f["spread_bps"] == pytest.approx(1e4 / 100.5)
    assert f["microprice"] == pytest.approx((101 * 3 + 100 * 1) / 4)   # leans to the ask: bids are heavier
    assert f["micro_dev_bps"] > 0
    assert f["depth_imb_5"] == pytest.approx((7 - 5) / 12)     # bids 3+4*1, asks 1+4*1
    assert f["depth_imb_20"] == pytest.approx((22 - 20) / 42)


def test_minute_features_and_trade_flow():
    b = synthetic_book([(100.0, 5, 100.1, 5), (100.0, 8, 100.1, 5), (100.1, 2, 100.2, 4),   # minute 0
                        (100.0, 6, 100.1, 9), (100.0, 6, 100.1, 4), (100.0, 6, 100.1, 4),
                        (100.0, 7, 100.1, 4)], step=10_000)                                     # 6th row at +60 s
    raw = pd.DataFrame({"ts": [T0 + 1000, T0 + 2000, T0 + 61_000], "a": [1, 2, 3], "price": [100.0, 101.0, 100.0],
                        "qty": [2.0, 1.0, 4.0], "is_buyer_maker": [0, 1, 1]})
    m = F.minute_features(b, raw, ofi_depth=2)
    assert len(m) == 2
    assert m["ofi"].iloc[0] == 3 + 7 - 11 + 5 + 0 and m["ofi"].iloc[1] == 1
    assert m["ofi_ml2"].iloc[0] == m["ofi"].iloc[0]   # level 2 never changes
    assert m["n_snapshots"].tolist() == [6, 1]
    assert m["signed_qty"].iloc[0] == 1.0 and m["signed_qty"].iloc[1] == -4.0
    assert m["vwap"].iloc[0] == pytest.approx(301 / 3) and m["n_trades"].iloc[0] == 2
    # per-second aggregates give the same flow as raw trades
    agg = SecondAggregator()
    for r in raw.itertuples():
        agg.add(r.ts, r.a, r.price, r.qty, bool(r.is_buyer_maker))
    rows = [{"ts": ts, **dict(zip(st.TRADES_1S_COLS[1:], f))} for ts, f in agg.pop_before()]
    t1 = F.trade_flow(pd.DataFrame(rows).apply(pd.to_numeric))
    tr = F.trade_flow(raw)
    assert np.allclose(t1[["signed_qty", "signed_quote", "n_trades", "vwap"]], tr[["signed_qty", "signed_quote",
                                                                                     "n_trades", "vwap"]])
