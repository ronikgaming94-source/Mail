from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📧 Create New Mail")],
        [KeyboardButton(text="👤 My Info"), KeyboardButton(text="🎁 Daily Bonus")],
        [KeyboardButton(text="👥 Refer & Earn"), KeyboardButton(text="⚠️ Disclaimer")],
        [KeyboardButton(text="🆘 Help")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def disclaimer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ I Agree", callback_data="disclaimer:agree")],
            [InlineKeyboardButton(text="❌ Decline", callback_data="disclaimer:decline")],
        ]
    )


def mailbox_card(mailbox_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Refresh", callback_data=f"mailbox:refresh:{mailbox_id}"),
                InlineKeyboardButton(text="🗑️ Delete Mail", callback_data=f"mailbox:delete:{mailbox_id}"),
            ],
            [InlineKeyboardButton(text="📬 My Mailboxes", callback_data="mailbox:list")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")],
        ]
    )


def delete_confirmation(mailbox_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm Delete", callback_data=f"mailbox:confirm_delete:{mailbox_id}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"mailbox:show:{mailbox_id}")],
        ]
    )


def email_notification(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 View Full Email", callback_data=f"email:view:{message_id}")],
            [InlineKeyboardButton(text="🗑️ Delete Email", callback_data=f"email:delete:{message_id}")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")],
        ]
    )


def email_view(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Delete Email", callback_data=f"email:delete:{message_id}")],
            [InlineKeyboardButton(text="🔙 Back", callback_data=f"email:back:{message_id}")],
        ]
    )
