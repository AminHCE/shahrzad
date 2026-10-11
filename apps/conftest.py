from __future__ import annotations

import pytest

from apps.common.redis import get_redis, reset_redis_for_tests
from apps.notifications.services import locks


@pytest.fixture(autouse=True)
def _fresh_redis():
    """Each test gets an empty fakeredis server and fresh script registrations."""
    reset_redis_for_tests()
    locks.user_queue._scripts = {}
    locks.user_queue._scripts_client = None
    yield get_redis()
    reset_redis_for_tests()


@pytest.fixture
def redis_client(_fresh_redis):
    return _fresh_redis


@pytest.fixture
def notification_settings(settings):
    """Mutable copy of settings.NOTIFICATIONS for a test."""
    settings.NOTIFICATIONS = dict(settings.NOTIFICATIONS)
    return settings.NOTIFICATIONS
