import numpy as np
import pandas as pd
import pytest


def make_ohlcv(n: int = 600, seed: int = 7, start_ms: int = 1_700_000_000_000 // 3_600_000 * 3_600_000,
               step_ms: int = 3_600_000, drift: float = 0.0) -> pd.DataFrame:
    """Synthetic geometric random walk for unit tests only (never used at runtime)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.01, n)
    close = 100 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.006, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread * rng.uniform(0.3, 1.0, n)
    vol = rng.lognormal(3, 0.5, n)
    return pd.DataFrame({
        "open_time": start_ms + np.arange(n) * step_ms,
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
        "quote_volume": vol * close, "trades": rng.integers(10, 1000, n),
        "taker_buy_base": vol * rng.uniform(0.3, 0.7, n), "taker_buy_quote": 0.0,
    })


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()


@pytest.fixture
async def db():
    from app import db as dbmod

    dbmod.init_engine("sqlite+aiosqlite:///:memory:")
    await dbmod.create_all()
    async with dbmod.session_factory()() as s:
        yield s
    await dbmod.engine().dispose()
