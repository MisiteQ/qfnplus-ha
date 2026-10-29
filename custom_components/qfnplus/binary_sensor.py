"""Binary sensor platform for the Q飞牛+ integration.

Disk health as a problem binary sensor (on = attention required). Health
values come from disks_detail[].health with overrides from the throttled
/api/disk_health grading cache; Chinese grading values are mapped to
on/off/unknown and never exposed raw. Also: a docker aggregate problem
binary sensor (unhealthy / dead / non-zero exit containers).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import HEALTH_BAD_VALUES, HEALTH_OK_VALUES
from .coordinator import FnOsDataUpdateCoordinator
from .entity import (
    QFnOsCoordinatorEntity,
    disk_device_info,
    entity_id_hint,
    main_device_info,
    ov,
    part,
    slug,
)

PLATFORM_DOMAIN = "binary_sensor"


def _map_health(value: Any) -> bool | None:
    """Map fnmonitor health/grade strings to on(True)/off(False)/unknown(None)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"unknown", "未知"}:
        return None
    if text in {v.lower() for v in HEALTH_OK_VALUES}:
        return False
    if text in {v.lower() for v in HEALTH_BAD_VALUES}:
        return True
    return None


def _disk_health_getter(disk_name: str) -> Callable[[Mapping[str, Any]], bool | None]:
    def getter(data: Mapping[str, Any]) -> bool | None:
        # 巡检分级缓存（/api/disk_health items[dev].grade）优先
        items = part(data, "disk_health").get("items") or {}
        item = items.get(disk_name)
        if isinstance(item, dict):
            mapped = _map_health(item.get("grade"))
            if mapped is not None:
                return mapped
        # 回退 overview.disks_detail[].health
        for detail in ov(data).get("disks_detail") or []:
            if detail.get("name") == disk_name:
                return _map_health(detail.get("health"))
        return None

    return getter


@dataclass(frozen=True, kw_only=True)
class QFnOsBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with an is_on extractor."""

    is_on: Callable[[Mapping[str, Any]], bool | None]


class QFnOsBinarySensor(QFnOsCoordinatorEntity, BinarySensorEntity):
    """Generic binary sensor backed by a description is_on getter."""

    def __init__(
        self,
        coordinator: FnOsDataUpdateCoordinator,
        description: QFnOsBinarySensorDescription,
        unique_id: str,
        device_info: DeviceInfo | None = None,
        placeholders: dict[str, str] | None = None,
        object_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, unique_id, device_info, object_id)
        self.entity_description = description
        self._attr_translation_placeholders = placeholders or {}

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on(self.coordinator.data or {})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up disk health + docker problem binary sensors (dynamic)."""
    coordinator: FnOsDataUpdateCoordinator = entry.runtime_data
    known_disks: set[str] = set()
    known_docker = False
    # 动态实体记账：拔盘/换盘（serial 变化）/Docker 不可用后清理（M-2b）
    created_disks: dict[str, list[Any]] = {}
    created_docker: list[Any] = []
    disk_serials: dict[str, str] = {}

    @callback
    def async_add_new_entities() -> None:
        # known_disks 有增强赋值（-=），必须 nonlocal
        nonlocal known_docker, created_docker, known_disks
        overview = ov(coordinator.data)
        if not overview:
            return
        registry = er.async_get(coordinator.hass)
        devices: list[QFnOsBinarySensor] = []

        # --- cleanup pass（先清理后新增） -----------------------------------
        current_disks: dict[str, str] = {}
        for disk in overview.get("disks_detail") or []:
            if isinstance(disk, dict) and disk.get("name"):
                current_disks[str(disk.get("name"))] = str(disk.get("serial") or "")
        # 拆分两类过期：盘已拔除（gone）/ 盘还在但 serial 变了（换盘）
        stale_gone = {name for name in known_disks if name not in current_disks}
        stale_serial = {
            name
            for name in known_disks
            if name in current_disks
            and name in disk_serials
            and disk_serials[name] != current_disks[name]
        }
        # 换盘（stale_serial）的实体一并移除并弹出记账，下方 additions 会在
        # 同一轮以新 serial 重建：registry.async_remove 同步派发注册表事件，
        # 旧实体对象的自删任务先于新实体注册任务入队执行，entity_id
        # 复用不存在竞态（HA 2024.6+ listener 仅在数据变化时触发，
        # 不能依赖「下一轮」补建）
        for gone in stale_gone | stale_serial:
            for entity in created_disks.pop(gone, []):
                if entity.entity_id:
                    registry.async_remove(entity.entity_id)
        known_disks -= stale_gone | stale_serial
        disk_serials.update(current_disks)

        docker = overview.get("docker") or {}
        if known_docker and not docker.get("available"):
            for entity in created_docker:
                if entity.entity_id:
                    registry.async_remove(entity.entity_id)
            created_docker = []
            known_docker = False

        # 每块物理盘一个「健康」problem 实体（子设备）
        for disk in overview.get("disks_detail") or []:
            name = str(disk.get("name") or "")
            if not name or name in known_disks:
                continue
            known_disks.add(name)
            entity = QFnOsBinarySensor(
                coordinator,
                QFnOsBinarySensorDescription(
                    key="disk_health",
                    translation_key="disk_health",
                    device_class=BinarySensorDeviceClass.PROBLEM,
                    is_on=_disk_health_getter(name),
                ),
                f"{entry.entry_id}_disk_{name}_health",
                disk_device_info(entry, disk),
                {"disk": name},
                entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_disk_{name}_health"),
            )
            devices.append(entity)
            created_disks.setdefault(name, []).append(entity)

        # Docker 聚合 problem：仅 docker.available 时创建（异常明细属性
        # 挂在 sensor 平台的 docker_* 计数实体上，这里只负责 on/off）
        if not known_docker and docker.get("available"):
            known_docker = True

            def _docker_problem(data: Mapping[str, Any]) -> bool | None:
                from .sensor import _docker_abnormal, _docker_containers

                containers = _docker_containers(data)
                if containers is None:
                    return None
                return len(_docker_abnormal(containers)) > 0

            entity = QFnOsBinarySensor(
                coordinator,
                QFnOsBinarySensorDescription(
                    key="docker_problem",
                    translation_key="docker_problem",
                    device_class=BinarySensorDeviceClass.PROBLEM,
                    icon="mdi:docker",
                    is_on=_docker_problem,
                ),
                f"{entry.entry_id}_docker_problem",
                # 挂主设备，与 sensor 平台的 docker_* 计数实体归组一致（L-4）
                main_device_info(entry, overview),
                None,
                entity_id_hint(PLATFORM_DOMAIN, "qfnplus_docker_problem"),
            )
            devices.append(entity)
            created_docker.append(entity)

        if devices:
            async_add_entities(devices)

    async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_entities))
