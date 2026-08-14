from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AdminAction, Broadcast, CreditTransaction, EmailMessage, Mailbox, Referral, User


class AdminService:
    async def action(
        self, session: AsyncSession, admin_id: int, action: str, target_id: str | None = None, details: str = ""
    ) -> None:
        session.add(AdminAction(admin_id=admin_id, action=action, target_id=target_id, details=details))
        await session.commit()

    async def dashboard(self, session: AsyncSession) -> dict[str, int]:
        return {
            "users": int(await session.scalar(select(func.count(User.id))) or 0),
            "active_users": int(await session.scalar(select(func.count(User.id)).where(~User.is_banned)) or 0),
            "banned": int(await session.scalar(select(func.count(User.id)).where(User.is_banned)) or 0),
            "mailboxes": int(await session.scalar(select(func.count(Mailbox.id)).where(Mailbox.status == "active")) or 0),
            "emails": int(await session.scalar(select(func.count(EmailMessage.id))) or 0),
            "referrals": int(await session.scalar(select(func.count(Referral.id))) or 0),
            "credits_used": abs(
                int(
                    await session.scalar(
                        select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                            CreditTransaction.type == "MAIL_CREATION"
                        )
                    )
                    or 0
                )
            ),
            "credits_issued": int(
                await session.scalar(
                    select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                        CreditTransaction.amount > 0
                    )
                )
                or 0
            ),
        }

    async def find_users(self, session: AsyncSession, query: str, limit: int = 10) -> list[User]:
        query = query.strip()
        statement = select(User).order_by(User.created_at.desc()).limit(limit)
        if query.isdigit():
            statement = select(User).where(User.telegram_id == int(query)).limit(limit)
        elif query:
            statement = (
                select(User)
                .where((User.username.ilike(f"%{query}%")) | (User.first_name.ilike(f"%{query}%")))
                .order_by(User.created_at.desc())
                .limit(limit)
            )
        return list((await session.scalars(statement)).all())

    async def broadcast_start(self, session: AsyncSession, admin_id: int, message) -> Broadcast:
        broadcast = Broadcast(
            admin_id=admin_id,
            content_type=message.content_type,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
        session.add(broadcast)
        await session.commit()
        await session.refresh(broadcast)
        return broadcast

    async def touch_broadcast(
        self, session: AsyncSession, broadcast_id: int, *, sent: int = 0, failed: int = 0, blocked: int = 0, done: bool = False
    ) -> None:
        broadcast = await session.get(Broadcast, broadcast_id)
        if not broadcast:
            return
        broadcast.sent += sent
        broadcast.failed += failed
        broadcast.blocked += blocked
        if done:
            broadcast.status = "finished"
            broadcast.finished_at = datetime.now(timezone.utc)
        await session.commit()
