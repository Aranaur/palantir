from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import feedparser
from telethon import TelegramClient
from telethon.tl.types import Message

from palantir.models.post import RawPost

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ScraperService:
    """Collects new posts from Telegram channels and RSS feeds."""

    def __init__(
        self,
        tg_api_id: int,
        tg_api_hash: str,
        tg_session_name: str,
        tg_channels: list[str],
        rss_feeds: list[str],
        scrape_limit: int = 50,
    ) -> None:
        self._tg_channels = tg_channels
        self._rss_feeds = rss_feeds
        self._scrape_limit = scrape_limit
        self._client = TelegramClient(tg_session_name, tg_api_id, tg_api_hash)

    async def start(self) -> None:
        await self._client.start()
        logger.info("Telethon client started")

    async def stop(self) -> None:
        await self._client.disconnect()
        logger.info("Telethon client disconnected")

    async def fetch_all(self) -> list[RawPost]:
        tg_posts = await self._fetch_telegram()
        rss_posts = await self._fetch_rss()
        all_posts = tg_posts + rss_posts
        logger.info("Fetched %d posts total (TG=%d, RSS=%d)", len(all_posts), len(tg_posts), len(rss_posts))
        return all_posts

    async def _fetch_telegram(self) -> list[RawPost]:
        posts: list[RawPost] = []
        for channel in self._tg_channels:
            try:
                entity = await self._client.get_entity(channel)
                messages: list[Message] = await self._client.get_messages(
                    entity, limit=self._scrape_limit
                )
                for msg in messages:
                    if not msg.text:
                        continue
                    post = RawPost(
                        source_id=f"tg:{channel}",
                        post_id=str(msg.id),
                        text=msg.text,
                        url=f"https://t.me/{channel.lstrip('@')}/{msg.id}",
                        timestamp=msg.date.replace(tzinfo=timezone.utc) if msg.date else datetime.now(timezone.utc),
                    )
                    posts.append(post)
            except Exception:
                logger.exception("Failed to fetch from TG channel: %s", channel)
        return posts

    async def _fetch_rss(self) -> list[RawPost]:
        posts: list[RawPost] = []
        loop = asyncio.get_running_loop()
        for feed_url in self._rss_feeds:
            try:
                feed = await loop.run_in_executor(None, feedparser.parse, feed_url)
                for entry in feed.entries[: self._scrape_limit]:
                    text = entry.get("summary") or entry.get("title") or ""
                    if not text:
                        continue
                    link = entry.get("link", feed_url)
                    post_id = hashlib.sha256(link.encode()).hexdigest()[:16]
                    published = entry.get("published_parsed")
                    ts = (
                        datetime(*published[:6], tzinfo=timezone.utc)
                        if published
                        else datetime.now(timezone.utc)
                    )
                    posts.append(
                        RawPost(
                            source_id=f"rss:{feed_url}",
                            post_id=post_id,
                            text=text,
                            url=link,
                            timestamp=ts,
                        )
                    )
            except Exception:
                logger.exception("Failed to fetch RSS feed: %s", feed_url)
        return posts
