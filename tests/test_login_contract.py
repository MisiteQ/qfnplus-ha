"""登录契约回归测试（0.1.1 修复：IP 主机 cookie 丢失导致「密码错误」误报）。

用真实 aiohttp 本地服务器（监听 127.0.0.1，同样是 IP 主机）按 fnmonitor
真实响应结构验证登录全链路，覆盖三条路径：错误口令、限速、成功登录。

协议形态来源标注：
- 实测定下（对 fnmonitor 测试实例 curl 实测）：
  * 错误口令 -> HTTP 200 {"ok": false, "error": "口令错误"}
  * 缺失 X-FNM-Request 头 / 非 JSON -> HTTP 403 csrf_rejected
  * GET /api/auth/config -> 200 {"auth_enabled":..,"requires_password":..,...}
- 源码推断（d:\\CODE\\fnmonitor\\app\\server.py，成功口令无法实测验证）：
  * 成功登录 -> HTTP 200 {"ok": true} +
    Set-Cookie: fnmp_sid=<64hex>; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax
  * 限速 -> HTTP 429 + {"ok": false, "error": "尝试次数过多...", "retry_after": <秒>}
    （server.py 在校验口令之前先查 limiter，429 与 Retry-After 头同发）

根因背景：aiohttp 默认 CookieJar 会静默丢弃来自 IP 地址主机的 Set-Cookie
（cookiejar.py update_cookies/filter_cookies 中 unsafe=False 分支）。
fnmonitor 以局域网 IP 访问，登录成功后 fnmp_sid 无法入 jar，
/api/overview 恒 401，任何口令都被误报为「密码错误」。
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.resolver import ThreadedResolver
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT


def _make_session(**kwargs) -> aiohttp.ClientSession:
    """Windows 测试环境：显式线程解析器，避开 aiodns/ProactorEventLoop 不兼容。"""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=ThreadedResolver()), **kwargs
    )

from custom_components.qfnplus import config_flow
from custom_components.qfnplus.api import (
    FnOsAuthError,
    FnOsClient,
    FnOsLoginLocked,
)

from .conftest import make_entry, patch_client

# 与 fnmonitor _create_session 一致：64 位 hex sid、7 天有效期
SID = secrets.token_hex(32)
GOOD_PASSWORD = "correct-horse-battery"
WRONG_PASSWORD = "definitely-wrong-test-pw"
RATE_LIMIT_PASSWORD = "rate-limited-marker"
SET_COOKIE_FMT = "fnmp_sid=%s; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"


def _mock_app() -> web.Application:
    """按 fnmonitor 真实响应结构模拟 /api/auth/password 与 /api/overview。"""

    async def login(request: web.Request) -> web.Response:
        # CSRF 闸门（实测：缺失 X-FNM-Request -> 403 csrf_rejected）
        if (
            request.headers.get("X-FNM-Request") != "1"
            or request.content_type != "application/json"
        ):
            return web.json_response({"ok": False, "error": "csrf_rejected"}, status=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        password = body.get("password")
        if password == GOOD_PASSWORD:
            resp = web.json_response({"ok": True})
            # 逐字节复刻 server.py _json() 的 Set-Cookie
            resp.headers["Set-Cookie"] = SET_COOKIE_FMT % SID
            return resp
        if password == RATE_LIMIT_PASSWORD:
            return web.json_response(
                {
                    "ok": False,
                    "error": "尝试次数过多，请 60 秒后再试",
                    "retry_after": 60,
                },
                status=429,
                headers={"Retry-After": "60"},
            )
        # 错误口令也是 HTTP 200（实测定案：判定必须看 JSON 的 ok 字段）
        return web.json_response({"ok": False, "error": "口令错误"})

    async def overview(request: web.Request) -> web.Response:
        if request.cookies.get("fnmp_sid") == SID:
            return web.json_response({"system": {"hostname": "mock-host"}})
        return web.json_response(
            {"error": "unauthorized", "auth_required": True}, status=401
        )

    app = web.Application()
    app.router.add_post("/api/auth/password", login)
    app.router.add_get("/api/overview", overview)
    return app


@pytest.fixture
async def mock_fnmonitor():
    """本地 fnmonitor 模拟服务（127.0.0.1 随机端口，即 IP 主机）。"""
    runner = web.AppRunner(_mock_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield "127.0.0.1", port
    await runner.cleanup()


@asynccontextmanager
async def _ip_safe_client(host: str, port: int, password: str):
    """按集成同款参数创建 client（unsafe jar，与 __init__/config_flow 一致）。"""
    jar = aiohttp.CookieJar(unsafe=True)
    session = _make_session(cookie_jar=jar)
    try:
        yield session, FnOsClient(session, host, port, password=password)
    finally:
        await session.close()


async def test_wrong_password_http_200_ok_false(mock_fnmonitor):
    """错误口令路径（实测定案）：HTTP 200 + ok:false -> FnOsAuthError。"""
    host, port = mock_fnmonitor
    async with _ip_safe_client(host, port, WRONG_PASSWORD) as (session, client):
        with pytest.raises(FnOsAuthError) as excinfo:
            await client.async_login()
        assert "口令错误" in str(excinfo.value)
        assert WRONG_PASSWORD not in str(excinfo.value)


async def test_missing_csrf_header_403(mock_fnmonitor):
    """缺失 X-FNM-Request 头（实测）：HTTP 403，客户端报连接/协议错误而非密码错误。"""
    host, port = mock_fnmonitor
    async with _make_session() as session:
        async with session.post(
            f"http://{host}:{port}/api/auth/password",
            json={"password": WRONG_PASSWORD},
            headers={"Content-Type": "application/json"},  # 故意缺 X-FNM-Request
        ) as resp:
            assert resp.status == 403
            payload = await resp.json()
            assert payload["error"] == "csrf_rejected"


async def test_rate_limited_429_with_retry_after(mock_fnmonitor):
    """限速路径（源码推断）：HTTP 429 + retry_after -> FnOsLoginLocked。"""
    host, port = mock_fnmonitor
    async with _ip_safe_client(host, port, RATE_LIMIT_PASSWORD) as (session, client):
        with pytest.raises(FnOsLoginLocked) as excinfo:
            await client.async_login()
        assert excinfo.value.retry_after == 60


async def test_successful_login_cookie_roundtrip(mock_fnmonitor):
    """成功登录路径（0.1.1 核心回归）：登录 -> cookie 入 jar -> overview 带 cookie。

    默认 CookieJar() 会丢弃 IP 主机的 Set-Cookie，导致登录后 overview
    恒 401、被误报为「密码错误」；必须用 unsafe=True jar 才能全链路通过。
    """
    host, port = mock_fnmonitor
    async with _ip_safe_client(host, port, GOOD_PASSWORD) as (session, client):
        assert await client.async_test_connection() == {"system": {"hostname": "mock-host"}}
        # 会话 cookie 已存储且值与 mock 发出的一致
        morsels = {m.key: m.value for m in session.cookie_jar}
        assert morsels.get("fnmp_sid") == SID


async def test_default_jar_drops_ip_host_cookie(mock_fnmonitor):
    """根因留证：默认 CookieJar 下登录成功但 cookie 被丢弃 -> overview 401。

    锁定本次 bug 的前提条件；若未来 aiohttp 改变默认行为导致此测试
    失败，unsafe=True 仍然无害，可按需移除本测试。
    """
    host, port = mock_fnmonitor
    async with _make_session() as session:  # 默认 unsafe=False
        client = FnOsClient(session, host, port, password=GOOD_PASSWORD)
        with pytest.raises(FnOsAuthError):
            await client.async_test_connection()
        assert list(session.cookie_jar) == []


async def test_setup_entry_uses_unsafe_cookie_jar(hass, overview_fixture):
    """集成入口创建的会话必须用 unsafe jar（真实跑 async_setup_entry 验证）。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    jar = entry.runtime_data.client._session.cookie_jar
    assert getattr(jar, "_unsafe", False) is True


async def test_config_flow_uses_unsafe_cookie_jar(hass, mock_setup_entry):
    """config flow 校验会话同样必须用 unsafe jar。"""
    with (
        patch("aiohttp.CookieJar", wraps=aiohttp.CookieJar) as jar_cls,
        patch.object(config_flow, "FnOsClient") as client_cls,
    ):
        client_cls.return_value.async_test_connection = AsyncMock(
            return_value={"system": {"hostname": "x"}}
        )
        await config_flow.async_validate_input(
            hass,
            {CONF_HOST: "127.0.0.1", CONF_PORT: 8777, CONF_PASSWORD: "whatever"},
        )
        assert jar_cls.call_args.kwargs.get("unsafe") is True
