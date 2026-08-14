from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import CreditTransaction, Referral, User
from bot.services.settings import SettingsService


class ReferralService:
    def __init__(self, settings: SettingsService) -> None:
        self.settings = settings

    async def apply_new_user_referral(
        self, session: AsyncSession, user: User, referrer_telegram_id: int | None
    ) -> bool:
        enabled = await self.settings.get(session, "referrals_enabled")
        reward = int(await self.settings.get(session, "referral_reward") or 0)
        if not enabled or reward <= 0 or not referrer_telegram_id:
            return False
        referrer = await session.scalar(
            select(User).where(User.telegram_id == referrer_telegram_id).with_for_update()
        )
        if referrer is None or referrer.id == user.id:
            return False
        user.referrer_id = referrer.id
        user_id = user.id
        referrer.total_referrals += 1
        session.add(Referral(referrer_id=referrer.id, referred_id=user_id, reward=reward))
        referrer.balance += reward
        session.add(
            CreditTransaction(
                user_id=referrer.id,
                amount=reward,
                type="REFERRAL_BONUS",
                description="Successful referral",
                reference_id=str(user_id),
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return False
        return True

    async def stats(self, session: AsyncSession, user_id: int) -> tuple[int, int]:
        count = int(
            await session.scalar(select(func.count(Referral.id)).where(Referral.referrer_id == user_id)) or 0
        )
        earned = int(
            await session.scalar(
                select(func.coalesce(func.sum(Referral.reward), 0)).where(Referral.referrer_id == user_id)
            )
            or 0
        )
        return count, earned
