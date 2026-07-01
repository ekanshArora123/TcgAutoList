"""Shared HTTP transport for TCGplayer fetches — pacing + connection reuse.

The sales/price-history collectors fire tens of thousands of requests across a
full backfill. Two things keep that from tripping TCGplayer's rate limiting:

  * one keep-alive ``httpx.AsyncClient`` reused for the whole run (no fresh TLS
    handshake per request — faster, and far less bot-like than a new connection
    every call), and
  * a single ``RateLimiter`` shared across every endpoint so the run is one
    polite, jittered stream rather than bursts.

``request_json`` centralises status handling: it raises a typed ``RateLimited``
on 403/429 (carrying ``Retry-After`` when present) so callers can back off or
stop gracefully, returns ``None`` on 404, and retries transient network errors.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Optional

import httpx

# 403 (Cloudflare bot challenge) and 429 (explicit throttle) both mean "slow
# down / you're blocked". We treat them the same at the transport layer and let
# the collector decide how long to wait and when to give up.
RATE_LIMIT_STATUSES = (403, 429)


class RateLimited(RuntimeError):
    """Raised on an HTTP 403/429. Carries the parsed Retry-After, if any."""

    def __init__(self, status: int, retry_after: Optional[float] = None):
        super().__init__(f"rate limited (HTTP {status})")
        self.status = status
        self.retry_after = retry_after


class RateLimiter:
    """Serialised request pacing with jitter, shared across a collection run.

    Enforces a gap between successive requests (``1 / rate_per_sec``), randomly
    stretched by up to ``jitter`` so the cadence isn't a giveaway lockstep. The
    lock is held across the wait, so requests go out one at a time — exactly the
    polite single stream we want for a backfill.

    Adaptive (AIMD) mode (``adaptive=True``) self-tunes the gap to the live
    sustainable rate, which is the right behaviour for a multi-day drip against
    a refill-limited bucket whose rate drifts with server load: ``on_block``
    multiplicatively slows down, ``on_success`` additively creeps back up. The
    gap is clamped to ``[min_interval, max_interval]``. Built for a single
    sequential consumer, so the tuning fields aren't separately locked.
    """

    def __init__(
        self,
        rate_per_sec: float = 1.5,
        jitter: float = 0.4,
        *,
        adaptive: bool = False,
        min_interval: float = 3.0,
        max_interval: float = 60.0,
        speedup_step: float = 0.5,
        speedup_after: int = 5,
    ):
        self._jitter = max(0.0, jitter)
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self.adaptive = adaptive
        start = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        if adaptive:
            self._min_interval = min_interval
            self._max_interval = max_interval
            self._speedup_step = speedup_step
            self._speedup_after = speedup_after
            self._interval = min(max(start, min_interval), max_interval)
            self._success_streak = 0
        else:
            self._interval = start

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = asyncio.get_event_loop().time()
            interval = self._interval * (1.0 + random.uniform(0.0, self._jitter))
            self._next_at = now + interval

    async def penalize(self, seconds: float) -> None:
        """Push the next allowed request out by ``seconds`` after a block."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            self._next_at = max(self._next_at, now + max(0.0, seconds))

    def on_block(self) -> None:
        """Rate-limited: multiplicatively widen the gap (back off)."""
        if not self.adaptive:
            return
        self._success_streak = 0
        self._interval = min(self._max_interval, self._interval * 2.0)

    def on_success(self) -> None:
        """Clean request: after a streak, additively narrow the gap (speed up)."""
        if not self.adaptive:
            return
        self._success_streak += 1
        if self._success_streak >= self._speedup_after:
            self._success_streak = 0
            self._interval = max(self._min_interval, self._interval - self._speedup_step)

    @property
    def current_rate(self) -> float | None:
        """Current target requests/sec (for progress reporting)."""
        return round(1.0 / self._interval, 3) if self._interval > 0 else None


def make_client() -> httpx.AsyncClient:
    """A keep-alive client tuned for a long, paced collection run."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=15.0),
        limits=httpx.Limits(
            max_keepalive_connections=4, max_connections=8, keepalive_expiry=60.0
        ),
        follow_redirects=True,
    )


def _parse_retry_after(headers: httpx.Headers) -> Optional[float]:
    """Seconds from a numeric Retry-After header; None if absent/HTTP-date."""
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form — ignore; caller falls back to its backoff


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    limiter: Optional[RateLimiter] = None,
    headers: Optional[dict[str, str]] = None,
    json: Any = None,
    retries: int = 2,
) -> Optional[Any]:
    """Paced request returning parsed JSON, or ``None`` on 404.

    Raises ``RateLimited`` on 403/429 and ``RuntimeError`` on other non-200s.
    Transient transport errors (timeouts, resets) get a couple of quick retries
    so a single network blip doesn't abort a long run.
    """
    attempt = 0
    while True:
        if limiter is not None:
            await limiter.acquire()
        try:
            response = await client.request(method, url, headers=headers, json=json)
        except httpx.TransportError as err:
            attempt += 1
            if attempt > retries:
                raise RuntimeError(f"network error after {retries} retries: {err}") from err
            await asyncio.sleep(min(2 ** attempt, 8))
            continue

        if response.status_code in RATE_LIMIT_STATUSES:
            raise RateLimited(response.status_code, _parse_retry_after(response.headers))
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(
                f"TCGplayer API error: {response.status_code} {response.reason_phrase}"
            )
        return response.json()
