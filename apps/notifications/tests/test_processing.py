import threading
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.services import processing
from apps.notifications.services.enqueue import enqueue
from apps.notifications.services.locks import lock_key, user_queue
from apps.notifications.services.processing import DrainOutcome
from apps.notifications.tests.fakes import AlwaysFailProvider, ScriptedProvider
from apps.notifications.tests.recipients import recipient
from apps.notifications.utils.provider import (
    ProviderError,
    ProviderPermanentError,
    ProviderTimeout,
    register_provider,
)

pytestmark = pytest.mark.django_db(transaction=True)


def make(user_id=1, type_="sms", message="m", **kwargs):
    return Notification.objects.create(
        user=recipient(user_id), type=type_, message=message, **kwargs
    )


def test_fifo_order_is_preserved_per_user(notification_settings):
    order = []
    provider = ScriptedProvider("sms", hook=lambda uid, msg: order.append(msg))
    register_provider("sms", provider)

    rows = [make(user_id=1, message=f"m{i}") for i in range(10)]
    uid = rows[0].user_id
    # Enqueue in arrival order; the first call starts the (eager) drain, which should still see
    # only item 0 at that point, so push everything first, then drain manually.
    for row in rows:
        user_queue.enqueue(uid, row.pk)
    token = user_queue.client.get(lock_key(uid))

    assert processing.drain(uid, token) is DrainOutcome.RELEASED
    assert order == [f"m{i}" for i in range(10)]
    assert Notification.objects.filter(status=NotificationStatus.SENT).count() == 10
    assert user_queue.length(uid) == 0
    assert not user_queue.is_locked(uid)


def test_at_most_one_in_flight_per_user(notification_settings):
    """Two concurrent drains for the same user: exactly one makes progress."""
    in_flight = 0
    max_in_flight = 0
    guard = threading.Lock()

    def hook(_uid, _msg):
        nonlocal in_flight, max_in_flight
        with guard:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        threading.Event().wait(0.01)
        with guard:
            in_flight -= 1

    register_provider("sms", ScriptedProvider("sms", hook=hook))
    rows = [make(user_id=1, message=f"m{i}") for i in range(20)]
    uid = rows[0].user_id
    for row in rows:
        user_queue.enqueue(uid, row.pk)
    token = user_queue.client.get(lock_key(uid))

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(processing.drain(uid, token))),
        threading.Thread(target=lambda: results.append(processing.drain(uid, "intruder"))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_in_flight == 1
    assert sorted(r.value for r in results) == ["lock_lost", "released"]
    assert Notification.objects.filter(status=NotificationStatus.SENT).count() == 20


def test_transient_failure_retries_then_succeeds(notification_settings):
    provider = ScriptedProvider("sms", outcomes=[ProviderError("x"), ProviderTimeout("t"), True])
    register_provider("sms", provider)
    row = make(user_id=2)

    # Zero backoff in tests => the eager re-dispatch runs inline until the row is terminal.
    assert enqueue(row.pk, row.user_id) is True
    row.refresh_from_db()
    assert row.status == NotificationStatus.SENT
    assert row.attempts == 3
    assert len(provider.calls) == 3
    assert row.last_error == ""
    assert not user_queue.is_locked(row.user_id)
    assert user_queue.length(row.user_id) == 0


def test_attempts_exhausted_marks_failed(notification_settings):
    notification_settings["MAX_ATTEMPTS"] = 3
    provider = AlwaysFailProvider("email")
    register_provider("email", provider)
    row = make(user_id=3, type_="email")

    enqueue(row.pk, row.user_id)
    row.refresh_from_db()
    assert row.status == NotificationStatus.FAILED
    assert row.attempts == 3
    assert row.failed_at is not None
    assert "boom" in row.last_error
    assert len(provider.calls) == 3


def test_permanent_error_fails_immediately(notification_settings):
    register_provider(
        "push", ScriptedProvider("push", outcomes=[ProviderPermanentError("bad token")])
    )
    row = make(user_id=4, type_="push")
    enqueue(row.pk, row.user_id)
    row.refresh_from_db()
    assert row.status == NotificationStatus.FAILED
    assert row.attempts == 1
    assert "bad token" in row.last_error


def test_backoff_keeps_head_of_line_and_reschedules(notification_settings, monkeypatch):
    """With a non-zero backoff the drain must step away (release lock) and schedule a re-drain,
    leaving later items for the same user queued behind the failing one."""
    notification_settings["RETRY_BASE_DELAY_SECONDS"] = 5.0
    notification_settings["RETRY_MAX_DELAY_SECONDS"] = 5.0
    scheduled = []
    monkeypatch.setattr(
        processing,
        "schedule_drain",
        lambda user_id, token, countdown=0: scheduled.append(
            {"user_id": user_id, "countdown": countdown}
        ),
    )
    register_provider("sms", ScriptedProvider("sms", outcomes=[ProviderError("flaky")]))

    first = make(user_id=5, message="first")
    second = make(user_id=5, message="second")
    uid = first.user_id
    user_queue.enqueue(uid, first.pk)
    user_queue.enqueue(uid, second.pk)
    token = user_queue.client.get(lock_key(uid))

    assert processing.drain(uid, token) is DrainOutcome.RESCHEDULED
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == NotificationStatus.PENDING
    assert first.attempts == 1
    assert first.next_attempt_at > timezone.now()
    assert second.status == NotificationStatus.PENDING and second.attempts == 0
    assert user_queue.peek(uid) == str(first.pk)  # still head of line
    assert not user_queue.is_locked(uid)
    assert len(scheduled) == 1 and 0 < scheduled[0]["countdown"] <= 5.0

    # A new drain arriving before the backoff elapsed must step away too, without doubling
    # the scheduled re-drain (retry slot already claimed).
    assert processing.drain(uid, "another") is DrainOutcome.RESCHEDULED
    assert len(scheduled) == 1


def test_processing_leftover_is_reclaimed(notification_settings):
    register_provider("sms", ScriptedProvider("sms"))
    row = make(
        user_id=6,
        status=NotificationStatus.PROCESSING,
        processing_started_at=timezone.now() - timedelta(minutes=10),
        attempts=1,
    )
    user_queue.enqueue(row.user_id, row.pk)
    token = user_queue.client.get(lock_key(row.user_id))
    assert processing.drain(row.user_id, token) is DrainOutcome.RELEASED
    row.refresh_from_db()
    assert row.status == NotificationStatus.SENT
    assert row.attempts == 2


def test_terminal_rows_in_queue_are_skipped(notification_settings):
    provider = ScriptedProvider("sms")
    register_provider("sms", provider)
    done = make(user_id=7, status=NotificationStatus.SENT)
    gone_id = "00000000-0000-0000-0000-000000000000"
    live = make(user_id=7)
    uid = done.user_id
    for nid in (done.pk, gone_id, live.pk):
        user_queue.enqueue(uid, nid)
    token = user_queue.client.get(lock_key(uid))
    assert processing.drain(uid, token) is DrainOutcome.RELEASED
    assert len(provider.calls) == 1
    assert user_queue.length(uid) == 0


def test_yields_after_max_drain_per_task(notification_settings, monkeypatch):
    notification_settings["MAX_DRAIN_PER_TASK"] = 3
    scheduled = []
    monkeypatch.setattr(processing, "schedule_drain", lambda *a, **k: scheduled.append((a, k)))
    register_provider("sms", ScriptedProvider("sms"))
    rows = [make(user_id=8, message=str(i)) for i in range(5)]
    uid = rows[0].user_id
    for row in rows:
        user_queue.enqueue(uid, row.pk)
    token = user_queue.client.get(lock_key(uid))

    assert processing.drain(uid, token) is DrainOutcome.YIELDED
    assert Notification.objects.filter(status=NotificationStatus.SENT).count() == 3
    assert user_queue.length(uid) == 2
    assert user_queue.is_locked(uid)  # lock is carried over to the re-dispatched task
    assert scheduled and scheduled[0][0] == (uid, token)
