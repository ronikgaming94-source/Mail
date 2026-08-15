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
        "🆘 HELP\n\n"
        "Need any help or facing an issue?\n\n"
        "👇 Tap the button below, contact our Support Team and drop your message. "
        "Our team will get back to you as soon as possible.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Contact Support", url="http://t.me/HelpSupportteambot")]
            ]
        ),
    )
