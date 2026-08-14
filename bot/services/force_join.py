from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ForceJoinChannel
from bot.services.settings import SettingsService

logger = logging.getLogger(__name__)


class ForceJoinService:
    def __init__(self, settings: SettingsService) -> None:
        self.settings = settings

    async def channels(self, session: AsyncSession) -> list[ForceJoinChannel]:
        enabled = await self.settings.get(session, "force_subscribe_enabled")
        if not enabled:
            return []
        return list((await session.scalars(select(ForceJoinChannel).where(ForceJoinChannel.enabled))).all())

    async def missing(self, bot: Bot, session: AsyncSession, telegram_id: int) -> list[ForceJoinChannel]:
        channels = await self.channels(session)
        missing: list[ForceJoinChannel] = []
        for channel in channels:
            try:
                member = await bot.get_chat_member(channel.channel_id, telegram_id)
                if member.status in {"left", "kicked"}:
                    missing.append(channel)
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                logger.warning("force subscribe check failed channel=%s error=%s", channel.channel_id, type(exc).__name__)
                missing.append(channel)
        return missing
