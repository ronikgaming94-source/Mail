from __future__ import annotations

import asyncio
import json
import logging
import secrets
import string
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class MailTmError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AsyncRateLimiter:
    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / max(per_second, 0.1)
        self.lock = asyncio.Lock()
        self.last_at = 0.0

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            sleep_for = self.interval - (now - self.last_at)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self.last_at = time.monotonic()


@dataclass(frozen=True)
class MailboxCredentials:
    account_id: str
    address: str
    password: str
    token: str


class MailTmClient:
    def __init__(self, base_url: str, hub_url: str, rate_per_second: float = 7.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.hub_url = hub_url
        self.rate_limiter = AsyncRateLimiter(rate_per_second)
        self.session: aiohttp.ClientSession | None = None
        self._domains: list[str] = []
        self._domains_at = 0.0

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25, connect=10, sock_read=25)
            self.session = aiohttp.ClientSession(timeout=timeout, headers={"Accept": "application/json"})

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method: str, path: str, *, token: str | None = None, json_body: dict | None = None, retries: int = 3) -> Any:
        await self.start()
        assert self.session is not None
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for attempt in range(retries):
            await self.rate_limiter.wait()
            try:
                async with self.session.request(
                    method, f"{self.base_url}{path}", headers=headers, json=json_body
                ) as response:
                    if response.status == 429:
                        delay = min(2**attempt, 8)
                        logger.warning("Mail.tm rate limit; retrying")
                        await asyncio.sleep(delay)
                        continue
                    if response.status >= 500:
                        if attempt < retries - 1:
                            await asyncio.sleep(min(2**attempt, 8))
                            continue
                    if response.status < 200 or response.status >= 300:
                        body = await response.text()
                        raise MailTmError(f"Mail.tm request failed ({response.status})", response.status)
                    if response.status == 204:
                        return {}
                    return await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == retries - 1:
                    raise MailTmError("Mail.tm is temporarily unreachable") from exc
                await asyncio.sleep(min(2**attempt, 8))
        raise MailTmError("Mail.tm request failed after retries")

    async def domains(self, force: bool = False) -> list[str]:
        if not force and self._domains and time.monotonic() - self._domains_at < 900:
            return list(self._domains)
        payload = await self._request("GET", "/domains?page=1")
        members = payload if isinstance(payload, list) else payload.get("hydra:member", [])
        domains = [str(row["domain"]) for row in members if row.get("isActive") and row.get("domain")]
        self._domains = domains
        self._domains_at = time.monotonic()
        return list(domains)

    async def create_account(self, reserved_addresses: set[str] | None = None) -> MailboxCredentials:
        domains = await self.domains()
        if not domains:
            raise MailTmError("No active Mail.tm domains are available")
        reserved = {address.casefold() for address in (reserved_addresses or set())}
        last_error: Exception | None = None
        for domain in domains[:3]:
            for _ in range(12):
                suffix = "".join(secrets.choice(string.digits) for _ in range(6))
                address = f"TempMailXpress{suffix}@{domain}"
                if address.casefold() in reserved:
                    continue
                password = secrets.token_urlsafe(24)
                try:
                    account = await self._request(
                        "POST", "/accounts", json_body={"address": address, "password": password}
                    )
                    account_id = str(account.get("id") or "")
                    if not account_id:
                        raise MailTmError("Mail.tm returned no account ID")
                    token_payload = await self._request(
                        "POST", "/token", json_body={"address": address, "password": password}
                    )
                    token = str(token_payload.get("token") or "")
                    if not token:
                        raise MailTmError("Mail.tm returned no account token")
                    return MailboxCredentials(account_id, address, password, token)
                except MailTmError as exc:
                    last_error = exc
                    if exc.status in {400, 404, 422}:
                        continue
                    raise
        raise MailTmError("Unable to create a Mail.tm mailbox") from last_error

    async def authenticate(self, address: str, password: str) -> str:
        payload = await self._request("POST", "/token", json_body={"address": address, "password": password})
        token = str(payload.get("token") or "")
        if not token:
            raise MailTmError("Mail.tm authentication returned no token")
        return token

    async def delete_account(self, account_id: str, token: str) -> None:
        await self._request("DELETE", f"/accounts/{account_id}", token=token)

    async def get_message(self, message_id: str, token: str) -> dict[str, Any]:
        return await self._request("GET", f"/messages/{message_id}", token=token)

    async def list_messages(self, token: str) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/messages?page=1", token=token)
        members = payload if isinstance(payload, list) else payload.get("hydra:member", [])
        return [item for item in members if isinstance(item, dict)]

    async def delete_message(self, message_id: str, token: str) -> None:
        await self._request("DELETE", f"/messages/{message_id}", token=token)

    async def sse_events(self, account_id: str, token: str) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        assert self.session is not None
        params = {"topic": f"/accounts/{account_id}"}
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
        async with self.session.get(self.hub_url, params=params, headers=headers) as response:
            if response.status != 200:
                raise MailTmError(f"Mail.tm event stream unavailable ({response.status})", response.status)
            event_name = ""
            data_lines: list[str] = []
            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        data = "\n".join(data_lines)
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError:
                            payload = {"id": data}
                        if event_name:
                            payload.setdefault("event", event_name)
                        yield payload
                    event_name, data_lines = "", []
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
