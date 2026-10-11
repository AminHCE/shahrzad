from datetime import timedelta

import pytest
from django.utils import timezone

from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.services import sweeper
from apps.notifications.services.locks import queue_key, user_queue
from apps.notifications.tests.fakes import ScriptedProvider
from apps.notifications.tests.recipients import recipient
from apps.notifications.utils.provider import register_provider

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def providers():
    for ch in ("sms", "email", "push"):
        register_provider(ch, ScriptedProvider(ch))


def old(seconds):
    return timezone.now() - timedelta(seconds=seconds)


def create_notification(*, created_at=None, **kwargs):
    """Insert a row, then backdate created_at. auto_now_add ignores a value passed to create()."""
    row = Notification.objects.create(**kwargs)
    if created_at is not None:
        Notification.objects.filter(pk=row.pk).update(created_at=created_at)
        row.created_at = created_at
    return row


def test_orphaned_pending_rows_are_dispatched(notification_settings):
    """Row committed but never enqueued (Redis hiccup): the sweeper rebuilds and dispatches."""
    user = recipient(1)
    a = create_notification(user=user, type="sms", message="a", created_at=old(600))
    b = create_notification(user=user, type="sms", message="b", created_at=old(500))
    fresh = Notification.objects.create(user=user, type="sms", message="fresh")  # within grace

    report = sweeper.sweep()
    assert report.orphan_users == 1
    assert report.users_dispatched == 1
    assert report.ids_requeued == 3  # rebuild includes every pending row for the user, FIFO

    for row in (a, b, fresh):
        row.refresh_from_db()
        assert row.status == NotificationStatus.SENT
    assert not user_queue.is_locked(user.id)


def test_stale_processing_is_reset_or_failed(notification_settings):
    notification_settings["MAX_ATTEMPTS"] = 3
    notification_settings["STALE_PROCESSING_SECONDS"] = 60
    retry = create_notification(
        user=recipient(2),
        type="sms",
        message="r",
        status=NotificationStatus.PROCESSING,
        processing_started_at=old(300),
        attempts=1,
        created_at=old(300),
    )
    dead = create_notification(
        user=recipient(3),
        type="sms",
        message="d",
        status=NotificationStatus.PROCESSING,
        processing_started_at=old(300),
        attempts=3,
        created_at=old(300),
    )
    live = Notification.objects.create(
        user=recipient(4),
        type="sms",
        message="l",
        status=NotificationStatus.PROCESSING,
        processing_started_at=timezone.now(),
        attempts=1,
    )

    report = sweeper.sweep()
    assert report.stale_processing_reset == 1
    assert report.stale_processing_failed == 1

    retry.refresh_from_db()
    dead.refresh_from_db()
    live.refresh_from_db()
    assert retry.status == NotificationStatus.SENT  # reset then eagerly re-dispatched
    assert retry.attempts == 2
    assert dead.status == NotificationStatus.FAILED
    assert live.status == NotificationStatus.PROCESSING  # untouched


def test_sweeper_does_not_steal_from_active_drain(notification_settings, monkeypatch):
    """If a worker holds the lock, the sweeper must not rebuild the queue under its feet."""
    row = create_notification(user=recipient(5), type="sms", message="x", created_at=old(600))
    token = user_queue.try_acquire(row.user_id)
    assert token
    user_queue.enqueue(row.user_id, row.pk)  # already queued by the active path

    calls = []
    monkeypatch.setattr(sweeper, "schedule_drain", lambda *a, **k: calls.append(a))
    report = sweeper.sweep()
    assert report.users_dispatched == 0
    assert calls == []
    assert user_queue.length(row.user_id) == 1


def test_sweeper_pushes_missing_ids_for_active_user(notification_settings, monkeypatch):
    user = recipient(6)
    missing = create_notification(user=user, type="sms", message="x", created_at=old(600))
    queued = create_notification(user=user, type="sms", message="y", created_at=old(590))
    user_queue.try_acquire(user.id)
    user_queue.enqueue(user.id, queued.pk)

    monkeypatch.setattr(sweeper, "schedule_drain", lambda *a, **k: None)
    report = sweeper.sweep()
    assert report.ids_requeued == 1
    assert user_queue.peek(user.id) == str(missing.pk)  # older one goes to the head


def test_sweeper_appends_missing_newer_ids(notification_settings, monkeypatch):
    """A lost newer row must not jump ahead of work the active worker already has queued."""
    user = recipient(9)
    older = create_notification(user=user, type="sms", message="old", created_at=old(600))
    newer = create_notification(user=user, type="sms", message="new", created_at=old(590))
    user_queue.try_acquire(user.id)
    user_queue.enqueue(user.id, older.pk)

    monkeypatch.setattr(sweeper, "schedule_drain", lambda *a, **k: None)
    report = sweeper.sweep()
    assert report.ids_requeued == 1
    assert user_queue.peek(user.id) == str(older.pk)
    assert user_queue.client.lrange(queue_key(user.id), 0, -1) == [str(older.pk), str(newer.pk)]


def test_rows_in_backoff_are_not_considered_orphaned(notification_settings, monkeypatch):
    create_notification(
        user=recipient(7),
        type="sms",
        message="x",
        created_at=old(600),
        next_attempt_at=timezone.now() + timedelta(seconds=30),
        attempts=1,
    )
    calls = []
    monkeypatch.setattr(sweeper, "schedule_drain", lambda *a, **k: calls.append(a))
    report = sweeper.sweep()
    assert report.orphan_users == 0
    assert calls == []
