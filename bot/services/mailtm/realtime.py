from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database.models import EmailMessage, Mailbox, User
from bot.database.session import Database
from bot.services.mailbox import MailboxService
from bot.services.mailtm.client import MailTmClient, MailTmError
from bot.services.mailtm.parser import event_message_id, parse_message
from bot.services.settings import SettingsService
from bot.utils.encryption import CredentialCipher

logger = logging.getLogger(__name__)


class MailEventManager:
    def __init__(
        self,
        database: Database,
        mailtm: MailTmClient,
        mailbox_service: MailboxService,
        settings: SettingsService,
        cipher: CredentialCipher,
        notify: Callable[[int, EmailMessage, Mailbox], Awaitable[None]],
    ) -> None:
        self.database = database
        self.mailtm = mailtm
        self.mailbox_service = mailbox_service
        self.settings = settings
        self.cipher = cipher
        self.notify = notify
        self.tasks: dict[int, asyncio.Task[None]] = {}
        self.stopping = False

    async def start(self) -> None:
        self.stopping = False
        async with self.database.session_factory() as session:
            mailboxes = list((await session.scalars(select(Mailbox).where(Mailbox.status == "active"))).all())
        for mailbox in mailboxes:
            self.add(mailbox.id)
        logger.info("mail event manager loaded %s active mailboxes", len(mailboxes))

    def add(self, mailbox_id: int) -> None:
        if mailbox_id not in self.tasks or self.tasks[mailbox_id].done():
            self.tasks[mailbox_id] = asyncio.create_task(self._listen(mailbox_id), name=f"mailbox-listener-{mailbox_id}")

    async def remove(self, mailbox_id: int) -> None:
        task = self.tasks.pop(mailbox_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop(self) -> None:
        self.stopping = True
        tasks = list(self.tasks.values())
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _listen(self, mailbox_id: int) -> None:
        backoff = 1
        while not self.stopping:
            try:
                async with self.database.session_factory() as session:
                    mailbox = await session.get(Mailbox, mailbox_id)
                    if not mailbox or mailbox.status != "active":
                        return
                    token = self.cipher.decrypt(mailbox.mailtm_token_encrypted)
                summaries = await self.mailtm.list_messages(token)
                # Mail.tm's Mercure stream is useful for low-latency delivery,
                # but it can be interrupted by hosting proxies. Polling the
                # account API keeps delivery reliable and is deduplicated by
                # the local message ID constraint.
                for summary in reversed(summaries):
                    message_id = event_message_id(summary)
                    if message_id:
                        await self.process_message(mailbox_id, message_id, token)
                backoff = 1
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except MailTmError as exc:
                logger.warning("mailbox sync error mailbox_id=%s status=%s", mailbox_id, exc.status)
                if exc.status == 401:
                    await self._reauthenticate(mailbox_id)
            except Exception:
                logger.exception("unexpected mailbox sync error mailbox_id=%s", mailbox_id)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _reauthenticate(self, mailbox_id: int) -> None:
        async with self.database.session_factory() as session:
            mailbox = await session.get(Mailbox, mailbox_id)
            if not mailbox or mailbox.status != "active":
                return
            try:
                password = self.cipher.decrypt(mailbox.mailtm_password_encrypted)
                token = await self.mailtm.authenticate(mailbox.email_address, password)
                mailbox.mailtm_token_encrypted = self.cipher.encrypt(token)
                await session.commit()
            except Exception:
                mailbox.status = "error"
                await session.commit()
                logger.warning("mailbox token refresh failed mailbox_id=%s", mailbox_id)

    async def process_message(self, mailbox_id: int, message_id: str, token: str) -> None:
        async with self.database.session_factory() as session:
            mailbox = await session.get(Mailbox, mailbox_id)
            if not mailbox or mailbox.status != "active":
                return
            user = await session.get(User, mailbox.user_id)
            if not user or user.is_banned:
                return
            if await session.scalar(select(EmailMessage.id).where(EmailMessage.mailtm_message_id == message_id)):
                return
            payload = await self.mailtm.get_message(message_id, token)
            parsed = parse_message(payload)
            if not parsed["mailtm_message_id"]:
                parsed["mailtm_message_id"] = message_id
            message = EmailMessage(mailbox_id=mailbox.id, **parsed)
            session.add(message)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            await session.refresh(message)
            await self.notify(user.telegram_id, message, mailbox)
