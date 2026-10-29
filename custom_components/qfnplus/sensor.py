"""Sensor platform for the Q飞牛+ integration.

Static sensors on the main device + dynamic discovery (volumes, physical
disks, NICs, fans, GPUs, docker aggregates) driven by coordinator data via a
coordinator listener. Field names follow fnmonitor v2.20.0 /api/overview
exactly (cpu.percent, mem.used, disks_detail[].health, power.total,
system.boot_time, ...).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import LOGGER, VIRTUAL_IFACE_PREFIXES, VIRTUAL_IFACE_SUFFIXES
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

PLATFORM_DOMAIN = "sensor"


@dataclass(frozen=True, kw_only=True)
class QFnOsSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor over coordinator data."""

    getter: Callable[[Mapping[str, Any]], Any]
    extra_attrs: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None


# --- getters ----------------------------------------------------------------


def _get(data: Mapping[str, Any], *path: Any) -> Any:
    """Safely walk a nested path; None when missing."""
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _num(value: Any) -> int | float | None:
    """fnmonitor 的 SMART 字段可能是 str/int/None/''，统一成数值或 None。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def _load_value(data: Mapping[str, Any], index: int) -> Any:
    """cpu.load[i]，数组短于 3 时越界位返回 None（L-1）。"""
    load = _get(data, "overview", "cpu", "load") or []
    values = list(load) if isinstance(load, (list, tuple)) else []
    return values[index] if index < len(values) else None


def _memory_used(data):
    return _get(data, "overview", "mem", "used")


def _swap_used(data):
    # 无 Swap（swap_total=0）时保持 unknown 而不是 0
    if _get(data, "overview", "mem", "swap_total"):
        return _get(data, "overview", "mem", "swap_used")
    return None


def _swap_percent(data):
    if _get(data, "overview", "mem", "swap_total"):
        return _get(data, "overview", "mem", "swap_percent")
    return None


def _uptime(data):
    # overview 内嵌 system.boot_time（epoch 秒）；缺失时用 ts - uptime 兜底
    overview = ov(data)
    boot = (overview.get("system") or {}).get("boot_time")
    if not boot:
        ts, uptime = overview.get("ts"), overview.get("uptime")
        if ts and uptime is not None:
            boot = float(ts) - float(uptime)
        else:
            return None
    return datetime.fromtimestamp(float(boot), tz=dt_util.UTC)


def _power(data):
    power = ov(data).get("power") or {}
    if power.get("ok"):
        return power.get("total")
    return None


def _top_process(data):
    processes = part(data, "processes").get("processes") or []
    if processes:
        return str(processes[0].get("comm") or "") or None
    return None


def _top_process_attrs(data):
    processes = part(data, "processes").get("processes") or []
    if not processes:
        return {}
    top = processes[0]
    return {
        "cpu_percent": top.get("cpu"),
        "mem_percent": top.get("mem"),
        "rss_bytes": top.get("rss"),
    }


# --- static entity list (main device) ---------------------------------------

STATIC_SENSORS: tuple[QFnOsSensorDescription, ...] = (
    QFnOsSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=lambda data: _get(data, "overview", "cpu", "percent"),
    ),
    QFnOsSensorDescription(
        key="load_1",
        translation_key="load_1",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        getter=lambda data: _load_value(data, 0),
    ),
    QFnOsSensorDescription(
        key="load_5",
        translation_key="load_5",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        getter=lambda data: _load_value(data, 1),
    ),
    QFnOsSensorDescription(
        key="load_15",
        translation_key="load_15",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        getter=lambda data: _load_value(data, 2),
    ),
    QFnOsSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        getter=_memory_used,
    ),
    QFnOsSensorDescription(
        key="memory_total",
        translation_key="memory_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        getter=lambda data: _get(data, "overview", "mem", "total"),
    ),
    QFnOsSensorDescription(
        key="memory_percent",
        translation_key="memory_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=lambda data: _get(data, "overview", "mem", "percent"),
    ),
    QFnOsSensorDescription(
        key="swap_used",
        translation_key="swap_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        getter=_swap_used,
    ),
    QFnOsSensorDescription(
        key="swap_percent",
        translation_key="swap_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=_swap_percent,
    ),
    QFnOsSensorDescription(
        key="storage_usage",
        translation_key="storage_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=lambda data: _get(data, "overview", "disk_total"),
    ),
    QFnOsSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        getter=_uptime,
    ),
    QFnOsSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=lambda data: _get(data, "overview", "temp", "cpu"),
    ),
    QFnOsSensorDescription(
        key="board_temperature",
        translation_key="board_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=lambda data: _get(data, "overview", "temp", "system"),
    ),
    QFnOsSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        getter=_power,
    ),
    QFnOsSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        getter=lambda data: _get(data, "power_stats", "energy", "today_kwh"),
    ),
    QFnOsSensorDescription(
        key="energy_month",
        translation_key="energy_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        getter=lambda data: _get(data, "power_stats", "energy", "month_kwh"),
    ),
    QFnOsSensorDescription(
        key="net_down_rate",
        translation_key="net_down_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        getter=lambda data: _get(data, "traffic", "realtime", "rx_rate"),
    ),
    QFnOsSensorDescription(
        key="net_up_rate",
        translation_key="net_up_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        getter=lambda data: _get(data, "traffic", "realtime", "tx_rate"),
    ),
    QFnOsSensorDescription(
        key="top_process",
        translation_key="top_process",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        getter=_top_process,
        extra_attrs=_top_process_attrs,
    ),
)


class QFnOsSensor(QFnOsCoordinatorEntity, SensorEntity):
    """Generic sensor backed by a description value getter."""

    def __init__(
        self,
        coordinator: FnOsDataUpdateCoordinator,
        description: QFnOsSensorDescription,
        unique_id: str,
        device_info: DeviceInfo | None = None,
        placeholders: dict[str, str] | None = None,
        object_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, unique_id, device_info, object_id)
        self.entity_description = description
        self._attr_translation_placeholders = placeholders or {}

    @property
    def native_value(self) -> Any:
        return self.entity_description.getter(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.extra_attrs is None:
            return None
        return self.entity_description.extra_attrs(self.coordinator.data or {})


# --- dynamic entity getters (closures over instance keys) --------------------


def _disk_detail(data: Mapping[str, Any], disk_name: str) -> dict[str, Any] | None:
    for detail in ov(data).get("disks_detail") or []:
        if detail.get("name") == disk_name:
            return detail
    return None


def _disk_temperature_getter(disk_name: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        detail = _disk_detail(data, disk_name)
        if detail is not None and detail.get("temp") is not None:
            return detail.get("temp")
        for item in _get(data, "overview", "temp", "disks") or []:
            if item.get("name") == disk_name:
                return item.get("temp")
        return None

    return getter


def _disk_smart_getter(disk_name: str, field: str) -> Callable[[Mapping[str, Any]], Any]:
    """SMART 诊断计数：disks_detail[] 字段（NVMe/未采集时为 None -> unknown）。"""
    def getter(data: Mapping[str, Any]) -> Any:
        detail = _disk_detail(data, disk_name)
        if detail is None:
            return None
        return _num(detail.get(field))

    return getter


def _volume_getter(mount: str, field: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        for volume in ov(data).get("disks") or []:
            if volume.get("mount") == mount:
                return volume.get(field)
        return None

    return getter


def _nic_entry(data: Mapping[str, Any], iface: str) -> dict[str, Any] | None:
    for nic in ov(data).get("net") or []:
        if nic.get("iface") == iface:
            return nic
    return None


def _nic_rate_getter(iface: str, field: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        nic = _nic_entry(data, iface)
        return None if nic is None else nic.get(field)

    return getter


def _nic_sum_getter(iface: str, fields: tuple[str, ...]) -> Callable[[Mapping[str, Any]], Any]:
    """累计丢包/错误：rx+tx 两方向求和（单调累计，自启动起）。"""
    def getter(data: Mapping[str, Any]) -> Any:
        nic = _nic_entry(data, iface)
        if nic is None:
            return None
        total = 0
        for field in fields:
            value = nic.get(field)
            if value is None:
                continue
            total += _num(value) or 0
        return total

    return getter


def _fan_rpm_getter(fan_name: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        for fan in _fan_list(data):
            if fan.get("name") == fan_name:
                return fan.get("rpm")
        return None

    return getter


def _fan_list(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """sensors.fans 优先，回退 hwmon 直读的顶层 fans。"""
    overview = ov(data)
    fans = (overview.get("sensors") or {}).get("fans") or []
    if fans:
        return [f for f in fans if isinstance(f, dict)]
    return [f for f in overview.get("fans") or [] if isinstance(f, dict)]


def _physical_nics(overview: dict[str, Any]) -> list[dict[str, Any]]:
    """net[] 过滤 lo 与虚拟网卡（docker/veth/ovs 等）。"""
    result = []
    for nic in overview.get("net") or []:
        iface = str(nic.get("iface") or "")
        if not iface or iface == "lo":
            continue
        if iface.startswith(VIRTUAL_IFACE_PREFIXES):
            continue
        if iface.endswith(VIRTUAL_IFACE_SUFFIXES):
            continue
        result.append(nic)
    return result


# --- docker aggregate getters -------------------------------------------------
# 仅聚合层（运行中/已停止/异常计数 + problem 二进制）；预留以后按需展开
# 单容器实体（每容器 running/CPU%/内存%，须由用户显式 opt-in，防止实体膨胀）。

def _docker_containers(data: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """overview.docker 容器列表；Docker 不可用/未安装时返回 None（不建实体）。"""
    docker = ov(data).get("docker") or {}
    if not docker.get("available"):
        return None
    return [c for c in docker.get("containers") or [] if isinstance(c, dict)]


def _docker_abnormal(containers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """异常 = unhealthy / dead / restarting / 非零退出码的已退出容器。"""
    bad = []
    for c in containers:
        state = str(c.get("state") or "")
        health = str(c.get("health") or "")
        exit_code = c.get("exit_code")
        if health == "unhealthy" or state in ("dead", "restarting") or (
            state == "exited" and exit_code not in (None, 0)
        ):
            bad.append(c)
    return bad


def _docker_count_getter(kind: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        containers = _docker_containers(data)
        if containers is None:
            return None
        if kind == "running":
            return sum(1 for c in containers if c.get("state") == "running")
        if kind == "abnormal":
            return len(_docker_abnormal(containers))
        # stopped：非运行中且未计入异常（exited(0)/created/paused）
        bad = {id(c) for c in _docker_abnormal(containers)}
        return sum(
            1 for c in containers if c.get("state") != "running" and id(c) not in bad
        )

    return getter


def _docker_attrs(data: Mapping[str, Any]) -> dict[str, Any]:
    containers = _docker_containers(data)
    if containers is None:
        return {}
    bad = _docker_abnormal(containers)
    return {
        "total": len(containers),
        "abnormal": [str(c.get("name") or c.get("id") or "") for c in bad],
    }


# --- disk IO getters -----------------------------------------------------------

_BASE_DISK_RE = re.compile(r"^(sd[a-z]+|nvme\d+n\d+|vd[a-z]+)$")


def _disk_io_map(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(io.get("name")): io
        for io in ov(data).get("diskio") or []
        if isinstance(io, dict) and io.get("name")
    }


def _disk_io_getter(disk_name: str, field: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        entry = _disk_io_map(data).get(disk_name)
        return None if entry is None else entry.get(field)

    return getter


def _disk_io_attrs(disk_name: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """每盘 IOPS 与队列深度挂在读写速率实体的属性上（不单独建实体防膨胀）。"""
    def attrs(data: Mapping[str, Any]) -> dict[str, Any]:
        entry = _disk_io_map(data).get(disk_name)
        if not entry:
            return {}
        return {
            "read_iops": entry.get("read_iops"),
            "write_iops": entry.get("write_iops"),
            "in_progress": entry.get("in_progress"),
        }

    return attrs


def _disk_io_total_getter(field: str) -> Callable[[Mapping[str, Any]], Any]:
    """整机 IO：仅汇总物理盘（disks_detail 里出现的名字），避免分区/dm 重复计数。"""
    def getter(data: Mapping[str, Any]) -> Any:
        ios = _disk_io_map(data)
        if not ios:
            return None
        names = {
            str(d.get("name"))
            for d in ov(data).get("disks_detail") or []
            if d.get("name")
        }
        if not names:
            # disks_detail 未就绪时回退：仅基础盘名（排除 nvme0n1p1/dm-0 等）
            names = {name for name in ios if _BASE_DISK_RE.match(name)}
        total = 0.0
        hit = False
        for name, entry in ios.items():
            if name in names and entry.get(field) is not None:
                total += float(entry[field])
                hit = True
        return round(total, 1) if hit else None

    return getter


# --- GPU key helpers -------------------------------------------------------------
# 键规则：name 优先（保持稳定 ID），无名时用 pci 地址，再无名用索引；
# 同名多卡在发现阶段追加 _index 后缀并告警（L-3）

def _gpu_base_key(gpu: dict[str, Any], index: int) -> str:
    return (
        slug(str(gpu.get("name") or ""))
        or slug(str(gpu.get("pci") or ""))
        or f"gpu{index}"
    )


def _gpu_key_map(data: Mapping[str, Any]) -> dict[int, str]:
    """gpu[] 索引 -> 实体键；同名 GPU 的后续实例加 _index 后缀去重。"""
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for index, gpu in enumerate(ov(data).get("gpu") or []):
        if not isinstance(gpu, dict):
            continue
        base = _gpu_base_key(gpu, index)
        if base in used:
            LOGGER.warning(
                "duplicate GPU name %r; using key %s_%s for the %s-th card",
                gpu.get("name"), base, index, index,
            )
            mapping[index] = f"{base}_{index}"
        else:
            mapping[index] = base
        used.add(mapping[index])
    return mapping


def _gpu_getter(gkey: str, field: str) -> Callable[[Mapping[str, Any]], Any]:
    def getter(data: Mapping[str, Any]) -> Any:
        gpu_list = ov(data).get("gpu") or []
        for index, key in _gpu_key_map(data).items():
            if key == gkey:
                return gpu_list[index].get(field)
        return None

    return getter


# --- dynamic setup -----------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up sensors: static list + dynamic discovery on coordinator updates."""
    coordinator: FnOsDataUpdateCoordinator = entry.runtime_data
    known_volumes: set[str] = set()
    known_disks: set[str] = set()
    known_nics: set[str] = set()
    known_fans: set[str] = set()
    known_gpus: set[str] = set()
    known_docker = False
    known_io_total = False
    # 已创建的动态实体按「集合 -> 键」记账，用于拔盘/删卷/移网卡/GPU 消失后的清理
    created: dict[str, dict[str, list[Any]]] = {
        "vol": {},
        "disk": {},
        "nic": {},
        "gpu": {},
        "docker": {},
    }
    # 盘名 -> serial：检测同名换盘（设备身份需重建）
    disk_serials: dict[str, str] = {}

    @callback
    def async_add_new_entities() -> None:
        # known_disks 有增强赋值（-=），必须 nonlocal
        nonlocal known_docker, known_io_total, known_disks
        data = coordinator.data or {}
        overview = ov(data)
        if not overview:
            return
        registry = er.async_get(coordinator.hass)

        def prune(bucket_key: str, alive: set[str]) -> None:
            """移除不再存在（或身份已失效）的动态实体（M-2b）。"""
            bucket = created.setdefault(bucket_key, {})
            for gone in [k for k in bucket if k not in alive]:
                for entity in bucket.pop(gone):
                    if entity.entity_id:
                        registry.async_remove(entity.entity_id)

        # --- cleanup pass（先清理后新增） -----------------------------------
        current_mounts = {
            str(v.get("mount"))
            for v in overview.get("disks") or []
            if v.get("mount")
        }
        prune("vol", current_mounts)

        current_disks: dict[str, str] = {}
        for disk in overview.get("disks_detail") or []:
            if isinstance(disk, dict) and disk.get("name"):
                current_disks[str(disk.get("name"))] = str(disk.get("serial") or "")
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
        prune("disk", set(current_disks) - stale_serial)
        known_disks -= stale_gone | stale_serial
        disk_serials.update(current_disks)

        current_nics = {
            str(nic.get("iface")) for nic in _physical_nics(overview)
        }
        prune("nic", current_nics)

        prune("gpu", set(_gpu_key_map(data).values()))

        if known_docker and _docker_containers(data) is None:
            # Docker 应用被移除/不可用：撤下聚合实体，允许将来重新创建
            prune("docker", set())
            known_docker = False

        devices: list[QFnOsSensor] = []

        main_info = main_device_info(entry, overview)

        # volumes: overview.disks[] (mount 唯一)
        for volume in overview.get("disks") or []:
            mount = volume.get("mount")
            if not mount or mount in known_volumes:
                continue
            known_volumes.add(mount)
            key = slug(mount) or "root"
            placeholders = {"mount": str(mount)}
            vol_entities = [
                QFnOsSensor(
                    coordinator,
                    desc,
                    f"{entry.entry_id}_vol_{key}_{desc.key}",
                    main_info,
                    placeholders,
                    entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_volume_{key}_{desc.key}"),
                )
                for desc in (
                    QFnOsSensorDescription(
                        key="used",
                        translation_key="volume_used",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        native_unit_of_measurement=UnitOfInformation.BYTES,
                        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
                        suggested_display_precision=2,
                        state_class=SensorStateClass.MEASUREMENT,
                        getter=_volume_getter(mount, "used"),
                    ),
                    QFnOsSensorDescription(
                        key="total",
                        translation_key="volume_total",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        native_unit_of_measurement=UnitOfInformation.BYTES,
                        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
                        suggested_display_precision=2,
                        state_class=SensorStateClass.MEASUREMENT,
                        getter=_volume_getter(mount, "total"),
                    ),
                    QFnOsSensorDescription(
                        key="percent",
                        translation_key="volume_percent",
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=1,
                        getter=_volume_getter(mount, "percent"),
                    ),
                )
            ]
            devices.extend(vol_entities)
            created["vol"][mount] = vol_entities

        # physical disks: overview.disks_detail[]（每盘一个子设备）
        # 温度 + SMART 诊断 4 项 + 磁盘 IO 读写速率；IOPS 挂属性
        for disk in overview.get("disks_detail") or []:
            name = str(disk.get("name") or "")
            if not name or name in known_disks:
                continue
            known_disks.add(name)
            disk_info = disk_device_info(entry, disk)
            smart_specs = (
                QFnOsSensorDescription(
                    key="power_on_hours",
                    translation_key="disk_power_on_hours",
                    native_unit_of_measurement=UnitOfTime.HOURS,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_smart_getter(name, "power_on_hours"),
                ),
                QFnOsSensorDescription(
                    key="reallocated_sectors",
                    translation_key="disk_reallocated_sectors",
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_smart_getter(name, "reallocated_sectors"),
                ),
                QFnOsSensorDescription(
                    key="pending_sectors",
                    translation_key="disk_pending_sectors",
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_smart_getter(name, "pending_sectors"),
                ),
                QFnOsSensorDescription(
                    key="uncorrectable",
                    translation_key="disk_uncorrectable",
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_smart_getter(name, "uncorrectable"),
                ),
            )
            io_specs = (
                QFnOsSensorDescription(
                    key="read_rate",
                    translation_key="disk_read_rate",
                    device_class=SensorDeviceClass.DATA_RATE,
                    native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
                    suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_io_getter(name, "read_rate"),
                    extra_attrs=_disk_io_attrs(name),
                ),
                QFnOsSensorDescription(
                    key="write_rate",
                    translation_key="disk_write_rate",
                    device_class=SensorDeviceClass.DATA_RATE,
                    native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
                    suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_io_getter(name, "write_rate"),
                    extra_attrs=_disk_io_attrs(name),
                ),
            )
            disk_entities = [
                QFnOsSensor(
                    coordinator,
                    QFnOsSensorDescription(
                        key="disk_temperature",
                        translation_key="disk_temperature",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=1,
                        getter=_disk_temperature_getter(name),
                    ),
                    f"{entry.entry_id}_disk_{name}_temperature",
                    disk_info,
                    {"disk": name},
                    entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_disk_{name}_temperature"),
                )
            ]
            for desc in smart_specs:
                disk_entities.append(
                    QFnOsSensor(
                        coordinator,
                        desc,
                        f"{entry.entry_id}_disk_{name}_{desc.key}",
                        disk_info,
                        {"disk": name},
                        entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_disk_{name}_{desc.key}"),
                    )
                )
            for desc in io_specs:
                disk_entities.append(
                    QFnOsSensor(
                        coordinator,
                        desc,
                        f"{entry.entry_id}_disk_{name}_{desc.key}",
                        disk_info,
                        {"disk": name},
                        entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_disk_{name}_{desc.key}"),
                    )
                )
            devices.extend(disk_entities)
            created["disk"][name] = disk_entities

        # NICs: overview.net[] 过滤虚拟口（每口速率 + 累计字节 + 丢包/错误）
        for nic in _physical_nics(overview):
            iface = str(nic.get("iface"))
            if iface in known_nics:
                continue
            known_nics.add(iface)
            placeholders = {"iface": iface}
            specs = (
                QFnOsSensorDescription(
                    key="nic_rx_rate",
                    translation_key="nic_rx_rate",
                    device_class=SensorDeviceClass.DATA_RATE,
                    native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
                    suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.MEASUREMENT,
                    getter=_nic_rate_getter(iface, "rx_rate"),
                ),
                QFnOsSensorDescription(
                    key="nic_tx_rate",
                    translation_key="nic_tx_rate",
                    device_class=SensorDeviceClass.DATA_RATE,
                    native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
                    suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.MEASUREMENT,
                    getter=_nic_rate_getter(iface, "tx_rate"),
                ),
                QFnOsSensorDescription(
                    key="nic_total_rx",
                    translation_key="nic_total_rx",
                    device_class=SensorDeviceClass.DATA_SIZE,
                    native_unit_of_measurement=UnitOfInformation.BYTES,
                    suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    getter=_nic_rate_getter(iface, "rx_bytes"),
                ),
                QFnOsSensorDescription(
                    key="nic_total_tx",
                    translation_key="nic_total_tx",
                    device_class=SensorDeviceClass.DATA_SIZE,
                    native_unit_of_measurement=UnitOfInformation.BYTES,
                    suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    getter=_nic_rate_getter(iface, "tx_bytes"),
                ),
                QFnOsSensorDescription(
                    key="nic_drop",
                    translation_key="nic_drop",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_nic_sum_getter(iface, ("rx_drop", "tx_drop")),
                ),
                QFnOsSensorDescription(
                    key="nic_errs",
                    translation_key="nic_errs",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_nic_sum_getter(iface, ("rx_errs", "tx_errs")),
                ),
            )
            nic_entities = [
                QFnOsSensor(
                    coordinator,
                    desc,
                    f"{entry.entry_id}_nic_{slug(iface)}_{desc.key}",
                    main_info,
                    placeholders,
                    entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_nic_{slug(iface)}_{desc.key}"),
                )
                for desc in specs
            ]
            devices.extend(nic_entities)
            created["nic"][iface] = nic_entities

        # 整机磁盘 IO（物理盘聚合，一次创建）
        if not known_io_total and (overview.get("diskio") or overview.get("disks_detail")):
            known_io_total = True
            io_total_specs = (
                QFnOsSensorDescription(
                    key="disk_io_read_rate",
                    translation_key="disk_io_read_rate",
                    device_class=SensorDeviceClass.DATA_RATE,
                    native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
                    suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.MEASUREMENT,
                    getter=_disk_io_total_getter("read_rate"),
                ),
                QFnOsSensorDescription(
                    key="disk_io_write_rate",
                    translation_key="disk_io_write_rate",
                    device_class=SensorDeviceClass.DATA_RATE,
                    native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
                    suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                    suggested_display_precision=2,
                    state_class=SensorStateClass.MEASUREMENT,
                    getter=_disk_io_total_getter("write_rate"),
                ),
                QFnOsSensorDescription(
                    key="disk_io_read_iops",
                    translation_key="disk_io_read_iops",
                    state_class=SensorStateClass.MEASUREMENT,
                    suggested_display_precision=1,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_io_total_getter("read_iops"),
                ),
                QFnOsSensorDescription(
                    key="disk_io_write_iops",
                    translation_key="disk_io_write_iops",
                    state_class=SensorStateClass.MEASUREMENT,
                    suggested_display_precision=1,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    getter=_disk_io_total_getter("write_iops"),
                ),
            )
            devices.extend(
                QFnOsSensor(
                    coordinator,
                    desc,
                    f"{entry.entry_id}_{desc.key}",
                    main_info,
                    None,
                    entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_{desc.key}"),
                )
                for desc in io_total_specs
            )

        # GPU：overview.gpu[]（无 GPU 不建实体；util/freq 允许 null -> unknown）
        gpu_keys = _gpu_key_map(data)
        for index, gpu in enumerate(overview.get("gpu") or []):
            if not isinstance(gpu, dict) or index not in gpu_keys:
                continue
            gkey = gpu_keys[index]
            if gkey in known_gpus:
                continue
            known_gpus.add(gkey)
            gpu_specs = (
                QFnOsSensorDescription(
                    key="utilization",
                    translation_key="gpu_utilization",
                    native_unit_of_measurement=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                    suggested_display_precision=1,
                    icon="mdi:expansion-card",
                    getter=_gpu_getter(gkey, "util"),
                ),
                QFnOsSensorDescription(
                    key="frequency",
                    translation_key="gpu_frequency",
                    device_class=SensorDeviceClass.FREQUENCY,
                    native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
                    state_class=SensorStateClass.MEASUREMENT,
                    suggested_display_precision=0,
                    icon="mdi:expansion-card",
                    getter=_gpu_getter(gkey, "freq_mhz"),
                ),
            )
            gpu_entities = [
                QFnOsSensor(
                    coordinator,
                    desc,
                    f"{entry.entry_id}_gpu_{gkey}_{desc.key}",
                    main_info,
                    None,
                    entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_gpu_{gkey}_{desc.key}"),
                )
                for desc in gpu_specs
            ]
            devices.extend(gpu_entities)
            created["gpu"][gkey] = gpu_entities

        # Docker 聚合：仅 docker.available 时创建（不可用不建，避免空壳实体）
        if not known_docker and _docker_containers(data) is not None:
            known_docker = True
            docker_specs = (
                QFnOsSensorDescription(
                    key="docker_running",
                    translation_key="docker_running",
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:docker",
                    getter=_docker_count_getter("running"),
                    extra_attrs=_docker_attrs,
                ),
                QFnOsSensorDescription(
                    key="docker_stopped",
                    translation_key="docker_stopped",
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:docker",
                    getter=_docker_count_getter("stopped"),
                    extra_attrs=_docker_attrs,
                ),
                QFnOsSensorDescription(
                    key="docker_abnormal",
                    translation_key="docker_abnormal",
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:docker",
                    getter=_docker_count_getter("abnormal"),
                    extra_attrs=_docker_attrs,
                ),
            )
            docker_entities = [
                QFnOsSensor(
                    coordinator,
                    desc,
                    f"{entry.entry_id}_{desc.key}",
                    main_info,
                    None,
                    entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_{desc.key}"),
                )
                for desc in docker_specs
            ]
            devices.extend(docker_entities)
            created["docker"]["all"] = docker_entities

        # fans: sensors.fans[]（回退顶层 fans[]）
        prune(
            "fan",
            {str(f.get("name")) for f in _fan_list(data) if f.get("name")},
        )
        for fan in _fan_list(data):
            fan_name = str(fan.get("name") or "")
            if not fan_name or fan_name in known_fans:
                continue
            known_fans.add(fan_name)
            fan_entity = QFnOsSensor(
                coordinator,
                QFnOsSensorDescription(
                    key="fan_rpm",
                    translation_key="fan_rpm",
                    native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
                    state_class=SensorStateClass.MEASUREMENT,
                    getter=_fan_rpm_getter(fan_name),
                ),
                f"{entry.entry_id}_fan_{slug(fan_name)}",
                main_info,
                {"fan": fan_name},
                entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_fan_{slug(fan_name)}"),
            )
            devices.append(fan_entity)
            created.setdefault("fan", {}).setdefault(fan_name, []).append(fan_entity)

        if devices:
            async_add_entities(devices)

    # 静态实体一次建齐（主设备）
    first_overview = ov(coordinator.data)
    async_add_entities(
        QFnOsSensor(
            coordinator,
            desc,
            f"{entry.entry_id}_{desc.key}",
            main_device_info(entry, first_overview),
            None,
            entity_id_hint(PLATFORM_DOMAIN, f"qfnplus_{desc.key}"),
        )
        for desc in STATIC_SENSORS
    )

    async_add_new_entities()
    # 动态实体增量补充一律走 coordinator listener（2026.6 起 listener+reload 组合已弃用）
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_entities))
