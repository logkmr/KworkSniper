"""
auto_responder.py

Утилиты для автоотклика на проекты Kwork:
    1. Генерация текста + цены через LLM на основе профиля
    2. Отправка отклика через API Kwork (POST /api/offer/createoffer)

Эндпоинты (найдены через анализ JS want-worker + new-offer, 18.05.2026):
    - GET  /wants/{id}/check_offer_notify  — проверка перед откликом
    - POST /api/offer/createoffer          — создание отклика (FormData)
    - POST /api/offer/editoffer            — редактирование отклика
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
AI_BASE_URL = os.getenv("AI_BASE_URL", "").rstrip("/")
AI_TOKEN = os.getenv("AI_TOKEN", "")
AI_MODEL = os.getenv("AI_MODEL", "google/gemini-3.1-flash-lite")

BASE_URL = "https://kwork.ru"

OFFER_CREATE_ENDPOINT = "/api/offer/createoffer"
OFFER_EDIT_ENDPOINT = "/api/offer/editoffer"
CHECK_OFFER_NOTIFY_ENDPOINT = "/wants/{want_id}/check_offer_notify"
CHECK_TEMPLATE_ENDPOINT = "/projects/check_is_template"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://kwork.ru",
}

# ---------------------------------------------------------------------------
# Системный промпт: генерация текста отклика + расчёт цены
# ---------------------------------------------------------------------------

_RESPONSE_SYSTEM_PROMPT = (
    "Ты — фрилансер на Kwork. Напиши короткий отклик заказчику и предложи цену.\n\n"
    "ЗАПРЕЩЕНО (самое важное):\n"
    "- НЕ начинай с «Вижу...», «Вижу, вам нужно...», «Вижу, задача...»\n"
    "- НЕ используй повелительное 1-е лицо: «сделаю», «запишу», «возьмусь», «напишу»\n"
    "- НЕ пересказывай описание заказа — заказчик САМ его писал\n\n"
    "СТРУКТУРА ОТКЛИКА (строго):\n"
    "1. Покажи понимание задачи — одной фразой, простым языком, без «Вижу».\n"
    "   Плохо: «Вижу, нужно настроить парсинг Wildberries...»\n"
    "   Хорошо: «Парсинг Wildberries с фильтрацией - типовая задача, решается через Selenium и прокси»\n"
    "2. Кратко про свой опыт в похожих задачах (1 предложение, без хвастовства).\n"
    "3. Как планируешь решать — подход, инструменты (1-2 предложения).\n"
    "4. Честный нюанс или риск, если есть.\n"
    "5. Уточняющий вопрос, если нужно. Иначе - предложение обсудить детали.\n"
    "Держись в рамках 3-5 предложений, пиши как живой человек.\n\n"
    "ПРАВИЛА РАСЧЁТА ЦЕНЫ:\n"
    "- Простая задача (фикс, консультация) → снизь на 30-50% от бюджета\n"
    "- Средняя сложность (бот, лендинг, скрипт) → ±10% от бюджета\n"
    "- Сложная задача (сайт, интеграции) → цена равна бюджету\n"
    "- НИКОГДА не выше бюджета заказчика\n"
    "- Цена — целое число в рублях\n\n"
    "ПРАВИЛА ТЕКСТА:\n"
    "- Пиши простым, естественным языком — как живой человек в чате\n"
    "- Без официоза, без канцеляризмов. Как коллега коллеге\n"
    "- Длина: 3-5 предложений\n"
    "- НЕ используй длинное тире «—», только короткое «-»\n"
    "- НЕ упоминай цену и срок в тексте\n"
    "- ЗАПРЕЩЕНЫ слова: «Вижу», «сделаю», «запишу», «возьмусь», «напишу», «настрою»\n"
    "- НЕ используй: «Здравствуйте», «готов выполнить», «качественно, быстро»\n\n"
    "ФОРМАТ ОТВЕТА (строго):\n"
    "Цена: XXXX\n"
    "Срок: X (дней/часов)\n"
    "\n"
    "Текст отклика (без упоминания срока и цены — они уже указаны выше)"
)

# ---------------------------------------------------------------------------
# Загрузка Cookies
# ---------------------------------------------------------------------------

def load_cookies_from_string(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    cookies = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def load_cookies() -> dict[str, str]:
    """Загружает куки из KWORK_COOKIE или kwork_cookies.txt."""
    raw = os.getenv("KWORK_COOKIE", "")
    if raw:
        return load_cookies_from_string(raw)

    path = "kwork_cookies.txt"
    if not os.path.exists(path):
        logger.error("Нет cookies! Установи KWORK_COOKIE или создай kwork_cookies.txt")
        sys.exit(1)

    cookies = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cookies[k.strip()] = v.strip()
    return cookies


def get_csrf_token(cookies: dict) -> str:
    return cookies.get("csrf_user_token", "")


# ---------------------------------------------------------------------------
# Генерация текста + цены через LLM
# ---------------------------------------------------------------------------

async def generate_response_text(project: dict, profile: dict) -> Optional[str]:
    """
    Генерирует текст отклика и цену через LLM.
    Возвращает сырой ответ в формате:
        Цена: XXXX

        Текст отклика...
    """
    if not AI_BASE_URL or not AI_TOKEN:
        logger.error("AI не настроен (AI_BASE_URL / AI_TOKEN)")
        return None

    title = project.get("title", "")
    description = project.get("description", "")
    price = project.get("price", "—")
    category = project.get("category", "")

    profile_text = profile.get("text", "").strip()
    if profile_text:
        profile_block = f"Мой профиль фрилансера:\n{profile_text}\n\n"
    else:
        profile_block = ""

    user_prompt = (
        f"{profile_block}"
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
        "max_tokens": 2000,
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


def parse_response(content: str, fallback_price: int) -> tuple[int, str, str]:
    """
    Парсит ответ ИИ формата:
        Цена: XXXX
        Срок: X (дней/часов)

        Текст отклика...

    Возвращает (цена, срок, текст).
    """
    price = fallback_price
    duration = "1 день"
    text = content

    price_match = re.match(r'Цена:\s*(\d+)', content)
    if price_match:
        try:
            parsed = int(price_match.group(1))
            if parsed > 0:
                price = parsed
        except ValueError:
            pass
        content = content[price_match.end():].strip()

    dur_match = re.match(r'Срок:\s*(.+)', content)
    if dur_match:
        duration = dur_match.group(1).strip()
        content = content[dur_match.end():].strip()

    text = content
    if not text:
        text = content

    return price, duration, text


def parse_price_and_text(content: str, fallback_price: int) -> tuple[int, str]:
    """Совместимость со старым API."""
    price, _, text = parse_response(content, fallback_price)
    return price, text


# ---------------------------------------------------------------------------
# Отправка отклика через API Kwork
# ---------------------------------------------------------------------------

async def check_can_offer(client: httpx.AsyncClient, want_id: str) -> dict | None:
    """Проверяет, можно ли отправить отклик на проект."""
    url = f"{BASE_URL}{CHECK_OFFER_NOTIFY_ENDPOINT.format(want_id=want_id)}"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            logger.info("check_offer_notify: %s", json.dumps(data, ensure_ascii=False))
            return data
        else:
            logger.error("check_offer_notify: HTTP %d — %s", resp.status_code, resp.text[:300])
            return None
    except Exception as e:
        logger.error("check_offer_notify error: %s", e)
        return None


async def check_template(client: httpx.AsyncClient, description: str, want_id: str) -> dict | None:
    """Проверяет текст на шаблонность."""
    form = httpx.FormData({
        "description": description,
        "wantid": want_id,
    })
    try:
        resp = await client.post(
            f"{BASE_URL}{CHECK_TEMPLATE_ENDPOINT}",
            data=form,
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info("check_is_template: %s", json.dumps(data, ensure_ascii=False))
            return data
        else:
            logger.warning("check_is_template: HTTP %d", resp.status_code)
            return None
    except Exception as e:
        logger.warning("check_is_template error: %s", e)
        return None


async def send_offer(
    client: httpx.AsyncClient,
    want_id: str,
    description: str,
    csrf_token: str,
    price: Optional[int] = None,
    duration: int = 1,
    offer_name: str = "",
) -> bool:
    """
    Отправляет отклик на проект через Kwork API.
    Эндпоинт: POST /api/offer/createoffer (FormData)
    """
    url = f"{BASE_URL}{OFFER_CREATE_ENDPOINT}"

    form = httpx.FormData({
        "wantId": want_id,
        "offerType": "custom",
        "description": description,
        "kwork_duration": str(duration),
        "kwork_price": str(price) if price else "",
    })

    if offer_name:
        form.add("kwork_name", offer_name)

    logger.info("Отправляю отклик: %s", url)
    logger.info("Данные: wantId=%s, offerType=custom, price=%s, duration=%s",
                want_id, price, duration)
    logger.info("Текст (первые 150 символов): %s", description[:150])

    try:
        resp = await client.post(
            url,
            data=form,
            headers={
                "Referer": f"https://kwork.ru/new_offer?project={want_id}",
            },
        )
        status = resp.status_code
        body = resp.text[:2000]

        logger.info("HTTP %d", status)
        logger.info("Response: %s", body[:500])

        if status in (200, 201):
            try:
                data = resp.json()
                if data.get("success") or data.get("status") == "success":
                    logger.info(">>> Отклик успешно отправлен! Проект %s", want_id)
                    return True
                if data.get("error") or data.get("status") == "error":
                    logger.error(">>> Ошибка: %s", data)
                    return False
            except json.JSONDecodeError:
                pass
            logger.info(">>> Отклик отправлен (статус %d)!", status)
            return True
        elif status == 403:
            logger.error(">>> 403 Forbidden — cookies протухли")
        elif status == 422:
            logger.error(">>> 422 Unprocessable — неверные данные. Body: %s", body)
        elif status == 404:
            logger.error(">>> 404 — эндпоинт не найден")
        elif status == 302:
            logger.info(">>> 302 Redirect — возможно успех (проверь вручную)")
            return True
        else:
            logger.error(">>> Неожиданный статус %d: %s", status, body[:300])

        return False

    except Exception as e:
        logger.error("Ошибка отправки отклика: %s", e)
        return False


async def send_offer_with_cookies(
    cookie_text: str,
    want_id: str,
    description: str,
    price: int,
    duration: int = 1,
) -> bool:
    """
    Отправляет отклик используя куки из строки (как получено от юзера).
    """
    cookies = load_cookies_from_string(cookie_text)
    if not cookies:
        logger.error("Не удалось распарсить куки")
        return False

    csrf_token = get_csrf_token(cookies)
    if not csrf_token:
        logger.warning("csrf_user_token не найден в cookies")

    async with httpx.AsyncClient(
        headers=HEADERS,
        cookies=cookies,
        follow_redirects=False,
        timeout=30,
    ) as client:
        check_data = await check_can_offer(client, want_id)
        if not check_data or not check_data.get("success"):
            logger.error(
                "Нельзя отправить отклик на проект %s: %s",
                want_id,
                json.dumps(check_data, ensure_ascii=False) if check_data else "no response",
            )
            return False

        return await send_offer(
            client,
            want_id=want_id,
            description=description,
            csrf_token=csrf_token,
            price=price,
            duration=duration,
            offer_name="",
        )


# ---------------------------------------------------------------------------
# CLI (backwards compatible)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Автоотклик Kwork")
    parser.add_argument("--project-id", "-p", required=True, help="ID проекта на Kwork")
    parser.add_argument("--dry-run", "-d", action="store_true",
                        help="Сгенерировать текст, но НЕ отправлять отклик")
    parser.add_argument("--profile", "-f", required=True,
                        help="JSON-файл с профилем пользователя")
    args = parser.parse_args()

    with open(args.profile, "r", encoding="utf-8") as f:
        profile = json.load(f)
    logger.info("Профиль загружен из %s", args.profile)

    async def _run():
        import parser as kwork_parser

        cookies = load_cookies()
        if not cookies:
            logger.error("Нет cookies — отклик невозможен")
            sys.exit(1)

        async with httpx.AsyncClient(
            headers=HEADERS,
            cookies=cookies,
            follow_redirects=False,
            timeout=30,
        ) as client:
            resp = await client.get(f"{BASE_URL}/projects/{args.project_id}/view")
            if resp.status_code != 200:
                logger.error("Не удалось загрузить проект: HTTP %d", resp.status_code)
                sys.exit(1)
            wants = kwork_parser.extract_wants_json(resp.text)
            project_info = wants[0] if wants else {}
            title = project_info.get("name", f"Проект {args.project_id}").strip()
            description = kwork_parser.clean_text(project_info.get("description") or "")
            price_limit = project_info.get("priceLimit", 0)

            project = {
                "id": args.project_id,
                "title": title,
                "price": str(price_limit),
                "description": description,
                "category": "",
            }

            raw_response = await generate_response_text(project, profile)
            if not raw_response:
                logger.error("Не удалось сгенерировать текст отклика")
                sys.exit(1)

            price_val = int(float(price_limit))
            suggested_price, response_text = parse_price_and_text(raw_response, price_val)

            logger.info("\n" + "=" * 60)
            logger.info("ПРЕДЛОЖЕННАЯ ЦЕНА: %d руб.", suggested_price)
            logger.info("ТЕКСТ ОТКЛИКА:")
            logger.info(response_text)
            logger.info("=" * 60)

            if args.dry_run:
                logger.info("[DRY RUN] Отклик НЕ отправлен")
                return

            csrf_token = get_csrf_token(cookies)
            success = await send_offer(
                client,
                want_id=args.project_id,
                description=response_text,
                csrf_token=csrf_token,
                price=suggested_price,
                duration=1,
                offer_name="",
            )
            if success:
                logger.info("Готово!")
            else:
                logger.error("Не удалось отправить отклик")
                sys.exit(1)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
