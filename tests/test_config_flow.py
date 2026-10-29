"""Tests for the Q飞牛+ config flow (user / reauth / options)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType

from custom_components.qfnplus.api import FnOsAuthError, FnOsConnectError, FnOsLoginLocked
from custom_components.qfnplus.const import (
    CONF_SCAN_INTERVAL,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_INVALID_HOST,
    ERROR_LOGIN_LOCKED,
    ERROR_UNKNOWN,
)

USER_INPUT = {CONF_HOST: "192.168.1.10", CONF_PORT: 8777, CONF_PASSWORD: "secret"}

from .conftest import make_entry  # noqa: E402


def _patch_validate(return_value=None, side_effect=None):
    """Patch the validation used by the config flow."""
    return patch(
        "custom_components.qfnplus.config_flow.async_validate_input",
        new_callable=AsyncMock,
        return_value=return_value,
        side_effect=side_effect,
    )


async def test_user_form_shown(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    fields = result["data_schema"].schema
    keys = {str(key) for key in fields}
    assert {CONF_HOST, CONF_PORT, CONF_PASSWORD} <= keys
    # 明文 HTTP only：不允许出现 ssl 相关字段
    assert not any("ssl" in key for key in keys)


async def test_user_flow_success(hass, mock_setup_entry, overview_fixture):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_validate(return_value=overview_fixture) as mock_validate:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # 标题取 overview 内嵌 system.hostname
    assert result["title"] == "fnos"
    assert result["data"] == USER_INPUT
    mock_validate.assert_awaited_once()


async def test_user_flow_invalid_auth(hass, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_validate(side_effect=FnOsAuthError("口令错误")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_INVALID_AUTH}


async def test_user_flow_cannot_connect(hass, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_validate(side_effect=FnOsConnectError("timeout")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_CANNOT_CONNECT}


async def test_user_flow_login_locked(hass, mock_setup_entry):
    # 429 + retry_after：使用独立的 login_locked 错误键
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_validate(side_effect=FnOsLoginLocked(60)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_LOGIN_LOCKED}


async def test_user_flow_unknown_error(hass, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_validate(side_effect=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_UNKNOWN}


async def test_duplicate_host_port_aborts(hass, mock_setup_entry):
    # fnmonitor 无稳定机器标识：重复添加按 host:port 比对防重入
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_validate(return_value={}):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_different_port_allowed(hass, mock_setup_entry, overview_fixture):
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    other = {**USER_INPUT, CONF_PORT: 8778}
    with _patch_validate(return_value=overview_fixture):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], other)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 8778


async def test_reauth_flow_updates_password(hass, mock_setup_entry, overview_fixture):
    entry = make_entry(password="old-password")
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    with _patch_validate(return_value=overview_fixture):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_flow_rejected_password(hass, mock_setup_entry):
    entry = make_entry(password="old-password")
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    with _patch_validate(side_effect=FnOsAuthError("口令错误")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_INVALID_AUTH}
    assert entry.data[CONF_PASSWORD] == "old-password"


async def test_options_flow_scan_interval(hass, mock_setup_entry):
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    # SelectSelector 档位以字符串提交（前端约定），存储转回 int 秒
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: "120"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 120


async def test_options_flow_offers_all_interval_presets(hass, mock_setup_entry):
    # 档位下拉必须恰好是 30/60/120/300（selector 内部为字符串表示）
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"].schema
    marker = next(k for k in schema if getattr(k, "schema", None) == CONF_SCAN_INTERVAL)
    selector = schema[marker]  # marker 的值校验器即 SelectSelector
    assert sorted(int(o) for o in selector.config["options"]) == [30, 60, 120, 300]


async def test_reauth_flow_aborts_when_entry_gone(hass, mock_setup_entry):
    # L-6 回归：reauth 发起时条目已被删除 -> 优雅 abort 而非 assert 崩溃
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": "gone"},
        data=USER_INPUT,
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_entry_gone"


async def test_validate_input_closes_session(hass):
    # L-7 回归：配置流校验结束（无论成败）都显式关闭 session，不再堆积
    from custom_components.qfnplus.config_flow import async_validate_input

    session = MagicMock()
    session.close = AsyncMock()
    # 校验过程必然失败（post 直接拒绝），finally 必须关闭 session
    session.post.side_effect = aiohttp.ClientConnectionError("refused")

    with patch(
        "aiohttp.ClientSession", return_value=session
    ):
        with pytest.raises(FnOsConnectError):
            await async_validate_input(hass, USER_INPUT)
    session.close.assert_awaited_once()


async def test_user_flow_rejects_invalid_host(hass, mock_setup_entry):
    # L-8 回归：URL 前缀 / 尾斜杠 / 空白的 host 直接 invalid_host，不发请求
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    for bad_host in ("http://192.168.1.10", "192.168.1.10/", "1.1.1.1 x"):
        with _patch_validate() as mock_validate:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {**USER_INPUT, CONF_HOST: bad_host}
            )
            await hass.async_block_till_done()
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_INVALID_HOST}
        mock_validate.assert_not_awaited()
