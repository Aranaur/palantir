from __future__ import annotations

import asyncio
import logging

from palantir.models.post import FinalPost, ScoredPost
from palantir.services.ai_service import AIService
from palantir.services.db_service import DBService
from palantir.services.notification_service import NotificationService
from palantir.services.scraper_service import ScraperService

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the full data pipeline: scrape → process → notify."""

    def __init__(
        self,
        db: DBService,
        scraper: ScraperService,
        ai: AIService,
        notifier: NotificationService,
    ) -> None:
        self._db = db
        self._scraper = scraper
        self._ai = ai
        self._notifier = notifier

    async def run_once(self) -> int:
        """Run one full cycle. Returns number of recommendations sent."""
        sent_count = 0

        raw_posts = await self._scraper.fetch_all()
        logger.info("Pipeline: %d raw posts fetched", len(raw_posts))

        for post in raw_posts:
            if await self._db.is_seen(post.unique_key):
                continue

            result = await self._ai.process(post)
            await asyncio.sleep(13)  # Gemini free tier: 5 RPM → 1 req/13s

            if result is None:
                continue

            await self._db.mark_seen(post.unique_key, post.source_id, post.post_id)

            if isinstance(result, ScoredPost):
                logger.info(
                    "Post %s skipped (score=%d/10)",
                    post.unique_key,
                    result.score,
                )
                continue

            assert isinstance(result, FinalPost)

            try:
                await self._notifier.send_recommendation(result)
                await self._db.mark_sent(post.unique_key, result.scored.score)
                sent_count += 1
            except Exception:
                logger.exception("Failed to send recommendation for: %s", post.unique_key)

        logger.info("Pipeline cycle complete: %d recommendations sent", sent_count)
        return sent_count

    async def run_loop(self, interval_seconds: int = 300) -> None:
        """Run pipeline in an infinite loop with a sleep interval."""
        logger.info("Pipeline loop started (interval=%ds)", interval_seconds)
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Pipeline cycle failed")
            await asyncio.sleep(interval_seconds)
