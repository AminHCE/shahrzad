import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.services.dispatch import schedule_drain
from apps.notifications.services.locks import user_queue

log = logging.getLogger("notifications.sweeper")

# Picks up rows the fast path lost: a dead worker, a missed enqueue, or a
# Redis list that disappeared. Safe to run next to live traffic.

BATCH_ROWS = 2000
MAX_USERS_PER_SWEEP = 500
REBUILD_LIMIT = 5000


@dataclass
class SweepReport:
    stale_processing_reset: int = 0
    stale_processing_failed: int = 0
    orphan_users: int = 0
    users_dispatched: int = 0
    ids_requeued: int = 0
    users: set = field(default_factory=set)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if k != "users"}


def sweep():
    cfg = settings.NOTIFICATIONS
    now = timezone.now()
    report = SweepReport()

    _reset_stale_processing(now, cfg, report)
    _find_orphaned_pending(now, cfg, report)

    for user_id in list(report.users)[:MAX_USERS_PER_SWEEP]:
        _ensure_user_dispatched(user_id, report)

    if report.users:
        log.info("sweep completed: %s", report.as_dict())
    return report


def _reset_stale_processing(now, cfg, report):
    cutoff = now - timedelta(seconds=cfg["STALE_PROCESSING_SECONDS"])
    stale = list(
        Notification.objects.filter(
            status=NotificationStatus.PROCESSING, processing_started_at__lt=cutoff
        ).values_list("id", "user_id", "attempts")[:BATCH_ROWS]
    )
    if not stale:
        return

    # ``attempts`` was incremented when the row was claimed, so a crash loop is bounded.
    exhausted = [i for i, _u, a in stale if a >= cfg["MAX_ATTEMPTS"]]
    retryable = [i for i, _u, a in stale if a < cfg["MAX_ATTEMPTS"]]

    if exhausted:
        report.stale_processing_failed += Notification.objects.filter(
            id__in=exhausted, status=NotificationStatus.PROCESSING
        ).update(
            status=NotificationStatus.FAILED,
            failed_at=now,
            last_error="worker lost mid-send; attempts exhausted",
        )
    if retryable:
        report.stale_processing_reset += Notification.objects.filter(
            id__in=retryable, status=NotificationStatus.PROCESSING
        ).update(
            status=NotificationStatus.PENDING,
            next_attempt_at=None,
            last_error="worker lost mid-send; re-queued",
        )

    report.users.update(u for _i, u, _a in stale)


def _find_orphaned_pending(now, cfg, report):
    # Give the fast path two lock leases before calling a row orphaned.
    grace = max(2 * cfg["USER_LOCK_TTL_SECONDS"], cfg["SWEEP_INTERVAL_SECONDS"])
    cutoff = now - timedelta(seconds=grace)
    user_ids = (
        Notification.objects.filter(status=NotificationStatus.PENDING, created_at__lt=cutoff)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()[:MAX_USERS_PER_SWEEP]
    )
    found = set(user_ids)
    report.orphan_users += len(found)
    report.users.update(found)


def _ensure_user_dispatched(user_id, report):
    pending_ids = list(
        Notification.objects.filter(user_id=user_id, status=NotificationStatus.PENDING)
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:REBUILD_LIMIT]
    )
    if not pending_ids:
        return

    token = user_queue.try_acquire(user_id)
    if token is not None:
        # Nobody is draining this user: rebuild the FIFO from the database and start a task.
        user_queue.rebuild(user_id, pending_ids)
        schedule_drain(user_id, token)
        report.users_dispatched += 1
        report.ids_requeued += len(pending_ids)
        return

    # A worker is already draining this user. Only fill ids the list is missing,
    # and keep arrival order so an older lost row is not appended behind a newer one.
    if user_queue.length(user_id) >= len(pending_ids):
        return
    report.ids_requeued += user_queue.merge_missing(user_id, pending_ids)
