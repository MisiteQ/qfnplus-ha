# 飞牛系统状态数据源规格（fnmonitor）

> 用途：供 Home Assistant 自定义集成读取飞牛 fnOS 系统运行状态。
> 数据源：[fnmonitor](file:///D:/CODE/fnmonitor) v2.20.0（飞牛 FPK 原生应用，作者 MisiteQ）。
> 本文基于源码静态分析（[app/server.py](file:///D:/CODE/fnmonitor/app/server.py) 约 1.2 万行，零第三方依赖），未修改任何代码。
> 分析日期：2026-10-28。

---

## 1. 技术栈与运行形态

| 项 | 说明 |
|---|---|
| 语言/框架 | Python 3 纯标准库单文件后端（`app/server.py`），`ThreadingHTTPServer` HTTP/1.1 长连接，无任何第三方依赖 |
| 前端 | 单文件 `app/www/index.html`（与对接无关） |
| 安装方式 | 飞牛 **FPK 原生应用包**（非 Docker、非裸机）：应用中心手动安装或 FnDepot 源安装；安装位置 `/var/apps/fnmonitor/`（基础目录）+ 应用可执行目录（`TRIM_APPDEST`） |
| 启动方式 | 生命周期脚本 `cmd/main`：`nohup python3 server.py --port 8777 --data-dir $TRIM_PKGVAR`，PID 文件 `$TRIM_PKGVAR/server.pid`，日志 `$TRIM_PKGVAR/server.log`（5 MB 轮转），`MALLOC_ARENA_MAX=2`，fcntl 单实例锁 |
| 监听 | IPv4+IPv6 双栈 `0.0.0.0` / `::`，默认端口 **8777**（manifest `service_port=8777`；可在面板设置改端口，config.json `port` 优先，绑定失败依次回退启动参数 → 8777） |
| 运行身份 | **root**（`config/privilege` 声明 `run-as: root`；需读 sysfs 风扇、docker.sock/virsh、smartctl、跨用户 /proc） |
| 配置文件 | `config.json` 固定在应用数据目录 `$TRIM_PKGVAR`（即 `/var/apps/fnmonitor/var/config.json`），0600 原子写；改采集间隔/端口等即时生效或重启生效 |
| 数据目录 | 默认 `$TRIM_PKGVAR/localdata/`（可在设置改到存储空间）；含 `monitor.db`（SQLite 历史库）、`ui.json`（界面配置） |
| 依赖系统命令 | `lsblk`、`lscpu`、`ip`、`ss`、`ps`、`systemctl`、`docker`（含 stats/inspect）、`hostname`；可选：`smartctl`（硬盘温度/SMART，SATA+NVMe+SAS）、`sensors`（lm-sensors）、`virsh`（虚拟机）、`dmidecode`（内存插槽）、`lspci`、`nvidia-smi`（N卡）、`ipmitool`（BMC 风扇）、`storcli/perccli`（RAID 卡）、`hdparm`（休眠检测） |
| 依赖 /proc /sys | `/proc/stat`、`/proc/meminfo`、`/proc/loadavg`、`/proc/uptime`、`/proc/cpuinfo`、`/proc/net/dev`、`/proc/diskstats`（兜底 `/sys/block/*/stat`）、`/proc/version`、`/proc/ioports`（EC 风扇实验特性）、`/sys/class/thermal`、`/sys/class/hwmon`、`/sys/block/*/device/hwmon`、`/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq`、`/sys/class/dmi/id/*` |
| 隐私 | 完全离线（仅天气/在线更新按需联网，可关）；全部数据存 NAS 本机 SQLite |

---

## 2. 采集指标清单

采集主体为 `Collector` 后台线程（[server.py L8131](file:///D:/CODE/fnmonitor/app/server.py#L8131)）：主循环默认每 **interval=10 秒**一轮（向导可选 5/10/15/30/60，最小 2 秒），重型子项按各自周期降频。v2.20.0 支持按面板隐藏"停采"（隐藏对应版块后相关 /proc 扫描、磁盘 IO、端口扫描等会跳过；CPU/内存/容量/温度/容器列表为保顶部健康结论始终采集）。

### 2.1 /api/overview 快照字段（核心实时指标）

| 指标 | 字段名（snapshot 内） | 类型/单位 | 计算方式 | 刷新周期 |
|---|---|---|---|---|
| CPU 使用率 | `cpu.percent` | float % | `/proc/stat` 两次采样差分，`100×(1-Δ(idle+iowait)/Δtotal)`，纳入 steal（虚拟机内更准） | 每 tick（10s），首轮 0 |
| 每核使用率 | `cpu.per_core[]` | float %[] | 每个 `cpuN` 同上差分 | 每 tick |
| 系统负载 | `cpu.load` | float[3] | `/proc/loadavg` 1/5/15 分钟 | 每 tick |
| CPU 拓扑 | `cpu.cores/threads/sockets/logical_cpus` | int | `lscpu` 拓扑（Socket×Core/Socket），失败回退 /proc/cpuinfo 去重；解析一次缓存 | 常量 |
| CPU 型号/频率 | `cpu.model` / `cpu.frequency_mhz` | str / int MHz | `/proc/cpuinfo`、`/sys/.../scaling_cur_freq` | 每 tick |
| 内存 | `mem.total/used/free/available/percent` | 字节 / % | `/proc/meminfo`，used=Total−Available | 每 tick |
| 缓存/缓冲 | `mem.cached/mem.buffers` | 字节 | `/proc/meminfo` | 每 tick |
| Swap | `mem.swap_total/swap_used/swap_free/swap_percent` | 字节 / % | `/proc/meminfo` | 每 tick |
| 文件系统 | `disks[]`：`device/mount/fs/total/used/free/percent/standby` | 字节 / % / bool | statvfs；按 fsid 去重（btrfs 子卷/别名只计一次）；排除伪文件系统、网络挂载、只读、<256MB、`/@` 子卷；休眠盘沿用缓存 | 每 tick |
| 存储总使用 | `disk_total`(%) / `disk_used` / `disk_size`(字节) / `disk_pools` | | 存在 `/vol1…` 存储池时只统计存储池，否则全部本地盘聚合 | 每 tick |
| 物理硬盘详情 | `disks_detail[]`：`name/model/vendor/size/used/total/percent/temp/mounts/state/standby/health/rpm/power_on_hours/serial` + SMART 计数 | | `lsblk -bJ` + 巡检缓存（smartctl，见 2.4）；state ∈ active/standby/unknown；SMART 计数键：`reallocated_sectors` `reallocated_events` `pending_sectors` `uncorrectable` `udma_crc`（SAS 盘映射 grown defects/不可纠正错误） | 60s |
| iDRAC 槽位状态 | `idrac_slot_state` | str | off/warming/ready（仅 Dell 服务器相关） | 每 tick |
| 网络 | `net[]`：`iface/rx_bytes/tx_bytes/rx_rate/tx_rate/rx_packets/tx_packets/rx_drop/tx_drop/rx_errs/tx_errs` | 字节累计 / B/s | `/proc/net/dev` 差分（排除 lo，含 docker/veth 等虚拟口）；速率为相邻 tick 均值 | 每 tick |
| 磁盘 IO | `diskio[]`：`name/read_rate/write_rate`(B/s) `/read_iops/write_iops/in_progress` | | `/proc/diskstats` 扇区/次数差分（512B/扇区），兜底 `/sys/block/*/stat` | 每 tick |
| CPU 温度 | `temp.cpu` | °C 或 null | `/sys/class/thermal`（x86_pkg/cpu/soc）→ hwmon coretemp/k10temp 兜底 → iDRAC 带外兜底 | 每 tick |
| 主板温度 | `temp.system` | °C 或 null | thermal zone acpitz/ec 等 | 每 tick |
| 硬盘温度 | `temp.disks[]`：`{name, temp}` | °C | sysfs hwmon drivetemp，smartctl 兜底（`-n standby` 不唤醒休眠盘）；RAID 物理盘温度并入（名称形如「阵列 32:0」） | 每 tick |
| 实时功耗 | `power`：`ok/total/package/core/uncore/dram/base/disks_w/disks_active/source`（source ∈ idrac/rapl/estimated），iDRAC 时另有 `capacity/min/max/avg/psu[]` | W | 优先级：iDRAC 整机 > RAPL（total=package+dram+底座 8W+活动硬盘×8W）> TDP 估算模型（TDP×CPU%+盘+网卡） | 10s |
| GPU | `gpu[]`：`vendor/type/name/pci/temp/util/freq_mhz/power_w/mem_used/mem_total/mem_pct/util_avail/util_source/engines` | °C/%/MHz/W/字节 | NVIDIA=nvidia-smi；AMD=sysfs；Intel 核显=i915 PMU 引擎占用/RC6（无独立温度显存读数） | 10s |
| 内存插槽 | `memory.items[]`/`memory.dual` | | dmidecode SPD：品牌/容量/频率/通道 | 300s |
| 运行时长 | `uptime` | float 秒 | `/proc/uptime` | 每 tick |
| 传感器明细 | `sensors.temps[]/fans[]/volts[]/available/fans_source` | °C/RPM/V | `sensors -j` + `/sys/class/hwmon` 直读去重 + iDRAC 并入；fans_source 标注来源（hwmon/sensors/ipmi/acpi/ec-hwm） | 15s |
| 风扇通道 | `fans[]`（hwmon 枚举） | RPM | `/sys/class/hwmon`（含 device/ 旧布局） | 15s |
| Docker | `docker`：`available/error/images/containers[]/sampled_at`；容器字段 `id/name/image/state/status/status_zh/uptime/health/exit_code/ports/created/networks/net_host/cpu/mem_usage/mem_percent/net/net_in_bytes/net_out_bytes/net_src/rx_rate/tx_rate` | | `docker ps -a --format json` + `docker stats --no-stream`；容器精确流量经 inspect PID → `/proc/<pid>/net/dev`（net_src=proc），差分得 `rx_rate/tx_rate` | 30s |
| 功能模块 | `modules[]`：`id/name/category/installed/running/method/port/web/proc` | | systemd is-active / 进程名 / 容器名匹配（Samba、NFS、UPS 等 60+ 模块） | 60s |
| 端口占用 | `ports`：`apps[]/docker[]/listeners[]` | | 应用 manifest + ss 监听交叉校验 | 60s |
| 硬件信息 | `hardware`：`cpu{model,cores,threads,sockets,arch}/board{sys_vendor,board_*,bios_*}/gpu[]/net[]` | | lscpu/lspci/DMI | 120s |
| RAID | `raid[]`（md 阵列） | | `/proc/mdstat` | 120s |
| RAID 卡 | `raidcard`：`type/label/detail/controllers[]`（storcli：控制器型号、ROC 温度、VD 状态、物理盘盘位/温度/健康/介质错误） | | storcli/perccli JSON | 300s |
| 内置应用 | `apps`（相册/影视/音乐统计） | | 目录扫描 + SQLite COUNT | 600s |
| 主机信息 | `system`：`hostname/kernel/arch/uptime/boot_time/cpu_model/fnos_version/kernel_version/ips[]`（ips 按 iface 分组 ipv4/ipv6，过滤 docker 网桥 172.x、链路本地） | | uname + `ip -o addr` | 60s 缓存 |
| 应用配置 | `config`（脱敏，无口令哈希） | | | 实时 |
| 时间戳 | `ts`（epoch 秒）/ `interval`（当前采集间隔） | | | 每 tick |

### 2.2 独立端点指标

- **进程 TOP**（`/api/processes`）：`processes[]`：`pid/user/cpu/mem/rss/comm/args`。cpu 为 `/proc/[pid]/stat` 区间差分（占整机口径，约 N 核满载），args 做密码类参数脱敏。后台 `ProcSampler` 每 **10s** 采一次内存快照。
- **虚拟机**（`/api/vms`）：`available/installed/kvm/error/vms[]/sampled_at`。virsh domstats 差分得 CPU/内存/磁盘读写/网速，含 IP、磁盘与网卡详情、自启状态。**按需采集**（仅前端轮询时），4s 缓存。`?probe=1` 只探测是否装了 virsh。
- **单盘 SMART**（`/api/smart?dev=sda`）：只接受内核设备名（sda/nvme0n1/vda），按需读取，5 分钟缓存，不唤醒休眠盘。
- **盘位健康**（`/api/disk_health`）：只读巡检缓存表 disk_health（dev → serial/model/proto/health/grade(正常/警告/故障)/defects/pending_defects/uncorr/temp_c/power_on_hours/rpm/standby/scan_ts/changed_ts），不触发 smartctl。

### 2.3 明确不支持的指标

- **UPS 实时数据：无**。仅在功能模块中检测 apcupsd / nut-server（服务/进程/容器）是否安装运行（`modules` 中 id=ups），不读任何 UPS 电量/供电状态。
- 无电池、无 SMART 整体自检进度（仅巡检分级）、无 ZFS pool 明细（按 fs 统计容量）、无 GPU 编解码器占用（Intel 只有引擎占用估算）。
- 无环境光照等其它传感器；温度仅 CPU/主板/硬盘/RAID 盘/iDRAC 测点。

### 2.4 后台巡检与告警

- `DiskScanner` 线程：默认每 **6 小时**（`disk_scan_interval_h`）全盘 smartctl 巡检，健康分级与缺陷计数变化落 SQLite（disk_health / disk_health_hist / disk_scan_log）；状态机去重——首次记基线、变化才通知、恢复也告知。
- 告警通道：SMTP 邮件 + Server酱（`/api/notify` 配置）；另有硬件告警定时汇总（每 30s 判断到点）。
- iDRAC Redfish 带外监控（Dell）：快/慢两组后台轮询线程，温度/风扇/电源冗余/SEL 日志，数据只从缓存读。

---

## 3. 对外接口

### 3.1 形态与鉴权

- **HTTP REST**（GET 只读 / POST 改状态），**无 WebSocket、无 SSE、无 Prometheus/InfluxDB/MQTT 导出**。
- 读取接口直接返回 JSON 对象（无统一 `{ok}` 包裹）；错误统一 `{"ok": false, "error": "<code>", "message": "..."}`。
- **鉴权三种状态**：
  1. **默认无鉴权**（`auth_enabled=0`）：所有接口直接可访问，但启用 **Host 校验**（防 DNS rebinding）——Host 必须是 IP 字面量 / localhost / 单标签主机名 / `.local .lan .home .internal .localdomain .home.arpa` 等局域网后缀，否则 403 `host_not_allowed`。**HA 用 IP 访问即可满足。**
  2. **开启登录口令**：需会话 Cookie。登录：`POST /api/auth/password`，要求 `Content-Type: application/json` + 请求头 `X-FNM-Request: 1` + body `{"password":"..."}`；成功返回 `{"ok":true}` 并 `Set-Cookie: fnmp_sid=<64位hex>; Path=/; Max-Age=<天数>; HttpOnly; SameSite=Lax`。之后所有请求带该 Cookie。口令 PBKDF2-SHA256 校验，登录失败限速（429 + `retry_after` 秒），会话有效期 1–365 天（默认 7），持久化于 sessions.json（只存 sha256(sid)）。
  3. **无静态 API token 机制**（没有 `?token=` 或 `Authorization: Bearer` 支持）。
- **CSRF**：所有 POST 必须 `Content-Type: application/json` **且** 头 `X-FNM-Request: 1`，否则 403 `csrf_rejected`。
- 其它：请求体上限 1 MiB（413）；空闲连接 30s 超时；并发连接有上限（503）；导出/报告并发上限 2（503 + Retry-After）。
- 始终可访问（无需登录）：`/login.html`、`/api/auth/session`（查询登录态）、`/api/auth/config`。

### 3.2 主要 GET 端点

| 路径 | 参数 | 说明 |
|---|---|---|
| `/api/overview` | — | **总览快照**：§2.1 全部字段 + `system` + `config`。响应级缓存 **5 秒**（多客户端复用同一份字节），gzip |
| `/api/system` | — | 主机信息 + `version` + `db_size` + `data_dir` |
| `/api/history` | `metric`、`range` | 历史序列 `{"metric","range","points":[[ts,val],...]}`；range ∈ 10m/1h/6h/24h/7d/all；SQL 内降采样 ≤~720 点；结果缓存 25s；历史为空时附加当前实时值兜底。metric 支持见 §4.1；特殊：`net`（聚合物理网卡 rx/tx 速率）、`net_bytes`（累计字节）、`diskio`（`{r:[],w:[]}` MB/s）、`ctr_in:<容器名>`（累计入站） |
| `/api/sensors` | — | 温度/风扇/电压明细 |
| `/api/fans` | — | hwmon 风扇通道 |
| `/api/power` | — | 实时功耗快照（snapshot.power） |
| `/api/traffic` | — | 流量统计：`realtime{rx_rate,tx_rate}`(B/s)、`ifaces`、`totals{boot_rx,boot_tx,today_rx,today_tx,month_rx,month_tx,basis}`、`containers[]`（TOP20，in/out_bytes+速率）、`config.exclude_bridge`；5s 缓存 |
| `/api/power_stats` | — | 功耗统计：`realtime{power_w,source,package,core,dram,base,disks_w,disks_active}`、`energy{today_kwh,month_kwh,boot_kwh,basis}`、`cost{today_yuan,month_yuan,rate_yuan_per_kwh}`、`disks[]`（休眠状态）、`estimation_params`；5s 缓存 |
| `/api/daily` | `days`（≤30） | 近 N 天每日汇总：kwh/电费/物理口流量/CPU 等，`has_data=false` 表示当天无采样；60s 缓存 |
| `/api/monthly_summary` | — | 上月汇总（CPU/内存/磁盘/温度 avg-max-min、功耗 kWh/电费、流量差值） |
| `/api/docker` | — | 容器列表（snapshot.docker） |
| `/api/modules` | — | 功能模块列表 |
| `/api/processes` | — | 进程 TOP20 |
| `/api/ports` | — | 端口占用 |
| `/api/apps` | `refresh=1` | 内置应用统计（refresh 强制重扫，60s 冷却） |
| `/api/hardware` | — | CPU/主板/显卡/网卡硬件信息 |
| `/api/raidcard` | — | RAID 卡详情 |
| `/api/disk_health` | — | 盘位健康分级（巡检缓存） |
| `/api/smart` | `dev=sda` | 单盘 SMART 明细（按需） |
| `/api/scan_log` | — | 巡检流水 |
| `/api/vms` | `probe=1` | 虚拟机列表 / virsh 探测 |
| `/api/gpu` | `diag=1` | GPU 实时（诊断模式给更多内部信息） |
| `/api/memory` | — | 内存插槽 SPD |
| `/api/config` | — | 应用配置（含 `listen_port`、`auth_enabled`、版本、数据目录；无敏感值） |
| `/api/ui` | — | 界面配置（面板顺序/隐藏/阈值 thresholds 等） |
| `/api/weather` | — | 天气（外部 API，缓存） |
| `/api/report` | `format=json\|html` | 一键健康报告 |
| `/api/export` | `type=history\|status\|traffic\|power&range=7d` | CSV/JSON 导出（流式） |
| `/api/history/diag` | — | 历史库诊断 |
| `/api/idrac`、`/api/idrac/sel` | — | iDRAC 数据 / SEL 日志 |
| `/api/notify` | — | 告警通知配置 |
| `/api/update/check` | `force=1` | 检查更新 |

### 3.3 POST 端点（与对接基本无关，列出备查）

`/api/auth/password`、`/api/auth/setup`、`/api/auth/logout`、`/api/config/save`、`/api/ui`、`/api/docker/action`（{id, action∈start/restart/stop}）、`/api/vm/action`（{uuid, action}）、`/api/notify/save|test`、`/api/idrac/save|test|refresh`、`/api/disk_scan/now`、`/api/scan_log/clear`、`/api/update/install`、`/api/update/download`。

### 3.4 错误码

| HTTP | error | 场景 |
|---|---|---|
| 401 | `未登录`（JSON，含 `auth_required: true`） | 开启口令且无有效会话 |
| 403 | `host_not_allowed` | 未开口令但经域名访问（Host 校验失败） |
| 403 | `csrf_rejected` | POST 缺 `X-FNM-Request: 1` 或 Content-Type 非 JSON |
| 404 | `not_found` | 未知路径 |
| 405 | `method_not_allowed` | GET 访问 POST-only 接口 |
| 411 | `length_required` | POST 带 Transfer-Encoding |
| 413 | `payload_too_large` | 请求体 > 1 MiB |
| 429 | （`retry_after`） | 登录失败限速 |
| 503 | — | 并发连接超限 / 导出互斥 |
| 500 | — | 服务端异常 |

### 3.5 /api/overview 返回结构示例（节选还原）

```json
{
  "ts": 1769600000.0,
  "interval": 10,
  "cpu": {"percent": 12.3, "per_core": [8.1, 15.2], "load": [0.42, 0.38, 0.35],
          "cores": 4, "threads": 4, "sockets": 1, "logical_cpus": 4,
          "model": "Intel(R) Celeron(R) N5095", "frequency_mhz": 2400},
  "mem": {"total": 8589934592, "used": 3435973836, "free": 123456789,
          "available": 5153960755, "percent": 40.0, "cached": 1234567890,
          "buffers": 123456789, "swap_total": 0, "swap_used": 0,
          "swap_free": 0, "swap_percent": 0.0},
  "disks": [{"device": "/dev/mapper/vd1", "mount": "/vol1", "fs": "btrfs",
             "total": 2000000000000, "used": 800000000000, "free": 1200000000000,
             "percent": 40.0, "standby": false}],
  "disk_total": 40.0, "disk_used": 800000000000, "disk_size": 2000000000000,
  "disk_pools": 1,
  "disks_detail": [{"name": "sda", "model": "WD40EFRX", "vendor": "WD",
                    "size": 4000787030016, "used": 800000000000, "total": 2000000000000,
                    "percent": 40.0, "temp": 38, "mounts": ["/vol1"],
                    "state": "active", "standby": false, "health": "正常",
                    "rpm": "5400", "power_on_hours": 12345, "serial": "WD-XXX",
                    "reallocated_sectors": 0, "pending_sectors": 0,
                    "uncorrectable": 0, "udma_crc": 0, "reallocated_events": 0}],
  "net": [{"iface": "eth0", "rx_bytes": 123456789012, "tx_bytes": 98765432109,
           "rx_rate": 1024000.0, "tx_rate": 512000.0, "rx_packets": 123456,
           "tx_packets": 111111, "rx_drop": 0, "tx_drop": 0, "rx_errs": 0, "tx_errs": 0}],
  "diskio": [{"name": "sda", "read_rate": 1048576.0, "write_rate": 524288.0,
              "read_iops": 12.0, "write_iops": 6.0, "in_progress": 0}],
  "temp": {"cpu": 45.5, "system": 40.0, "disks": [{"name": "sda", "temp": 38}]},
  "power": {"ok": true, "source": "rapl", "total": 18.5, "package": 3.2,
            "core": 2.1, "uncore": 1.1, "dram": 1.5, "base": 8.0,
            "disks_w": 16.0, "disks_active": 2},
  "gpu": [{"vendor": "8086", "type": "igpu", "name": "UHD Graphics", "pci": "0000:00:02.0",
           "temp": null, "util": 5.0, "freq_mhz": 600.0, "power_w": null,
           "mem_used": null, "mem_total": null, "mem_pct": null,
           "util_avail": true, "util_proxy": false, "util_source": "pmu", "shared_memory": true}],
  "memory": {"items": [{"slot": "DIMM0", "vendor": "Samsung", "size": 8589934592,
                        "speed": 3200}], "dual": true},
  "uptime": 123456.7,
  "sensors": {"temps": [{"name": "主板(CPU附近)", "raw": "temp1", "chip": "it8613-isa-0",
                         "type": "board", "value": 40.0, "max": null, "crit": null}],
              "fans": [{"name": "it8613", "rpm": 1640}],
              "volts": [{"name": "in0", "value": 1.344}],
              "available": true, "fans_source": "hwmon"},
  "fans": [],
  "docker": {"available": true, "error": "", "images": 12, "sampled_at": 1769599980.0,
             "containers": [{"id": "a1b2c3d4e5f6", "name": "jellyfin", "image": "jellyfin:latest",
                             "state": "running", "status": "Up 3 days (healthy)",
                             "uptime": "运行 3 天", "status_zh": "运行 3 天 · 健康",
                             "health": "healthy", "exit_code": null,
                             "ports": "8096->8096/tcp", "created": "2026-01-01 00:00:00 +0800 CST",
                             "networks": ["bridge"], "net_host": false,
                             "cpu": "2.35", "mem_usage": "512MiB / 8GiB",
                             "mem_percent": "6.25", "net": "1.2MB / 0.8MB",
                             "net_in_bytes": 1258291, "net_out_bytes": 838861,
                             "net_src": "proc", "rx_rate": 10240.0, "tx_rate": 5120.0}]},
  "modules": [{"id": "smba", "name": "Samba 文件共享", "category": "文件共享",
               "installed": true, "running": true, "method": "systemd:smbd",
               "port": "445", "web": false, "proc": "smbd"}],
  "ports": {"apps": [{"name": "飞牛监控", "appid": "fnmonitor", "port": "8777",
                      "running": true}],
            "docker": [{"name": "jellyfin", "image": "jellyfin", "state": "running",
                        "ports": ["8096->8096/tcp"]}],
            "listeners": [{"port": 8777, "proto": "tcp", "addr": "0.0.0.0", "proc": "python3"}],
            "ts": 1769599980.0},
  "hardware": {"cpu": {"model": "Intel(R) Celeron(R) N5095", "cores": 4, "threads": 4,
                       "sockets": 1, "arch": "x86_64"},
               "board": {"sys_vendor": "...", "board_name": "...", "bios_version": "..."},
               "gpu": ["UHD Graphics"], "net": ["eth0"]},
  "raid": [], "raidcard": {"type": "none", "label": "", "detail": ""},
  "apps": {},
  "idrac_slot_state": "off",
  "system": {"hostname": "fnos", "kernel": "6.x.x", "arch": "x86_64", "uptime": 123456.7,
             "boot_time": 1769476543.3, "cpu_model": "Intel(R) Celeron(R) N5095",
             "fnos_version": "fnOS 1.2.0800", "kernel_version": "...",
             "ips": [{"iface": "eth0", "ipv4": ["192.168.1.10"], "ipv6": ["fd00::a"]}]},
  "config": {"interval": 10, "retention_days": 7, "history_interval": 60,
             "auth_enabled": 0, "auth_session_days": 7, "thresholds": {"cpuWarn": 85},
             "version": "2.20.0", "listen_port": 8777}
}
```

> 注：`config` 字段已脱敏（无口令哈希）；不同机型字段可能为空/null/缺省；`modules`/`ports` 等仅在对应版块未隐藏时采集。

---

## 4. 数据时效与历史

### 4.1 实时值时效

- 采集线程主循环 **interval 默认 10s**（5–60 可配）；重型子项降频：功耗/GPU 10s、传感器/风扇 15s、Docker 30s、硬盘详情/模块/端口 60s、硬件/RAID 120s、内存插槽/RAID 卡 300s、内置应用 600s。
- `/api/overview`、`/api/traffic`、`/api/power_stats` 响应级缓存 **5s**（同一时刻多客户端复用同一份）；`/api/history` 缓存 25s；`/api/daily` 60s。
- **结论：外部轮询间隔低于 10s 不会获得更新的数据。**

### 4.2 历史存储

- 存储：SQLite（WAL 模式）`monitor.db`，位于数据目录（默认 `localdata/`）。表：`metrics(ts, metric, value)`、`daily_summary`（按自然日，30 天）、`monthly_summary`（不受清理影响）、`disk_health`、`disk_health_hist`、`disk_scan_log`、`meta`。
- 采样间隔 `history_interval` 默认 **60s/条**（可配）；保留 `retention_days` 默认 **7 天**（1/3/7/30 可选），每小时清理。
- 历史指标名（写入 metrics 表）：`cpu`、`mem`、`load1`、`disk:<挂载路径>`、`disk_total`、`net_rx:<iface>`、`net_tx:<iface>`（速率 B/s）、`net_rx_bytes:<iface>`、`net_tx_bytes:<iface>`（累计字节）、`net_drop:<iface>`、`net_err:<iface>`、`temp`、`temp_mb`、`power`、`power_est`（估算口径单独标记）、`fan_avg`、`diskio_r:<dev>`、`diskio_w:<dev>`（MB/s）、`ctr_in:<容器名>`、`ctr_out:<容器名>`。
- 历史查询能力：`/api/history`（单指标 + 降采样）、`/api/history?metric=net|net_bytes|diskio`、`/api/export?type=history`（CSV 全量）。**无任意时间窗的批量 API**；HA 侧建议自建 recorder，不依赖 fnmonitor 历史。
- 休眠保护：历史库所在盘休眠时不写不读，采样行内存暂存唤醒后补写；面板轮询触发的 SELECT 走读缓存。**外部程序（包括 HA）直接读 monitor.db 会绕过该保护并可能唤醒休眠盘，强烈不建议。**

---

## 5. 对接评估（面向 HA 集成）

### 5.1 数据够用度

**够用。** CPU、内存、Swap、负载、uptime、文件系统容量、物理盘温度/健康/SMART 计数、网卡速率与累计流量、磁盘 IO、风扇、温度、功耗、GPU、容器状态与资源 —— 一个 `GET /api/overview`（5s 缓存 + gzip）即可全部拿到；补充 `/api/traffic`（今日/本月流量、容器流量排行）、`/api/disk_health`（巡检健康分级）、`/api/power_stats`（kWh/电费）、`/api/processes` 可覆盖面板全部需求。

### 5.2 缺口

1. **无静态 token**：HA 集成习惯 `api_key` 单字段配置；fnmonitor 只有"无鉴权（限 Host）"或"口令→Cookie 会话"两档，集成需实现登录流程（POST 带 `X-FNM-Request: 1` + JSON + 失败限速处理 + 会话过期重登）。
2. **无推送**：只能 REST 轮询。
3. **/api/overview 体量大**：含 disks_detail、docker、ports、modules 等重数据（数十 KB），HA 只为取几个传感器时略浪费（有 5s 缓存与 gzip，实际影响小）。
4. **机器可读性**：部分字段为中文展示值（`status_zh`、`health: "正常"`、`uptime: "运行 3 天"`），集成应以英文键（`state`、`exit_code`、数值字段）为准。
5. **UPS 无数据**（仅服务检测）；需要 UPS 传感器的 HA 集成需另寻方案（如 NUT 集成直连）。
6. 未开口令时的 Host 校验：HA 必须用 **IP**（或局域网主机名）访问，不能走自定义域名/反向代理。

### 5.3 最小改动方案（若 fork 定制，均不实际实施）

| 方案 | 内容 | 改动量 | 风险 | 评价 |
|---|---|---|---|---|
| A. 零改动直连 | HA 轮询 `/api/overview`（+ 按需 `/api/traffic`、`/api/disk_health`、`/api/power_stats`）；不开口令走 IP，或开口令由集成实现登录会话 | **0** | 0 | **推荐**。上游 v2.20.0 直接可用 |
| B. 精简聚合 + 静态 token | server.py 新增 `GET /api/v1/summary`（只输出 CPU/内存/磁盘/网络/温度/功耗/风扇等数值字段）+ config.json 增加 `api_token`，GET 校验 `X-API-Token` 头并豁免 Host 校验 | ~150–250 行 | 低：需注意放在鉴权/Host 检查的既有链路中，避免破坏 CSRF/限速逻辑；fork 后每次上游升级需重打包 FPK | 仅当方案 A 的鉴权/体量问题不可接受时考虑 |
| C. 推送通道 | 增加 SSE `/api/events` 或 MQTT 导出 | 大：新增长连接管理或外部依赖，违背"零依赖"设计 | 中高 | 不推荐 |

---

## 6. 推荐对接方式

**结论：REST 轮询（方案 A）。**

理由：
- fnmonitor 没有任何推送接口，WebSocket/SSE 无从谈起；
- 直读本地数据文件（monitor.db）需 HA 挂载 NAS 共享目录，SQLite 跨机访问有锁/WAL 风险，且会绕过 fnmonitor 的休眠盘保护（外部 SELECT 可唤醒机械硬盘）——明确排除；
- REST 轮询实现最简单、容错好，且服务端已有 5s 响应缓存，轮询成本被大幅摊薄。

**建议 HA 轮询间隔：**
- 实时传感器（`/api/overview`）：**30 秒**（可低至 15s；低于 10s 无意义——采集周期 10s + 服务端缓存 5s）；
- 流量/功耗统计（`/api/traffic`、`/api/power_stats`）：30–60 秒；
- 硬盘健康分级（`/api/disk_health`）：5 分钟（巡检周期 6h）；
- 历史：HA 自建 recorder 长期化，不轮询 fnmonitor 历史 API；
- 鉴权推荐：让用户在 fnmonitor 面板**不开启口令**（HA 用 IP 访问），或集成实现"登录→持有 fnmp_sid→过期自动重登"的会话管理。

---

## 7. HA 集成鉴权与轮询建议（基于代码逐条核实）

> 结论先行：**「HA 局域网 IP 直连 + 密码登录拿 Cookie + REST 轮询（60s+）」方案可行**，无需 fork。

### 7.1 登录接口的访问限制（问题 1）

| 项 | 代码事实 |
|---|---|
| 纯 HTTP | 服务端只有明文 HTTP（`ThreadingHTTPServer`，无 TLS 封装），登录接口天然走 HTTP，无强制跳转 HTTPS |
| 局域网 IP 访问 | **允许**。`_host_ok()`（[L9309](file:///D:/CODE/fnmonitor/app/server.py#L9309)）：只要口令鉴权已启用（`auth_enabled=1` 且已设口令），**任意 Host 直接放行**；仅当未启用口令时才要求 Host 为 IP/localhost/局域网后缀。即开启密码后用 IP 或域名都行，但 HA 用 IP 最稳 |
| Host 白名单 | 无显式白名单表；见上——未开口令时的局域网 Host 校验规则见 §3.1 |
| CSRF | 登录 POST 同样受两道闸约束：`Content-Type: application/json` **且** 请求头 `X-FNM-Request: 1`，缺一返回 403 `csrf_rejected` |
| CSRF token / 验证码 | **均无**。不需要先 GET 首页取任何 token |
| 失败锁定/限速 | `LoginLimiter`（[L266-319](file:///D:/CODE/fnmonitor/app/server.py#L266-L319)）：**单 IP 10 分钟窗口内失败 5 次 → 锁 60 秒，重复触发翻倍（最长 15 分钟）**；全局 60 秒内所有来源共失败 30 次 → 全局锁 30 秒。触发时先返回 429 + `retry_after` 秒。1 小时无失败后等级复位；登录成功即清零该 IP 计数。口令校验由 `BoundedSemaphore(2)` 串行（PBKDF2 24 万次迭代防 CPU 打满） |

### 7.2 会话行为（问题 2）

| 项 | 代码事实 |
|---|---|
| Cookie 名称 | `fnmp_sid`（64 位 hex，sha256(os.urandom(32))），属性 `Path=/; HttpOnly; SameSite=Lax` |
| 有效期 | `Max-Age = auth_session_days × 86400`，默认 **7 天**（可配 1–365）。**固定有效期，无滑动续期**——服务端只在创建时写 `expires`，后续请求不续 |
| 服务重启 | **会话不失效**。会话表持久化于 `sessions.json`（0600，只存 sha256(sid)），`MonitorApp.__init__` 启动时从磁盘加载 |
| 并发会话数 | **无限制**。仅过期会话定期清理；`_revoke_other_sessions`（踢掉其他会话）只在首次设置口令/修改口令时触发，日常登录不踢旧会话 |
| 登出接口 | 有：`POST /api/auth/logout`（同样要求 JSON + `X-FNM-Request: 1`，需带 Cookie），删除服务端会话并 `Set-Cookie: fnmp_sid=; Max-Age=0` |

### 7.3 会话失效判定（问题 3）

- 会话失效（过期/未登录）访问任意 `/api/*`：返回 **HTTP 401 + JSON** `{"ok": false, "error": "未登录", "auth_required": true}`；**不会 302 跳转**（跳转只发生在浏览器打开 `/` 时返回登录页 HTML）。
- HA 端稳定判定：HTTP 状态码 == 401 且 body 含 `auth_required: true` → 重登。注意：**登录失败本身返回 HTTP 200**（body `{"ok": false, "error": "口令错误"}`），不能只看状态码。
- 轻量校验接口：**有**。`GET /api/auth/session`（始终免鉴权）返回 `{"ok": true, "logged_in": true/false, "auth_required": true/false}`，不查库、开销极小。实践建议：直接轮询 `/api/overview`，收到 401 再重登即可，无需额外心跳；重登前可选调 `/api/auth/session` 复核。
- 重登注意限速：见 §7.1，失败 5 次即锁 60 秒——集成应只在 401 后重登一次，失败则退避（如 5 分钟），避免口令错误时打满限速。

### 7.4 登录请求精确形态（问题 4）

```http
POST /api/auth/password HTTP/1.1
Host: <NAS_IP>:8777
Content-Type: application/json
X-FNM-Request: 1

{"password": "<口令>"}
```

- 字段名只有 `password`（≤256 字符；无 username 字段，fnmonitor 不设用户名）。
- 成功：HTTP 200，body `{"ok": true}`，响应头 `Set-Cookie: fnmp_sid=<hex>; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax`。之后所有轮询请求带 `Cookie: fnmp_sid=<hex>`。
- 失败：HTTP 200 + `{"ok": false, "error": "口令错误"}`（可能附 `retry_after`）；被限速时 HTTP 429 + `{"ok": false, "error": "尝试次数过多，请 N 秒后再试", "retry_after": N}`；未设口令时 `{"ok": false, "error": "未配置登录口令"}`。
- **不需要任何前置请求**（无 CSRF token、无验证码）。
- 请求体上限 1 MiB；GET 轮询请求无需任何特殊头。

### 7.5 建议 HA 配置字段与轮询安全性（问题 5）

建议配置流（`async_get_step / config_flow`）：

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `host` | 是 | — | NAS 的局域网 IP（推荐 IP 而非域名：未开口令时域名会 403；开了口令则域名也可） |
| `port` | 是 | 8777 | manifest `service_port`；用户改过端口时以面板「设置 → 服务」显示的实际监听端口为准 |
| `password` | 视情况 | — | fnmonitor 开启登录口令时必填；未开启（`auth_enabled=0`）时留空即可直连。集成可在验证阶段先调 `GET /api/auth/config` 探测 `auth_enabled` / `requires_password` 决定是否要求该字段 |
| `username` | 否 | — | **不需要**，协议无用户名概念，不建议放此字段 |
| `use_ssl` / `verify_ssl` | 否 | false | 服务端无 HTTPS 能力，配了也连不上；建议干脆不提供或固定 false 并在文档说明 |
| `scan_interval` | 否 | 60 | 见下 |

轮询间隔评估：

- **60 秒 / 120 秒均安全**。服务端对该路径无请求频次限制（限速只针对登录失败）；`/api/overview` 有 5 秒响应缓存，60s 轮询每分钟仅 1 次缓存失效重算，对 NAS 负载可忽略。
- **日志零噪音**：访问日志被静默（`log_message` 为空），带有效会话的轮询不写 server.log；只有登录成功/失败会各打一行。
- **会话存活**：轮询不影响有效期（固定过期不滑动），7 天到期重登一次即可；若担心临界，可在集成侧把会话按「剩余 < 1 天则主动重登」处理，或在 HA 侧每 24h 定期重登一次（成功登录不踢旧会话，旧 Cookie 自然过期）。
- 例外提醒：`/api/traffic`、`/api/power_stats` 也有 5s 缓存，同样安全；`/api/disk_health` 只读巡检缓存表，安全；**不要轮询 `/api/smart?dev=`（可能触发 smartctl）与 `/api/apps?refresh=1`（60s 冷却）**。

### 7.6 与 fork 静态 token 方案对比（问题 6）

| | 登录会话方案（现状，推荐） | fork 加静态 token（§5.3 方案 B） |
|---|---|---|
| 可行性 | 立即可用，上游任何版本 | 可行但需维护 fork |
| 改动量 | 0（仅 HA 侧实现登录+重登逻辑，约几十行） | server.py 约 150–250 行 + 重打包双架构 FPK |
| 风险 | 会话 7 天过期需重登（1 次请求）；口令错误触发限速需退避 | 每次上游升级需 rebase/重打包；改鉴权链路若处理不当可能削弱 CSRF/Host 防护 |
| 适用 | 用户接受"在 HA 填一次密码" | 仅当需要 token 级别的机器对机器凭证（如多实例共享）时 |

**结论：登录会话路线对自动化足够稳，无需 fork。** 需要用户注意的限制：① 密码正确性——连续输错 5 次会锁 60 秒起，集成必须做退避；② 会话固定 7 天（默认）无滑动续期，到期自动重登即可；③ fnmonitor 面板本身若被手动登出/改密码，HA 侧会话随之失效，属预期行为；④ 未开口令时 HA 必须用 IP 访问。

---

## 附：对接最小验证清单

1. `curl http://<NAS_IP>:8777/api/overview` —— 无口令时应 200 返回 JSON；403 host_not_allowed 说明用了域名（改用 IP）。
2. `curl http://<NAS_IP>:8777/api/auth/config` —— 确认 `auth_enabled` 与 `requires_password`。
3. 开口令时登录：
   ```bash
   curl -c cookies.txt -X POST http://<NAS_IP>:8777/api/auth/password \
     -H "Content-Type: application/json" -H "X-FNM-Request: 1" \
     -d '{"password":"<口令>"}'
   curl -b cookies.txt http://<NAS_IP>:8777/api/overview
   ```
4. 确认 `ts` 字段推进间隔 ≈ interval 配置值（10s），据此校准轮询。
