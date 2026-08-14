from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import ForceJoinChannel


def force_join(channels: list[ForceJoinChannel]) -> InlineKeyboardMarkup:
    rows = []
    for channel in channels:
        url = channel.invite_url or (
            f"https://t.me/{channel.channel_username.lstrip('@')}" if channel.channel_username else None
        )
        if url:
            rows.append([InlineKeyboardButton(text=f"📢 {channel.display_name}", url=url)])
    rows.append([InlineKeyboardButton(text="✅ Verify", callback_data="force:verify")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
