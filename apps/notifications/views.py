from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from apps.notifications.models import Notification
from apps.notifications.serializers import (
    NotificationCreatedSerializer,
    NotificationCreateSerializer,
    NotificationDetailSerializer,
    NotificationListFilterSerializer,
)
from apps.notifications.services.enqueue import enqueue_after_commit

IDEMPOTENCY_HEADER = "Idempotency-Key"


class NotificationCursorPagination(CursorPagination):
    ordering = ("-created_at", "-id")
    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"


# ``Idempotency-Key`` returns the original row instead of inserting a duplicate.
# The response is only the new id; the caller polls the detail endpoint for status.
class NotificationCollectionView(ListAPIView):
    serializer_class = NotificationDetailSerializer
    pagination_class = NotificationCursorPagination

    @extend_schema(
        tags=["notifications"],
        summary="List notifications",
        parameters=[NotificationListFilterSerializer],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        filters = NotificationListFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        qs = Notification.objects.all()
        for field, value in filters.validated_data.items():
            if field == "user_id":
                qs = qs.filter(user=value)
            else:
                qs = qs.filter(**{field: value})
        return qs

    @extend_schema(
        tags=["notifications"],
        summary="Accept a notification",
        request=NotificationCreateSerializer,
        responses={201: NotificationCreatedSerializer, 200: NotificationCreatedSerializer},
        parameters=[
            OpenApiParameter(
                name=IDEMPOTENCY_HEADER,
                type=str,
                location=OpenApiParameter.HEADER,
                required=False,
                description="Return the original row instead of creating a duplicate.",
            )
        ],
    )
    def post(self, request, *args, **kwargs):
        serializer = NotificationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.headers.get(IDEMPOTENCY_HEADER) or None

        try:
            with transaction.atomic():
                notification = Notification.objects.create(
                    user=data["user_id"],
                    type=data["type"],
                    message=data["message"],
                    idempotency_key=idempotency_key,
                )
                enqueue_after_commit(notification)
        except IntegrityError:
            if not idempotency_key:
                raise
            existing = Notification.objects.get(idempotency_key=idempotency_key)
            return Response(NotificationCreatedSerializer(existing).data, status=status.HTTP_200_OK)

        return Response(
            NotificationCreatedSerializer(notification).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["notifications"], summary="Notification status")
class NotificationDetailView(RetrieveAPIView):
    serializer_class = NotificationDetailSerializer
    queryset = Notification.objects.all()
