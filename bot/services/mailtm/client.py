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
    def __init__(
        self,
        base_url: str,
        hub_url: str,
        rate_per_second: float = 7.0,
        fallback_base_url: str = "https://api.mail.gw",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.hub_url = hub_url
        self.fallback_base_url = fallback_base_url.rstrip("/")
        self.rate_limiter = AsyncRateLimiter(rate_per_second)
        self.session: aiohttp.ClientSession | None = None
        self._domains_by_base: dict[str, list[str]] = {}
        self._domains_at: dict[str, float] = {}
        self._domain_sources: dict[str, str] = {}

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25, connect=10, sock_read=25)
            self.session = aiohttp.ClientSession(timeout=timeout, headers={"Accept": "application/json"})

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict | None = None,
        retries: int = 5,
        base_url: str | None = None,
    ) -> Any:
        await self.start()
        assert self.session is not None
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request_base = (base_url or self.base_url).rstrip("/")
        last_status: int | None = None
        for attempt in range(retries):
            await self.rate_limiter.wait()
            try:
                async with self.session.request(
                    method, f"{request_base}{path}", headers=headers, json=json_body
                ) as response:
                    last_status = response.status
                    if response.status == 429:
                        if attempt == retries - 1:
                            raise MailTmError("Mail service rate limit reached", response.status)
                        delay = min(2**attempt, 30)
                        logger.warning("Mail service rate limit; retrying in %ss", delay)
                        await asyncio.sleep(delay)
                        continue
                    if response.status >= 500:
                        if attempt < retries - 1:
                            await asyncio.sleep(min(2**attempt, 8))
                            continue
                    if response.status < 200 or response.status >= 300:
                        await response.text()
                        raise MailTmError(f"Mail.tm request failed ({response.status})", response.status)
                    if response.status == 204:
                        return {}
                    return await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == retries - 1:
                    raise MailTmError("Mail.tm is temporarily unreachable") from exc
                await asyncio.sleep(min(2**attempt, 8))
        raise MailTmError("Mail.tm request failed after retries", last_status)

    async def _domains_for_base(self, base_url: str, force: bool = False) -> list[str]:
        cached = self._domains_by_base.get(base_url, [])
        cached_at = self._domains_at.get(base_url, 0.0)
        if not force and cached and time.monotonic() - cached_at < 900:
            return list(cached)
        payload = await self._request("GET", "/domains?page=1", base_url=base_url)
        members = payload if isinstance(payload, list) else payload.get("hydra:member", [])
        domains = [str(row["domain"]) for row in members if row.get("isActive") and row.get("domain")]
        self._domains_by_base[base_url] = domains
        self._domains_at[base_url] = time.monotonic()
        for domain in domains:
            self._domain_sources[domain.casefold()] = base_url
        return list(domains)

    async def domains(self, force: bool = False) -> list[str]:
        all_domains: list[str] = []
        bases = dict.fromkeys((self.base_url, self.fallback_base_url))
        for base_url in bases:
            try:
                all_domains.extend(await self._domains_for_base(base_url, force=force))
            except MailTmError:
                logger.warning("Could not load domains from %s", base_url)
        return list(dict.fromkeys(all_domains))

    async def _base_for_address(self, address: str) -> str:
        domain = address.rsplit("@", 1)[-1].casefold()
        source = self._domain_sources.get(domain)
        if source:
            return source
        await self.domains()
        return self._domain_sources.get(domain, self.base_url)

    async def create_account(self, reserved_addresses: set[str] | None = None) -> MailboxCredentials:
        alphabet = string.ascii_lowercase + string.digits
        reserved = {address.casefold() for address in (reserved_addresses or set())}
        last_error: Exception | None = None
        providers = dict.fromkeys((self.base_url, self.fallback_base_url))
        for provider_base in providers:
            try:
                domains = await self._domains_for_base(provider_base)
            except MailTmError as exc:
                last_error = exc
                continue
            provider_rate_limited = False
            for domain in domains[:3]:
                for _ in range(12):
                    address = "".join(secrets.choice(alphabet) for _ in range(16)) + f"@{domain}"
                    if address.casefold() in reserved:
                        continue
                    password = secrets.token_urlsafe(24)
                    try:
                        account = await self._request(
                            "POST",
                            "/accounts",
                            json_body={"address": address, "password": password},
                            retries=1,
                            base_url=provider_base,
                        )
                        account_id = str(account.get("id") or "")
                        if not account_id:
                            raise MailTmError("Mail.tm returned no account ID")
                        provider_address = str(account.get("address") or address).casefold()
                        token_payload = await self._request(
                            "POST",
                            "/token",
                            json_body={"address": provider_address, "password": password},
                            base_url=provider_base,
                        )
                        token = str(token_payload.get("token") or "")
                        if not token:
                            raise MailTmError("Mail.tm returned no account token")
                        return MailboxCredentials(account_id, address, password, token)
                    except MailTmError as exc:
                        last_error = exc
                        if exc.status in {400, 404, 422}:
                            continue
                        if exc.status == 429:
                            provider_rate_limited = True
                            break
                        raise
                if provider_rate_limited:
                    break
        raise MailTmError("Unable to create a Mail.tm mailbox") from last_error

    async def authenticate(self, address: str, password: str) -> str:
        provider_base = await self._base_for_address(address)
        payload = await self._request(
            "POST",
            "/token",
            json_body={"address": address.casefold(), "password": password},
            base_url=provider_base,
        )
        token = str(payload.get("token") or "")
        if not token:
            raise MailTmError("Mail.tm authentication returned no token")
        return token

    async def delete_account(self, account_id: str, token: str, address: str | None = None) -> None:
        provider_base = await self._base_for_address(address) if address else self.base_url
        await self._request("DELETE", f"/accounts/{account_id}", token=token, base_url=provider_base)

    async def get_message(self, message_id: str, token: str, address: str | None = None) -> dict[str, Any]:
        provider_base = await self._base_for_address(address) if address else self.base_url
        return await self._request("GET", f"/messages/{message_id}", token=token, base_url=provider_base)

    async def list_messages(self, token: str, address: str | None = None) -> list[dict[str, Any]]:
        provider_base = await self._base_for_address(address) if address else self.base_url
        payload = await self._request("GET", "/messages?page=1", token=token, base_url=provider_base)
        members = payload if isinstance(payload, list) else payload.get("hydra:member", [])
        return [item for item in members if isinstance(item, dict)]

    async def delete_message(self, message_id: str, token: str, address: str | None = None) -> None:
        provider_base = await self._base_for_address(address) if address else self.base_url
        await self._request("DELETE", f"/messages/{message_id}", token=token, base_url=provider_base)

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
