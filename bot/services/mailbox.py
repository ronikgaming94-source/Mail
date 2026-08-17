from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import CreditTransaction, EmailMessage, Mailbox, User
from bot.database.session import Database
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
        database: Database,
        pool_target: int = 20,
        pool_refill_threshold: int = 10,
    ) -> None:
        self.mailtm = mailtm
        self.cipher = cipher
        self.credits = credits
        self.settings = settings
        self.database = database
        self.pool_target = max(pool_target, 0)
        self.pool_refill_threshold = max(min(pool_refill_threshold, self.pool_target), 0)
        self._remote_creation_lock = asyncio.Lock()
        self._pool_refill_lock = asyncio.Lock()

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

                # Claiming is database-atomic: two users cannot receive the
                # same pre-created mailbox, even if they tap simultaneously.
                mailbox = await session.scalar(
                    select(Mailbox)
                    .where(Mailbox.status == "available", Mailbox.user_id.is_(None))
                    .order_by(Mailbox.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if mailbox is not None:
                    mailbox.user_id = user_id
                    mailbox.status = "active"
                else:
                    for _ in range(3):
                        reserved_addresses = set((await session.scalars(select(Mailbox.email_address))).all())
                        async with self._remote_creation_lock:
                            candidate = await self.mailtm.create_account(reserved_addresses)
                        duplicate = await session.scalar(
                            select(Mailbox.id).where(func.lower(Mailbox.email_address) == candidate.address.casefold())
                        )
                        if duplicate is None:
                            credentials = candidate
                            break
                        await self.mailtm.delete_account(candidate.account_id, candidate.token, candidate.address)
                    if credentials is None:
                        raise MailTmError("Unable to create a unique mailbox")
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
                session.add(
                    CreditTransaction(
                        user_id=user_id,
                        amount=-cost,
                        type="MAIL_CREATION",
                        description="Mail.tm mailbox creation",
                        reference_id=mailbox.mailtm_account_id,
                    )
                )
                await session.flush()
            logger.info("mailbox created user_id=%s mailbox_id=%s", user_id, mailbox.id)
            return mailbox
        except Exception:
            if credentials is not None:
                try:
                    await self.mailtm.delete_account(
                        credentials.account_id,
                        credentials.token,
                        credentials.address,
                    )
                except Exception:
                    logger.warning("Could not clean up remote Mail.tm account after failed local transaction")
            raise

    async def replenish_pool(self) -> int:
        """Create never-before-assigned mailboxes until the pool reaches target."""
        if self.pool_target <= 0:
            return 0
        async with self._pool_refill_lock:
            created = 0
            while True:
                async with self.database.session_factory() as session:
                    available = int(
                        await session.scalar(
                            select(func.count(Mailbox.id)).where(
                                Mailbox.status == "available",
                                Mailbox.user_id.is_(None),
                            )
                        )
                        or 0
                    )
                    if available >= self.pool_target:
                        return created
                    reserved_addresses = set((await session.scalars(select(Mailbox.email_address))).all())

                credentials = None
                keep_account = False
                try:
                    async with self._remote_creation_lock:
                        credentials = await self.mailtm.create_account(reserved_addresses)
                    async with self.database.session_factory() as session:
                        async with session.begin():
                            duplicate = await session.scalar(
                                select(Mailbox.id).where(
                                    func.lower(Mailbox.email_address) == credentials.address.casefold()
                                )
                            )
                            if duplicate is None:
                                session.add(
                                    Mailbox(
                                        user_id=None,
                                        mailtm_account_id=credentials.account_id,
                                        email_address=credentials.address,
                                        mailtm_password_encrypted=self.cipher.encrypt(credentials.password),
                                        mailtm_token_encrypted=self.cipher.encrypt(credentials.token),
                                        status="available",
                                    )
                                )
                                await session.flush()
                                keep_account = True
                                created += 1
                finally:
                    if credentials is not None and not keep_account:
                        try:
                            await self.mailtm.delete_account(
                                credentials.account_id,
                                credentials.token,
                                credentials.address,
                            )
                        except Exception:
                            logger.warning("Could not clean up unused pool mailbox", exc_info=True)

    async def run_pool_refiller(self, interval_seconds: int = 10) -> None:
        while True:
            try:
                stats = await self.pool_stats()
                if stats["available"] < self.pool_refill_threshold:
                    added = await self.replenish_pool()
                    if added:
                        logger.info("mailbox pool replenished count=%s available=%s", added, stats["available"] + added)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mailbox pool refill failed")
            await asyncio.sleep(interval_seconds)

    async def pool_stats(self) -> dict[str, object]:
        async with self.database.session_factory() as session:
            available_rows = list(
                (
                    await session.scalars(
                        select(Mailbox.email_address)
                        .where(Mailbox.status == "available", Mailbox.user_id.is_(None))
                        .order_by(Mailbox.id)
                    )
                ).all()
            )
            active = int(
                await session.scalar(
                    select(func.count(Mailbox.id)).where(
                        Mailbox.status == "active",
                        Mailbox.user_id.is_not(None),
                    )
                )
                or 0
            )
            deleted = int(
                await session.scalar(select(func.count(Mailbox.id)).where(Mailbox.status == "deleted")) or 0
            )
        providers: dict[str, int] = {}
        for address in available_rows:
            domain = address.rsplit("@", 1)[-1].lower()
            providers[domain] = providers.get(domain, 0) + 1
        return {
            "available": len(available_rows),
            "active": active,
            "deleted": deleted,
            "providers": providers,
        }

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
            await self.mailtm.delete_account(mailbox.mailtm_account_id, token, mailbox.email_address)
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
