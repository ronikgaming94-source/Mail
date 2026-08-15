from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import EmailMessage, Mailbox, User
from bot.services.credits import CreditService
from bot.services.mailtm.client import MailTmClient, MailTmError
from bot.services.settings import SettingsService
from bot.utils.encryption import CredentialCipher

logger = logging.getLogger(__name__)


class MailboxService:
    def __init__(
        self,
        mailtm: MailTmClient,
        cipher: CredentialCipher,
        credits: CreditService,
        settings: SettingsService,
    ) -> None:
        self.mailtm = mailtm
        self.cipher = cipher
        self.credits = credits
        self.settings = settings

    async def create(self, session: AsyncSession, user_id: int) -> Mailbox:
        cost = int(await self.settings.get(session, "mail_credit_cost") or 1)
        await session.rollback()
        credentials = None
        try:
            async with session.begin():
                user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
                if user is None or user.is_banned:
                    raise MailTmError("Your account cannot create mailboxes")
                if user.balance < cost:
                    raise MailTmError("You do not have enough credits")
                for _ in range(3):
                    reserved_addresses = set((await session.scalars(select(Mailbox.email_address))).all())
                    candidate = await self.mailtm.create_account(reserved_addresses)
                    duplicate = await session.scalar(
                        select(Mailbox.id).where(func.lower(Mailbox.email_address) == candidate.address.casefold())
                    )
                    if duplicate is None:
                        credentials = candidate
                        break
                    await self.mailtm.delete_account(candidate.account_id, candidate.token)
                if credentials is None:
                    raise MailTmError("Unable to create a unique mailbox address")
                mailbox = Mailbox(
                    user_id=user_id,
                    mailtm_account_id=credentials.account_id,
                    email_address=credentials.address,
                    mailtm_password_encrypted=self.cipher.encrypt(credentials.password),
                    mailtm_token_encrypted=self.cipher.encrypt(credentials.token),
                    status="active",
                )
                session.add(mailbox)
                user.balance -= cost
                from bot.database.models import CreditTransaction

                session.add(
                    CreditTransaction(
                        user_id=user_id,
                        amount=-cost,
                        type="MAIL_CREATION",
                        description="Mail.tm mailbox creation",
                        reference_id=credentials.account_id,
                    )
                )
                await session.flush()
            logger.info("mailbox created user_id=%s mailbox_id=%s", user_id, mailbox.id)
            return mailbox
        except Exception:
            if credentials is not None:
                try:
                    await self.mailtm.delete_account(credentials.account_id, credentials.token)
                except Exception:
                    logger.warning("Could not clean up remote Mail.tm account after failed local transaction")
            raise

    def decrypt_token(self, mailbox: Mailbox) -> str:
        return self.cipher.decrypt(mailbox.mailtm_token_encrypted)

    def decrypt_password(self, mailbox: Mailbox) -> str:
        return self.cipher.decrypt(mailbox.mailtm_password_encrypted)

    async def delete(self, session: AsyncSession, mailbox_id: int, user_id: int) -> None:
        mailbox = await session.scalar(
            select(Mailbox).where(Mailbox.id == mailbox_id, Mailbox.user_id == user_id, Mailbox.status == "active")
        )
        if mailbox is None:
            raise ValueError("Mailbox not found")
        token = self.decrypt_token(mailbox)
        try:
            await self.mailtm.delete_account(mailbox.mailtm_account_id, token)
        except MailTmError as exc:
            if exc.status != 404:
                raise
        mailbox.status = "deleted"
        mailbox.deleted_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("mailbox deleted mailbox_id=%s user_id=%s", mailbox_id, user_id)

    async def refresh(self, session: AsyncSession, mailbox_id: int, user_id: int) -> tuple[Mailbox, int]:
        mailbox = await session.scalar(
            select(Mailbox).where(Mailbox.id == mailbox_id, Mailbox.user_id == user_id, Mailbox.status == "active")
        )
        if mailbox is None:
            raise ValueError("Mailbox not found")
        count = int(await session.scalar(select(func.count(EmailMessage.id)).where(EmailMessage.mailbox_id == mailbox_id)) or 0)
        return mailbox, count

    async def active_for_user(self, session: AsyncSession, user_id: int) -> list[Mailbox]:
        return list(
            (
                await session.scalars(
                    select(Mailbox).where(Mailbox.user_id == user_id, Mailbox.status == "active").order_by(Mailbox.created_at.desc())
                )
            ).all()
        )
