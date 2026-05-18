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


async def set_price_range(user_id: int, min_price: Optional[int], max_price: Optional[int]) -> bool:
    payload = {}
    if min_price is not None:
        payload["min_price"] = min_price
    if max_price is not None:
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


async def set_quiet_hours(user_id: int, start: Optional[int], end: Optional[int]) -> bool:
    payload = {}
    if start is not None:
        payload["quiet_hours_start"] = start
    if end is not None:
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


async def set_user_profile(user_id: int, **fields) -> bool:
    allowed = {
        "profile_name", "profile_spec", "profile_exp",
        "profile_skills", "profile_portfolio", "profile_strengths",
        "profile_rate",
    }
    payload = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not payload:
        return False
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json=payload,
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def set_user_cookies(user_id: int, cookies: str) -> bool:
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"kwork_cookies": cookies},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)


async def get_sent_offer_ids(user_id: int) -> list[str]:
    user = await get_user(user_id)
    if not user:
        return []
    raw = user.get("auto_respond_sent_ids")
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


async def add_sent_offer_id(user_id: int, want_id: str) -> bool:
    current = await get_sent_offer_ids(user_id)
    if want_id in current:
        return True
    current.append(want_id)
    async with _client() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}",
            json={"auto_respond_sent_ids": current},
            headers={"Prefer": "return=minimal"},
        )
        return resp.status_code in (200, 204)
