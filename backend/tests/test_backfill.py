import respx
from httpx import Response

from app.data.binance import BinanceClient
from app.data.ingest import backfill
from app.data.store import load_candles, upsert_candles
from app.data.timeframes import floor_time

H = 3_600_000


def kl(t):
    return [t, "100", "101", "99", "100.5", "10", t + H - 1, "1000", 5, "5", "500", "0"]


@respx.mock
async def test_backfill_extends_history_backwards(db, monkeypatch):
    import app.data.ingest as ing

    now = floor_time(1_800_000_000_000, "1h") + 1000
    monkeypatch.setattr(ing, "now_ms", lambda: now)

    def handler(request):
        p = dict(request.url.params)
        start = int(p["startTime"])
        stop = int(p.get("endTime", now))
        t = floor_time(start, "1h") + (H if floor_time(start, "1h") < start else 0)
        rows = []
        while t <= stop and len(rows) < int(p["limit"]):
            rows.append(kl(t))
            t += H
        return Response(200, json=rows)

    respx.get(url__regex=r"https://api\.binance\.com/api/v3/klines.*").mock(side_effect=handler)
    last = floor_time(now, "1h") - H
    # seed only the most recent 100 bars
    await upsert_candles(db, "BTCUSDT", "spot", "1h",
                         [{"open_time": last - i * H, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1,
                           "quote_volume": 1, "trades": 1, "taker_buy_base": 0.5, "taker_buy_quote": 1} for i in range(100)])
    c = BinanceClient()
    await backfill(c, "BTCUSDT", "1h", 2500)
    await c.close()
    df = await load_candles(db, "BTCUSDT", "1h", limit=5000)
    assert len(df) >= 2499
    assert df["open_time"].diff().dropna().eq(H).all()
