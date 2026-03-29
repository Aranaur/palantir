from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from aiogram import Bot

from palantir.models.post import FinalPost

logger = logging.getLogger(__name__)

_DIGEST_HEADER = "📋 <b>Щоденний дайджест</b> — {date}\nЗнайдено рекомендацій: {count}\n"

_DIGEST_ITEM = """\

━━━━━━━━━━━━━━━━━━━━
<b>{index}.</b> {summary}
🔥 {score}/10 · 💬 {rationale}
🔗 <a href="{url}">Оригінал</a>"""

_MAX_MESSAGE_LEN = 4096
_PART_SUFFIX_RESERVE = 30  # room for "📄 Частина XX/XX"


class NotificationService:
    """Sends post recommendations to admin via aiogram."""

    def __init__(self, bot_token: str, admin_id: int) -> None:
        self._bot = Bot(token=bot_token)
        self._admin_id = admin_id

    async def send_digest(self, posts: list[FinalPost]) -> None:
        """Send a daily digest with all recommendations."""
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")
        header = _DIGEST_HEADER.format(date=date_str, count=len(posts))

        items: list[str] = []
        for i, post in enumerate(posts, 1):
            summary = html.escape(self._truncate(post.rewritten_text, 300))
            rationale = html.escape(self._truncate(post.scored.rationale, 150))
            url = html.escape(post.scored.raw.url)
            items.append(
                _DIGEST_ITEM.format(
                    index=i,
                    summary=summary,
                    score=post.scored.score,
                    rationale=rationale,
                    url=url,
                )
            )

        messages = self._split_messages(header, items)

        for idx, msg in enumerate(messages, 1):
            if len(messages) > 1:
                msg = msg.rstrip() + f"\n\n📄 Частина {idx}/{len(messages)}"
            await self._bot.send_message(
                chat_id=self._admin_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        logger.info("Digest sent: %d posts in %d message(s)", len(posts), len(messages))

    @staticmethod
    def _split_messages(header: str, items: list[str]) -> list[str]:
        """Pack items into messages, each under 4096 chars. Split at post boundaries."""
        limit = _MAX_MESSAGE_LEN - _PART_SUFFIX_RESERVE
        messages: list[str] = []
        current = header

        for item in items:
            if len(current) + len(item) > limit:
                if current.strip():
                    messages.append(current)
                current = item
            else:
                current += item

        if current.strip():
            messages.append(current)

        return messages if messages else [header]

    @staticmethod
    def _truncate(text: str, max_len: int = 300) -> str:
        """Truncate text at word boundary, safe for HTML entities."""
        if len(text) <= max_len:
            return text
        cut = text[:max_len].rsplit(" ", 1)[0]
        # Prevent cutting in the middle of an HTML entity
        last_amp = cut.rfind("&")
        if last_amp != -1 and ";" not in cut[last_amp:]:
            cut = cut[:last_amp]
        return cut + "..."

    async def close(self) -> None:
        await self._bot.session.close()
