from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import feedparser
import httpx
from bs4 import BeautifulSoup
from telethon import TelegramClient
from telethon.tl.types import Message

from palantir.models.post import RawPost

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15.0
_MAX_BODY_LEN = 4000  # chars — enough context for AI, avoids huge pages


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
        custom_blogs: list[str] | None = None,
    ) -> None:
        self._tg_channels = tg_channels
        self._rss_feeds = rss_feeds
        self._scrape_limit = scrape_limit
        self._custom_blogs = custom_blogs or []
        self._client = TelegramClient(tg_session_name, tg_api_id, tg_api_hash)
        self._http = httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )

    async def start(self) -> None:
        await self._client.start()
        logger.info("Telethon client started")

    async def stop(self) -> None:
        await self._client.disconnect()
        await self._http.aclose()
        logger.info("Telethon client disconnected")

    async def fetch_all(self) -> list[RawPost]:
        tg_posts = await self._fetch_telegram()
        rss_posts = await self._fetch_rss()
        blog_posts = await self._fetch_custom_blogs()
        all_posts = tg_posts + rss_posts + blog_posts
        logger.info(
            "Fetched %d posts total (TG=%d, RSS=%d, blogs=%d)",
            len(all_posts), len(tg_posts), len(rss_posts), len(blog_posts),
        )
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
                    summary = entry.get("summary") or entry.get("title") or ""
                    link = entry.get("link", feed_url)
                    if not summary and not link:
                        continue

                    post_id = hashlib.sha256(link.encode()).hexdigest()[:16]
                    published = entry.get("published_parsed")
                    ts = (
                        datetime(*published[:6], tzinfo=timezone.utc)
                        if published
                        else datetime.now(timezone.utc)
                    )

                    full_text = await self._fetch_article(link)
                    text = full_text if full_text else summary

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

    async def _fetch_custom_blogs(self) -> list[RawPost]:
        """Scrape blog index pages that don't have RSS feeds."""
        posts: list[RawPost] = []
        for blog_url in self._custom_blogs:
            try:
                resp = await self._http.get(blog_url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                from urllib.parse import urljoin, urlparse
                base = f"{urlparse(blog_url).scheme}://{urlparse(blog_url).netloc}"

                # Collect all internal blog post links
                seen: set[str] = set()
                article_links: list[str] = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    full_url = urljoin(base, href)
                    # Keep only links that look like blog posts (contain /blog/)
                    if (
                        "/blog/" in full_url
                        and full_url != blog_url
                        and full_url not in seen
                        and urlparse(full_url).netloc == urlparse(blog_url).netloc
                    ):
                        seen.add(full_url)
                        article_links.append(full_url)

                for link in article_links[: self._scrape_limit]:
                    post_id = hashlib.sha256(link.encode()).hexdigest()[:16]
                    full_text = await self._fetch_article(link)
                    if not full_text:
                        continue
                    posts.append(
                        RawPost(
                            source_id=f"blog:{blog_url}",
                            post_id=post_id,
                            text=full_text,
                            url=link,
                            timestamp=datetime.now(timezone.utc),
                        )
                    )
            except Exception:
                logger.exception("Failed to scrape custom blog: %s", blog_url)
        return posts

    async def _fetch_article(self, url: str) -> str | None:
        """Fetch full article text from URL. Returns None on failure."""
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove non-content elements
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "form"]):
                tag.decompose()

            # Try common article containers first
            article = (
                soup.find("article")
                or soup.find("main")
                or soup.find(class_=lambda c: c and "post-content" in c)
                or soup.find(class_=lambda c: c and "article-body" in c)
                or soup.find(class_=lambda c: c and "entry-content" in c)
            )

            target = article if article else soup.body
            if not target:
                return None

            text = target.get_text(separator="\n", strip=True)
            if len(text) < 100:
                return None

            return text[:_MAX_BODY_LEN]

        except Exception:
            logger.debug("Failed to fetch article: %s", url)
            return None
