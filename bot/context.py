from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot

from bot.config import Settings
from bot.database.session import Database
from bot.services.admin import AdminService
from bot.services.bonus import BonusService
from bot.services.credits import CreditService
from bot.services.force_join import ForceJoinService
from bot.services.mailbox import MailboxService
from bot.services.mailtm.client import MailTmClient
from bot.services.mailtm.realtime import MailEventManager
from bot.services.referrals import ReferralService
from bot.services.settings import SettingsService
from bot.utils.encryption import CredentialCipher


@dataclass
class AppContext:
    settings: Settings
    bot: Bot
    database: Database
    settings_service: SettingsService
    credits: CreditService
    bonus: BonusService
    referrals: ReferralService
    force_join: ForceJoinService
    admin: AdminService
    mailtm: MailTmClient
    mailbox: MailboxService
    events: MailEventManager
    cipher: CredentialCipher


_context: AppContext | None = None


def set_context(value: AppContext) -> None:
    global _context
    _context = value


def ctx() -> AppContext:
    if _context is None:
        raise RuntimeError("Application context is not initialized")
    return _context
