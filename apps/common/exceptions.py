"""Uniform error envelope for the API and graceful degradation for infra failures."""

from __future__ import annotations

import logging

from django.db import DatabaseError, OperationalError
from redis.exceptions import RedisError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger("api")


class ServiceUnavailable(Exception):
    """Raised when a dependency (DB/Redis) is unavailable and the request cannot be accepted."""


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        response.data = {
            "error": {
                "code": _code_for_status(response.status_code),
                "detail": response.data,
            }
        }
        return response

    if isinstance(exc, (ServiceUnavailable, OperationalError, RedisError)):
        log.warning("dependency unavailable: %s", exc)
        return Response(
            {"error": {"code": "service_unavailable", "detail": "Dependency unavailable; retry."}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )

    if isinstance(exc, DatabaseError):
        log.exception("database error")
        return Response(
            {"error": {"code": "database_error", "detail": "Database error."}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )

    return None


def _code_for_status(code: int) -> str:
    return {
        400: "validation_error",
        401: "not_authenticated",
        403: "permission_denied",
        404: "not_found",
        405: "method_not_allowed",
        415: "unsupported_media_type",
        429: "throttled",
    }.get(code, "error")
