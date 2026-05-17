"""
Kwork parser — чистые функции без print'ов.
"""

import asyncio
import html
import json
import re
from typing import Optional

import httpx

BASE_URL = "https://kwork.ru"
PROJECTS_URL = "https://kwork.ru/projects?view=0"

# ─── Маппинг category_id (из want JSON) → родительская категория ─────────
CATEGORIES = {
    "design":       {"name": "Дизайн",                "ids": {"37", "38", "39", "41", "81"}},
    "programming":  {"name": "Разработка и IT",       "ids": {"303", "73", "74"}},
    "writing":      {"name": "Тексты и переводы",     "ids": {"41"}},
    "seo":          {"name": "SEO и трафик",          "ids": {"24", "25", "250", "270", "68", "90"}},
    "promotion":    {"name": "Соцсети и маркетинг",   "ids": {"40"}},
    "audio":        {"name": "Аудио, видео, съемка",  "ids": {"49"}},
    "business":     {"name": "Бизнес и жизнь",        "ids": {"108", "113", "46", "59", "76", "262"}},
}

# Обратный маппинг для быстрого поиска
CAT_ID_TO_SLUG: dict[str, str] = {}
for slug, data in CATEGORIES.items():
    for cid in data["ids"]:
        CAT_ID_TO_SLUG[cid] = slug


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


def clean_text(text: str) -> str:
    """Очищает текст от мусорных символов Kwork и декодирует HTML-сущности."""
    text = html.unescape(text)
    text = re.sub(r"\[:[0-9a-fA-F]+\]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        ch
        for ch in text
        if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) <= 0x10FFFF)
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_text(text: str, max_len: int = 3000) -> str:
    """Обрезает текст по границе слова, добавляя троеточие."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len * 0.85:
        truncated = truncated[:last_space]
    return truncated.strip() + "..."


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

        price_limit = w.get("priceLimit", "")

        if price_limit:
            try:
                price_val = float(price_limit)
                price_str = f"{price_val:,.0f}".replace(",", " ")
            except ValueError:
                price_str = str(price_limit)
        else:
            price_str = "—"

        cat_id = str(w.get("category_id", ""))

        projects.append({
            "id": pid,
            "title": title,
            "price": price_str,
            "url": f"{BASE_URL}/projects/{pid}",
            "description": clean_text(w.get("description") or ""),
            "category": CAT_ID_TO_SLUG.get(cat_id),
        })

    return projects


def matches_keywords(project: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = (project["title"] + " " + project.get("description", "")).lower()
    return any(kw.lower() in text for kw in keywords)


def extract_price_value(price_str: str) -> Optional[float]:
    """Извлекает числовое значение цены из строки Kwork."""
    if not price_str or price_str == "—":
        return None
    cleaned = re.sub(r"[^\d\s.,]", "", price_str)
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def format_project_message(project: dict) -> str:
    desc = project.get("description", "")
    if desc:
        desc = truncate_text(desc, 3000)
        desc_block = f"\n\n<blockquote expandable>{desc}</blockquote>"
    else:
        desc_block = ""
    return (
        f"<b>{project['title']}</b>\n\n"
        f"💰 <b>{project['price']}</b> ₽"
        f"{desc_block}"
    )


async def fetch_page(client: httpx.AsyncClient) -> str:
    """Загружает страницу с повторными попытками."""
    delays = [2, 5, 15]
    for attempt, delay in enumerate(delays, 1):
        try:
            response = await client.get(PROJECTS_URL)
            if response.status_code == 200:
                return response.text
            if response.status_code == 403:
                await asyncio.sleep(delay)
                continue
            await asyncio.sleep(delay)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            await asyncio.sleep(delay)
    return ""
