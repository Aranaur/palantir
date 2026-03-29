"""Standalone script to send the weekly report."""

from __future__ import annotations

import asyncio
import logging
import sys

from palantir.config import get_settings
from palantir.services.db_service import DBService
from palantir.services.notification_service import NotificationService


async def _async_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger(__name__)
    logger.info("Generating weekly report...")

    settings = get_settings()
    db = DBService(db_path=settings.db_path)
    notifier = NotificationService(
        bot_token=settings.bot_token,
        admin_id=settings.admin_id,
    )

    try:
        await db.connect()
        stats = await db.weekly_stats(days=7)
        await notifier.send_weekly_report(stats)
        logger.info("Weekly report sent successfully")
    finally:
        await notifier.close()
        await db.close()


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
