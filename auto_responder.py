"""
auto_responder.py

Генерация текста отклика на проекты Kwork через LLM.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

AI_BASE_URL = os.getenv("AI_BASE_URL", "").rstrip("/")
AI_TOKEN = os.getenv("AI_TOKEN", "")
AI_MODEL = os.getenv("AI_MODEL", "google/gemini-3.1-flash-lite")

# ---------------------------------------------------------------------------
# Системный промпт: генерация текста отклика
# ---------------------------------------------------------------------------

_RESPONSE_SYSTEM_PROMPT = (
    "Ты — опытный фрилансер на Kwork. Пиши короткие отклики так, "
    "как пишет адекватный разработчик в чате, а не ИИ.\n\n"

    "Главная цель — вызвать доверие и показать понимание задачи без воды.\n\n"

    "Правила:\n"
    "- Не пересказывай весь заказ.\n"
    "- Не используй шаблонные фразы: "
    "«Вижу», «готов выполнить», «сделаю быстро и качественно», "
    "«имею большой опыт».\n"
    "- Не пиши слишком официально.\n"
    "- Не используй длинное тире.\n"
    "- Не хвастайся и не обещай невозможное.\n"
    "- Не пиши слишком много технических деталей без необходимости.\n"
    "- Не задавай 5 вопросов подряд.\n\n"

    "Хороший отклик:\n"
    "1. Коротко показывает понимание задачи.\n"
    "2. Упоминает релевантный опыт или похожие задачи.\n"
    "3. Кратко описывает подход.\n"
    "4. При необходимости уточняет 1 важный момент.\n\n"

    "- Обращение только на «вы».\n"
 "- Не использовать фамильярный стиль: "
"«привет», «скинешь», «гляну», «без проблем», "
"«как удобнее?», «всё просто».\n"
"- Тон дружелюбный, но профессиональный.\n"

    "- Не преуменьшай сложность задачи фразами "
"«это просто», «легко», «без проблем».\n"

    "Стиль:\n"
    "- 3-6 коротких предложений.\n"
    "- Простой разговорный русский.\n"
    "- Как будто пишет живой разработчик, а не менеджер.\n"
    "- Допустимы фразы: «Могу помочь», «Такие задачи уже были», "
    "«Обычно это решается через...».\n"
    "- Иногда можно использовать лёгкую человеческую реакцию, "
    "если это уместно.\n\n"

    "Плохой стиль:\n"
    "- «Вижу, вам требуется...»\n"
    "- «Готов качественно выполнить...»\n"
    "- «Сделаю быстро и недорого»\n"
    "- Слишком формальный тон\n"
    "- Полный пересказ ТЗ\n\n"

    "Пиши естественно и по делу."
)


async def generate_response_text(project: dict) -> Optional[str]:
    """
    Генерирует текст отклика через LLM.
    Возвращает текст отклика или None при ошибке.
    """
    if not AI_BASE_URL or not AI_TOKEN:
        logger.error("AI не настроен (AI_BASE_URL / AI_TOKEN)")
        return None

    title = project.get("title", "")
    description = project.get("description", "")
    price = project.get("price", "—")
    category = project.get("category", "")

    user_prompt = (
        f"Проект на Kwork:\n"
        f"- Заголовок: {title}\n"
        f"- Категория: {category}\n"
        f"- Бюджет: {price} руб.\n"
        f"- Описание: {description}\n\n"
        f"Напиши отклик на этот проект."
    )

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": _RESPONSE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 5000,
    }

    logger.info("Генерирую текст отклика через %s...", AI_MODEL)

    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
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
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    content = content.strip().strip('"').strip("'")
                    content = content.replace('\u2014', '-').replace('\u2013', '-')
                    logger.info("Текст отклика сгенерирован (%d символов)", len(content))
                    return content
                else:
                    logger.warning("Пустой ответ от LLM (попытка %d/3)", attempt)
        except Exception as e:
            logger.warning("Ошибка LLM (попытка %d/3): %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(5)

    return None
