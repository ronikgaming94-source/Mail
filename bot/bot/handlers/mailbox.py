from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.bot.helpers import gate, mailbox_text
from bot.bot.keyboards.user import delete_confirmation, mailbox_card
from bot.context import ctx
from bot.database.models import Mailbox, User
from bot.services.mailtm.client import MailTmError

logger = logging.getLogger(__name__)
router = Router(name="mailbox")


async def _animate_creation(progress: Message) -> None:
    frames = (
        "⏳ Creating your temporary mailbox…\n\n🔐 Preparing secure inbox",
        "⏳ Creating your temporary mailbox…\n\n🌐 Connecting to email service",
        "⏳ Creating your temporary mailbox…\n\n📬 Activating your inbox",
    )
    frame = 0
    while True:
        await asyncio.sleep(0.9)
        frame = (frame + 1) % len(frames)
        try:
            await progress.edit_text(frames[frame])
        except Exception:
            logger.debug("Could not update mailbox creation animation", exc_info=True)


@router.message(F.text == "📧 Create New Mail")
async def create_mail(message: Message) -> None:
    user, allowed = await gate(message)
    if not allowed or not user:
        return
    progress = await message.answer("⏳ Creating your temporary mailbox…\n\n🔐 Preparing secure inbox")
    animation = asyncio.create_task(_animate_creation(progress))
    try:
        async with ctx().database.session_factory() as session:
            mailbox = await ctx().mailbox.create(session, user.id)
            fresh_user = await session.get(User, user.id)
            balance = fresh_user.balance if fresh_user else 0
        ctx().events.add(mailbox.id)
        await progress.edit_text(mailbox_text(mailbox.email_address, balance), reply_markup=mailbox_card(mailbox.id))
    except MailTmError as exc:
        logger.warning("mailbox creation refused: %s", str(exc))
        await progress.edit_text("❌ Unable to create your email right now.\n\nPlease try again in a moment.")
    except Exception:
        logger.exception("mailbox creation failed")
        await progress.edit_text("❌ Unable to create your email right now.\n\nPlease try again in a moment.")
    finally:
        animation.cancel()
        await asyncio.gather(animation, return_exceptions=True)


@router.callback_query(lambda call: call.data == "mailbox:list")
async def list_mailboxes(callback: CallbackQuery) -> None:
    user, allowed = await gate(callback)
    if not allowed or not user or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        mailboxes = await ctx().mailbox.active_for_user(session, user.id)
    if not mailboxes:
        await callback.answer("No active mailboxes", show_alert=True)
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [InlineKeyboardButton(text=f"📧 {mailbox.email_address}", callback_data=f"mailbox:show:{mailbox.id}")]
        for mailbox in mailboxes
    ]
    keyboard.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")])
    await callback.answer()
    await callback.message.edit_text("📧 MY MAILBOXES\n\nChoose a mailbox:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


async def _show(callback: CallbackQuery, mailbox_id: int) -> None:
    user, allowed = await gate(callback)
    if not allowed or not user or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        try:
            mailbox, count = await ctx().mailbox.refresh(session, mailbox_id, user.id)
            fresh = await session.get(User, user.id)
            balance = fresh.balance if fresh else 0
        except ValueError:
            await callback.answer("Mailbox not found.", show_alert=True)
            return
    await callback.answer()
    await callback.message.edit_text(mailbox_text(mailbox.email_address, balance, count), reply_markup=mailbox_card(mailbox.id))


@router.callback_query(lambda call: (call.data or "").startswith("mailbox:show:"))
async def show_mailbox(callback: CallbackQuery) -> None:
    await _show(callback, int((callback.data or "").rsplit(":", 1)[1]))


@router.callback_query(lambda call: (call.data or "").startswith("mailbox:refresh:"))
async def refresh_mailbox(callback: CallbackQuery) -> None:
    await _show(callback, int((callback.data or "").rsplit(":", 1)[1]))


@router.callback_query(lambda call: (call.data or "").startswith("mailbox:delete:"))
async def confirm_delete(callback: CallbackQuery) -> None:
    user, allowed = await gate(callback)
    if not allowed or not user or not callback.message:
        return
    mailbox_id = int((callback.data or "").rsplit(":", 1)[1])
    async with ctx().database.session_factory() as session:
        mailbox = await session.scalar(select(Mailbox).where(Mailbox.id == mailbox_id, Mailbox.user_id == user.id))
    if not mailbox:
        await callback.answer("Mailbox not found.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"⚠️ DELETE MAILBOX?\n\nEmail: {mailbox.email_address}\n\nDeleting this mailbox may prevent future messages from being received.",
        reply_markup=delete_confirmation(mailbox_id),
    )


@router.callback_query(lambda call: (call.data or "").startswith("mailbox:confirm_delete:"))
async def delete_mailbox(callback: CallbackQuery) -> None:
    user, allowed = await gate(callback)
    if not allowed or not user or not callback.message:
        return
    mailbox_id = int((callback.data or "").rsplit(":", 1)[1])
    try:
        async with ctx().database.session_factory() as session:
            await ctx().mailbox.delete(session, mailbox_id, user.id)
        await ctx().events.remove(mailbox_id)
        await callback.answer("Mailbox deleted")
        await callback.message.edit_text("✅ Mailbox deleted. Credits are not refunded.")
    except Exception:
        logger.exception("mailbox deletion failed")
        await callback.answer("Could not delete mailbox right now.", show_alert=True)
