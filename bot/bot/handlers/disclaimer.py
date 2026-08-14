from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from bot.bot.helpers import send_home
from bot.bot.keyboards.user import disclaimer
from bot.context import ctx
from bot.database.models import User

router = Router(name="disclaimer")


@router.callback_query(lambda call: call.data == "disclaimer:agree")
async def agree(callback: CallbackQuery) -> None:
    async with ctx().database.session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == callback.from_user.id))
        if not user:
            await callback.answer("Please send /start first.", show_alert=True)
            return
        user.is_agreed = True
        user.agreement_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.answer("Agreement saved")
    if callback.message:
        await send_home(callback.message, user)


@router.callback_query(lambda call: call.data == "disclaimer:decline")
async def decline(callback: CallbackQuery) -> None:
    await callback.answer("You must agree to use the bot.", show_alert=True)
    if callback.message:
        await callback.message.answer(
            "You declined the terms, so the service remains unavailable. Send /start if you want to review them again.",
            reply_markup=disclaimer(),
        )
