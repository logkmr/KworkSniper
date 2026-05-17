"""
AI анализатор заказов Kwork через Groq / OpenAI-compatible API.
Кэширует оценки в памяти (одна оценка на заказ для всех пользователей).
"""
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
AI_MODEL = os.getenv("AI_MODEL", "openai/gpt-oss-120b")

_SYSTEM_PROMPT = (
    "Ты — фрилансер на Kwork, специализация: веб-разработка, программирование, IT. "
    "Оцени заказ по шкале 1–10, исходя из соотношения цена / трудозатраты для "
    "опытного разработчика, который активно использует AI-инструменты.\n\n"
    "ВАЖНО: оценивай время реалистично для опытного человека с AI, а не для новичка:\n"
    "- Деплой/установка бота, скрипта на сервер по инструкции — 15–30 мин.\n"
    "- Исправить баг на сайте (найти + починить) — 15–45 мин.\n"
    "- Простая интеграция API — 30–60 мин.\n"
    "- Парсер с нуля — 1–2 ч.\n"
    "- Лендинг по макету — 2–4 ч.\n"
    "- Телеграм-бот с базовым функционалом — 1–3 ч.\n\n"
    "Жёсткие ориентиры по эффективной ставке:\n"
    "- Норма: 1500–2000 ₽/час чистого рабочего времени.\n"
    "- Ниже 500 ₽/час — всегда 1–3/10.\n"
    "- 500–1000 ₽/час — 3–5/10.\n"
    "- 1000–1500 ₽/час — 5–6/10.\n"
    "- 1500–2500 ₽/час — 7–8/10.\n"
    "- 2500+ ₽/час — 9–10/10.\n\n"
    "Если у заказа указан 'допустимый бюджет' выше желаемого — "
    "считай по допустимому, так как реальная сумма может быть выше.\n\n"
    "Штрафы (каждый снижает оценку на 1–2 балла):\n"
    "- Рутинный/однообразный объём (карточки, фото, тексты): -2.\n"
    "- Размытое ТЗ или фраза «и другие правки»: -1.\n"
    "- Стек с высоким риском боли (Битрикс, 1С, legacy): -1.\n"
    "- Задача вне IT-специализации (дизайн, редактура, обработка фото): -1.\n\n"
    "Используй всю шкалу от 1 до 10. Большинство заурядных заказов — 3–6.\n\n"
    "Ответь строго одной строкой в формате:\n"
    "Оценка: X/10\n"
    "Без пояснений, без текста вне формата."
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
        f"Заголовок: {title}\n"
        f"Цена: {price} ₽\n"
        f"Описание: {description}"
    )

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1000,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_TOKEN}",
                    "Content-Type": "application/json",
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

    except Exception as exc:
        logger.warning("[AI] API error for project %s: %s", pid, exc)
        _rating_cache[pid] = None
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
