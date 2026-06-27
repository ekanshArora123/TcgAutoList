"""Tests for the robust sales collector — transport + batch selection + backoff.

No real network: ``request_json`` is exercised through an ``httpx.MockTransport``,
and batch selection / backlog counting run against an in-memory DB. Async paths
run via ``asyncio.run`` (matching the suite's convention — no pytest-asyncio).
The timing paths (``RateLimiter`` spacing, block backoff) are asserted on the
computed wait, not by sleeping.

Run: pytest -q
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.market.sales_collector import SalesCollector
from services.card_server.helpers.tcgplayer.transport import (
    RateLimited,
    RateLimiter,
    _parse_retry_after,
    request_json,
)

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


# ─── request_json status handling ────────────────────────────


async def _do_request(handler, **kw):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        return await request_json(c, "GET", "https://x/", **kw)


def test_request_json_returns_parsed_body_on_200():
    assert asyncio.run(_do_request(lambda req: httpx.Response(200, json={"ok": 1}))) == {"ok": 1}


def test_request_json_returns_none_on_404():
    assert asyncio.run(_do_request(lambda req: httpx.Response(404))) is None


@pytest.mark.parametrize("status", [403, 429])
def test_request_json_raises_rate_limited(status):
    with pytest.raises(RateLimited) as exc:
        asyncio.run(_do_request(lambda req: httpx.Response(status, headers={"retry-after": "12"})))
    assert exc.value.status == status
    assert exc.value.retry_after == 12.0


def test_request_json_raises_runtimeerror_on_500():
    with pytest.raises(RuntimeError):
        asyncio.run(_do_request(lambda req: httpx.Response(500)))


def test_request_json_retries_transient_errors_then_gives_up(monkeypatch):
    import services.card_server.helpers.tcgplayer.transport as transport

    async def _no_sleep(_s):  # keep the retry backoff instant
        return None

    monkeypatch.setattr(transport.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    with pytest.raises(RuntimeError, match="network error"):
        asyncio.run(_do_request(handler, retries=2))
    assert calls["n"] == 3  # initial try + 2 retries


def test_parse_retry_after_handles_missing_and_http_date():
    assert _parse_retry_after(httpx.Headers({})) is None
    assert _parse_retry_after(httpx.Headers({"retry-after": "30"})) == 30.0
    # HTTP-date form isn't parsed — caller falls back to its own backoff.
    assert _parse_retry_after(httpx.Headers({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None


# ─── RateLimiter pacing ──────────────────────────────────────


def test_rate_limiter_spaces_requests():
    async def go():
        limiter = RateLimiter(rate_per_sec=1000, jitter=0.0)  # ~1ms spacing
        now = asyncio.get_event_loop().time
        await limiter.acquire()
        t0 = now()
        await limiter.acquire()
        return now() - t0

    assert asyncio.run(go()) >= 0.0009  # roughly the 1ms min interval


# ─── block backoff policy ────────────────────────────────────


def test_block_pause_retry_after_raises_floor_when_larger():
    # Retry-After (45s) exceeds the block-1 floor (30s) -> honour the 45s.
    err = RateLimited(429, retry_after=45.0)
    assert SalesCollector._block_pause_s(err, 1, 30_000, 300_000) == 45.0


def test_block_pause_ignores_too_short_retry_after():
    # A short Retry-After (4s) is below our floor -> wait the escalating floor,
    # not the advisory hint (which would just 429 again immediately).
    err = RateLimited(429, retry_after=4.0)
    assert SalesCollector._block_pause_s(err, 1, 30_000, 300_000) == 30.0   # block-1 floor
    assert SalesCollector._block_pause_s(err, 2, 30_000, 300_000) == 60.0   # block-2 floor


def test_block_pause_caps_retry_after_at_max():
    err = RateLimited(429, retry_after=99_999.0)
    assert SalesCollector._block_pause_s(err, 1, 30_000, 300_000) == 300.0


def test_block_pause_exponential_without_retry_after():
    err = RateLimited(403, retry_after=None)
    assert SalesCollector._block_pause_s(err, 1, 30_000, 300_000) == 30.0   # 30 * 2**0
    assert SalesCollector._block_pause_s(err, 2, 30_000, 300_000) == 60.0   # 30 * 2**1
    assert SalesCollector._block_pause_s(err, 9, 30_000, 300_000) == 300.0  # capped


# ─── batch selection + backlog count ─────────────────────────


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _own(conn: sqlite3.Connection, card_id: str, name: str) -> None:
    """Create an owned card: cards -> skus -> inventory(status unlisted)."""
    CardsHelper(conn).upsert({"id": card_id, "card_name": name, "set_name": "S"})
    cur = conn.execute(
        "INSERT INTO skus (card_id, condition, finish) VALUES (?, 'NM', 'Holo')", (card_id,)
    )
    conn.execute(
        "INSERT INTO inventory (sku_id, status) VALUES (?, 'unlisted')", (cur.lastrowid,)
    )


def _sale(conn: sqlite3.Connection, card_id: str, fetched_at: str) -> None:
    conn.execute(
        """INSERT INTO sales (card_id, condition, finish, source, order_date,
               purchase_price, shipping_price, quantity, has_image, fetched_at)
           VALUES (?, 'NM', 'Holo', 'tcgplayer', '2026-01-01', 1, 0, 1, 0, ?)""",
        (card_id, fetched_at),
    )


def test_batch_orders_never_fetched_first_then_oldest(db):
    _own(db, "A", "Alpha")  # never fetched
    _own(db, "B", "Bravo")
    _own(db, "C", "Charlie")
    _sale(db, "B", "2020-01-01T00:00:00")  # oldest fetched
    _sale(db, "C", "2025-06-01T00:00:00")  # newer, but still > a year stale

    collector = SalesCollector(db)
    # Both fetch dates are well past a 7-day window, so all three are pending;
    # expect never-fetched A first, then oldest-fetched B, then C.
    ids = collector._get_batch_card_ids(limit=10, max_age_days=7)
    assert ids == ["A", "B", "C"]


def test_batch_skips_recently_fetched_and_respects_limit(db):
    _own(db, "A", "Alpha")  # never fetched -> pending
    _own(db, "B", "Bravo")
    _sale(db, "B", (datetime.now() - timedelta(days=1)).isoformat())  # fresh -> skipped

    collector = SalesCollector(db)
    assert collector._get_batch_card_ids(limit=10, max_age_days=7) == ["A"]
    assert collector.count_pending(max_age_days=7) == 1
    # A tight limit caps the batch even when more are pending.
    _own(db, "C", "Charlie")
    assert len(collector._get_batch_card_ids(limit=1, max_age_days=7)) == 1
    assert collector.count_pending(max_age_days=7) == 2


def test_sold_cards_excluded_from_backlog(db):
    _own(db, "A", "Alpha")
    db.execute("UPDATE inventory SET status='sold'")
    assert SalesCollector(db).count_pending(max_age_days=7) == 0
