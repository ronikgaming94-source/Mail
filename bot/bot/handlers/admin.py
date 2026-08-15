from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.bot.helpers import is_admin, send_home
from bot.bot.keyboards.admin import admin_back, admin_menu
from bot.context import ctx
from bot.database.models import Broadcast, ForceJoinChannel, User
from bot.services.mailtm.client import MailTmError
from bot.utils.text import fmt_date, safe_text

logger = logging.getLogger(__name__)
router = Router(name="admin")


class AdminStates(StatesGroup):
    search_user = State()
    credit_change = State()
    broadcast = State()
    force_add = State()
    setting_value = State()


async def _guard(callback: CallbackQuery | Message) -> bool:
    if await is_admin(callback.from_user.id):
        return True
    if isinstance(callback, CallbackQuery):
        await callback.answer("Admin access required.", show_alert=True)
    else:
        await callback.answer("Admin access required.")
    return False


@router.message(F.text == "🛠️ Admin Panel")
async def admin_panel(message: Message) -> None:
    if await _guard(message):
        await message.answer("🛠️ ADMIN PANEL", reply_markup=admin_menu())


@router.callback_query(lambda call: call.data == "admin:home")
async def admin_home(callback: CallbackQuery) -> None:
    if not await _guard(callback):
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("🛠️ ADMIN PANEL", reply_markup=admin_menu())


@router.callback_query(lambda call: call.data == "admin:dashboard")
async def dashboard(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        stats = await ctx().admin.dashboard(session)
        settings = await ctx().settings_service.all(session)
    try:
        await ctx().mailtm.domains()
        email_service_status = "reachable"
    except Exception:
        email_service_status = "unreachable"
    await callback.answer()
    await callback.message.edit_text(
        "📊 DASHBOARD\n\n"
        f"👥 Total Users: {stats['users']}\n🟢 Active Users: {stats['active_users']}\n🚫 Banned Users: {stats['banned']}\n"
        f"📧 Total Mailboxes: {stats['mailboxes']}\n📩 Total Emails: {stats['emails']}\n👥 Total Referrals: {stats['referrals']}\n"
        f"💳 Credits Used: {stats['credits_used']}\n💳 Credits Issued: {stats['credits_issued']}\n"
        f"🔧 Maintenance: {'ON' if settings['maintenance_enabled'] else 'OFF'}\n📡 Email service: {email_service_status}",
        reply_markup=admin_back(),
    )


@router.callback_query(lambda call: call.data == "admin:users")
async def users_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback) or not callback.message:
        return
    await state.set_state(AdminStates.search_user)
    await callback.answer()
    await callback.message.answer("Send a Telegram ID, username, or name to search.")


@router.message(AdminStates.search_user)
async def users_search(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    async with ctx().database.session_factory() as session:
        users = await ctx().admin.find_users(session, message.text or "")
    await state.clear()
    if not users:
        await message.answer("No users found.", reply_markup=admin_back())
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [InlineKeyboardButton(text=f"{user.telegram_id} · {safe_text(user.username or user.first_name)}", callback_data=f"admin:user:{user.id}")]
        for user in users
    ]
    keyboard.append([InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")])
    await message.answer("👥 SEARCH RESULTS", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.callback_query(lambda call: (call.data or "").startswith("admin:user:"))
async def user_detail(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    user_id = int((callback.data or "").rsplit(":", 1)[1])
    async with ctx().database.session_factory() as session:
        user = await session.get(User, user_id)
    if not user:
        await callback.answer("User not found", show_alert=True)
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.answer()
    await callback.message.edit_text(
        f"👤 USER\n\nID: {user.telegram_id}\nUsername: @{safe_text(user.username, 'none')}\nName: {safe_text(user.first_name)}\n"
        f"Balance: {user.balance}\nReferrals: {user.total_referrals}\nJoined: {fmt_date(user.created_at)}\n"
        f"Last active: {fmt_date(user.last_active_at)}\nBanned: {'yes' if user.is_banned else 'no'}\nAgreed: {'yes' if user.is_agreed else 'no'}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Unban" if user.is_banned else "🚫 Ban", callback_data=f"admin:ban:{user.id}")],
                [InlineKeyboardButton(text="➕ Add Credits", callback_data=f"admin:add:{user.id}"), InlineKeyboardButton(text="➖ Remove Credits", callback_data=f"admin:remove:{user.id}")],
                [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")],
            ]
        ),
    )


@router.callback_query(lambda call: (call.data or "").startswith("admin:ban:"))
async def toggle_ban(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    user_id = int((callback.data or "").rsplit(":", 1)[1])
    async with ctx().database.session_factory() as session:
        user = await session.get(User, user_id)
        if not user:
            await callback.answer("User not found", show_alert=True)
            return
        user.is_banned = not user.is_banned
        await session.commit()
        await ctx().admin.action(session, callback.from_user.id, "BAN_TOGGLE", str(user.telegram_id), str(user.is_banned))
    await callback.answer("Updated")
    await user_detail(callback)


@router.callback_query(lambda call: (call.data or "").startswith("admin:add:") | (call.data or "").startswith("admin:remove:"))
async def credit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback) or not callback.message:
        return
    parts = (callback.data or "").split(":")
    await state.set_state(AdminStates.credit_change)
    await state.update_data(user_id=int(parts[2]), operation=parts[1])
    await callback.answer()
    await callback.message.answer("Send the whole number of credits to change (positive integer).")


@router.message(AdminStates.credit_change)
async def credit_apply(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    data = await state.get_data()
    try:
        amount = int(message.text or "")
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Please send a positive whole number.")
        return
    if data["operation"] == "remove":
        amount = -amount
    async with ctx().database.session_factory() as session:
        try:
            user = await ctx().credits.change(
                session, int(data["user_id"]), amount, "ADMIN_ADD" if amount > 0 else "ADMIN_REMOVE", "Admin credit adjustment", admin_id=message.from_user.id
            )
            await session.commit()
            await ctx().admin.action(session, message.from_user.id, "CREDIT_CHANGE", str(user.telegram_id), str(amount))
        except ValueError as exc:
            await session.rollback()
            await message.answer(str(exc))
            return
    await state.clear()
    await message.answer(f"✅ Credits updated. New balance: {user.balance}")


@router.callback_query(lambda call: call.data == "admin:credits")
async def credits_help(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    await callback.answer()
    await callback.message.edit_text("💳 Search a user from Users, then choose Add or Remove Credits.", reply_markup=admin_back())


@router.callback_query(lambda call: call.data == "admin:bonus")
async def bonus_settings(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        reward = await ctx().settings_service.get(session, "daily_bonus")
        cooldown = await ctx().settings_service.get(session, "daily_bonus_cooldown_hours")
        enabled = await ctx().settings_service.get(session, "daily_bonus_enabled")
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.answer()
    await callback.message.edit_text(
        f"🎁 DAILY BONUS\n\nReward: {reward}\nCooldown: {cooldown} hours\nEnabled: {enabled}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Toggle", callback_data="admin:bonus_toggle")],
                [InlineKeyboardButton(text="Set Reward/Cooldown", callback_data="admin:bonus_set")],
                [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")],
            ]
        ),
    )


@router.callback_query(lambda call: call.data == "admin:bonus_toggle")
async def bonus_toggle(callback: CallbackQuery) -> None:
    if not await _guard(callback):
        return
    async with ctx().database.session_factory() as session:
        enabled = bool(await ctx().settings_service.get(session, "daily_bonus_enabled"))
        await ctx().settings_service.set(session, "daily_bonus_enabled", not enabled)
        await ctx().admin.action(session, callback.from_user.id, "DAILY_BONUS_TOGGLE", details=str(not enabled))
    await callback.answer("Updated")
    await bonus_settings(callback)


@router.callback_query(lambda call: call.data == "admin:bonus_set")
async def bonus_set_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback) or not callback.message:
        return
    await state.set_state(AdminStates.setting_value)
    await state.update_data(setting="bonus")
    await callback.answer()
    await callback.message.answer("Send `reward,cooldown_hours`, for example `10,24`.")


@router.callback_query(lambda call: call.data == "admin:referrals")
async def referral_settings(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        reward = await ctx().settings_service.get(session, "referral_reward")
        enabled = await ctx().settings_service.get(session, "referrals_enabled")
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.answer()
    await callback.message.edit_text(
        f"👥 REFERRALS\n\nReward: {reward} Credits\nEnabled: {enabled}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Toggle", callback_data="admin:ref_toggle")],
                [InlineKeyboardButton(text="Set Reward", callback_data="admin:ref_set")],
                [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")],
            ]
        ),
    )


@router.callback_query(lambda call: call.data == "admin:ref_toggle")
async def ref_toggle(callback: CallbackQuery) -> None:
    if not await _guard(callback):
        return
    async with ctx().database.session_factory() as session:
        enabled = bool(await ctx().settings_service.get(session, "referrals_enabled"))
        await ctx().settings_service.set(session, "referrals_enabled", not enabled)
    await callback.answer("Updated")
    await referral_settings(callback)


@router.callback_query(lambda call: call.data == "admin:ref_set")
async def ref_set_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback) or not callback.message:
        return
    await state.set_state(AdminStates.setting_value)
    await state.update_data(setting="referral")
    await callback.answer()
    await callback.message.answer("Send the new referral reward as a positive whole number.")


@router.message(AdminStates.setting_value)
async def setting_apply(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    data = await state.get_data()
    raw = (message.text or "").strip()
    async with ctx().database.session_factory() as session:
        try:
            if data["setting"] == "bonus":
                reward_text, cooldown_text = raw.split(",", 1)
                reward, cooldown = int(reward_text), float(cooldown_text)
                if reward < 0 or cooldown <= 0:
                    raise ValueError
                await ctx().settings_service.set(session, "daily_bonus", reward)
                await ctx().settings_service.set(session, "daily_bonus_cooldown_hours", cooldown)
            else:
                reward = int(raw)
                if reward < 0:
                    raise ValueError
                await ctx().settings_service.set(session, "referral_reward", reward)
        except ValueError:
            await message.answer("Invalid value. Please try again.")
            return
    await state.clear()
    await message.answer("✅ Setting updated.")


@router.callback_query(lambda call: call.data == "admin:maintenance")
async def maintenance(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        enabled = bool(await ctx().settings_service.get(session, "maintenance_enabled"))
        await ctx().settings_service.set(session, "maintenance_enabled", not enabled)
        await ctx().admin.action(session, callback.from_user.id, "MAINTENANCE_TOGGLE", details=str(not enabled))
    await callback.answer("Maintenance mode updated")
    await callback.message.edit_text(f"🔧 Maintenance is now {'ON' if not enabled else 'OFF'}.", reply_markup=admin_back())


@router.callback_query(lambda call: call.data == "admin:force")
async def force_menu(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        channels = list((await session.scalars(select(ForceJoinChannel).order_by(ForceJoinChannel.id))).all())
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [[InlineKeyboardButton(text=f"➖ {channel.display_name}", callback_data=f"admin:force_remove:{channel.id}")] for channel in channels]
    rows += [
        [InlineKeyboardButton(text="➕ Add Channel", callback_data="admin:force_add")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")],
    ]
    await callback.answer()
    await callback.message.edit_text(
        "📣 FORCE SUBSCRIBE\n\n" + ("\n".join(f"• {channel.display_name} ({channel.channel_id})" for channel in channels) or "No channels configured."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(lambda call: call.data == "admin:force_add")
async def force_add_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback) or not callback.message:
        return
    await state.set_state(AdminStates.force_add)
    await callback.answer()
    await callback.message.answer("Send `channel_id | username | invite_url | display_name`.")


@router.message(AdminStates.force_add)
async def force_add_apply(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    try:
        channel_id, username, invite, display_name = [part.strip() for part in (message.text or "").split("|", 3)]
        if not channel_id or not display_name:
            raise ValueError
    except ValueError:
        await message.answer("Invalid format. Use `channel_id | username | invite_url | display_name`.")
        return
    async with ctx().database.session_factory() as session:
        session.add(
            ForceJoinChannel(
                channel_id=channel_id,
                channel_username=username.lstrip("@") or None,
                invite_url=invite or None,
                display_name=display_name,
            )
        )
        await session.commit()
        await ctx().admin.action(session, message.from_user.id, "FORCE_CHANNEL_ADD", channel_id)
    await state.clear()
    await message.answer("✅ Channel added.")


@router.callback_query(lambda call: (call.data or "").startswith("admin:force_remove:"))
async def force_remove(callback: CallbackQuery) -> None:
    if not await _guard(callback):
        return
    channel_id = int((callback.data or "").rsplit(":", 1)[1])
    async with ctx().database.session_factory() as session:
        channel = await session.get(ForceJoinChannel, channel_id)
        if channel:
            await session.delete(channel)
            await session.commit()
            await ctx().admin.action(session, callback.from_user.id, "FORCE_CHANNEL_REMOVE", str(channel_id))
    await callback.answer("Removed")
    await force_menu(callback)


@router.callback_query(lambda call: call.data == "admin:broadcast")
async def broadcast_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback) or not callback.message:
        return
    await state.set_state(AdminStates.broadcast)
    await callback.answer()
    await callback.message.answer("Send the text, photo, video, or document to broadcast. It will be copied in the background.")


@router.message(AdminStates.broadcast)
async def broadcast_create(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    async with ctx().database.session_factory() as session:
        broadcast = await ctx().admin.broadcast_start(session, message.from_user.id, message)
    await state.clear()
    await message.answer(f"📢 Broadcast #{broadcast.id} queued. Delivery is running in the background.")
    asyncio.create_task(_run_broadcast(broadcast.id), name=f"broadcast-{broadcast.id}")


async def _run_broadcast(broadcast_id: int) -> None:
    async with ctx().database.session_factory() as session:
        broadcast = await session.get(Broadcast, broadcast_id)
        users = list((await session.scalars(select(User.telegram_id).where(~User.is_banned))).all())
    sent = failed = blocked = 0
    for telegram_id in users:
        try:
            await ctx().bot.copy_message(telegram_id, broadcast.source_chat_id, broadcast.source_message_id)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await ctx().bot.copy_message(telegram_id, broadcast.source_chat_id, broadcast.source_message_id)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.12)
    async with ctx().database.session_factory() as session:
        await ctx().admin.touch_broadcast(session, broadcast_id, sent=sent, failed=failed, blocked=blocked, done=True)


@router.callback_query(lambda call: call.data == "admin:mailboxes")
@router.callback_query(lambda call: call.data == "admin:emails")
async def mailbox_email_stats(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    async with ctx().database.session_factory() as session:
        stats = await ctx().admin.dashboard(session)
    await callback.answer()
    await callback.message.edit_text(
        f"📧 Mailboxes: {stats['mailboxes']}\n📩 Emails: {stats['emails']}\n\nUse Users to inspect individual owners.",
        reply_markup=admin_back(),
    )


@router.callback_query(lambda call: call.data in {"admin:settings", "admin:logs", "admin:media"})
async def simple_admin_pages(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    if callback.data == "admin:settings":
        async with ctx().database.session_factory() as session:
            values = await ctx().settings_service.all(session)
        text = "⚙️ SETTINGS\n\n" + "\n".join(f"{key}: {value}" for key, value in values.items())
    elif callback.data == "admin:logs":
        text = "📜 Logs are written to the service log and admin action history."
    else:
        text = "🖼️ Media broadcasts are supported by copying Telegram messages; no files are stored by the bot."
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=admin_back())


@router.callback_query(lambda call: call.data == "admin:health")
async def health(callback: CallbackQuery) -> None:
    if not await _guard(callback) or not callback.message:
        return
    database = await ctx().database.ping()
    try:
        await ctx().mailtm.domains()
        email_service = "reachable"
    except Exception:
        email_service = "unreachable"
    try:
        await ctx().bot.get_me()
        telegram = "reachable"
    except Exception:
        telegram = "unreachable"
    await callback.answer()
    await callback.message.edit_text(
        f"❤️ SYSTEM HEALTH\n\nPostgreSQL: {'connected' if database else 'unavailable'}\nTelegram: {telegram}\nEmail service: {email_service}",
        reply_markup=admin_back(),
    )
