"""Liveness and readiness probes.

- /health/live  : the process is up (never touches dependencies; used for restart decisions).
- /health/ready : can serve traffic (database, Redis, and the Celery broker).
"""

from __future__ import annotations

import time

import redis
from django.conf import settings
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.common.redis import get_redis


@require_GET
def live(_request):
    return JsonResponse({"status": "ok"})


def _check(fn) -> dict:
    start = time.perf_counter()
    try:
        fn()
        return {"status": "ok", "latency_ms": round((time.perf_counter() - start) * 1000, 2)}
    except Exception as exc:  # noqa: BLE001 - report any failure as unhealthy
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _check_db() -> None:
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_redis() -> None:
    if not get_redis().ping():
        raise RuntimeError("redis ping returned falsy")


def _check_broker() -> None:
    """Celery's broker is a separate Redis DB from the coordination store."""
    url = settings.CELERY_BROKER_URL or ""
    if not url.startswith(("redis://", "rediss://")):
        # Eager / in-memory broker (tests): nothing to ping.
        return
    client = redis.Redis.from_url(url, socket_connect_timeout=1.0, socket_timeout=1.0)
    try:
        if not client.ping():
            raise RuntimeError("broker ping returned falsy")
    finally:
        client.close()


@require_GET
def ready(_request):
    checks = {
        "database": _check(_check_db),
        "redis": _check(_check_redis),
        "broker": _check(_check_broker),
    }
    healthy = all(c["status"] == "ok" for c in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )
