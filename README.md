# Notification service

A Django + DRF service that accepts sms, email, and push notifications, keeps a durable status for each one, and delivers them in arrival order with at most one send in flight per user. It is built to stay correct under concurrent API instances and worker crashes, and to accept at least 1000 creates per second.

The database is the source of truth. Redis only coordinates per-user queues and locks. If Redis or the broker drops a dispatch, a periodic sweeper rebuilds it from PostgreSQL.

## Stack

Python 3.12, Django 5.2, Django REST framework, Celery (threads pool), Redis 7, PostgreSQL 16, Gunicorn, Flower.

## Run

```bash
cp .env.example .env
make up
```

| Service | URL |
| --- | --- |
| API | http://localhost:8000/api/v1/notifications/ |
| Swagger | http://localhost:8000/swagger/ |
| Health | http://localhost:8000/health/live and /health/ready |
| Flower | http://localhost:5555 |
| Admin | http://localhost:8000/admin/ |

`make up` builds the image, migrates, and starts the API, two Celery workers, beat, Flower, Postgres, and Redis.

```bash
make logs          # follow logs
make down          # stop
make clean         # stop and delete volumes
```

### Create a notification

```bash
curl -s -X POST http://localhost:8000/api/v1/notifications/ \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "<account-user-uuid>", "type": "sms", "message": "hello"}'
```

`user_id` is the account user's id. `201` returns `{id, status, created_at}`. Poll the status:

```bash
curl -s http://localhost:8000/api/v1/notifications/<notification-uuid>/
```

Statuses are `pending`, `processing`, `sent`, and `failed`. An optional `Idempotency-Key` header returns the original row (`200`) instead of inserting a duplicate.

Operators can list rows (cursor pagination) with `GET /api/v1/notifications/?user_id=&status=&type=`.

The provider is simulated: 50–300 ms of latency and about a 10% transient failure rate. Failures are retried with exponential backoff (3 attempts by default), then marked `failed`.

## Tests

```bash
make test-local
```

The suite uses in-memory SQLite and fakeredis, so Docker is not required. Set `USE_POSTGRES_TESTS=1` to run it against `DATABASE_URL` instead. `make test` runs the same suite inside the API image.

## Load

With the stack running, send a burst across many users and print ingest latency plus how the workers drain it:

```bash
make load
```

Defaults are 8000 notifications, 400 users, and 100 concurrent clients. Override them with `LOAD_REQUESTS`, `LOAD_USERS`, and `LOAD_CONCURRENCY`. Delivery stays one-at-a-time per user, so a small `LOAD_USERS` understates throughput.

## Layout

```
config/                 settings, Celery app, URLs
apps/notifications/     model, API, enqueue, locks, drain loop, sweeper
apps/health/            live and ready probes
apps/common/            Redis client
docs/                   architecture and decisions
```

Design notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## Configuration

Everything is environment-driven. See `.env.example`. The knobs that matter in production are `WEB_CONCURRENCY`, `WORKER_CONCURRENCY`, `WORKER_REPLICAS`, `USER_LOCK_TTL_SECONDS`, and `MAX_ATTEMPTS`. Put PgBouncer in front of Postgres before raising API concurrency further; the app already sets `CONN_MAX_AGE` and a short connect timeout.
