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
│  Domain         servers/(manager,rcon,logbus,metrics,     │
│                 provision) · configs/ · plugins/          │
├─────────────────────────────────────────────────────────┤
│  Platform       authz( casbin ) · security(argon2/jwt)    │
│                 events( pub/sub ) · extensions( hooks )   │
│                 db · settings · audit                     │
├─────────────────────────────────────────────────────────┤
│  Infra          SQLite · 本地文件系统 · 第三方 HTTP API     │
└─────────────────────────────────────────────────────────┘
```

依赖方向严格单向：`Interface → Domain → Platform → Infra`。Domain 不 import Interface，Platform 不 import Domain。

## 3. 模块划分与职责

| 模块 | 文件 | 职责 | 对外契约 |
|---|---|---|---|
| 配置 | `settings.py` | pydantic-settings，环境变量 + `.env` | `settings` 单例 |
| 持久化 | `db.py` / `models.py` | async SQLAlchemy，SQLite WAL | `session_scope()` |
| 认证 | `security.py` | Argon2id 哈希、JWT 签发/校验、登录限流 | `hash_password` / `create_token` |
| 鉴权 | `authz.py` | Casbin RBAC（角色继承 + keyMatch2） | `can(role, obj, act)` |
| 依赖 | `deps.py` | 当前用户解析、权限守卫 | `require("server:control")` |
| 事件总线 | `events.py` | 进程内 pub/sub + WS 广播 + 背压 | `bus.publish(topic, data)` |
| 扩展点 | `extensions.py` | `nautilus.extensions` entry points + 内置 hook | `hooks.emit("server.started", ctx)` |
| 进程监管 | `servers/manager.py` | 状态机、启停、重启退避、优雅停机阶梯 | `manager.start(id)` |
| RCON | `servers/rcon.py` | 纯 asyncio RCON 协议实现 | `await rcon.command("list")` |
| 日志 | `servers/logbus.py` | stdout + 文件双通道、环形缓冲、事件解析 | `logbus.subscribe(id)` |
| 指标 | `servers/metrics.py` | psutil 采样、历史环形队列 | `metrics.history(id)` |
| 装服 | `servers/provision.py` | Fill v3 / Purpur / Mojang / Fabric 下载 | `provision.install(...)` |
| 文件 | `api/files.py` | 目录浏览、读写、上传下载、路径沙箱 | — |
| 配置 | `configs/` | properties / YAML / JSON / TOML 解析与原子写 | — |
| 插件 | `plugins/` | Modrinth / Hangar 搜索、安装、哈希校验、本地扫描 | — |
| 审计 | `audit.py` | 所有写操作的不可变记录 | `audit.record(...)` |

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

## 5. 数据模型

```
users(id, username, password_hash, role, disabled, created_at, last_login_at, failed_count, locked_until)
servers(id, name, type, version, jar, java, memory_mb, port, rcon_port, rcon_password,
        dir, jvm_args, server_args, auto_start, auto_restart, eula, created_at, state*, *)
audit_logs(id, ts, actor_id, actor, action, target, detail, ip)
plugin_records(id, server_id, provider, project_id, slug, name, version, file, installed_at)
policies(id, ptype, v0, v1, v2)   -- casbin: p/g 规则，可热更新
settings_kv(key, value)
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
| GET | `/api/servers/{id}/files?path=` | `server:files` |
| PUT | `/api/servers/{id}/files/content` | `server:files` |
| GET/PUT | `/api/servers/{id}/config/{path}` | `server:config` |
| GET | `/api/servers/{id}/plugins/search?q=` | `server:plugins` |
| POST | `/api/servers/{id}/plugins/install` | `server:plugins` |
| GET | `/api/servers/{id}/metrics?window=300` | `server:read` |
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
