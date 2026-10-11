import logging

from django.db import transaction

from apps.notifications.services.locks import user_queue

# ``on_commit`` runs only after the row is visible. If Redis is down here the
# row stays ``pending`` and the sweeper retries, so the request still succeeds.

log = logging.getLogger("notifications.enqueue")


def enqueue_after_commit(notification):
    transaction.on_commit(lambda: enqueue(notification.pk, notification.user_id))


def enqueue(notification_id, user_id):
    """Push onto the user's FIFO. Returns True when this call started a drain."""
    try:
        result = user_queue.enqueue(user_id, notification_id)
        if result.acquired_lock:
            from apps.notifications.services.dispatch import schedule_drain

            schedule_drain(user_id, result.token)
        return result.acquired_lock
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "enqueue failed for notification %s user %s; sweeper will retry: %s",
            notification_id,
            user_id,
            exc,
        )
        return False
