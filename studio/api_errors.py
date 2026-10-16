"""Consistent API error payloads.

Every error response has the shape::

    {"error": {"code": "snake_case", "message": "Human readable.", "details": [...]}}

Messages are safe for clients: no filesystem paths, no tracebacks.
"""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from studio.timeline import TimelineValidationError

logger = logging.getLogger(__name__)


def _flatten(detail: object) -> list[str]:
    if isinstance(detail, dict):
        out: list[str] = []
        for key, value in detail.items():
            out.extend(f"{key}: {item}" for item in _flatten(value))
        return out
    if isinstance(detail, (list, tuple)):
        out = []
        for item in detail:
            out.extend(_flatten(item))
        return out
    return [str(detail)]


def beatlab_exception_handler(exc: object, context: dict) -> Response | None:
    if isinstance(exc, TimelineValidationError):
        return Response(
            {
                "error": {
                    "code": "invalid_timeline",
                    "message": "Timeline validation failed.",
                    "details": exc.errors,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = exception_handler(exc, context)  # type: ignore[arg-type]
    if response is None:
        return None

    code = "error"
    if isinstance(exc, Exception):
        code = getattr(exc, "default_code", None) or getattr(exc, "code", None) or exc.__class__.__name__
        code = str(code).lower().replace(" ", "_")

    detail = response.data
    message = "Request failed."
    details: list[str] = []
    if isinstance(detail, dict) and set(detail.keys()) & {"detail", "non_field_errors"}:
        message = str(next(iter(detail.values())))
        if isinstance(message, list):
            message = str(message[0]) if message else "Request failed."
        details = _flatten(detail)
    elif isinstance(detail, dict):
        message = "Validation failed."
        details = _flatten(detail)
    else:
        details = _flatten(detail)

    response.data = {
        "error": {
            "code": code,
            "message": message[:500],
            "details": details[:50],
        }
    }
    return response
