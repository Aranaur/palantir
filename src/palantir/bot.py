"""Lightweight long-polling bot that handles inline button callbacks."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery

from palantir.config import get_settings
from palantir.services.db_service import DBService

logger = logging.getLogger(__name__)

_REACTIONS = {
    "save": "📌 Збережено!",
    "skip": "👎 Позначено як нецікаве",
}

dp = Dispatcher()


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

    # Update button text to show selected reaction
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


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

    await db.connect()
    dp["db"] = db

    logger.info("Palantir callback bot started (long-polling)")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()
        logger.info("Palantir callback bot stopped")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
