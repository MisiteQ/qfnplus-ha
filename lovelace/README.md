# Q飞牛+ NAS 仪表盘（Home Assistant · 原生卡片版）

基于「Q飞牛+」集成 **0.2.0** 的 Lovelace 仪表盘，**只使用 HA 内置卡片，不依赖任何前端插件**。
集成 0.2.0 起 domain 与目录为 `qfnplus`，实体 ID 前缀统一为 `qfnplus_`。

> **实体 ID 已全部按 0.2.0 集成源码重新校正**（`sensor.py` / `binary_sensor.py` 的
> `entity_id_hint` 构造逻辑），不再沿用早期口头映射。
> **你尚未完成迁移、新实体还不存在——迁移完成后请到 开发者工具 → 状态 搜索 `qfnplus_`，
> 对照文末三态清单核对一遍再使用。**

## 文件清单

| 文件 | 说明 |
| --- | --- |
| [qfnplus-dashboard-builtin.yaml](qfnplus-dashboard-builtin.yaml) | 仪表盘本体（原生卡片版，0.2.0 实体命名） |
| [preview.png](preview.png) | 效果预览（HA 深色主题、示例数值） |

## 导入步骤

1. **先完成集成 0.2.0 迁移并确认新实体已出现**（开发者工具 → 状态 搜索 `qfnplus_`）。
2. HA 左侧栏 → **设置 → 仪表盘** → 右下角 **添加仪表盘** → 新建空白仪表盘，标题随意。
3. 打开新仪表盘 → 右上角铅笔进入编辑 → 右上角 **⋮ → 原始编辑器（Raw editor）**。
4. 打开 yaml 文件，全选复制粘贴覆盖原内容 → **保存 → 完成**。
5. 若提示路径已存在：把 YAML 中 `views[0].path`（`qfnplus`）改成别的值即可。

## 布局说明

- **顶部**：markdown 一行摘要（运行时长 · 磁盘健康 · 实时功耗，均带状态守卫）；
- **状态速览**：glance 3 列，3 块盘健康（problem 着色）+ CPU 温度 / 功耗 / 本月用电；
- **三环**：grid 三等分 gauge（needle），CPU 60/85、内存 70/90、存储 80/92 阈值变色；
- **系统区**：glance 3 列（负载×3、内存、Swap）+ entities（Swap 已用、运行时长相对时间）；
- **容器（0.2.0 新增）**：运行中 / 已停止 / 异常计数 + 整体 problem（着色）；
- **存储区**：卷用量 glance（5 卷 3 列）+ 物理磁盘 glance（温度 + 健康着色）+ 卷已用空间 entities；
- **磁盘 IO（0.2.0 新增）**：整机读写速率与 IOPS，glance 2 列；
- **网络区**：网卡速率 glance（**仅物理口 eno1 / wlp3s0** 上下行）+ 24h history-graph + 累计流量 glance（0.2.0 新增）；
- **电源区**：用电 glance 3 项 + 实时功耗 24h history-graph；
- **GPU**：识别名取决于运行时数据，YAML 中为**注释模板**，迁移后按注册表启用；
- 颜色全部使用 HA 状态色/主题默认色，未硬编码刺眼颜色。

> 为什么没有 eno1-ovs / ovs-system：0.2.0 源码过滤所有虚拟网卡（`ovs*` 前缀、
> `*-ovs` 后缀、docker/veth/br- 等），这两张 OVS 虚拟口**永远不会生成实体**，相关卡片已删除。

## 新增存储卷 / 新磁盘后怎么加卡片

集成会自动发现新卷与新盘并生成实体。到 开发者工具 → 状态 搜索 `qfnplus_` 拿到新实体 ID 后：

**新卷**（实体规律 `sensor.qfnplus_volume_<key>_percent / _used / _total`，
key = mount 的 slug，如 `/vol4` → `vol4`、`/` → 回退 `root`）：

- 「存储卷用量」glance 加一项：
  ```yaml
  - entity: sensor.qfnplus_volume_vol4_percent
    name: /vol4
  ```
- 「卷已用空间」entities 加一项：
  ```yaml
  - entity: sensor.qfnplus_volume_vol4_used
    name: /vol4 已用
  ```

**新盘**（实体规律 `sensor.qfnplus_disk_<盘名>_temperature`、
`binary_sensor.qfnplus_disk_<盘名>_health`）：

- 「物理磁盘」glance 各加温度、健康两项：
  ```yaml
  - entity: sensor.qfnplus_disk_nvme2n1_temperature
    name: nvme2n1 温度
  - entity: binary_sensor.qfnplus_disk_nvme2n1_health
    name: nvme2n1 健康
  ```

## 实体 ID 三态清单（以 0.2.0 源码为唯一依据）

### ✅ 源码可确定 —— 静态实体（hint = `sensor.qfnplus_<key>`）

`cpu_usage`、`load_1`、`load_5`、`load_15`、`memory_used`、`memory_total`、
`memory_percent`、`swap_used`、`swap_percent`、`storage_usage`、`uptime`、
`cpu_temperature`、`board_temperature`、`power`、`energy_today`、`energy_month`

- 另有 `net_down_rate` / `net_up_rate`（整机聚合速率）与 `top_process`（默认禁用）未上卡，按需自行添加。

### ✅ 源码可确定 —— 动态实体（命名规则确定）

| 区块 | 实体 |
| --- | --- |
| 磁盘 | `sensor.qfnplus_disk_<盘名>_temperature`、`binary_sensor.qfnplus_disk_<盘名>_health`（本机 nvme0n1 / nvme1n1 / sda） |
| 磁盘 IO | `sensor.qfnplus_disk_io_read_rate/_write_rate/_read_iops/_write_iops` |
| 网卡 | `sensor.qfnplus_nic_eno1_ / _wlp3s0_` 的 `_rx_rate / _tx_rate / _total_rx / _total_tx` |
| 容器 | `sensor.qfnplus_docker_running/_stopped/_abnormal`、`binary_sensor.qfnplus_docker_problem`（Docker available 时才创建） |
| 卷 | `sensor.qfnplus_volume_<key>_percent / _used / _total` |

### ⚠️ 需迁移后核对（取决于运行时数据）

| 项 | 规则 | 待确认点 |
| --- | --- | --- |
| 卷 key 清单 | key = `slug(mount)`，`/` 为空回退 `root` | 本表按 `/`、`/fs`、`/vol1`、`/vol2`、`/vol3` 预写（`volume_root_*`、`volume_fs_*`、`volume_vol1..3_*`）；与实际 mount 不符时按注册表改名 |
| GPU | gkey = `slug(GPU 名称)`，名称为空回退 `gpu<序号>`；`sensor.qfnplus_gpu_<gkey>_utilization / _frequency` | 识别名未知，YAML 中为注释模板；搜 `qfnplus_gpu_` 确认后启用 |

### ❌ 确定不存在（已删除相关卡片）

- `sensor.qfnplus_nic_eno1_ovs_*`、`sensor.qfnplus_nic_ovs_system_*` —— 虚拟网卡被集成过滤，永远不会创建。

## 已知情况

- 曲线仍为内置 history-graph（24h），未引入 apexcharts 等前端资源；
- 预览图数值为示例（迁移前新实体无数据），实际以 HA 实时数据为准。
