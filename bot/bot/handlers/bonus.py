from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.types import Message

from bot.bot.helpers import gate
from bot.context import ctx
from bot.database.models import User

router = Router(name="bonus")


@router.message(F.text == "🎁 Daily Bonus")
async def daily_bonus(message: Message) -> None:
    user, allowed = await gate(message)
    if not allowed or not user:
        return
    async with ctx().database.session_factory() as session:
        success, reward, remaining = await ctx().bonus.claim(session, user.id)
        if success:
            fresh = await session.get(User, user.id)
            balance = fresh.balance if fresh else user.balance + reward
        else:
            balance = user.balance
    if success:
        await message.answer(f"🎁 DAILY BONUS CLAIMED\n\n+{reward} Credits\n💳 Balance: {balance} Credits")
    elif remaining:
        total_minutes = max(0, int(remaining.total_seconds() // 60))
        await message.answer(
            f"⏳ DAILY BONUS ALREADY CLAIMED\n\nNext bonus: {total_minutes // 60} hours {total_minutes % 60} minutes"
        )
    else:
        await message.answer("Daily bonus is currently disabled.")
