"""Authentication API endpoints.

* ``POST /api/auth/telegram/`` — verify Telegram.WebApp.initData, log in.
* ``POST /api/auth/dev/``      — development-only mock login (never in prod).
* ``POST /api/auth/logout/``   — end the session.
* ``GET  /api/me/``            — current profile (also sets the CSRF cookie).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login, logout
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.models import TelegramUser
from accounts.telegram_auth import TelegramAuthError, verify_init_data

logger = logging.getLogger(__name__)


def _profile_payload(user: TelegramUser) -> dict:
    return {
        "id": user.pk,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "telegram_id": user.telegram_id,
        "language_code": user.telegram_language_code,
        "photo_url": user.telegram_photo_url,
        "is_dev_account": user.telegram_id is None,
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def telegram_login(request: Request) -> Response:
    """Exchange verified Telegram initData for a Django session."""
    init_data = request.data.get("init_data") or request.data.get("initData")
    if not isinstance(init_data, str) or not init_data:
        return Response(
            {"error": {"code": "init_data_missing", "message": "Field 'init_data' is required."}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        identity = verify_init_data(
            init_data,
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            max_age_seconds=settings.TELEGRAM_AUTH_MAX_AGE,
        )
    except TelegramAuthError as exc:
        logger.info("Telegram auth rejected: %s", exc.code)
        return Response(
            {"error": {"code": exc.code, "message": exc.message}},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    user, _created = TelegramUser.objects.update_or_create(
        telegram_id=identity.telegram_id,
        defaults={
            "username": identity.username or f"tg{identity.telegram_id}",
            "first_name": identity.first_name,
            "last_name": identity.last_name,
            "telegram_language_code": identity.language_code,
            "telegram_photo_url": identity.photo_url,
        },
    )
    login(request, user)
    request.session.set_expiry(settings.TELEGRAM_AUTH_MAX_AGE)
    return Response({"user": _profile_payload(user)})


@api_view(["POST"])
@permission_classes([AllowAny])
def dev_login(request: Request) -> Response:
    """Development-only mock identity for ordinary browsers.

    Refused unless ``ALLOW_DEV_AUTH`` is on, which is impossible in
    production (settings abort at import time if attempted).
    """
    if not settings.ALLOW_DEV_AUTH:
        return Response(
            {"error": {"code": "dev_auth_disabled", "message": "Development login is disabled."}},
            status=status.HTTP_403_FORBIDDEN,
        )

    username = request.data.get("username") or "devuser"
    if not isinstance(username, str) or not username.replace("_", "").replace("-", "").isalnum():
        return Response(
            {"error": {"code": "invalid_username", "message": "Username must be alphanumeric."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    username = username[:64]

    user, _created = TelegramUser.objects.get_or_create(
        username=username,
        defaults={"telegram_id": None, "first_name": "Dev"},
    )
    login(request, user)
    return Response({"user": _profile_payload(user)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request: Request) -> Response:
    logout(request)
    return Response({"detail": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request: Request) -> Response:
    return Response({"user": _profile_payload(request.user)})


@api_view(["GET"])
@permission_classes([AllowAny])
def auth_config(_request: Request) -> Response:
    """Tell the frontend which login flows are available (no secrets)."""
    return Response(
        {
            "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN),
            "dev_auth_enabled": settings.ALLOW_DEV_AUTH,
        }
    )
