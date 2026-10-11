from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView as SimpleJWTTokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from apps.accounts.models import User
from apps.accounts.serializers import RegisterSerializer, UserSerializer

# Public endpoints must not run JWT authentication. An expired Bearer token
# would otherwise reject a valid login or registration body.
_PUBLIC = {"authentication_classes": [], "permission_classes": [AllowAny]}


@extend_schema(tags=["auth"], summary="Create an account and return a JWT pair")
class RegisterView(CreateAPIView):
    serializer_class = RegisterSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except IntegrityError:
            data = serializer.validated_data
            errors = {}
            if User.objects.filter(email=data["email"]).exists():
                errors["email"] = ["A user with this email already exists."]
            phone = data.get("phone")
            if phone and User.objects.filter(phone=phone).exists():
                errors["phone"] = ["A user with this phone number already exists."]
            if not errors:
                errors["email"] = ["A user with this email already exists."]
            raise ValidationError(errors) from None
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["auth"], summary="Exchange email and password for a JWT pair")
class TokenObtainPairView(SimpleJWTTokenObtainPairView):
    authentication_classes = _PUBLIC["authentication_classes"]
    permission_classes = _PUBLIC["permission_classes"]


@extend_schema(tags=["auth"], summary="Rotate a refresh token")
class TokenRefreshView(SimpleJWTTokenRefreshView):
    authentication_classes = _PUBLIC["authentication_classes"]
    permission_classes = _PUBLIC["permission_classes"]


@extend_schema(tags=["auth"], summary="Current user")
class MeView(RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user
