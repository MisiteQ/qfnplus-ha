"""DataUpdateCoordinator for the Q飞牛+ integration.

Single coordinator polls /api/overview every cycle (default 60s, options
30/60/120/300). Low-frequency endpoints (traffic, power_stats, processes,
disk_health) are timestamp-throttled and only fetched when older than
their throttle window. On failure the update interval grows exponentially
so a down NAS is not hammered; failed cycles leave entities unavailable
(via CoordinatorEntity) instead of writing bogus states.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FnOsAuthError, FnOsClient, FnOsConnectError, FnOsError, FnOsLoginLocked
from .const import (
    BATCH_TIMEOUT,
    DOMAIN,
    LOGGER,
    MAX_BACKOFF_SECONDS,
    BACKOFF_MAX_STEPS,
    THROTTLE_DISK_HEALTH,
    THROTTLE_POWER_STATS,
    THROTTLE_PROCESSES,
    THROTTLE_TRAFFIC,
)

# (data key, throttle seconds, client fetch method name)
SUB_FETCHES: tuple[tuple[str, float, str], ...] = (
    ("traffic", THROTTLE_TRAFFIC, "async_get_traffic"),
    ("power_stats", THROTTLE_POWER_STATS, "async_get_power_stats"),
    ("processes", THROTTLE_PROCESSES, "async_get_processes"),
    ("disk_health", THROTTLE_DISK_HEALTH, "async_get_disk_health"),
)


class FnOsDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll fnmonitor and expose a dict: overview/traffic/power_stats/..."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FnOsClient,
        base_interval: timedelta,
    ) -> None:
        """Initialize; base_interval comes from entry options (scan_interval)."""
        self.client = client
        self._base_interval = base_interval
        self._consecutive_failures = 0
        # monotonic timestamps of last successful fetch per throttled endpoint
        self._last_fetch: dict[str, float] = {}
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=base_interval,
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest data; raise UpdateFailed/ConfigEntryAuthFailed."""
        try:
            async with asyncio.timeout(BATCH_TIMEOUT):
                data: dict[str, Any] = {
                    "overview": await self.client.async_get_overview()
                }
                now = time.monotonic()
                for key, throttle, method in SUB_FETCHES:
                    last = self._last_fetch.get(key)
                    # None 哨兵：从未取过必须立即取。不能用 0.0 默认值——
                    # CI 新机 monotonic 开机计数小，now - 0.0 < throttle
                    # 会把大 throttle 端点（disk_health 300s）首轮误跳过
                    if last is not None and now - last < throttle:
                        # 节流跳过：沿用上一轮成功值，避免 30s 档下
                        # 流量/能耗/进程传感器周期性闪 unknown
                        if self.data and key in self.data:
                            data[key] = self.data[key]
                        continue
                    fetch: Callable[[], Awaitable[Any]] = getattr(self.client, method)
                    try:
                        data[key] = await fetch()
                        self._last_fetch[key] = now
                    except FnOsError as err:
                        # 低频端点失败不拖垮本轮 overview 数据；
                        # 失败的 key 不结转，消费方降级 unknown
                        LOGGER.debug("fetch %s failed: %s", key, err)
        except FnOsAuthError as err:
            # 已存口令被拒（单飞重登一次后仍 401）-> 触发 reauth
            self._consecutive_failures += 1
            self._apply_backoff()
            raise ConfigEntryAuthFailed(
                "fnmonitor rejected the stored password"
            ) from err
        except (FnOsLoginLocked, FnOsConnectError, TimeoutError) as err:
            self._consecutive_failures += 1
            self._apply_backoff()
            if isinstance(err, FnOsLoginLocked) and err.retry_after:
                raise UpdateFailed(
                    f"fnmonitor login locked for {err.retry_after:.0f}s"
                ) from err
            raise UpdateFailed(f"error communicating with fnmonitor: {err}") from err

        self._consecutive_failures = 0
        self.update_interval = self._base_interval
        return data

    def _apply_backoff(self) -> None:
        """Exponential backoff on consecutive failures, capped."""
        step = min(self._consecutive_failures, BACKOFF_MAX_STEPS)
        self.update_interval = min(
            self._base_interval * (2**step), timedelta(seconds=MAX_BACKOFF_SECONDS)
        )
