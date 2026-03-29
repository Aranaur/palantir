"""Long-polling bot: inline button callbacks + admin commands."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from palantir.config import get_settings
from palantir.services.db_service import DBService
from palantir.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

_REACTIONS = {
    "save": "📌 Збережено!",
    "skip": "👎 Позначено як нецікаве",
}

_HELP_TEXT = """\
<b>Команди Palantir</b>

/status — статус бота та статистика за сьогодні
/sources — список джерел (TG канали + RSS)
/report — щотижневий звіт (за останні 7 днів)
/run — запустити pipeline зараз
/help — ця довідка"""

dp = Dispatcher()


def _admin_only(message: Message) -> bool:
    """Filter: only allow messages from admin."""
    admin_id: int = dp["admin_id"]
    return message.from_user is not None and message.from_user.id == admin_id


# ── Callback handler ────────────────────────────────────────────

@dp.callback_query()
async def on_reaction(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 1) if callback.data else []
    if len(parts) != 2 or parts[0] not in _REACTIONS:
        await callback.answer("❌ Невідома дія")
        return

    action, unique_key = parts
    db: DBService = dp["db"]

    await db.save_feedback(unique_key, action)
    await callback.answer(_REACTIONS[action])

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


# ── Commands ────────────────────────────────────────────────────

@dp.message(Command("help", "start"), F.func(_admin_only))
async def cmd_help(message: Message) -> None:
    await message.answer(_HELP_TEXT, parse_mode="HTML")


@dp.message(Command("status"), F.func(_admin_only))
async def cmd_status(message: Message) -> None:
    db: DBService = dp["db"]
    stats = await db.weekly_stats(days=1)

    text = (
        "📡 <b>Статус Palantir</b>\n\n"
        f"📥 Постів за сьогодні: <b>{stats['total_seen']}</b>\n"
        f"📤 Рекомендацій: <b>{stats['total_sent']}</b>\n"
    )

    feedback = stats.get("feedback", {})
    if feedback:
        text += (
            f"📌 Збережено: <b>{feedback.get('save', 0)}</b> · "
            f"👎 Пропущено: <b>{feedback.get('skip', 0)}</b>"
        )

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("sources"), F.func(_admin_only))
async def cmd_sources(message: Message) -> None:
    settings = get_settings()

    lines = ["<b>Джерела</b>\n"]

    if settings.tg_channels:
        lines.append(f"<b>Telegram ({len(settings.tg_channels)}):</b>")
        for ch in settings.tg_channels:
            lines.append(f"  — {ch}")

    if settings.rss_feeds:
        lines.append(f"\n<b>RSS ({len(settings.rss_feeds)}):</b>")
        for feed in settings.rss_feeds:
            # Shorten URL for readability
            short = feed.replace("https://", "").replace("http://", "")
            if len(short) > 50:
                short = short[:47] + "..."
            lines.append(f"  — {short}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("report"), F.func(_admin_only))
async def cmd_report(message: Message) -> None:
    db: DBService = dp["db"]
    notifier: NotificationService = dp["notifier"]

    stats = await db.weekly_stats(days=7)
    await notifier.send_weekly_report(stats)


@dp.message(Command("run"), F.func(_admin_only))
async def cmd_run(message: Message) -> None:
    await message.answer("⏳ Запускаю pipeline...")

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "palantir.main",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        await proc.wait()

        if proc.returncode == 0:
            await message.answer("✅ Pipeline завершено")
        else:
            await message.answer(f"❌ Pipeline завершено з помилкою (код {proc.returncode})")
    except Exception as exc:
        await message.answer(f"❌ Помилка запуску: {exc}")


# ── Entry point ─────────────────────────────────────────────────

async def _async_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    settings = get_settings()
    bot = Bot(token=settings.bot_token)
    db = DBService(db_path=settings.db_path)
    notifier = NotificationService(
        bot_token=settings.bot_token,
        admin_id=settings.admin_id,
    )

    await db.connect()
    dp["db"] = db
    dp["notifier"] = notifier
    dp["admin_id"] = settings.admin_id

    logger.info("Palantir bot started (long-polling)")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await notifier.close()
        await bot.session.close()
        logger.info("Palantir bot stopped")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
