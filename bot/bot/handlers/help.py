from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.bot.helpers import gate
from bot.context import ctx

router = Router(name="help")


@router.message(F.text == "🆘 Help")
async def help_message(message: Message) -> None:
    _, allowed = await gate(message)
    if not allowed:
        return
    await message.answer(
        "🆘 HELP\n\nUse the buttons to create real Mail.tm mailboxes and manage your credits. "
        "Mailbox availability and retention depend on Mail.tm.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Contact Support", url=ctx().settings.support_bot_url)]]
        ),
    )
