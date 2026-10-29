"""Tests for the fnmonitor async client (login + 401 single-flight relogin)."""
from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest

from custom_components.qfnplus.api import (
    FnOsAuthError,
    FnOsClient,
    FnOsConnectError,
    FnOsLoginLocked,
)

from .conftest import OVERVIEW_MINIMAL


class FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload or {}

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """Minimal aiohttp.ClientSession stand-in (no real network)."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.get_calls: list[str] = []
        self.post_calls: list[str] = []

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        self.post_calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(session: FakeSession, password: str = "secret") -> FnOsClient:
    return FnOsClient(session, host="192.168.1.10", port=8777, password=password)


async def test_login_success_then_overview(overview_fixture):
    session = FakeSession(
        [
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, overview_fixture),
        ]
    )
    client = make_client(session)
    assert await client.async_test_connection() == overview_fixture
    assert len(session.post_calls) == 1
    assert session.post_calls[0].endswith("/api/auth/password")
    assert len(session.get_calls) == 1
    assert session.get_calls[0].endswith("/api/overview")


async def test_login_wrong_password_http_200():
    # fnmonitor 登录失败也返回 HTTP 200，必须解析 JSON 的 ok 字段
    session = FakeSession([FakeResponse(200, {"ok": False, "error": "口令错误"})])
    client = make_client(session)
    with pytest.raises(FnOsAuthError):
        await client.async_login()


async def test_login_rate_limited_429():
    session = FakeSession(
        [FakeResponse(429, {"ok": False, "error": "尝试次数过多", "retry_after": 60})]
    )
    client = make_client(session)
    with pytest.raises(FnOsLoginLocked) as excinfo:
        await client.async_login()
    assert excinfo.value.retry_after == 60


async def test_login_auth_disabled_no_password():
    # 面板未开启口令时登录返回 ok=false + 未配置，客户端应跳过登录直连
    session = FakeSession(
        [
            FakeResponse(200, {"ok": False, "error": "未配置登录口令"}),
            FakeResponse(200, OVERVIEW_MINIMAL),
        ]
    )
    client = make_client(session, password="whatever")
    assert await client.async_test_connection() == OVERVIEW_MINIMAL


async def test_connect_error_wrapped():
    class ErrSession(FakeSession):
        def get(self, url, **kwargs):
            self.get_calls.append(url)
            raise aiohttp.ClientConnectionError("refused")

    session = ErrSession([])
    client = make_client(session, password="")
    with pytest.raises(FnOsConnectError):
        await client.async_get_overview()


async def test_401_relogin_single_flight_retry_once(overview_fixture):
    # 会话失效（过期/登出/改口令）：401 -> 用已存口令重登一次 -> 重试成功
    session = FakeSession(
        [
            FakeResponse(401, {"ok": False, "error": "未登录", "auth_required": True}),
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, overview_fixture),
        ]
    )
    client = make_client(session)
    assert await client.async_get_overview() == overview_fixture
    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 2


async def test_401_still_unauthorized_after_relogin():
    # 重登后仍 401 -> FnOsAuthError（coordinator 转 ConfigEntryAuthFailed 触发 reauth）
    session = FakeSession(
        [
            FakeResponse(401, {"ok": False, "error": "未登录", "auth_required": True}),
            FakeResponse(200, {"ok": True}),
            FakeResponse(401, {"ok": False, "error": "未登录", "auth_required": True}),
        ]
    )
    client = make_client(session)
    with pytest.raises(FnOsAuthError):
        await client.async_get_overview()
    # 只重登一次、只重试一次
    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 2


async def test_concurrent_401s_share_one_relogin():
    # 并发请求同时 401 时应单飞：只真正登录一次（宽限窗口内复用）
    session = FakeSession([FakeResponse(200, {"ok": True})])
    client = make_client(session)

    import asyncio

    results = await asyncio.gather(
        client._async_relogin(),
        client._async_relogin(),
    )
    assert results == [None, None]
    assert len(session.post_calls) == 1


async def test_http_500_is_connect_error():
    session = FakeSession([FakeResponse(503, {"ok": False, "error": "busy"})])
    client = make_client(session, password="")
    with pytest.raises(FnOsConnectError):
        await client.async_get_overview()


async def test_password_never_in_error_messages(overview_fixture):
    # 红线：任何异常消息都不得包含口令
    session = FakeSession([FakeResponse(200, {"ok": False, "error": "口令错误"})])
    client = make_client(session, password="SUPER-SECRET")
    with pytest.raises(FnOsAuthError) as excinfo:
        await client.async_test_connection()
    assert "SUPER-SECRET" not in str(excinfo.value)


async def test_empty_password_skips_login(overview_fixture):
    with patch.object(
        FnOsClient, "async_login", side_effect=AssertionError("must not login")
    ):
        session = FakeSession([FakeResponse(200, overview_fixture)])
        client = make_client(session, password="")
        assert await client.async_test_connection() == overview_fixture
        assert session.post_calls == []


async def test_login_200_empty_body_is_connect_error():
    # L-2 回归：200 + 空/非 JSON body（host 误指其它 HTTP 服务）是
    # 协议错误而非「口令错误」，配置流应提示 cannot_connect
    session = FakeSession([FakeResponse(200, {})])
    client = make_client(session)
    with pytest.raises(FnOsConnectError):
        await client.async_login()
