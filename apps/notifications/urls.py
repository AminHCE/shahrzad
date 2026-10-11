from django.urls import path

from apps.notifications.views import NotificationCollectionView, NotificationDetailView

app_name = "notifications"

urlpatterns = [
    path("notifications/", NotificationCollectionView.as_view(), name="collection"),
    path("notifications/<uuid:pk>/", NotificationDetailView.as_view(), name="detail"),
]
