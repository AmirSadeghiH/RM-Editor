"""BeatLab Telegram bot.

Launches independently of the web app: ``python -m bot``. If
``TELEGRAM_BOT_TOKEN`` is not configured the process exits cleanly with
setup instructions — the web application keeps working without it.

The bot's job in the MVP is minimal on purpose: greet users, explain the
product, and open the Mini App with a prominent button.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("beatlab.bot")

START_TEXT = (
    "به <b>BeatLab</b> خوش آمدید! 🎛️\n\n"
    "استودیو آنلاین میکس و ریمیکس موسیقی، مستقیماً داخل تلگرام:\n\n"
    "• فایل‌های صوتی‌تان را آپلود کنید\n"
    "• روی یک تایم‌لاین چندترکی بچینید\n"
    "• برش، حجم، محوشدگی و کراس‌فید بدهید\n"
    "• خروجی نهایی را رندر و دانلود کنید\n\n"
    "برای شروع، روی دکمه زیر بزنید."
)

HELP_TEXT = (
    "<b>راهنمای BeatLab</b>\n\n"
    "/start — شروع و معرفی\n"
    "/help — این راهنما\n\n"
    "برای ساخت ریمیکس:\n"
    "۱. ویرایشگر را باز کنید و «پروژه جدید» بسازید\n"
    "۲. فایل صوتی آپلود کنید (حداکثر ۲۵ مگابایت، ۱۰ دقیقه)\n"
    "۳. کلیپ‌ها را روی تایم‌لاین بچینید\n"
    "۴. دکمه «خروجی» را بزنید و فایل نهایی را دانلود کنید\n\n"
    "توجه: فقط فایل‌هایی را آپلود کنید که حق استفاده از آن‌ها را دارید."
)


def build_dispatcher_and_bot():
    """Construct bot + dispatcher. Raises ValueError when unconfigured."""
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    webapp_url = os.environ.get("TELEGRAM_WEBAPP_URL", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set.")
    if not webapp_url:
        raise ValueError("TELEGRAM_WEBAPP_URL is not set.")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    from aiogram import F, Router
    from aiogram.filters import Command, CommandStart
    from aiogram.types import (
        KeyboardButton,
        Message,
        ReplyKeyboardMarkup,
        ReplyKeyboardRemove,
        WebAppInfo,
    )

    router = Router(name="beatlab")

    def open_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🎹 باز کردن استودیو", web_app=WebAppInfo(url=webapp_url))],
                [KeyboardButton(text="📖 راهنما")],
            ],
            resize_keyboard=True,
        )

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(START_TEXT, reply_markup=open_keyboard())

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=open_keyboard())

    @router.message(F.text == "📖 راهنما")
    async def btn_help(message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=open_keyboard())

    @router.message(F.web_app_data)
    async def on_web_app_data(message: Message) -> None:
        # Reserved for future round-trips (e.g. “export finished” pings).
        await message.answer("دریافت شد 👌", reply_markup=ReplyKeyboardRemove())

    dp.include_router(router)
    return bot, dp


async def run() -> int:
    try:
        bot, dp = build_dispatcher_and_bot()
    except ValueError as exc:
        logger.info("Bot not started: %s", exc)
        print(
            "\nBeatLab bot is not configured yet.\n"
            "  1. Create a bot with @BotFather and copy the token.\n"
            "  2. Set TELEGRAM_BOT_TOKEN in your environment / .env.\n"
            "  3. Set TELEGRAM_WEBAPP_URL to your HTTPS Mini App URL\n"
            "     (also configure it on the bot via BotFather → Menu Button).\n"
            "The web application runs fine without the bot.\n"
        )
        return 0

    logger.info("Starting BeatLab bot (long polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
