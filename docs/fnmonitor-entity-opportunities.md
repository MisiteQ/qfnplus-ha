# fnmonitor → HA 可新增实体候选清单（全量盘点）

> 基线：集成 0.1 已暴露约 47 个实体。本文基于 [fnmonitor v2.20.0 源码](file:///D:/CODE/fnmonitor/app/server.py) 只读分析，逐路由清点全部可暴露数据，与现有实体对照后给出「可新增」候选及优先级。
> 分析日期：2026-10-28。配套文档：[fnmonitor-api-spec.md](fnmonitor-api-spec.md)（接口规格与鉴权细节）。

---

## 1. 全量路由清单

所有 `/api/*` GET 共用一套鉴权：未设口令时要求局域网 Host（IP 即可）；设口令后需 `fnmp_sid` Cookie，失效返回 401 JSON。以下 GET 除标注外返回 JSON 对象，无统一 `{ok}` 包裹；`POST` 全部要求 `Content-Type: application/json` + 头 `X-FNM-Request: 1`。

### 1.1 GET（数据读取）

| 路径 | 返回结构要点 | 数据时效 | 用途 |
|---|---|---|---|
| `/api/overview` | §快照：cpu/mem/disks/disks_detail/net/diskio/temp/power/gpu/memory/uptime/sensors/fans/docker/modules/ports/hardware/raid/raidcard/apps/system/config/ts | 采集 10s 主循环，子项降频（docker 30s、sensors 15s、gpu/power 10s、disks_detail 60s…），响应缓存 5s | **总入口，一次拿全** |
| `/api/traffic` | `realtime{rx_rate,tx_rate}`、`ifaces`、`totals{boot/today/month × rx/tx}`、`containers[]` TOP20 流量、`exclude_bridge` | 5s 缓存；today/month 按自然日/月累计 | 流量统计 |
| `/api/power_stats` | `realtime{power_w,source,package,core,dram,base,disks_w,disks_active}`、`energy{today/month/boot kWh}`、`cost{today/month 元,rate}`、`disks[]` 休眠状态 | 5s 缓存 | 功耗/电费 |
| `/api/disk_health` | 表 `disk_health`：dev→serial/model/proto/health/grade(正常/警告/故障)/defects/pending_defects/uncorr/temp_c/power_on_hours/rpm/standby/scan_ts/changed_ts | 巡检缓存（默认 6h 巡检），不触发 smartctl | 盘位健康 |
| `/api/smart?dev=sda` | 单盘 SMART 全量明细（只收内核设备名，5 分钟缓存，不唤醒休眠盘） | 按需 | 深查单盘 |
| `/api/scan_log` | 巡检流水（状态机去重：变化才记录） | 事件型 | 硬盘健康变化历史 |
| `/api/processes` | `processes[]` TOP20：pid/user/cpu(区间差分)/mem%/rss/comm/args(脱敏) | 后台 10s 采样 | 进程 |
| `/api/docker` | snapshot.docker：available/images/containers[]（state/health/exit_code/cpu/mem_usage/mem_percent/net_in/out_bytes/rx_rate/tx_rate/ports/networks） | 30s | 容器 |
| `/api/vms` | `available/installed/kvm/error/vms[]`：state/running/autostart/vcpus/mem/mem_rss/cpu/rd/wr/rx/tx 速率与累计/uptime/agent_connected/disks[](capacity,allocation)/nics+ips | **按需采集**（被轮询才采，4s 缓存） | 虚拟机 |
| `/api/sensors` | temps[]/fans[]/volts[]/available/fans_source | 15s | 传感器明细 |
| `/api/fans` | hwmon 风扇通道枚举 | 15s | 风扇 |
| `/api/gpu` | snapshot.gpu[]（diag=1 给诊断信息） | 10s | GPU |
| `/api/power` | snapshot.power（iDRAC 时含 psu[]、capacity/min/max/avg） | 10s | 功耗快照 |
| `/api/memory` | dmidecode 插槽 SPD：items[]（slot/vendor/size/speed）、dual 通道 | 300s | 硬件 |
| `/api/hardware` | cpu{model,cores,threads,sockets,arch}/board{sys_vendor,board_*,bios_*}/gpu[]/net[] | 120s | 硬件 |
| `/api/raidcard` | storcli/perccli：控制器型号、ROC 温度、VD 状态、物理盘盘位/温度/健康/介质错误 | 300s | RAID 卡 |
| `/api/modules` | 60+ 功能模块：installed/running/method/port/web/proc（含 UPS 检测、Samba、NFS…） | 60s | 服务状态 |
| `/api/ports` | apps[]/docker[]/listeners[]（端口→进程） | 60s | 端口占用 |
| `/api/apps` | 相册/影视/音乐条目统计（refresh=1 强制重扫，60s 冷却） | 600s | 内置应用 |
| `/api/system` | hostname/kernel/arch/uptime/boot_time/cpu_model/fnos_version/kernel_version/ips[] + version/db_size/data_dir | 60s 缓存 | 主机信息 |
| `/api/config` | 应用配置（listen_port/auth_enabled/thresholds/version；脱敏） | 实时 | 配置 |
| `/api/ui` | 面板顺序/隐藏/阈值 | 实时 | 界面配置 |
| `/api/history` | `metric`+`range`(10m/1h/6h/24h/7d/all) → points[[ts,val]]，≤720 点降采样 | 25s 缓存 | 历史序列 |
| `/api/history/diag` | 历史库诊断 | — | 排障 |
| `/api/daily?days=N` | 近 N 天每日 kWh/电费/流量/CPU（has_data 标记） | 60s 缓存 | 日汇总 |
| `/api/monthly_summary` | 上月 CPU/内存/磁盘/温度/功耗/流量汇总 | — | 月报 |
| `/api/export` | `type=history\|status\|traffic\|power&range=` CSV/JSON 流式导出 | — | 批量导出 |
| `/api/report` | 一键健康报告（json/html） | — | 报告 |
| `/api/notify` | 告警通道配置（SMTP/Server酱）+ 已忽略提醒 | — | 告警配置 |
| `/api/idrac`、`/api/idrac/sel` | Dell 带外：温度/风扇/电源冗余、SEL 日志 | 后台轮询 | 仅 Dell 服务器 |
| `/api/update/check` | 检查 fnmonitor 应用更新（force=1 触发联网） | 缓存 | 更新 |
| `/api/weather` | 外部天气 API（缓存） | 缓存 | 面板装饰 |
| `/api/auth/session`、`/api/auth/config` | 登录态/鉴权配置（**始终免鉴权**） | 实时 | 会话探测 |
| 静态 `/login.html`、`/index.html` | 面板页 | — | 浏览器 |

### 1.2 POST（操作类，对 HA 只读集成价值低）

`auth/password|setup|logout`、`docker/action`(start/stop/restart)、`vm/action`、`ui`、`config/save`、`notify/save|test`、`idrac/save|test|refresh`、`disk_scan/now`、`scan_log/clear`、`update/install|download`。
（若将来做按钮/服务：容器重启、触发巡检是仅有的两个安全候选。）

---

## 2. 与现有 47 实体对照分类

**已覆盖（0.1）**：cpu.percent、cpu.load[3]、mem.used/total/percent、swap、disk_total.percent、uptime、temp.cpu、temp.system、power.realtime、energy.today/month、per-volume used/total/percent、per-disk temp+health、per-iface rx_rate/tx_rate。

**不建议暴露**：

| 字段 | 原因 |
|---|---|
| `status_zh`、`uptime:"运行 3 天"`、`state` 之外的中文展示串（容器 status） | 纯文本展示，HA 有 uptime/时间戳更好 |
| `health:"正常"` 中文分级 | 建议映射成枚举（ok/warn/crit）或用 grade |
| `config.*`、`ui.*` | 应用自身配置，与家庭状态无关 |
| `hardware.board.bios_*`、serial 类 | 隐私/纯信息 |
| `/api/history`、`/api/daily`、`/api/monthly_summary` | HA recorder 已长期化，双份历史无意义 |
| `/api/weather` | 非 NAS 状态，HA 有 weather 集成 |
| `/api/apps` 相册条目数 | 纯展示，与家庭自动化无关 |
| `ports.listeners` 全量 | 诊断意义弱，实体膨胀 |
| `disks[].standby` 之外的重复容量口径（disk_used/disk_size 与 per-volume 重复） | 0.1 已有 per-volume |
| 进程全表（/api/processes TOP20） | 用户已默认禁用 top_process；20 个实体纯噪音 |

**明确没有（fnmonitor 代码不支持，需用户知晓）**：定时任务、共享文件夹、系统用户/登录会话、启动项、系统日志、TCP 连接数（无 ss -s/established 采集）、网卡 up/down 与协商速率（无 operstate/ethtool 读取）、UPS 实时电量（仅检测 apcupsd/nut 服务是否运行）、风扇（**代码支持**，见 §3 主题 T8）。

---

## 3. 可新增候选（按主题，含优先级与成本）

> 成本：低=复用已有轮询字段；中=需新增 1 个端点轮询或动态实体逻辑；高=需 fork 或复杂聚合。

### T1 物理盘 SMART 诊断（优先级 高 · 成本 低）
- 来源：`/api/overview` → `disks_detail[]`（60s 刷新）或 `/api/disk_health`（只读巡检缓存）。
- 字段：`power_on_hours`（通电小时）、`reallocated_sectors`（重映射扇区）、`reallocated_events`、`pending_sectors`（待映射）、`uncorrectable`（不可纠正）、`udma_crc`（接口 CRC 错）、`rpm`、`serial`、`health`/`grade`。
- 实体形态：每盘 3–5 个 **diagnostic sensor**（power_on_hours + 4 计数可合并为多 attribute 或分立）+ 1 个 `binary_sensor`「健康警告」（grade=警告/故障）。
- 轮询：复用现有 overview 轮询，**零新增请求**。
- 膨胀：3 盘 × 5 ≈ 15 个（diagnostic 类不进仪表盘，可接受）。

### T2 磁盘 IO 与 IOPS（优先级 高 · 成本 低）
- 来源：`/api/overview` → `diskio[]`（10s）。
- 字段：`read_rate/write_rate`（B/s）、`read_iops/write_iops`、`in_progress`。
- 实体：每盘 2 sensor（读写速率，建议可选 IOPS），聚合一个「总 IO」可选。
- 轮询：复用 overview。膨胀：3 盘 × 2 = 6。

### T3 Docker 容器（优先级 高 · 成本 中）
- 来源：`/api/overview` → `docker`（30s）或 `/api/docker`。
- 字段：`state`（running/exited/paused）、`health`（healthy/unhealthy/none）、`exit_code`、`cpu`（字符串百分比，需 float()）、`mem_usage`/`mem_percent`（字符串，需解析）、`rx_rate/tx_rate`、`net_in_bytes/net_out_bytes`（累计）、`image`、`created`。
- 实体建议（防膨胀）：
  - 聚合层：`running 容器数`、`总容器数`、`unhealthy 容器数`（3 个 sensor + 1 个 binary_sensor「有容器异常」）——**默认只做这层**；
  - 展开层（opt-in 选项）：每容器 `binary_sensor` 运行中 + `sensor` CPU%、内存% + 可选 rx/tx 速率（≈4–6 个/容器）。10 个容器 ≈ 60 实体，**必须**由用户显式选择展开名单。
- 轮询：复用 overview，零新增。容器名建议做 slug 化 entity_id。

### T4 GPU（优先级 高（N5095 有 UHD 核显）· 成本 低）
- 来源：`/api/overview` → `gpu[]`（10s）。
- 字段：`util`（%）、`freq_mhz`、`temp`、`power_w`、`mem_used/mem_total/mem_pct`、`util_avail/util_source`。
- 注意：Intel 核显 `temp/power_w/mem_*` 多为 null（PMU 只给占用与频率），实体要做 null 容错——**只暴露有值的**。
- 实体：每 GPU 2–3 个 sensor。轮询复用 overview。

### T5 网络累计字节与错误丢包（优先级 高 · 成本 低）
- 来源：`/api/overview` → `net[]`。
- 字段：`rx_bytes/tx_bytes`（**单调累计**，自启动）、`rx_drop/tx_drop`、`rx_errs/tx_errs`（累计）、`rx_packets/tx_packets`。
- 实体：每物理口 2 个 sensor（累计字节，state_class=total_increasing）+ 聚合 1 个「丢包/错误」diagnostic。
- 轮询复用 overview。建议**过滤虚拟口**（`ovs-system`、`wlp3s0` 若无流量也建议按名称过滤；代码只排除 lo）——提供可配置 iface 白名单。

### T6 流量统计日/月累计（优先级 中高 · 成本 低）
- 来源：**新增轮询** `/api/traffic`（建议 60s）。
- 字段：`totals.today_rx/tx`、`month_rx/tx`、`boot_rx/tx`（字节）+ `containers[]` 每容器累计流量 TOP20。
- 实体：4 sensor（今日上/下行、本月上/下行）。容器流量展开留 0.3。

### T7 温度探头与电压（优先级 中 · 成本 低）
- 来源：`/api/sensors`（或 overview.sensors）。
- 字段：`temps[]`（chip+name+value+max/crit）、`volts[]`、`fans[]`。
- 实体：超出 cpu/board 的额外温度探头（如芯片组）做 sensor；`volts` 做 **diagnostic**（变化无动作价值）；带 max/crit 的探头可给 max_value。
- 轮询：若做，15–30s 新增轮询 `/api/sensors`；或复用 overview 内嵌 sensors（无额外请求，推荐）。

### T8 风扇（优先级 中（视硬件）· 成本 低）
- **代码完全支持**：15s 采集 `/sys/class/hwmon` + `sensors -j` + iDRAC，字段 `fans[]`/`sensors.fans[]`（RPM）、`fans_source`。
- 用户机器上为空的原因：N5095 主板未向 hwmon 暴露风扇 RPM（super io 不被内核驱动识别），**不是 fnmonitor 缺功能**。另有实验性 `ec_fan_probe`（默认关）。
- 建议：集成做成「检测到才创建」的动态实体；此机器上不会出现，无需用户操作。

### T9 虚拟机（优先级 中 · 成本 中）
- 来源：**新增轮询** `/api/vms`。注意该端点**按需采集**（被请求才跑 virsh domstats，4s 缓存）——HA 轮询会实际驱动 virsh，建议 ≥60s 间隔且仅当 `available=true`。
- 字段：`state/running/autostart/vcpus/cpu/mem/mem_rss/rd_rate/wr_rate/rx_rate/tx_rate/uptime/agent_connected/ips`。
- 实体：每 VM `binary_sensor` 运行中 + 2–3 sensor（CPU/内存）；仅当用户启用虚拟机功能时创建。不用 VM 则零成本。

### T10 系统版本与更新（优先级 中 · 成本 低）
- 来源：`/api/system`（fnos_version、kernel_version）+ `/api/update/check`（**建议 6–24h 一次**，force=1 会联网查源，勿高频）。
- 实体：`sensor` fnOS 版本（diagnostic）、`binary_sensor` fnmonitor 有更新。

### T11 功能模块/服务（优先级 中 · 成本 低）
- 来源：`/api/modules`（overview.modules，60s）。
- 字段：60+ 模块 `installed/running`。
- 实体：**聚合优先**：`binary_sensor`「N+1 项服务异常」+ 数量 sensor；可选白名单展开（如 Samba running）。全展开 = 60+ 实体，禁止。

### T12 告警/事件（优先级 中 · 成本 低）
- fnmonitor 无推送；事件源仅：`/api/disk_health` 的 `changed_ts`、`/api/scan_log`、iDRAC SEL（Dell 专属）。
- 实体：T1 的健康 binary_sensor 已覆盖主场景；可加 `sensor` 最近巡检时间。**无通用事件流**，勿期待「告警中心」实体。

### T13 UPS（优先级 低 · 成本 低）
- 来源：`modules` 中 id=ups：仅 `installed/running`（检测 apcupsd/nut-server）。
- 实体：1 个 `binary_sensor`「UPS 守护进程运行中」。无电量/供电数据——真 UPS 监控请用 HA 的 NUT 集成直连。

### T14 CPU 频率 / 内存 cache/buffers / 功耗分解（优先级 中低 · 成本 低）
- `cpu.frequency_mhz`：1 sensor（overview）。
- `mem.cached/buffers`：2 diagnostic sensor（overview）。
- `power` 分解 package/core/dram/base/disks_w + `source`：diagnostic，仅在 source=rapl/idrac 时有意义；估算口径（estimated）分解值无意义，建议不暴露。

### T15 RAID/存储池（优先级 低（用户无 RAID 卡）· 成本 低）
- `raid[]`（md）、`raidcard`（storcli）、`disk_pools` 数量：用户环境全空。做「检测到才创建」。
- `/api/memory` 插槽 SPD：diagnostic attribute 更合适，不建议实体。

### T16 进程明细（优先级 低 · 成本 低）
- 用户已默认禁用 top_process。最多提供「CPU 最高进程名」1 个 sensor（opt-in），不做 per-process 实体。

---

## 4. 高优先级项落地要点汇总

| 项 | 端点 | 新增轮询 | 建议频率 | 实体形态 | 膨胀控制 |
|---|---|---|---|---|---|
| T1 SMART 诊断 | overview（disks_detail）/ disk_health | 否 | 复用现有（60s 数据） | diagnostic sensor + binary_sensor | 每盘 ≤5，diagnostic 不进仪表盘 |
| T2 磁盘 IO | overview（diskio） | 否 | 复用现有 | sensor（rate+可选 iops） | 每盘 2 |
| T3 容器 | overview（docker） | 否 | 复用现有（30s 数据） | 聚合 sensor/binary + opt-in 展开 | 默认仅聚合 4 个；展开走选项 |
| T4 GPU | overview（gpu） | 否 | 复用现有 | sensor（null 字段跳过） | 每 GPU 2–3 |
| T5 网络累计/丢包 | overview（net） | 否 | 复用现有 | total_increasing sensor + diagnostic | 每口 2 + 聚合 1，iface 白名单 |

全部高优先级项都来自 `/api/overview`，**一次轮询全拿，无需新增请求**——实现成本主要是实体定义与 null/字符串解析（容器 cpu/mem 是带单位的字符串）。

---

## 5. 建议版本范围

**进 0.2（低成本高价值，全部零新增轮询）**
- T1 SMART 诊断（power_on_hours、4 个缺陷计数、健康 binary_sensor）——NAS 最核心的预警能力；
- T2 磁盘 IO/IOPS；
- T3 容器聚合层（运行数/停止数/异常数）+ opt-in 展开框架；
- T4 GPU（util/freq/temp）；
- T5 网络累计字节 + 丢包/错误 diagnostic；
- T8 风扇动态实体（有则显示）；
- T14 CPU 频率、mem cache/buffers。

**留到 0.3（需新增端点或产品决策）**
- T6 流量日/月累计（`/api/traffic`，60s）；
- T9 虚拟机（`/api/vms`，≥60s，检测可用才启用）；
- T11 服务模块聚合 + 白名单展开；
- T10 版本/更新（低频）；
- T7 额外温度探头/电压 diagnostic；
- T3 容器流量明细、容器重启按钮（POST 动作）。

**明确不做**
- 中文文本字段（status_zh 等）、history/daily/monthly（recorder 取代）、weather、apps 条目、ports.listeners 全量、进程全表、UPS 电量（无数据）、连接数/网卡协商速率（fnmonitor 不采集）、共享文件夹/用户/定时任务/启动项/系统日志（fnmonitor 不采集）、RAID 卡（无硬件）。

**需要拍板**：① 容器默认聚合还是展开；② 每盘 SMART 用「多 attribute 单实体」还是「分立实体」；③ 虚拟网卡（ovs-system 等）是否默认过滤。
