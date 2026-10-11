"""Burst-create notifications against a running stack and print ingest and drain results.

Requires ``make up``. Uses only the standard library. Tunables come from the environment:

    LOAD_REQUESTS      notifications to create (default 8000)
    LOAD_USERS         distinct recipients (default 400)
    LOAD_CONCURRENCY   parallel HTTP clients (default 100)
    LOAD_API           API origin (default http://127.0.0.1:8000)
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
USER_PREFIX = "loadtest-"


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from None
    if value < 1:
        raise SystemExit(f"{name} must be >= 1, got {value}")
    return value


def compose(*args: str, input_text: str | None = None) -> str:
    proc = subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SystemExit(f"docker compose {' '.join(args)} failed:\n{detail}")
    return proc.stdout


def psql(sql: str) -> str:
    return compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "notify",
        "-d",
        "notify",
        "-v",
        "ON_ERROR_STOP=1",
        "-t",
        "-A",
        "-c",
        sql,
    )


def seed_users(count: int) -> list[str]:
    script = f"""
from apps.accounts.models import User

count = {count}
existing = User.objects.filter(email__startswith={USER_PREFIX!r}).count()
if existing < count:
    batch = []
    for i in range(existing, count):
        user = User(email=f"{USER_PREFIX}{{i:04d}}@example.com")
        user.set_unusable_password()
        batch.append(user)
    User.objects.bulk_create(batch, batch_size=200)

ids = (
    User.objects.filter(email__startswith={USER_PREFIX!r})
    .order_by("email")
    .values_list("id", flat=True)[:count]
)
print("\\n".join(str(pk) for pk in ids), flush=True)
"""
    output = compose(
        "exec",
        "-T",
        "api",
        "python",
        "manage.py",
        "shell",
        "--verbosity",
        "0",
        input_text=script,
    )
    ids = [line.strip() for line in output.splitlines() if _is_uuid(line.strip())]
    if len(ids) < count:
        raise SystemExit(f"expected {count} load-test users, found {len(ids)}")
    return ids


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def check_api(host: str, port: int) -> None:
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request("GET", "/health/live")
        resp = conn.getresponse()
        resp.read()
    except OSError as exc:
        raise SystemExit(
            f"API is not reachable at {host}:{port} ({exc}). Start it with: make up"
        ) from exc
    finally:
        conn.close()
    if resp.status != 200:
        raise SystemExit(f"/health/live returned {resp.status}. Start the stack with: make up")


def burst(host: str, port: int, users: list[str], total: int, concurrency: int) -> dict:
    types = ("sms", "email", "push")
    bodies = [
        json.dumps(
            {
                "user_id": users[i % len(users)],
                "type": types[i % 3],
                "message": f"load-test {i}",
            }
        ).encode()
        for i in range(total)
    ]
    workers = min(concurrency, total)
    chunks = [bodies[i::workers] for i in range(workers)]

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda chunk: _post_chunk(host, port, chunk), chunks))
    elapsed = time.perf_counter() - started

    latencies: list[float] = []
    codes: dict[str, int] = {}
    for chunk_latencies, chunk_codes in results:
        latencies.extend(chunk_latencies)
        for key, count in chunk_codes.items():
            codes[key] = codes.get(key, 0) + count
    latencies.sort()
    return {"elapsed": elapsed, "latencies": latencies, "codes": codes}


def _post_chunk(host: str, port: int, bodies: list[bytes]) -> tuple[list[float], dict[str, int]]:
    latencies: list[float] = []
    codes: dict[str, int] = {}
    conn = http.client.HTTPConnection(host, port, timeout=30)
    for body in bodies:
        t0 = time.perf_counter()
        try:
            conn.request(
                "POST",
                "/api/v1/notifications/",
                body=body,
                headers={"Content-Type": "application/json", "Connection": "keep-alive"},
            )
            resp = conn.getresponse()
            resp.read()
            latencies.append((time.perf_counter() - t0) * 1000)
            key = str(resp.status)
        except Exception as exc:
            key = type(exc).__name__
            try:
                conn.close()
            except Exception:
                pass
            conn = http.client.HTTPConnection(host, port, timeout=30)
        codes[key] = codes.get(key, 0) + 1
    conn.close()
    return latencies, codes


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(round((pct / 100) * (len(sorted_values) - 1)))))
    return sorted_values[index]


def status_counts(since: str) -> dict[str, int]:
    sql = f"""
    SELECT status, count(*)
    FROM notifications_notification
    WHERE created_at >= TIMESTAMPTZ '{since}+00'
    GROUP BY status
    """
    counts: dict[str, int] = {}
    for line in psql(sql).splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        status, count = line.split("|", 1)
        counts[status] = int(count)
    return counts


def print_drain(since: str, accepted: int) -> None:
    print(f"{'t+s':>6} {'pending':>8} {'processing':>11} {'sent':>8} {'failed':>8}")
    deadline = time.perf_counter() + max(120, accepted / 40)
    started = time.perf_counter()
    last: dict[str, int] = {}
    while True:
        last = status_counts(since)
        pending = last.get("pending", 0)
        processing = last.get("processing", 0)
        sent = last.get("sent", 0)
        failed = last.get("failed", 0)
        elapsed = time.perf_counter() - started
        print(f"{elapsed:6.1f} {pending:8d} {processing:11d} {sent:8d} {failed:8d}", flush=True)
        if pending == 0 and processing == 0 and sent + failed >= accepted:
            break
        if time.perf_counter() >= deadline:
            print("drain still in progress when the wait expired")
            break
        time.sleep(2)

    stats = psql(
        f"""
        SELECT
          coalesce(round(avg(attempts)::numeric, 2), 0),
          coalesce(max(attempts), 0),
          coalesce(round(avg(EXTRACT(EPOCH FROM (
            COALESCE(sent_at, failed_at) - created_at
          )))::numeric, 2), 0),
          coalesce(round((percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (COALESCE(sent_at, failed_at) - created_at))
          ))::numeric, 2), 0)
        FROM notifications_notification
        WHERE created_at >= TIMESTAMPTZ '{since}+00'
          AND status IN ('sent', 'failed')
        """
    ).strip()
    if not stats:
        return
    avg_attempts, max_attempts, avg_s, p95_s = stats.split("|")
    print(
        "end_to_end:"
        f" avg_attempts={avg_attempts} max_attempts={max_attempts}"
        f" avg_s={avg_s} p95_s={p95_s}"
    )


def main() -> int:
    total = env_int("LOAD_REQUESTS", 8000)
    user_count = env_int("LOAD_USERS", 400)
    concurrency = env_int("LOAD_CONCURRENCY", 100)
    parsed = urlparse(os.environ.get("LOAD_API", "http://127.0.0.1:8000"))
    if parsed.scheme not in {"http", ""} or not parsed.hostname:
        raise SystemExit(f"LOAD_API must be an http origin, got {parsed.geturl()!r}")
    host = parsed.hostname
    port = parsed.port or 80

    check_api(host, port)
    users = seed_users(user_count)
    since = psql(
        "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')"
    ).strip()
    result = burst(host, port, users, total, concurrency)

    latencies: list[float] = result["latencies"]
    codes: dict[str, int] = result["codes"]
    accepted = codes.get("201", 0)
    elapsed = result["elapsed"]
    rps = accepted / elapsed if elapsed else 0.0
    print(f"users={len(users)} requests={total} concurrency={min(concurrency, total)}")
    print(f"ingest: {accepted} accepted in {elapsed:.2f}s ({rps:.1f} req/s) codes={codes}")
    if latencies:
        print(
            "latency_ms:"
            f" min={latencies[0]:.1f}"
            f" p50={percentile(latencies, 50):.1f}"
            f" p95={percentile(latencies, 95):.1f}"
            f" p99={percentile(latencies, 99):.1f}"
            f" max={latencies[-1]:.1f}"
        )
    if accepted == 0:
        return 1
    print("drain:")
    print_drain(since, accepted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
