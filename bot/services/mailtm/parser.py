from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import bleach


def _address(value: Any) -> str:
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        address = str(value.get("address") or "").strip()
        return f"{name} <{address}>" if name and address else address or name
    if isinstance(value, list):
        return ", ".join(_address(item) for item in value)
    return str(value or "")


def parse_message(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text") or payload.get("intro") or "").replace("\x00", "").strip()
    html_value = payload.get("html") or ""
    if isinstance(html_value, list):
        html_value = "\n".join(str(item) for item in html_value)
    safe_html = bleach.clean(
        str(html_value),
        tags=["p", "br", "b", "strong", "i", "em", "u", "blockquote", "pre", "code", "a", "ul", "ol", "li"],
        attributes={"a": ["href", "title"]},
        protocols=["http", "https", "mailto"],
        strip=True,
    )
    received_at = payload.get("createdAt") or payload.get("updatedAt")
    parsed_date: datetime | None = None
    if received_at:
        try:
            parsed_date = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        except ValueError:
            parsed_date = None
    attachments = []
    for item in payload.get("attachments") or []:
        if isinstance(item, dict):
            attachments.append(
                {
                    "filename": str(item.get("filename") or "attachment"),
                    "size": int(item.get("size") or 0),
                    "content_type": str(item.get("contentType") or "application/octet-stream"),
                }
            )
    return {
        "mailtm_message_id": str(payload.get("id") or ""),
        "sender": _address(payload.get("from")),
        "recipient": _address(payload.get("to")),
        "subject": str(payload.get("subject") or "(no subject)")[:500],
        "text_content": text[:100_000],
        "safe_html": safe_html[:200_000],
        "received_at": parsed_date,
        "has_attachments": bool(attachments or payload.get("hasAttachments")),
        "attachments_json": json.dumps(attachments),
    }


def event_message_id(event: dict[str, Any]) -> str | None:
    for key in ("id", "message_id", "messageId", "@id"):
        value = event.get(key)
        if value:
            return str(value).rsplit("/", 1)[-1]
    raw = event.get("data")
    if isinstance(raw, dict):
        return event_message_id(raw)
    if isinstance(raw, str):
        match = re.search(r"(?:messages/)?([a-f0-9-]{8,})", raw, flags=re.I)
        if match:
            return match.group(1)
    return None
