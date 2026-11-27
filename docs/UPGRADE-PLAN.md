# Nautilus 大规模升级方案（v0.1 → v1.0）

> 2026-11-27 定稿。本文件是执行中的活文档：每完成一条在「进度」里记录 commit。
> 结构性约束的代码证据、四条原则、里程碑 v0.2/v0.3/v1.0 与「明确不做」清单都在此。

## 进度

- [x] P6.5 性能剖析（TPS/MSPT + JFR）—— `c4f9b58` + `8a83b14`（原路线图条目，先于本方案完成）
- [x] **W1-Step1 detach/reattach（面板升级零停机）** —— 本文件 §W1 Step1，2026-11-27 完成：
  `runtime.json` 指纹 + 启动认领（含无指纹的旧实例进程扫描接管）+ tail latest.log +
  RCON-only 指令通道 + `NAUTILUS_SHUTDOWN_POLICY=keep|stop`（**默认 keep，行为变更**）。
  真机验收：kill -9 面板后新面板认领同一 pid、日志流恢复、停止阶梯生效。
- [x] **W0 CI / 契约自动化 / 发布工程** —— 2026-11-27 完成：
  `.github/workflows/ci.yml`（pytest 矩阵 py3.11/3.12 × linux/windows + 前端 tsc/build +
  「dist 必须与新鲜构建一致」漂移检查 + 产物上传）；
  `release.yml`（tag 触发：stamp 版本+commit → sdist/wheel → GitHub Release，smoke 装 wheel 验证）+
  `.github/release.yml` 发布说明模板；
  契约：`tests/test_contract.py` 扫 `frontend/src` 全部 api/fetch/SSE 调用比对 `app.openapi()`，
  `scripts/gen_contract.py` 从 `authz.py` 生成 `frontend/src/generated/contract.ts`（Users 角色下拉改用 ROLES）；
  版本单一来源 `nautilus/_version.py`（pyproject dynamic attr；`/api/meta`、`/api/system` 返回 version+commit；
  前端侧栏/登录页从 meta 取版本，不再硬编码）。
  镜像+校验和：start.py 早已镜像优先/官方源回退，jar 下载 SHA-256/SHA-1（provision.py）、
  JDK 下载 SHA-256（java_svc，清华镜像优先）——本轮复核确认，无缺漏。
- [ ] W1-Step2 `nautilus-agent`
- [ ] W2 状态持久化与数据层
- [ ] W3–W7（详见下文）

---

## 一、现状的结构性天花板（代码证据）

| # | 天花板 | 代码证据 | 后果 |
|---|---|---|---|
| 1 | **面板生命周期 = 服务器生命周期**（W1-Step1 已解决） | `manager.shutdown()` 退出时逐个 `stop()` 所有服务器；POSIX 下 `start_new_session=True`，面板崩溃则进程成孤儿，重启后 `port_in_use()` 预检反而报「端口被占用」 | 升级面板 = 全服玩家掉线；崩溃后留下无法接管的孤儿 JVM |
| 2 | **运行态全部易失** | AI 会话/轨迹/审批、指标环形队列（600 点 × 3s ≈ 30 分钟）全在进程内存 | 重启丢对话、丢监控；无法回答「昨晚为什么卡」 |
| 3 | **在线备份无一致性协调** | `backups.create()` 直接对运行中的世界打 tar.gz，没有 RCON `save-off/save-all` 三段式；仅全量、仅本地、无加密 | 可能打包到半写的 region 文件；磁盘损坏即全丢 |
| 4 | **数据层单轨** | `settings.db_url` 硬编码 `sqlite+aiosqlite`；`db.py` 的 `SYNC_DB_URL` 与 PRAGMA 无条件执行 | PG 在路线图里（P6），但没有开关 |
| 5 | **授权粒度是「类型」不是「实例」** | Casbin 策略 `v1="server"`；`api_tokens` 表无 scope 列 | 无法表达「用户 A 只能管 3 号服」；一枚令牌泄露 = 该角色全量权限 |
| 6 | **零 CI、零前端测试** | 仓库无 `.github/workflows`；`package.json` 无任何测试框架；`api.ts`/`types.ts` 手写契约 | 大规模重构没有回归网；前后端契约漂移靠人眼 |
| 7 | **AI 新攻击面未设防** | 玩家聊天经控制台日志 → `read_console` 工具进入 agent 上下文；自动确认模式可关闭审批 | 恶意玩家可在聊天里写提示注入指令，诱导 AI 执行危险操作 |
| 8 | **控制台镜像日志无限增长** | `_pump()` 以 append 模式写 `logs/nautilus-console.log`，无轮转 | 长期运行的服务器会吃满磁盘 |

另有两个工程细节：`Assistant.tsx` 已膨胀到 50KB 单文件；`docs/research.md` 选型表与实际实现漂移（写的是 Tailwind/shadcn，实际是 Mantine 8），且 §8–§11 章节重复。

## 二、升级总览

**四条原则**：① 单机零依赖仍是默认，分布式能力全部做成可选开关；② 先补工程地基再动架构；③ 每阶段独立可发布、Alembic 可回滚、API 向后兼容；④ 尊重 docs 里已记录的取舍（不换 agent 框架、不引 AGPL、默认形态不上传外部观测后端）。

| 线 | 主题 | 规模 | 依赖 | 对应原路线图 |
|---|---|---|---|---|
| **W0** | 工程地基：CI / 类型 / 测试 / 发布 | M | — | 新增 |
| **W1** | 运行态解耦：detach/reattach → 独立 agent | L | W0 | P6.1 前半 |
| **W2** | 状态持久化与数据层 | M | W0 | 新增 |
| **W3** | 多节点舰队与资源治理 | XL | W1、W2 | P6.1 |
| **W4** | 游戏运维纵深：TPS / 备份 / 整合包 / 群组 | L | W2 | P6.2 / P6.4 / P6.5 |
| **W5** | 安全加固：2FA / OIDC / AI 注入防护 | M | 可并行 | P6.3 |
| **W6** | AI 演进：持久化 / 巡检 / 评测回归 | M | W2 | 新增 |
| **W7** | 前端体验：i18n / 预览 / diff / PWA | M | 可并行 | §13 遗留项 |

## 三、各工作流

### W0 · 工程地基

- **CI 矩阵**（GitHub Actions）：ruff + pyright（MIT）+ pytest（3.11/3.12/3.13 × ubuntu/windows）+ `tsc` + vitest（MIT）+ Playwright（Apache-2.0）冒烟。现有 `scripts/e2e_vanilla.py` / `e2e_forge.py` / `mock_llm.py` 挂 nightly。
- **质量闸门**：pytest-cov 覆盖率棘轮；`pip-audit` + `pip-licenses` 许可证闸门（GPL/AGPL 红灯）；dependabot；pre-commit；`size-limit` 主包 ≤320KB。
- **契约自动化**：`openapi-typescript` + `openapi-fetch` 从 FastAPI OpenAPI 生成前端类型，替换手写 `api.ts`/`types.ts`。
- **发布工程**：semver + tag 触发 PyPI（trusted publishing）+ GHCR 多架构镜像（buildx arm64）+ CHANGELOG。
- **拆文件与修文档**：`Assistant.tsx` 拆组件 + `useAssistantStateMachine` hook（vitest 覆盖状态机）；修订 research.md 漂移与重复章节。

### W1 · 运行态与面板生命周期解耦

**Step 1 — detach + reattach（已完成）**
- 启动时写 `servers/<id>/.nautilus-runtime.json`：pid + `psutil.create_time()` 指纹（防 PID 复用）+ jar + 端口 + started_at；停止/退出确认后删除。
- 面板启动 `manager.claim_orphans()`：指纹匹配 → 认领为 detached Runtime；无指纹（旧版本遗留）→ 按「java 进程 + cwd=服务器目录 + argv 含 `-jar <jar>`」扫描，优先取持有游戏端口的进程。
- detached 通道：控制台 = 轮询尾随 `logs/latest.log`（0.7s），认领时先回灌文件尾部重建缓冲与在线玩家集合；指令 = RCON-only（stdin 已随旧面板关闭，`send` 给出明确错误）；停止阶梯 = RCON stop → 轮询死亡 → Windows `taskkill /F /T` / POSIX terminate→kill。
- `NAUTILUS_SHUTDOWN_POLICY=keep|stop`，**默认 keep**：面板退出只取消面板侧任务，JVM 存活等待下一次认领。auto_start 因 `rt.alive` 天然跳过已接管服务器。

**Step 2 — `nautilus-agent` 独立守护进程（终态，对齐 P6.1 与 MCSManager/Wings 先例）**
- agent 拥有子进程、stdout 泵、状态机、指标、RCON；面板经本机 Unix socket / named pipe 连接，agent detached 拉起，面板重启后重连并按事件 seq 回放。
- 抽出 `RuntimeBackend`：`LocalBackend` / `AgentBackend` / `DockerBackend`。**同一套 pytest 生命周期契约测试跑在两个后端上**。
- 协议 WSS + JSON 帧（`websockets` 已在依赖），不上 gRPC。

**验收**：`kill -9` 面板再拉起，服务器零停机、控制台与指标 5 秒内恢复。

### W2 · 状态持久化与数据层

- **PG 开关**：`NAUTILUS_DB_URL` 全量可配；修 `SYNC_DB_URL` 硬编码、PRAGMA 改 SQLite 条件执行；`[postgres]` extra（asyncpg）；CI 双引擎迁移测试。默认仍 SQLite。
- **AI 状态落库**（Alembic 0005+）：`ai_conversations / ai_messages / ai_runs`；**待审批项重启即作废**（安全语义）。
- **指标持久化 + 降采样**：3s 原始点留内存；分钟级聚合落库（7 天分钟级 / 90 天小时级）；可选 `[prometheus]` extra 暴露 `/metrics`。
- **日志治理**：`nautilus-console.log` 按大小轮转 + gzip；解析行入 SQLite FTS5（PG 用 tsvector，接口抽象），控制台历史跨重启可搜。
- **密钥轮转**：Fernet → MultiFernet，`nautilus secrets rotate`。
- **可靠投递 outbox**：webhook 通知与任务结果先落表，APScheduler 指数退避重试 + 死信。

### W3 · 多节点舰队与资源治理

- 节点模型 `servers.node_id`；agent 只做出站 WSS 回连 + 一次性配对码换 mTLS（自签 mini-CA）；心跳 + 断线 store-and-forward；文件分块流式 RPC；agent 非特权运行。
- Docker 运行时落地（itzg/docker-minecraft-server，Apache-2.0）。
- 非容器限额：Linux systemd-run/cgroups v2；Windows Job Objects（ctypes）。
- 按服 ACL：Casbin RBAC with domains；`api_tokens.scope` 列，守卫取「令牌 scope ∩ 用户角色」；HTTP/WS/AI/MCP 四路一致。
- 端口注册表替代 `free_port()` 线性扫描。

### W4 · 游戏运维纵深

- TPS/MSPT 曲线与告警（TPS 已完成；告警：TPS<18 持续 60s、崩溃循环、磁盘水位 → webhook / AI 巡检）；原版/Fabric 走 spark；mcstatus SLP 外部探活区分「进程活着」与「服务可连」。
- 备份一致性三段式 `save-off → save-all flush → 归档 → finally save-on`；增量/加密/异地交 **restic**（BSD-2，按 JRE 模式托管二进制 + SHA-256）；定期恢复演练（level.dat 校验）入审计。
- `.mrpack` 导入；CurseForge zip（API Key 开关）；**导入现有服务器目录**；版本升级向导（兼容性检查 → 备份 → 升级 → 回滚）。
- Velocity/BungeeCord、Geyser/Floodgate、BlueMap/Dynmap 标签页。
- 定时公告（RCON title/actionbar）、Discord bot 入站、公开状态页 + SVG 徽章。

### W5 · 安全加固

- TOTP 2FA（pyotp）+ 恢复码；OIDC（Authlib）；`users.token_version` 会话吊销。
- **AI 提示注入防护**：日志/聊天内容 spotlighting；污点跟踪（摄入过聊天内容后危险工具强制人工审批）；按角色的控制台指令策略层（HTTP/AI/MCP 共用）。
- AI 预算：usage 落库 + 按角色 token 预算/限速。
- 常规：CSP、镜像 non-root + read-only rootfs、依赖漏洞扫描进 CI。

### W6 · AI 演进（不换框架）

- 会话/轨迹持久化（W2）；AI 巡检 `scheduled_tasks kind=ai_inspect`；`search_logs`（FTS5）；golden-trajectory 评测回归（扩展 mock_llm.py）；`compact_model` 双模型路由；工具数 >~50 再重估分组。

### W7 · 前端体验

- i18n（react-i18next，zh-CN/en）+ 命令面板（cmdk）；服务器列表实时 SLP 预览；配置 diff/回滚可视化；编辑器升级 CodeMirror 6（MIT，懒加载，textarea 保留为降级）；PWA + Web Push；虚拟滚动。

## 四、里程碑

| 版本 | 主题 | 内容 | 退出标准 | 粗估 |
|---|---|---|---|---|
| **v0.2** | 重启不掉服、状态不丢 | W0 + W1-Step1（已完成）+ W2 + W5(2FA/注入防护) | kill -9 面板服务器零停机；CI 矩阵全绿 | ~3 周 |
| **v0.3** | 从单机到舰队 | W1-Step2 + W3 | 双远程节点统一管控；按服 ACL 四路一致 | ~4 周 |
| **v1.0** | 运维纵深 + API 冻结 | W4 + W6 + W7 + 文档重写 | 备份恢复演练 100%；OpenAPI 契约冻结 | ~4 周（可并行） |

## 五、明确不做

不换 agent 框架（LangGraph / OpenAI Agents SDK）；不引 AGPL；不默认要求外部中间件（PG/Prometheus/restic 均为可选 extra）；不做内置 Web 终端；不做 K8s/Swarm 编排。

## 六、验收度量与前三风险

| 指标 | 现状 → 目标 |
|---|---|
| 面板升级导致的服务器停机 | 全部停止 → **0**（W1-Step1 已达成，待 CI 固化） |
| 指标可查询窗口 | 30 分钟 → 90 天（降采样） |
| 备份 RPO / 恢复演练 | 手动全量 → 增量 ≤15 分钟 / 脚本化演练 100% |
| CI | 无 → 3 Python × 2 OS 矩阵 + 前端测试 + 许可证/漏洞闸门 |
| AI 危险操作审批旁路 | 自动确认可全关 → 污点内容触发时 **0 旁路** |
| 发布 | 0 release → tag 自动化三产物 |

**前三风险**：① agent 协议是最大新攻击面 → 出站-only + mTLS + 一次性配对码 + 非特权运行；② 双后端状态机发散 → 同一套契约测试强制跑 Local/Agent 两遍；③ 范围蔓延 → 每线独立验收、v0.2 只做「重启不掉服」这一件事做透。
