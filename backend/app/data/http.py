"""Resilient async HTTP with per-provider weight budgeting, retries, exponential backoff and failover."""
import asyncio
import random
import time
from dataclasses import dataclass, field

import httpx

from app.logging import log


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class ProviderStatus:
    name: str
    ok: bool = True
    last_ok_ms: int | None = None
    last_error: str | None = None
    last_error_ms: int | None = None
    consecutive_failures: int = 0
    requests: int = 0
    banned_until_ms: int = 0
    latency_ms: float | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class WeightBudget:
    """Sliding 60s budget. Binance spot: 6000 weight/min; USDⓈ-M futures: 2400/min. We keep a safety margin."""

    per_minute: int
    events: list[tuple[float, int]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, weight: int) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                self.events = [(t, w) for t, w in self.events if now - t < 60]
                used = sum(w for _, w in self.events)
                if used + weight <= self.per_minute:
                    self.events.append((now, weight))
                    return
                wait = 60 - (now - self.events[0][0]) + 0.05
            await asyncio.sleep(max(wait, 0.05))


class ResilientClient:
    def __init__(self, name: str, base_urls: list[str], per_minute_weight: int, timeout: float = 10.0):
        self.name = name
        self.base_urls = base_urls
        self.budget = WeightBudget(per_minute_weight)
        self.status = ProviderStatus(name)
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "aitrade/0.1"})

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict | None = None, weight: int = 1, retries: int = 4):
        now_ms = int(time.time() * 1000)
        if self.status.banned_until_ms > now_ms:
            raise ProviderUnavailable(f"{self.name} rate-limit ban until {self.status.banned_until_ms}")
        await self.budget.acquire(weight)
        last_exc: Exception | None = None
        for attempt in range(retries):
            base = self.base_urls[attempt % len(self.base_urls)]
            t0 = time.perf_counter()
            try:
                r = await self._client.get(base + path, params=params)
                self.status.requests += 1
                if r.status_code in (418, 429):
                    retry_after = int(r.headers.get("Retry-After", "60"))
                    self.status.banned_until_ms = int(time.time() * 1000) + retry_after * 1000
                    raise ProviderUnavailable(f"{self.name} HTTP {r.status_code}, retry after {retry_after}s")
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"server {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                self.status.latency_ms = (time.perf_counter() - t0) * 1000
                self._mark_ok()
                return r.json()
            except ProviderUnavailable as e:
                self._mark_err(str(e))
                raise
            except httpx.HTTPStatusError as e:
                if e.response is not None and 400 <= e.response.status_code < 500:
                    self._mark_err(f"{e.response.status_code}: {e.response.text[:200]}")
                    raise
                last_exc = e
            except (httpx.TransportError, ValueError) as e:
                last_exc = e
            delay = min(2**attempt + random.random(), 20)
            log.warning("http_retry", provider=self.name, path=path, attempt=attempt, error=str(last_exc))
            await asyncio.sleep(delay)
        self._mark_err(str(last_exc))
        raise ProviderUnavailable(f"{self.name} failed after {retries} attempts: {last_exc}")

    def _mark_ok(self) -> None:
        self.status.ok = True
        self.status.consecutive_failures = 0
        self.status.last_ok_ms = int(time.time() * 1000)

    def _mark_err(self, msg: str) -> None:
        self.status.consecutive_failures += 1
        self.status.ok = self.status.consecutive_failures < 3
        self.status.last_error = msg
        self.status.last_error_ms = int(time.time() * 1000)
