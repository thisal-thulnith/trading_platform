import numpy as np
import pandas as pd

from app.ml import forecast as F
from app.quant.lab.factors import Wide


def _wide(n=40, spike_day=None, spike_high=None, dip_day=None, dip_low=None):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    cols = ["AAAUSDT"]
    close = pd.DataFrame(100.0, index=idx, columns=cols)
    high = pd.DataFrame(101.0, index=idx, columns=cols)
    low = pd.DataFrame(99.0, index=idx, columns=cols)
    if spike_day is not None:
        high.iloc[spike_day, 0] = spike_high
    if dip_day is not None:
        low.iloc[dip_day, 0] = dip_low
    z = pd.DataFrame(1e7, index=idx, columns=cols)
    return Wide(close=close, high=high, low=low, quote_volume=z, taker_buy_quote=z / 2,
                funding=pd.DataFrame(np.nan, index=idx, columns=cols))


def test_target_first_labels_use_only_later_candles():
    # ATR = 2, so from day 20 a long's stop is 97 and its target 106 (3 ATR); a 107 high on day 23 wins
    y = F.labels(_wide(spike_day=23, spike_high=107.0))
    assert y["long_tp_first"].iloc[20, 0] == 1.0 and y["short_tp_first"].iloc[20, 0] == 0.0
    assert y["long_tp_first"].iloc[23, 0] == 0.0          # the spike day itself is not "later"
    assert np.isnan(y["long_tp_first"].iloc[-1, 0])       # the future is unknown at the end
    # a day touching both the stop and the target counts as the stop
    both = F.labels(_wide(spike_day=23, spike_high=107.0, dip_day=23, dip_low=96.0))
    assert both["long_tp_first"].iloc[20, 0] == 0.0
    drop = F.labels(_wide(dip_day=24, dip_low=95.0))
    assert drop["drop_risk_7d"].iloc[20, 0] == 1.0 and drop["drop_risk_7d"].iloc[10, 0] == 0.0


def test_for_symbol_reports_status_accuracy_and_coverage(monkeypatch):
    rep = {"trained_at": "2026-11-22T00:00:00+00:00", "models": {
        "long_tp_first": {"label": "Long hits target", "what": "w", "status": "accepted", "why": "AUC 0.649",
                          "kind": "binary", "chosen": "linear", "beats_volatility_only": True,
                          "holdout": {"auc": 0.649, "top_decile_rate": 0.28, "bottom_decile_rate": 0.06, "base_rate": 0.14}},
        "drop_risk_7d": {"label": "Drop risk", "what": "w", "status": "accepted", "why": "AUC 0.653", "kind": "binary",
                         "chosen": "linear", "beats_volatility_only": False,
                         "holdout": {"auc": 0.653, "top_decile_rate": 0.4, "bottom_decile_rate": 0.1, "base_rate": 0.22}}}}
    td = {"as_of": "2026-11-21", "plan": {"stop_atr": 1.5, "target_atr": 3, "days": 14},
          "rows": {"ETHUSDT": {"long_tp_first": 0.09, "drop_risk_7d": 0.2, "atr_pct": 6.3}}}
    monkeypatch.setattr(F, "report", lambda tier="core": rep)
    monkeypatch.setattr(F, "today", lambda: td)
    r = F.for_symbol("ETHUSDT")
    assert r["covered"] and r["as_of"] == "2026-11-21"
    lt = next(m for m in r["models"] if m["target"] == "long_tp_first")
    assert lt["value"] == 0.09 and lt["base_rate"] == 0.14 and "top tenth" in lt["accuracy"]
    dr = next(m for m in r["models"] if m["target"] == "drop_risk_7d")
    assert "volatility alone" in dr["accuracy"]          # honest about where the edge comes from
    assert not F.for_symbol("NOPEUSDT")["covered"]


def test_model_check_counts_only_accepted_models(monkeypatch):
    from app.agents import validators as V

    def fake(symbol):
        return {"available": True, "covered": True, "models": [
            {"target": "long_tp_first", "status": "accepted", "value": 0.25, "base_rate": 0.14},
            {"target": "short_tp_first", "status": "weak", "value": 0.30, "base_rate": 0.17},
            {"target": "drop_risk_7d", "status": "accepted", "value": 0.40, "base_rate": 0.22}]}

    monkeypatch.setattr(F, "for_symbol", fake)
    long_checks = {c["name"]: c for c in V.model_checks("ETHUSDT", "LONG")}
    assert long_checks["Trained model: target before stop"]["status"] == "pass"
    assert long_checks["Trained model: drop risk"]["status"] == "warn"
    short = {c["name"]: c for c in V.model_checks("ETHUSDT", "SHORT")}
    assert short["Trained model: target before stop"]["status"] == "warn"   # a weak model never passes
