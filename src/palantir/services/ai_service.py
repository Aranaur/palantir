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

_POST_GEN_PROMPT = """\
Ти — досвідчений Data Scientist, статистик та автор експертного Telegram-каналу. \
Твоя мета — розповідати про складні речі (машинне навчання, статистику, аналіз даних) \
простою, захопливою мовою з практичним стилем.

Твоє завдання: прочитати наданий матеріал і згенерувати пост для Telegram-каналу, \
суворо дотримуючись авторської стилістики та технічних обмежень платформи.

ГЕНЕРУЙ ПОСТ ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ.

### Тип контенту
Звичайне текстове повідомлення: максимум 4096 символів. Краще з невеликим запасом.

### Tone of Voice
- Експертний, але не сухий академічний. Ти спілкуєшся з колегами.
- Використовуй влучні метафори (наприклад, "катування даних", "темна магія", "найстрашніше рівняння").
- Пиши живою мовою, уникай канцеляризмів та типових "маркетингових" чи "нудних" вступів \
  (ніяких "У цій захопливій статті ми розглянемо...").
- Оберігай баланс: використовуй професійні терміни (p-value, ML, variance, causal inference), \
  але поясни їхню суть "на хлопський розум" або через життєві/історичні приклади.

### Структура посту
1. Заголовок: Влучний, інтригуючий, 1-2 рядки. Обов'язково закінчується тематичним емодзі.
2. Вступ/Хук: Одразу до суті проблеми. Чому це важливо? Який біль Data Scientist'ів це вирішує? (Іноді можна починати з tl;dr:).
3. Основна частина: Коротко про те, що зробив автор. ОБОВ'ЯЗКОВО постав посилання на джерело прямо в текст у форматі Назва (URL). Якщо підпис до медіа — скороти до 1-2 речень.
4. Інсайт / Головна думка (⚡️): Найважливіший висновок або застереження. Обов'язково починається з емодзі ⚡️. Це має бути сильна, критична думка.
5. Деталі / Приклади (⚡️): Якщо є цікаві кейси зі статті, опиши 1-2 найцікавіші. Починай з ⚡️.
6. Кінцівка / Call to Action (📌 або 🔗): Заклик заглянути в коментарі за кодом, посиланням на GitHub або перехід на повну статтю. Використовуй 📌 або 🔗.

### Чого ОБОВ'ЯЗКОВО уникати
- Довгих простирадел тексту (роби абзаци по 2-4 речення).
- Надмірного використання емодзі (використовуй їх лише як структурні маркери: ⚡️, 📌, 🔗, 🔬).
- Загальних фраз, які не несуть інформаційної цінності.
- Хештегів (якщо я прямо не попрошу їх додати).

### ФОРМАТ ВИХОДУ
Поверни виключно валідний JSON з полем:
- "post_text" (str): готовий текст посту для Telegram.
"""

_SYSTEM_PROMPT_TEMPLATE = """\
Ти — персональний асистент-дослідник для викладача Data Science та статистики.
Автор працює з R і Python, викладає і готує авторські огляди матеріалів.

Твоє завдання — аналізувати публікації та визначити, чи варто автору звертати на них увагу.

УСІ ВІДПОВІДІ (rationale та rewritten_text) ГЕНЕРУЙ ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ, незалежно від мови оригіналу.

Поверни виключно валідний JSON з полями:

1. "score" (int, 1-10) — оціни за такою шкалою:

   9-10 MUST READ — проривні дослідження, нові фундаментальні методи:
   — Нові статистичні методи, теоретичні результати (байєсіанство, причинність)
   — Проривні архітектури ML/DL з доведеним впливом
   — Шедеври візуалізації даних (інтерактивна дата-журналістика, глибокий історичний датавіз).
   — Ідеальні матеріали для викладання (інтуїтивні експлейнери складних концепцій, унікальні відкриті датасети).

   7-8 ЦІННЕ — глибокий технічний контент, практичні інсайти:
   — Глибокий розбір алгоритмів з математичним обґрунтуванням
   — Нові R/Python пакети, що вирішують реальну проблему (tidymodels, polars, ...)
   — Промислові кейси з метриками: A/B тести, ML-пайплайни на проді (Netflix, Airbnb, Booking)
   — Якісні туторіали з нетривіальними прийомами (ggplot, Quarto, Shiny, Streamlit)
   — Візуалізація даних: нестандартні типи графіків, цікаві знахідки у даних (навіть якщо текст короткий).
   
   5-6 НОРМАЛЬНО — корисно, але без глибини:
   — Стандартні огляди бібліотек без порівнянь чи нюансів
   — Новини релізів (R 4.x, Python 3.x, PyTorch 2.x) без аналізу наслідків
   — Базові туторіали для початківців
   — Інтерв'ю без технічної глибини

   3-4 СЛАБКО — банальне або не по темі:
   — "Що таке ML", "Топ-10 бібліотек Python" (загальновідоме)
   — Маркетингові пости курсів і платформ
   — Крипто, блокчейн, web3 без зв'язку з DS

   1-2 ШЛАК — спам, клікбейт, нерелевант:
   — Реклама, самопіар без змісту
   — Контент без зв'язку з DS/ML/статистикою

2. "rationale" (str): 1-2 речення — ЧОМУ саме така оцінка. Вкажи категорію та головну "фішку" матеріалу.

3. "rewritten_text" (str | null): Якщо score >= {threshold}, зроби коротке SUMMARY. Якщо score < {threshold}, поверни null.

   ВИМОГИ до summary (rewritten_text):
   — Формат: ЧИСТИЙ ТЕКСТ. Без HTML, Markdown-форматування (ніяких *, #, `, *). Звичайна пунктуація, включаючи круглі дужки, дозволена.
   — Дозволено: емодзі (📌 📊 🔬 ⚙️ 💡), порожні рядки, тире (—) для списків.
   — Почни з перекладеного ЗАГОЛОВКУ (великими літерами).
   — Далі 1-5 речень (залежно від обсягу оригіналу): суть матеріалу, ключові інсайти або стек/методологія (якщо це технічна стаття).
   — Тон: професійний, академічно грамотний. Без маркетингу і клікбейту.
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
        post_gen_model: str = "gemini-2.5-flash",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._text_model = text_model
        self._post_gen_model = post_gen_model
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

    async def generate_post(self, summary: str, url: str) -> str | None:
        """Generate a Telegram channel post from a saved summary using Gemini 2.5 Flash.

        Returns the post text or None on error.
        """
        user_content = (
            f"Матеріал для обробки:\n{summary}\n\n"
            f"Посилання на оригінал (ОБОВ'ЯЗКОВО встав у текст посту): {url}\n\n"
            "Тип посту: Звичайний текст (до 4096 символів)"
        )
        try:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

            response = await self._client.aio.models.generate_content(
                model=self._post_gen_model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=_POST_GEN_PROMPT,
                    temperature=0.7,
                    response_mime_type="application/json",
                ),
            )
            if not response.text:
                logger.warning("Empty response from LLM for post generation")
                return None
            data = self._parse_json(response.text)
            return data.get("post_text")
        except Exception:
            logger.exception("Post generation failed")
            return None

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
                        attempt, self._MAX_RETRIES, exc.code, retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                raise

        raise RuntimeError("Unreachable")  # pragma: no cover

    def _extract_retry_delay(self, exc: ClientError | ServerError) -> float | None:
        """Extract retry delay from Gemini error response, or use default backoff.

        Returns None for daily quota errors (retrying won't help until midnight).
        """
        status = getattr(exc, "code", None) or getattr(exc, "status_code", 0)
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
