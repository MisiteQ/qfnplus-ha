"""Diagnostics support for the Q飞牛+ integration.

The password is never included: entry data is redacted with
async_redact_data before being returned.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

TO_REDACT = {CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (password redacted)."""
    # setup 失败/未完成时 runtime_data 为 None，不能直接取属性
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return {
            "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
            "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
            "coordinator": None,
        }
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            # 2025.1 起该属性被移除，跨版本兜底
            "last_update_success_time": getattr(
                coordinator, "last_update_success_time", None
            ),
            "update_interval_seconds": coordinator.update_interval.total_seconds(),
            "consecutive_failures": getattr(coordinator, "_consecutive_failures", 0),
        },
    }
