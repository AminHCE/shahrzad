from rest_framework import serializers

from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationStatus, NotificationType

MAX_MESSAGE_LENGTH = 4000


class NotificationCreateSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    type = serializers.ChoiceField(choices=NotificationType.choices)
    message = serializers.CharField(
        max_length=MAX_MESSAGE_LENGTH, allow_blank=False, trim_whitespace=False
    )


class NotificationCreatedSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "status", "created_at")
        read_only_fields = fields


class NotificationDetailSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "user_id",
            "type",
            "message",
            "status",
            "attempts",
            "last_error",
            "next_attempt_at",
            "created_at",
            "updated_at",
            "processing_started_at",
            "sent_at",
            "failed_at",
        )
        read_only_fields = fields


class NotificationListFilterSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    status = serializers.ChoiceField(choices=NotificationStatus.choices, required=False)
    type = serializers.ChoiceField(choices=NotificationType.choices, required=False)
