"""
AI анализатор заказов Kwork через Gemini (OpenAI-compatible API).
Кэширует оценки в памяти (одна оценка на заказ для всех пользователей).
"""
import asyncio
import logging
import os
import re
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

AI_BASE_URL = os.getenv("AI_BASE_URL", "").rstrip("/")
AI_TOKEN = os.getenv("AI_TOKEN", "")
AI_MODEL = os.getenv("AI_MODEL", "google/gemini-3.1-flash-lite")

_SYSTEM_PROMPT = (
    "Ты — фрилансер на Kwork, специализация: веб-разработка, программирование, IT. "
    "Оцени заказ по шкале 1–10, исходя из соотношения цена / трудозатраты именно для "
    "разработчика. Задачи вне твоей специализации (дизайн, фото, тексты, переводы) "
    "оценивай строже — ты потратишь на них больше времени, чем профильный исполнитель.\n\n"
    "Жёсткие ориентиры по эффективной ставке:\n"
    "- Норма: 1500–2000 ₽/час чистого рабочего времени.\n"
    "- Ниже 500 ₽/час — всегда 1–3/10, без исключений.\n"
    "- 500–1000 ₽/час — 3–5/10.\n"
    "- 1000–1500 ₽/час — 5–7/10.\n"
    "- 1500–2500 ₽/час — 7–8/10.\n"
    "- 2500+ ₽/час и задача решается за 10–30 мин (в т.ч. с AI) — 9–10/10.\n\n"
    "Штрафы (каждый снижает оценку на 1–2 балла):\n"
    "- Рутинный/однообразный объём (карточки, фото, тексты): -2.\n"
    "- Размытое ТЗ или фраза «и другие правки»: -1.\n"
    "- Стек с высоким риском боли (Битрикс, 1С, legacy): -1.\n"
    "- Задача вне IT-специализации (дизайн, редактура, обработка фото): -1.\n\n"
    "Используй всю шкалу от 1 до 10. Избегай скопления оценок — "
    "большинство заурядных заказов должны попадать в диапазон 3–6.\n\n"
    "Ответь строго одной строкой в формате:\n"
    "Оценка: X/10\n"
    "Где X — целое число от 1 до 10. Без пояснений, без текста вне формата."
)

# project_id -> "8/10" | None
_rating_cache: dict[str, Optional[str]] = {}


async def get_rating(project: dict) -> Optional[str]:
    """
    Возвращает строку рейтинга (например '8/10') или None при ошибке / отсутствии API.
    Результат кэшируется по project['id'].
    """
    pid = str(project.get("id", ""))
    if not pid:
        return None

    if pid in _rating_cache:
        return _rating_cache[pid]

    if not AI_BASE_URL or not AI_TOKEN:
        _rating_cache[pid] = None
        return None

    title = project.get("title", "")
    description = project.get("description", "")
    price = project.get("price", "—")

    user_prompt = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"Заголовок: {title}\n"
        f"Цена: {price} ₽\n"
        f"Описание: {description}"
    )

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1000,
    }

    last_exc = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{AI_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {AI_TOKEN}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/KworkSniper",
                        "X-Title": "KworkSniper",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("[AI] Full response for project %s: %s", pid, data)
                choice = data.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "")
                finish_reason = choice.get("finish_reason", "")
                logger.info(
                    "[AI] Project %s | finish_reason=%s | content=%r",
                    pid, finish_reason, content,
                )

                match = re.search(r"Оценка:\s*(\d{1,2})/10", content)
                if match:
                    score = int(match.group(1))
                    if 1 <= score <= 10:
                        rating_text = f"{score}/10"
                        _rating_cache[pid] = rating_text
                        logger.info("[AI] Parsed rating for project %s: %s", pid, rating_text)
                        return rating_text

                logger.warning("[AI] Regex miss for project %s. Content: %r", pid, content)
                _rating_cache[pid] = None
                return None

        except httpx.HTTPStatusError as exc:
            last_exc = exc
            body = exc.response.text if hasattr(exc, "response") else "no body"
            logger.warning(
                "[AI] API error for project %s (attempt %d/3): %s | body: %s",
                pid, attempt, exc, body,
            )
            if attempt < 3:
                await asyncio.sleep(15)
        except Exception as exc:
            last_exc = exc
            logger.warning("[AI] API error for project %s (attempt %d/3): %s", pid, attempt, exc)
            if attempt < 3:
                await asyncio.sleep(15)

    _rating_cache[pid] = None
    logger.error("[AI] All attempts failed for project %s. Last error: %s", pid, last_exc)
    return None


def parse_score(rating_text: Optional[str]) -> Optional[int]:
    """Извлекает числовую оценку из строки 'X/10'."""
    if not rating_text:
        return None
    match = re.match(r"(\d+)/10", rating_text)
    if match:
        score = int(match.group(1))
        if 1 <= score <= 10:
            return score
    return None


def format_rating_line(rating_text: Optional[str]) -> str:
    """
    Форматирует строку рейтинга для Telegram-сообщения.
    🔥 для 7–8, 🔥🔥 для 9–10.
    """
    if not rating_text:
        return ""

    score_match = re.match(r"(\d+)/10", rating_text)
    if not score_match:
        return f"\n\nОценка: {rating_text}"

    score = int(score_match.group(1))
    if score >= 9:
        fire = "🔥🔥 "
    elif score >= 7:
        fire = "🔥 "
    else:
        fire = ""
    return f"\n\n{fire}Оценка: {rating_text}"
