"""Long-polling bot: inline button callbacks + admin commands."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from palantir.config import get_settings
from palantir.services.ai_service import AIService
from palantir.services.db_service import DBService
from palantir.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


_HELP_TEXT = """\
<b>Команди Palantir</b>

/status — статус бота та статистика за сьогодні
/sources — список джерел (TG канали + RSS)
/report — щотижневий звіт (за останні 7 днів)
/run — запустити pipeline зараз
/next — рекомендація для публікації в канал
/help — ця довідка"""

dp = Dispatcher()


def _admin_only(message: Message) -> bool:
    """Filter: only allow messages from admin."""
    admin_id: int = dp["admin_id"]
    return message.from_user is not None and message.from_user.id == admin_id


# ── Callback handler ────────────────────────────────────────────

@dp.callback_query(F.data.startswith("rate:"))
async def on_rate(callback: CallbackQuery) -> None:
    parts = callback.data.split(":") if callback.data else []
    if len(parts) != 3:
        await callback.answer("❌ Невідома дія")
        return

    _, priority, short_key = parts
    if priority not in ("high", "medium", "low"):
        await callback.answer("❌ Невірний пріоритет")
        return

    db: DBService = dp["db"]
    unique_key = await db.unique_key_by_short(short_key)
    if unique_key is None:
        await callback.answer("❌ Пост не знайдено")
        return

    try:
        await db.save_user_priority(unique_key, priority)
    except ValueError as e:
        await callback.answer(f"❌ {e}")
        return

    labels = {"high": "🟢 Високий", "medium": "🟡 Середній", "low": "🔴 Низький"}
    await callback.answer(f"{labels[priority]} пріоритет збережено!")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("skip:"))
async def on_skip(callback: CallbackQuery) -> None:
    short_key = callback.data.split(":", 1)[1] if callback.data else ""
    db: DBService = dp["db"]

    unique_key = await db.unique_key_by_short(short_key)
    if unique_key is None:
        await callback.answer("❌ Пост не знайдено")
        return

    await db.save_feedback(unique_key, "skip")
    await callback.answer("👎 Відхилено")
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
        text += f"👎 Відхилено: <b>{feedback.get('skip', 0)}</b>"

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


# ── /next command ──────────────────────────────────────────────

@dp.message(Command("next"), F.func(_admin_only))
async def cmd_next(message: Message) -> None:
    db: DBService = dp["db"]
    ai: AIService = dp["ai"]

    candidates = await db.get_unpublished_saved()
    if not candidates:
        await message.answer("📭 Немає збережених постів для публікації.")
        return

    best = candidates[0]
    unique_key = best["unique_key"]
    short_key = DBService.make_short_key(unique_key)

    rewritten = await db.get_rewritten_text(unique_key)
    if not rewritten:
        await message.answer("❌ Не знайдено текст для цього посту.")
        return

    url = (await (await db.conn.execute(
        "SELECT url FROM posts WHERE unique_key = ?", (unique_key,)
    )).fetchone() or (None,))[0] or ""

    await message.answer(
        f"⏳ Генерую пост ({len(candidates)} в черзі)...",
    )

    post_text = await ai.generate_post(rewritten, url)
    if not post_text:
        await message.answer("❌ Не вдалося згенерувати пост. Спробуйте ще раз.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубліковано",
                    callback_data=f"pub:{short_key}",
                ),
                InlineKeyboardButton(
                    text="⏭️ Пропустити",
                    callback_data=f"pskip:{short_key}",
                ),
                InlineKeyboardButton(
                    text="🔄 Перегенерувати",
                    callback_data=f"regen:{short_key}",
                ),
            ]
        ]
    )

    await message.answer(
        post_text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("pub:"))
async def on_publish(callback: CallbackQuery) -> None:
    short_key = callback.data.split(":", 1)[1]
    db: DBService = dp["db"]

    unique_key = await db.unique_key_by_short(short_key)
    if unique_key is None:
        await callback.answer("❌ Пост не знайдено")
        return

    post_text = callback.message.text if callback.message else ""
    await db.mark_published(unique_key, post_text)
    await callback.answer("✅ Позначено як опубліковане!")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("pskip:"))
async def on_pub_skip(callback: CallbackQuery) -> None:
    short_key = callback.data.split(":", 1)[1]
    db: DBService = dp["db"]

    unique_key = await db.unique_key_by_short(short_key)
    if unique_key is None:
        await callback.answer("❌ Пост не знайдено")
        return

    # Mark as published with empty text so it won't appear again
    await db.mark_published(unique_key, "")
    await callback.answer("⏭️ Пропущено")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("regen:"))
async def on_regen(callback: CallbackQuery) -> None:
    short_key = callback.data.split(":", 1)[1]
    db: DBService = dp["db"]
    ai: AIService = dp["ai"]

    unique_key = await db.unique_key_by_short(short_key)
    if unique_key is None:
        await callback.answer("❌ Пост не знайдено")
        return

    rewritten = await db.get_rewritten_text(unique_key)
    if not rewritten:
        await callback.answer("❌ Текст не знайдено")
        return

    url = (await (await db.conn.execute(
        "SELECT url FROM posts WHERE unique_key = ?", (unique_key,)
    )).fetchone() or (None,))[0] or ""

    await callback.answer("🔄 Перегенеровую...")

    post_text = await ai.generate_post(rewritten, url)
    if not post_text:
        if callback.message:
            await callback.message.answer("❌ Не вдалося перегенерувати пост.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубліковано",
                    callback_data=f"pub:{short_key}",
                ),
                InlineKeyboardButton(
                    text="⏭️ Пропустити",
                    callback_data=f"pskip:{short_key}",
                ),
                InlineKeyboardButton(
                    text="🔄 Перегенерувати",
                    callback_data=f"regen:{short_key}",
                ),
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(
            post_text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


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
    ai = AIService(
        api_key=settings.gemini_api_key,
        text_model=settings.gemini_model,
        rpm_limit=settings.ai_rpm_limit,
        score_threshold=settings.score_threshold,
        fallback_api_key=settings.gemini_api_key_2,
        post_gen_model=settings.post_gen_model,
    )

    await db.connect()
    dp["db"] = db
    dp["notifier"] = notifier
    dp["ai"] = ai
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
