import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "s3cure-pass"


@pytest.fixture
def client():
    return APIClient()


def _register(client, email="ada@example.com", password=PASSWORD, **extra):
    return client.post(
        reverse("accounts:register"),
        {"email": email, "password": password, **extra},
        format="json",
    )


def test_register_returns_user_and_tokens(client):
    resp = _register(client, email="Ada@Example.com")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert set(body) == {"id", "email", "phone", "access", "refresh"}
    assert body["email"] == "ada@example.com"
    assert body["phone"] is None
    assert body["access"]
    assert body["refresh"]

    user = User.objects.get(email="ada@example.com")
    assert str(user.id) == body["id"]
    assert user.check_password(PASSWORD)
    assert not user.password.startswith(PASSWORD)


def test_register_rejects_duplicate_and_weak_password(client):
    assert _register(client).status_code == 201

    duplicate = _register(client, email="ADA@example.com")
    assert duplicate.status_code == 400
    assert "email" in duplicate.json()["error"]["detail"]

    weak = _register(client, email="other@example.com", password="123")
    assert weak.status_code == 400
    assert "password" in weak.json()["error"]["detail"]
    assert User.objects.filter(email="other@example.com").count() == 0


def test_token_login_and_me(client):
    assert _register(client).status_code == 201

    token = client.post(
        reverse("accounts:token_obtain_pair"),
        {"email": "ada@example.com", "password": PASSWORD},
        format="json",
    )
    assert token.status_code == 200, token.content
    access = token.json()["access"]

    anon = client.get(reverse("accounts:me"))
    assert anon.status_code == 401
    assert anon.json()["error"]["code"] == "not_authenticated"

    me = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {access}")
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"
    assert me.json()["phone"] is None


def test_register_stores_normalized_phone(client):
    resp = _register(client, phone="+1 (202) 555-0143")
    assert resp.status_code == 201, resp.content
    assert resp.json()["phone"] == "+12025550143"
    assert User.objects.get(email="ada@example.com").phone == "+12025550143"

    duplicate = _register(client, email="other@example.com", phone="+1 202 555 0143")
    assert duplicate.status_code == 400
    assert "phone" in duplicate.json()["error"]["detail"]

    invalid = _register(client, email="third@example.com", phone="abc")
    assert invalid.status_code == 400
    assert "phone" in invalid.json()["error"]["detail"]


def test_inactive_user_cannot_obtain_token(client):
    user = User.objects.create_user(email="ada@example.com", password=PASSWORD)
    user.is_active = False
    user.save(update_fields=["is_active"])

    token = client.post(
        reverse("accounts:token_obtain_pair"),
        {"email": "ada@example.com", "password": PASSWORD},
        format="json",
    )
    assert token.status_code == 401
    assert client.get(reverse("accounts:me")).status_code == 401


def test_refresh_rotates_and_blacklists_the_previous_token(client):
    registered = _register(client)
    refresh = registered.json()["refresh"]

    rotated = client.post(
        reverse("accounts:token_refresh"),
        {"refresh": refresh},
        format="json",
    )
    assert rotated.status_code == 200, rotated.content
    assert rotated.json()["refresh"] != refresh
    assert rotated.json()["access"]

    reused = client.post(
        reverse("accounts:token_refresh"),
        {"refresh": refresh},
        format="json",
    )
    assert reused.status_code == 401
