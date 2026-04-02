from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from palantir.models.post import FinalPost
from palantir.services.db_service import DBService

logger = logging.getLogger(__name__)

_DIGEST_HEADER = (
    "📋 <b>Щоденний дайджест</b> — {date}\n"
    "Знайдено рекомендацій: {count}"
)

_POST_TEMPLATE = """\
{summary}

━━━━━━━━━━━━━━━━━━━━
🔥 Рейтинг: <b>{score}/10</b>
💬 {rationale}
🔗 <a href="{url}">Оригінал</a>"""

_MAX_MESSAGE_LEN = 4096


class NotificationService:
    """Sends post recommendations to admin via aiogram."""

    def __init__(self, bot_token: str, admin_id: int) -> None:
        self._bot = Bot(token=bot_token)
        self._admin_id = admin_id

    async def send_digest(self, posts: list[FinalPost]) -> list[FinalPost]:
        """Send digest header + individual post messages with reaction buttons.

        Returns the list of posts that were successfully sent.
        """
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")

        await self._bot.send_message(
            chat_id=self._admin_id,
            text=_DIGEST_HEADER.format(date=date_str, count=len(posts)),
            parse_mode="HTML",
        )

        sent: list[FinalPost] = []
        for post in posts:
            try:
                await self._send_post(post)
                sent.append(post)
            except Exception:
                logger.exception("Failed to send post %s", post.scored.raw.unique_key)

        logger.info("Digest sent: %d/%d post(s)", len(sent), len(posts))
        return sent

    async def _send_post(self, post: FinalPost) -> None:
        safe_text = html.escape(post.rewritten_text)
        safe_rationale = html.escape(
            self._truncate(post.scored.rationale, 300),
        )
        url = html.escape(post.scored.raw.url)
        unique_key = post.scored.raw.unique_key
        short_key = DBService.make_short_key(unique_key)

        message = _POST_TEMPLATE.format(
            summary=safe_text,
            score=post.scored.score,
            rationale=safe_rationale,
            url=url,
        )

        if len(message) > _MAX_MESSAGE_LEN:
            available = _MAX_MESSAGE_LEN - (len(message) - len(safe_text)) - 3
            safe_text = self._truncate(safe_text, available)
            message = _POST_TEMPLATE.format(
                summary=safe_text,
                score=post.scored.score,
                rationale=safe_rationale,
                url=url,
            )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔴 Низький",
                        callback_data=f"rate:low:{short_key}",
                    ),
                    InlineKeyboardButton(
                        text="🟡 Середній",
                        callback_data=f"rate:medium:{short_key}",
                    ),
                    InlineKeyboardButton(
                        text="🟢 Високий",
                        callback_data=f"rate:high:{short_key}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="👎 Відхилити",
                        callback_data=f"skip:{short_key}",
                    ),
                ],
            ]
        )

        await self._bot.send_message(
            chat_id=self._admin_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )

    @staticmethod
    def _truncate(text: str, max_len: int = 300) -> str:
        """Truncate text at word boundary, safe for HTML entities."""
        if len(text) <= max_len:
            return text
        cut = text[:max_len].rsplit(" ", 1)[0]
        last_amp = cut.rfind("&")
        if last_amp != -1 and ";" not in cut[last_amp:]:
            cut = cut[:last_amp]
        return cut + "..."

    async def send_weekly_report(self, stats: dict) -> None:
        """Send a weekly summary report."""
        days = stats["days"]
        total_seen = stats["total_seen"]
        total_sent = stats["total_sent"]
        score_dist = stats["score_dist"]
        top_sources = stats["top_sources"]
        feedback = stats["feedback"]

        lines = [
            f"📊 <b>Щотижневий звіт</b> (останні {days} днів)\n",
            f"📥 Оброблено постів: <b>{total_seen}</b>",
            f"📤 Надіслано рекомендацій: <b>{total_sent}</b>",
        ]

        if feedback:
            skipped = feedback.get("skip", 0)
            lines.append(f"👎 Відхилено: <b>{skipped}</b>")

        if score_dist:
            lines.append("\n<b>Розподіл оцінок:</b>")
            for score in sorted(score_dist, reverse=True):
                bar = "█" * min(score_dist[score], 20)
                lines.append(f"  {score:>2}/10: {bar} {score_dist[score]}")

        if top_sources:
            lines.append("\n<b>Топ джерела:</b>")
            for source_id, count in top_sources:
                name = source_id.replace("tg:@", "@").replace("rss:", "")
                lines.append(f"  — {name}: {count} рек.")

        await self._bot.send_message(
            chat_id=self._admin_id,
            text="\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("Weekly report sent")

    async def close(self) -> None:
        await self._bot.session.close()
