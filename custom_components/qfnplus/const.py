"""Constants for the Q飞牛+ (fnOS monitor) integration."""
from __future__ import annotations

from logging import Logger, getLogger

from homeassistant.const import Platform

LOGGER: Logger = getLogger(__package__)

DOMAIN = "qfnplus"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

MANUFACTURER = "飞牛"
DEFAULT_NAME = "Q飞牛+"

# --- Config keys -----------------------------------------------------------
# 凭据与连接标识只存 entry.data；调优项存 entry.options
CONF_SCAN_INTERVAL = "scan_interval"

# --- Connection defaults ---------------------------------------------------
# fnmonitor 仅提供明文 HTTP（无 TLS 能力），故不提供 use_ssl/verify_ssl 字段
DEFAULT_PORT = 8777
DEFAULT_SCAN_INTERVAL = 60
INTERVAL_OPTIONS = (30, 60, 120, 300)

# --- Request timing --------------------------------------------------------
# fnmonitor 采集主循环默认 10s + /api/overview 5s 响应缓存，
# 轮询低于 10s 拿不到新数据；请求超时 10s 足够局域网使用
REQUEST_TIMEOUT = 10.0
BATCH_TIMEOUT = 60.0
MAX_BACKOFF_SECONDS = 600.0
BACKOFF_MAX_STEPS = 5

# --- Low-frequency endpoint throttling (monotonic seconds) -----------------
# 磁盘健康巡检 6h 一次；traffic/power_stats 服务端 5s 缓存；
# processes 采样 10s 一次。按时间戳节流，不每次轮询都取
THROTTLE_TRAFFIC = 60.0
THROTTLE_POWER_STATS = 60.0
THROTTLE_PROCESSES = 60.0
THROTTLE_DISK_HEALTH = 300.0

# --- API paths (fnmonitor v2.20.0) -----------------------------------------
API_OVERVIEW = "/api/overview"
API_TRAFFIC = "/api/traffic"
API_POWER_STATS = "/api/power_stats"
API_DISK_HEALTH = "/api/disk_health"
API_PROCESSES = "/api/processes"
LOGIN_PATH = "/api/auth/password"

# --- Config flow error keys ------------------------------------------------
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_INVALID_HOST = "invalid_host"
ERROR_LOGIN_LOCKED = "login_locked"
ERROR_UNKNOWN = "unknown"

# --- Data mapping ----------------------------------------------------------
# 虚拟网卡前缀（与 fnmonitor VIRT_IFACE_PREFIXES 对齐，另排除 lo）。
# 在 fnmonitor 基础上补充 OVS：ovs-* 前缀（ovs-system）与 *-ovs 后缀（eno1-ovs）
VIRTUAL_IFACE_PREFIXES = ("docker", "veth", "br-", "virbr", "tun", "tap",
                          "vnet", "lxc", "kube", "wg", "ovs")
VIRTUAL_IFACE_SUFFIXES = ("-ovs",)

# disks_detail[].health / disk_health items[].grade 的中文值映射（不直接暴露中文）
HEALTH_OK_VALUES = frozenset({"正常", "good", "ok", "healthy", "passed"})
HEALTH_BAD_VALUES = frozenset({"警告", "故障", "warning", "failed", "failing"})
