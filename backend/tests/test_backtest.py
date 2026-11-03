import numpy as np

from app.backtest.engine import BacktestConfig, resample_closed, run_backtest
from app.backtest.metrics import max_drawdown, trade_stats
from tests.conftest import make_ohlcv


def test_resample_drops_incomplete_bucket():
    df = make_ohlcv(10, start_ms=0)  # 1h bars from epoch
    r = resample_closed(df, "1h", "4h")
    assert len(r) == 2  # bars 0-3, 4-7 complete; 8-9 incomplete dropped
    assert r["high"].iloc[0] == df["high"].iloc[:4].max()


def test_metrics():
    assert max_drawdown(np.array([100, 120, 90, 130]))[0] == -0.25
    s = trade_stats([1, -1, 2, -1, -1, 3])
    assert s["n"] == 6 and s["max_consecutive_losses"] == 2 and s["expectancy_r"] == 0.5


def test_backtest_runs_end_to_end():
    data = {"AAA": make_ohlcv(700, seed=4, drift=0.001), "BBB": make_ohlcv(700, seed=9, drift=-0.001)}
    cfg = BacktestConfig(symbols=list(data), timeframe="1h", step=2, window=250)
    res = run_backtest(data, cfg)
    base = res["scenarios"]["base"]
    assert "trades" in base and "equity_curve" in base
    assert res["config_hash"] == cfg.hash()
    if base["trades"]["n"] > 0:
        # stress costs can only make results worse or equal
        assert res["scenarios"]["stress"]["trades"]["total_r"] <= base["trades"]["total_r"] + 1e-9
