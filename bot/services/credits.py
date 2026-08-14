from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import CreditTransaction, User

logger = logging.getLogger(__name__)


class CreditService:
    async def change(
        self,
        session: AsyncSession,
        user_id: int,
        amount: int,
        transaction_type: str,
        description: str,
        reference_id: str | None = None,
        admin_id: int | None = None,
    ) -> User:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise ValueError("User not found")
        new_balance = user.balance + amount
        if new_balance < 0:
            raise ValueError("Insufficient credits")
        user.balance = new_balance
        session.add(
            CreditTransaction(
                user_id=user.id,
                amount=amount,
                type=transaction_type,
                description=description,
                reference_id=reference_id,
                admin_id=admin_id,
            )
        )
        logger.info("credit change user_id=%s amount=%s type=%s", user.id, amount, transaction_type)
        return user
