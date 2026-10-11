"""
Process-wide Redis client.

A single connection pool per process is shared by the API (enqueue) and workers (locks
and queues). Short socket timeouts keep a sick Redis from stalling request
threads: the API degrades (returns 503 for new writes) instead of hanging.
"""

from __future__ import annotations

import threading

import redis
from django.conf import settings

_lock = threading.Lock()
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = _build_client()
    return _client


def _build_client() -> redis.Redis:
    if getattr(settings, "USE_FAKE_REDIS", False):
        import fakeredis

        # One shared in-memory server per process so API and (eager) tasks see the same data.
        server = fakeredis.FakeServer()
        return fakeredis.FakeRedis(server=server, decode_responses=True)

    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=2.0,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=False,
        max_connections=256,
    )


def reset_redis_for_tests() -> None:
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.flushall()
            except Exception:  # pragma: no cover - defensive
                pass
        _client = None
