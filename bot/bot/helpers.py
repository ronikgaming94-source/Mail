from __future__ import annotations

from aiogram import Bot
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.context import ctx
from bot.database.models import User
from bot.bot.keyboards.force_join import force_join
from bot.bot.keyboards.user import disclaimer, main_menu
from bot.utils.text import clip, fmt_date, safe_text


async def get_user(telegram_id: int, message: Message | CallbackQuery | None = None) -> User | None:
    async with ctx().database.session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user and message is not None:
            from datetime import datetime, timezone

            user.last_active_at = datetime.now(timezone.utc)
            await session.commit()
        return user


async def is_admin(telegram_id: int) -> bool:
    return telegram_id in ctx().settings.admin_ids


async def gate(message_or_callback: Message | CallbackQuery, *, require_agreement: bool = True) -> tuple[User | None, bool]:
    telegram_id = message_or_callback.from_user.id
    if await is_admin(telegram_id):
        return await get_user(telegram_id), True
    async with ctx().database.session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None, False
        if user.is_banned:
            text = "🚫 Your account is restricted. Please contact support if you believe this is an error."
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.answer("Account restricted", show_alert=True)
            else:
                await message_or_callback.answer(text)
            return user, False
        maintenance = await ctx().settings_service.get(session, "maintenance_enabled")
        if maintenance:
            custom = await ctx().settings_service.get(session, "maintenance_message")
            text = f"🔧 BOT UNDER MAINTENANCE\n\n{safe_text(custom)}"
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.answer("Maintenance mode", show_alert=True)
            else:
                await message_or_callback.answer(text)
            return user, False
        if require_agreement and not user.is_agreed:
            text = await ctx().settings_service.get(session, "disclaimer")
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.message.answer(text, reply_markup=disclaimer())
                await message_or_callback.answer()
            else:
                await message_or_callback.answer(text, reply_markup=disclaimer())
            return user, False
        missing = await ctx().force_join.missing(ctx().bot, session, telegram_id)
        if missing:
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.message.answer(
                    "🔒 JOIN REQUIRED CHANNELS\n\nJoin all required channels to continue.",
                    reply_markup=force_join(missing),
                )
                await message_or_callback.answer()
            else:
                await message_or_callback.answer(
                    "🔒 JOIN REQUIRED CHANNELS\n\nJoin all required channels to continue.",
                    reply_markup=force_join(missing),
                )
            return user, False
        return user, True


async def send_home(message: Message, user: User | None = None) -> None:
    await message.answer(
        "🏠 Welcome to Temp Mail Xpress.\n\nCreate temporary email inboxes and receive incoming messages here.",
        reply_markup=main_menu(await is_admin(message.from_user.id)),
    )


def mailbox_text(address: str, balance: int, count: int | None = None) -> str:
    extra = f"\n📩 Emails received: {count}" if count is not None else ""
    return f"📧 NEW EMAIL CREATED\n\nYour Email:\n{address}\n\n💳 Balance:\n{balance} Credits{extra}"


def email_preview_text(mailbox_address: str, sender: str, subject: str, body: str) -> str:
    return (
        f"📧 {safe_text(mailbox_address)}\n\n"
        "📩 NEW EMAIL RECEIVED\n"
        f"👤 From: {safe_text(sender)}\n"
        f"📝 Subject: {safe_text(subject)}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{clip(safe_text(body), 1700) or '(No text content)'}"
    )
