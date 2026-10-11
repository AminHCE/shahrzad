import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.tests.fakes import ScriptedProvider
from apps.notifications.utils.provider import register_provider

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture(autouse=True)
def deterministic_providers():
    for channel in ("sms", "email", "push"):
        register_provider(channel, ScriptedProvider(channel))


def test_create_returns_id_and_processes_eagerly(client):
    user = User.objects.create_user(email="ada@example.com")
    resp = client.post(
        reverse("notifications:collection"),
        {"user_id": str(user.id), "type": "sms", "message": "hello"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert set(body) == {"id", "status", "created_at"}

    # Celery runs eagerly in tests, so after the on_commit hook the row is already terminal.
    detail = client.get(reverse("notifications:detail", args=[body["id"]]))
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == NotificationStatus.SENT
    assert data["attempts"] == 1
    assert data["user_id"] == str(user.id)
    assert data["type"] == "sms"
    assert data["message"] == "hello"


_VALID_USER = object()


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"type": "sms", "message": "x"}, "user_id"),
        ({"user_id": "0", "type": "sms", "message": "x"}, "user_id"),
        ({"user_id": _VALID_USER, "type": "fax", "message": "x"}, "type"),
        ({"user_id": _VALID_USER, "type": "sms", "message": ""}, "message"),
        ({"user_id": _VALID_USER, "type": "sms"}, "message"),
        ({"user_id": "abc", "type": "sms", "message": "x"}, "user_id"),
        (
            {"user_id": "00000000-0000-0000-0000-000000000000", "type": "sms", "message": "x"},
            "user_id",
        ),
    ],
)
def test_create_validation_errors(client, payload, field):
    user = User.objects.create_user(email="ada@example.com")
    payload = {
        key: str(user.id) if value is _VALID_USER else value for key, value in payload.items()
    }
    resp = client.post(reverse("notifications:collection"), payload, format="json")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert field in body["error"]["detail"]
    assert Notification.objects.count() == 0


def test_detail_404_envelope(client):
    missing = "00000000-0000-0000-0000-000000000000"
    resp = client.get(reverse("notifications:detail", args=[missing]))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_idempotency_key_returns_existing(client):
    user = User.objects.create_user(email="ada@example.com")
    headers = {"HTTP_IDEMPOTENCY_KEY": "abc-123"}
    payload = {"user_id": str(user.id), "type": "email", "message": "hi"}
    first = client.post(reverse("notifications:collection"), payload, format="json", **headers)
    second = client.post(reverse("notifications:collection"), payload, format="json", **headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert Notification.objects.count() == 1


def test_list_filters_and_paginates(client):
    alice = User.objects.create_user(email="alice@example.com")
    bob = User.objects.create_user(email="bob@example.com")
    for i in range(3):
        client.post(
            reverse("notifications:collection"),
            {"user_id": str(alice.id), "type": "push", "message": f"m{i}"},
            format="json",
        )
    client.post(
        reverse("notifications:collection"),
        {"user_id": str(bob.id), "type": "sms", "message": "other"},
        format="json",
    )

    list_url = reverse("notifications:collection")
    assert list_url == "/api/v1/notifications/"
    resp = client.get(list_url, {"user_id": str(alice.id)})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 3
    assert all(r["user_id"] == str(alice.id) for r in body["results"])

    resp = client.get(reverse("notifications:collection"), {"status": "sent", "type": "sms"})
    assert [r["user_id"] for r in resp.json()["results"]] == [str(bob.id)]

    resp = client.get(reverse("notifications:collection"), {"status": "nope"})
    assert resp.status_code == 400


def test_swagger_and_schema(client):
    ui = client.get("/swagger/")
    assert ui.status_code == 200

    schema = client.get("/api/schema/", {"format": "json"})
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/notifications/" in paths
    assert {"get", "post"} <= set(paths["/api/v1/notifications/"])
    assert any(path.startswith("/api/v1/notifications/") for path in paths)


def test_health_endpoints(client):
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["redis"]["status"] == "ok"
    assert ready.json()["checks"]["database"]["status"] == "ok"
    assert ready.json()["checks"]["broker"]["status"] == "ok"
