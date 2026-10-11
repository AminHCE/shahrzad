"""Test settings.

The suite defaults to in-memory SQLite so it runs with no infrastructure (the compose ``.env``
points ``DATABASE_URL`` at the docker hostname). Set ``USE_POSTGRES_TESTS=1`` to run against
``DATABASE_URL`` instead. Redis is replaced with an in-process fakeredis server (see
apps/common/redis.py), and Celery executes tasks eagerly.
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False
SECRET_KEY = "test-secret-key"

if not env.bool("USE_POSTGRES_TESTS", default=False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
            "OPTIONS": {"timeout": 20},
        }
    }

USE_FAKE_REDIS = env.bool("USE_FAKE_REDIS", default=True)

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"

NOTIFICATIONS = {
    **NOTIFICATIONS,  # noqa: F405
    "SIMULATED_MIN_LATENCY_MS": 0,
    "SIMULATED_MAX_LATENCY_MS": 1,
    "RETRY_BASE_DELAY_SECONDS": 0.0,
    "RETRY_MAX_DELAY_SECONDS": 0.0,
}

LOGGING["root"]["level"] = "WARNING"  # noqa: F405
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
