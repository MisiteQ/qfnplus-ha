"""Config flow for the Q飞牛+ integration (user / reauth / options)."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import homeassistant.helpers.config_validation as cv
from aiohttp import ClientTimeout
from aiohttp.resolver import ThreadedResolver

from .api import FnOsAuthError, FnOsClient, FnOsConnectError, FnOsLoginLocked
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_INVALID_HOST,
    ERROR_LOGIN_LOCKED,
    ERROR_UNKNOWN,
    INTERVAL_OPTIONS,
    REQUEST_TIMEOUT,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        # 面板未开启口令时可留空（直接访问，跳过登录）
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): str,
    }
)


async def async_validate_input(hass, data: dict[str, Any]) -> dict[str, Any]:
    """Test the connection: login (when password set) then read /api/overview.

    Returns the overview payload (used for the entry title).
    Raises the client exceptions on failure.
    """
    # 独立短生命周期 session（不用 async_create_clientsession：HA 管理的
    # session 不允许手动 close，而校验是短生命周期操作，必须用完即关，
    # 避免配置流反复重试时堆积 session）。
    # 独立 CookieJar；仅明文 HTTP（fnmonitor 无 TLS 能力，无 use_ssl 字段）。
    # unsafe=True 必需：fnmonitor 以局域网 IP 访问，aiohttp 默认 CookieJar
    # 会静默丢弃来自 IP 主机的 Set-Cookie（fnmp_sid），导致校验登录
    # 成功后 overview 401，被误报为「密码错误」。
    session = aiohttp.ClientSession(
        timeout=ClientTimeout(total=REQUEST_TIMEOUT),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        # 显式线程 resolver：fnmonitor 为 IP 直连不依赖 DNS；裸 ClientSession
        # 默认 resolver 在装了 aiodns 的 Windows（Proactor loop）上会直接抛错
        connector=aiohttp.TCPConnector(resolver=ThreadedResolver()),
    )
    try:
        client = FnOsClient(
            session,
            host=data[CONF_HOST],
            port=data[CONF_PORT],
            password=data.get(CONF_PASSWORD, ""),
        )
        return await client.async_test_connection()
    finally:
        # 校验是短生命周期操作：显式关闭会话，避免配置流反复重试时堆积
        await session.close()


def _validate_host(host: str) -> str | None:
    """Host 规范化与格式校验：返回清洗后的值，非法时 None。

    拒绝 URL 前缀（http://）、路径（/）、空白等会导致坏 URL 的输入。
    """
    host = str(host).strip()
    if not host:
        return None
    lowered = host.lower()
    if (
        "://" in lowered
        or "/" in host
        or any(ch.isspace() for ch in host)
        or host.startswith(".")
    ):
        return None
    return host


def _host_port_in_use(hass, user_input: dict[str, Any]) -> bool:
    """重复添加防重入：按 host:port 比对已有条目。"""
    host = str(user_input[CONF_HOST]).strip().lower()
    port = int(user_input[CONF_PORT])
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            str(entry.data.get(CONF_HOST, "")).strip().lower() == host
            and int(entry.data.get(CONF_PORT, 0)) == port
        ):
            return True
    return False


class QFnOsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Q飞牛+."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> QFnOsOptionsFlowHandler:
        """Get the options flow handler."""
        return QFnOsOptionsFlowHandler()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _validate_host(user_input[CONF_HOST])
            if host is None:
                errors["base"] = ERROR_INVALID_HOST
            else:
                user_input = {**user_input, CONF_HOST: host}
                if _host_port_in_use(self.hass, user_input):
                    return self.async_abort(reason="already_configured")
                try:
                    overview = await async_validate_input(self.hass, user_input)
                except FnOsLoginLocked:
                    # 服务端限速：5 次失败锁 60 秒起翻倍，文案提示等待后重试
                    errors["base"] = ERROR_LOGIN_LOCKED
                except FnOsAuthError:
                    errors["base"] = ERROR_INVALID_AUTH
                except (FnOsConnectError, TimeoutError, aiohttp.ClientError):
                    errors["base"] = ERROR_CANNOT_CONNECT
                except Exception:  # noqa: BLE001
                    errors["base"] = ERROR_UNKNOWN
                else:
                    hostname = str(
                        (overview.get("system") or {}).get("hostname") or ""
                    ).strip()
                    return self.async_create_entry(
                        title=hostname or DEFAULT_NAME, data=user_input
                    )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        """Start a reauth flow after ConfigEntryAuthFailed."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            # 发起 reauth 前条目已被删除（或 flow 迟到）
            return self.async_abort(reason="reauth_entry_gone")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the (new) password and store it."""
        errors: dict[str, str] = {}
        if self._reauth_entry is None:
            # 提交前条目被删除，优雅终止而不是 assert 崩溃
            return self.async_abort(reason="reauth_entry_gone")
        if user_input is not None:
            data = {**self._reauth_entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await async_validate_input(self.hass, data)
            except FnOsLoginLocked:
                errors["base"] = ERROR_LOGIN_LOCKED
            except FnOsAuthError:
                errors["base"] = ERROR_INVALID_AUTH
            except (FnOsConnectError, TimeoutError, aiohttp.ClientError):
                errors["base"] = ERROR_CANNOT_CONNECT
            except Exception:  # noqa: BLE001
                errors["base"] = ERROR_UNKNOWN
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry, data=data
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._reauth_entry.title},
        )


class QFnOsOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow: polling interval (30/60/120/300s dropdown)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            # SelectSelector 档位是字符串（HA 前端约定），存储时转回 int 秒
            return self.async_create_entry(
                title="",
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])},
            )
        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=str(current)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[str(v) for v in INTERVAL_OPTIONS],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
