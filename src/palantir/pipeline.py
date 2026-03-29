from __future__ import annotations

import logging

from palantir.models.post import FinalPost, ScoredPost
from palantir.services.ai_service import AIService
from palantir.services.db_service import DBService
from palantir.services.dedup_service import deduplicate
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
        recommendations: list[FinalPost] = []

        raw_posts = await self._scraper.fetch_all()
        raw_posts = deduplicate(raw_posts)
        logger.info("Pipeline: %d posts after dedup", len(raw_posts))

        for post in raw_posts:
            if await self._db.is_seen(post.unique_key):
                continue

            result = await self._ai.process(post)

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

            if not isinstance(result, FinalPost):
                continue

            recommendations.append(result)

        sent_count = 0
        if recommendations:
            recommendations.sort(key=lambda fp: fp.scored.score, reverse=True)
            sent = await self._notifier.send_digest(recommendations)
            sent_count = len(sent)
            for rec in sent:
                await self._db.mark_sent(rec.scored.raw.unique_key, rec.scored.score)

        logger.info("Pipeline cycle complete: %d recommendations sent", sent_count)
        return sent_count
