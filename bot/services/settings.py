from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import BotSetting


DEFAULTS: dict[str, Any] = {
    "signup_bonus": 10,
    "daily_bonus": 10,
    "daily_bonus_cooldown_hours": 24,
    "daily_bonus_enabled": True,
    "referral_reward": 5,
    "referrals_enabled": True,
    "mail_credit_cost": 1,
    "maintenance_enabled": False,
    "maintenance_message": "The bot is currently unavailable. Please try again later.",
    "disclaimer": (
        "⚠️ DISCLAIMER & TERMS OF USE\n\n"
        "By using this bot, you confirm that you have read, understood, and agreed to our Terms.\n\n"
        "• You are responsible for all activities performed through this bot.\n"
        "• Illegal, fraudulent, abusive, spam, phishing, or malicious use is strictly prohibited.\n"
        "• We may restrict, suspend, or terminate access without prior notice.\n"
        "• Credits, bonuses, referrals, limits, domains, and features may change at any time.\n"
        "• We do not guarantee uninterrupted service, permanent email availability, or successful delivery.\n\n"
        "⚠️ By continuing, you agree to all current and future Terms, Rules, and Policies."
    ),
    "notifications_enabled": True,
    "force_subscribe_enabled": True,
}


class SettingsService:
    async def ensure_defaults(self, session: AsyncSession) -> None:
        existing = set((await session.scalars(select(BotSetting.key))).all())
        for key, value in DEFAULTS.items():
            if key not in existing:
                session.add(BotSetting(key=key, value=json.dumps(value)))
        await session.commit()

    async def get(self, session: AsyncSession, key: str) -> Any:
        row = await session.get(BotSetting, key)
        if row is None:
            return DEFAULTS.get(key)
        try:
            return json.loads(row.value)
        except json.JSONDecodeError:
            return row.value

    async def set(self, session: AsyncSession, key: str, value: Any) -> None:
        row = await session.get(BotSetting, key)
        encoded = json.dumps(value)
        if row:
            row.value = encoded
        else:
            session.add(BotSetting(key=key, value=encoded))
        await session.commit()

    async def all(self, session: AsyncSession) -> dict[str, Any]:
        rows = (await session.scalars(select(BotSetting))).all()
        result = dict(DEFAULTS)
        for row in rows:
            try:
                result[row.key] = json.loads(row.value)
            except json.JSONDecodeError:
                result[row.key] = row.value
        return result
