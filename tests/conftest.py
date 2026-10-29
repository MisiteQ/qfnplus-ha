"""Shared fixtures for Q飞牛+ tests."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

if sys.platform == "win32":
    # PHCC 在每个测试的 pytest_runtest_setup 里调用
    # pytest_socket.disable_socket(allow_unix_socket=True)：Linux 上 asyncio 的
    # AF_UNIX socketpair 可通行，但 Windows 的 socketpair 走 AF_INET 回退，
    # 会被 GuardedSocket 拦截，ProactorEventLoop 创建即失败。
    # 本套件全部走 mock、无真实网络，故在 Windows 上将 disable_socket 置为
    # 空操作（PHCC 运行时才解析模块属性，补丁必然生效）。Linux CI 不受影响。
    import pytest_socket as _pytest_socket

    def _disable_socket_noop(allow_unix_socket: bool = False) -> None:
        return None

    _pytest_socket.disable_socket = _disable_socket_noop


@pytest.fixture(autouse=True)
def _win32_default_dns_resolver():
    """Windows 上避免 aiodns 初始化失败（需要 SelectorEventLoop）。

    async_create_clientsession 会无条件创建 AsyncResolver（aiodns），
    而 Windows 的 ProactorEventLoop 不被 aiodns 支持。注意 aiohttp 的
    DefaultResolver 在装有 aiodns 时就是 AsyncResolver 的别名，必须
    显式换用线程版 ThreadedResolver。真实集成仅 IP 直连局域网主机，
    不依赖 DNS。仅 win32 生效，Linux CI 不受影响。
    """
    if sys.platform != "win32":
        yield
        return
    from aiohttp.resolver import ThreadedResolver

    with patch("homeassistant.helpers.aiohttp_client.AsyncResolver", ThreadedResolver):
        yield

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.qfnplus.api import FnOsClient  # noqa: E402
from custom_components.qfnplus.const import DOMAIN  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

# 低频端点返回结构（字段名均按 fnmonitor v2.20.0 源码确认）
TRAFFIC_FIXTURE: dict = {
    "realtime": {"rx_rate": 1024000.0, "tx_rate": 512000.0},
    "ifaces": [],
    "totals": {
        "boot_rx": 123456789012,
        "boot_tx": 98765432109,
        "today_rx": 1073741824,
        "today_tx": 536870912,
        "month_rx": 32212254720,
        "month_tx": 16106127360,
        "basis": "daily",
    },
    "containers": [],
    "config": {"exclude_bridge": 0},
}

POWER_STATS_FIXTURE: dict = {
    "realtime": {
        "power_w": 18.5,
        "source": "rapl",
        "package": 3.2,
        "core": 2.1,
        "dram": 1.5,
        "base": 8.0,
        "disks_w": 16.0,
        "disks_active": 2,
    },
    "energy": {
        "today_kwh": 0.321,
        "month_kwh": 7.65,
        "boot_kwh": 0.635,
        "basis": "daily",
    },
    "cost": {"today_yuan": 0.19, "month_yuan": 4.59, "rate_yuan_per_kwh": 0.6},
    "disks": [],
    "standby_protect": True,
    "estimation_params": {
        "tdp_w": 65.0,
        "disk_typical_w": 8.0,
        "nic_fixed_w": 5.0,
        "base_w": 8.0,
    },
}

DISK_HEALTH_FIXTURE: dict = {
    "ok": True,
    "enabled": True,
    "interval_h": 6,
    "presets": [6, 12, 24, 72, 168],
    "last_ts": 1769596800.0,
    "next_ts": 1769618400.0,
    "scanning": False,
    "summary": {},
    "error": "",
    "items": {
        "sda": {
            "dev": "sda",
            "grade": "正常",
            "detail": "",
            "health": "正常",
            "serial": "WD-XXX",
            "model": "WD40EFRX",
            "proto": "sata",
            "defects": 0,
            "pending_defects": 0,
            "uncorr": 0,
            "temp_c": 38,
            "power_on_hours": 12345,
            "rpm": "5400",
            "manufactured": "",
            "standby": False,
            "scan_ts": 1769596800.0,
            "age": 3200,
        }
    },
    "now": 1769600000.0,
}

PROCESSES_FIXTURE: dict = {
    "processes": [
        {
            "pid": 1234,
            "user": "root",
            "cpu": 7.2,
            "mem": 3.1,
            "rss": 265289728,
            "comm": "python3",
            "args": "python3 server.py --port 8777",
        }
    ]
}


# 最小 overview（api 层测试用；字段名同真实结构）
OVERVIEW_MINIMAL: dict = {
    "ts": 1769600000.0,
    "interval": 10,
    "cpu": {"percent": 12.3, "load": [0.42, 0.38, 0.35]},
    "mem": {"total": 8589934592, "used": 3435973836, "percent": 40.0, "swap_total": 0},
    "uptime": 123456.7,
    "system": {"hostname": "fnos", "boot_time": 1769476543.3},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    yield


@pytest.fixture(name="overview_fixture")
def overview_fixture_fixture() -> dict:
    """Real-shape /api/overview payload (fnmonitor v2.20.0)."""
    return json.loads((FIXTURES / "overview.json").read_text(encoding="utf-8"))


@pytest.fixture(name="mock_setup_entry")
def mock_setup_entry_fixture():
    """Prevent real setup during config flow tests."""
    with patch(
        "custom_components.qfnplus.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


def make_entry(
    host: str = "192.168.1.10",
    port: int = 8777,
    password: str = "secret",
    **kwargs,
) -> MockConfigEntry:
    """Create a MockConfigEntry for the integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="fnos",
        data={CONF_HOST: host, CONF_PORT: port, CONF_PASSWORD: password},
        **kwargs,
    )


@contextmanager
def patch_client(
    overview: dict,
    traffic: dict | None = None,
    power_stats: dict | None = None,
    disk_health: dict | None = None,
    processes: dict | None = None,
):
    """Patch all FnOsClient fetch methods with fixture data.

    Yields a dict of AsyncMocks so tests can assert call counts.
    （不用 patch.multiple 的返回值：仅当值为 DEFAULT 时才回传 dict）
    """
    mocks: dict[str, AsyncMock] = {
        "async_get_overview": AsyncMock(return_value=overview),
        "async_get_traffic": AsyncMock(return_value=traffic or TRAFFIC_FIXTURE),
        "async_get_power_stats": AsyncMock(return_value=power_stats or POWER_STATS_FIXTURE),
        "async_get_disk_health": AsyncMock(return_value=disk_health or DISK_HEALTH_FIXTURE),
        "async_get_processes": AsyncMock(return_value=processes or PROCESSES_FIXTURE),
    }
    with patch.multiple(FnOsClient, **mocks):
        yield mocks


def entity_id_for(hass, entry: ConfigEntry, platform: str, unique_suffix: str) -> str | None:
    """Look up the entity id registered for a unique_id."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{unique_suffix}"
    )
