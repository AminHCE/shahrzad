from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_id",
        "type",
        "status",
        "attempts",
        "created_at",
        "sent_at",
        "failed_at",
    )
    list_filter = ("status", "type")
    search_fields = ("=id", "=user_id", "idempotency_key")
    readonly_fields = tuple(f.name for f in Notification._meta.fields)
    ordering = ("-created_at",)
    list_per_page = 100

    def has_add_permission(self, request):
        return False
