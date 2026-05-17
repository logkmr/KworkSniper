"""
Telegram bot handlers for Kwork Sniper (aiogram 3.x).
"""

import asyncio
from typing import Optional

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import database as db
import parser

router = Router()

# In-memory cache пользователей (user_id -> row dict)
_user_cache: dict[int, dict] = {}


class Form(StatesGroup):
    keywords = State()
    min_price = State()
    max_price = State()


async def _cache_get(user_id: int) -> dict | None:
    """Возвращает пользователя из кэша или загружает из БД."""
    if user_id in _user_cache:
        return _user_cache[user_id]
    user = await db.get_user(user_id)
    if user:
        _user_cache[user_id] = user
    return user


async def get_cached_filters(user_id: int) -> list[str] | None:
    """Возвращает фильтры пользователя из кэша (или None если нет)."""
    user = await _cache_get(user_id)
    if user is None:
        return None
    return list(user.get("filters", []))


def _cache_update(user_id: int, **fields):
    """Обновляет кэш пользователя и пишет в БД в фоне."""
    if user_id in _user_cache:
        _user_cache[user_id].update(fields)
    asyncio.create_task(_bg_patch(user_id, fields))


async def _bg_patch(user_id: int, fields: dict):
    """Фоновый патч в Supabase (без блокировки UI)."""
    try:
        if "notifications_enabled" in fields and len(fields) == 1:
            await db.toggle_notifications(user_id, fields["notifications_enabled"])
        elif "filters" in fields and len(fields) == 1:
            await db.set_user_filters(user_id, fields["filters"])
        elif "keywords" in fields and len(fields) == 1:
            await db.set_keywords(user_id, fields["keywords"])
        elif "ai_enabled" in fields and len(fields) == 1:
            await db.set_ai_enabled(user_id, fields["ai_enabled"])
        elif "ai_min_score" in fields and len(fields) == 1:
            await db.set_ai_min_score(user_id, fields["ai_min_score"])
        elif "min_price" in fields or "max_price" in fields:
            await db.set_price_range(
                user_id,
                fields.get("min_price"),
                fields.get("max_price"),
            )
        elif "quiet_hours_start" in fields or "quiet_hours_end" in fields:
            await db.set_quiet_hours(
                user_id,
                fields.get("quiet_hours_start"),
                fields.get("quiet_hours_end"),
            )
        else:
            for k, v in fields.items():
                if k == "notifications_enabled":
                    await db.toggle_notifications(user_id, v)
                elif k == "filters":
                    await db.set_user_filters(user_id, v)
                elif k == "keywords":
                    await db.set_keywords(user_id, v)
                elif k == "ai_enabled":
                    await db.set_ai_enabled(user_id, v)
                elif k == "ai_min_score":
                    await db.set_ai_min_score(user_id, v)
                elif k == "min_price":
                    await db.set_price_range(user_id, v, None)
                elif k == "max_price":
                    await db.set_price_range(user_id, None, v)
                elif k == "quiet_hours_start":
                    await db.set_quiet_hours(user_id, v, None)
                elif k == "quiet_hours_end":
                    await db.set_quiet_hours(user_id, None, v)
    except Exception:
        pass


def _main_menu_text(first_name: str) -> str:
    return (
        f"👋 Привет, {first_name or 'друг'}!\n\n"
        f"Я <b>Kwork Sniper</b> — слежу за новыми заказами на Kwork.ru "
        f"и присылаю уведомления, как только появляется что-то новое.\n\n"
        f"Используй кнопки ниже для настроек."
    )


async def _send_main_menu(event: types.Message | CallbackQuery, user_id: int):
    """Отправляет или редактирует главное меню."""
    user = await _cache_get(user_id)
    enabled = user.get("notifications_enabled", True) if user else True
    text = _main_menu_text(
        event.from_user.first_name if hasattr(event, "from_user") else "друг"
    )
    markup = _notif_keyboard(enabled)
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


def _notif_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    text = "🔔 Уведомления включены" if enabled else "🔕 Уведомления выключены"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data="toggle_notif")],
            [InlineKeyboardButton(text="🤖 AI-анализ", callback_data="open_ai")],
            [InlineKeyboardButton(text="⚙️ Фильтры категорий", callback_data="open_filters")],
            [InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="open_keywords")],
            [InlineKeyboardButton(text="💰 Диапазон цены", callback_data="open_price")],
            [InlineKeyboardButton(text="🌙 Тихий час", callback_data="open_quiet")],
        ]
    )


def _filters_keyboard(user_filters: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for slug, data in parser.CATEGORIES.items():
        enabled = slug in user_filters
        icon = "✅" if enabled else "❌"
        rows.append(
            [InlineKeyboardButton(text=f"{icon} {data['name']}", callback_data=f"filter:{slug}")]
        )
    rows.append([
        InlineKeyboardButton(text="✅ Включить все", callback_data="filter:all:on"),
        InlineKeyboardButton(text="❌ Выключить все", callback_data="filter:all:off"),
    ])
    rows.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username

    user = await _cache_get(user_id)
    if user is None:
        user = await db.add_user(user_id, username)
        if user:
            _user_cache[user_id] = user

    await _send_main_menu(message, user_id)


@router.callback_query(F.data == "toggle_notif")
async def callback_toggle(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    user = await _cache_get(user_id)
    if user is None:
        user = await db.add_user(user_id, callback.from_user.username)
        if user:
            _user_cache[user_id] = user

    current = user.get("notifications_enabled", True) if user else True
    new_state = not current

    await callback.answer(
        "Уведомления включены ✅" if new_state else "Уведомления выключены ❌"
    )

    _cache_update(user_id, notifications_enabled=new_state)
    await callback.message.edit_reply_markup(
        reply_markup=_notif_keyboard(new_state)
    )


@router.callback_query(F.data == "open_filters")
async def callback_open_filters(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    user_filters = user.get("filters", []) if user else []

    await callback.message.edit_text(
        "⚙️ <b>Настройка фильтров</b>\n\n"
        "Выбери категории заказов, которые хочешь получать.\n"
        "Если ничего не выбрано — <b>ничего не приходит</b>.",
        reply_markup=_filters_keyboard(user_filters),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await _send_main_menu(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("filter:"))
async def callback_filter(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]

    user = await _cache_get(user_id)
    user_filters = list(user.get("filters", [])) if user else []

    if action == "all:on":
        all_slugs = list(parser.CATEGORIES.keys())
        _cache_update(user_id, filters=all_slugs)
        user_filters = all_slugs
        await callback.answer("Все категории включены ✅")
    elif action == "all:off":
        _cache_update(user_id, filters=[])
        user_filters = []
        await callback.answer("Все категории выключены ❌")
    else:
        slug = action
        if slug in user_filters:
            user_filters.remove(slug)
            status = "Выключено"
        else:
            user_filters.append(slug)
            status = "Включено"

        _cache_update(user_id, filters=user_filters)
        await callback.answer(
            f"{status}: {parser.CATEGORIES[slug]['name']}"
        )

    await callback.message.edit_reply_markup(
        reply_markup=_filters_keyboard(user_filters)
    )


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    user = await _cache_get(user_id)

    if user is None:
        await message.answer("Ты ещё не зарегистрирован. Нажми /start")
        return

    enabled = user.get("notifications_enabled", True)
    ai_enabled = user.get("ai_enabled", False)
    ai_min_score = user.get("ai_min_score")
    filters = user.get("filters", [])
    keywords = user.get("keywords", [])
    min_p = user.get("min_price")
    max_p = user.get("max_price")
    qs = user.get("quiet_hours_start")
    qe = user.get("quiet_hours_end")

    status_text = "✅ включены" if enabled else "❌ выключены"
    ai_min_text = f"мин. {ai_min_score}" if ai_min_score is not None else "без фильтра"
    ai_text = f"{'✅ включена' if ai_enabled else '❌ выключена'} ({ai_min_text})"
    if filters:
        filter_names = [parser.CATEGORIES[s]["name"] for s in filters if s in parser.CATEGORIES]
        filters_text = ", ".join(filter_names)
    else:
        filters_text = "ничего не выбрано"

    kw_text = ", ".join(keywords) if keywords else "не заданы"
    price_text = ""
    if min_p is not None and max_p is not None:
        price_text = f"от {min_p} до {max_p} ₽"
    elif min_p is not None:
        price_text = f"от {min_p} ₽"
    elif max_p is not None:
        price_text = f"до {max_p} ₽"
    else:
        price_text = "не задан"

    quiet_text = f"с {qs}:00 до {qe}:00" if qs is not None and qe is not None else "не задан"

    await message.answer(
        f"📊 <b>Статус</b>\n\n"
        f"Уведомления: {status_text}\n"
        f"AI-анализ: {ai_text}\n"
        f"Категории: {filters_text}\n"
        f"Ключевые слова: {kw_text}\n"
        f"Цена: {price_text}\n"
        f"Тихий час: {quiet_text}\n\n"
        f"Нажми /start чтобы управлять.",
        parse_mode="HTML",
    )


# ─── AI-анализ ────────────────────────────────────────────────

def _ai_keyboard(ai_enabled: bool, min_score: Optional[int]) -> InlineKeyboardMarkup:
    toggle_text = "✅ AI-оценка: включена" if ai_enabled else "❌ AI-оценка: выключена"
    min_text = f"Мин. оценка: {min_score}" if min_score is not None else "Мин. оценка: не задана"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="ai:toggle")],
            [InlineKeyboardButton(text=min_text, callback_data="ai:min_score")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
        ]
    )


def _ai_min_score_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for row_start in range(1, 11, 5):
        row = []
        for score in range(row_start, min(row_start + 5, 11)):
            row.append(InlineKeyboardButton(text=str(score), callback_data=f"ai_score:{score}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🗑 Сбросить", callback_data="ai_score:reset")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_ai")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_ai_screen(event: types.Message | CallbackQuery, user: dict | None):
    ai_enabled = user.get("ai_enabled", False) if user else False
    min_score = user.get("ai_min_score")
    markup = _ai_keyboard(ai_enabled, min_score)
    text = (
        "🤖 <b>AI-анализ</b>\n\n"
        "Бот оценивает каждый заказ по шкале 1–10. "
        "Ты можешь настроить, чтобы заказы ниже определённой оценки не приходили.\n\n"
        f"Сейчас AI {'включён' if ai_enabled else 'выключен'}."
    )
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "open_ai")
async def cb_open_ai(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    await _show_ai_screen(callback, user)


@router.callback_query(F.data == "ai:toggle")
async def cb_ai_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    current = user.get("ai_enabled", False) if user else False
    new_state = not current
    _cache_update(user_id, ai_enabled=new_state)
    await callback.answer("AI включён 🤖" if new_state else "AI выключен ❌")
    user = await _cache_get(user_id)
    await _show_ai_screen(callback, user)


@router.callback_query(F.data == "ai:min_score")
async def cb_ai_min_score_select(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🤖 <b>Минимальная оценка</b>\n\n"
        "Выбери минимальную оценку заказа (1–10). "
        "Заказы ниже этой оценки не будут приходить.\n\n"
        "🗑 Сброс — отменить фильтр.",
        reply_markup=_ai_min_score_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai_score:"))
async def cb_set_ai_min_score(callback: CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]

    if action == "reset":
        await db.set_ai_min_score(user_id, None)
        if user_id in _user_cache:
            _user_cache[user_id]["ai_min_score"] = None
        await callback.answer("Фильтр сброшен 🗑")
    else:
        score = int(action)
        await db.set_ai_min_score(user_id, score)
        if user_id in _user_cache:
            _user_cache[user_id]["ai_min_score"] = score
        await callback.answer(f"Мин. оценка: {score}")

    user = await _cache_get(user_id)
    await _show_ai_screen(callback, user)


@router.callback_query(F.data == "back_to_ai")
async def cb_back_to_ai(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    await _show_ai_screen(callback, user)


# ─── Ключевые слова ───────────────────────────────────────────

def _input_cancel_keyboard(cancel_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data)]
        ]
    )


def _keywords_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_keywords")],
            [InlineKeyboardButton(text="🗑 Сбросить", callback_data="reset_keywords")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
        ]
    )


@router.callback_query(F.data == "open_keywords")
async def cb_open_keywords(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    kw = user.get("keywords", []) if user else []
    kw_text = ", ".join(kw) if kw else "не заданы"
    await callback.message.edit_text(
        f"🔑 <b>Ключевые слова</b>\n\n"
        f"Сейчас: {kw_text}\n\n"
        f"Бот присылает только заказы, где есть хотя бы одно из этих слов "
        f"в заголовке или описании.",
        reply_markup=_keywords_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "edit_keywords")
async def cb_edit_keywords(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "🔑 <b>Ключевые слова</b>\n\n"
        "Отправь новые ключевые слова через запятую.",
        reply_markup=_input_cancel_keyboard("cancel:keywords"),
        parse_mode="HTML",
    )
    await state.set_state(Form.keywords)


@router.callback_query(F.data == "reset_keywords")
async def cb_reset_keywords(callback: CallbackQuery):
    await callback.answer("Ключевые слова сброшены 🗑")
    user_id = callback.from_user.id
    await db.set_keywords(user_id, [])
    if user_id in _user_cache:
        _user_cache[user_id]["keywords"] = []
    user = await _cache_get(user_id)
    kw = user.get("keywords", []) if user else []
    kw_text = ", ".join(kw) if kw else "не заданы"
    await callback.message.edit_text(
        f"🔑 <b>Ключевые слова</b>\n\n"
        f"Сейчас: {kw_text}\n\n"
        f"Бот присылает только заказы, где есть хотя бы одно из этих слов "
        f"в заголовке или описании.",
        reply_markup=_keywords_keyboard(),
        parse_mode="HTML",
    )


@router.message(Form.keywords)
async def process_keywords(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()
    if text == "-":
        keywords = []
    else:
        keywords = [k.strip().lower() for k in text.split(",") if k.strip()]

    await db.set_keywords(user_id, keywords)
    if user_id in _user_cache:
        _user_cache[user_id]["keywords"] = keywords

    await message.answer(
        f"Ключевые слова обновлены: {keywords if keywords else 'сброшены'}."
    )
    await state.clear()
    await _send_main_menu(message, user_id)


# ─── Диапазон цены ────────────────────────────────────────────

def _price_keyboard(min_p, max_p) -> InlineKeyboardMarkup:
    min_text = f"Мин. цена: {min_p} ₽" if min_p is not None else "Мин. цена: не задана"
    max_text = f"Макс. цена: {max_p} ₽" if max_p is not None else "Макс. цена: не задана"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=min_text, callback_data="price:edit_min")],
            [InlineKeyboardButton(text=max_text, callback_data="price:edit_max")],
            [InlineKeyboardButton(text="🗑 Сбросить", callback_data="price:reset")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
        ]
    )


async def _show_price_screen(event: types.Message | CallbackQuery, user: dict | None):
    min_p = user.get("min_price") if user else None
    max_p = user.get("max_price") if user else None
    markup = _price_keyboard(min_p, max_p)
    text = "💰 <b>Диапазон цены</b>\n\nВыбери границу для редактирования:"
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "open_price")
async def cb_open_price(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    await _show_price_screen(callback, user)


@router.callback_query(F.data == "price:edit_min")
async def cb_edit_min_price(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = await _cache_get(callback.from_user.id)
    min_p = user.get("min_price")
    current = f"{min_p} ₽" if min_p is not None else "не задана"
    await callback.message.edit_text(
        f"💰 <b>Минимальная цена</b>\n\n"
        f"Сейчас: {current}\n\n"
        f"Отправь новое значение (число) или «-» чтобы сбросить:",
        reply_markup=_input_cancel_keyboard("cancel:price"),
        parse_mode="HTML",
    )
    await state.set_state(Form.min_price)


@router.callback_query(F.data == "price:edit_max")
async def cb_edit_max_price(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = await _cache_get(callback.from_user.id)
    max_p = user.get("max_price")
    current = f"{max_p} ₽" if max_p is not None else "не задана"
    await callback.message.edit_text(
        f"💰 <b>Максимальная цена</b>\n\n"
        f"Сейчас: {current}\n\n"
        f"Отправь новое значение (число) или «-» чтобы сбросить:",
        reply_markup=_input_cancel_keyboard("cancel:price"),
        parse_mode="HTML",
    )
    await state.set_state(Form.max_price)


@router.callback_query(F.data == "price:reset")
async def cb_reset_price(callback: CallbackQuery):
    await callback.answer("Диапазон цены сброшен 🗑")
    user_id = callback.from_user.id
    await db.set_price_range(user_id, None, None)
    if user_id in _user_cache:
        _user_cache[user_id]["min_price"] = None
        _user_cache[user_id]["max_price"] = None
    user = await _cache_get(user_id)
    await _show_price_screen(callback, user)


@router.message(Form.min_price)
async def process_min_price(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "-":
        min_price = None
    else:
        try:
            val = int(text)
            if val < 0:
                raise ValueError
            min_price = val
        except ValueError:
            await message.answer("Введи целое число ≥ 0 или «-».")
            return

    await db.set_price_range(message.from_user.id, min_price, None)
    if message.from_user.id in _user_cache:
        _user_cache[message.from_user.id]["min_price"] = min_price

    await message.answer("Минимальная цена обновлена.")
    await state.clear()
    user = await _cache_get(message.from_user.id)
    await _show_price_screen(message, user)


@router.message(Form.max_price)
async def process_max_price(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "-":
        max_price = None
    else:
        try:
            val = int(text)
            if val < 0:
                raise ValueError
            max_price = val
        except ValueError:
            await message.answer("Введи целое число ≥ 0 или «-».")
            return

    user_id = message.from_user.id
    user = await _cache_get(user_id)
    min_price = user.get("min_price") if user else None

    if min_price is not None and max_price is not None and min_price > max_price:
        await message.answer(
            "Минимальная цена не может быть больше максимальной. "
            "Сначала сбрось минимальную цену или увеличь её."
        )
        await state.clear()
        await _show_price_screen(message, user)
        return

    await db.set_price_range(user_id, min_price, max_price)
    if user_id in _user_cache:
        _user_cache[user_id]["max_price"] = max_price

    await message.answer("Максимальная цена обновлена.")
    await state.clear()
    await _show_price_screen(message, user)


# ─── Тихий час ────────────────────────────────────────────────

def _quiet_keyboard(qs, qe) -> InlineKeyboardMarkup:
    start_text = f"Начало: {qs}:00" if qs is not None else "Начало: не задано"
    end_text = f"Конец: {qe}:00" if qe is not None else "Конец: не задано"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=start_text, callback_data="quiet:edit_start")],
            [InlineKeyboardButton(text=end_text, callback_data="quiet:edit_end")],
            [InlineKeyboardButton(text="🗑 Сбросить", callback_data="quiet:reset")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
        ]
    )


def _hours_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру с часами 0-23 для выбора."""
    rows = []
    for row_start in range(0, 24, 6):
        row = []
        for h in range(row_start, min(row_start + 6, 24)):
            row.append(InlineKeyboardButton(text=f"{h}:00", callback_data=f"{prefix}:{h}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_quiet")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_quiet_screen(event: types.Message | CallbackQuery, user: dict | None):
    qs = user.get("quiet_hours_start") if user else None
    qe = user.get("quiet_hours_end") if user else None
    markup = _quiet_keyboard(qs, qe)
    text = (
        "🌙 <b>Тихий час</b>\n\n"
        "В это время уведомления не присылаются.\n\n"
        "Выбери время для редактирования:"
    )
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "open_quiet")
async def cb_open_quiet(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    await _show_quiet_screen(callback, user)


@router.callback_query(F.data == "quiet:edit_start")
async def cb_edit_quiet_start(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🌙 <b>Тихий час — начало</b>\n\nВыбери час:",
        reply_markup=_hours_keyboard("quiet_start"),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "quiet:edit_end")
async def cb_edit_quiet_end(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🌙 <b>Тихий час — конец</b>\n\nВыбери час:",
        reply_markup=_hours_keyboard("quiet_end"),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("quiet_start:"))
async def cb_set_quiet_start(callback: CallbackQuery):
    hour = int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    qe = user.get("quiet_hours_end") if user else None

    await db.set_quiet_hours(user_id, hour, qe)
    if user_id in _user_cache:
        _user_cache[user_id]["quiet_hours_start"] = hour

    await callback.answer(f"Начало установлено на {hour}:00")
    user = await _cache_get(user_id)
    await _show_quiet_screen(callback, user)


@router.callback_query(F.data.startswith("quiet_end:"))
async def cb_set_quiet_end(callback: CallbackQuery):
    hour = int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    qs = user.get("quiet_hours_start") if user else None

    await db.set_quiet_hours(user_id, qs, hour)
    if user_id in _user_cache:
        _user_cache[user_id]["quiet_hours_end"] = hour

    await callback.answer(f"Конец установлен на {hour}:00")
    user = await _cache_get(user_id)
    await _show_quiet_screen(callback, user)


@router.callback_query(F.data == "quiet:reset")
async def cb_reset_quiet(callback: CallbackQuery):
    await callback.answer("Тихий час сброшен 🗑")
    user_id = callback.from_user.id
    await db.set_quiet_hours(user_id, None, None)
    if user_id in _user_cache:
        _user_cache[user_id]["quiet_hours_start"] = None
        _user_cache[user_id]["quiet_hours_end"] = None
    user = await _cache_get(user_id)
    await _show_quiet_screen(callback, user)


@router.callback_query(F.data == "back_to_quiet")
async def cb_back_to_quiet(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await _cache_get(user_id)
    await _show_quiet_screen(callback, user)


# ─── Отмена ввода ─────────────────────────────────────────────

@router.callback_query(F.data == "cancel:keywords")
async def cb_cancel_keywords(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    user = await _cache_get(callback.from_user.id)
    kw = user.get("keywords", []) if user else []
    kw_text = ", ".join(kw) if kw else "не заданы"
    await callback.message.edit_text(
        f"🔑 <b>Ключевые слова</b>\n\n"
        f"Сейчас: {kw_text}\n\n"
        f"Бот присылает только заказы, где есть хотя бы одно из этих слов "
        f"в заголовке или описании.",
        reply_markup=_keywords_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel:price")
async def cb_cancel_price(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    user = await _cache_get(callback.from_user.id)
    await _show_price_screen(callback, user)
