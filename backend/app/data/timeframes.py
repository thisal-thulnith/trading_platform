"""Timeframe utilities. Times are UTC epoch milliseconds for candle open time."""
TIMEFRAMES: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

# Binance weekly candles open on Monday 00:00 UTC; epoch (1970-01-01) was a Thursday.
_WEEK_OFFSET_MS = 4 * 86_400_000


def tf_ms(tf: str) -> int:
    try:
        return TIMEFRAMES[tf]
    except KeyError as e:
        raise ValueError(f"unsupported timeframe {tf!r}") from e


def floor_time(ts_ms: int, tf: str) -> int:
    step = tf_ms(tf)
    if tf == "1w":
        return ((ts_ms - _WEEK_OFFSET_MS) // step) * step + _WEEK_OFFSET_MS
    return (ts_ms // step) * step


def higher_timeframes(tf: str) -> list[str]:
    keys = list(TIMEFRAMES)
    return keys[keys.index(tf) + 1 :]


def expected_open_times(start_ms: int, end_ms: int, tf: str) -> list[int]:
    step = tf_ms(tf)
    t = floor_time(start_ms, tf)
    if t < start_ms:
        t += step
    out = []
    while t <= end_ms:
        out.append(t)
        t += step
    return out
