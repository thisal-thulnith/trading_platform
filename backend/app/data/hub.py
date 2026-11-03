"""Live in-memory market state: forming candles, last prices, derivatives, order books, provider health."""
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class StreamStatus:
    name: str
    connected: bool = False
    last_msg_ms: int | None = None
    reconnects: int = 0
    last_error: str | None = None
    messages: int = 0

    def d(self) -> dict:
        age = (now_ms() - self.last_msg_ms) / 1000 if self.last_msg_ms else None
        return {**self.__dict__, "age_s": age}


@dataclass
class MarketHub:
    universe: list[str] = field(default_factory=list)  # base assets, e.g. BTC
    symbols: list[str] = field(default_factory=list)  # BTCUSDT
    live: dict[tuple[str, str], dict] = field(default_factory=dict)  # (symbol, tf) -> forming candle
    last_price: dict[str, float] = field(default_factory=dict)
    last_price_ms: dict[str, int] = field(default_factory=dict)
    mark: dict[str, dict] = field(default_factory=dict)
    deriv: dict[str, dict] = field(default_factory=dict)
    books: dict[str, dict] = field(default_factory=dict)
    liquidations: dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=200)))
    streams: dict[str, StreamStatus] = field(default_factory=dict)
    gaps_repaired: int = 0
    candles_rejected: int = 0
    last_scan: dict[str, int] = field(default_factory=dict)
    universe_meta: dict = field(default_factory=dict)
    started_ms: int = field(default_factory=now_ms)
    funding_interval_h: dict[str, int] = field(default_factory=dict)

    def stream(self, name: str) -> StreamStatus:
        if name not in self.streams:
            self.streams[name] = StreamStatus(name)
        return self.streams[name]

    def freshness(self, symbol: str) -> dict:
        t = self.last_price_ms.get(symbol)
        return {"last_update_ms": t, "age_s": (now_ms() - t) / 1000 if t else None,
                "stale": t is None or now_ms() - t > 60_000}


hub = MarketHub()
