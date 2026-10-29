"""Shared entity helpers for the Q飞牛+ integration."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER
from .coordinator import FnOsDataUpdateCoordinator


def ov(data: dict[str, Any] | None) -> dict[str, Any]:
    """Overview snapshot from coordinator data."""
    return (data or {}).get("overview") or {}


def part(data: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """A low-frequency section (traffic/power_stats/...) or {}."""
    section = (data or {}).get(key)
    return section if isinstance(section, dict) else {}


def slug(value: str) -> str:
    """Sanitize a dynamic key for unique ids."""
    return re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower()).strip("_")


def entity_id_hint(platform_domain: str, object_id: str) -> str:
    """Full entity_id hint used to pin the registry object_id.

    注册期 entity_platform 会把设备名拼进 suggested_object_id
    （"Q飞牛+ xxx" 经 slugify 会残留杂字符），通过 entity_id 提示
    可以拿到稳定的 `sensor.qfnplus_xxx` 形态。
    """
    return f"{platform_domain}.{object_id}"


def main_device_info(entry: ConfigEntry, overview: dict[str, Any]) -> DeviceInfo:
    """Main device: the fnOS host itself."""
    system = overview.get("system") or {}
    config = overview.get("config") or {}
    hardware = overview.get("hardware") or {}
    board = hardware.get("board") or {}
    sw_version = str(system.get("fnos_version") or config.get("version") or "") or None
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        # 用户决策：主设备显示名统一「Q飞牛+」（hostname 不再作设备名）
        name=DEFAULT_NAME,
        manufacturer=MANUFACTURER,
        model=str(board.get("board_name") or "fnOS"),
        sw_version=sw_version,
    )


def disk_device_info(entry: ConfigEntry, disk: dict[str, Any]) -> DeviceInfo:
    """Sub-device for a physical disk (via the main device)."""
    name = str(disk.get("name") or "unknown")
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_disk_{slug(disk.get('serial') or name)}")},
        name=name,
        manufacturer=str(disk.get("vendor")) if disk.get("vendor") else None,
        model=str(disk.get("model")) if disk.get("model") else None,
        serial_number=str(disk.get("serial")) if disk.get("serial") else None,
        via_device=(DOMAIN, entry.entry_id),
    )


class QFnOsCoordinatorEntity(CoordinatorEntity[FnOsDataUpdateCoordinator]):
    """Base class: has_entity_name + coordinator availability."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FnOsDataUpdateCoordinator,
        unique_id: str,
        device_info: DeviceInfo | None = None,
        object_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        # 稳定英文 object_id（如 qfnplus_cpu_usage）；friendly name 仍走
        # has_entity_name + 中文翻译（设备名 + 实体名）。
        # 注意 Entity.entity_id 是普通类属性（无 _attr_ 代理），必须直接赋值；
        # 注册期 entity_platform 会读取它作为 object_id 提示。
        if object_id:
            self.entity_id = object_id
