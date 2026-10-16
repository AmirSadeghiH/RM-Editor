"""Telegram initData verification and dev-auth safety tests."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from django.conf import settings
from django.test import override_settings

from accounts.telegram_auth import TelegramAuthError, verify_init_data

BOT_TOKEN = "123456:TEST-TOKEN-abcdef"


def make_init_data(
    *,
    bot_token: str = BOT_TOKEN,
    auth_date: float | None = None,
    user: dict | None = None,
    tamper: str | None = None,
) -> str:
    """Build a signed initData query string exactly like Telegram does."""
    auth_date = int(time.time()) if auth_date is None else int(auth_date)
    user = user if user is not None else {
        "id": 777,
        "first_name": "Test",
        "last_name": "User",
        "username": "tester",
        "language_code": "fa",
    }
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAF_test",
        "user": json.dumps(user, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if tamper == "hash":
        digest = "0" * 64
    elif tamper == "user":
        fields["user"] = json.dumps({**user, "id": 999}, separators=(",", ":"))
    return urlencode({**fields, "hash": digest})


class TestVerifyInitData:
    def test_valid_init_data(self):
        identity = verify_init_data(make_init_data(), BOT_TOKEN, 3600)
        assert identity.telegram_id == 777
        assert identity.username == "tester"

    def test_invalid_signature(self):
        with pytest.raises(TelegramAuthError) as exc:
            verify_init_data(make_init_data(tamper="hash"), BOT_TOKEN, 3600)
        assert exc.value.code == "invalid_signature"

    def test_tampered_user_rejected(self):
        with pytest.raises(TelegramAuthError) as exc:
            verify_init_data(make_init_data(tamper="user"), BOT_TOKEN, 3600)
        assert exc.value.code == "invalid_signature"

    def test_expired_init_data(self):
        old = make_init_data(auth_date=time.time() - 7200)
        with pytest.raises(TelegramAuthError) as exc:
            verify_init_data(old, BOT_TOKEN, 3600)
        assert exc.value.code == "init_data_expired"

    def test_future_auth_date_rejected(self):
        future = make_init_data(auth_date=time.time() + 3600)
        with pytest.raises(TelegramAuthError):
            verify_init_data(future, BOT_TOKEN, 3600)

    def test_missing_hash(self):
        raw = make_init_data().split("&hash=")[0]
        with pytest.raises(TelegramAuthError):
            verify_init_data(raw, BOT_TOKEN, 3600)

    def test_empty_init_data(self):
        with pytest.raises(TelegramAuthError):
            verify_init_data("", BOT_TOKEN, 3600)

    def test_wrong_bot_token(self):
        with pytest.raises(TelegramAuthError) as exc:
            verify_init_data(make_init_data(), "999:OTHER-TOKEN", 3600)
        assert exc.value.code == "invalid_signature"

    def test_no_bot_token_configured(self):
        with pytest.raises(TelegramAuthError) as exc:
            verify_init_data(make_init_data(), "", 3600)
        assert exc.value.code == "telegram_not_configured"


class TestAuthEndpoints:
    def test_telegram_login_creates_session(self, db):
        from rest_framework.test import APIClient

        client = APIClient()
        with override_settings(TELEGRAM_BOT_TOKEN=BOT_TOKEN):
            response = client.post(
                "/api/auth/telegram/",
                {"init_data": make_init_data()},
                format="json",
            )
        assert response.status_code == 200, response.content
        assert response.json()["user"]["telegram_id"] == 777

        me = client.get("/api/me/")
        assert me.status_code == 200
        assert me.json()["user"]["username"] == "tester"

    def test_telegram_login_rejects_bad_signature(self, db):
        from rest_framework.test import APIClient

        client = APIClient()
        with override_settings(TELEGRAM_BOT_TOKEN=BOT_TOKEN):
            response = client.post(
                "/api/auth/telegram/",
                {"init_data": make_init_data(tamper="hash")},
                format="json",
            )
        assert response.status_code == 401

    def test_dev_login_enabled(self, db):
        from rest_framework.test import APIClient

        client = APIClient()
        response = client.post("/api/auth/dev/", {"username": "devperson"}, format="json")
        assert response.status_code == 200
        assert response.json()["user"]["is_dev_account"] is True

    def test_dev_login_rejected_when_disabled(self, db):
        from rest_framework.test import APIClient

        client = APIClient()
        with override_settings(ALLOW_DEV_AUTH=False):
            response = client.post("/api/auth/dev/", {"username": "devperson"}, format="json")
        assert response.status_code == 403

    def test_dev_login_username_validation(self, db):
        from rest_framework.test import APIClient

        client = APIClient()
        response = client.post("/api/auth/dev/", {"username": "../etc/passwd"}, format="json")
        assert response.status_code == 400

    def test_me_requires_auth(self, db):
        from rest_framework.test import APIClient

        response = APIClient().get("/api/me/")
        assert response.status_code == 403  # session auth → 403 w/ CSRF-style deny


def test_production_settings_refuse_dev_auth():
    """Importing settings with production + dev auth must fail fast."""
    import importlib
    import os

    from django.conf import settings as django_settings

    old_env = os.environ.get("DJANGO_ENV")
    old_secret = os.environ.get("DJANGO_SECRET_KEY")
    old_dev = os.environ.get("BEATLAB_DEV_AUTH")
    try:
        os.environ["DJANGO_ENV"] = "production"
        os.environ["DJANGO_SECRET_KEY"] = "x" * 48
        os.environ["BEATLAB_DEV_AUTH"] = "1"
        # Re-exec the settings module body in a scratch namespace.
        import beatlab.settings as settings_module

        with pytest.raises(RuntimeError):
            importlib.reload(settings_module)
    finally:
        # Restore environment and settings module state for other tests.
        if old_env is None:
            os.environ.pop("DJANGO_ENV", None)
        else:
            os.environ["DJANGO_ENV"] = old_env
        if old_secret is None:
            os.environ.pop("DJANGO_SECRET_KEY", None)
        else:
            os.environ["DJANGO_SECRET_KEY"] = old_secret
        if old_dev is None:
            os.environ.pop("BEATLAB_DEV_AUTH", None)
        else:
            os.environ["BEATLAB_DEV_AUTH"] = old_dev
        os.environ["BEATLAB_DEV_AUTH"] = "1"
        os.environ["DJANGO_ENV"] = "development"
        importlib.reload(settings_module)
        assert django_settings.ALLOW_DEV_AUTH is True
