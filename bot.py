"""
Парсер новых заказов с Kwork.ru (через встроенный JSON в HTML)
Выводит в консоль: заголовок, цена, ссылка

Запуск: python bot.py
Зависимости: pip install httpx
"""

import asyncio
import json
import sys
from datetime import datetime

import httpx

# ─── Настройки ────────────────────────────────────────────────
POLL_INTERVAL = 20          # Как часто проверять (секунды)
BASE_URL = "https://kwork.ru"
PROJECTS_URL = "https://kwork.ru/projects?view=0"

# Ключевые слова для фильтрации (оставь пустым — все заказы)
# Пример: KEYWORDS = ["python", "бот", "парсинг"]
KEYWORDS = []

# Если хочешь конкретную категорию, добавь параметры:
# PROJECTS_URL = "https://kwork.ru/projects?c=11"  # c=11 — разработка
# ──────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def safe_print(text: str):
    """Печатает текст, игнорируя ошибки кодировки консоли."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoded = text.encode(sys.stdout.encoding or "cp1251", errors="replace").decode(
            sys.stdout.encoding or "cp1251"
        )
        print(encoded)


def extract_wants_json(html: str) -> list[dict]:
    """Извлекает массив wants из встроенного JSON в HTML Kwork."""
    marker = '"wants":'
    idx = html.find(marker)
    if idx == -1:
        return []

    start = idx + len(marker)
    while start < len(html) and html[start] in " \t\n\r":
        start += 1

    if start >= len(html) or html[start] != "[":
        return []

    # Ищем закрывающую ] с учётом строк и вложенности
    depth = 1
    i = start + 1
    in_str = False
    escape = False

    while i < len(html) and depth > 0:
        ch = html[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
        i += 1

    json_str = html[start:i]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return []


def parse_projects(html: str) -> list[dict]:
    """Парсит HTML, извлекая заказы из встроенного JSON."""
    wants = extract_wants_json(html)
    projects = []

    for w in wants:
        pid = str(w.get("id", ""))
        if not pid:
            continue

        title = w.get("name", "").strip()
        if not title:
            continue

        # Цена
        price_limit = w.get("priceLimit", "")
        possible_limit = w.get("possiblePriceLimit")
        is_higher = w.get("isHigherPrice", False)

        if price_limit:
            try:
                price_val = float(price_limit)
                price_str = f"{price_val:,.0f} ₽".replace(",", " ")
            except ValueError:
                price_str = str(price_limit)
        else:
            price_str = "—"

        if is_higher and possible_limit:
            try:
                poss_val = float(possible_limit)
                price_str += f" (допустимо до {poss_val:,.0f} ₽)".replace(",", " ")
            except ValueError:
                pass

        url = f"{BASE_URL}/projects/{pid}"

        projects.append({
            "id": pid,
            "title": title,
            "price": price_str,
            "url": url,
            "description": (w.get("description") or "").strip(),
        })

    return projects


def matches_keywords(project: dict) -> bool:
    """Проверяет, содержит ли заказ ключевые слова."""
    if not KEYWORDS:
        return True
    text = (project["title"] + " " + project.get("description", "")).lower()
    return any(kw.lower() in text for kw in KEYWORDS)


def print_project(project: dict):
    """Красиво выводит заказ в консоль."""
    now = datetime.now().strftime("%H:%M:%S")
    safe_print(f"\n{'─' * 55}")
    safe_print(f"🕐 {now}")
    safe_print(f"📌 {project['title']}")
    safe_print(f"💰 {project['price']}")
    safe_print(f"🔗 {project['url']}")
    safe_print(f"{'─' * 55}")


async def fetch_page(client: httpx.AsyncClient) -> str:
    """Загружает страницу с повторными попытками и backoff."""
    delays = [2, 5, 15]  # экспоненциальный backoff
    for attempt, delay in enumerate(delays, 1):
        try:
            response = await client.get(PROJECTS_URL)
            if response.status_code == 200:
                return response.text
            if response.status_code == 403:
                safe_print("[!] 403 Forbidden — попробуй добавить Cookie из браузера в HEADERS")
                await asyncio.sleep(delay)
                continue
            safe_print(f"[!] HTTP {response.status_code}, попытка {attempt}/{len(delays)}...")
            await asyncio.sleep(delay)
        except httpx.ConnectError as e:
            safe_print(f"[!] Ошибка подключения (попытка {attempt}): {e}")
            await asyncio.sleep(delay)
        except httpx.TimeoutException as e:
            safe_print(f"[!] Таймаут (попытка {attempt}): {e}")
            await asyncio.sleep(delay)
        except httpx.RequestError as e:
            safe_print(f"[!] Сетевая ошибка (попытка {attempt}): {type(e).__name__}: {e}")
            await asyncio.sleep(delay)
    return ""


async def run():
    seen_ids: set[str] = set()
    first_run = True

    safe_print("=" * 55)
    safe_print("  Kwork Parser — мониторинг новых заказов")
    safe_print(f"  Интервал: {POLL_INTERVAL} сек")
    if KEYWORDS:
        safe_print(f"  Фильтр: {', '.join(KEYWORDS)}")
    safe_print("=" * 55)

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=30
    ) as client:
        while True:
            try:
                safe_print(f"\n[→] Запрос... {datetime.now().strftime('%H:%M:%S')}")
                html = await fetch_page(client)

                if not html:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                projects = parse_projects(html)

                if not projects:
                    safe_print("[!] Заказы не найдены — возможно, изменилась структура HTML")
                    safe_print("    Сохраняю HTML в debug.html для анализа...")
                    with open("debug.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                new_count = 0
                for project in projects:
                    if project["id"] in seen_ids:
                        continue
                    seen_ids.add(project["id"])

                    if not first_run and matches_keywords(project):
                        print_project(project)
                        new_count += 1

                if first_run:
                    safe_print(f"[✓] Инициализация: загружено {len(projects)} заказов")
                    safe_print("    Слежу за новыми...\n")
                    first_run = False
                elif new_count > 0:
                    safe_print(f"[✓] Найдено новых: {new_count}")
                else:
                    safe_print("[✓] Новых заказов нет")

            except Exception as e:
                safe_print(f"[!] Неожиданная ошибка: {e}")

            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        safe_print("\n\n[✓] Остановлен.")
