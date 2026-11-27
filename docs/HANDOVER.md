# Nautilus 交接文档

> 面向「接手这个项目继续干」的人。读完这一份，你应该能跑起来、看懂它、知道哪些坑踩过了、
> 以及下一刀该切在哪里。
>
> 文档时间：2026-11-22 · 代码状态：`main` @ `dafa5e4`（已推送，工作区干净）
> 仓库：https://github.com/flulwh/nautilus-mc （public，MIT，唯一贡献者 flulwh）

---

## 0. 一句话概括

Nautilus 是一个**单进程内跑起来的 Minecraft 服务端 Web 管理面板**：FastAPI + React，
内置进程监管、实时控制台、玩家/白名单、世界备份、定时任务、文件与配置编辑、插件市场、
指标曲线、RBAC 权限、审计日志，外加一个能读写面板全部能力的 **AI 助手**（含 MCP 出口）。

它的设计立场：**基础设施不自研**。认证用 fastapi-users，权限用 Casbin，迁移用 Alembic，
调度用 APScheduler，Agent 循环用 LangGraph（2026-11-28 起，替换原 pydantic-ai；历史保持
纯 dict 不与框架耦合），配置解析用 ruamel.yaml —— 每个都站在成熟上游上，
面板只写"MC 服务端运维"这一层。

---

## 1. 三十秒跑起来

```bash
git clone https://github.com/flulwh/nautilus-mc.git
cd nautilus-mc
python -m venv .venv
# Windows: .venv\Scripts\activate   /   Linux: source .venv/bin/activate
pip install -e .
export NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD='NautilusAdmin2026'
python -m nautilus
```

打开 <http://127.0.0.1:8199>，`admin` / 上面的密码登录。

Windows / Linux 用户也可以直接 `start.bat` / `./start.sh`，脚本会自己建 venv、装依赖、
引导设置管理员密码、开浏览器。

**改前端**（另开一个终端）：

```bash
cd frontend && npm install && npm run dev   # 5173，vite 代理到 8199
```

**注意**：`nautilus/webui/dist/` 是**已提交进仓库**的构建产物。改完前端必须
`npm run build` 把产物写回去，否则别人 clone 下来看到的是旧 UI。

---

## 2. 代码地图

```
nautilus-mc/
├── nautilus/
│   ├── api/          # FastAPI 路由层，一个模块一个资源域
│   │   ├── servers.py   # 534 行，最大：进程/日志/指标/jar 分发
│   │   ├── ai.py        # AI 助手：配置/对话/会话/审批/轨迹/压缩
│   │   ├── system.py    # 系统信息、JRE 管理、Webhook、地址配置、审计
│   │   └── files.py configs.py plugins.py players.py backups.py
│   │                  tasks.py worlds.py tokens.py
│   ├── ai/           # AI 能力实现，与 HTTP 解耦
│   │   ├── agent.py     # LangGraph react 图（agent ⇄ tools）+ SSE 事件流
│   │   ├── model.py     # ReasoningChatOpenAI：解析 reasoning_content 思考增量
│   │   ├── tools.py     # 40 个面板能力工具（唯一改动面板状态的入口）
│   │   ├── trace.py     # 运行轨迹：逐步骤 + 真实耗时（进程内，TTL/LRU）
│   │   ├── approvals.py # 危险操作人工确认，续跑复用同一 run_id
│   │   ├── compaction.py # 上下文压缩
│   │   ├── session.py   # 会话与消息历史（纯 dict，OpenAI 风格，为 W2 落库铺路）
│   │   ├── runs.py      # 运行控制（注册/取消/活跃检测）
│   │   ├── discovery.py # Ollama / OpenAI 兼容模型发现
│   │   ├── plan.py playbooks.py config.py
│   ├── servers/      # 进程监管：subprocess + psutil 进程树 + RCON + cron
│   │   └── ops.py       # 精选运维动作（save/reload/broadcast/维护模式），HTTP 与 AI 共用
│   ├── resources/    # 配置文件读写、文件沙箱、插件市场、备份归档、玩家名单
│   ├── auth/         # fastapi-users 集成
│   ├── authz.py      # Casbin 封装（4 角色 × 13 动作）
│   ├── events.py     # 进程内 pub/sub（有界队列 + 丢旧）
│   ├── ws.py         # 控制台/指标 WebSocket
│   ├── extensions.py # pluggy 扩展钩子
│   ├── mcp_server.py # MCP 服务端（外部 agent 反向驱动面板）
│   └── webui/dist/   # 前端构建产物（已提交）
├── frontend/src/
│   ├── motion.tsx    # ★ 动效系统单一入口（EASE/variants/hooks）
│   ├── styles.css    # Codex 中性配色令牌 + CSS 物理反馈动效
│   ├── theme.ts      # Mantine 暗色优先双主题
│   ├── api.ts perm.ts types.ts useSocket.ts
│   ├── components/   # Confirm / AsyncState / ServerTable / MetricChart / StatusBadge
│   └── pages/        # 10 个页面 + tabs/ 10 个详情页签
├── docs/             # architecture.md（架构）· research.md（调研 14 节）
├── tests/            # 135 项 pytest
├── scripts/          # e2e_vanilla.py（真机 E2E）· e2e_forge.py · mock_llm.py
├── migrations/       # Alembic
├── deploy/           # Dockerfile / compose / systemd / env 样例
└── start.py start.bat start.sh
```

**规模**：后端 Python 约 11400 行（其中 `ai/` + `api/` 两块约 5200 行），
前端 TS/TSX 约 8550 行，AI 工具 40 个。

---

## 3. 核心设计决策（改代码前必读）

### 3.1 权限模型：4 角色 × 4 对象 × 13 动作

`nautilus/authz.py` 定义对象（`server/users/audit/system`）与动作
（`read/create/delete/control/console/files/config/plugins/install/backup/players/tasks/manage`）。
策略存在数据库 `policies` 表，启动时物化成 Casbin 的 model + policy 文件。
角色继承链：`admin → operator → moderator → viewer`。

**Fail-closed**：Casbin enforcer 构建失败时，所有 `can()` 返回 `False`——宁可全拒也不能全放。

**踩过的坑 15**：`permissions()` 早期直接返回 Casbin 原始策略串（`server:read|control|console`），
前端按 `split(":")` 解析后得到一堆无效权限。现在改成在服务端用同一个 enforcer 展开成
`server:read` 这样的具体对再下发 —— 两边用同一套匹配逻辑，不会再各算各的。

### 3.2 进程监管

`asyncio.create_subprocess_exec` + 逐行读 stdout/stderr，解析就绪信号
（`Done (x.xxxs)!`）与玩家进出。**优雅停止阶梯**（移植自 `itzg/mc-server-runner`）：
RCON `stop` → stdin `stop` → `SIGTERM`/Windows `CTRL_BREAK` → 宽限期后强杀；
Windows 用 `taskkill /F /T` 连带结束进程树。

崩溃自动重启：指数退避 2/4/8…最多 60s，上限 10 次，手动停止不触发，健康运行 5 分钟后计数归零。

TPS 采样（P6.5）：APScheduler 每 `tps_interval`（默认 10s）对**运行中且开启 RCON 的 Paper/Purpur** 服务端发 `/tps`，解析结果进 `Runtime.tps` 环形缓冲（同 `metrics` 一样只存内存、重启即清）。不用 stdin 回退——那条路径拿不到关联的响应文本。

面板重启不掉服（W1-Step1，2026-11-27）：`shutdown_policy` 默认 `keep`——面板退出只取消面板侧任务，JVM 存活。spawn 时在 `servers/<id>/.nautilus-runtime.json` 写 `{pid, create_time, jar, port, started_at}` 指纹，`_wait_exit` 确认退出后删。面板启动 `manager.claim_orphans()` 扫描指纹认领为 **detached Runtime**（`create_time` 防 PID 复用；无指纹的旧实例按 java + cwd + `-jar` 扫描接管）。detached 模式下 stdout 管道已随旧面板消失：控制台改为轮询尾随 `logs/latest.log`（Java `PrintStream` 吞掉 broken pipe，JVM 不阻塞），指令通道 RCON-only（`send` 对 stdin 明确报错）。前端 `ServerDetail` 显示「接管模式」徽章。设 `NAUTILUS_SHUTDOWN_POLICY=stop` 回到旧的全停行为。

**坑 6**：Windows 上 `java.exe` 只是启动器，真正的 JVM 在子进程里。CPU/RSS 必须按
`psutil` 进程树累加，CPU% 用累计 CPU 时间差分，不能直接读 `java.exe` 的数。

### 3.3 AI 助手

- **一条事件流**：thinking / text / tool_call / tool_result / approval / error / done，
  每条带 `run_id` + `seq` + `ts`。
- **工具即权限**：`ai/tools.py` 里 40 个工具都过 `_require(obj, act)`，
  走的是和 HTTP 路由同一套 Casbin 检查。AI 不能做用户做不到的事。
- **危险操作要人确认**：`auto_approve=false` 时写操作挂起等确认，续跑复用同一个 `run_id`。
- **执行轨迹**：`ai/trace.py` 进程内保存每一步，`time.perf_counter()` 实测耗时，
  不落库（与会话同生命周期，重启即清，符合单 worker 定位）。
- **上下文压缩**：`ai/compaction.py`，用户可指定 `/compact <focus>` 式重点。

### 3.4 前端

React 19 + Vite 7 + Mantine 8 + TanStack Query + uPlot。
Tab 级 lazy 分包，`motion` 单独拆 chunk（主包 443KB → 309KB）。

主题：**Codex 桌面端风格**（默认暗色：#0f1012 底 + 分层面板 + 1px 边框；单色墨
`steelblue` 色阶充当 ink 强调色，主按钮 primaryShade {light:9, dark:1} 实现黑底白字/
白底黑字的反转观感；圆角 6-14px；语义色仅用于状态）。无桌面顶栏——品牌行、导航、
用户菜单与主题切换全部收进 240px 侧栏，激活项用 layoutId 滑动的圆角高亮 + 墨色
inset 边缘条。设计令牌直接取自 .codexskin 设计源码（SPEC.md + skins/guts-terminal、
asuka-eva02 的 theme.json）：暖 ink 对 #2a2a28/#e8e5da、恒定深色的 screen/screen-2
代码面（#191a1d/#212327，两种配色都不变——它们是内容面板不是界面 chrome）、
semanticColors 三件套（diffAdded #46c077=teal、diffRemoved #d64541=red、
skill #e8a33d=yellow/skill，AI 思考与模型推理用 skill 琥珀）、弹层配方
（--pop 实底 + inset 顶部高光 + 大柔影 + 遮罩 blur(2px) saturate(90%)、
tooltip 恒为小 screen 面）。参考：github.com/Wangnov/awesome-codex-skins、
github.com/xuhuanstudio/codex-styler。

---

## 4. 当前状态

### 4.1 已完成并推送

| commit | 内容 |
|---|---|
| `2c113d2` | 工业级缺陷扫描，修复权限/安全/并发/数据一致性 30+ 处 |
| `3ddd8aa` | 补齐缺失功能，对齐上游成熟实现 |
| `e7fd179` | 一键启动脚本 |
| `c672d54` | 接入大模型 API，可通过对话/MCP 操纵面板全部能力 |
| `4163546` | 四类地址统一配置，修复旧进程导致的 AI「读取中」 |
| `adf703c` | 完善 agent 运行控制与多步任务能力 |
| `9c46be8` | Ollama 模型列表直接拉取 |
| `5cfa169` | 多会话、上下文压缩与反馈优化；修复 `install_project` 导入错误 |
| `c562604` | 推理过程可视化与发送即时反馈 |
| `4b721a3` | Agent 运行轨迹与前端可观测性 |
| `94d9767` | 工业控制台亮色主题 + 功能查缺补漏 |
| `dafa5e4` | 工业仪表动效系统 + 交接文档 |

### 4.2 验证基线（2026-11-22 实测）

```
pytest                     133 passed in 36.54s
npx tsc --noEmit           干净，无输出
npm run build              成功，motion 独立 chunk 133KB / gzip 44KB
真机 E2E                   scripts/e2e_vanilla.py → E2E OK
浏览器实测                 登录/总览/路由转场/导航指示条/AI 对话/工具调用/
                          执行轨迹 Modal 全部正常（Edge + 内置浏览器）
```

**一处容易误判的行为**：`执行轨迹` 按钮在当前会话还没有任何消息时是 `disabled`
（`activeId` 为空）。这不是 bug —— 发一条消息后按钮立即可用，Modal 正常弹出并显示
该次运行的逐步骤时间线（模型步 + 工具步，含服务端实测耗时、参数与结果）。
若要优化，是加一句 tooltip 提示"先发一条消息"，而不是改按钮的启用条件。

### 4.3 动效这一轮改了什么（已在 `dafa5e4`）

20 个前端文件 + `nautilus/webui/dist/` 产物，核心是：

- **新增** `frontend/src/motion.tsx`（150 行）—— 动效单一入口
- **新增** `frontend/package.json` 依赖 `motion@12`
- **改** `frontend/src/styles.css`（+628 行）—— 工程网格、状态呼吸、骨架 shimmer、扫描线等
- **改** `App.tsx / Dashboard.tsx / Assistant.tsx / ServerDetail.tsx / Login.tsx / Users.tsx /
  Audit.tsx / System.tsx / Profile.tsx / ServerTable.tsx / AsyncState.tsx / Confirm.tsx /
  MetricChart.tsx / tabs/*` —— 接入 stagger、转场、数字滚动、骨架屏
- **改** `frontend/vite.config.ts` —— motion 拆独立 chunk
- **改** `docs/research.md` —— 补第 14 节动效设计说明

同时清理了临时验证脚本 `tools/stub_openai.py`（正式替代品是 `scripts/mock_llm.py`）。

> Windows 上 push 可能报 `CRYPT_E_NO_REVOCATION_CHECK`，用
> `git -c http.sslBackend=schannel -c http.schannelCheckRevoke=false push`
> （该仓库 local config 已配好）。

---

## 5. 已知遗留 / 下一步候选

> **大规模升级（v0.1→v1.0）已定稿为 `docs/UPGRADE-PLAN.md`**：8 条工作流 W0–W7、里程碑
> v0.2/v0.3/v1.0、结构性天花板的代码证据与「明确不做」清单都在那里。当前进度：
> **W1-Step1（面板重启零停机）与 W0（CI/契约/发布地基）已完成**，见 §3.2 与 UPGRADE-PLAN「进度」。
> 下一步是 W2（状态持久化 Step1/2）与 W1-Step2（`nautilus-agent` 守护进程）。

### 5.1 明确的待办

（暂无——原「截图更新」待办已于 2026-11-28 完成，`docs/screenshots/` 全套 13 张已用
Codex 设计令牌主题重拍，见 commit `d1374a6`。）

### 5.2 架构文档里已经列出的演进路线

| 阶段 | 内容 |
|---|---|
| P6.1 | 多节点：`nautilus-agent`（gRPC/WS 双向流），master 汇总，沿用同一 Domain 层。**前半已落地**：W1-Step1 detach/reattach（面板重启零停机，见 §3.2）；独立 agent 守护进程（W1-Step2）仍待做 |
| P6.2 | 备份调度：增量 zip + 异地上传（S3/WebDAV），保留策略 |
| P6.3 | OIDC 单点登录（Authlib），企业用户目录对接 |
| P6.4 | 插件市场聚合器：Modrinth + Hangar + CurseForge 统一检索与冲突检测 |
| P6.5 | 性能剖析：**已完整上线**。TPS/MSPT 曲线（2026-11-27：RCON 每 10 秒采样 Paper `/tps`，REST `/servers/{id}/tps`，WS `tps_history`/`tps` 帧，监控页曲线 + Tick 健康徽章）；JFR 接入（2026-11-27：`nautilus/servers/jfr.py` 经 `jcmd` 直控 JVM，REST `/servers/{id}/jfr` 状态 + start/dump/stop/delete，剖析文件写 `logs/nautilus-jfr-*.jfr`、保留最近 `jfr_keep`=5 份，监控页「性能剖析」卡片：定时录制 / 运行中快照 / 下载 / 删除。前提：服务器选用 JDK，系统页可在线下载） |

### 5.3 明确"没做"的及原因

| 没做 | 为什么 |
|---|---|
| 自研 agent 框架 | LangGraph 已接管（2026-11-28 迁移完成，见 UPGRADE-PLAN 进度）；仍不自写循环 |
| LangSmith / OTel / Langfuse | 要额外部署与上报密钥，对单用户面板排障收益不抵运维成本 |
| 轨迹落库 | 与会话同生命周期，重启即清，符合单 worker 定位 |
| Monaco 编辑器 | 体积与依赖不划算，textarea + 后端原子写 + 语法校验够用 |
| OIDC | 需外部 IdP，超出本地面板定位 |
| 异地备份 | 超出面板自身范围，接口留给外部任务 |
| 视差 / 粒子 / Lottie | 展示型页面语言，放在运维界面上只会分散注意力 |
| 国际化 / 全局命令面板 | 优先级靠后 |

---

## 6. 踩过的坑（全部可复用）

| # | 坑 | 结论 |
|---|---|---|
| 1 | `JWTStrategy.read_token()` 在 fastapi-users v15 返回 **User 对象**而非 id | 跨 session 使用要取快照，不要带 detached ORM 实例 |
| 2 | `@dataclass` 默认 `eq=True` → `__hash__=None` | 放进 `set` 报 unhashable，pub/sub 的 Channel 必须 `eq=False` |
| 3 | fastapi-users 登录路由把 `authenticate()` 返回 `None` 当密码错误 | **抛异常会变成 500** |
| 4 | PaperMC API **v2 已下线**（`{"ok":false,"error":"sunset"}`） | 必须用 Fill **v3** |
| 5 | Mojang piston-meta 只给 **SHA-1** | 校验按 SHA-1 走，别强求 SHA-256 |
| 6 | Windows 上 `java.exe` 只是启动器，JVM 在子进程 | CPU/RSS 按 psutil 进程树聚合，CPU% 用累计时间差分 |
| 7 | Modrinth facets：`datapack` 是 **project_type** 不是 category | 映射错会静默返回 0 结果 |
| 8 | WS 初始批次与实时样本必须用不同 topic | 否则前端把批次当数据点 → `toFixed of undefined` 白屏 |
| 9 | uPlot 轴 `size` 太小会**静默截断**时间标签（"02:50"→"-50"） | 短窗口按跨度决定是否带秒 |
| 10 | Windows git schannel 报 `CRYPT_E_NO_REVOCATION_CHECK` | `git -c http.sslBackend=schannel -c http.schannelCheckRevoke=false push` |
| 11 | `whitelist add` 会让服务端用**内存副本**回写 JSON | 顺序固定：`whitelist reload` → RCON → 面板落盘 |
| 12 | SQLite `DateTime(timezone=True)` 取出是 naive | 统一走 `models.iso_utc()` 显式补 `+00:00`，否则浏览器按本地时间解释，偏 8 小时 |
| 13 | `free_port()` 只问内核 | 不知道已被别的（停机）服务器占用 → 跳过 DB 已用端口 |
| 14 | SQLite 单写者 | 手动执行任务前必须 `session.commit()` 释放审计事务的写锁，否则 `database is locked` |
| 15 | Casbin `permissions()` 返回原始策略串 | 在服务端用同一 enforcer 展开成具体 `obj:action` 对 |
| 16 | `motion.div` 不支持 Mantine 的 `w/p` 等样式属性 | 用原生 `style` 或标准 HTML 元素 |
| 17 | Mantine `ActionIcon` 不接受 `whileTap` | 走 CSS `.btn-press` |
| 18 | uPlot v1 的 `paths` API 与新版本不同 | 用 `uPlot.paths.linear()` 包装默认路径并裁剪绘制范围 |
| 19 | 工程网格放在 `body` 上会污染登录页 | 收窄到 `.shell-main` |
| 20 | 前端 `fetch` 里 headers 展开顺序 | `...rest` 后再 `headers`，否则 Content-Type 被静默丢掉 |
| 21 | 响应体只能读一次 | `res.json()` 后再 `res.text()` 会抛 "body used already"，掩盖真实错误 |
| 22 | Paper 1.20.6+ 的 `/tps` 输出第三窗口是 **15m**（旧版 10m），且每行带 `§` 颜色码 | 解析先剥颜色码，正则同时接受 `10m/15m` |
| 23 | Adoptium API 的下载链接重定向到 GitHub Release，国内基本不可用 | 优先试清华 `mirrors.tuna.tsinghua.edu.cn/Adoptium` 镜像（文件名与 API package name 一致），失败回退源站，SHA-256 仍以 API 为准 |
| 24 | `jcmd JFR.dump` 在没有进行中录制时**退出码 0** 且不写任何文件（"No recordings to dump from"） | 快照前必须先 `JFR.check` 确认有 running 录制并把 `name=` 传给它，否则接口会"成功"地返回一个不存在的文件 |
| 25 | `JFR.check` 输出格式跨 JDK 版本不同（21：`Recording 1: name=x duration=30s (running)`；旧版 `name: (running)`），且 `jcmd` 只随 JDK 发布 | 解析正则兼容两种形态；面板托管运行时从 JRE 改为下载 JDK（旧 JRE 目录自动重建） |
| 26 | 面板崩溃/重启后孤儿 JVM 的 stdout 管道已断，写会 EPIPE | Java `System.out` 是 `PrintStream`，吞掉 broken pipe 不崩（真机 kill -9 验证），所以接管模式改尾随 `logs/latest.log` 而非重连管道；PID 会被内核复用，认领必须比对 `psutil.create_time()`，只存 pid 会认错进程 |
| 27 | 服务器运行时面板原子写 `server.properties` 报 `PermissionError [WinError 5]` | 运行中的 JVM 持有该文件；且 Paper 的 `/whitelist on|off` **自己会把它持久化进 server.properties**。在线改属性只发 RCON 指令，绝不在面板侧写文件 |

---

## 7. 开发守则

1. **改前端必须 `npm run build`** —— `nautilus/webui/dist/` 在版本控制里。
2. **改后端权限相关代码，必须同步跑 `pytest tests/test_auth.py tests/test_regressions.py`**，
   Casbin 策略串的行为容易在没人注意的地方退化。
3. **新加 AI 工具时**，走 `ai/tools.py` 的 `@tool` 装饰器并在里面 `_require()`，
   不要在 tool 函数里手写权限判断 —— 那是唯一的入口。
4. **动效只碰 `transform` / `opacity`**。页面同时在跑 4 秒轮询，
   触发布局的属性动画会让滚动和点击掉帧。
5. **新增 SSE 事件必须带 `run_id` + `seq` + `ts`**，轨迹时间线靠这三个字段串起来。
6. **时间一律 `models.iso_utc()`**，别直接 `datetime.isoformat()`。
7. **删掉临时脚本**。上一轮留过 `tools/stub_openai.py`（OpenAI 兼容桩）用于浏览器验证，
   本轮已清理；`scripts/mock_llm.py` 是保留的正式开发工具。
8. **版本号只在 `nautilus/_version.py`**。pyproject/`/api/meta`/前端展示都从它取；
   改角色/对象/动作后跑 `python scripts/gen_contract.py`（CI 会检查 `contract.ts` 是否过期）。

---

## 8. 常用命令速查

```bash
# 后端
.venv/Scripts/python.exe -m pytest -q              # 全部测试
.venv/Scripts/python.exe -m pytest tests/test_ai.py -v
.venv/Scripts/python.exe -m nautilus              # 启动面板
python scripts/mock_llm.py --port 8899            # 本地假模型，AI 页面免花钱调试
python scripts/e2e_vanilla.py                     # 真机 E2E：下载→启动→RCON→采样→停机

# 前端
cd frontend && npm run dev                        # 5173
cd frontend && npx tsc --noEmit                   # 类型检查
cd frontend && npm run build                      # 产物写入 ../nautilus/webui/dist

# 提交
git add -A && git commit -m "..." && git push
```

---

## 9. 联系与上下文

- 调研与决策全过程：`docs/research.md`（14 节，每节都列了上游 GitHub 项目对照与取舍理由）
- 架构分层与关键流程：`docs/architecture.md`
- 完整功能说明与截图：`README.md`
- 项目工作区记忆：`C:\Users\lwh\Desktop\21\.workbuddy\memory\2026-11-22.md`
  （含本项目全部踩坑记录与用户偏好：下载走国内镜像、交付要完整不要骨架、命名偏"高级点"）

**用户偏好备忘**：要"完整开发"而不是骨架；基础设施一律找 GitHub 上成熟方案；
交付前要在真机/真浏览器上跑通，不能只过类型检查。
