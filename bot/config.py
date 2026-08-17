from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _admin_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_IDS", "")
    values: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if value:
            try:
                values.add(int(value))
            except ValueError as exc:
                raise RuntimeError("ADMIN_IDS must contain comma-separated integers") from exc
    if not values:
        raise RuntimeError("ADMIN_IDS is required")
    return frozenset(values)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    admin_ids: frozenset[int]
    encryption_key: str
    support_bot_url: str
    port: int
    mailtm_api_base: str = "https://api.mail.tm"
    mailtm_hub_url: str = "https://mercure.mail.tm/.well-known/mercure"
    mailtm_fallback_api_base: str = "https://api.mail.gw"
    # Mail.tm currently limits the API to roughly 30 requests per minute.
    # Keep a small safety margin so polling does not block mailbox creation.
    mailtm_rate_per_second: float = 1.0
    mailbox_pool_target: int = 20
    mailbox_pool_refill_threshold: int = 10
    mailbox_pool_refill_interval: int = 10
    attachment_limit_bytes: int = 10 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = _required("DATABASE_URL")
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return cls(
            bot_token=_required("BOT_TOKEN"),
            database_url=database_url,
            admin_ids=_admin_ids(),
            encryption_key=_required("ENCRYPTION_KEY"),
            support_bot_url=os.getenv("SUPPORT_BOT_URL", "https://t.me/HelpSupportteambot"),
            port=int(os.getenv("PORT", "8000")),
            mailtm_api_base=os.getenv("MAILTM_API_BASE", "https://api.mail.tm"),
            mailtm_hub_url=os.getenv(
                "MAILTM_HUB_URL",
                "https://mercure.mail.tm/.well-known/mercure",
            ),
            mailtm_fallback_api_base=os.getenv("MAILTM_FALLBACK_API_BASE", "https://api.mail.gw"),
            mailtm_rate_per_second=float(os.getenv("MAILTM_RATE_PER_SECOND", "1.0")),
            mailbox_pool_target=max(int(os.getenv("MAILBOX_POOL_TARGET", "20")), 1),
            mailbox_pool_refill_threshold=max(int(os.getenv("MAILBOX_POOL_REFILL_THRESHOLD", "10")), 0),
            mailbox_pool_refill_interval=max(int(os.getenv("MAILBOX_POOL_REFILL_INTERVAL", "10")), 5),
        )
