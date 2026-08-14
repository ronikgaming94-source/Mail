from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DailyBonusClaim, User
from bot.services.credits import CreditService
from bot.services.settings import SettingsService


class BonusService:
    def __init__(self, settings: SettingsService, credits: CreditService) -> None:
        self.settings = settings
        self.credits = credits

    async def claim(self, session: AsyncSession, user_id: int) -> tuple[bool, int, timedelta | None]:
        enabled = await self.settings.get(session, "daily_bonus_enabled")
        reward = int(await self.settings.get(session, "daily_bonus") or 0)
        cooldown_hours = float(await self.settings.get(session, "daily_bonus_cooldown_hours") or 24)
        if not enabled or reward <= 0:
            return False, 0, None
        await session.rollback()
        async with session.begin():
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None or user.is_banned:
                return False, 0, None
            claim = await session.scalar(
                select(DailyBonusClaim).where(DailyBonusClaim.user_id == user_id).with_for_update()
            )
            now = datetime.now(timezone.utc)
            if claim:
                next_at = claim.last_claimed_at + timedelta(hours=cooldown_hours)
                if next_at > now:
                    return False, 0, next_at - now
                claim.last_claimed_at = now
            else:
                session.add(DailyBonusClaim(user_id=user_id, last_claimed_at=now))
            user.balance += reward
            from bot.database.models import CreditTransaction

            session.add(
                CreditTransaction(
                    user_id=user_id,
                    amount=reward,
                    type="DAILY_BONUS",
                    description="Daily bonus",
                )
            )
        return True, reward, None
