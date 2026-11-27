# Nautilus 架构设计

## 1. 设计目标与非目标

**目标**
- 单进程可运行：`uvicorn` 起一个进程就能完整服务面板（前端静态资源由同一进程托管），零外部中间件（SQLite + 文件系统）。
- 进程监管真实可用：能拉起真实 Paper/Vanilla/Fabric/Purpur/Bedrock 服务端，能优雅停机、能崩溃重启、能实时看日志与指标。
- 模块化到「删掉一个目录不影响其余模块」：每个能力域是独立的 router + service，通过事件总线与扩展点解耦。
- 生产能力：RBAC、审计、限流、路径白名单、原子写、备份回滚。

**非目标（本版本）**
- 多节点/集群调度（P6 通过 agent 协议演进）。
- 容器编排（提供 `NAUTILUS_RUNTIME=docker` 适配位，但不做 Swarm/K8s）。
- 内建 Web 终端（安全面太大，用 RCON + 文件编辑覆盖 95% 场景）。

## 2. 分层

```
┌─────────────────────────────────────────────────────────┐
│  Presentation   frontend/dist  (React SPA, 由 FastAPI 托管) │
├─────────────────────────────────────────────────────────┤
│  Interface      api/*.py  routers · ws.py · deps.py       │
│                 (只做参数校验、鉴权、编排，不含业务逻辑)      │
├─────────────────────────────────────────────────────────┤
│  AI             ai/(agent,session,config,plan,playbooks,  │
│                 runs,approvals,trace) · mcp_server        │
│                 (agent 循环、流式事件、轨迹、运行控制)       │
├─────────────────────────────────────────────────────────┤
│  Domain         servers/(manager,rcon,logbus,metrics,     │
│                 provision) · configs/ · plugins/          │
│                 ai/tools.py（能力工具化，与上面共用）        │
├─────────────────────────────────────────────────────────┤
│  Platform       authz( casbin ) · security(argon2/jwt)    │
│                 events( pub/sub ) · extensions( hooks )   │
│                 db · settings · audit                     │
├─────────────────────────────────────────────────────────┤
│  Infra          SQLite · 本地文件系统 · 第三方 HTTP API     │
└─────────────────────────────────────────────────────────┘
```

依赖方向严格单向：`Interface → AI → Domain → Platform → Infra`。Domain 不 import Interface，Platform 不 import Domain。
`ai/tools.py` 是唯一例外：它属于 Domain，但会读 authz / audit 这两个 Platform 能力——和 HTTP 路由的鉴权方式完全一致。

**前端（Interface）要点**：React 19 + Mantine 8，默认亮色工业控制台主题（`theme.ts` 的 `steelblue`
色板 + `styles.css` 两套 CSS 变量），终端/代码编辑器保持独立深色面板。服务端 Tab 按需 `React.lazy`
分包。权限由 `/auth/me` 返回的展开式 `object:action` 驱动：`perm.ts` 做客户端包含判断，
`server-perms.tsx` 经 Context 把 `server:<action>` 传给详情 Tab 决定可见性；后端仍是唯一授权点，
前端隐藏只是避免误导与无效请求。全局 `ConfirmProvider` 提供 Promise 式确认弹窗，
`AsyncState` 统一列表的空/加载/错误三态，避免查询失败被静默当成空数据。

## 3. 模块划分与职责

| 模块 | 文件 | 职责 | 对外契约 |
|---|---|---|---|
| 配置 | `settings.py` | pydantic-settings，环境变量 + `.env` | `settings` 单例 |
| 持久化 | `db.py` / `models.py` | async SQLAlchemy，SQLite WAL | `session_scope()` |
| 认证 | `auth/backend.py` | fastapi-users：Argon2id、JWT、Cookie 传输、失败锁定 | `current_user` / `get_jwt_strategy` |
| 鉴权 | `authz.py` | Casbin RBAC（`g` 继承 + keyMatch/regexMatch） | `can(role, obj, act)` |
| 依赖 | `deps.py` | 当前用户解析、权限守卫 | `require("server:control")` |
| 事件总线 | `events.py` | 进程内 pub/sub + WS 广播 + 背压 | `bus.publish(topic, data)` |
| 扩展点 | `extensions.py` | `nautilus.extensions` entry points + 内置 hook | `hooks.emit("server.started", ctx)` |
| 进程监管 | `servers/manager.py` | 状态机、启停、重启退避、优雅停机阶梯 | `manager.start(id)` |
| 运维动作 | `servers/ops.py` | 精选动作 save/reload/broadcast/维护模式；HTTP 与 AI 工具共用，在线态绝不写 `server.properties` | `ops.set_maintenance(server, on)` |
| RCON | `servers/rcon.py` | 纯 asyncio RCON 协议实现 | `await rcon.command("list")` |
| 日志 | `servers/logbus.py` | stdout + 文件双通道、环形缓冲、事件解析 | `logbus.subscribe(id)` |
| 指标 | `servers/metrics.py` | psutil 采样、历史环形队列 | `metrics.history(id)` |
| 装服 | `servers/provision.py` | Fill v3 / Purpur / Mojang / Fabric / Forge / NeoForge（安装器 + shim jar） | `provision.install(...)` |
| Java | `servers/java.py` | 运行时探测、Adoptium JRE 下载/解压/校验 | `java.detect()/download()` |
| 文件 | `api/files.py` | 目录浏览、读写、上传下载、路径沙箱 | — |
| 配置 | `configs/` | properties / YAML / JSON / TOML 解析与原子写 | — |
| 插件 | `resources/plugins.py` | Modrinth / Hangar 搜索、安装、哈希校验、本地扫描 | — |
| 备份 | `resources/backups.py` | tar.gz 归档、清单、安全恢复、份数淘汰 | `create/list/restore` |
| 玩家 | `resources/players.py` | whitelist/ops/bans 文件、UUID 解析、RCON 同步、存档统计 | `lists/whitelist_add/op_add/ban_add` |
| 世界 | `resources/worlds.py` | 世界目录扫描、切换/新建、回收站式删除、玩法属性校验 | `list_worlds/activate/delete` |
| 通知 | `notify.py` | Webhook 投递（Discord embed / 扁平 JSON）、事件订阅、频率抑制 | `notify.emit(event, ...)` |
| 定时 | `servers/tasks.py` | APScheduler cron 维护任务（重启/备份/指令） | `run_task(id)` |
| 审计 | `audit.py` | 所有写操作的不可变记录 | `audit.record(...)` |
| 地址配置 | `addresses.py` | 面板对外地址（Host 推断兜底）、MCP 传输模式/监听地址，存 `settings_kv` | `load/save/public` |
| AI 配置 | `ai/config.py` | 端点/模型/密钥配置，Fernet 加密后存 `settings_kv` | `load/save` |
| AI 工具 | `ai/tools.py` | 把面板能力封装为 40 个工具：Casbin 权限、危险标记、超时、统一返回、写审计；不依赖 agent 框架 / FastAPI | `TOOLS / catalogue()` |
| AI agent | `ai/agent.py` | LangGraph react 图（agent ⇄ tools）、SSE 事件映射、审批续跑、取消、工具超时、运行时指令、自动压缩、失败保留消息 | `stream_turn() / build_graph() / runtime_instructions()` |
| 模型发现 | `ai/discovery.py` | `/v1/models` 拿名单，本地端点再读 `/api/tags` 补体积/参数量/量化；合并去重、限响应体、失败给可操作提示 | `discover()` |
| AI 会话 | `ai/session.py` | 进程内多轮历史 + 标题/摘要元数据，按用户提问裁剪 + TTL/LRU；历史可序列化回前端 | `conversations / serialize()` |
| 上下文压缩 | `ai/compaction.py` | 超阈值时模型摘要旧轮次（失败则确定性兜底），在用户提问边界裁剪，保留最近 10 轮原文 | `compact() / needs_compaction()` |
| 任务清单 | `ai/plan.py` | 按会话保存步骤与状态，模型通过 `update_plan` 更新，SSE `plan` 事件驱动前端 | `plans` |
| 运维手册 | `ai/playbooks.py` | 6 份按需加载的 SOP；常驻上下文只留摘要 | `catalogue() / get()` |
| 运行控制 | `ai/runs.py` | 每会话一个 `CancellationToken`，支持取消、重复运行保护、僵尸清理 | `runs` |
| 审批状态 | `ai/approvals.py` | 服务端记录暂停的调用，校验 id 与 TTL，用后即焚 | `approvals` |
| 运行轨迹 | `ai/trace.py` | 进程内逐步轨迹：每次 run 一个 `run_id`，记录模型推理/工具/计划/审批/压缩各步的服务端真实耗时、参数与结果；审批续跑复用同一 run，TTL/LRU 清理 | `traces / begin() / add_step() / finish()` |
| MCP | `mcp_server.py` | 官方 `mcp` SDK，stdio / streamable-http，复用同一批工具 | `python -m nautilus.mcp_server` |

## 4. 关键流程

### 4.1 启动服务端

```
POST /api/servers/{id}/start
  → 鉴权 server:control
  → manager.start(id)
      ├─ 状态机 stopped → starting（加锁，防并发）
      ├─ 校验 jar 存在 + EULA 已接受 + 端口未被占用
      ├─ 组命令：java -Xms.. -Xmx.. <jvm_args> -jar <jar> <args>
      ├─ asyncio.create_subprocess_exec(cwd=server_dir, stdout=PIPE, stderr=STDOUT)
      ├─ reader task：逐行 → logbus.push() → 解析就绪/玩家事件 → bus.publish
      └─ wait task：退出 → crashed(非0) / stopped(0) → 若 auto_restart 则指数退避重启
  → 返回 {state:"starting"}
```

### 4.2 优雅停机阶梯（移植自 mc-server-runner）

```
RCON "stop" ──(timeout 30s)──▶ stdin "stop" ──(10s)──▶ SIGTERM/CTRL_BREAK ──(10s)──▶ kill
```
每一级都先检查进程是否已退出，避免对已死进程发信号。

### 4.3 实时通道

单一 WebSocket `/ws/servers/{id}`，多路复用：

```jsonc
{"t":"log",     "line":"...", "stream":"stdout"}
{"t":"state",   "state":"running", "since":1690000000}
{"t":"metrics", "cpu":12.4, "rss":734003200, "players":3}
{"t":"players", "list":["Alice","Bob"]}
{"t":"progress","task":"install","pct":42}
```

服务端为每个连接维护 `asyncio.Queue(maxsize=512)`，满则**丢弃最旧**并计数（背压策略优于阻塞进程 stdout 读取 —— 阻塞会导致 MC 服务端因管道写满而卡死）。

### 4.4 扩展点

```python
# 第三方包 pyproject.toml
[project.entry-points."nautilus.extensions"]
my_ext = "my_ext:register"

# my_ext.py
def register(app, hooks, bus):
    @hooks.on("server.started")
    async def on_start(ctx): ...
    app.include_router(my_router, prefix="/api/ext/my")
```
启动时扫描 entry points，失败只记 warning，不影响主进程（扩展不能拖垮面板）。

### 4.5 AI 助手的一次工具调用

```
POST /api/ai/chat (SSE)
  → api/ai.py：校验 system:read / 模型配置，构造 Ctx(role, user_id, ip, source="panel")；生成 run_id
  → ai/agent.py：build_graph() 用 OpenAI 兼容 provider（ReasoningChatOpenAI）装配 react 图
  → ai/trace.py：traces.begin(run_id) 开一条轨迹；每个 SSE 事件带 run_id + seq + ts
  → ai/compaction.py：新提问时若历史超阈值 → 模型摘要旧轮次 + SSE compacted（轨迹记 note 步）
  → graph.astream(stream_mode=["messages","updates"])（instructions 注入时间/角色，静态提示词保持前缀稳定）
      （run 开始前先发 SSE started，前端立刻切“思考中”，不等首字节；轨迹记 model 步）
      messages 模式（agent 节点 chunk）→ SSE thinking / text（reasoning_content 由 ReasoningChatOpenAI 解析）
      updates[agent] 带 tool_calls → SSE tool_call（轨迹记 tool 步 running，收尾 model 步）
      updates[tools] 带 pending → SSE approval_required（轨迹记 approval 步 waiting，路由直接 END）
      updates[tools] 带 tool_results → SSE tool_result（轨迹 finish_step 写服务端真实耗时/结果）
                                        update_plan 额外发 SSE plan（轨迹记 plan 步）
      图自然结束（agent 无工具调用）→ SSE done（usage / pending / status）
  → ai/tools.py 包装器：_require(obj, act) → 真实服务函数 → audit.record("ai.<tool>")
  → 前端 header「执行轨迹」按钮：GET /api/ai/conversations/{id}/runs 列摘要，
    GET /api/ai/runs/{run_id} 取完整步骤时间线（审批续跑接在同一条 run 后）
```

运行控制：

- `POST /api/ai/chat` 在返回流之前先把 `CancellationToken`（自研，asyncio.Event）注册进 `ai/runs.py`；
  `POST /api/ai/cancel` cancel 它，`stream_turn` 的泵把每个图事件与该 event 竞速，
  发出 SSE `cancelled` 并把已累积的消息写回会话。
- 每个工具有超时（只读 60s / 写 120s / 安装恢复 300s），tools 节点里 `asyncio.timeout`
  兜底，避免 RCON 或下载卡死整轮；模型请求整体 180s。
- 同一会话不允许两个 run 并存（409），防止两份历史互相覆盖；僵尸登记项按 TTL 清理。
- `_compress_history` 只压缩发给模型的旧工具结果，不改写存下来的历史；
  最近 12 条消息原样发送，tool_call / tool 返回配对保持不变。

确认续跑：`POST /api/ai/approve` 带 `{call_id: bool}` 回到 `stream_turn()`，
以 `resume=True` 入图 —— **从 tools 节点起步**，把批准/拒绝落地后再问模型。
**恢复时不能再传用户 prompt**：历史末尾挂着未决 tool_calls，先补上 tool 消息
才能继续，所以确认接口只接审批结果。新提问入图前会裁掉上一轮的悬空调用
（`_sanitize_history`），防止 OpenAI 端点 400。
审批项由 `ai/approvals.py` 按会话保存，30 分钟过期，id 不匹配或重复使用返回 409
（刻意不用 interrupt/checkpointer：历史是纯 dict，为 W2 持久化铺路）。

外部 agent：`python -m nautilus.mcp_server` 把 `tools.TOOLS` 原样注册为 MCP 工具，
身份取自启动进程的环境变量 `NAUTILUS_MCP_ROLE` / `NAUTILUS_MCP_USER`，
与面板内置对话共用同一套权限与审计，不存在「MCP 这条路更宽松」。

## 5. 数据模型

```
users(id, username, password_hash, role, disabled, created_at, last_login_at, failed_count, locked_until)
servers(id, name, type, version, jar, java, memory_mb, port, rcon_port, rcon_password,
        dir, jvm_args, server_args, auto_start, auto_restart, eula, created_at, state*, *)
audit_logs(id, ts, actor_id, actor, action, target, detail, ip)
plugin_records(id, server_id, provider, project_id, slug, name, version, file, installed_at)
policies(id, ptype, v0, v1, v2)   -- casbin: p/g 规则，可热更新
settings_kv(key, value)
scheduled_tasks(id, server_id, name, kind, cron, payload, enabled,
                last_run, last_status, last_result, created_at)
api_tokens(id, user_id, name, prefix, token_hash, created_at, last_used_at, expires_at)
```

`state` 为运行时字段，不落库（进程重启后从 jar 与配置重建）。

## 6. API 契约（节选）

| 方法 | 路径 | 权限 |
|---|---|---|
| POST | `/api/auth/login` | 公开（限流） |
| GET | `/api/auth/me` | 已登录 |
| GET/POST | `/api/servers` | `server:read` / `server:create` |
| POST | `/api/servers/{id}/start\|stop\|restart\|kill` | `server:control` |
| POST | `/api/servers/{id}/command` | `server:console` |
| POST | `/api/servers/{id}/actions/{save\|reload}` | `server:console`（审计 server.save/reload） |
| POST | `/api/servers/{id}/actions/broadcast` | `server:console`（say，换行压平） |
| POST | `/api/servers/{id}/actions/maintenance` | `server:console`（白名单准入 + 踢出非白名单，OP 豁免） |
| GET | `/api/servers/{id}/files?path=` | `server:files` |
| PUT | `/api/servers/{id}/files/content` | `server:files` |
| GET/PUT | `/api/servers/{id}/config/{path}` | `server:config` |
| GET | `/api/servers/{id}/worlds` | `server:config` |
| POST | `/api/servers/{id}/worlds/{name}/activate` | `server:config` |
| GET | `/api/servers/{id}/players/stats` | `server:players` |
| PATCH | `/api/servers/{id}`（`public_address`） | `server:config` |
| GET/PUT | `/api/system/java` | `system:read` / `system:manage` |
| GET/PUT | `/api/system/webhooks` | `system:read` / `system:manage` |
| GET/POST | `/api/tokens` | 已登录（仅本人令牌） |
| GET/PUT | `/api/system/addresses` | `system:read` / `system:manage` |
| GET/PUT | `/api/ai/config` | `system:read` / `system:manage` |
| POST | `/api/ai/config/test` | `system:manage` |
| POST | `/api/ai/models` | `system:manage` |
| GET | `/api/ai/tools` | `system:read` |
| GET | `/api/ai/conversations` | `system:read` |
| GET | `/api/ai/conversations/{id}/messages` | `system:read` |
| GET | `/api/ai/conversations/{id}/runs` | `system:read`（运行记录摘要） |
| GET | `/api/ai/runs/{run_id}` | `system:read`（单次完整轨迹，按用户隔离） |
| PATCH | `/api/ai/conversations/{id}` | `system:read` |
| POST | `/api/ai/conversations/{id}/compact` | `system:manage` |
| POST | `/api/ai/chat` | `system:read`（SSE 流式） |
| POST | `/api/ai/approve` | `system:read` |
| POST | `/api/ai/cancel` | `server:read` |
| GET | `/api/servers/{id}/plugins/search?q=` | `server:plugins` |
| POST | `/api/servers/{id}/plugins/install` | `server:plugins` |
| GET | `/api/servers/{id}/metrics?window=300` | `server:read` |
| GET | `/api/servers/{id}/players` | `server:players`（或 `server:control`） |
| POST/DELETE | `/api/servers/{id}/players/{whitelist\|ops\|bans}` | `server:players` |
| GET/POST | `/api/servers/{id}/backups` | `server:backup`（或 `server:config`） |
| POST | `/api/servers/{id}/backups/{name}/restore` | `server:backup` |
| GET/POST/PATCH/DELETE | `/api/servers/{id}/tasks` | `server:tasks`（或 `server:config`） |
| GET | `/api/audit` | `audit:read` |
| GET/POST/PATCH/DELETE | `/api/users` | `users:manage` |

所有非 GET 请求校验 `Origin`（同源），写操作全部落审计。

## 7. 安全模型

| 面 | 措施 |
|---|---|
| 传输 | 支持反代 TLS；`X-Forwarded-Proto` 感知，生产强制 cookie `Secure` |
| 会话 | JWT HS256，HttpOnly + SameSite=Lax，2h 过期，密钥启动时生成并持久化 |
| 口令 | Argon2id（time_cost=3, memory=64MB, parallelism=4） |
| 爆破 | 5 次失败锁定 15 分钟，恒定时间比较 |
| 越权 | Casbin 三层：角色 → 权限 → 资源通配；每路由显式声明所需权限 |
| 路径穿越 | `Path.resolve()` 后必须 `is_relative_to(server_root)`，符号链接二次校验 |
| 指令注入 | 控制台指令走 RCON（不经 shell）；`start` 参数为列表，`shell=False` |
| 上传 | 大小上限 + 扩展名白名单 + 覆盖需确认 |
| 审计 | 谁、何时、从哪、做了什么、结果，全量落库，只读接口 |
| 模型密钥 | Fernet 加密后存 `settings_kv`，密钥由 `secret_key` 派生；接口只回掩码，换密钥后旧密文失效而非崩溃 |
| 地址注入 | 面板地址留空时按 `Host`/`X-Forwarded-Proto` 推断；MC 进服地址由每台服务器的 `public_address` 提供，AI 不猜端口 |
| AI 越权 | 每个工具声明 `(obj, act)`，执行前以**当前用户角色**过 Casbin；AI 永远不会比调用者权限大 |
| AI 误操作 | 危险工具需服务端校验的一次性审批；停止按钮真取消 run；工具超时兜底；同一会话禁止并发写历史 |
| 审批重放 | 待确认项带 30 分钟 TTL，确认后即销毁；过期页面重复点击只会得到 409 |
| 危险操作 | 删除/覆盖/强杀等工具标记 `dangerous`，未开自动确认时图在 tools 节点收齐待确认项直接 END，按 `call_id` 绑定结果 |
| 上下文膨胀 | 单个工具返回上限 24k 字符；模型往返步数上限 `max_steps`（1~40） |
| MCP 暴露 | 复用同一批工具与审计；默认 stdio，配置切 http 时只监听 127.0.0.1；绑非本机地址会告警，身份由启动环境变量决定 |

## 8. 部署形态

```
单机：python -m nautilus  (uvicorn, 1 worker)
      ↓ 反代（Caddy/Nginx）
      ↓ TLS
容器：Dockerfile 多阶段（node build → python runtime）
服务：deploy/nautilus.service (systemd, 非 root, 最小权限)
```

**必须单 worker**：进程监管状态在内存中，多 worker 会导致「A worker 启的服，B worker 不知道」。要横向扩展走 P6 的 agent 架构，而不是加 worker。

## 9. 演进路线

| 阶段 | 内容 |
|---|---|
| P6.1 | 多节点：`nautilus-agent`（gRPC/WS 双向流），master 汇总，沿用同一 Domain 层 |
| P6.2 | 备份调度：增量 zip + 异地上传（S3/WebDAV），保留策略 |
| P6.3 | OIDC 单点登录（Authlib），企业用户目录对接 |
| P6.4 | 插件市场聚合器：Modrinth + Hangar + CurseForge 统一检索与冲突检测 |
| P6.5 | 性能剖析：JFR/Metrics 接入，TPS/MSPT 曲线（Paper 的 `/tps` 解析） |
