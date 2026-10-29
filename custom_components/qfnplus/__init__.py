"""The Q飞牛+ (fnOS monitor) integration.

Read-only monitoring sensors for a fnOS NAS running fnmonitor v2.20.0.
Connection: LAN IP + optional password login (cookie session), REST
polling of /api/overview (60s default; low-frequency endpoints throttled).
"""
from __future__ import annotations

from datetime import timedelta

import aiohttp
from aiohttp import ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import FnOsClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS, REQUEST_TIMEOUT
from .coordinator import FnOsDataUpdateCoordinator

# fnmonitor 无稳定机器标识（无序列号/machine-id，hostname 用户可改），
# 按官方"最后手段"规则以 config entry id 作为实体 unique_id 前缀


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (polling interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Q飞牛+ from a config entry."""
    # 专属会话 + 独立 CookieJar：绝不复用共享 session，避免会话 cookie 串扰。
    # unsafe=True 必需：fnmonitor 以局域网 IP 访问，aiohttp 默认 CookieJar
    # 会静默丢弃来自 IP 主机的 Set-Cookie（fnmp_sid），导致登录后
    # /api/overview 仍然 401、被误报为「密码错误」。
    # auto_cleanup 默认 True：HA 停止时自动关闭，不要自己 close()
    session = async_create_clientsession(
        hass,
        timeout=ClientTimeout(total=REQUEST_TIMEOUT),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    client = FnOsClient(
        session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        password=entry.data.get(CONF_PASSWORD, ""),
    )
    base_interval = timedelta(
        seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    coordinator = FnOsDataUpdateCoordinator(hass, entry, client, base_interval)
    # 首次刷新失败：UpdateFailed -> ConfigEntryNotReady 自动退避重试；
    # FnOsAuthError -> ConfigEntryAuthFailed 触发 reauth
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # options（轮询间隔）变更 -> 自动重载条目，新间隔与实体立即生效
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
