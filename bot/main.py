from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from uvicorn import Config, Server

from bot.api.health import create_app
from bot.bot.handlers import admin, bonus, disclaimer, email, help, info, mailbox, menu, referral, start
from bot.bot.helpers import email_preview_text
from bot.config import Settings
from bot.context import AppContext, set_context
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
from bot.utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def build_context() -> AppContext:
    settings = Settings.from_env()
    database = Database(settings)
    await database.init()
    settings_service = SettingsService()
    async with database.session_factory() as session:
        await settings_service.ensure_defaults(session)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))
    mailtm = MailTmClient(
        settings.mailtm_api_base,
        settings.mailtm_hub_url,
        settings.mailtm_rate_per_second,
        settings.mailtm_fallback_api_base,
    )
    await mailtm.start()
    await mailtm.warm_domains()
    cipher = CredentialCipher(settings.encryption_key)
    credits = CreditService()
    mailbox = MailboxService(
        mailtm,
        cipher,
        credits,
        settings_service,
        database,
        settings.mailbox_pool_target,
        settings.mailbox_pool_refill_threshold,
    )

    async def notify(telegram_id, message, mailbox_record) -> None:
        async with database.session_factory() as session:
            notifications = await settings_service.get(session, "notifications_enabled")
        if not notifications:
            return
        body = email_preview_text(
            mailbox_record.email_address,
            message.sender,
            message.subject,
            message.text_content,
        )
        from bot.bot.keyboards.user import email_notification

        try:
            await bot.send_message(telegram_id, body, reply_markup=email_notification(message.id))
        except Exception:
            logger.exception("incoming email notification failed")

    events = MailEventManager(database, mailtm, mailbox, settings_service, cipher, notify)
    context = AppContext(
        settings=settings,
        bot=bot,
        database=database,
        settings_service=settings_service,
        credits=credits,
        bonus=BonusService(settings_service, credits),
        referrals=ReferralService(settings_service),
        force_join=ForceJoinService(settings_service),
        admin=AdminService(),
        mailtm=mailtm,
        mailbox=mailbox,
        events=events,
        cipher=cipher,
    )
    set_context(context)
    return context


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(start.router)
    dispatcher.include_router(disclaimer.router)
    dispatcher.include_router(email.router)
    dispatcher.include_router(mailbox.router)
    dispatcher.include_router(bonus.router)
    dispatcher.include_router(referral.router)
    dispatcher.include_router(info.router)
    dispatcher.include_router(help.router)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(menu.router)
    return dispatcher


async def run_check(context: AppContext) -> None:
    await context.mailtm.domains(force=True)
    me = await context.bot.get_me()
    logger.info("check passed bot_username=%s active_domains=%s", me.username, len(await context.mailtm.domains()))


async def run() -> None:
    context = await build_context()
    dispatcher = build_dispatcher()
    await context.events.start()
    pool_task = asyncio.create_task(
        context.mailbox.run_pool_refiller(context.settings.mailbox_pool_refill_interval),
        name="mailbox-pool-refiller",
    )
    api = create_app()
    server = Server(Config(api, host="0.0.0.0", port=context.settings.port, log_config=None))
    try:
        await asyncio.gather(
            dispatcher.start_polling(context.bot, allowed_updates=dispatcher.resolve_used_update_types()),
            server.serve(),
        )
    finally:
        pool_task.cancel()
        await asyncio.gather(pool_task, return_exceptions=True)
        await context.events.stop()
        await context.mailtm.close()
        await context.bot.session.close()
        await context.database.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Temp Mail Xpress Telegram bot")
    parser.add_argument("--check", action="store_true", help="check PostgreSQL, Telegram, and email service without polling")
    args = parser.parse_args()
    setup_logging()
    context = await build_context()
    try:
        if args.check:
            await run_check(context)
        else:
            await context.events.start()
            pool_task = asyncio.create_task(
                context.mailbox.run_pool_refiller(context.settings.mailbox_pool_refill_interval),
                name="mailbox-pool-refiller",
            )
            dispatcher = build_dispatcher()
            api = create_app()
            server = Server(Config(api, host="0.0.0.0", port=context.settings.port, log_config=None))
            try:
                await asyncio.gather(
                    dispatcher.start_polling(context.bot, allowed_updates=dispatcher.resolve_used_update_types()),
                    server.serve(),
                )
            finally:
                pool_task.cancel()
                await asyncio.gather(pool_task, return_exceptions=True)
    finally:
        await context.events.stop()
        await context.mailtm.close()
        await context.bot.session.close()
        await context.database.close()


if __name__ == "__main__":
    asyncio.run(main())
