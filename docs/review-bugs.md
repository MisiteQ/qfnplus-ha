# 「Q飞牛+」v0.2.0 只读代码审查报告

- 审查范围：`custom_components/qfnplus/`（api / coordinator / __init__ / config_flow / sensor / binary_sensor / entity / diagnostics / const / strings / translations）、`tests/`、`docs/` 三份规格文档。
- 方法：先通读源码与文档，实际运行 pytest 确认基线，再以临时验证用例（审查后已删除，未改动正式测试与源码）核实可疑点。
- **基线：`.venv` 下 `pytest tests/` 实测 48 passed in 2.79s**（与预期一致）。
- 结论速览：高 1 项、中 3 项、低 8 项；另有 4 项常见怀疑点经实测证伪（见附录 A）。

---

## 高

### H-1 coordinator 未导入 `FnOsError`，任一低频端点失败都会炸掉整轮更新（NameError）

- **问题**：[coordinator.py:85](file:///d:/CODE/QFN+/custom_components/qfnplus/coordinator.py#L85) 的 `except FnOsError as err:`，但 [coordinator.py:23](file:///d:/CODE/QFN+/custom_components/qfnplus/coordinator.py#L23) 仅导入 `FnOsAuthError, FnOsClient, FnOsConnectError, FnOsLoginLocked`，没有 `FnOsError`。traffic / power_stats / processes / disk_health 任一端点抛出 `FnOsConnectError`/`FnOsAuthError`/`FnOsLoginLocked`（均为 `FnOsError` 子类）时，异常处理分支本身抛 `NameError`。
- **证据**：临时用例实测：强制本周期重取 traffic 并令其抛 `FnOsConnectError` 后，`coordinator.last_exception` 为 `NameError`，日志出现 `Unexpected error fetching qfnplus data ... NameError: name 'FnOsError' is not defined`，`last_update_success=False`。
- **影响**：
  1. 「低频端点失败不拖垮本轮 overview 数据」的设计（[coordinator.py:86](file:///d:/CODE/QFN+/custom_components/qfnplus/coordinator.py#L86) 注释）从未生效——一个端点抖动即整轮失败，全部实体 unavailable；
  2. `NameError` 走 HA 的 unexpected-error 路径，不经过 `_consecutive_failures` / `_apply_backoff`（实测退避不生效），恢复后间隔也不复位，指数退避逻辑整体旁路；
  3. 子请求里的 `FnOsAuthError` 既被吞不了也不触发 reauth（实测无 reauth flow 产生）。
- **建议修法**：`from .api import FnOsError`（或 `except (FnOsConnectError, FnOsAuthError, FnOsLoginLocked) as err:`）。
- **是否已验证**：是（临时用例，含 `FnOsConnectError` 与 `FnOsAuthError` 两条路径）。

### H-2 官方用例 `test_sub_fetch_failure_keeps_overview_alive` 空转，掩盖 H-1

- **问题**：[test_coordinator.py:253-271](file:///d:/CODE/QFN+/tests/test_coordinator.py#L253-L271) 设置 `async_get_traffic.side_effect = FnOsConnectError` 后直接把 hass 时钟拨 61s。但节流时间戳用的是 `time.monotonic()`（[coordinator.py:77](file:///d:/CODE/QFN+/custom_components/qfnplus/coordinator.py#L77)），hass 的 mock 时钟不会推进它——第二轮 `now - _last_fetch["traffic"] ≈ 0 < 60`，traffic 被节流跳过，**失败的 mock 根本没被调用**；`"traffic" not in coordinator.data` 与 `last_update_success` 的断言因「跳过」而非「失败隔离」通过。
- **证据**：同套件 48 passed，但 H-1 的 NameError 实测存在；两件事互证该用例未真正触达被测分支。
- **影响**：回归防线失效，H-1 类缺陷可再次静默引入。
- **建议修法**：用例中显式 `coordinator._last_fetch["traffic"] = 0.0`（或注入可控 monotonic 时钟）后再触发刷新。
- **是否已验证**：是（由 H-1 用例反推 + 源码时序分析）。

---

## 中

### M-1 节流跳过周期返回的 dict 不携带上一轮低频数据 → 30s 档传感器周期性闪断 unknown

- **问题**：[coordinator.py:74-87](file:///d:/CODE/QFN+/custom_components/qfnplus/coordinator.py#L74-L87) 每轮新建 `{"overview": ...}`，被节流跳过的 key 直接缺席，未从上轮 `self.data` 结转。
- **证据**：临时用例（options=30s）：首轮 `net_down_rate` 有值 → 第二轮（traffic 被节流跳过）`coordinator.data` 无 `"traffic"` 键、实体状态 `unknown` → 窗口到期后恢复。
- **影响**：30s 档下 traffic / power_stats / processes 每隔一轮缺失，`net_down_rate`、`net_up_rate`、`energy_today`、`energy_month`、`top_process` 周期性闪 unknown，历史曲线与 Energy 面板出现锯齿。默认 60s 档因实际间隔 ≥60s 恰好每次都越过节流窗口而侥幸不闪断；`disk_health`（300s 节流）在 60/120s 档的缺失周期仅靠 binary_sensor 回退 overview 兜底，sensor 侧无此端点消费，故影响面集中在 30s 档。
- **建议修法**：结转旧数据，如 `data = {**(self.data or {}), "overview": await ...}`，或子节流段失败/跳过时保留旧值。
- **是否已验证**：是（临时用例）。

### M-2 options 更新轮询间隔不生效：`__init__.py` 未注册 update listener

- **问题**：全仓 grep 确认没有 `add_update_listener`；options flow 保存后（[config_flow.py:175-181](file:///d:/CODE/QFN+/custom_components/qfnplus/config_flow.py#L175-L181)）没有任何重载逻辑。
- **证据**：临时用例：setup(60s) → `async_update_entry(options={"scan_interval": 300})` → `coordinator.update_interval` 仍为 60s。
- **影响**：用户改 30/120/300s 档位后完全无效果，需重启 HA 或手动重载条目；与 [test_coordinator.py:274-281](file:///d:/CODE/QFN+/tests/test_coordinator.py#L274-L281) 只验证「setup 时读取 options」形成测试盲区。
- **建议修法**：在 `async_setup_entry` 注册 `entry.async_on_unload(entry.add_update_listener(update_listener))`，listener 中 reload 条目（或直接调 `coordinator.set_update_interval`）。
- **是否已验证**：是（临时用例）。

### M-2b 动态实体只增不删：磁盘/卷/网卡移除后留僵尸实体；换盘后子设备身份陈旧

- **问题**：[sensor.py:593-599](file:///d:/CODE/QFN+/custom_components/qfnplus/sensor.py#L593-L599)、[binary_sensor.py:105-106](file:///d:/CODE/QFN+/custom_components/qfnplus/binary_sensor.py#L105-L106) 的 `known_*` 均为单向增长集合，无移除逻辑；[entity.py:58-68](file:///d:/CODE/QFN+/custom_components/qfnplus/entity.py#L58-L68) 子设备 identifiers 在实体创建时按当时的 serial 固化。
- **证据**：临时用例：setup 后把 overview 改为仅剩 sda 并刷新，nvme0n1 温度实体仍在注册表中、状态 unknown。
- **影响**：拔盘/删卷/移除网卡后实体永久残留 unknown（无法从 UI 删除除非手工清理注册表）；同名换盘（如 sda 换新盘、新 serial）后传感器数据是新盘的，设备身份/序列号还是旧盘的。
- **建议修法**：listener 中对比当前集合与 `known_*` 差集，用 `entity_registry.async_get(hass).async_remove(eid)` 清理；或改为每次全量重建。换盘场景可按 name 比对 serial 变化并重建设备。
- **是否已验证**：是（僵尸实体部分实测；换盘陈旧为代码证据）。

---

## 低

### L-1 `cpu.load` 数组长度不足 3 时 getter 抛 IndexError

- **问题**：[sensor.py:161-176](file:///d:/CODE/QFN+/custom_components/qfnplus/sensor.py#L161-L176) `(load or [None,None,None])[1]` 只兜底 `[]`/None，长度 1/2 的数组仍越界。
- **证据**：临时用例：`load_15.getter({"overview":{"cpu":{"load":[0.5]}}})` 抛 IndexError。
- **影响**：一旦服务端返回短数组，native_value 每轮刷新抛错刷日志、实体不更新。fnmonitor 用 psutil getloadavg 恒 3 元素，风险低但非零。
- **建议修法**：`load = list(load or ()) + [None]*3; return load[i]`。
- **是否已验证**：是。

### L-2 登录 HTTP 200 + 空/非 JSON body 被误报为「口令错误」

- **问题**：[api.py:68-74](file:///d:/CODE/QFN+/custom_components/qfnplus/api.py#L68-L74) `_safe_json` 解析失败返回 `{}`，[api.py:128-135](file:///d:/CODE/QFN+/custom_components/qfnplus/api.py#L128-L135) 据此走 `FnOsAuthError` 分支。
- **证据**：临时用例：200 + 非 JSON body → `async_login()` 抛 `FnOsAuthError`（应为连接/协议错误）。
- **影响**：host:port 误指到其它 HTTP 服务时，配置流提示「密码错误」，误导排障方向。
- **建议修法**：`_safe_json` 失败（或登录分支 payload 为空 dict）时抛 `FnOsConnectError`。
- **是否已验证**：是。

### L-3 两块同名 GPU 时第二块被静默跳过；无名 GPU 按索引键会漂移

- **问题**：[sensor.py:569-570](file:///d:/CODE/QFN+/custom_components/qfnplus/sensor.py#L569-L570) `_gpu_key` 用 `slug(name)`，[sensor.py:896-902](file:///d:/CODE/QFN+/custom_components/qfnplus/sensor.py#L896-L902) `known_gpus` 命中即 `continue`。
- **证据**：临时用例：两块同名 GPU 只注册 1 组实体（打印确认 `GPU_ENTITY_IDS=[...utilization, ...frequency]`），无日志无提示。
- **影响**：多块同型号卡（如双 NVIDIA 同型号）第二块无数据且无任何报错线索；GPU `name` 为空时键为 `gpu{index}`，枚举顺序变化后 unique_id 与数据静默错位。
- **建议修法**：键改为 `pci` 地址优先、name 兜底；同名时追加 index 后缀并 `LOGGER.warning`。
- **是否已验证**：是（静默跳过部分；索引漂移为代码证据）。

### L-4 `binary_sensor.docker_problem` 未挂设备

- **问题**：[binary_sensor.py:153-168](file:///d:/CODE/QFN+/custom_components/qfnplus/binary_sensor.py#L153-L168) `device_info` 传 `None`，而同主题的 `docker_*` sensor 挂主设备。
- **影响**：该实体不出现在任何设备下，UI 归组与实体清理体验不一致。
- **建议修法**：传入 `main_device_info(entry, overview)`。
- **是否已验证**：代码证据。

### L-5 entry 未成功 setup 时 diagnostics 抛 AttributeError

- **问题**：[diagnostics.py:22](file:///d:/CODE/QFN+/custom_components/qfnplus/diagnostics.py#L22) `entry.runtime_data` 在 setup 失败（ConfigEntryNotReady 期间）为 None，直接取属性崩溃。密码/cookie 脱敏本身完备（实测 `**REDACTED**`，且诊断输出不含 session/cookie）。
- **建议修法**：`coordinator = getattr(entry, "runtime_data", None)` 并分支输出。
- **是否已验证**：代码证据（脱敏部分有官方测试覆盖）。

### L-6 reauth 期间 entry 被删除 → assert 崩溃

- **问题**：[config_flow.py:135-137](file:///d:/CODE/QFN+/custom_components/qfnplus/config_flow.py#L135-L137) `async_get_entry` 可能返回 None，[config_flow.py:145](file:///d:/CODE/QFN+/custom_components/qfnplus/config_flow.py#L145) `assert` 直接炸出未处理异常而非优雅 abort。
- **建议修法**：None 时 `return self.async_abort(reason="reauth_successful")`（或新增 abort 键）。
- **是否已验证**：代码证据。

### L-7 config flow 校验 session 反复创建、仅随 HA 停止回收

- **问题**：[config_flow.py:62-66](file:///d:/CODE/QFN+/custom_components/qfnplus/config_flow.py#L62-L66) 每次校验 `async_create_clientsession`。entry setup 期的 session 已实测在 unload 时被 HA 关闭，但 flow 上下文创建的 session 只在 hass stop 时关闭，配置流反复失败重试会堆积 session 对象（连接池空闲连接会释放，泄漏量小）。
- **建议修法**：校验结束 `await session.close()`，或用 `async_timeout` 包裹的 try/finally。
- **是否已验证**：代码证据（对照实测 setup 期 session 关闭行为）。

### L-8 host 无格式校验（空格 / `http://` 前缀 / 尾斜杠均放行）

- **问题**：[config_flow.py:36-43](file:///d:/CODE/QFN+/custom_components/qfnplus/config_flow.py#L36-L43) host 仅 `str`，`_host_port_in_use` 的 strip 只影响去重比对不影响存储值。非法 host 拼出坏 URL，因 `aiohttp.InvalidURL ⊂ ClientError`（实测类继承）最终落到 cannot_connect 提示，不崩溃但文案不准。
- **建议修法**：提交前 `host = host.strip()` 并拒绝含 `://`、`/`、空白的输入。
- **是否已验证**：部分（InvalidURL 继承关系实测；流程归类为代码证据）。

---

## 附录 A：经实测证伪的怀疑项（无需修改）

1. **unload/reload 泄漏专属 session / CookieJar**：证伪。实测 `async_unload_entry` 后 `entry.runtime_data.client._session.closed == True`——`async_create_clientsession(auto_cleanup=True)` 在 entry 卸载时由 HA 自动关闭。
2. **entity_id 提示直接赋值在重名时抛错或覆盖**：证伪。实测双实例下第二个 `cpu_usage` 自动去重为 `sensor.qfnplus_cpu_usage_2`，不抛错不覆盖。
3. **401 单飞重登死锁/竞态、重登失败误触发 reauth**：未发现。`asyncio.Lock` 宽限窗口逻辑自洽：429 → `UpdateFailed`（不 reauth），重登后仍 401 → `FnOsAuthError` → reauth，均与设计一致；并发场景有官方测试覆盖。
4. **翻译键不一致**：未发现。strings.json / en.json / zh-Hans.json 三方完全一致，与全部 `translation_key`（sensor 45 项 + binary_sensor 2 项 + config/options/error/abort）一一对应，无缺失多出；单位换算与 device_class/state_class 组合均合法（DATA_SIZE、DATA_RATE、ENERGY+TOTAL_INCREASING、TIMESTAMP 等）；密码无泄漏路径（现有红线测试覆盖）。

另注：`custom_components/qfnplus/brand/`（icon.png）与 `__pycache__/` 不应随 HACS 包发布（打包脚本应排除 `__pycache__`），非逻辑缺陷。

## 附录 B：测试盲区清单（对应上述风险）

1. 低频端点真实失败路径无覆盖（H-2 所述空转用例）；
2. 30s 档节流数据结转/闪断（M-1）；
3. options 更新后的生效性（M-2，现有用例只测 setup 读取）；
4. 磁盘/卷/网卡消失的实体清理与换盘设备身份（M-2b）；
5. `load` 短数组、GPU 同名/无名等数据形状鲁棒性（L-1/L-3）；
6. 登录 200+非 JSON body 的错误归类（L-2）；
7. reauth 中途 entry 删除、未 setup 条目的 diagnostics 等异常路径（L-5/L-6）；
8. 双实例 entity_id 去重（附录 A-2 行为目前无回归保护）。

> 验证脚本说明：以上「已验证」项均由临时用例（`.tmp_review/`）复现，审查结束后该目录已删除，未改动任何正式测试文件与源码。

---

## 修复情况（v0.2.1，2026-10-28）

12 项问题全部修复，无「不做」项。测试从 48 个增至 **61 个**（新增 13、重写 1），全量 `pytest tests/` 实测 **61 passed**。

### 高

- **H-1 已修**：[coordinator.py](file:///d:/CODE/QFN+/custom_components/qfnplus/coordinator.py) 补导入 `FnOsError`。验证：临时撤掉导入后重写用例立即转红（NameError → `last_update_success=False` 被断言捕获），恢复后转绿。
- **H-2 已修**：`test_sub_fetch_failure_keeps_overview_alive` 重写——显式 `coordinator._last_fetch["traffic"] = 0.0` 重置节流窗口（旧用例空转根因：hass mock 时钟不推进 `time.monotonic()`），断言 traffic mock 真实被调用（await_count 2→失败）、`last_update_success=True`、overview 数据存活、CPU 实体状态正常。已实测能捕获 H-1。

### 中

- **M-1 已修**：节流跳过周期结转上一轮成功值（`data[key] = self.data[key]`）；仅真正发起拉取且失败的 key 才缺席（消费方降级 unknown）。回归：`test_throttle_skip_carries_previous_data`（断言低频 mock 不重调 + `coordinator.data` 结转 + `net_down_rate` 保持 1.024 MB/s 不闪 unknown）。
- **M-2 已修**：[__init__.py](file:///d:/CODE/QFN+/custom_components/qfnplus/__init__.py) 注册 `entry.add_update_listener`，options 变更自动 `async_reload`。回归：`test_options_change_reloads_and_applies`（60s → 300s 后新 coordinator 以新间隔运行、实体恢复）。
- **M-2b 已修**：[sensor.py](file:///d:/CODE/QFN+/custom_components/qfnplus/sensor.py) 与 [binary_sensor.py](file:///d:/CODE/QFN+/custom_components/qfnplus/binary_sensor.py) 的 listener 增加 cleanup pass——卷按 mount、物理盘按 name（serial 变化→换盘，同轮以新身份重建）、网卡按物理口集合、GPU 按 `gpu[]`、风扇按 `sensors.fans[]`、Docker 不可用时撤下聚合实体；换盘子设备 serial/model 随重建更新。回归：`test_disk_removal_cleans_entities`、`test_volume_and_nic_removal_cleans_entities`、`test_disk_swap_recreates_entities_with_new_serial`。
  - 设计说明：换盘采用「同轮删除 + 同轮重建」而非「下一轮补建」。原因是实测确认 HA 2024.6+ 的 `DataUpdateCoordinator`（`always_update=False`）仅在数据变化时触发 listeners，「下一轮」不会到来；而同轮重建无 entity_id 竞态——`registry.async_remove` 同步派发注册表事件，旧实体对象的自删任务先于新实体注册任务入队执行，entity_id 可安全复用（已由换盘回归测试验证，实体 ID 保持 `sensor.qfnplus_disk_sda_temperature` 不漂移为 `_2`）。

### 低

- **L-1 已修**：`_load_value` helper——`cpu.load` 短于 3 时越界档位返回 None（unknown）。回归：`test_cpu_load_short_array_tolerated`。
- **L-2 已修**：登录 HTTP 200 但 body 为空/非 JSON 时抛 `FnOsConnectError`（协议错误），不再误报「口令错误」。回归：`test_login_200_empty_body_is_connect_error`。
- **L-3 已修**：`_gpu_key_map` 同名卡的后续实例追加 `_index` 后缀并 `LOGGER.warning`，不再静默跳过。回归：`test_duplicate_gpu_names_get_unique_keys`。
  - 注：键序保持 **name → pci → gpu{index}**（与 0.2.0 已发布的 unique_id 兼容，避免升级后实体重建）；审查建议的 pci 优先改为兜底位。name/pci 均为空时的索引键漂移保留（fnmonitor 正常必返回其一，实际风险可忽略）。
- **L-4 已修**：`docker_problem` 挂主设备（与 `docker_*` 计数实体归组一致）。回归：`test_docker_problem_attached_to_main_device`。
- **L-5 已修**：diagnostics 兼容 `runtime_data` 为 None（setup 未完成时不崩，返回 `coordinator: null`）。回归：`test_diagnostics_without_coordinator`。
- **L-6 已修**：reauth 入口与确认步的 entry 缺失均优雅 `abort("reauth_entry_gone")`（替代 assert；strings/zh-Hans/en 三语文案同步）。回归：`test_reauth_flow_aborts_when_entry_gone`（发起时条目已删场景；「确认步中途删除」场景因 HA 移除条目会自动终止关联 flow 而无法单独复现，防御分支保留）。
- **L-7 已修**：配置流校验改用独立短生命周期 `aiohttp.ClientSession` + `finally: await session.close()`，不再堆积 session。两处附带修正：① HA 托管的共享 session 禁止手动 close，故必须用独立 session；② 独立 session 显式 `ThreadedResolver`（fnmonitor 为 IP 直连，且裸 session 的默认 resolver 在装有 aiodns 的 Windows/Proactor loop 上直接抛错）。回归：`test_validate_input_closes_session`（失败路径也保证 close）。
- **L-8 已修**：`_validate_host` 拒绝空值、`://` 前缀、`/`、空白、`.` 开头，报 `invalid_host`（三语文案），不发任何请求。回归：`test_user_flow_rejects_invalid_host`。

### 其他

- 附录 A 注记（`__pycache__` 不入包）：[package_sideload.py](file:///d:/CODE/QFN+/scripts/package_sideload.py) 已确认具备 `EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}` 及 `.pyc` 排除，无需改动。
- 版本号 0.2.0 → 0.2.1（[manifest.json](file:///d:/CODE/QFN+/custom_components/qfnplus/manifest.json)）。
