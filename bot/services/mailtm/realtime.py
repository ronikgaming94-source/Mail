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

    async def _sync_mailbox(self, mailbox_id: int, token: str, mailbox_address: str) -> None:
        summaries = await self.mailtm.list_messages(token, mailbox_address)
        for summary in reversed(summaries):
            message_id = event_message_id(summary)
            if message_id:
                await self.process_message(mailbox_id, message_id, token, mailbox_address)

    async def _listen(self, mailbox_id: int) -> None:
        backoff = 1
        while not self.stopping:
            try:
                async with self.database.session_factory() as session:
                    mailbox = await session.get(Mailbox, mailbox_id)
                    if not mailbox or mailbox.status != "active":
                        return
                    account_id = mailbox.mailtm_account_id
                    token = self.cipher.decrypt(mailbox.mailtm_token_encrypted)
                    mailbox_address = mailbox.email_address

                # Recover messages that arrived while the process was starting
                # or while an SSE connection was being re-established.
                await self._sync_mailbox(mailbox_id, token, mailbox_address)
                backoff = 1

                # Mercure delivers account-change events immediately. The event
                # payload is an account resource, so fetch the message list only
                # when the provider signals that the inbox changed.
                async for _event in self.mailtm.sse_events(account_id, token, mailbox_address):
                    await self._sync_mailbox(mailbox_id, token, mailbox_address)
                    backoff = 1

                raise MailTmError("Mail.tm event stream ended")
            except asyncio.CancelledError:
                raise
            except MailTmError as exc:
                logger.warning("mailbox sync error mailbox_id=%s status=%s", mailbox_id, exc.status)
                if exc.status == 401:
                    await self._reauthenticate(mailbox_id)
            except Exception:
                logger.exception("unexpected mailbox sync error mailbox_id=%s", mailbox_id)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _reauthenticate(self, mailbox_id: int) -> None:
        async with self.database.session_factory() as session:
            mailbox = await session.get(Mailbox, mailbox_id)
            if not mailbox or mailbox.status != "active":
                return
            mailbox_address = mailbox.email_address
            password = self.cipher.decrypt(mailbox.mailtm_password_encrypted)

        # Never hold a database connection while waiting on the provider.
        try:
            token = await self.mailtm.authenticate(mailbox_address, password)
        except MailTmError as exc:
            # A rate limit or upstream outage is temporary; do not disable a
            # healthy mailbox just because re-authentication was delayed.
            if exc.status not in {401, 403}:
                logger.warning(
                    "mailbox token refresh deferred mailbox_id=%s status=%s",
                    mailbox_id,
                    exc.status,
                )
                return
            async with self.database.session_factory() as session:
                mailbox = await session.get(Mailbox, mailbox_id)
                if mailbox and mailbox.status == "active":
                    mailbox.status = "error"
                    await session.commit()
            logger.warning("mailbox token refresh failed mailbox_id=%s status=%s", mailbox_id, exc.status)
            return
        except Exception:
            logger.exception("mailbox token refresh deferred mailbox_id=%s", mailbox_id)
            return

        async with self.database.session_factory() as session:
            mailbox = await session.get(Mailbox, mailbox_id)
            if mailbox and mailbox.status == "active":
                mailbox.mailtm_token_encrypted = self.cipher.encrypt(token)
                await session.commit()

    async def process_message(
        self,
        mailbox_id: int,
        message_id: str,
        token: str,
        mailbox_address: str | None = None,
    ) -> None:
        async with self.database.session_factory() as session:
            mailbox = await session.get(Mailbox, mailbox_id)
            if not mailbox or mailbox.status != "active":
                return
            user = await session.get(User, mailbox.user_id)
            if not user or user.is_banned:
                return
            if await session.scalar(select(EmailMessage.id).where(EmailMessage.mailtm_message_id == message_id)):
                return
            address = mailbox_address or mailbox.email_address

        # Fetching the message can take time and must not occupy a database
        # connection while the provider responds.
        payload = await self.mailtm.get_message(message_id, token, address)
        parsed = parse_message(payload)
        if not parsed["mailtm_message_id"]:
            parsed["mailtm_message_id"] = message_id

        async with self.database.session_factory() as session:
            mailbox = await session.get(Mailbox, mailbox_id)
            if not mailbox or mailbox.status != "active":
                return
            user = await session.get(User, mailbox.user_id)
            if not user or user.is_banned:
                return
            if await session.scalar(select(EmailMessage.id).where(EmailMessage.mailtm_message_id == message_id)):
                return
            message = EmailMessage(mailbox_id=mailbox.id, **parsed)
            session.add(message)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            await session.refresh(message)
            telegram_id = user.telegram_id

        await self.notify(telegram_id, message, mailbox)
