"""
Kwork Sniper — Telegram bot + parser in one process.

Run:
    python main.py

Dependencies:
    pip install aiogram httpx python-dotenv
"""

import asyncio
import html as html_lib
import json
import logging
import os
from collections import deque
from datetime import datetime
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

import ai_analyzer
import database as db
import parser
from telegram_bot import router, get_cached_filters, cache_projects, set_runtime_stats

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ERROR_CHAT_ID = os.getenv("ERROR_CHAT_ID", "")
POLL_INTERVAL = 20  # seconds between Kwork checks
SEEN_MAXLEN = 1500  # сколько ID хранить в памяти (старые вытесняются)
SEEN_IDS_PATH = Path(os.getenv("SEEN_IDS_PATH", "seen_projects.json"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_seen_ids() -> deque[str]:
    try:
        raw = json.loads(SEEN_IDS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return deque((str(item) for item in raw[-SEEN_MAXLEN:]), maxlen=SEEN_MAXLEN)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("[Parser] Failed to load seen IDs: %s", exc)
    return deque(maxlen=SEEN_MAXLEN)


def save_seen_ids(seen_ids: deque[str]) -> None:
    try:
        SEEN_IDS_PATH.write_text(
            json.dumps(list(seen_ids), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("[Parser] Failed to save seen IDs: %s", exc)


def build_match_reason(project: dict, user: dict, ai_rating: str | None = None) -> str:
    reasons = []

    cat = project.get("category")
    if cat and cat in parser.CATEGORIES:
        reasons.append(f"категория: {parser.CATEGORIES[cat]['name']}")
    elif not cat:
        reasons.append("категория не распознана")

    keywords = user.get("keywords", [])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except json.JSONDecodeError:
            keywords = []
    matched_keywords = [
        kw for kw in keywords
        if kw.lower() in (project.get("title", "") + " " + project.get("description", "")).lower()
    ]
    if matched_keywords:
        reasons.append("ключевые слова: " + ", ".join(matched_keywords[:5]))

    price_val = parser.extract_price_value(project.get("price", ""))
    min_p = user.get("min_price")
    max_p = user.get("max_price")
    if price_val is not None and (min_p is not None or max_p is not None):
        reasons.append(f"цена: {project.get('price')} ₽")

    if ai_rating and user.get("ai_enabled"):
        reasons.append(f"AI: {ai_rating}")

    if not reasons:
        reasons.append("подошёл по текущим фильтрам")

    return "\n\n<b>Почему пришёл:</b> " + "; ".join(reasons)


async def notify_error(bot: Bot, message: str) -> None:
    if not ERROR_CHAT_ID:
        return
    try:
        safe_message = html_lib.escape(message[:3500])
        await bot.send_message(ERROR_CHAT_ID, f"<b>Kwork Sniper error</b>\n<code>{safe_message}</code>")
    except Exception as exc:
        logger.warning("[Bot] Failed to send error notification: %s", exc)


def is_quiet_hour(now_hour: int, start: int | None, end: int | None) -> bool:
    if start is None or end is None:
        return False
    if start == end:
        return False
    if start < end:
        return start <= now_hour < end
    else:
        return now_hour >= start or now_hour < end


async def run_parser(bot: Bot):
    """Background task: polls Kwork and broadcasts new projects."""
    seen_ids = load_seen_ids()
    first_run = not bool(seen_ids)

    logger.info("[Parser] Starting... seen_ids=%d", len(seen_ids))

    async with httpx.AsyncClient(
        headers=parser.HEADERS, follow_redirects=True, timeout=30
    ) as client:
        while True:
            try:
                html = await parser.fetch_page(client)
                if not html:
                    logger.warning("[Parser] Empty response from Kwork")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                projects = parser.parse_projects(html)
                if not projects:
                    logger.warning("[Parser] No projects parsed")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                cache_projects(projects)
                set_runtime_stats(
                    last_cycle_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    last_projects_count=len(projects),
                    last_error=None,
                )

                new_projects = []
                seen_changed = False
                for p in projects:
                    if p["id"] in seen_ids:
                        continue
                    seen_ids.append(p["id"])
                    seen_changed = True
                    if not first_run:
                        new_projects.append(p)

                if seen_changed:
                    save_seen_ids(seen_ids)

                if not first_run and new_projects:
                    users = await db.get_subscribed_users()
                    logger.info(
                        "[Parser] New projects: %d | Subscribers: %d",
                        len(new_projects),
                        len(users),
                    )

                    base_keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="🔗 Открыть заказ",
                                    url="",
                                )
                            ]
                        ]
                    )

                    for project in new_projects:
                        # ─── Первый проход: собираем recipients ────────────
                        recipients = []
                        for user in users:
                            # Фильтр по категориям
                            user_filters = await get_cached_filters(user["id"])
                            if user_filters is None:
                                user_filters = await db.get_user_filters(user["id"])
                            if not user_filters:
                                continue
                            cat = project.get("category")
                            if cat and cat not in user_filters:
                                continue

                            # Фильтр по ключевым словам
                            user_keywords = user.get("keywords", [])
                            if isinstance(user_keywords, str):
                                try:
                                    user_keywords = json.loads(user_keywords)
                                except json.JSONDecodeError:
                                    user_keywords = []
                            if user_keywords and not parser.matches_keywords(project, user_keywords):
                                continue

                            # Фильтр по цене
                            price_val = parser.extract_price_value(project.get("price", ""))
                            min_p = user.get("min_price")
                            max_p = user.get("max_price")
                            if price_val is not None:
                                if min_p is not None and price_val < min_p:
                                    continue
                                if max_p is not None and price_val > max_p:
                                    continue

                            # Тихий час
                            now_hour = datetime.now().hour
                            qs = user.get("quiet_hours_start")
                            qe = user.get("quiet_hours_end")
                            if is_quiet_hour(now_hour, qs, qe):
                                continue

                            recipients.append(user)

                        if not recipients:
                            continue

                        # ─── AI-оценка (только если хоть у кого-то включена) ─
                        ai_rating = None
                        need_ai = any(u.get("ai_enabled") for u in recipients)
                        logger.info(
                            "[AI] Project %s | recipients=%d | need_ai=%s | ai_enabled_flags=%s",
                            project["id"],
                            len(recipients),
                            need_ai,
                            [u.get("ai_enabled") for u in recipients],
                        )
                        if need_ai:
                            ai_rating = await ai_analyzer.get_rating(project)
                            logger.info(
                                "[AI] Project %s | rating=%s",
                                project["id"],
                                ai_rating,
                            )
                            if ai_rating is None:
                                logger.warning(
                                    "[AI] Rating unavailable for project %s",
                                    project["id"],
                                )

                        base_text = parser.format_project_message(project)
                        for user in recipients:
                            text = base_text
                            if user.get("ai_enabled"):
                                score = ai_analyzer.parse_score(ai_rating)
                                min_score = user.get("ai_min_score")
                                if min_score is not None and score is None:
                                    logger.info(
                                        "[AI] Skip user %s — score unavailable, min %s",
                                        user["id"], min_score,
                                    )
                                    continue
                                if min_score is not None and score is not None and score < min_score:
                                    logger.info(
                                        "[AI] Skip user %s — score %s < min %s",
                                        user["id"], score, min_score,
                                    )
                                    continue
                                if ai_rating:
                                    text += ai_analyzer.format_rating_line(ai_rating)
                                    logger.info(
                                        "[AI] Added rating for user %s: %s",
                                        user["id"],
                                        ai_rating,
                                    )

                            text += build_match_reason(project, user, ai_rating)

                            if user.get("auto_respond_enabled"):
                                user_keyboard = InlineKeyboardMarkup(
                                    inline_keyboard=[
                                        [
                                            InlineKeyboardButton(
                                                text="🔗 Открыть заказ",
                                                url=project["url"],
                                            ),
                                            InlineKeyboardButton(
                                                text="⚡ Сгенерировать отклик",
                                                callback_data=f"autorespond:{project['id']}",
                                            ),
                                        ]
                                    ]
                                )
                            else:
                                user_keyboard = InlineKeyboardMarkup(
                                    inline_keyboard=[
                                        [
                                            InlineKeyboardButton(
                                                text="🔗 Открыть заказ",
                                                url=project["url"],
                                            )
                                        ]
                                    ]
                                )

                            try:
                                await bot.send_message(
                                    chat_id=user["id"],
                                    text=text,
                                    reply_markup=user_keyboard,
                                    disable_web_page_preview=True,
                                )
                            except Exception as exc:
                                logger.debug(
                                    "Failed to send to %s: %s", user["id"], exc
                                )

                if first_run:
                    logger.info(
                        "[Parser] Init complete: %d projects loaded", len(projects)
                    )
                    first_run = False
                else:
                    logger.info("[Parser] Cycle complete, new: %d", len(new_projects))
                set_runtime_stats(last_new_count=len(new_projects))

            except Exception as exc:
                logger.exception("[Parser] Error: %s", exc)
                set_runtime_stats(last_error=str(exc))
                await notify_error(bot, str(exc))

            await asyncio.sleep(POLL_INTERVAL)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not found in .env")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("[Bot] Starting polling...")

    parser_task = asyncio.create_task(run_parser(bot))

    try:
        await dp.start_polling(bot)
    finally:
        logger.info("[Bot] Shutting down...")
        parser_task.cancel()
        try:
            await parser_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("[Bot] Done.")


if __name__ == "__main__":
    asyncio.run(main())
