from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from apps.notifications.services import processing, sweeper
from apps.notifications.services.dispatch import schedule_drain


@shared_task(
    name="notifications.drain_user_queue",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def drain_user_queue(self, *, user_id, token):
    try:
        outcome = processing.drain(user_id, token)
    except SoftTimeLimitExceeded:
        # The lock is still held, so hand the rest of the queue to a new task.
        schedule_drain(user_id, token)
        outcome = processing.DrainOutcome.YIELDED
    return outcome.value


@shared_task(name="notifications.sweep_stale", ignore_result=True)
def sweep_stale():
    return sweeper.sweep().as_dict()
