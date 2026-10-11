import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User

# E.164 without separators: optional leading +, then 8–15 digits.
_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def normalize_phone(value):
    if value is None:
        return None
    compact = re.sub(r"[\s\-().]", "", str(value).strip())
    if not compact:
        return None
    if not _PHONE_RE.match(compact):
        raise serializers.ValidationError("Enter a valid phone number in international format.")
    return compact


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "phone", "is_active", "date_joined")
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=32)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        email = User.objects.normalize_email(value)
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate_phone(self, value):
        phone = normalize_phone(value)
        if phone and User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")
        return phone

    def validate(self, attrs):
        try:
            validate_password(attrs["password"], user=User(email=attrs["email"]))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            phone=validated_data.get("phone"),
        )

    def to_representation(self, instance):
        # ``CreateAPIView`` reads ``serializer.data`` twice (headers + body).
        # Issue the pair once so the blacklist stores a single refresh token.
        tokens = getattr(self, "_tokens", None)
        if tokens is None:
            refresh = RefreshToken.for_user(instance)
            tokens = {"access": str(refresh.access_token), "refresh": str(refresh)}
            self._tokens = tokens
        return {
            "id": str(instance.id),
            "email": instance.email,
            "phone": instance.phone,
            **tokens,
        }
