from __future__ import annotations

from datetime import datetime, timezone
from html import escape


TELEGRAM_TEXT_LIMIT = 4096


def safe_text(value: object | None, fallback: str = "—") -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text or fallback


def clip(value: str, limit: int = 3500) -> str:
    value = value or ""
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 40)] + "\n\n[Message truncated for Telegram]"


def fmt_date(value: datetime | None) -> str:
    if not value:
        return "—"
    return value.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def html_escape(value: object | None) -> str:
    return escape(safe_text(value))
