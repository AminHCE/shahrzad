from django.db import models

from apps.common.models import TimestampedModel, UniversalModel


class NotificationType(models.TextChoices):
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"
    PUSH = "push", "Push"


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


TERMINAL_STATUSES = frozenset({NotificationStatus.SENT, NotificationStatus.FAILED})


class Notification(UniversalModel, TimestampedModel):
    """One send request. Status changes are conditional updates."""

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    type = models.CharField(max_length=10, choices=NotificationType.choices)
    message = models.TextField()
    status = models.CharField(
        max_length=12, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )

    idempotency_key = models.CharField(max_length=128, null=True, blank=True, unique=True)

    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            # Sweeper looks up pending rows that are old enough to be orphans.
            models.Index(fields=("status", "created_at"), name="notif_status_created_idx"),
            # Sweeper looks up processing rows whose worker stopped.
            models.Index(fields=("status", "processing_started_at"), name="notif_status_proc_idx"),
            # Listing one user, and rebuilding that user's queue in arrival order.
            models.Index(fields=("user_id", "status", "created_at"), name="notif_user_status_idx"),
        ]

    def __str__(self):
        return f"Notification#{self.pk} user={self.user_id} {self.type} {self.status}"

    @property
    def is_terminal(self):
        return self.status in TERMINAL_STATUSES
