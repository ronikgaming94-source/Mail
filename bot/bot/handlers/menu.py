from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.bot.helpers import gate, send_home
from bot.context import ctx

router = Router(name="menu")


@router.callback_query(lambda call: call.data == "menu:home")
async def home_callback(callback: CallbackQuery) -> None:
    user, allowed = await gate(callback)
    if not allowed:
        return
    await callback.answer()
    if callback.message:
        await send_home(callback.message, user)


@router.message(F.text == "⚠️ Disclaimer")
async def show_disclaimer(message: Message) -> None:
    async with ctx().database.session_factory() as session:
        text = await ctx().settings_service.get(session, "disclaimer")
    await message.answer(str(text))
