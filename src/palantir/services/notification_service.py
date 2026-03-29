from __future__ import annotations

import html
import logging

from aiogram import Bot

from palantir.models.post import FinalPost

logger = logging.getLogger(__name__)

_ANALYTICS_TEMPLATE = """\

━━━━━━━━━━━━━━━━━━━━
📊 <b>Аналітика</b>
🔥 Рейтинг ефективності: <b>{score}/10</b>
💬 {rationale}

🔗 <a href="{url}">Оригінал</a>"""


class NotificationService:
    """Sends post recommendations to admin via aiogram."""

    def __init__(self, bot_token: str, admin_id: int) -> None:
        self._bot = Bot(token=bot_token)
        self._admin_id = admin_id

    async def send_recommendation(self, post: FinalPost) -> None:
        # Escape HTML in LLM-generated text to prevent parse errors
        safe_text = html.escape(post.rewritten_text)
        safe_rationale = html.escape(post.scored.rationale)

        analytics = _ANALYTICS_TEMPLATE.format(
            score=post.scored.score,
            rationale=safe_rationale,
            url=html.escape(post.scored.raw.url),
        )

        message = safe_text + analytics

        if len(message) <= 4096:
            await self._bot.send_message(
                chat_id=self._admin_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            longread_header = (
                '⚠️ <b>Лонгрід!</b> Ідеальний кандидат для '
                'публікації повної версії у блозі: https://aranaur.rbind.io/blog/\n\n'
            )
            available = 4096 - len(longread_header) - len(analytics) - 3
            cut = safe_text[:available]
            # Prevent cutting in the middle of an HTML entity like &amp;
            last_amp = cut.rfind("&")
            if last_amp != -1 and ";" not in cut[last_amp:]:
                cut = cut[:last_amp]
            truncated = longread_header + cut + "..." + analytics
            await self._bot.send_message(
                chat_id=self._admin_id,
                text=truncated,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        logger.info("Recommendation sent to admin for: %s", post.scored.raw.unique_key)

    async def close(self) -> None:
        await self._bot.session.close()
