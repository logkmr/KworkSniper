"""
Supabase REST API client for Kwork Sniper.
No extra deps — uses httpx directly.
"""

import json
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_UNSET = object()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )


# ─── Users ────────────────────────────────────────────────────

async def get_user(user_id: int) -> Optional[dict]:
    """Получить пользователя по Telegram ID."""
    async with _client() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}&select=*"
        )
        if resp.status_code == 200:
            data = resp.json()
            return data[0] if data else None
        return None


async def add_user(user_id: int, username: Optional[str]) -> Optional[dict]:
    """Добавить нового пользователя."""
    payload = {"id": user_id, "username": username or "", "notifications_enabled": True}
    async with _client() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/users",
            json=payload,
            headers={"Prefer": "return=representation"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return data[0] if data else None
        return None


async def toggle_notifications(user_id: int, enabled: bool) -> Optional[dict]:
    """Включить/выключить уведомления."""
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"notifications_enabled": enabled},
            headers={"Prefer": "return=representation"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data[0] if data else None
        if resp.status_code == 204:
            return {"id": user_id, "notifications_enabled": enabled}
        return None


async def get_subscribed_users() -> list[dict]:
    """Все пользователи с включёнными уведомлениями."""
    async with _client() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users?notifications_enabled=eq.true&select=*"
        )
        if resp.status_code == 200:
            return resp.json()
        return []


async def get_users_count() -> int:
    async with _client() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users?select=id",
            headers={"Prefer": "count=exact"},
        )
        if resp.status_code == 200:
            total = resp.headers.get("content-range", "0/0").split("/")[-1]
            try:
                return int(total)
            except ValueError:
                return len(resp.json())
        return 0


# ─── Filters ──────────────────────────────────────────────────

async def get_user_filters(user_id: int) -> list[str]:
    """Возвращает список ВКЛЮЧЕННЫХ category_slug для пользователя."""
    user = await get_user(user_id)
    if not user:
        return []
    raw = user.get("filters")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []
    return []


async def set_user_filters(user_id: int, filters: list[str]) -> bool:
    """Устанавливает список фильтров для пользователя."""
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"filters": filters},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def set_keywords(user_id: int, keywords: list[str]) -> bool:
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"keywords": keywords},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def set_price_range(user_id: int, min_price=_UNSET, max_price=_UNSET) -> bool:
    payload = {}
    if min_price is not _UNSET:
        payload["min_price"] = min_price
    if max_price is not _UNSET:
        payload["max_price"] = max_price
    if not payload:
        return False
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json=payload,
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def set_quiet_hours(user_id: int, start=_UNSET, end=_UNSET) -> bool:
    payload = {}
    if start is not _UNSET:
        payload["quiet_hours_start"] = start
    if end is not _UNSET:
        payload["quiet_hours_end"] = end
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json=payload,
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def set_ai_enabled(user_id: int, enabled: bool) -> bool:
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"ai_enabled": enabled},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def set_ai_min_score(user_id: int, score: Optional[int]) -> bool:
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"ai_min_score": score},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


# ─── Auto-respond ─────────────────────────────────────────────

async def set_auto_respond_enabled(user_id: int, enabled: bool) -> bool:
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"auto_respond_enabled": enabled},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)
