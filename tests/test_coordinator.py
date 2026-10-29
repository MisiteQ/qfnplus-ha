"""Integration tests: coordinator polling, throttling, backoff, entities."""
from __future__ import annotations

import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.qfnplus.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.qfnplus.coordinator import FnOsDataUpdateCoordinator
from custom_components.qfnplus.api import FnOsAuthError, FnOsConnectError

from .conftest import (
    DISK_HEALTH_FIXTURE,
    entity_id_for,
    make_entry,
    patch_client,
)


async def test_setup_creates_static_and_dynamic_entities(hass, overview_fixture):
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator: FnOsDataUpdateCoordinator = entry.runtime_data
        assert coordinator.last_update_success
        assert coordinator.data["overview"]["cpu"]["percent"] == 12.3

        # 首轮应取齐 overview + 全部低频端点（top_process 默认禁用，仅查注册表）
        for suffix, platform in (
            ("cpu_usage", "sensor"),
            ("memory_used", "sensor"),
            ("swap_used", "sensor"),
            ("uptime", "sensor"),
            ("power", "sensor"),
            ("energy_today", "sensor"),
            ("net_down_rate", "sensor"),
            ("disk_sda_temperature", "sensor"),
            ("vol_vol1_used", "sensor"),
            ("nic_eth0_nic_rx_rate", "sensor"),
            ("fan_it8613", "sensor"),
            ("disk_sda_health", "binary_sensor"),
            ("docker_running", "sensor"),
            ("gpu_uhd_graphics_utilization", "sensor"),
            ("disk_io_read_rate", "sensor"),
            ("nic_eth0_nic_total_rx", "sensor"),
            ("disk_sda_power_on_hours", "sensor"),
        ):
            eid = entity_id_for(hass, entry, platform, suffix)
            assert eid, f"entity for {suffix} not registered"
            assert hass.states.get(eid) is not None, f"state missing for {eid}"

        # top_process 默认禁用（entity_registry_enabled_default=False）：
        # 有注册但无状态
        top_eid = entity_id_for(hass, entry, "sensor", "top_process")
        assert top_eid, "top_process entity not registered"
        assert hass.states.get(top_eid) is None, "disabled entity must have no state"

        # 首轮节流窗口全部到期 -> 5 个端点各 1 次
        assert mocks["async_get_overview"].await_count == 1
        assert mocks["async_get_traffic"].await_count == 1
        assert mocks["async_get_power_stats"].await_count == 1
        assert mocks["async_get_disk_health"].await_count == 1
        assert mocks["async_get_processes"].await_count == 1


async def test_sensor_values_follow_api_fields(hass, overview_fixture):
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # CPU 使用率（suggested_display_precision=1）
        eid = entity_id_for(hass, entry, "sensor", "cpu_usage")
        assert hass.states.get(eid).state == "12.3"

        # 内存已用：native bytes，data_size 建议展示 GiB
        eid = entity_id_for(hass, entry, "sensor", "memory_used")
        state = hass.states.get(eid)
        assert state.state not in ("unknown", "unavailable")
        assert float(state.state) > 0

        # 无 Swap（swap_total=0）-> unknown
        eid = entity_id_for(hass, entry, "sensor", "swap_used")
        assert hass.states.get(eid).state == "unknown"

        # 实时功耗 power.total（power.ok 时有效）
        eid = entity_id_for(hass, entry, "sensor", "power")
        assert float(hass.states.get(eid).state) == 18.5

        # 今日用电 kWh
        eid = entity_id_for(hass, entry, "sensor", "energy_today")
        assert float(hass.states.get(eid).state) == 0.321

        # 整机下行速率（traffic.realtime.rx_rate）
        eid = entity_id_for(hass, entry, "sensor", "net_down_rate")
        assert float(hass.states.get(eid).state) > 0

        # 磁盘温度
        eid = entity_id_for(hass, entry, "sensor", "disk_sda_temperature")
        assert float(hass.states.get(eid).state) == 38.0

        # 磁盘健康：grade 正常 -> off
        eid = entity_id_for(hass, entry, "binary_sensor", "disk_sda_health")
        assert hass.states.get(eid).state == "off"

        # 风扇转速
        eid = entity_id_for(hass, entry, "sensor", "fan_it8613")
        assert float(hass.states.get(eid).state) == 1640.0

        # 卷使用率
        eid = entity_id_for(hass, entry, "sensor", "vol_vol1_percent")
        assert float(hass.states.get(eid).state) == 40.0

        # 运行时长：timestamp 设备类（datetime 状态）
        eid = entity_id_for(hass, entry, "sensor", "uptime")
        assert hass.states.get(eid).attributes.get("device_class") == "timestamp"


async def test_health_bad_grade_turns_on(hass, overview_fixture):
    bad_health = {
        **DISK_HEALTH_FIXTURE,
        "items": {
            "sda": {**DISK_HEALTH_FIXTURE["items"]["sda"], "grade": "故障"}
        },
    }
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture, disk_health=bad_health):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        eid = entity_id_for(hass, entry, "binary_sensor", "disk_sda_health")
        assert hass.states.get(eid).state == "on"


async def test_virtual_nics_filtered_out(hass, overview_fixture):
    docker_nic = {**overview_fixture["net"][0], "iface": "docker0", "rx_rate": 1.0}
    overview = {**overview_fixture, "net": [*overview_fixture["net"], docker_nic]}
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "nic_docker0_nic_rx_rate") is None
        assert entity_id_for(hass, entry, "sensor", "nic_eth0_nic_rx_rate") is not None


async def test_ovs_interfaces_filtered_out(hass, overview_fixture):
    # fixture 自带 ovs-system 与 eno1-ovs（OVS 环境）——不得生成网卡实体
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "nic_ovs_system_nic_rx_rate") is None
        assert entity_id_for(hass, entry, "sensor", "nic_eno1_ovs_nic_rx_rate") is None
        # 物理口不受影响
        assert entity_id_for(hass, entry, "sensor", "nic_eth0_nic_rx_rate") is not None


async def test_failed_update_backoff_and_unavailable(hass, overview_fixture):
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        base_interval = coordinator.update_interval
        assert base_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)

        eid = entity_id_for(hass, entry, "sensor", "cpu_usage")
        assert hass.states.get(eid).state == "12.3"

        # 下一轮失败：指数退避 + 实体 unavailable
        mocks["async_get_overview"].side_effect = FnOsConnectError("boom")
        async_fire_time_changed(
            hass, dt_util.utcnow() + base_interval + timedelta(seconds=1)
        )
        await hass.async_block_till_done()

        assert not coordinator.last_update_success
        assert coordinator.update_interval > base_interval
        assert hass.states.get(eid).state == "unavailable"

        # 恢复：interval 重置，实体恢复可用并写回数据
        mocks["async_get_overview"].side_effect = None
        async_fire_time_changed(
            hass, dt_util.utcnow() + coordinator.update_interval + timedelta(seconds=1)
        )
        await hass.async_block_till_done()
        assert coordinator.last_update_success
        assert coordinator.update_interval == base_interval
        assert hass.states.get(eid).state == "12.3"


async def test_auth_failure_triggers_reauth_flow(hass, overview_fixture):
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        mocks["async_get_overview"].side_effect = FnOsAuthError("口令错误")

        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
        )
        await hass.async_block_till_done()

        assert not entry.runtime_data.last_update_success
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert any(flow["context"].get("source") == "reauth" for flow in flows)


async def test_low_frequency_throttle(hass, overview_fixture):
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        # 节流窗口内（monotonic 时间戳未推进）：低频端点不再请求，仅 overview
        for key in ("traffic", "power_stats", "processes", "disk_health"):
            coordinator._last_fetch[key] = time.monotonic()
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
        )
        await hass.async_block_till_done()
        assert coordinator.last_update_success
        assert mocks["async_get_traffic"].await_count == 1
        assert mocks["async_get_disk_health"].await_count == 1
        assert mocks["async_get_overview"].await_count == 2

        # 节流窗口外（时间戳重置）：重新拉取
        coordinator._last_fetch["traffic"] = 0.0
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
        )
        await hass.async_block_till_done()
        assert mocks["async_get_traffic"].await_count == 2


async def test_sub_fetch_failure_keeps_overview_alive(hass, overview_fixture):
    # 低频端点失败不拖垮本轮 overview 数据（H-1/H-2 回归：修复前
    # except 分支 NameError -> 整轮失败 + 实体 unavailable）
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert mocks["async_get_traffic"].await_count == 1

        # 显式重置节流窗口：hass mock 时钟不推进 time.monotonic()，
        # 不重置则 traffic 被节流跳过、失败路径根本不会执行（旧用例空转根因）
        coordinator._last_fetch["traffic"] = 0.0
        mocks["async_get_traffic"].side_effect = FnOsConnectError("traffic down")
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
        )
        await hass.async_block_till_done()

        # 失败的 mock 必须真的被调用到
        assert mocks["async_get_traffic"].await_count == 2
        # overview 数据存活，本轮不算失败
        assert coordinator.last_update_success
        assert "traffic" not in coordinator.data
        eid = entity_id_for(hass, entry, "sensor", "cpu_usage")
        assert hass.states.get(eid).state == "12.3"


async def test_throttle_skip_carries_previous_data(hass, overview_fixture):
    # M-1 回归：节流跳过的周期沿用上一轮成功值，低频传感器不闪 unknown
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        first_traffic = coordinator.data["traffic"]

        # 全部低频端点置于节流窗口内：本轮只拉 overview
        for key in ("traffic", "power_stats", "processes", "disk_health"):
            coordinator._last_fetch[key] = time.monotonic()
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
        )
        await hass.async_block_till_done()

        assert coordinator.last_update_success
        assert mocks["async_get_overview"].await_count == 2
        assert mocks["async_get_traffic"].await_count == 1
        assert mocks["async_get_power_stats"].await_count == 1
        assert mocks["async_get_processes"].await_count == 1
        assert mocks["async_get_disk_health"].await_count == 1
        # 结转上轮数据：跳过周期数据不缺失
        assert coordinator.data["traffic"] == first_traffic
        assert coordinator.data["power_stats"]["energy"]["today_kwh"] == 0.321
        # 消费方（net_down_rate，展示单位 MB/s）不闪 unknown
        eid = entity_id_for(hass, entry, "sensor", "net_down_rate")
        assert float(hass.states.get(eid).state) == 1.024


async def test_options_change_reloads_and_applies(hass, overview_fixture):
    # M-2 回归：options 改轮询间隔 -> update listener 自动重载并生效
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL
        )
        old_coordinator = entry.runtime_data

        hass.config_entries.async_update_entry(
            entry, options={CONF_SCAN_INTERVAL: 300}
        )
        await hass.async_block_till_done()

        # listener 触发重载：新 coordinator 以新间隔运行
        assert entry.runtime_data is not old_coordinator
        assert entry.runtime_data.update_interval == timedelta(seconds=300)
        assert entry.runtime_data.last_update_success
        # 重载后实体仍在、数据正常
        eid = entity_id_for(hass, entry, "sensor", "cpu_usage")
        assert hass.states.get(eid).state == "12.3"


async def test_options_scan_interval_applies(hass, overview_fixture):
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_SCAN_INTERVAL: 300})
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.update_interval == timedelta(seconds=300)


async def test_diagnostics_redact_password(hass, overview_fixture):
    from custom_components.qfnplus.diagnostics import async_get_config_entry_diagnostics

    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        diag = await async_get_config_entry_diagnostics(hass, entry)
        assert diag["entry_data"]["password"] == "**REDACTED**"
        assert diag["coordinator"]["last_update_success"] is True


async def test_object_ids_use_qfnplus_prefix(hass, overview_fixture):
    """所有实体 object_id 统一 qfnplus_ 前缀；友好名称 = 设备名 + 中文实体名。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        checks = (
            ("sensor", "cpu_usage", "sensor.qfnplus_cpu_usage", "Q飞牛+ CPU usage"),
            ("sensor", "energy_month", "sensor.qfnplus_energy_month",
             "Q飞牛+ Energy this month"),
            ("sensor", "vol_vol1_used", "sensor.qfnplus_volume_vol1_used", None),
            ("sensor", "nic_eth0_nic_rx_rate", "sensor.qfnplus_nic_eth0_nic_rx_rate", None),
            ("sensor", "disk_sda_temperature", "sensor.qfnplus_disk_sda_temperature",
             "sda Temperature"),
            ("sensor", "disk_nvme0n1_temperature", "sensor.qfnplus_disk_nvme0n1_temperature",
             "nvme0n1 Temperature"),
            ("binary_sensor", "disk_sda_health", "binary_sensor.qfnplus_disk_sda_health",
             "sda Health"),
            ("binary_sensor", "docker_problem", "binary_sensor.qfnplus_docker_problem", None),
        )
        for platform, suffix, expected_eid, friendly in checks:
            eid = entity_id_for(hass, entry, platform, suffix)
            assert eid == expected_eid, f"{suffix}: {eid} != {expected_eid}"
            state = hass.states.get(eid)
            assert state is not None, f"state missing for {eid}"
            if friendly is not None:
                assert state.attributes["friendly_name"] == friendly


async def test_docker_aggregate_entities(hass, overview_fixture):
    """容器聚合：running=2（web/monitor）、stopped=1（backup）、abnormal=2。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        eid = entity_id_for(hass, entry, "sensor", "docker_running")
        assert float(hass.states.get(eid).state) == 2.0
        eid = entity_id_for(hass, entry, "sensor", "docker_stopped")
        assert float(hass.states.get(eid).state) == 1.0
        eid = entity_id_for(hass, entry, "sensor", "docker_abnormal")
        state = hass.states.get(eid)
        assert float(state.state) == 2.0
        assert sorted(state.attributes["abnormal"]) == ["crashed", "monitor"]

        eid = entity_id_for(hass, entry, "binary_sensor", "docker_problem")
        assert hass.states.get(eid).state == "on"


async def test_docker_unavailable_creates_no_entities(hass, overview_fixture):
    """Docker 不可用（未安装应用）时不生成任何容器实体。"""
    overview = {
        **overview_fixture,
        "docker": {"available": False, "error": "docker 命令不可用", "images": 0,
                   "containers": []},
    }
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "docker_running") is None
        assert entity_id_for(hass, entry, "binary_sensor", "docker_problem") is None


async def test_smart_diagnostics_and_null_tolerance(hass, overview_fixture):
    """SMART 诊断实体：sda 有值；nvme0n1 计数 None/空串 -> unknown。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # sda：ATA 计数齐全
        eid = entity_id_for(hass, entry, "sensor", "disk_sda_power_on_hours")
        state = hass.states.get(eid)
        assert float(state.state) == 12345.0
        assert state.attributes.get("unit_of_measurement") == "h"
        eid = entity_id_for(hass, entry, "sensor", "disk_sda_reallocated_sectors")
        assert float(hass.states.get(eid).state) == 0.0

        # nvme0n1：SMART 计数未采集（null）/通电小时为空串 -> unknown
        eid = entity_id_for(hass, entry, "sensor", "disk_nvme0n1_uncorrectable")
        assert hass.states.get(eid).state == "unknown"
        eid = entity_id_for(hass, entry, "sensor", "disk_nvme0n1_power_on_hours")
        assert hass.states.get(eid).state == "unknown"


async def test_disk_io_entities(hass, overview_fixture):
    """整机 IO 只汇总物理盘（不含 nvme0n1p1 分区）；每盘速率 + IOPS 属性。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # 整机读速率 = nvme0n1(1048576) + sda(2097152)，分区不计（展示单位 MB/s）
        eid = entity_id_for(hass, entry, "sensor", "disk_io_read_rate")
        assert float(hass.states.get(eid).state) == 3.145728
        eid = entity_id_for(hass, entry, "sensor", "disk_io_write_rate")
        assert float(hass.states.get(eid).state) == 1.572864
        eid = entity_id_for(hass, entry, "sensor", "disk_io_read_iops")
        assert float(hass.states.get(eid).state) == 42.0
        eid = entity_id_for(hass, entry, "sensor", "disk_io_write_iops")
        assert float(hass.states.get(eid).state) == 21.0

        # 每盘速率 + IOPS 属性
        eid = entity_id_for(hass, entry, "sensor", "disk_nvme0n1_read_rate")
        state = hass.states.get(eid)
        assert float(state.state) == 1.048576
        assert state.attributes["read_iops"] == 12.0
        assert state.attributes["in_progress"] == 0


async def test_gpu_entities(hass, overview_fixture):
    """GPU 利用率/频率；util 为 null 时 unknown；无 GPU 不建实体。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        eid = entity_id_for(hass, entry, "sensor", "gpu_uhd_graphics_utilization")
        assert float(hass.states.get(eid).state) == 12.5
        eid = entity_id_for(hass, entry, "sensor", "gpu_uhd_graphics_frequency")
        assert float(hass.states.get(eid).state) == 1200.0

    # GPU 清空 -> 不生成
    entry2 = make_entry()
    entry2.add_to_hass(hass)
    overview = {**overview_fixture, "gpu": []}
    with patch_client(overview):
        assert await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry2, "sensor", "gpu_uhd_graphics_utilization") is None

    # util=null -> unknown
    entry3 = make_entry()
    entry3.add_to_hass(hass)
    gpu = [{**overview_fixture["gpu"][0], "util": None}]
    overview = {**overview_fixture, "gpu": gpu}
    with patch_client(overview):
        assert await hass.config_entries.async_setup(entry3.entry_id)
        await hass.async_block_till_done()
        eid = entity_id_for(hass, entry3, "sensor", "gpu_uhd_graphics_utilization")
        assert hass.states.get(eid).state == "unknown"


async def test_nic_total_bytes_total_increasing(hass, overview_fixture):
    """累计收发字节：TOTAL_INCREASING 统计口径；丢包/错误为诊断类。"""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        eid = entity_id_for(hass, entry, "sensor", "nic_eth0_nic_total_rx")
        state = hass.states.get(eid)
        # 建议展示单位 GB：123456789012 B -> 123.456789012 GB
        assert float(state.state) == 123.456789012
        assert state.attributes.get("state_class") == "total_increasing"
        eid = entity_id_for(hass, entry, "sensor", "nic_eth0_nic_total_tx")
        assert float(hass.states.get(eid).state) == 98.765432109

        # 丢包/错误（rx+tx 求和）
        eid = entity_id_for(hass, entry, "sensor", "nic_eth0_nic_drop")
        assert float(hass.states.get(eid).state) == 0.0


# --- M-2b 动态实体清理 / 换盘重建 --------------------------------------------


def _next_refresh(hass) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
    )


async def test_disk_removal_cleans_entities(hass, overview_fixture):
    # M-2b 回归：拔盘后 sensor/binary_sensor 实体从注册表移除，不留僵尸
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "disk_sda_temperature")
        assert entity_id_for(hass, entry, "binary_sensor", "disk_sda_health")

        # 拔掉 sda：disks_detail 与 diskio 均不再出现
        remaining = {
            **overview_fixture,
            "disks_detail": [
                d for d in overview_fixture["disks_detail"] if d["name"] != "sda"
            ],
            "diskio": [
                d for d in overview_fixture["diskio"] if not d["name"].startswith("sda")
            ],
        }
        mocks["async_get_overview"].return_value = remaining
        _next_refresh(hass)
        await hass.async_block_till_done()

        assert entity_id_for(hass, entry, "sensor", "disk_sda_temperature") is None
        assert entity_id_for(hass, entry, "sensor", "disk_sda_power_on_hours") is None
        assert entity_id_for(hass, entry, "binary_sensor", "disk_sda_health") is None
        # 未拔的盘不受影响
        assert entity_id_for(hass, entry, "sensor", "disk_nvme0n1_temperature")
        assert entity_id_for(hass, entry, "binary_sensor", "disk_nvme0n1_health")


async def test_volume_and_nic_removal_cleans_entities(hass, overview_fixture):
    # M-2b 回归：删卷/移除网卡后对应实体清理
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "vol_vol1_used")
        assert entity_id_for(hass, entry, "sensor", "nic_eth0_nic_rx_rate")

        # 删卷
        mocks["async_get_overview"].return_value = {**overview_fixture, "disks": []}
        _next_refresh(hass)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "vol_vol1_used") is None
        assert entity_id_for(hass, entry, "sensor", "vol_vol1_percent") is None
        # 网卡不受影响
        assert entity_id_for(hass, entry, "sensor", "nic_eth0_nic_rx_rate")

        # 移除网卡
        mocks["async_get_overview"].return_value = {
            **overview_fixture,
            "net": [n for n in overview_fixture["net"] if n["iface"] != "eth0"],
        }
        _next_refresh(hass)
        await hass.async_block_till_done()
        assert entity_id_for(hass, entry, "sensor", "nic_eth0_nic_rx_rate") is None
        assert entity_id_for(hass, entry, "sensor", "nic_eth0_nic_total_rx") is None


async def test_disk_swap_recreates_entities_with_new_serial(hass, overview_fixture):
    # M-2b 回归：同名换盘（serial 变化）-> 旧实体删除，下一轮以新盘身份重建
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    with patch_client(overview_fixture) as mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        eid = entity_id_for(hass, entry, "sensor", "disk_sda_temperature")
        assert eid == "sensor.qfnplus_disk_sda_temperature"
        old_device = registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_disk_wd_xxx")}
        )
        assert old_device is not None
        assert old_device.serial_number == "WD-XXX"

        # 换盘：同槽位 sda，serial/model 更新
        sda = next(d for d in overview_fixture["disks_detail"] if d["name"] == "sda")
        nvme = next(
            d for d in overview_fixture["disks_detail"] if d["name"] == "nvme0n1"
        )
        swapped = {**sda, "serial": "WD-NEW", "model": "WD40EFRZ"}
        mocks["async_get_overview"].return_value = {
            **overview_fixture,
            "disks_detail": [swapped, nvme],
        }
        # 换盘轮：旧实体移除并以新 serial 同轮重建
        _next_refresh(hass)
        await hass.async_block_till_done()

        assert entity_id_for(
            hass, entry, "sensor", "disk_sda_temperature"
        ) == "sensor.qfnplus_disk_sda_temperature"
        assert entity_id_for(hass, entry, "binary_sensor", "disk_sda_health")
        new_device = registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_disk_wd_new")}
        )
        assert new_device is not None
        assert new_device.serial_number == "WD-NEW"
        assert new_device.model == "WD40EFRZ"


# --- 低危回归（L-1 / L-3 / L-4 / L-5）----------------------------------------


async def test_cpu_load_short_array_tolerated(hass, overview_fixture):
    # L-1 回归：cpu.load 长度不足 3 时缺失档位 unknown，不抛 IndexError
    overview = {
        **overview_fixture,
        "cpu": {**overview_fixture["cpu"], "load": [0.5]},
    }
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        eid = entity_id_for(hass, entry, "sensor", "load_1")
        assert float(hass.states.get(eid).state) == 0.5
        eid = entity_id_for(hass, entry, "sensor", "load_5")
        assert hass.states.get(eid).state == "unknown"
        eid = entity_id_for(hass, entry, "sensor", "load_15")
        assert hass.states.get(eid).state == "unknown"


async def test_duplicate_gpu_names_get_unique_keys(hass, overview_fixture):
    # L-3 回归：同名双卡第二块加 index 后缀，不再静默跳过
    gpu = overview_fixture["gpu"][0]
    overview = {**overview_fixture, "gpu": [gpu, {**gpu, "util": 55.0}]}
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        eid1 = entity_id_for(hass, entry, "sensor", "gpu_uhd_graphics_utilization")
        assert float(hass.states.get(eid1).state) == 12.5
        eid2 = entity_id_for(hass, entry, "sensor", "gpu_uhd_graphics_1_utilization")
        assert float(hass.states.get(eid2).state) == 55.0


async def test_docker_problem_attached_to_main_device(hass, overview_fixture):
    # L-4 回归：docker_problem 必须挂在主设备下（与 docker_* 计数实体一致）
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch_client(overview_fixture):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        from homeassistant.helpers import entity_registry as er

        eid = entity_id_for(hass, entry, "binary_sensor", "docker_problem")
        registry = er.async_get(hass)
        registry_entry = registry.async_get(eid)
        assert registry_entry.device_id
        device = dr.async_get(hass).async_get(registry_entry.device_id)
        assert (DOMAIN, entry.entry_id) in device.identifiers


async def test_diagnostics_without_coordinator(hass, overview_fixture):
    # L-5 回归：entry 未成功 setup（runtime_data 为 None）时 diagnostics 不崩
    from custom_components.qfnplus.diagnostics import async_get_config_entry_diagnostics

    entry = make_entry()
    entry.add_to_hass(hass)
    # 不 setup：无 runtime_data
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["coordinator"] is None
    assert diag["entry_data"]["password"] == "**REDACTED**"
