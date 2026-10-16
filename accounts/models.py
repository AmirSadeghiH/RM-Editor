"""User model.

The project uses a single custom user model. Regular browser logins (the
development bypass) and Telegram-authenticated Mini App users share this
table; Telegram identity is optional so the model works before a bot token
exists.
"""
from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class TelegramUser(AbstractUser):
    """Application user keyed by Telegram identity when available."""

    telegram_id = models.BigIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Unique Telegram user id. Null for development accounts.",
    )
    telegram_language_code = models.CharField(max_length=16, blank=True, default="")
    telegram_photo_url = models.URLField(max_length=512, blank=True, default="")

    def __str__(self) -> str:  # pragma: no cover - debug helper
        label = self.username or self.first_name or f"user-{self.pk}"
        return f"{label} (tg={self.telegram_id})" if self.telegram_id else label
