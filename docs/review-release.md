# 「Q飞牛+」v0.2.0 (qfnplus) 兼容性与发布规范审查

- 审查日期：2026-10-28（只读审查，未修改任何源码）
- 审查范围：`custom_components/qfnplus/`、`hacs.json`、`manifest.json`、`README.md`、`.github/workflows/validate.yaml`、`requirements_test.txt`、`pytest.ini`、`scripts/`、`lovelace/`、`docs/`、`dist/`
- 目标环境：HA Core 2026.10.4 / HAOS 18.3.1，Python 3.13
- 方法：静态阅读 + JSON/YAML 解析 + 全文残留搜索（grep/Select-String）+ 本机 .venv 内 HA 源码比对。**未运行 pytest**（按要求规避资源冲突）。
- 重要局限：本机 .venv 内实际安装的是 **HA 2025.1.4**（pytest-homeassistant-custom-component 0.13.205，见 `homeassistant/const.py` 的 `MAJOR_VERSION = 2025`），因此「2026.10 兼容」只能基于 2025.1 源码 + API 演进趋势判断，非实证。

## 总体结论

- **不会导致 2026.10 加载失败**：核心代码全部使用现行 API（`entry.runtime_data`、`async_forward_entry_setups`、`DataUpdateCoordinator(config_entry=…, always_update=False)`、`async_config_entry_first_refresh`、`DeviceInfo`、`UnitOf*`、`translation_key`/`_attr_translation_placeholders`、`SelectSelector`、`asyncio.timeout`），未发现 2025.1 中已存在且计划移除的写法。
- **HACS 发布前有硬阻碍**（P0-1 manifest 占位符、P1-3 仓库未初始化 git、P1-4 brands 图标缺失）。
- **明天迁移前应修**（P0-2 lovelace 实体 ID 与实际 object_id 不匹配，迁移后仪表盘会出现成片「实体不可用」）。

---

## P0 — 阻碍发布 / 影响迁移当天体验

### P0-1 manifest.json 占位符未替换（HACS/hassfest 硬阻碍）
- **问题**：codeowner 与文档/issue 链接全部是占位符，且仓库名仍带旧 domain `qfnos-ha`。
- **证据**：`custom_components/qfnplus/manifest.json:4` `"codeowners": ["@your-github-user"]`；`:6` `"documentation": "https://github.com/your-github-user/qfnos-ha"`；`:9` `"issue_tracker": "…/your-github-user/qfnos-ha/issues"`。
- **影响**：hassfest 对自定义集成的 manifest 校验虽不校验用户名真实性，但发布后 issue 会投递到不存在的仓库；README 徽章同样失效；这是「未完成品」标志，HACS 社区规范明确要求真实 owner。占位符搜索另见 `README.md:3`。
- **建议**：确定最终 GitHub 用户名与仓库名（建议 `qfnplus-ha`，与 domain 呼应）后统一替换 manifest 3 处 + README 徽章 1 处。

### P0-2 lovelace 仪表盘实体 ID 与集成实际 object_id 不匹配（7 处）
- **问题**：`lovelace/qfnplus-dashboard-builtin.yaml` 引用的实体名与 `strings.json`/`sensor.py` 中的 `translation_key`（决定 object_id 后缀）不一致。
- **证据**（仪表盘引用 → 集成实际 object_id）：
  - `:45,:269` `sensor.qfnplus_energy_this_month` → 实际 `qfnplus_energy_month`（`sensor.py:281` key=`energy_month`）
  - `:65` `sensor.qfnplus_memory_usage` → 实际 `qfnplus_memory_percent`（`sensor.py:198`）
  - `:92,:94,:96` `sensor.qfnplus_cpu_load_1m/5m/15m` → 实际 `qfnplus_load_1/5/15`（`sensor.py:158,165,172`）
  - `:102` `sensor.qfnplus_swap_usage` → 实际 `qfnplus_swap_percent`（`sensor.py:216`）
  - `:152,:154,:156,:158` `sensor.qfnplus_volume_*_usage` → 实际 `qfnplus_volume_*_percent`（`sensor.py:651`）
  - `:228,:230,:232,:234` `sensor.qfnplus_nic_eno1_ovs_*`、`sensor.qfnplus_nic_ovs_system_*` → **永远不会存在**：`eno1-ovs` 命中后缀过滤、`ovs-system` 命中前缀过滤（`const.py:59-61` `VIRTUAL_IFACE_PREFIXES/SUFFIXES`），与 0.2.0 更新日志「补充 OVS 过滤」一致，仪表盘却按可生成实体写。
- **影响**：用户明天迁移后导入该 YAML，以上卡片全部「实体不可用」。
- **建议**：按上表改为实际 object_id；OVS 两张曲线卡删除或注明「需上游放开过滤才可用」；`lovelace/README.md` 同步。

### P0-3 测试代码泄露真实内网 IP（公开发布前必须处理）
- **问题**：`tests/test_login_contract.py:7` 注释包含真实设备地址「对 `192.168.31.252:8777` curl 实测」。
- **证据**：全文搜索 `31.252` 仅此一处；其余文件均为示例 IP `192.168.1.10`（`tests/conftest.py:183`、`tests/test_api.py:58`、`tests/fixtures/overview.json:334`、`docs/fnmonitor-api-spec.md:238`，可保留）。
- **影响**：内网拓扑泄露（危害有限但属隐私项，公开仓库不可逆）。
- **建议**：改为 `192.168.1.10` 或 `192.0.2.x` 文档保留段。未发现任何 `71xk`、真实口令、Cookie 值、token 泄露；测试口令均为 `"secret"`/`secrets.token_hex` 生成，诊断已脱敏（`diagnostics.py:15,24`），日志路径未打印密码（`api.py` 错误消息只含异常类名）。

---

## P1 — 发布规范问题（发布前应修，不阻碍加载）

### P1-1 本地/CI 测试环境与目标 HA 2026.10 脱节
- **问题**：`.venv` 内 HA 为 **2025.1.4**（phc 0.13.205）；`requirements_test.txt:6` 钉的是 `>=0.13.190,<0.14` 的宽松区间，未按文件自己注释的建议「按目标 HA 版本钉住 minor」。
- **影响**：「33 项测试通过」只证明 2025.1 兼容，2026.10 的行为差异（如有）不被覆盖；若 phc 未来发 0.14.x（对应更新 HA），`<0.14` 会把正确版本挡住。
- **建议**：查 pytest-homeassistant-custom-component 的版本→HA 对照表，钉住对应 2026.10 的确切版本（如 `==0.13.2xx`），升级 venv 后重跑 pytest（另一会话完成后）。

### P1-2 dist/ 被 .gitignore 忽略，但 README 指引用户直接下载仓库内 zip
- **证据**：`.gitignore` 含 `dist/`；`README.md:24`「或直接下载仓库里的 `dist/qfnplus-sideload.zip`」。
- **影响**：公开仓库后该路径 404，侧载用户困惑。
- **建议**：README 改为「从 GitHub Releases 附件下载」，发布时把 zip 作为 Release 资产上传。
- **附带**：旧 `qfnos-sideload.zip` 已确认删除（`dist/` 现仅含 `qfnplus-sideload.zip`）；zip 结构正确（根级 `qfnplus/…`，14 个文件，无 `__pycache__`，含 brand/icon.png 属无害冗余）。

### P1-3 仓库尚未初始化 git
- **证据**：`d:\CODE\QFFA` 根目录（含隐藏项）无 `.git`，本机 `git` 命令不可用。
- **影响**：validate.yaml 与 README 徽章假设 GitHub 环境；无 git 则无 tag、无 Releases、HACS 无法添加。
- **建议**：发布序列 = 替换占位符 → `git init` + 首提交（注意 P1-4/P2-1）→ 建 GitHub 仓库（建议名 `qfnplus-ha`）→ push → tag `v0.2.0` 并发 Release（HACS 以 release 为版本源）→ 替换 README 徽章。

### P1-4 HACS/HA 品牌图（brands）缺失
- **证据**：品牌图仅存在于 `custom_components/qfnplus/brand/icon.png`（`scripts/make_icon.py` 生成，256×256 RGBA）。HA 运行时与 HACS 都**不读取**集成目录内的 brand 文件夹。
- **影响**：集成列表无图标；`hacs/action` 的 brands 校验会报缺失项。
- **建议**：向 `home-assistant/brands` 提 PR（`icon.png` + `icon@2x.png` + `logo.png`，domain `qfnplus`）；本地 brand 目录可保留作素材源，或从打包脚本排除。

### P1-5 CI workflow 缺陷
- **证据**：`.github/workflows/validate.yaml` 全文仅 20 行：
  - 无 `permissions:` 最小权限声明；
  - 无 hassfest 定时复核（HA 官方建议加 `schedule` cron，hassfest 规则随上游演进）；
  - `push` + `pull_request` 双触发会在自己仓库的 PR 上重复跑；
  - **未运行 pytest**——仓库有 5 个测试文件但 CI 只做静态校验。
- **建议**：加 `permissions: { contents: read }`、`schedule: cron`、测试 job（`pip install -r requirements_test.txt && pytest`）。

---

## P2 — 低优先级 / 提示项

### P2-1 .gitignore 缺 `.screenshots/`
- **证据**：根目录存在 `.screenshots/`（含 263KB `lovelace/preview.png` 之外的截图产物），`.gitignore` 未列。首提交前加入，避免垃圾入库。

### P2-2 `FlowResult` 弃用类型别名
- **证据**：`config_flow.py:11` 自 `homeassistant.data_entry_flow` 导入 `FlowResult`（5 处注解）。2025.1 源码确认仍存在（`data_entry_flow.py:126`），未移除，官方推荐改用 `homeassistant.config_entries.ConfigFlowResult`。
- **影响**：仅弃用告警级别，2026.10 应仍可用。建议顺手替换。

### P2-3 `docs/ha-integration-research.md` 残留 qfnos（18 处）
- **证据**：全文搜索结果，如 `:98`「建议域名为 qfnos」、`:143` 旧 manifest 示例。
- **影响**：属历史研究档案，README 迁移章节已声明 domain 变更，可不改；如担心误导可在文档头部加「历史文档，domain 已改为 qfnplus」注记。

### P2-4 其他小提示
- `README.md:146`：bash 代码块里写 Windows 路径 `.venv\Scripts\activate`，建议标注 cmd/PowerShell 专用或改 `source .venv/bin/activate`。
- `en.json`/`strings.json` 的 config flow 标题为「Q飞牛+」（中文品牌名进英文界面）——若视为品牌名可接受；若想英化，改 `en.json` 的 `title` 即可（`en.json` 与 `strings.json` 已确认逐字一致，改时注意同步）。
- `energy_today/energy_month` 用 `TOTAL_INCREASING`（`sensor.py:270,279`）：日/月归零属正常重置语义，HA 统计可处理，无需改；仅提示勿改成 `TOTAL`。

---

## 逐项审查结论（用户清单映射）

1. **manifest.json**：字段齐全（domain/name/version/codeowners/documentation/issue_tracker/iot_class/integration_type/config_flow/requirements 均在），version `0.2.0` 合规；**但 3 处占位符未替换（P0-1）**。
2. **hacs.json**：`name`/`render_readme`/`homeassistant`（最低 HA 版本 2026.10.0）均为合法 HACS 键；无需 `domain` 键（HACS 从 manifest 读取）；`custom_components/qfnplus` 根布局符合 HACS integration 仓库结构，JSON 解析通过。
3. **HA API 兼容性**：未发现弃用/移除项（详见总体结论）；`entity.entity_id` 预设提示 object_id 的用法已在 2025.1 源码 `entity_platform.py:796-797,857-861` 验证受支持。唯一遗留：`FlowResult`（P2-2）。2026.10 实证受限于本机 venv 版本（P1-1）。
4. **测试依赖与 CI**：phc 区间过宽且本地环境滞后（P1-1）；workflow 能跑 hassfest 与 HACS Action（category=integration 正确），触发条件可用但有 P1-5 的四项缺陷。
5. **安全与隐私**：真实 IP 一处（P0-3）；无口令/Cookie/token 泄露；diagnostics 脱敏正确；api.py 不打印密码。
6. **迁移一致性**：源码/翻译/脚本/dist/测试/README 路径已全部 qfnplus 化；旧 zip 已删；迁移 7 步说明完整（含「不能共存」「历史不迁移」）；残留仅历史研究文档（P2-3）；**lovelace 仪表盘实体 ID 不匹配是迁移一致性最大漏洞（P0-2）**。
7. **发布前检查**：LICENSE（MIT）✓、README 结构完整 ✓、brand 图标缺失（P1-4）、git 仓库未初始化（P1-3）、版本 tag 计划随 P1-3 执行、GitHub 仓库 description/topics 待建库时填写。

## 建议修复顺序（迁移前 → 发布前）

1. **明天迁移前**：修 lovelace 仪表盘 7 处实体 ID（P0-2）；改掉测试文件里的真实 IP（P0-3）。
2. **发布前**：替换 manifest/README 占位符（P0-1）→ 加 `.screenshots/` 进 .gitignore（P2-1）→ git init + 首提交 → 建仓 push → tag v0.2.0 + Release（附 zip）→ brands PR（P1-4）→ CI 补 permissions/schedule/pytest（P1-5）。
3. **并行**：升级 venv 到对应 2026.10 的 phc 并重跑 pytest（P1-1）。
