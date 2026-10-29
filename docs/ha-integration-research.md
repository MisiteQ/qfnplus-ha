# 「Q飞牛+」Home Assistant 自定义集成开发技术方案（HACS 分发）

> **历史文档注记（2026-10-29）**：本文是 0.1.x 时期的调研档案，文中 `qfnos` domain、`qfnos-ha` 仓库名均为旧命名。现行为：domain `qfnplus`，仓库 [MisiteQ/qfnplus-ha](https://github.com/MisiteQ/qfnplus-ha)。内容仅作背景参考，勿按本文示例复制配置。

> 调研日期：2026-10-28（本文所有官方文档链接的访问日期均为 2026-10-28）
> 版本基线：Home Assistant **2026.10**（2026-10-07 正式发布，2026.10.1 补丁于 2026-10-10 发布；2026.11 已进入 beta，计划 2026-11-04 正式发布）
> 数据源：飞牛 fnOS 上的 **fnmonitor v2.20.0**（Python 标准库 HTTP 服务，默认端口 8777，带登录口令认证）
> 本文档只做技术调研与方案设计，不包含集成实现代码仓库。

---

## 目录

1. [目标与总体架构](#1-目标与总体架构)
2. [版本基线与 2026 年重要变更](#2-版本基线与-2026-年重要变更)
3. [自定义集成最小骨架](#3-自定义集成最小骨架)
4. [配置流程设计（Config Flow / OptionsFlow / Reauth / Reconfigure）](#4-配置流程设计)
5. [数据更新与性能（DataUpdateCoordinator）](#5-数据更新与性能)
6. [实体建模](#6-实体建模)
7. [发布与分发（HACS）](#7-发布与分发hacs)
8. [本地开发与联调](#8-本地开发与联调)
9. [风险与常见坑](#9-风险与常见坑)
10. [分阶段实施建议（MVP → 完整版）](#10-分阶段实施建议mvp--完整版)
11. [参考链接汇总](#11-参考链接汇总)

---

## 1. 目标与总体架构

### 1.1 目标

在 HAOS 上通过 HACS 安装一个自定义集成，把 fnmonitor 采集的飞牛 fnOS 运行状态（CPU / 内存 / 磁盘 / 温度 / 网络 / 功耗 / GPU / Docker 容器 / 虚拟机 / 进程等）接入 HA 仪表盘，支持自动化与长期统计。

### 1.2 数据链路

```
fnOS 主机（fnmonitor, :8777, HTTP+JSON, 登录口令认证）
        ▲  轮询（aiohttp 异步客户端，单一协调器统一拉取）
        │
HA（custom_components/<domain>/）
   ├── config_flow.py   UI 添加集成（host/port/password，password 可选）
   ├── coordinator.py   DataUpdateCoordinator：定时轮询 /api/*
   ├── sensor.py        数值型指标 → sensor 实体
   └── binary_sensor.py 健康类指标 → binary_sensor 实体
```

### 1.3 fnmonitor 数据源（以规格文档为准）

接口、鉴权、字段与轮询约束的权威依据为项目内规格文档：**[docs/fnmonitor-api-spec.md](fnmonitor-api-spec.md)**（基于 fnmonitor v2.20.0 源码静态分析，分析日期 2026-10-28）。

数据源端点分层：

| 层级 | 端点 | 说明 |
|---|---|---|
| **主聚合数据源** | `GET /api/overview` | 总览快照：CPU/内存/磁盘/网络/温度/功耗/GPU/Docker/传感器/系统信息单请求全拿；响应缓存 5s |
| **可选低频数据源** | `GET /api/traffic` | 今日/本月流量统计、容器流量 TOP（5s 缓存；建议 30–60s 轮询） |
| | `GET /api/power_stats` | kWh 累计与电费统计（5s 缓存；建议 30–60s 轮询） |
| | `GET /api/disk_health` | 硬盘巡检健康分级（只读巡检缓存表，巡检周期 6h；建议 300s 轮询） |
| | `GET /api/processes` | 进程 TOP（10s 采样；仅诊断展示，不建议做实体） |
| 辅助端点（免鉴权） | `GET /api/auth/config` | 探测 `auth_enabled`，config flow 预判是否要求口令 |
| | `GET /api/auth/session` | 查询登录态（重登前可选复核） |

关键约束（详见规格文档）：

- **无推送通道**（无 WebSocket/SSE/MQTT）→ `iot_class = local_polling`，纯 REST 轮询。
- 登录态失效判定：**HTTP 401 + JSON `{"auth_required": true}`**；**登录失败本身返回 HTTP 200**（body `{"ok": false, ...}`），不能只看状态码。
- **禁轮询**：`/api/smart?dev=`（按需触发 smartctl）、`/api/apps?refresh=1`（60s 冷却）。
- 采集主循环默认 **10s/轮** + overview 响应缓存 5s → **HA 轮询间隔低于 10s 无意义**（见 5.3）。
- 鉴权路线已定：**密码登录 + Cookie 会话（`fnmp_sid`，7 天固定有效期）**，不修改 fnmonitor、不加长效 Token（见 5.5）。

---

## 2. 版本基线与 2026 年重要变更

| 项目 | 现状（2026-10-28 核实） |
|---|---|
| HA 稳定版 | **2026.10**（2026-10-07 发布）；补丁节奏为每周五一次（2026.10.1 已于 10-10 发布）。来源：home-assistant.io/blog/2026/10/07/release-202610/ |
| 下一版本 | 2026.11 beta 期（Core Beta Release 2026-10-28，正式版 2026-11-04）。来源：contributors.home-assistant.io/events |
| 开发文档迁移 | `developers.home-assistant.io` 的实体/manifest/测试等页面正在整体迁移到 **contributors.home-assistant.io**（部分旧 URL 仍可用，部分 302 到新站首页）。本文引用时优先给出可用路径，并注明新旧两个入口 |
| pytest 测试库 | `pytest-homeassistant-custom-component`（README 徽章显示当前对应 **HA 2026.10.4**，每日随 HA 最新版自动更新） |
| HAOS 侧载 | Samba share / Terminal & SSH / Studio Code Server add-on 访问 `/config`，代码变更后**必须重启 HA** |

### 2.1 与本集成直接相关的 2026 年 API 变更（务必在开工前对齐）

1. **Config entry 函数返回 None**（2026-10-23 官方博客）：core 集成的 `async_setup_entry` / `async_unload_entry` / `async_migrate_entry` 改为返回 `None`，失败一律抛 `ConfigEntryError` / `ConfigEntryNotReady` / `ConfigEntryAuthFailed`。**自定义集成返回布尔值仍然支持（无弃用）**：`True` 成功；`False` 会被当作带通用翻译文案的 `ConfigEntryError`。推荐直接采用新写法（`-> None`），并在测试的 `mock_setup_entry` fixture 中 `return_value=None`。来源：developers.home-assistant.io/blog/2026/10/23/config-entry-methods-return-none/
2. **迁移函数可抛 ConfigEntry 异常**（2026-09-17 博客）：`async_migrate_entry` 不再只能 `return False`。来源：contributors.home-assistant.io/blog/2026/09/17/Use-config-entry-exc-in-migration/
3. **Config entry listener 与 reload 方法组合弃用**（2026-05-07 博客）：2026.6 起弃用，**2026.12 起报错**。影响：不要用"config entry 更新监听 + `async_reload`"来实现选项变更/动态实体，应改用 coordinator 自身的 listener 机制。来源：contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/
4. **新实体生命周期钩子**（2026-10-12）：`Entity.async_prepare_to_add_to_hass`（2026.10+）在实体加入 hass 前执行；另有 `async_entity_id_changed`。来源：developers.home-assistant.io/blog/2026/10/12/new-entity-lifecycle-hooks/
5. **集成分类 categories**（2026-10-20）：manifest 新增 categories 概念（Lighting、Energy 等），官方博客明确说明自定义集成的接入方式（细节以该博客为准，未逐字核实）。来源：contributors.home-assistant.io/blog/2026/10/20/integration-categories/
6. **卸载平台**：2026.11 起 core 提供 `async_unload_platforms_or_raise`（平台卸载失败抛翻译后的 `ConfigEntryError`）；自定义集成可继续用 `async_unload_platforms`。来源：同第 1 条博客。

---

## 3. 自定义集成最小骨架

### 3.1 仓库形态（integration 仓库）

**必须做成 integration 仓库**：HACS 要求集成仓库**只包含一个集成**，且全部集成文件位于仓库根目录的 `custom_components/<domain>/` 下（有官方 OK/Not OK 结构示例）。

建议域名为 **`qfnos`**（示例中统一使用；开工前需确认该 domain 未被现有集成占用，且全小写、仅字母/下划线）。

```
qfnos-ha/                          # GitHub 仓库根
├── hacs.json                      # HACS 元数据（必须）
├── README.md                      # 安装/配置说明（HACS 前台展示）
├── LICENSE
├── .github/
│   ├── workflows/validate.yaml    # hassfest + HACS Action 校验
│   └── FUNDING.yml                # 可选
├── custom_components/
│   └── qfnos/
│       ├── __init__.py            # 集成入口：setup/unload/migrate
│       ├── manifest.json          # 集成清单（version 为自定义集成必填）
│       ├── const.py               # DOMAIN、常量、单位、超时等
│       ├── api.py                 # fnmonitor 异步客户端（aiohttp 封装）
│       ├── coordinator.py         # FnOsDataUpdateCoordinator（DataUpdateCoordinator 子类）
│       ├── config_flow.py         # 用户配置 + OptionsFlow + reauth + reconfigure
│       ├── entity.py              # QFnOsEntity 基类（device_info、可用性等公共逻辑）
│       ├── sensor.py              # sensor 平台
│       ├── binary_sensor.py       # binary_sensor 平台
│       ├── diagnostics.py         # 设备诊断信息下载（可选，加分项）
│       ├── services.yaml          # 自定义动作（如"立即刷新"，可选）
│       ├── quality_scale.yaml     # 质量等级自评清单（可选）
│       ├── strings.json           # 英文翻译源
│       ├── translations/
│       │   ├── en.json
│       │   └── zh-Hans.json       # 简体中文
│       └── brand/                 # 集成自带图标（新流程，2026.3+）
│           └── icon.png
└── tests/                         # pytest 测试（与 custom_components 同级）
    ├── conftest.py
    ├── test_config_flow.py
    ├── test_coordinator.py
    └── test_sensor.py
```

### 3.2 各文件职责与关键代码要点

#### manifest.json（自定义集成必填项）

来源：contributors.home-assistant.io/core/integrations/manifest/、hacs.xyz/docs/publish/integration（访问日期 2026-10-28）

```json
{
  "domain": "qfnos",
  "name": "Q飞牛+ fnOS Monitor",
  "codeowners": ["@YourGitHub"],
  "documentation": "https://github.com/YourGitHub/qfnos-ha",
  "issue_tracker": "https://github.com/YourGitHub/qfnos-ha/issues",
  "iot_class": "local_polling",
  "version": "0.1.0",
  "config_flow": true,
  "integration_type": "hub",
  "requirements": []
}
```

要点：

- 自定义集成**必填**：`domain`（与目录同名）、`name`、`codeowners`、`documentation`（必须指向自己的 https 文档）、`iot_class`、**`version`**（AwesomeVersion 可解析格式，如 `1.2.0`，不强制 SemVer）。
- `iot_class` 取值：`local_polling` / `local_push` / `cloud_polling` / `cloud_push` / `assumed_state` / `calculated`。本项目用 **`local_polling`**（若后续改推送则改 `local_push`）。
- hassfest 强制键顺序：`domain`、`name` 在前，其余按字母序。
- `requirements`：可以带 pip 依赖但必须精确锁版本（`pkg==x.y.z`）；官方建议**只列出 HA 本身尚未包含的依赖**。本项目仅用 `aiohttp`（HA 内置），`requirements` 留空即可，零依赖是最优解。
- `config_flow: true` 声明后需在 dev 环境跑一次 `python3 -m script.hassfest` 激活（core checkout 内）；自定义仓库用 GitHub Action 校验即可。

#### `__init__.py`（入口）

职责：创建 API 客户端与 coordinator、首次刷新、`runtime_data` 存储、平台转发、卸载清理。

```python
"""The Q飞牛+ fnOS integration."""
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: QFnOsConfigEntry) -> None:
    """Set up Q飞牛+ from a config entry."""
    client = FnOsClient(
        base_url=f"http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}",  # fnmonitor 无 HTTPS
        password=entry.data.get(CONF_PASSWORD, ""),  # auth_enabled=0 时留空
        session=async_create_clientsession(     # 专属会话（带 cookie jar），见 5.5
            hass,
            cookie_jar=aiohttp.CookieJar(),
        ),
    )
    coordinator = FnOsDataUpdateCoordinator(hass, entry, client)
    # 首次刷新失败会抛 ConfigEntryNotReady，HA 自动按退避重试 setup
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

async def async_unload_entry(hass: HomeAssistant, entry: QFnOsConfigEntry) -> None:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

要点：

- **类型别名**：`type QFnOsConfigEntry = ConfigEntry[FnOsDataUpdateCoordinator]`（2024.4+ 的 `entry.runtime_data` 模式，官方推荐，替代旧的 `hass.data[DOMAIN][entry_id]`）。来源：developers.home-assistant.io/blog/2024/04/30/store-runtime-data-inside-config-entry/
- 2026.10 官方示例中 `async_setup_entry` 已为 `-> None` 写法（见 2.1 第 1 条）。
- 不要在 `async_setup_entry` 之外做阻塞 I/O；会话鉴权场景**不要用共享 session**，用 `async_create_clientsession` 创建专属会话（原因与生命周期语义见 5.5）。

#### const.py

集中管理：`DOMAIN`、`CONF_*` 键（`CONF_HOST`、`CONF_PORT`、`CONF_PASSWORD`、`CONF_SCAN_INTERVAL`；**无** `CONF_USERNAME`/`CONF_USE_SSL`/`CONF_VERIFY_SSL`——协议不设用户名、服务端无 HTTPS 能力）、默认端口 `8777`、默认轮询间隔 `DEFAULT_SCAN_INTERVAL = 60`、可选间隔 `INTERVAL_CHOICES = (30, 60, 120, 300)`、请求超时 `TIMEOUT = 10`、API 路径常量（`/api/overview` 等）、鉴权常量（登录路径 `/api/auth/password`、请求头 `X-FNM-Request: 1`、Cookie 名 `fnmp_sid`）、单位常量。

#### config_flow.py / sensor.py / coordinator.py / strings.json

分别在 4、5、6 章展开。

#### diagnostics.py（加分项）

实现 `async_get_config_entry_diagnostics(hass, entry)`，返回配置（脱敏）、coordinator 最后更新时间、last_update_success、实体列表等，用于用户在"设置 → 设备 → 下载诊断"一键排障。脱敏必须用官方助手 `homeassistant.helpers.redact.async_redact_data`（已核实 core dev 源码：命中键的值整体替换为 `**REDACTED**`）：

```python
from homeassistant.helpers.redact import async_redact_data

TO_REDACT = {CONF_PASSWORD}  # 口令必须脱敏；host/port 是否隐藏按隐私需求决定

return {
    "entry_data": async_redact_data(entry.data_as_dict, TO_REDACT),
    "options": async_redact_data(entry.options, TO_REDACT),
    "coordinator_last_update_success": coordinator.last_update_success,
    "last_update_time": coordinator.last_update_success_time,
}
```

红线：**诊断输出不得包含登录口令、会话 cookie**。UI 侧口令字段按惯例以 `password` 命名即可，但 diagnostics JSON 没有任何自动打码机制，完全依赖集成自己显式脱敏。

---

## 4. 配置流程设计

官方主文档：developers.home-assistant.io/docs/config_entries_config_flow_handler/（2026-10-28 访问可用；同页含 Options flow、reauth、reconfigure 各节）。

### 4.1 user 步骤（连接信息 + 测试连接）

字段设计（按已确认的数据源对接方式）：

| 字段 | CONF 键 | 类型 / 默认 | 存放 | 说明 |
|---|---|---|---|---|
| 主机地址 | `CONF_HOST` | str，必填 | data | fnOS 的 IP 或主机名 |
| 端口 | `CONF_PORT` | port，默认 `8777` | data | fnmonitor 服务端口 |
| 登录口令 | `CONF_PASSWORD` | str，可选 | data | fnmonitor 开启登录口令时必填；未开启（`auth_enabled=0`）时留空直连。**无 username 字段**（协议不设用户名） |

**存放规则**：凭据（password）与连接标识（host/port）都放 `entry.data`——凭据只在 reauth 时修改，连接标识通过 reconfigure 修改；调优类（轮询间隔等）放 `entry.options`。口令字段按惯例命名为 `password`；config entry 落盘（`.storage/`）为明文 JSON 是 HA 通用行为，diagnostics 侧必须显式脱敏（见 3.2）。

**不提供 use_ssl / verify_ssl 字段**（对前版设计稿的修正）：fnmonitor 服务端只有明文 HTTP、无 TLS 能力（规格 §7.5），配了也连不上——字段直接移除，base_url 固定 `http://`。

流程要点：提交后组装 base_url（固定 `http://{host}:{port}`，fnmonitor 无 HTTPS），执行**测试连接**：`GET /api/auth/config` 探测是否开启口令 → 需要时 `POST /api/auth/password` 登录（必须带 `Content-Type: application/json` 与请求头 `X-FNM-Request: 1`，body 仅 `{"password": "..."}`；**成功与失败都返回 HTTP 200，必须看 JSON 的 `ok` 字段**）→ `GET /api/overview` 验证读取权限。失败按异常类型映射 error key：

| error key | 触发条件 | strings.json UI 提示（中文示例） |
|---|---|---|
| `cannot_connect` | 网络不可达、超时、DNS 失败、拒绝连接；未开口令时经域名访问（403 `host_not_allowed`，提示改用 IP） | 无法连接到 fnmonitor，请检查地址与端口（未开口令时需用 IP 访问） |
| `invalid_auth` | 登录接口返回 HTTP 200 且 JSON `ok:false`（口令错误） | 登录口令错误，请重试 |
| `login_locked` | 登录被限速：HTTP 429 + `retry_after` 秒（单 IP 10 分钟内失败 5 次锁 60 秒，重复触发翻倍、最长 15 分钟） | 尝试次数过多，请 {retry_after} 秒后再试 |
| `unknown` | 其他未预期异常 | 发生未知错误，请查看日志 |

```python
class QFnOsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Q飞牛+."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = async_create_clientsession(
                self.hass,
                cookie_jar=aiohttp.CookieJar(),      # 本集成专属 cookie jar（见 5.5）
            )
            client = FnOsClient(
                base_url=f"http://{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
                password=user_input.get(CONF_PASSWORD, ""),  # auth_enabled=0 时留空直连
                session=session,
            )
            try:
                info = await client.async_test_connection()
                # 步骤：GET /api/auth/config 探测 auth_enabled → 按需 POST /api/auth/password
                # （JSON body 仅 {"password": ...}，头 X-FNM-Request: 1；
                #   成功/失败均返回 HTTP 200，看 JSON 的 ok 字段）→ GET /api/overview
            except FnOsAuthError:
                errors["base"] = "invalid_auth"       # 登录 HTTP 200 且 ok:false
            except FnOsLoginLocked as err:
                errors["base"] = "login_locked"       # HTTP 429 + retry_after（文案带秒数）
            except (FnOsConnectError, TimeoutError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"     # 含未开口令时域名访问的 403 host_not_allowed
            else:
                # fnmonitor 无稳定机器标识（见 4.2）：不用 host 作 unique_id，
                # 改为比对现有条目的 host:port 防重复
                for existing in self._async_current_entries():
                    if (existing.data.get(CONF_HOST), existing.data.get(CONF_PORT)) == (
                            user_input[CONF_HOST], user_input[CONF_PORT]):
                        return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title=info.host_name, data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,             # 推荐 IP：未开口令时域名会 403
            vol.Required(CONF_PORT, default=8777): cv.port,
            vol.Optional(CONF_PASSWORD): str,         # 无 username 字段（协议不设用户名）
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
```

注：config flow 阶段创建的会话没有对应的 config entry，只在 HA 关闭时统一 detach；正式 setup 中创建的会话会在**条目卸载时**自动 detach（见 5.5 的源码语义）。

### 4.2 unique_id 与重复配置防护（硬性规范）

- 每个流程必须 `async_set_unique_id()` + `self._abort_if_unique_id_configured()` 防止同一台 NAS 被重复添加；已存在时 UI 显示 `strings.json` 中 `config.abort.already_configured` 文案。
- **unique_id 来源必须稳定且用户不可改**：官方明确可接受设备序列号、（API 返回的）MAC 地址、烧录标识；**明确不可接受：IP 地址、设备名、用户可改的 hostname、URL**。fnmonitor v2.20.0 未提供序列号 / machine-id 类稳定标识（`/api/overview` 的 `system` 仅含 hostname/kernel/arch/uptime/boot_time/fnos_version/ips，hostname 可变故禁用）→ 实体 unique_id 采用官方"最后手段"（entity registry 文档原文：last resort）：**以 Config Entry ID 为前缀**（`{entry.entry_id}_{指标}_{实例}`，entry_id 终身稳定）。config entry 重复添加防护不靠 unique_id，而是在测试连接成功后**比对现有条目的 host:port**，命中则中止并提示 `already_configured`。
- IP 变化时的正牌做法：发现/重配置流程里 `self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})` 自动更新地址，而不是删了重加。
- unique_id 是字符串，**只需在集成域内唯一**；config entry 的 unique_id 会成为其下实体 unique_id 的天然前缀素材。

### 4.3 Reauth（凭据失效）

- 触发：`async_setup_entry` 或 `DataUpdateCoordinator._async_update_data` 中抛 `ConfigEntryAuthFailed`（仅这两处抛出才会有效触发 reauth 流程），HA 自动进入 `async_step_reauth`，并把 `entry_id`、`unique_id`、`source=SOURCE_REAUTH` 放入流程上下文；`title_placeholders` 自动设为 `{"name": config_entry_title}`。
- reauth 步骤内：`self.hass.config_entries.async_get_entry(entry_id)` 取回条目，仅要求重新输入口令，校验通过后 `self.async_update_reload_and_finish(entry, {**entry.data, CONF_PASSWORD: new})`（旧写法 `self.hass.config_entries.async_update_entry` + `async_reload` 的组合自 2026.6 起与 listener 弃用规则冲突，注意以当前文档为准）。
- fnmonitor 是"登录口令 + 会话"模式（已确定不加长效 Token、不改 fnmonitor）：口令未变则不会触发 reauth；reauth 只在"已存口令本身被服务端拒绝"时发生。

> **会话过期 ≠ 凭据失效**：fnmonitor 会话为 `fnmp_sid` 7 天固定有效期（无滑动续期），且**服务重启不会使会话失效**（sessions.json 持久化，规格 §7.2）；到期 / 面板手动登出 / 改口令导致的会话失效应通过"用已存口令自动重登"自愈（见 5.5），不触发 reauth。只有当**已存口令本身被拒绝**（重登后仍失败）时才抛 `ConfigEntryAuthFailed` 进入 reauth，让用户重新输入口令；重登失败还须遵守登录限速退避（10 分钟内 5 次失败锁 60 秒并翻倍），见 5.5。

### 4.4 Reconfigure（改地址/端口）

`async_step_reconfigure` 是保留步骤名：用户在集成卡片"配置"入口发起。流程内先 `self.config_entry = self._get_reconfigure_entry()`，校验新地址后 `async_update_reload_and_finish`。reauth 与 reconfigure 都会自动带 `title_placeholders`。

### 4.5 OptionsFlow（轮询间隔、模块开关）

- `manifest.json` 无需额外声明；在 `config_flow.py` 中实现 `async_get_options_flow(config_entry)`。
- 推荐选项：轮询间隔（**30 / 60 / 120 / 300 秒，默认 60s**，建议用下拉选择而非自由输入，防止设出过高频率拖垮 fnmonitor）、启用的指标模块（CPU/内存/磁盘/温度/网络/功耗/GPU/容器/VM，复选）、单位偏好（如网速显示 KB/s 或 Mbit/s）。
- Options 数据存 `entry.options`，读取用 `{**entry.data, **entry.options}`；修改 options 后 HA 会自动 reload 该条目（标准行为，无需自己监听）。

### 4.6 错误与翻译

`strings.json` 需覆盖：`config.step.user.data`（host/port/password 字段名）、`config.error`（`cannot_connect`、`invalid_auth`、`login_locked`、`unknown`）、`config.abort`（`already_configured`）、`options.step.init.data`。`translations/zh-Hans.json` 提供中文；本地调试翻译见第 8 章。

---

## 5. 数据更新与性能

官方主文档：developers.home-assistant.io/docs/integration_fetching_data/（最后更新 2026-07-09）。

### 5.1 单一协调器模式（推荐）

fnmonitor 的每个 `/api/*` 端点都是"一次请求喂全部实体"的形态，完美匹配 **Coordinated, single API poll** 模式：

```python
class FnOsDataUpdateCoordinator(DataUpdateCoordinator[FnOsData]):
    """Coordinate polling of fnmonitor."""

    def __init__(self, hass, entry, client):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,                      # 2024+ 必须传
            name="fnmonitor",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            always_update=False,                     # 数据可 __eq__ 比较时避免空转写状态机
        )
        self.client = client

    async def _async_setup(self):
        """首次刷新前一次性加载：磁盘清单/系统信息（设备注册与动态实体发现）；unique_id 用 Config Entry ID 前缀（见 4.2）。"""

    async def _async_update_data(self) -> FnOsData:
        try:
            async with asyncio.timeout(TIMEOUT):     # 10s
                overview = await self.client.async_get_overview()   # 主聚合数据源（单请求喂全部实体）
                # 可选低频数据源按需加挂并节流（见 5.2）：
                # /api/traffic、/api/power_stats（30–60s）、/api/disk_health（300s）
            return FnOsData.from_api(overview)
        except FnOsAuthError as err:
            raise ConfigEntryAuthFailed("fnmonitor 认证失败") from err   # 触发 reauth
        except FnOsLoginLocked as err:
            raise UpdateFailed(f"登录被限速，{err.retry_after} 秒后重试") from err
        except (FnOsConnectError, TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"通信失败: {err}") from err
```

要点（均出自官方 Fetching data / Setup failures 文档）：

- `asyncio.TimeoutError` 与 `aiohttp.ClientError` coordinator 已内置处理，自定义异常才需要显式包装成 `UpdateFailed`。
- `always_update=False`：coordinator 默认每次都回调监听者；当数据可比较（`__eq__`）时设为 `False`，数据未变就不写状态机——对 30s 轮询 NAS 指标特别有价值。
- 首次刷新：`await coordinator.async_config_entry_first_refresh()`，失败自动抛 `ConfigEntryNotReady` → HA 指数退避重试 setup，UI 上显示原因（传了 message 时按 debug 级别记日志，避免刷屏）。**不要在平台的 `async_setup_entry` 里抛 `ConfigEntryNotReady`**（为时已晚，无效）。
- 若 API 有限流退避信号，`ConfigEntryNotReady("...", retry_after=seconds)` 可指定下次重试延迟。
- 并发控制：HA 按集成维护并行请求信号量，`PARALLEL_UPDATES` 一般不需要动（coordinator 场景只有 1 个请求源）。

### 5.2 快慢双节奏（可选优化）

fnmonitor 指标天然分两类：

| 节奏 | 数据 | 建议 |
|---|---|---|
| 快（60s，options 可选 30/60/120/300） | `/api/overview` 全部字段（CPU、内存、磁盘、网速、温度、实时功耗、GPU、容器等） | 主 coordinator |
| 慢（30–60s） | `/api/traffic`（今日/本月流量）、`/api/power_stats`（kWh/电费） | 可选低频数据源；响应同为 5s 缓存，可挂主 coordinator 时间戳节流或独立拉取 |
| 慢（约 300s） | `/api/disk_health`（巡检健康分级，巡检周期 6h） | 可选低频数据源；第二个 coordinator，或主 coordinator 内**时间戳节流**（距上次 <300s 直接复用缓存）或**每 N 次轮询取一次**（N=5 → 60s×5=5min）。推荐时间戳节流，对用户改间隔更稳健 |

**禁轮询**：`/api/smart?dev=`（按需触发 smartctl）、`/api/apps?refresh=1`（60s 冷却）。

两个 coordinator 分别存 `entry.runtime_data`（可用一个简单 dataclass 包装），实体按需挂接，互不拖累。

### 5.3 轮询间隔与"不要拖垮飞牛"

- HA 侧轮询下限理论是 5s（`SCAN_INTERVAL` 最小值），但 fnmonitor 采集主循环默认 10s/轮且 `/api/overview` 有 5s 响应缓存，**轮询间隔低于 10s 不会获得更新的数据**（规格 §4.1）。60/120s 轮询对 NAS 负载可忽略（服务端限速仅针对登录失败）；默认 60s 足够仪表盘观感，30s 档留给对实时性要求高的场景，300s 档适合纯记录用途。
- `entity_registry_enabled_default=False` 用于高频细粒度实体（如每网卡/每容器的累计流量），用户需要时再手动启用，防止 recorder 数据库膨胀。
- fnmonitor 单进程 Python + 零依赖，抗压能力有限：超时 10s、失败即 `UpdateFailed` 进入退避（coordinator 内建），不要在失败路径上立即重试。

### 5.4 推送/订阅方案与取舍（可选，非 MVP）

| 方案 | 做法 | 取舍 |
|---|---|---|
| coordinator 推送模式 | 不传 `update_method`/`update_interval`，fnmonitor 侧有新数据时调 `coordinator.async_set_updated_data(data)` | 需要 fnmonitor 增加向 HA 回调的能力（如 webhook 客户端），侵入 fnmonitor；收益是低延迟+低无效轮询 |
| HA Webhook | 集成用 `homeassistant.components.webhook.async_register` 注册回调地址给 fnmonitor 推 | 依赖 HA 可被 fnOS 主机访问（通常同局域网没问题）；需处理 URL 变化 |
| MQTT discovery | fnmonitor 作为 MQTT 发布者，集成侧做 discovery 或直接让 HA 的 MQTT 集成自动发现 | 最"HA 原生"，但要求用户额外装 Mosquitto add-on，部署复杂度最高 |

**结论**：MVP 与完整版都用纯轮询；推送仅作为远期方向，且优先做 fnmonitor→HA webhook 单向推送（改动最小）。

### 5.5 会话鉴权（Cookie 会话）与失效恢复

fnmonitor 采用"登录口令 + 会话"认证（登录接口签发会话 cookie）。鉴权实现要点如下。

**复用 HA 共享会话？不。** `async_get_clientsession(hass)` 返回的共享 ClientSession 按固定键（verify_ssl/family/ssl_cipher）在**所有集成间复用同一个全局 cookie jar**（已核实 core dev 源码 `_make_key` / `DATA_CLIENTSESSION`），会话 cookie 会互相污染。应使用 `async_create_clientsession` 创建**本集成专属会话**：

```python
# __init__.py 的 async_setup_entry 内创建（必须在 setup 上下文中，HA 会自动绑定卸载清理）
session = async_create_clientsession(
    hass,
    cookie_jar=aiohttp.CookieJar(),   # 专属 jar：只存 fnmonitor 的会话 cookie（fnmp_sid）
)
```

生命周期语义（源码 `helpers/aiohttp_client.py`，2026-10-28 核实）：`async_create_clientsession(..., auto_cleanup=True)` 默认在 **HA 停止时**以及**在 config entry setup 期间创建、该条目卸载时**自动 `detach()`。因此：不要自己调 `session.close()`（HA 会发 warning"关闭了 Home Assistant 的 aiohttp 会话"）；也不要用 `auto_cleanup=False`（需自己管理 detach）。

**cookie 生命周期跨轮询保持**：aiohttp 自动把登录响应的 `Set-Cookie`（`fnmp_sid`，HttpOnly/SameSite=Lax）存入专属 jar，后续同主机请求自动携带；coordinator 以同一 session/jar 做每轮 60s 轮询，会话在 **7 天固定有效期内持续可用**（无滑动续期、服务重启也不失效），到期后走下述重登路径自愈。集成卸载/重载时旧会话随条目自动 detach，新 setup 创建全新会话，互不残留。

**登录态失效：单飞重登（single-flight）+ 一次性重试**：

```python
class FnOsClient:
    def __init__(self, ...):
        self._relogin_lock = asyncio.Lock()

    async def _async_request(self, path: str) -> Any:
        resp = await self._do_request(path)
        if resp.status == 401:                  # 登录态失效（body 含 auth_required:true）
            await self._async_relogin()          # 用已存口令重登一次
            resp = await self._do_request(path)  # 只重试一次
        return await self._parse(resp)

    async def _async_relogin(self) -> None:
        # 单飞：多个并发请求同时收到 401 时，仅第一个协程真正执行登录，
        # 其余等锁后直接复用新会话，避免并发重复登录触发 fnmonitor 限流
        async with self._relogin_lock:
            resp = await self._session.post(
                f"{self._base_url}/api/auth/password",
                headers={"Content-Type": "application/json", "X-FNM-Request": "1"},
                json={"password": self._password},
            )
            data = await resp.json()
            if resp.status == 429:               # 触发锁定：60s 起翻倍，最长 15 分钟
                raise FnOsLoginLocked(retry_after=data["retry_after"])
            if not data.get("ok"):               # 登录失败返回 HTTP 200，必须看 JSON
                raise FnOsAuthError(data.get("error", "口令错误"))
```

**重登失败的退避（防打满限速：10 分钟内 5 次失败锁 60 秒并翻倍，规格 §7.1）**：

- **口令错误**（重登返回 `ok:false`）：进入"认证失败"状态，至少 **5 分钟内不再自动重登**（轮询照常进行、实体置 unavailable），期间仅记 debug 日志；连续失败则指数拉长（如 5 → 15 → 30 分钟），用户经 reauth 修复口令后清除。
- **限速锁定**（429）：按响应的 `retry_after` 秒数退避并叠加缓冲（如 +5s），到点后允许重试一次；不视为凭据失效。
- config flow 侧同理：连续输错口令时把 `login_locked` 错误连同剩余秒数直接展示在 UI，引导用户等待而不是反复提交。

**会话失效（7 天固定到期 / 面板手动登出 / 修改口令）的恢复路径与日志级别**（服务重启**不会**使会话失效，sessions.json 持久化，规格 §7.2）：

| 阶段 | 行为 | 日志 |
|---|---|---|
| 首次 401 | 用已存口令自动重登（单飞），重试一次原请求 | `debug`：会话失效，正在重新登录 |
| 重登成功 | 本轮拉数完成，数据流无感恢复，不触发 reauth、实体不进 unavailable | `debug`：重新登录成功 |
| 重登后仍失败（`ok:false`） | 进入退避（≥5 分钟，见上），期间轮询继续、实体不可用；退避到期重试重登，仍失败则抛 `FnOsAuthError` → coordinator 转 `ConfigEntryAuthFailed` → HA 自动发起 reauth | `warning`：口令可能已变更，需要重新认证 |
| 重登遇 429 | 按 `retry_after` 退避后重试一次；不视为凭据失效 | `warning`：登录被限速，N 秒后重试 |
| 连接层失败（NAS 断网/重启瞬间的拒绝/超时） | 抛 `UpdateFailed` → coordinator 指数退避重试，实体进入 `unavailable`，恢复后自动写回 | 首次 `warning` + 后续退避按 `debug`（由 coordinator 内建日志逻辑控制） |

另：**集成保持只读**，绝不调用 fnmonitor 的 POST 动作类接口（docker/vm action 等，见规格文档 §3.3）。

---

## 6. 实体建模

官方文档：Entity 通用属性 developers.home-assistant.io/docs/core/entity/；Sensor developers.home-assistant.io/docs/core/entity/sensor/；binary_sensor 设备类（用户侧）home-assistant.io/integrations/binary_sensor/（均 2026-10-28 访问）。

### 6.1 设备与实体组织

- **主设备**：fnOS 主机一台。`DeviceInfo(identifiers={(DOMAIN, entry.unique_id)}, name="Q飞牛 NAS", manufacturer="飞牛", model=机型, sw_version=fnOS 版本, via_device=None)`。
- **子设备**：每块物理磁盘一个 device（`identifiers={(DOMAIN, disk_serial)}`，`via_device=主设备`），其下挂温度、容量、SMART 健康等实体。磁盘拔出时**不删实体**，置 `available=False`。
- Docker 容器/VM 不建议各建设备（数量失控），归属主设备、实体名带容器名（`translation_placeholders` 注入）。

### 6.2 通用规范（硬性）

- `has_entity_name = True`（新集成强制）：`friendly_name = 设备名 + 实体名`，`entity_id = sensor.<设备名>_<实体名>`（snake_case）。实体名只描述数据点本身（如 "CPU 使用率"），不含设备名/平台类型。
- 实体名**不要硬编码英文字符串**，用 `translation_key` + `strings.json` 的 `entity.sensor.<key>.name`；动态名（磁盘/网卡/容器）用 `translation_placeholders` 传变量。注意官方提醒：翻译名在**实体创建时按后端语言**生成并固化，之后改语言不影响旧实体。
- `unique_id` 必须**终身稳定**：建议 `{entry.entry_id}_{指标}_{实例标识}`（如 `{entry_id}_disk_{serial}_temperature`；fnmonitor 无机器标识，见 4.2）。官方要求：单设备多实体时组合 `{unique_id}-{sensor_type}`；不要包含 domain 和平台名。**一旦发布绝不能改**（改了 = 用户所有自定义配置丢失 + 孤儿实体）。
- 属性尽量走 `_attr_*` 类属性/实例属性或 EntityDescription，property 里禁止 I/O；少用 `extra_state_attributes`（官方明确警告：高频变化的属性会撑爆数据库，宁可拆成独立 sensor）。

### 6.3 指标 → 实体映射表

（"API"列以规格文档 §3.5 的 `/api/overview` 返回结构为准，避免臆造；标注**「动态」**的行需按数据驱动发现（见 6.4）——磁盘、网卡、温度探头、风扇、电压、GPU、容器数量因机而异。优先取英文键与数值字段，避开中文展示值（`status_zh`、`health:"正常"`、`uptime:"运行 3 天"`））

| fnmonitor 数据（字段名以规格 §3.5 为准） | 实体（平台） | device_class | state_class | native_unit | 备注 |
|---|---|---|---|---|---|
| `cpu.percent`（`/api/overview`） | sensor | 无 | `measurement` | `%` | `suggested_display_precision=1` |
| `cpu.load[3]`（1/5/15 分钟） | sensor | 无 | `measurement` | 无 | 官方明确：CPU load 不用 `TOTAL`；另有 `cpu.per_core[]` 可选 |
| `mem.used` / `mem.total` | sensor | `data_size` | `measurement` | `GiB` | 字节值，HA 自动做单位换算 |
| `mem.percent`（另有 `swap_percent`） | sensor | 无 | `measurement` | `%` | |
| `disks[].used/total/percent`（按 `mount`） | sensor（子设备=卷/挂载点） | `data_size` / 无 | `measurement` | `GiB`/`TiB` / `%` | **动态实体**（挂载点维度，见 6.4） |
| `disks_detail[].temp`（按 `serial`/`name`） | sensor（磁盘子设备） | `temperature` | `measurement` | `°C` | **动态实体**；随用户单位系统自动换算 |
| `disks_detail[].health`（中文值"正常/警告/故障"）+ SMART 计数 | **binary_sensor** | `problem` | — | — | **动态实体**；中文健康值需映射为布尔，`on`=有问题；计数（`reallocated_sectors`/`pending_sectors` 等）可作 diagnostic sensor |
| `disks_detail[].standby` / `disks[].standby` | binary_sensor | 无（或自定义） | — | — | `on`=休眠；**动态实体** |
| `/api/disk_health` 的 `grade`/`defects`（巡检分级） | binary_sensor | `problem` | — | — | **可选低频数据源**（300s）；只读巡检缓存表，不触发 smartctl |
| `net[].rx_rate/tx_rate`（按 `iface`） | sensor | `data_rate` | `measurement` | `B/s` 系 | **动态实体**；`DATA_RATE` 支持单位含 bit/s、kB/s、MiB/s 等；用 `suggested_unit_of_measurement` 控制默认展示 |
| `net[].rx_bytes/tx_bytes` | sensor | `data_volume` | `total_increasing` | `GB` 系 | 字节累计，fnOS 重启清零 → `total_increasing` 正确处理重置；**动态实体** |
| `/api/traffic` 的 `totals.today_rx/today_tx/month_rx/month_tx` | sensor | `data_volume` | `total` | — | **可选低频数据源**（30–60s）；自然日/月清零，非单调 |
| `temp.cpu` / `temp.system`；`sensors.temps[]`（多探头） | sensor | `temperature` | `measurement` | `°C` | **动态实体**（`sensors.temps[]` 按探头生成，建议默认禁用） |
| `sensors.fans[].rpm`（另有 hwmon `fans[]`） | sensor | 无 | `measurement` | `RPM` | **动态实体**；无现成 device_class，自定义单位即可 |
| `sensors.volts[].value` | sensor | `voltage` | `measurement` | `V` | **动态实体** |
| `power.total`（`source ∈ idrac/rapl/estimated`） | sensor | `power` | `measurement` | `W` | overview 内含 power 快照，无需单独轮询 `/api/power` |
| `/api/power_stats` 的 `energy.today_kwh/month_kwh` | sensor | `energy` | `total` | `kWh` | **可选低频数据源**（30–60s）；自然日/月清零非单调 → 用 `total`；可进能源面板 |
| `gpu[].util` / `.temp`（可多卡） | sensor | 无 / `temperature` | `measurement` | `%` / `°C` | **动态实体**；字段可能为 null |
| `system.boot_time`（epoch 秒） | sensor | `timestamp` | — | — | `native_value` 返回带时区的 `datetime`；`entity_category=DIAGNOSTIC`；另有顶层 `uptime`（秒）可换算 |
| `system.fnos_version`、`system.ips[]` | sensor | 无 | — | — | `entity_category=DIAGNOSTIC`；**接口无 MAC/序列号字段** |
| 更新可用（fnmonitor 自身，`/api/update/check`） | binary_sensor | `update` | — | — | 远期可选（Phase 3） |
| `docker.containers[].state`（英文键） | binary_sensor | `running` | — | — | **动态实体**；每容器一个，`translation_placeholders` 注入 `name`；避开中文 `status_zh`/`uptime` 展示值 |
| `/api/vms` 的 `vms[]`（virsh，按需采集 4s 缓存） | binary_sensor | `running` | — | — | **动态实体**；按需采集端点，默认禁用 |
| 进程 TOP `/api/processes` | **不建议做实体** | — | — | — | 文本型/高频；可作为主设备 diagnostic 属性或独立 diagnostic sensor（"最高 CPU 进程名"，`entity_category=DIAGNOSTIC`，`entity_registry_enabled_default=False`） |
| UPS | — | — | — | — | 规格 §2.3 确认**无 UPS 实时数据**（仅在 `modules[]` 检测 apcupsd/nut 是否安装运行）；如需 UPS 由 HA 侧 NUT 集成承担 |

`state_class` 选择规则（官方 caution）：绝大多数情况用 `MEASUREMENT`；可增可减的累计量用 `TOTAL`（如剩余容量这类"绝对值更有意义"的量官方明确不要用 `TOTAL`，用 `MEASUREMENT`）；单调递增且会清零的用 `TOTAL_INCREASING`（递减被解释为新一轮计量开始）。设置 `state_class` 后自动进入 HA 长期统计。

### 6.4 动态实体发现（磁盘 / 网卡 / 容器）

模式：**setup 时按首份数据建实体 + coordinator listener 增量补充**。

```python
# sensor.py 内
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def async_add_new_entities() -> None:
        new = [d for d in coordinator.data.disks if d.id not in known]
        for d in new:
            known.add(d.id)
        async_add_entities(
            DiskTemperatureSensor(coordinator, d) for d in new
        )

    async_add_new_entities()                       # 初始一批
    config_entry.async_on_unload(                  # 卸载时自动移除 listener
        coordinator.async_add_listener(async_add_new_entities))
```

注意（2026.6 弃用规则）：**不要**用"config entry update listener + `async_reload`"来响应选项或数据变化（2026.12 起报错）；新增/移除实体一律走 coordinator listener + `async_add_entities`。磁盘消失时不删实体、置不可用，避免 unique_id 漂移。

### 6.5 实体裁剪与多语言小结

- 高频或边缘实体默认禁用（`entity_registry_enabled_default=False`）：网卡级累计流量、每容器状态、进程诊断。
- `entity_category=DIAGNOSTIC`：uptime、版本、IP/MAC、探针元数据；`CONFIG` 留给未来可能的开关实体。
- 翻译三件套：`strings.json`（英文源）→ `translations/en.json`、`translations/zh-Hans.json`；覆盖 config/options 流程文案 + `entity.sensor` / `entity.binary_sensor` 的实体名与状态值（如磁盘休眠态 on/off 的语义化文案）。自定义集成的翻译不会进 Lokalise，需自行维护。

---

## 7. 发布与分发（HACS）

来源：hacs.xyz/docs/publish/start、/publish/integration、/publish/action、/publish/include；contributors.home-assistant.io/core/integrations/manifest/（2026-10-28 访问）。

### 7.1 仓库硬性要求

| 项 | 要求 |
|---|---|
| 仓库形态 | **integration 仓库**：单一集成，文件在根目录 `custom_components/<domain>/`（官方有 OK/Not OK 结构图） |
| `hacs.json` | 必填仅 `name`；可选 `render_readme`、`zip_release`（须配 `filename`）、`hide_default_branch`、`country`、`homeassistant`（最低 HA 版本）、`hacs`（最低 HACS 版本）等；**`domains` 字段已废弃**（现行字段表中不存在） |
| 版本 | GitHub **Release 非强制**（"preferred but not required"；无 release 则用默认分支）；有 release 时取最新 release 的 tag 作版本号，**只打 tag 不发 release 无效**。tag 建议语义化版本（与 manifest `version` 一致） |
| README | 必须包含使用说明；HACS 前台默认渲染 README（`render_readme: true` 可用自定义渲染）。`info.md` 为旧机制，现行文档未单列要求（未核实细节） |

最小 `hacs.json`：

```json
{
  "name": "Q飞牛+ fnOS Monitor",
  "render_readme": true,
  "homeassistant": "2026.10.0"
}
```

### 7.2 CI 校验（hassfest + HACS Action）

`.github/workflows/validate.yaml`：

```yaml
name: Validate
on: [push, pull_request]
jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

- **hassfest** 校验 manifest 键序与完整性、`strings.json`、`services.yaml`、`quality_scale.yaml` 一致性等；未知 manifest category 对自定义集成仅警告，但 **HACS Action 会因此失败**。
- **HACS Action** 可 `with.ignore` 跳过项：archived、brands、description、hacsjson、images、information、issues、license、topics。

### 7.3 图标（brand）

两条路线（2026 年现状）：

- **新流程（2026.3 起，推荐）**：在 `custom_components/<domain>/brand/` 内放 `icon.png`（至少此项），本地图片优先于 brands 仓库。
- **旧流程**：向 `home-assistant/brands` 仓库提 PR，目录 `custom_integrations/<domain>/`，可放 `icon.png`（256×256，@2x 512×512）、`logo.png`（最短边 128–256px）及 `dark_*` 变体；不允许 symlink、不得使用 HA 官方品牌图。

HACS 的 brands 检查：先查仓库内 `brand/`，否则回退 `home-assistant/brands`。

### 7.4 质量等级（Quality Scale）

- 现行四级 Bronze → Silver → Gold → Platinum，写入 manifest `quality_scale` 键；core 新集成必须 Bronze 起。**HACS 对自定义集成不强制质量等级**，自评是加分项：对照 contributors.home-assistant.io/core/quality-scale/ 的规则清单逐条自查（典型规则：`entity-unavailable`、`action-exceptions`、`config-flow`、`diagnostics`、`reauthentication` 等），可附 `quality_scale.yaml` 记录达成情况。

### 7.5 安装侧体验（HACS 用户视角）

用户在 HACS → 自定义存储库（Custom repositories）→ 填仓库地址、类别选 Integration → 下载 → 重启 HA → 设置 → 设备与服务 → 添加集成。README 需写清这三步与"找不到集成时重启"的提示。

---

## 8. 本地开发与联调

### 8.1 测试环境（pytest + pytest-homeassistant-custom-component）

来源：github.com/MatthewFlamm/pytest-homeassistant-custom-component（2026-10-28，当前对应 HA 2026.10.4）。

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pytest pytest-asyncio pytest-homeassistant-custom-component pytest-cov
```

```ini
# pytest.ini / setup.cfg
[pytest]
asyncio_mode = auto
```

```python
# tests/conftest.py
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """启用 custom_components 加载（该 fixture 来自插件）。"""

@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """阻断真实 setup。注意 2026.10+ 官方写法 return_value=None。"""
    with patch(
        "custom_components.qfnos.async_setup_entry", return_value=None
    ) as mock_setup_entry:
        yield mock_setup_entry
```

测试要点（官方 tests 文档：contributors.home-assistant.io/core/development/tests/）：

- **Config flow**：`hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})` → `async_configure` 提交表单；patch 客户端 `async_validate` 的成功/超时/认证失败三条路径；重复添加走 `MockConfigEntry`（预置相同 unique_id）断言 `abort reason == already_configured`。
- **Coordinator / 平台**：`MockConfigEntry(...).add_to_hass(hass)` + `hass.config_entries.async_setup(entry_id)` + `await hass.async_block_till_done()`；用 `async_fire_time_changed(hass, utcnow() + timedelta(seconds=30))` 推进时间触发轮询；`hass.states.get("sensor.xxx")` 断言状态；时间相关用 `freezer` fixture 而非手 patch datetime。
- 版本对应关系：`pytest-homeassistant-custom-component` 的 minor 版本变化代表抽取逻辑变化，patch 版本跟随 HA——CI 里把版本钉在对应 HA 2026.10 的版本上。

### 8.2 桌面全量调试（可选）

用 HA devcontainer / 官方开发环境直接跑 `hass -c config`，可本地完整复现 setup 流程（含 config flow UI）；轻量场景直接进 8.3 的真机联调。

### 8.3 HAOS 真机侧载步骤

来源：home-assistant.io/common-tasks/os/、home-assistant.io/docs/configuration/、home-assistant.io/integrations/logger/（2026-10-28）。

1. **访问 /config**：装官方 **Samba share** add-on（设置 → 加载项 → Samba share，配置账号后 `\\NAS-IP\config`）；或 **Studio Code Server**（VS Code）add-on 直接在机内编辑；或 **Terminal & SSH / Advanced SSH & Web Terminal**。
2. **放置代码**：拷贝整个 `custom_components/qfnos/` 到 `/config/custom_components/qfnos/`（目录不存在则创建）。
3. **生效**：**必须重启 HA**（设置 → 系统 → 右上角重启）。UI 里的 Reload / `homeassistant.reload_config_entry` 只重跑 `async_setup_entry`，不会重新导入已加载的 Python 模块——**改了代码必须重启**（此点为社区公认实践，官方文档未明文）。
4. **看日志**：设置 → 系统 → 日志；`configuration.yaml` 增加：
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.qfnos: debug
   ```
5. **快速验证**：开发者工具 → 状态/服务；实体注册表（设置 → 设备与服务 → 实体）检查 unique_id/设备归属；下载诊断（如实现了 diagnostics.py）验证排障输出。
6. **hassfest 本地校验**（可选）：core checkout 内 `python3 -m script.hassfest --integration-path ...`；日常以 GitHub Action 为准。

---

## 9. 风险与常见坑

| # | 风险/坑 | 说明与对策 |
|---|---|---|
| 1 | **内部 API 无兼容承诺** | HA 自定义集成只能依赖公开文档接口；2026 年文档站整体迁移（developers → contributors），`entity_registry_index` 等页面 6 月才更新。对策：CI 钉住目标 HA 版本跑测试；每次 HA 月度升级跑一遍 beta 回归 |
| 2 | **setup/unload 返回值语义变化** | 2026.11 起 core 强制 `-> None`；custom 返回 `False` 变成 `ConfigEntryError`（通用文案）。新代码直接抛具体异常并返回 `None` |
| 3 | **config entry listener + reload 弃用** | 2026.6 弃用、2026.12 报错。动态实体/选项变更改用 coordinator listener，不要自己 reload |
| 4 | **unique_id 一旦发布不可变** | 变更 = 用户改名/禁用/区域等设置全丢 + 孤儿实体。开工前定稿命名规则（`{entry.entry_id}_{指标}_{实例}`），磁盘用序列号（`disks_detail[].serial`）不用槽位号 |
| 5 | **IP/hostname 作 unique_id** | 官方明确禁止（IP 变化即断链）。fnmonitor 无序列号/machine-id 字段 → 用 Config Entry ID 作实体 unique_id 前缀（官方 last resort，见 4.2）；未开口令时 HA 必须用 IP 访问（域名会 403 `host_not_allowed`）；IP 变更走 reconfigure 的 `updates={CONF_HOST: ...}` |
| 6 | **阻塞事件循环** | `requests`、`time.sleep`、同步文件 I/O 在事件循环中会被 HA 检测并告警（0.109 起内建检测）。全链路 aiohttp + asyncio.timeout |
| 7 | **实体/recorder 膨胀** | 高频实体 + 频繁变化的 `extra_state_attributes` 会撑大数据库。默认禁用细粒度实体；属性瘦身；`state_class` 正确（错误设置会导致统计失真） |
| 8 | **单位与时区** | `native_value` 必须是 `native_unit` 下的原始值，换算交给 HA（`temperature`/`data_size` 自动跟随用户单位系统）；`uptime`/`timestamp` 类必须返回**带时区**的 `datetime`；`data_volume` 计数器清零用 `total_increasing` |
| 9 | **fnmonitor 会话认证** | 会话失效（`fnmp_sid` 7 天固定到期/登出/改口令；**服务重启不失效**）先用已存口令**单飞重登一次**自愈（仅 debug 日志），重登仍失败才抛 `ConfigEntryAuthFailed` 触发 reauth（见 5.5）。**登录失败返回 HTTP 200**，必须看 JSON `ok` 字段；口令错误重登需退避 ≥5 分钟、429 按 `retry_after` 退避（10 分钟内 5 次失败锁 60 秒并翻倍），防止打满限速；并发请求用 `asyncio.Lock` 防止重复登录。集成只读，绝不调用 POST 动作接口（docker/vm action） |
| 10 | **高频轮询拖垮 NAS** | fnmonitor 采集 10s/轮 + overview 5s 缓存 → **低于 10s 的轮询无意义**；60/120s 安全（服务端限速仅针对登录失败）；禁轮询 `/api/smart?dev=` 与 `/api/apps?refresh=1`；超时 10s；失败退避交给 coordinator |
| 11 | **异常与降级** | 轮询失败时 `CoordinatorEntity.available` 自动变 False（保留最后数据），恢复后自动写回；不要吞异常返回旧值伪装正常 |
| 12 | **卸载清理** | listener 用 `entry.async_on_unload(coordinator.async_add_listener(...))` 注册，卸载自动移除；共享 aiohttp session 由 HA 管理、不要自己 close；平台卸载失败时 custom 仍用 `async_unload_platforms`（2026.11 起可用 `..._or_raise`） |
| 13 | **翻译生成时机** | 实体翻译名按创建时后端语言固化（官方 note），切换 UI 语言不影响旧实体；首次添加前就配好 zh-Hans 翻译 |
| 14 | **密码明文落盘** | config entry 数据以明文 JSON 存于 `.storage/`（HA 通用行为，非集成可控）；凭据只放 `entry.data`（reauth 修改），不放 `options`；`api.py` 中严禁把口令写进日志/异常消息；diagnostics 必须 `async_redact_data` 显式脱敏（见 3.2）。鉴权路线已定：密码登录 + 会话，不修改 fnmonitor、不加长效 Token |

---

## 10. 分阶段实施建议（MVP → 完整版）

### Phase 0 — 骨架（0.5 天量级，先行验证链路）

`manifest.json`、`const.py`、`api.py`（仅 auth + overview）、`coordinator.py`、`config_flow.py`（user + options）、`strings.json` + zh-Hans、HACS/hassfest CI。验收：HAOS 手动侧载，能添加集成、日志无报错。

### Phase 1 — MVP 实体集（建议范围）

单一快 coordinator（默认 60s，options 可选 30/60/120/300s），来源 **`GET /api/overview`（主聚合数据源）**；可选低频数据源 `/api/traffic`、`/api/power_stats`、`/api/disk_health`（按 5.2 节流加挂）：

| 实体 | 平台 | 数量 |
|---|---|---|
| CPU 使用率 / CPU 负载 | sensor | 2 |
| 内存已用 / 使用率 | sensor | 2 |
| 每卷使用量（used，%) | sensor | 卷数 ×1–2 |
| 磁盘温度 | sensor | 盘数 |
| 主板/CPU 温度 | sensor | 1–2 |
| 磁盘 SMART 健康 | binary_sensor | 盘数 |
| 系统问题总览（健康分→problem） | binary_sensor | 1 |
| 上/下行速率（整机） | sensor | 2 |
| uptime | sensor（DIAGNOSTIC） | 1 |

≈ 15–25 个实体，单设备，全静态+半动态（磁盘列表一次发现即可）。验收：仪表盘可用、30 天统计稳定、重启/reauth/断网恢复各过一遍。

### Phase 2 — 完整版

- 动态发现完善：网卡级速率/累计流量（`data_rate`/`data_volume`）、多温度探头、风扇/电压、GPU、功耗与累计电量（进能源面板）
- 磁盘子设备（serial + via_device）、SMART 明细、慢节奏第二 coordinator
- reauth / reconfigure 完整流程、diagnostics.py、OptionsFlow 模块开关
- Docker 容器 / VM 运行状态 binary_sensor（默认禁用，防膨胀）
- 质量等级自评（Bronze 对照表）、brands 图标、GitHub Release、HACS 正式上架

### Phase 3 — 远期可选

推送模式（fnmonitor → HA webhook）、`services.yaml` 自定义动作（立即刷新）、更新检测 binary_sensor、多语言补全（en/zh-Hant 等）。

---

## 11. 参考链接汇总

全部访问日期：**2026-10-28**。

**官方开发文档（核心）**

- File structure — developers.home-assistant.io/docs/creating_integration_file_structure/（页面标注最后更新 2026-06-15）
- Config flow — developers.home-assistant.io/docs/config_entries_config_flow_handler/
- Fetching data（DataUpdateCoordinator）— developers.home-assistant.io/docs/integration_fetching_data/（更新于 2026-07-09）
- Handling setup failures — developers.home-assistant.io/docs/integration_setup_failures/（更新于 2026-10-09）
- Entity 通用属性 — developers.home-assistant.io/docs/core/entity/
- Sensor entity（device_class/state_class 全表）— developers.home-assistant.io/docs/core/entity/sensor/
- Binary sensor（用户侧设备类列表）— home-assistant.io/integrations/binary_sensor/
- Entity registry / unique_id 规范 — developers.home-assistant.io/docs/entity_registry_index/（更新于 2026-06-15）
- Manifest — contributors.home-assistant.io/core/integrations/manifest/（旧址 developers.home-assistant.io/docs/creating_integration_manifest/）
- Quality Scale — contributors.home-assistant.io/core/quality-scale/
- aiohttp 客户端助手源码（`async_create_clientsession` 的 auto_cleanup / 条目卸载绑定语义、共享 cookie jar 复用逻辑）— github.com/home-assistant/core/blob/dev/homeassistant/helpers/aiohttp_client.py
- 数据脱敏助手源码（`async_redact_data` / `**REDACTED**`）— github.com/home-assistant/core/blob/dev/homeassistant/helpers/redact.py

**项目内规格文档**

- fnmonitor 数据源规格（接口/鉴权/字段/轮询约束，本文数据源章节的权威依据）— 本仓库 [docs/fnmonitor-api-spec.md](fnmonitor-api-spec.md)（基于 fnmonitor v2.20.0 源码静态分析，2026-10-28）

**2026 年关键变更博客**

- Config entry functions now return None — developers.home-assistant.io/blog/2026/10/23/config-entry-methods-return-none/
- Migration 可抛 ConfigEntry 异常 — contributors.home-assistant.io/blog/2026/09/17/Use-config-entry-exc-in-migration/
- Config entry listener + reload 弃用 — contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/
- 新实体生命周期钩子 — developers.home-assistant.io/blog/2026/10/12/new-entity-lifecycle-hooks/
- Integration categories — contributors.home-assistant.io/blog/2026/10/20/integration-categories/
- runtime_data 模式 — developers.home-assistant.io/blog/2024/04/30/store-runtime-data-inside-config-entry/

**版本与发布**

- HA 2026.10 发布公告 — home-assistant.io/blog/2026/10/07/release-202610/

**HACS**

- 发布起步 / 集成要求 / Action / include 规则 — hacs.xyz/docs/publish/start、/publish/integration、/publish/action、/publish/include
- Brands 新流程（集成内 `brand/` 目录）— hacs.xyz 相关页与 home-assistant/brands 仓库 README

**测试与联调**

- pytest-homeassistant-custom-component — github.com/MatthewFlamm/pytest-homeassistant-custom-component
- 官方测试文档 — contributors.home-assistant.io/core/development/tests/
- HAOS 常用任务（Samba/SSH）— home-assistant.io/common-tasks/os/
- Logger 集成 — home-assistant.io/integrations/logger/
- reload_config_entry 动作 — home-assistant.io/actions/homeassistant.reload_config_entry/

---

*文档完。数据源接口、鉴权与字段以 [docs/fnmonitor-api-spec.md](fnmonitor-api-spec.md) 为准；鉴权路线已确定为密码登录 + 会话（`fnmp_sid`，7 天固定有效期），不修改 fnmonitor、不加长效 Token。*
