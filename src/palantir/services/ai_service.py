from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from palantir.models.post import FinalPost, RawPost, ScoredPost

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """\
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
3. "rewritten_text" (str | null): Якщо score >= {threshold}, зроби коротке SUMMARY матеріалу. Якщо score < {threshold}, поверни null.

   КРИТИЧНІ ВИМОГИ до summary (поле rewritten_text):
   - Формат: ЧИСТИЙ ТЕКСТ. Без HTML-тегів, без Markdown-розмітки (жодних *, #, ```, [], () тощо).
   - Використовуй ТІЛЬКИ: емодзі (📌, 📊, 🔬, ⚙️, 💡), порожні рядки між абзацами, тире (—) для списків.
   - Зміст: Почни з перекладеного ЗАГОЛОВКУ статті (великими літерами).
   - Далі 3-5 речень або короткий список. Чітко виділи: яку проблему обговорює автор, яка методологія чи стек технологій використовується, і які головні висновки.
   - Тон: суворо професійний, академічно грамотний. Жодного маркетингу, клікбейту чи "води". Тільки факти, логіка та суть.
"""


class AIService:
    """Handles all LLM interactions via Google GenAI (Gemini)."""

    _MAX_RETRIES = 3

    def __init__(
        self,
        api_key: str,
        text_model: str = "gemini-2.5-flash",
        rpm_limit: int = 8,
        score_threshold: int = 6,
        fallback_api_key: str = "",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._text_model = text_model
        self._min_interval = 60.0 / rpm_limit
        self._last_call: float = 0.0
        self._score_threshold = score_threshold
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(threshold=score_threshold)
        self._fallback_key = fallback_api_key
        self._using_fallback = False

    async def process(self, post: RawPost) -> ScoredPost | FinalPost | None:
        """Analyze, score, and (if score >= 6) rewrite post in one flow.

        Returns:
            ScoredPost  — if score < 6 (rejected).
            FinalPost   — if score >= 6 (ready for admin).
            None        — on error.
        """
        try:
            data = await self._call_llm_with_retry(post.text)
            score = int(data["score"])
            rationale = str(data["rationale"])
        except Exception:
            logger.exception("LLM call failed for post: %s", post.unique_key)
            return None
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

    async def _call_llm_with_retry(self, text: str) -> dict:
        """Call Gemini with rate limiting and retry on 429/5xx errors."""
        for attempt in range(1, self._MAX_RETRIES + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

            try:
                response = await self._client.aio.models.generate_content(
                    model=self._text_model,
                    contents=text,
                    config=types.GenerateContentConfig(
                        system_instruction=self._system_prompt,
                        temperature=0.4,
                        response_mime_type="application/json",
                    ),
                )
                if not response.text:
                    logger.warning("Empty response from LLM (attempt %d), skipping", attempt)
                    raise ValueError("Empty LLM response")
                return self._parse_json(response.text)
            except (ClientError, ServerError) as exc:
                retry_after = self._extract_retry_delay(exc)
                if attempt < self._MAX_RETRIES and retry_after is not None:
                    logger.warning(
                        "LLM attempt %d/%d failed (%s), retrying in %.0fs",
                        attempt, self._MAX_RETRIES, exc.status_code, retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                raise

        raise RuntimeError("Unreachable")  # pragma: no cover

    def _extract_retry_delay(self, exc: ClientError | ServerError) -> float | None:
        """Extract retry delay from Gemini error response, or use default backoff.

        Returns None for daily quota errors (retrying won't help until midnight).
        """
        status = getattr(exc, "status_code", 0)
        if status == 429 or status >= 500:
            # Daily quota exhausted — switch to fallback key if available
            exc_str = str(exc).lower()
            if "per_day" in exc_str or "perday" in exc_str or "per day" in exc_str:
                if self._fallback_key and not self._using_fallback:
                    logger.warning("Daily quota exhausted, switching to fallback Gemini key")
                    self._client = genai.Client(api_key=self._fallback_key)
                    self._using_fallback = True
                    return 0.0  # retry immediately with new key
                logger.error("Daily Gemini quota exhausted on all keys. Stopping AI processing.")
                return None
            # Try to parse retryDelay from error details
            details = getattr(exc, "details", None) or []
            if isinstance(details, dict):
                details = details.get("details", [])
            for detail in details:
                if isinstance(detail, dict) and "retryDelay" in detail:
                    delay_str = detail["retryDelay"]
                    if isinstance(delay_str, str) and delay_str.endswith("s"):
                        try:
                            return float(delay_str.rstrip("s"))
                        except ValueError:
                            pass
            # Default backoff for retryable errors
            return 30.0
        return None

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        result = json.loads(cleaned)
        if isinstance(result, list):
            result = result[0]
        return result
