"""Forward-test candidate book (synthetic data only, no network)."""
import json

import numpy as np
import pandas as pd
import pytest

from app.quant.lab import candidate as qc
from app.quant.lab import data as qd
from tests.test_quant_lab_exec import _wide


def _setup(monkeypatch, root, panel, fund):
    monkeypatch.setattr(qd, "load_panel", lambda *a, **k: panel)
    monkeypatch.setattr(qd, "load_funding_daily", lambda *a, **k: fund)
    monkeypatch.setattr(qc, "ROOT", root)


def test_candidate_book_uses_no_data_after_as_of(tmp_path, monkeypatch):
    panel, fund = _wide(seed=7, n_sym=40, n_days=500)
    as_of = "2022-03-20"
    _setup(monkeypatch, tmp_path / "a", panel, fund)
    a = qc.candidate_book("C", as_of)
    spec = json.loads((tmp_path / "a" / "C" / "spec.json").read_text())
    assert a["promoted"] is False and a["candidate"] == "C" and "forward" in a["note"]
    assert a["as_of"] == a["data_through"] == as_of and a["spec_sha"] == spec["spec_sha"]
    assert list((tmp_path / "a" / "C").glob("model_*.txt"))                  # fitted boosters are saved
    # wreck every row after as_of (and add later rows): the book must not change
    p2 = panel.copy()
    after = pd.to_datetime(p2["date"]) > pd.Timestamp(as_of)
    rng = np.random.default_rng(0)
    for c in ("open", "high", "low", "close"):
        p2.loc[after, c] *= np.exp(rng.normal(0, 0.5, after.sum()))
    p2.loc[after, "quote_volume"] *= rng.uniform(0.01, 100, after.sum())
    f2 = fund.copy()
    f2.loc[pd.to_datetime(f2["date"]) > pd.Timestamp(as_of), "funding"] = 0.05
    _setup(monkeypatch, tmp_path / "b", p2, f2)
    b = qc.candidate_book("C", as_of)
    assert [(r["symbol"], r["score"], r["weight"]) for r in a["rows"]] == \
        [(r["symbol"], r["score"], r["weight"]) for r in b["rows"]]
    assert a["portfolio"] == b["portfolio"]
    # the spec is frozen: a second call never rewrites it
    before = (tmp_path / "a" / "C" / "spec.json").read_text()
    _setup(monkeypatch, tmp_path / "a", panel, fund)
    qc.candidate_book("C", as_of)
    assert (tmp_path / "a" / "C" / "spec.json").read_text() == before


def test_candidate_weights_sum_to_configured_gross(tmp_path, monkeypatch):
    panel, fund = _wide(seed=8, n_sym=40, n_days=500)
    _setup(monkeypatch, tmp_path, panel, fund)
    rebalance_day = "2022-03-25"            # (2022-03-25 - 2021-01-01) = 448 days = 64 weeks
    r = qc.candidate_book("C", rebalance_day)
    p = r["portfolio"]
    assert p["k"] == 10 and p["side"] == "long_short" and p["rebalance_days"] == 7
    assert p["rebalance_anchor"] == "2021-01-01" and p["execution"] == "next_open" and p["order_type"] == "taker"
    assert p["rebalance_today"] is True and p["next_rebalance"] == rebalance_day
    longs = sum(x["weight"] for x in r["rows"] if x["side"] == "long")
    shorts = -sum(x["weight"] for x in r["rows"] if x["side"] == "short")
    assert longs == pytest.approx(p["gross_long"], abs=2e-3) and shorts == pytest.approx(p["gross_short"], abs=2e-3)
    assert 0 < p["gross_long"] <= 1.0 + 1e-9 and 0 < p["gross_short"] <= 1.0 + 1e-9
    # without the vol target and turnover limit, a rebalance day's book is exactly the configured gross per side
    spec = json.loads((tmp_path / "C" / "spec.json").read_text())
    spec["portfolio"].update(vol_target_ls=0.0, turnover_limit=None)
    root2 = tmp_path / "plain"
    (root2 / "C").mkdir(parents=True)
    (root2 / "C" / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setattr(qc, "ROOT", root2)
    r2 = qc.candidate_book("C", rebalance_day)
    assert r2["portfolio"]["gross_long"] == pytest.approx(spec["portfolio"]["max_gross_long"], abs=1e-3)
    assert r2["portfolio"]["gross_short"] == pytest.approx(spec["portfolio"]["max_gross_short"], abs=1e-3)
    assert r2["portfolio"]["positions"] == 20
