from __future__ import annotations

import json
import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from bot.bot.helpers import email_preview_text, gate
from bot.bot.keyboards.user import email_view
from bot.context import ctx
from bot.database.models import EmailMessage, Mailbox
from bot.services.mailtm.client import MailTmError
from bot.utils.text import clip, fmt_date, safe_text

logger = logging.getLogger(__name__)
router = Router(name="email")


async def _owned_message(message_id: int, telegram_id: int):
    async with ctx().database.session_factory() as session:
        row = await session.execute(
            select(EmailMessage, Mailbox)
            .join(Mailbox, Mailbox.id == EmailMessage.mailbox_id)
            .join(Mailbox.user)
            .where(EmailMessage.id == message_id, Mailbox.status == "active")
        )
        result = row.first()
        if not result:
            return None
        message, mailbox = result
        from bot.database.models import User

        user = await session.scalar(select(User).where(User.id == mailbox.user_id, User.telegram_id == telegram_id))
        return message, mailbox if user else None


@router.callback_query(lambda call: (call.data or "").startswith("email:view:"))
async def view_email(callback: CallbackQuery) -> None:
    _, allowed = await gate(callback)
    if not allowed or not callback.message:
        return
    message_id = int((callback.data or "").rsplit(":", 1)[1])
    owned = await _owned_message(message_id, callback.from_user.id)
    if not owned or owned[1] is None:
        await callback.answer("Email not found.", show_alert=True)
        return
    message, mailbox = owned
    async with ctx().database.session_factory() as session:
        stored = await session.get(EmailMessage, message.id)
        if stored:
            stored.seen = True
            await session.commit()
    body = message.text_content or "No plain-text content was provided."
    attachments = ""
    if message.has_attachments:
        try:
            items = json.loads(message.attachments_json)
            attachments = "\n\n" + "\n".join(f"📎 Attachment: {safe_text(item.get('filename'))}" for item in items)
        except (TypeError, json.JSONDecodeError):
            attachments = "\n\n📎 This email contains attachments."
    text = (
        "📩 EMAIL\n\n"
        f"From: {safe_text(message.sender)}\n"
        f"To: {safe_text(message.recipient or mailbox.email_address)}\n"
        f"Subject: {safe_text(message.subject)}\n"
        f"Date: {fmt_date(message.received_at)}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{clip(body, 3400)}{attachments}"
    )
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=email_view(message.id))


@router.callback_query(lambda call: (call.data or "").startswith("email:back:"))
async def back_email(callback: CallbackQuery) -> None:
    _, allowed = await gate(callback)
    if not allowed or not callback.message:
        return
    message_id = int((callback.data or "").rsplit(":", 1)[1])
    owned = await _owned_message(message_id, callback.from_user.id)
    if not owned or owned[1] is None:
        await callback.answer("Email not found.", show_alert=True)
        return
    message, mailbox = owned
    await callback.answer()
    await callback.message.edit_text(
        email_preview_text(mailbox.email_address, message.sender, message.subject, message.text_content),
        reply_markup=email_view(message.id),
    )


@router.callback_query(lambda call: (call.data or "").startswith("email:delete:"))
async def delete_email(callback: CallbackQuery) -> None:
    _, allowed = await gate(callback)
    if not allowed or not callback.message:
        return
    message_id = int((callback.data or "").rsplit(":", 1)[1])
    owned = await _owned_message(message_id, callback.from_user.id)
    if not owned or owned[1] is None:
        await callback.answer("Email not found.", show_alert=True)
        return
    message, mailbox = owned
    try:
        await ctx().mailtm.delete_message(message.mailtm_message_id, ctx().mailbox.decrypt_token(mailbox))
    except MailTmError as exc:
        if exc.status != 404:
            await callback.answer("Could not delete this email right now.", show_alert=True)
            return
    async with ctx().database.session_factory() as session:
        stored = await session.get(EmailMessage, message.id)
        if stored:
            await session.delete(stored)
            await session.commit()
    await callback.answer("Email deleted")
    await callback.message.edit_text("✅ Email deleted.")
