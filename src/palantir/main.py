from __future__ import annotations

import asyncio
import logging
import sys

from palantir.config import get_settings
from palantir.pipeline import Pipeline
from palantir.services.ai_service import AIService
from palantir.services.db_service import DBService
from palantir.services.notification_service import NotificationService
from palantir.services.scraper_service import ScraperService


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def _async_main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Palantir starting up...")

    settings = get_settings()

    # --- Initialize services ---
    db = DBService(db_path=settings.db_path)
    scraper = ScraperService(
        tg_api_id=settings.tg_api_id,
        tg_api_hash=settings.tg_api_hash,
        tg_session_name=settings.tg_session_name,
        tg_channels=settings.tg_channels,
        rss_feeds=settings.rss_feeds,
        scrape_limit=settings.scrape_limit,
    )
    ai = AIService(
        api_key=settings.gemini_api_key,
        text_model=settings.gemini_model,
        rpm_limit=settings.ai_rpm_limit,
        score_threshold=settings.score_threshold,
    )
    notifier = NotificationService(
        bot_token=settings.bot_token,
        admin_id=settings.admin_id,
    )

    pipeline = Pipeline(
        db=db,
        scraper=scraper,
        ai=ai,
        notifier=notifier,
    )

    # --- Start ---
    try:
        await db.connect()
        await scraper.start()
        logger.info("All services initialized. Starting pipeline loop.")
        await pipeline.run_once()
    finally:
        await scraper.stop()
        await notifier.close()
        await db.close()
        logger.info("Palantir shut down gracefully.")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
