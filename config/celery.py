import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("shahrzad")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.on_after_configure.connect
def _setup_periodic_tasks(sender: Celery, **_kwargs) -> None:
    from django.conf import settings

    cfg = settings.NOTIFICATIONS
    sender.add_periodic_task(
        cfg["SWEEP_INTERVAL_SECONDS"],
        sender.signature("notifications.sweep_stale"),
        name="sweep stale notifications",
    )
