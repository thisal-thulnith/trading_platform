"""In-process pub/sub with bounded subscriber queues (drop-oldest backpressure).

Single-process deployment keeps the engine and API together; for multi-process scaling swap this for Redis
pub/sub behind the same interface (see ARCHITECTURE.md)."""
import asyncio
from collections import defaultdict
from typing import Any


class Bus:
    def __init__(self, maxsize: int = 1000) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.maxsize = maxsize
        self.dropped = 0

    def subscribe(self, *topics: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(self.maxsize)
        for t in topics:
            self._subs[t].add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        for subs in self._subs.values():
            subs.discard(q)

    def publish(self, topic: str, payload: Any) -> None:
        for q in list(self._subs.get(topic, ())) + list(self._subs.get("*", ())):
            if q.full():
                try:
                    q.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait({"topic": topic, "data": payload})


bus = Bus()
