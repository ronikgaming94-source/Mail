from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def admin_menu() -> InlineKeyboardMarkup:
    labels = [
        ("📊 Dashboard", "admin:dashboard"),
        ("👥 Users", "admin:users"),
        ("📧 Mailboxes", "admin:mailboxes"),
        ("📦 Mailbox Pool Stock", "admin:pool"),
        ("📩 Emails", "admin:emails"),
        ("💳 Credits", "admin:credits"),
        ("🎁 Daily Bonus", "admin:bonus"),
        ("👥 Referrals", "admin:referrals"),
        ("📢 Broadcast", "admin:broadcast"),
        ("📣 Force Subscribe", "admin:force"),
        ("🔧 Maintenance", "admin:maintenance"),
        ("⚙️ Settings", "admin:settings"),
        ("📜 Logs", "admin:logs"),
        ("🖼️ Media", "admin:media"),
        ("❤️ System Health", "admin:health"),
    ]
    rows = [[InlineKeyboardButton(text=label, callback_data=data)] for label, data in labels]
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")]])
