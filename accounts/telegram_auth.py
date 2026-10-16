"""Server-side verification of Telegram Mini App initData.

Implements the official validation algorithm described in
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Summary:
  * initData is a URL-encoded query string containing ``hash`` plus fields.
  * data_check_string = all fields except ``hash``, sorted, joined by newlines
    as ``key=value`` with values in decoded form.
  * secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token).
  * hash == hex( HMAC_SHA256(key=secret_key, msg=data_check_string) ).

We never trust ``initDataUnsafe`` or a client-supplied Telegram id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class TelegramAuthError(Exception):
    """Raised when initData cannot be trusted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_id: int
    first_name: str
    last_name: str
    username: str
    language_code: str
    photo_url: str
    auth_date: int


def _compute_hash(data_check_string: str, bot_token: str) -> str:
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def parse_init_data(init_data: str) -> dict[str, str]:
    """Parse the raw query-string payload into a plain dict."""
    if not init_data or not isinstance(init_data, str):
        raise TelegramAuthError("init_data_missing", "initData is required.")
    if len(init_data) > 4096:
        raise TelegramAuthError("init_data_too_long", "initData is too long.")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    except ValueError as exc:
        raise TelegramAuthError("init_data_malformed", "initData is malformed.") from exc
    data: dict[str, str] = {}
    for key, value in pairs:
        if key in data:
            raise TelegramAuthError("init_data_malformed", "Duplicate field in initData.")
        data[key] = value
    return data


def verify_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int,
    now: float | None = None,
) -> TelegramIdentity:
    """Verify initData and return the authenticated Telegram identity.

    Raises ``TelegramAuthError`` with a safe code for any failure.
    """
    if not bot_token:
        raise TelegramAuthError("telegram_not_configured", "Telegram bot token is not configured.")

    data = parse_init_data(init_data)

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise TelegramAuthError("hash_missing", "initData hash is missing.")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    expected = _compute_hash(data_check_string, bot_token)
    if not hmac.compare_digest(expected, received_hash.lower()):
        raise TelegramAuthError("invalid_signature", "initData signature is invalid.")

    try:
        auth_date = int(data["auth_date"])
    except (KeyError, ValueError) as exc:
        raise TelegramAuthError("auth_date_invalid", "initData auth_date is missing or invalid.") from exc

    current = time.time() if now is None else now
    if max_age_seconds > 0 and (current - auth_date) > max_age_seconds:
        raise TelegramAuthError("init_data_expired", "initData has expired.")
    if auth_date > current + 300:
        raise TelegramAuthError("auth_date_invalid", "initData auth_date is in the future.")

    raw_user = data.get("user", "")
    try:
        user_payload = json.loads(raw_user) if raw_user else {}
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("user_invalid", "initData user field is invalid.") from exc
    if not isinstance(user_payload, dict):
        raise TelegramAuthError("user_invalid", "initData user field is invalid.")

    try:
        telegram_id = int(user_payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("user_invalid", "initData user id is missing.") from exc

    return TelegramIdentity(
        telegram_id=telegram_id,
        first_name=str(user_payload.get("first_name", ""))[:150],
        last_name=str(user_payload.get("last_name", ""))[:150],
        username=str(user_payload.get("username", ""))[:150],
        language_code=str(user_payload.get("language_code", ""))[:16],
        photo_url=str(user_payload.get("photo_url", ""))[:512],
        auth_date=auth_date,
    )
