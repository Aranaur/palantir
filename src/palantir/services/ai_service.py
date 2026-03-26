from __future__ import annotations

import json
import logging
import re

from google import genai
from google.genai import types

from palantir.models.post import FinalPost, RawPost, ScoredPost

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Ти — досвідчений головний редактор та експерт з контенту.
Тематика каналу: Data Science, Computer Science, програмування, технології, аналітика даних та статистика (зокрема математичні методи та моделювання).
Аудиторія: фахівці, студенти та дослідники, які цінують професійний, лаконічний та аргументований контент, підкріплений строгими даними.

Твоє завдання — обробити вхідний текст новини/статті та повернути виключно валідний JSON з такими полями:

1. "score" (int): Рейтинг потенційної ефективності посту (Engagement & Retention) від 1 до 10.
   - 1-4: Банально, загальновідомо, нерелевантно.
   - 5-7: Корисна інформація, стандартні новини індустрії або базові освітні матеріали.
   - 8-10: Ексклюзив, глибока аналітика, складні статистичні концепти простою мовою, проривні алгоритми, корисні інструменти.
2. "rationale" (str): Коротке обґрунтування оцінки (1-2 речення). Чому це зачепить або не зачепить аудиторію.
3. "rewritten_text" (str | null): Якщо score >= 8, напиши пост українською мовою для Telegram-каналу. Якщо score < 8, поверни null.

   КРИТИЧНІ ВИМОГИ до rewritten_text:
   - Формат: ЧИСТИЙ ТЕКСТ. Без HTML-тегів, без Markdown-розмітки (жодних *, #, ```, [], () тощо).
   - Для структури використовуй ТІЛЬКИ: емодзі (➡️, 📊, 🔗, 🔬, 💡), порожні рядки між абзацами, тире (—).
   - Для списків: починай кожен пункт з емодзі на новому рядку.
   - Посилання вставляй як голі URL після тексту, наприклад: "Детальніше: [https://example.com](https://example.com)"
   - Починай з чіпляючого заголовку (великими літерами або з емодзі).
   - Додай "tl;dr:" на початку — стисле резюме в 1-2 речення.
   - Стиль: академічно грамотний, але доступний; лаконічний, без зайвої "води". Тільки суть, методологія та цінність для читача.
   - НЕ скорочуй контент — збережи всю суть та усі ТЕМАТИЧНІ посилання з оригіналу (на статті, дослідження, документацію).
   - ВИДАЛИ особисті/промо посилання автора: банки (monobank, privatbank тощо), YouTube-канали, особисті сайти, донат-посилання, посилання на соціальні мережі автора.
"""


class AIService:
    """Handles all LLM interactions via Google GenAI (Gemini)."""

    def __init__(
        self,
        api_key: str,
        text_model: str = "gemini-2.5-flash",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._text_model = text_model

    async def process(self, post: RawPost) -> ScoredPost | FinalPost | None:
        """Analyze, score, and (if score >= 6) rewrite post in one flow.

        Returns:
            ScoredPost  — if score < 6 (rejected).
            FinalPost   — if score >= 6 (ready for admin).
            None        — on error.
        """
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

        if score < 6:
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
