# Q飞牛+ (QFN+ · fnOS Monitor)

[![Validate](https://github.com/MisiteQ/qfnplus-ha/actions/workflows/validate.yaml/badge.svg)](https://github.com/MisiteQ/qfnplus-ha/actions/workflows/validate.yaml)

**Q飞牛+** 是一个 Home Assistant 自定义集成，把飞牛 fnOS 上 [fnmonitor](https://github.com/MisiteQ/fnmonitor)（v2.20.0）采集的 NAS 运行状态接入 HA 仪表盘：CPU、内存、Swap、负载、存储卷容量、物理盘温度/健康/SMART 诊断、磁盘 IO、GPU、Docker 容器聚合、网络速率与累计流量、整机温度、风扇、功耗/用电等。纯只读，不调用任何控制接口。

作者：**[MisiteQ](https://github.com/MisiteQ)**（fnmonitor 同作者）· 仓库：[MisiteQ/qfnplus-ha](https://github.com/MisiteQ/qfnplus-ha) · 问题反馈：[Issues](https://github.com/MisiteQ/qfnplus-ha/issues)

## 命名说明

- **项目对外名称：Q飞牛+**；GitHub 仓库：**[MisiteQ/qfnplus-ha](https://github.com/MisiteQ/qfnplus-ha)**（仓库名不允许 `+`，故用合法等价写法）。
- **集成 domain 固定为 `qfnplus`**（HA domain 不允许 `+`，故用合法等价写法；0.1.x 的 `qfnos` 已废弃，两者不可同时安装）。
- 实体 object_id 统一 `qfnplus_` 前缀（如 `sensor.qfnplus_cpu_usage`）；友好名称为「设备名 + 实体名」的中文（如「Q飞牛+ CPU 使用率」「nvme0n1 温度」）。
- 设备改名后**友好名称会随之更新，实体 ID 保持不变**（同一安装内）。

## 安装

### 方式一：HACS（推荐，发布后可用）

1. HACS → 右上角 ⋮ → 自定义存储库
2. 仓库地址填本仓库，类别选 **Integration（集成）**
3. 下载后 **重启 Home Assistant**

### 方式二：手动侧载

1. 从 [Releases](https://github.com/MisiteQ/qfnplus-ha/releases) 下载 `qfnplus-sideload.zip`（或自行打包）：
   ```bash
   python scripts/package_sideload.py   # 生成 dist/qfnplus-sideload.zip
   ```
2. 把 zip 里的 `qfnplus/` 文件夹放入 HA 的 `custom_components` 目录，两种常见途径：
   - **Samba share** 加载项：`\\<HAOS-IP>\config\custom_components\`（目录不存在则新建）
   - **Terminal & SSH / Studio Code Server** 加载项：`/config/custom_components/`
3. 最终目录结构应为 `/config/custom_components/qfnplus/{__init__.py, manifest.json, ...}`
4. **重启 Home Assistant**（设置 → 系统 → 右上角重启）。改代码后必须重启，UI 里的“重新加载”不会重新导入 Python 模块。

## 配置

设置 → 设备与服务 → 添加集成 → 搜索 **Q飞牛+**：

| 字段 | 说明 |
|---|---|
| 主机地址 | fnOS 的局域网 IP（推荐 IP；未开口令时用域名会被 403 拒绝） |
| 端口 | fnmonitor 服务端口，默认 `8777` |
| 登录口令 | fnmonitor 面板开启口令保护时必填；未开启时留空直连 |

选项（集成卡片 → 配置）：**轮询间隔** 30/60/120/300 秒，默认 60 秒。fnmonitor 采集周期 10s + 服务端 5s 缓存，不建议低于 30s。

口令变更后集成会自动发起重新认证（输入新口令即可）；连续输错 5 次会触发 fnmonitor 登录锁定（60 秒起翻倍），集成会提示稍后再试并自动退避。

## 实体清单（0.2.0）

主设备名「Q飞牛+」；每块物理盘一个子设备（盘名，如 `nvme0n1`）。所有实体的 object_id 均为 `qfnplus_` 前缀。**[诊断] = entity_category diagnostic（默认不进主界面，实体列表里默认隐藏在「诊断」分组）；[默认禁用] = 需手动启用。**

### 主设备（静态）

| 实体（友好名） | object_id | 来源字段 | 单位/说明 |
|---|---|---|---|
| CPU 使用率 | `sensor.qfnplus_cpu_usage` | `cpu.percent` | % |
| CPU 负载 1/5/15 分钟 | `sensor.qfnplus_load_1/5/15` | `cpu.load[]` | — |
| 内存已用 / 总量 / 使用率 | `…memory_used / memory_total / memory_percent` | `mem.*` | B（展示 GiB）/ % |
| Swap 已用 / 使用率 | `…swap_used / swap_percent` | `mem.swap_*` | 无 Swap 时 unknown |
| 存储使用率 | `sensor.qfnplus_storage_usage` | `disk_total` | % |
| 运行时长 | `sensor.qfnplus_uptime` | `system.boot_time` | 时间戳 [诊断] |
| CPU 温度 / 主板温度 | `…cpu_temperature / board_temperature` | `temp.*` | °C |
| 实时功耗 | `sensor.qfnplus_power` | `power.total` | W |
| 今日/本月用电 | `…energy_today / energy_month` | `/api/power_stats` | kWh，可进能源面板 |
| 下行/上行速率（整机） | `…net_down_rate / net_up_rate` | `/api/traffic` | B/s（展示 MB/s） |
| CPU 占用最高进程 | `sensor.qfnplus_top_process` | `/api/processes` | [诊断][默认禁用] |

### 主设备（动态）

| 实体 | object_id 后缀 | 来源 | 说明 |
|---|---|---|---|
| 存储卷 {mount} 已用/总容量/使用率 | `volume_{vol1}_used/total/percent` | `disks[]` | 每挂载点一组 |
| {iface} 下行/上行速率 | `nic_{iface}_rx_rate / tx_rate` | `net[]` | 物理口（lo 与虚拟口已过滤） |
| {iface} 累计接收/发送 | `nic_{iface}_total_rx / total_tx` | `net[].rx_bytes/tx_bytes` | 单调累计，适合统计面板 |
| {iface} 累计丢包/错误 | `nic_{iface}_nic_drop / nic_errs` | `net[].rx_drop/tx_drop/rx_errs/tx_errs` | [诊断] |
| 磁盘读/写速率（整机） | `disk_io_read_rate / write_rate` | `diskio[]` 物理盘聚合 | B/s（展示 MB/s）；分区/dm 不重复计数 |
| 磁盘读/写 IOPS（整机） | `disk_io_read_iops / write_iops` | `diskio[]` 聚合 | [诊断] |
| GPU 利用率 / 频率 | `gpu_{名称}_utilization / frequency` | `gpu[].util / freq_mhz` | 无 GPU 不生成；null → unknown |
| 运行中 / 已停止 / 异常容器 | `docker_running / docker_stopped / docker_abnormal` | `docker.containers[]` | 异常 = unhealthy/dead/restarting/非零退出码；Docker 不可用时不生成 |
| 风扇 {fan} 转速 | `fan_{fan}` | `sensors.fans[]` | RPM；无风扇数据不生成 |

### 物理盘子设备（每盘）

| 实体 | object_id 后缀 | 来源 | 说明 |
|---|---|---|---|
| 温度 | `disk_{盘名}_temperature` | `disks_detail[].temp` | °C |
| 健康（problem） | `binary_sensor.qfnplus_disk_{盘名}_health` | `/api/disk_health` grade，回退 `disks_detail[].health` | on = 需关注 |
| 通电时间 | `disk_{盘名}_power_on_hours` | `disks_detail[].power_on_hours` | h [诊断] |
| 重映射 / 待映射 / 不可纠正扇区 | `disk_{盘名}_reallocated_sectors / pending_sectors / uncorrectable` | `disks_detail[]` 同名字段 | [诊断]；NVMe 未采集时 unknown |
| 读速率 / 写速率 | `disk_{盘名}_read_rate / write_rate` | `diskio[]` | [诊断]；IOPS/队列深度在属性里 |

> 容器聚合只做整层计数，不展开单容器（预留以后 opt-in 展开）。

## 排错

1. **打开调试日志**：`configuration.yaml` 加：
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.qfnplus: debug
   ```
   日志位置：设置 → 系统 → 日志。
2. **常见错误**
   - `无法连接`：地址/端口不对、fnmonitor 未运行；`curl http://<NAS_IP>:8777/api/overview` 验证。
   - 403 `host_not_allowed`：未开启口令时必须用 **IP** 访问，不能用域名/反代。
   - `登录口令错误`：检查口令；注意 fnmonitor 登录锁定（5 次失败锁 60 秒起翻倍）。
   - 集成反复重试 setup：首次刷新失败会按退避自动重试，恢复后自动上线。
3. **下载诊断**：设备页 → 下载诊断，输出已自动脱敏口令，便于提 issue。

## 隐私与安全

- 集成只读，绝不调用 fnmonitor 的 POST 动作类接口（docker/vm action 等）。
- 登录口令仅保存在该配置条目（`.storage/core.config_entries`，HA 通用明文落盘行为），绝不写入日志；诊断输出已显式脱敏。
- fnmonitor 仅明文 HTTP，建议仅在局域网内使用。

## 开发

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1      # Windows PowerShell
pip install -r requirements_test.txt
pytest                                              # 测试（config flow / api / coordinator / login contract）
python scripts/package_sideload.py                  # 打侧载包
```

测试 fixture（`tests/fixtures/overview.json`）按 fnmonitor v2.20.0 真实 `/api/overview` 返回结构编写，字段名以 fnmonitor 源码为准，未臆造字段。

## 版本说明

- **0.2.1**：发布前修复——manifest/README 接入真实仓库（MisiteQ/qfnplus-ha）；lovelace 仪表盘实体 ID 与源码对齐（`load_1/5/15`、`memory_percent`、`swap_percent`、`volume_*_percent`、`energy_month`，移除永不生成的 OVS 网卡项）；更换品牌图标；移除测试注释中的真实内网 IP；LICENSE 署名更正为 MisiteQ。
- **0.2.0**
  - 命名统一：对外名称「Q飞牛+」（仓库可名 QFN+），domain `qfnos` → `qfnplus`（**破坏性变更，见迁移章节**）；主设备名固定「Q飞牛+」，磁盘子设备直接用盘名，实体名去重复（不再出现「磁盘 nvme0n1 硬盘 nvme0n1 健康」）。
  - 实体 object_id 统一 `qfnplus_` 前缀。
  - 新增：Docker 容器聚合（运行/停止/异常计数 + problem）、每盘 SMART 诊断（通电时间/重映射/待映射/不可纠正）、磁盘 IO（整机读写速率与 IOPS + 每盘速率）、GPU 利用率/频率、每网卡累计收发字节与丢包/错误；虚拟口过滤补充 OVS（`ovs-system`、`eno1-ovs`）。
- **0.1.1**：修复面板开启口令后登录失败（aiohttp CookieJar 丢弃 IP 主机会话 cookie）。
- **0.1.0**：首个版本，47 个实体。

## 已知限制

- fnmonitor 无稳定机器标识（无序列号/machine-id），实体 unique_id 以配置条目 ID 为前缀；同一主机按 host:port 防重复添加。
- 磁盘子设备以盘名（sda/nvme0n1）标识；每张物理网卡不创建独立设备。
- 今日/本月用电来自 fnmonitor 历史库，依赖 fnmonitor 自身采样连续性。
- SMART 诊断依赖 fnmonitor 巡检缓存（默认 6h 一轮），休眠盘沿用上次活跃值。

## License

MIT
