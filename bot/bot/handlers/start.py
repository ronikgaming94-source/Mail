from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.bot.helpers import gate, send_home
from bot.bot.keyboards.force_join import force_join
from bot.bot.keyboards.user import disclaimer
from bot.context import ctx
from bot.database.models import CreditTransaction, User

router = Router(name="start")


async def _register(message: Message, referrer_id: int | None) -> User:
    now = datetime.now(timezone.utc)
    async with ctx().database.session_factory() as session:
        existing = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if existing:
            existing.username = message.from_user.username
            existing.first_name = message.from_user.first_name or ""
            existing.last_name = message.from_user.last_name
            existing.last_active_at = now
            await session.commit()
            return existing
        signup_bonus = int(await ctx().settings_service.get(session, "signup_bonus") or 0)
        user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
            last_name=message.from_user.last_name,
            balance=signup_bonus,
            last_active_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            CreditTransaction(
                user_id=user.id,
                amount=signup_bonus,
                type="SIGNUP_BONUS",
                description="New user signup bonus",
            )
        )
        await session.commit()
        await session.refresh(user)

    if referrer_id:
        async with ctx().database.session_factory() as session:
            referrer = await session.scalar(select(User).where(User.telegram_id == referrer_id))
            if referrer and referrer.id != user.id:
                user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
                if user and user.referrer_id is None:
                    await ctx().referrals.apply_new_user_referral(session, user, referrer_id)
                    await session.commit()
    return user


@router.message(CommandStart())
async def start(message: Message, command) -> None:
    raw = str(command.args or "").strip()
    referrer_id = int(raw) if raw.isdigit() else None
    user = await _register(message, referrer_id)
    if user.is_banned:
        await message.answer("🚫 Your account is restricted. Please contact support.")
        return
    if not user.is_agreed:
        text = await _disclaimer_text()
        await message.answer(text, reply_markup=disclaimer())
        return
    _, allowed = await gate(message)
    if allowed:
        await send_home(message, user)


async def _disclaimer_text() -> str:
    async with ctx().database.session_factory() as session:
        return str(await ctx().settings_service.get(session, "disclaimer"))


@router.callback_query(lambda call: call.data == "force:verify")
async def verify_force_join(callback: CallbackQuery) -> None:
    async with ctx().database.session_factory() as session:
        missing = await ctx().force_join.missing(ctx().bot, session, callback.from_user.id)
        if missing:
            await callback.answer("Please join every required channel first.", show_alert=True)
            return
    await callback.answer("Verified")
    if callback.message:
        await callback.message.answer("✅ Subscription verified. Send /start to open the menu.")
