from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "telegram_id", "is_staff", "date_joined")
    search_fields = ("username", "first_name", "telegram_id")
    fieldsets = UserAdmin.fieldsets + (
        ("Telegram", {"fields": ("telegram_id", "telegram_language_code", "telegram_photo_url")}),
    )
