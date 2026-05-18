"""
auto_responder.py

Proof-of-Concept автоотклика на проекты Kwork.

Логика:
    1. При обнаружении проекта с AI-оценкой 9-10/10
    2. ИИ генерирует текст отклика на основе профиля пользователя
    3. Отправляет отклик через API Kwork (POST /api/offer/createoffer)

Эндпоинты найдены через анализ JS want-worker + new-offer (18.05.2026):
    - GET  /wants/{id}/check_offer_notify  — проверка перед откликом
    - POST /api/offer/createoffer          — создание отклика (FormData)
    - POST /api/offer/editoffer            — редактирование отклика

Требования:
    - Заполненный .env (AI_BASE_URL, AI_TOKEN, AI_MODEL)
    - kwork_cookies.txt с cookies авторизованной сессии

Использование (режим dry-run):
    python auto_responder.py --dry-run --project-id 1234567

Использование (реальный отклик):
    python auto_responder.py --project-id 1234567
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

# Эндпоинты Kwork (найдены через анализ JS)
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
# Профиль пользователя для персонализации откликов
# ---------------------------------------------------------------------------
USER_PROFILE = {
    "name": os.getenv("KWORK_USER_NAME", "Фрилансер"),
    "specialization": os.getenv("KWORK_USER_SPEC", "Веб-разработчик, Python, Telegram-боты"),
    "experience": os.getenv("KWORK_USER_EXP", "5 лет в веб-разработке, 3 года на Kwork"),
    "skills": os.getenv("KWORK_USER_SKILLS", "Python, JavaScript, React, Node.js, PostgreSQL, Docker"),
    "portfolio": os.getenv("KWORK_USER_PORTFOLIO", ""),
    "strengths": os.getenv("KWORK_USER_STRENGTHS", "Быстрое выполнение, внимательность к деталям, всегда на связи"),
    "rate": os.getenv("KWORK_USER_RATE", "2000 руб/час"),
    "kwork_link": os.getenv("KWORK_USER_LINK", ""),
}

# ---------------------------------------------------------------------------
# Трекинг отправленных откликов
# ---------------------------------------------------------------------------
SENT_OFFERS_FILE = "sent_offers.json"


def load_sent_offers() -> set[str]:
    if not os.path.exists(SENT_OFFERS_FILE):
        return set()
    try:
        with open(SENT_OFFERS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, TypeError):
        return set()


def save_sent_offers(ids: set[str]) -> None:
    with open(SENT_OFFERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(ids), f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Загрузка Cookies
# ---------------------------------------------------------------------------

def load_cookies() -> dict[str, str]:
    raw = os.getenv("KWORK_COOKIE", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            cookies = {}
            for pair in raw.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    cookies[k.strip()] = v.strip()
            return cookies

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
# Генерация текста отклика через LLM
# ---------------------------------------------------------------------------

_RESPONSE_SYSTEM_PROMPT = (
    "Ты — фрилансер на Kwork. Ты просматриваешь проект и должен написать "
    "короткий, убедительный отклик (предложение) заказчику.\n\n"
    "ПРАВИЛА:\n"
    "- Пиши на русском языке\n"
    "- Длина: 3-7 предложений (максимум 800 символов)\n"
    "- Начни с персонализированного обращения к задаче заказчика (покажи, что прочитал описание)\n"
    "- Кратко опиши свой релевантный опыт\n"
    "- Предложи конкретные сроки (если возможно оценить)\n"
    "- Закончи вопросом или призывом к действию (например, предложи обсудить детали)\n"
    "- НЕ используй шаблонные фразы вроде 'Здравствуйте, я фрилансер с опытом...'\n"
    "- НЕ предлагай цену ниже указанной в заказе\n"
    "- НЕ пересказывай всё описание заказа — покажи, что ты понял суть\n"
    "- Будь конкретным и профессиональным\n\n"
    "Ответь ТОЛЬКО текстом отклика, без кавычек, без markdown, без 'Вот отклик:'."
)


async def generate_response_text(project: dict, profile: dict) -> Optional[str]:
    if not AI_BASE_URL or not AI_TOKEN:
        logger.error("AI не настроен (AI_BASE_URL / AI_TOKEN)")
        return None

    title = project.get("title", "")
    description = project.get("description", "")
    price = project.get("price", "—")
    category = project.get("category", "")

    user_prompt = (
        f"Твой профиль на Kwork:\n"
        f"- Специализация: {profile.get('specialization', '')}\n"
        f"- Опыт: {profile.get('experience', '')}\n"
        f"- Навыки: {profile.get('skills', '')}\n"
        f"- Сильные стороны: {profile.get('strengths', '')}\n"
        f"- Ставка: {profile.get('rate', '')}\n"
        f"- Портфолио: {profile.get('portfolio', '')}\n\n"
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
        "temperature": 0.7,
        "max_tokens": 1000,
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
                    logger.info("Текст отклика сгенерирован (%d символов)", len(content))
                    return content
                else:
                    logger.warning("Пустой ответ от LLM (попытка %d/3)", attempt)
        except Exception as e:
            logger.warning("Ошибка LLM (попытка %d/3): %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(5)

    return None


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

    Параметры FormData (из анализа JS setRequestDataOfferPage):
        wantId          — ID проекта
        offerType       — "custom" (своё предложение) или "kwork" (существующий кворк)
        description     — текст сопроводительного письма
        kwork_duration  — срок выполнения (в днях)
        kwork_price     — цена предложения
        kwork_name      — название предложения
    """
    url = f"{BASE_URL}{OFFER_CREATE_ENDPOINT}"

    # Формируем FormData как в Kwork JS
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


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

async def auto_respond(project_id: str, dry_run: bool = False) -> bool:
    """
    Полный пайплайн автоотклика:
    1. Проверяет, можно ли отправить отклик (check_offer_notify)
    2. Загружает данные проекта
    3. Генерирует текст через LLM
    4. Отправляет (или симулирует в dry-run)
    """
    sent_offers = load_sent_offers()
    if project_id in sent_offers:
        logger.info("Проект %s — уже был отклик, пропускаю", project_id)
        return False

    cookies = load_cookies()
    if not cookies:
        logger.error("Нет cookies — отклик невозможен")
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
        # Шаг 1: Проверка — можно ли отправить отклик
        logger.info("=== Шаг 1: Проверка check_offer_notify ===")
        check_data = await check_can_offer(client, project_id)
        if not check_data or not check_data.get("success"):
            logger.error("Нельзя отправить отклик на проект %s", project_id)
            return False

        # Шаг 2: Загружаем данные проекта
        logger.info("=== Шаг 2: Загрузка проекта %s ===", project_id)
        try:
            resp = await client.get(f"{BASE_URL}/projects/{project_id}/view")
            if resp.status_code != 200:
                logger.error("Не удалось загрузить проект: HTTP %d", resp.status_code)
                return False
            html = resp.text
        except Exception as e:
            logger.error("Ошибка загрузки проекта: %s", e)
            return False

        # Извлекаем описание из embedded JSON
        import parser as kwork_parser
        wants = kwork_parser.extract_wants_json(html)
        project_info = wants[0] if wants else {}

        title = project_info.get("name", f"Проект {project_id}").strip()

        price_limit = project_info.get("priceLimit", "—")
        price_str = "—"
        if price_limit:
            try:
                price_str = f"{float(price_limit):,.0f}".replace(",", " ")
            except ValueError:
                price_str = str(price_limit)

        description = kwork_parser.clean_text(project_info.get("description") or "")

        project = {
            "id": project_id,
            "title": title,
            "price": price_str,
            "description": description,
            "category": "",
        }

        logger.info("Проект: %s | %s руб.", title, price_str)

        # Шаг 3: Генерация текста отклика
        logger.info("=== Шаг 3: Генерация текста отклика ===")
        response_text = await generate_response_text(project, USER_PROFILE)
        if not response_text:
            logger.error("Не удалось сгенерировать текст отклика")
            return False

        logger.info("\n" + "=" * 60)
        logger.info("ТЕКСТ ОТКЛИКА:")
        logger.info(response_text)
        logger.info("=" * 60)

        if dry_run:
            logger.info("[DRY RUN] Отклик НЕ отправлен (флаг --dry-run)")
            return True

        # Шаг 4: Отправка отклика
        logger.info("=== Шаг 4: Отправка отклика ===")
        price_value = None
        try:
            price_value = int(float(str(price_limit).replace(" ", "")))
        except (ValueError, TypeError):
            pass

        success = await send_offer(
            client,
            want_id=project_id,
            description=response_text,
            csrf_token=csrf_token,
            price=price_value,
            duration=1,
            offer_name="",
        )

        if success:
            sent_offers.add(project_id)
            save_sent_offers(sent_offers)
            logger.info(">>> Сохранено в %s", SENT_OFFERS_FILE)

        return success


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Автоотклик Kwork — PoC")
    parser.add_argument("--project-id", "-p", required=True, help="ID проекта на Kwork")
    parser.add_argument("--dry-run", "-d", action="store_true",
                        help="Сгенерировать текст, но НЕ отправлять отклик")
    parser.add_argument("--profile", "-f",
                        help="JSON-файл с профилем пользователя")
    args = parser.parse_args()

    global USER_PROFILE
    if args.profile:
        with open(args.profile, "r", encoding="utf-8") as f:
            USER_PROFILE = json.load(f)
        logger.info("Профиль загружен из %s", args.profile)

    success = asyncio.run(auto_respond(args.project_id, dry_run=args.dry_run))

    if success:
        logger.info("Готово!")
    else:
        logger.error("Не удалось выполнить автоотклик")
        sys.exit(1)


if __name__ == "__main__":
    main()
