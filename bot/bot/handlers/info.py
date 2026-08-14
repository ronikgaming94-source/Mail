from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import func, select

from bot.bot.helpers import gate
from bot.context import ctx
from bot.database.models import EmailMessage, Mailbox, User
from bot.utils.text import fmt_date

router = Router(name="info")


@router.message(F.text == "👤 My Info")
async def my_info(message: Message) -> None:
    user, allowed = await gate(message)
    if not allowed or not user:
        return
    async with ctx().database.session_factory() as session:
        mail_count = int(await session.scalar(select(func.count(Mailbox.id)).where(Mailbox.user_id == user.id)) or 0)
        email_count = int(
            await session.scalar(
                select(func.count(EmailMessage.id)).join(Mailbox, Mailbox.id == EmailMessage.mailbox_id).where(Mailbox.user_id == user.id)
            )
            or 0
        )
        fresh = await session.get(User, user.id)
    await message.answer(
        f"👤 MY INFORMATION\n\n"
        f"🆔 User ID: {user.telegram_id}\n💳 Current Balance: {fresh.balance if fresh else user.balance} Credits\n"
        f"👥 Total Referrals: {user.total_referrals}\n📧 Mails Created: {mail_count}\n📩 Emails Received: {email_count}\n"
        f"📅 Joined: {fmt_date(user.created_at)}"
    )
