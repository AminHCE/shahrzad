import enum
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.db.models import F
from django.utils import timezone
from redis.exceptions import RedisError

from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.services.backoff import retry_delay
from apps.notifications.services.dispatch import schedule_drain
from apps.notifications.services.locks import new_token, user_queue
from apps.notifications.utils.provider import (
    ProviderError,
    ProviderPermanentError,
    get_provider,
)

log = logging.getLogger("notifications.processing")

# A transient failure stays at the head of that user's queue, so the rest of
# the queue waits out the backoff.


class DrainOutcome(enum.StrEnum):
    RELEASED = "released"  # queue empty, lock released
    LOCK_LOST = "lock_lost"  # another worker owns the user; nothing to do
    RESCHEDULED = "rescheduled"  # stepped away for backoff, re-drain scheduled
    YIELDED = "yielded"  # hit MAX_DRAIN_PER_TASK, re-dispatched self
    INFRA_ERROR = "infra_error"  # DB/Redis failure, left for lease expiry + sweeper


class StepKind(enum.Enum):
    DONE = "done"  # reached a terminal state, pop and continue
    SKIP = "skip"  # row gone or already terminal, pop and continue
    WAIT = "wait"  # head is in backoff, step away for `delay`


@dataclass(frozen=True)
class Step:
    kind: StepKind
    delay: float = 0.0


def _cfg():
    return settings.NOTIFICATIONS


def drain(user_id, token):
    q = user_queue
    try:
        if not q.acquire_or_refresh(user_id, token):
            return DrainOutcome.LOCK_LOST

        processed = 0
        max_per_task = int(_cfg()["MAX_DRAIN_PER_TASK"])

        while True:
            if processed >= max_per_task:
                schedule_drain(user_id, token)
                return DrainOutcome.YIELDED

            notification_id = q.peek(user_id)
            if notification_id is None:
                verdict = q.release_if_empty(user_id, token)
                if verdict == 1:
                    return DrainOutcome.RELEASED
                if verdict == -1:
                    return DrainOutcome.LOCK_LOST
                continue  # something arrived between peek and release

            if not q.refresh(user_id, token):
                return DrainOutcome.LOCK_LOST

            step = process_one(notification_id, user_id)

            if step.kind in (StepKind.DONE, StepKind.SKIP):
                q.pop_if_head(user_id, notification_id)
                processed += 1
                continue

            # WAIT: step away so the worker slot is freed, come back later.
            q.release(user_id, token)
            if q.claim_retry_slot(user_id, step.delay):
                schedule_drain(user_id, new_token(), countdown=step.delay)
            return DrainOutcome.RESCHEDULED

    except (DatabaseError, RedisError) as exc:
        # Do not clean up through the same broken dependency. The lease expires
        # and the sweeper re-dispatches from the database.
        log.warning("drain failed for user %s: %s", user_id, exc)
        return DrainOutcome.INFRA_ERROR


def process_one(notification_id, user_id):
    now = timezone.now()
    row = Notification.objects.filter(pk=notification_id).first()
    if row is None or row.is_terminal:
        return Step(StepKind.SKIP)

    if row.status == NotificationStatus.PROCESSING:
        # We hold the user's lock, so this is a leftover from a worker that
        # died mid-send. Reclaim it.
        Notification.objects.filter(
            pk=notification_id, status=NotificationStatus.PROCESSING
        ).update(status=NotificationStatus.PENDING, last_error="reclaimed after worker loss")
        row.refresh_from_db()
        if row.is_terminal:
            return Step(StepKind.SKIP)

    if row.next_attempt_at and row.next_attempt_at > now:
        return Step(StepKind.WAIT, (row.next_attempt_at - now).total_seconds())

    # Backoff has elapsed, so the slot that scheduled this drain can be reused.
    user_queue.clear_retry_slot(user_id)

    if row.attempts >= int(_cfg()["MAX_ATTEMPTS"]):
        _mark_failed(row, reason="exhausted", error=row.last_error or "attempts exhausted")
        return Step(StepKind.DONE)

    claimed = Notification.objects.filter(
        pk=notification_id, status=NotificationStatus.PENDING
    ).update(
        status=NotificationStatus.PROCESSING,
        processing_started_at=now,
        attempts=F("attempts") + 1,
        next_attempt_at=None,
    )
    if not claimed:
        row.refresh_from_db()
        return Step(StepKind.SKIP) if row.is_terminal else Step(StepKind.WAIT, 1.0)

    attempt = row.attempts + 1
    try:
        provider = get_provider(row.type)
        provider.send(
            user_id=user_id,
            message=row.message,
            timeout=float(_cfg()["PROVIDER_TIMEOUT_SECONDS"]),
        )
    except ProviderPermanentError as exc:
        _mark_failed(row, reason="permanent", error=str(exc))
        return Step(StepKind.DONE)
    except ProviderError as exc:
        return _handle_transient_failure(row, attempt, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("provider failed for notification %s", row.pk)
        return _handle_transient_failure(row, attempt, f"{type(exc).__name__}: {exc}")
    else:
        _mark_sent(row)
        return Step(StepKind.DONE)


def _handle_transient_failure(row, attempt, error):
    if attempt >= int(_cfg()["MAX_ATTEMPTS"]):
        _mark_failed(row, reason="exhausted", error=error)
        return Step(StepKind.DONE)

    delay = retry_delay(attempt)
    Notification.objects.filter(pk=row.pk, status=NotificationStatus.PROCESSING).update(
        status=NotificationStatus.PENDING,
        next_attempt_at=timezone.now() + timedelta(seconds=delay),
        last_error=error[:2000],
    )
    log.warning(
        "retry notification %s (%s) attempt %s in %.3fs: %s",
        row.pk,
        row.type,
        attempt,
        delay,
        error,
    )
    return Step(StepKind.WAIT, delay)


def _mark_sent(row):
    now = timezone.now()
    Notification.objects.filter(pk=row.pk, status=NotificationStatus.PROCESSING).update(
        status=NotificationStatus.SENT, sent_at=now, last_error="", next_attempt_at=None
    )
    log.info("sent notification %s (%s)", row.pk, row.type)


def _mark_failed(row, *, reason, error):
    now = timezone.now()
    Notification.objects.filter(pk=row.pk).exclude(status=NotificationStatus.SENT).update(
        status=NotificationStatus.FAILED,
        failed_at=now,
        last_error=error[:2000],
        next_attempt_at=None,
    )
    log.error("notification %s (%s) failed (%s): %s", row.pk, row.type, reason, error)
