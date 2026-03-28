from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from google import genai
from google.genai import types

from palantir.models.post import FinalPost, RawPost, ScoredPost

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Ти — персональний асистент-дослідник та аналітик контенту.
Тематика: Data Science, Computer Science, програмування (зокрема R та Python), математичні методи, моделювання та статистика.

Твоє завдання — аналізувати нові статті/публікації та робити для автора короткі, змістовні вижимки (summary), щоб він міг швидко оцінити наукову чи практичну цінність матеріалу і вирішити, чи варто готувати про це авторський огляд.

УСІ ВІДПОВІДІ (rationale та rewritten_text) ГЕНЕРУЙ ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ, незалежно від мови оригіналу.

Поверни виключно валідний JSON з такими полями:

1. "score" (int): Рейтинг користі матеріалу від 1 до 10.
   - 1-4: Банально, загальновідомо (напр., "що таке машинне навчання").
   - 5-7: Стандартні новини індустрії, базові туторіали.
   - 8-10: Висока цінність: глибока аналітика, розбір специфічних алгоритмів, статистичних методів, нові потужні інструменти, неочевидні інсайти.
2. "rationale" (str): Коротке обґрунтування оцінки (1-2 речення). Чому це варто уваги фахівця чи дослідника, і в чому головна "фішка" матеріалу.
3. "rewritten_text" (str | null): Якщо score >= 8, зроби коротке SUMMARY матеріалу. Якщо score < 8, поверни null.

   КРИТИЧНІ ВИМОГИ до summary (поле rewritten_text):
   - Формат: ЧИСТИЙ ТЕКСТ. Без HTML-тегів, без Markdown-розмітки (жодних *, #, ```, [], () тощо).
   - Використовуй ТІЛЬКИ: емодзі (📌, 📊, 🔬, ⚙️, 💡), порожні рядки між абзацами, тире (—) для списків.
   - Зміст: Почни з перекладеного ЗАГОЛОВКУ статті (великими літерами).
   - Далі 3-5 речень або короткий список. Чітко виділи: яку проблему обговорює автор, яка методологія чи стек технологій використовується, і які головні висновки.
   - Тон: суворо професійний, академічно грамотний. Жодного маркетингу, клікбейту чи "води". Тільки факти, логіка та суть.
"""


class AIService:
    """Handles all LLM interactions via Google GenAI (Gemini)."""

    def __init__(
        self,
        api_key: str,
        text_model: str = "gemini-2.5-flash",
        rpm_limit: int = 8,
        score_threshold: int = 6,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._text_model = text_model
        self._min_interval = 60.0 / rpm_limit
        self._last_call: float = 0.0
        self._score_threshold = score_threshold

    async def process(self, post: RawPost) -> ScoredPost | FinalPost | None:
        """Analyze, score, and (if score >= 6) rewrite post in one flow.

        Returns:
            ScoredPost  — if score < 6 (rejected).
            FinalPost   — if score >= 6 (ready for admin).
            None        — on error.
        """
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            logger.debug("Rate limiter: sleeping %.1fs", wait)
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

        try:
            response = await self._client.aio.models.generate_content(
                model=self._text_model,
                contents=post.text,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    temperature=0.4,
                    response_mime_type="application/json",
                ),
            )
            data = self._parse_json(response.text or "")
        except Exception:
            logger.exception("LLM call failed for post: %s", post.unique_key)
            return None

        score = int(data["score"])
        rationale = str(data["rationale"])
        scored = ScoredPost(raw=post, score=score, rationale=rationale)

        logger.info("Post %s scored %d/10", post.unique_key, score)

        if score < self._score_threshold:
            return scored

        rewritten_text = data.get("rewritten_text")

        if not rewritten_text:
            logger.warning(
                "Post %s scored %d but LLM returned empty rewrite",
                post.unique_key,
                score,
            )
            return scored

        return FinalPost(
            scored=scored,
            rewritten_text=str(rewritten_text),
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        return json.loads(cleaned)
