from __future__ import annotations

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.bot.helpers import gate
from bot.context import ctx
from bot.database.models import User

router = Router(name="referral")


@router.message(F.text == "👥 Refer & Earn")
async def referrals(message: Message) -> None:
    user, allowed = await gate(message)
    if not allowed or not user:
        return
    me = await ctx().bot.get_me()
    async with ctx().database.session_factory() as session:
        count, earned = await ctx().referrals.stats(session, user.id)
        reward = int(await ctx().settings_service.get(session, "referral_reward") or 0)
    link = f"https://t.me/{me.username}?start={user.telegram_id}"
    await message.answer(
        f"👥 REFER & EARN\n\nInvite friends and earn {reward} credits for every successful referral.\n\n"
        f"Your Referrals: {count}\nCredits Earned: {earned}\n\nYour Link:\n{link}\n\n"
        "A referral is rewarded once, only when a new user joins for the first time.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Share Link", url=f"https://t.me/share/url?url={link}")],
            ]
        ),
    )
