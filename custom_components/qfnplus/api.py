"""Async client for the fnmonitor REST API (fnOS monitor app).

Only read-only GET endpoints are used plus the password login POST;
no POST action endpoints (docker/vm actions etc.) are ever called.

Authentication notes (fnmonitor v2.20.0):
- Login: POST /api/auth/password with Content-Type: application/json and
  header X-FNM-Request: 1, body {"password": "..."}. Both success and
  failure respond HTTP 200; the JSON body decides ("ok": true/false).
  Rate limited logins respond HTTP 429 with a retry_after field
  (5 failures within 10 minutes lock the source IP for 60s, doubling
  on repeat offences).
- An expired/invalid session yields HTTP 401 with auth_required: true.
  Sessions are fixed-term (default 7 days); they are only invalidated by
  expiry, logout or a password change.
- The session cookie (fnmp_sid) is issued over plain HTTP from a LAN IP.
  aiohttp's default CookieJar silently DROPS Set-Cookie responses from
  IP-address hosts; the session must therefore be created with
  CookieJar(unsafe=True) (see __init__.py / config_flow.py), otherwise
  login "succeeds" but every following GET is 401 and the password is
  wrongly reported as rejected.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_DISK_HEALTH,
    API_OVERVIEW,
    API_POWER_STATS,
    API_PROCESSES,
    API_TRAFFIC,
    LOGIN_PATH,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

# 会话刚重登过时的宽限窗口：并发请求同时收到 401 时只允许一次真正重登
_RELOGIN_GRACE = 5.0


class FnOsError(Exception):
    """Base error for the fnmonitor client."""


class FnOsConnectError(FnOsError):
    """Network / protocol level failure (timeout, HTTP error, bad JSON)."""


class FnOsAuthError(FnOsError):
    """The configured password was rejected (relogin still unauthorized)."""


class FnOsLoginLocked(FnOsError):
    """Login rate limited by the server (429 + retry_after)."""

    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(f"login rate limited, retry after {retry_after}s")


async def _safe_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """Parse a JSON body regardless of content type; {} on garbage."""
    try:
        payload = await resp.json(content_type=None)
    except (ValueError, aiohttp.ClientError):
        return {}
    return payload if isinstance(payload, dict) else {}


class FnOsClient:
    """Minimal async client for fnmonitor (plain HTTP, cookie session)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        password: str = "",
    ) -> None:
        self._session = session
        self._base_url = f"http://{host}:{port}"
        self._password = password
        self._relogin_lock = asyncio.Lock()
        self._last_login_ok = 0.0

    # -- login ---------------------------------------------------------------

    async def async_login(self) -> bool:
        """Login with the configured password.

        Returns True when a session cookie was issued, False when the
        server reports that no password protection is configured
        (auth disabled -> direct access works without login).
        Raises FnOsAuthError when the password is rejected and
        FnOsLoginLocked when the server rate limits login attempts.
        """
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.post(
                    f"{self._base_url}{LOGIN_PATH}",
                    json={"password": self._password},
                    headers={"Content-Type": "application/json", "X-FNM-Request": "1"},
                ) as resp:
                    payload = await _safe_json(resp)
                    status = resp.status
        except (TimeoutError, aiohttp.ClientError) as err:
            raise FnOsConnectError(
                f"login request failed: {err.__class__.__name__}"
            ) from err

        if status == 429:
            retry_after = payload.get("retry_after")
            try:
                retry_after = float(retry_after) if retry_after is not None else None
            except (TypeError, ValueError):
                retry_after = None
            raise FnOsLoginLocked(retry_after)
        if status != 200:
            raise FnOsConnectError(f"login returned HTTP {status}")
        # 200 但 body 非合法 JSON/为空：大概率指到了其它 HTTP 服务，
        # 属协议错误而非「密码错误」，明确归类为连接错误
        if not payload:
            raise FnOsConnectError("login returned an empty or non-JSON body")

        if payload.get("ok") is True:
            self._last_login_ok = time.monotonic()
            return True
        error = str(payload.get("error") or "")
        if "未配置" in error:
            # 面板未开启登录口令：无需会话，直接访问
            return False
        raise FnOsAuthError(error or "authentication rejected")

    # -- low-level GET with 401 single-flight relogin ------------------------

    async def _async_get(self, path: str, allow_retry: bool = True) -> Any:
        """GET a JSON endpoint; on 401 relogin once (single-flight) and retry."""
        unauthorized: dict[str, Any] = {}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(f"{self._base_url}{path}") as resp:
                    status = resp.status
                    payload = await _safe_json(resp)
                    if status == 401:
                        unauthorized = payload
        except FnOsError:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            raise FnOsConnectError(
                f"GET {path} failed: {err.__class__.__name__}"
            ) from err

        if status == 401:
            if not allow_retry:
                raise FnOsAuthError(str(unauthorized.get("error") or "unauthorized"))
            _LOGGER.debug("session invalid, relogin and retry %s", path)
            await self._async_relogin()
            return await self._async_get(path, allow_retry=False)

        if status != 200:
            detail = payload.get("error") if isinstance(payload, dict) else None
            raise FnOsConnectError(f"GET {path} returned HTTP {status}: {detail or ''}")
        return payload

    async def _async_relogin(self) -> None:
        """Re-login with the stored password; single-flight via asyncio.Lock.

        Concurrent 401s share one login: the winner performs the POST,
        the others reuse the fresh cookie jar thanks to the grace window.
        Raises FnOsAuthError when the stored password itself is rejected
        (coordinator turns this into ConfigEntryAuthFailed -> reauth).
        """
        async with self._relogin_lock:
            if time.monotonic() - self._last_login_ok < _RELOGIN_GRACE:
                return
            await self.async_login()

    # -- endpoints -----------------------------------------------------------

    async def async_get_overview(self) -> dict[str, Any]:
        """Full overview snapshot (main polling endpoint)."""
        return await self._async_get(API_OVERVIEW)

    async def async_get_traffic(self) -> dict[str, Any]:
        """Traffic statistics (realtime rates, totals)."""
        return await self._async_get(API_TRAFFIC)

    async def async_get_power_stats(self) -> dict[str, Any]:
        """Power statistics (realtime watts, kWh energy)."""
        return await self._async_get(API_POWER_STATS)

    async def async_get_disk_health(self) -> dict[str, Any]:
        """Disk health grading cache (read-only, no smartctl triggered)."""
        return await self._async_get(API_DISK_HEALTH)

    async def async_get_processes(self) -> dict[str, Any]:
        """Top processes list."""
        return await self._async_get(API_PROCESSES)

    # -- validation helper ---------------------------------------------------

    async def async_test_connection(self) -> dict[str, Any]:
        """Validate credentials + connectivity by reading /api/overview.

        When a password is configured, login first; when the password is
        empty (auth disabled on the panel) go straight to the overview.
        """
        if self._password:
            await self.async_login()
        return await self.async_get_overview()
