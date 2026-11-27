# Nautilus 技术选型调研报告

> 目标：为 MC 服务端管理面板挑选**可复用的成熟开源模块**，覆盖进程管理、Web 界面、权限认证、日志监控、插件与配置管理五大核心能力。所有结论基于 2026-11-22 对 GitHub 的实测采集（`gh api` / GraphQL），数据可复现。
>
> 调研原则：**能在 GitHub 找到成熟实现的一律不自己写**；只有「与本项目数据模型强耦合、且外部库会显著增加部署复杂度」的部分才内联实现，且必须对齐成熟项目的规范（如 Casbin 的 RBAC 模型）。

---

## 1. 结论速览

| 能力域 | 选择 | 类型 | 许可证 | 核心理由 |
|---|---|---|---|---|
| Web 框架 / 异步运行时 | **FastAPI 0.115+ + Uvicorn** | 直接集成 | MIT / BSD-3 | 原生 async、OpenAPI 自文档、WebSocket/SSE 一等公民，单进程即可扛住面板级并发 |
| 进程监管 | **自研 Supervisor（对齐 mc-server-runner + Wings 语义）**，Docker 模式旁挂 **itzg/docker-minecraft-server** | 二封装 | Apache-2.0 | 无现成库同时满足「按服状态机 + 优雅停机 + 事件流 + 热重载」，但两处语义直接复用 |
| WebUI | **React 19 + Vite 6 + Tailwind 4 + TanStack Query**，组件层用 shadcn/ui 的**源码内联**模式 | 集成 | 均 MIT | 构建产物可静态托管，零运行时框架依赖；shadcn 是「复制源码」而非 npm 依赖，无版本锁定风险 |
| 权限认证 | **Argon2id（argon2-cffi）+ PyJWT + pycasbin** | 集成 | MIT / Apache-2.0 | 密码哈希用 OWASP 首选算法；鉴权复用 Casbin 的 RBAC 模型（含角色继承），策略落库 |
| 日志监控 | **watchdog（文件轮转）+ 进程 stdout 直读 + psutil** | 集成 | Apache-2.0 / BSD-3 | 日志双通道：stdout 实时、文件通道覆盖 `latest.log` 轮转；指标采样用 psutil 无侵入 |
| 插件管理 | **Modrinth API v2 + Hangar API v1**，本地 `plugin.yml` 解析兜底 | 集成 | 服务条款允许程序化访问 | 两个源覆盖 90%+ 插件；下载走 SHA-1/SHA-512 校验，拒绝无哈希产物 |
| 配置管理 | **ruamel.yaml（round-trip）+ 自研 properties/JSON 编辑器** | 集成 | MIT | round-trip 保留注释与键序，避免「面板一保存就吃掉用户注释」这一经典事故 |
| 服务端分发 | **PaperMC Fill v3 + Purpur v2 + Mojang piston-meta + Fabric Meta** | 集成 | 公开 API | 一键装服，含 SHA-256 校验与 Java 版本要求探测 |

**没有选用的高星项目及原因**（避免踩坑，详见 §3）：

- `pterodactyl/panel`（9.5k★）：PHP/Laravel + MySQL + 独立 Go daemon，架构完整但**不可嵌入**，二次开发成本高于自建。
- `pelican/panel`（2.4k★）：**AGPL-3.0**，网络分发即触发传染，商业/闭源场景不可接受。
- `MCSManager`（5.0k★）：Node 双进程（Web + Daemon），无 Python 生态，且插件机制弱。
- `CraftyController`：本次核验时 GitHub 返回 **404**，无法确认许可证与维护状态，**排除**（合规红线：许可证不可考即不用）。
- `grafana/loki`（29k★）：**AGPL-3.0** 且需要额外部署日志后端，对「单机面板」是杀鸡用牛刀。
- `Supervisor`（9.2k★）：进程模型是「守护一堆长期服务」，缺少按服状态机、在线玩家感知与热重载，且其许可证为 BSD 变体 + 商标条款。

---

## 2. 采集数据（2026-11-22）

> ★ = stargazers，Fork = fork 数，Issue = 开放 issue 数，「最近提交」= pushedAt。数据来自 GitHub GraphQL API。

| 项目 | 语言 | ★ | Fork | 开放 Issue | 许可证 | 最近提交 |
|---|---|---:|---:|---:|---|---|
| pterodactyl/panel | PHP | 9,536 | 2,754 | 122 | MIT（含商标声明，SPDX 识别为 NOASSERTION） | 2026-11-18 |
| pterodactyl/wings | Go | 1,081 | 506 | 0 | MIT | 2026-11-11 |
| pelican/panel | PHP | 2,433 | 323 | 3 | **AGPL-3.0** | 2026-11-20 |
| pufferpanel/pufferpanel | Go | 1,769 | 268 | 119 | Apache-2.0 | 2026-11-20 |
| MCSManager/MCSManager | TypeScript | 5,014 | 605 | 146 | Apache-2.0 | 2026-11-15 |
| itzg/docker-minecraft-server | Shell | 14,566 | 1,920 | 128 | Apache-2.0 | 2026-11-21 |
| itzg/mc-server-runner | Go | 46 | — | — | Apache-2.0 | 2026-11-11 |
| itzg/docker-minecraft-bedrock-server | Shell | 1,890 | — | — | MIT | 2026-11-19 |
| itzg/rcon-cli | Go | 219 | 22 | 6 | Apache-2.0 | 2026-11-19 |
| Tiiffi/mcrcon | C | 1,105 | — | — | zlib | 2025-12-01 |
| py-mine/mcstatus | Python | 609 | 42 | 14 | Apache-2.0 | 2026-11-21 |
| Supervisor/supervisor | Python | 9,177 | — | — | BSD 变体 + 商标条款 | 2025-12-21 |
| grafana/loki | Go | 29,213 | — | — | **AGPL-3.0** | 2026-11-21 |
| Ylianst/MeshCentral | JavaScript | 7,736 | — | — | Apache-2.0 | 2026-11-20 |
| fastapi/fastapi | Python | 104,995 | 10,366 | 1 | MIT | 2026-11-18 |
| Kludex/uvicorn | Python | 11,125 | 1,100 | 20 | BSD-3-Clause | 2026-11-01 |
| fastapi-users/fastapi-users | Python | 6,264 | 510 | 5 | MIT | 2026-11-18 |
| apache/casbin-pycasbin | Python | 1,778 | 223 | 2 | Apache-2.0 | 2026-11-21 |
| hynek/argon2-cffi | Python | 742 | 58 | 1 | MIT | 2026-11-13 |
| jpadilla/pyjwt | Python | 5,727 | 839 | 35 | MIT | 2026-11-18 |
| gorakhargosh/watchdog | Python | 7,461 | 823 | 138 | Apache-2.0 | 2026-11-21 |
| giampaolo/psutil | Python | 11,330 | 1,538 | 216 | BSD-3-Clause | 2026-11-20 |
| sqlalchemy/sqlalchemy | Python | 12,301 | 1,959 | 188 | MIT | 2026-11-19 |
| HangarMC/Hangar | Java | 285 | 79 | 73 | MIT | 2026-11-20 |
| modrinth/code | Rust | 2,545 | 676 | 990 | — (平台自身 AGPL，**API 另有条款**) | 2026-11-21 |
| vitejs/vite | TypeScript | 84,014 | 9,229 | 508 | MIT | 2026-11-20 |
| shadcn-ui/ui | TypeScript | 128,694 | 11,424 | 753 | MIT | 2026-11-21 |
| TanStack/query | TypeScript | 50,607 | 4,233 | 42 | MIT | 2026-11-21 |

### 2.1 API 可用性实测（决定集成方式的关键）

| 端点 | 实测结果 | 结论 |
|---|---|---|
| `api.papermc.io/v2/projects/paper` | `{"ok":false,"error":"sunset"}` | **已下线**，所有仍写 v2 的教程/项目都已失效 |
| `fill.papermc.io/v3/projects/paper/versions/1.21.4/builds/latest` | 200，返回 `downloads["server:default"].url` + SHA-256 | **改用 Fill v3** |
| `api.purpurmc.org/v2/purpur/{ver}/latest` | 200 | 可用 |
| `meta.fabricmc.net/v2/versions/loader/{ver}` | 200 | 可用 |
| `piston-meta.mojang.com/mc/game/version_manifest_v2.json` | 200 | 可用（原版） |
| `api.modrinth.com/v2/search` | 200（需 User-Agent） | 可用，**必须带 UA** |
| `hangar.papermc.io/api/v1/projects` | 200 | 可用 |

> 这四项实测直接决定了实现细节：Paper 走 Fill v3、Modrinth 必带 UA、Purpur 走 v2、原版走 piston-meta。任何照抄旧博客的实现都会在第一步挂掉。

---

## 3. 分模块对比与决策

### 3.1 服务器进程管理

| 候选 | 许可证 | 活跃度 | 性能 | 扩展性 | 安全性 | 兼容性 | 判定 |
|---|---|---|---|---|---|---|---|
| **pterodactyl/wings** | MIT | 高 | 极高（Go） | 高（Docker 抽象） | 高 | 需 Wings+Panel 整套 | **语义参考**：进程状态机、优雅停机、资源限制的设计范本 |
| **itzg/mc-server-runner** | Apache-2.0 | 中 | 极高 | 低（单一职责） | 高 | 仅容器场景 | **二封装**：其 `STOP_DURATION`/信号转发策略被移植进自研 supervisor |
| **itzg/docker-minecraft-server** | Apache-2.0 | 极高 | 高 | 中 | 中（需 Docker 权限） | Linux 优先 | **可选后端**：`NAUTILUS_RUNTIME=docker` 时作为容器运行时 |
| Supervisor | BSD 变体 | 中（2025-12 后无提交） | 中 | 低 | 中 | 好 | 排除：守护模型不匹配 |
| 自研 asyncio supervisor | — | — | 高 | 高 | 高 | Win/Linux 双通 | **采用** |

**为什么自研而不是直接用 Supervisor**：面板需要的是「每个服务器一个独立状态机 + 在线玩家感知 + 配置热重载 + 崩溃自动重启退避」，这些都需要在应用进程内直接访问 `asyncio.subprocess` 的 stdout 流。Supervisor 是外部守护进程，跨进程拿 stdout 必须绕 XML-RPC，反而更脆。

**移植的两条成熟语义**（来自上述开源项目，不是拍脑袋）：
1. **优雅停机阶梯**（mc-server-runner）：`RCON stop` → 等待 N 秒 → `stdin stop` → 等待 → `SIGTERM`/`CTRL_BREAK` → 等待 → `SIGKILL`。逐级降级，每级都有超时。
2. **就绪判定**（Wings 的 `done` 匹配）：以日志出现 `Done (x.xxxs)! For help, type "help"` 作为「启动完成」信号，而不是「进程还活着」——这是面板显示 `running` 与 `starting` 的区别，也是能否安全发指令的前提。

### 3.2 Web 框架与 WebUI

| 候选 | 许可证 | 性能（面板级） | 扩展性 | 安全 | 判定 |
|---|---|---|---|---|---|
| **FastAPI + Uvicorn** | MIT / BSD-3 | 单进程 3–8k req/s，够用 | 依赖注入 + Router 天然分模块 | Pydantic 强校验、OpenAPI | **采用** |
| Flask + Gunicorn | BSD | 中 | 中 | 中 | 同步模型做日志流要额外起线程 |
| Django + DRF | BSD | 中 | 高（自带 admin/auth） | 高 | 太重，ORM 同步，WS 需 Channels |
| MCSManager 前端方案（Vue2 老栈） | Apache-2.0 | — | 低 | — | 排除 |
| **React 19 + Vite 6 + Tailwind 4 + TanStack Query** | MIT | 构建产物 ~120KB gzip | 组件级 | 无 innerHTML 注入点 | **采用** |
| Vue 3 + Naive UI | MIT | 相近 | 高 | 高 | 备选，团队熟悉度取舍 |

**shadcn/ui 的用法很关键**：它不是一个 npm 组件库，而是「把组件源码复制进你的仓库」。这意味着**没有运行时依赖、没有被上游 breaking change 打断的风险、可以任意改样式**。这正是面板这种要长期维护、又要视觉定制的项目最需要的模式。本次实现按其设计语言（Radix 风格语义 + Tailwind token）手写组件，不引入 Radix 运行时。

### 3.3 权限与认证

| 候选 | 许可证 | 判定 |
|---|---|---|
| **argon2-cffi（Argon2id）** | MIT | **采用**：OWASP 当前推荐的首选口令哈希，优于 bcrypt 的抗 GPU 特性 |
| **PyJWT（HS256）** | MIT | **采用**：无状态会话，cookie 承载 |
| **pycasbin** | Apache-2.0 | **采用**：RBAC + 角色继承，策略可落库、可热更新 |
| fastapi-users | MIT | 不用：它绑定自己的 User 表与路由，与自定义 RBAC、审计、多角色冲突，改造成本 > 收益 |
| Authlib / OAuth2 | BSD | 阶段三：接入 OIDC 单点登录时再引入 |

**安全设计取舍**（不是默认值，而是刻意选择）：
- JWT 放 **HttpOnly + SameSite=Lax cookie**，不放 localStorage —— XSS 拿不到 token；代价是必须做 CSRF 防护，因此所有非 GET 请求校验 `Origin`，且拒绝跨站。
- 登录失败**恒定时间**比较 + 失败计数限流（防撞库）。
- WebSocket 复用同一 cookie 鉴权，握手阶段校验，不引入 query token（避免日志泄漏）。

### 3.4 日志与监控

| 候选 | 许可证 | 性能 | 判定 |
|---|---|---|---|
| **进程 stdout 直读（asyncio）** | — | 零额外 IO | **采用**：主通道，实时性最好 |
| **watchdog** | Apache-2.0 | 事件驱动，inotify/ReadDirectoryChangesW | **采用**：覆盖 `latest.log` 轮转与外部写入 |
| grafana/loki + promtail | AGPL-3.0 | 高 | 排除：AGPL + 需额外组件 |
| Filebeat | 自定义许可 | 高 | 排除：重量级 |
| `tail -F` 子进程 | — | 中 | 排除：跨平台差、僵尸进程风险 |
| **psutil** | BSD-3 | 微秒级采样 | **采用**：CPU/RSS/线程数/句柄，无需 agent |
| py-mine/mcstatus | Apache-2.0 | 一次 SLP 握手 | 阶段二：外部探活（区分「进程活着」与「服务可连」） |

日志通道设计：`stdout → 解析器（玩家进出/死亡/成就/报错）→ 环形缓冲（2000 行）→ WebSocket 广播`；同时 `watchdog → latest.log 增量 read → 同一解析器`。两路去重靠行内容哈希 + 时间窗，避免重复推送。

### 3.5 插件与配置管理

| 候选 | 许可证/条款 | 判定 |
|---|---|---|
| **Modrinth API v2** | 平台 AGPL，**API 使用不受 AGPL 约束**，有速率限制与 UA 要求 | **主源**：插件/模组/整合包覆盖最全，返回 SHA-1/SHA-512 |
| **Hangar API v1** | MIT（Hangar 本体） | **副源**：Paper 生态官方，项目质量高 |
| SpigotMC SpiGet | 非官方、条款模糊、无稳定 SLA | 不接：合规与稳定性风险 |
| CurseForge API | 需 API Key + 条款限制分发 | 阶段三：有 Key 再开 |
| **ruamel.yaml** | MIT | **采用**：round-trip 保留注释/锚点/键序 |
| PyYAML | MIT | 只用于读取校验（`safe_load`），写回一律走 ruamel |
| configobj | BSD | 备选：properties 语义不如自研可控 |

**配置写入的三重保险**（这是最容易出事故的地方）：
1. 写前 `backup` 到 `.nautilus/backups/{file}.{ts}.bak`，保留最近 20 份；
2. 先写 `*.tmp` 再 `os.replace` 原子替换，避免半截文件导致服务端起不来；
3. 写前用 ruamel/PyYAML **回读校验**语法，校验失败直接回滚并报错。

---

## 4. 推荐组合（最终技术栈）

```
┌────────────────────────────────────────────────────────────┐
│  WebUI  React 19 + Vite 6 + Tailwind 4 + TanStack Query     │  MIT
├────────────────────────────────────────────────────────────┤
│  API    FastAPI + Uvicorn（REST + WebSocket）                │  MIT / BSD-3
├──────────────┬──────────────┬──────────────┬───────────────┤
│ 认证          │ 鉴权          │ 进程监管      │ 观测           │
│ argon2-cffi  │ pycasbin     │ asyncio      │ psutil        │
│ + PyJWT      │ (RBAC+继承)   │ supervisor   │ + watchdog    │
│ MIT          │ Apache-2.0   │ (自研/二封装) │ BSD-3/Apache-2│
├──────────────┴──────────────┴──────────────┴───────────────┤
│  数据   SQLAlchemy 2 (async) + aiosqlite                    │  MIT
├────────────────────────────────────────────────────────────┤
│  外部源  Fill v3 · Purpur v2 · Fabric Meta · piston-meta     │
│          Modrinth v2 · Hangar v1                            │
└────────────────────────────────────────────────────────────┘
```

许可证整体兼容性：**MIT / Apache-2.0 / BSD-3 全部互相兼容**，无 AGPL 传染，可自由闭源分发。项目自身采用 MIT。

---

## 5. 风险登记册

| # | 风险 | 影响 | 概率 | 缓解措施 |
|---|---|---|---|---|
| R1 | 第三方 API 变更/下线（PaperMC v2 已实证发生） | 一键装服失效 | **高** | Provider 抽象层 + 多源回退 + 手动上传 jar 兜底；不把 API 变更写死在路由里 |
| R2 | 面板被入侵 → 直接 RCE（能起进程、能执行指令） | 灾难 | 中 | 强制改初始密码、默认仅监听 127.0.0.1、全局审计、路径白名单、指令黑名单可选、非 root 运行 |
| R3 | 配置写坏导致服务端起不来 | 高 | 中 | 三重保险（备份/原子写/回读校验）+ 一键回滚 |
| R4 | 日志洪泛打爆内存/WS | 中 | 中 | 环形缓冲上限 + 每连接背压丢弃 + 行长度截断 |
| R5 | Windows 下无 SIGTERM，`terminate()` 语义不同 | 中 | **高** | 平台分支：Windows 用 `CTRL_BREAK_EVENT` + `taskkill /T`；优雅停机靠 RCON 优先 |
| R6 | 插件下载物被投毒 | 高 | 低 | 强制哈希校验（SHA-1/SHA-512/SHA-256），缺失哈希则拒绝安装 |
| R7 | 多实例并发写同一配置文件 | 中 | 中 | 每服 asyncio 锁 + 文件 mtime 乐观锁 |
| R8 | 插件 API 速率限制 | 低 | 中 | 结果缓存（TTL 5 分钟）+ 指数退避 |
| R9 | AGPL 污染 | 法务 | 低 | 已排除 pelican/loki；依赖清单做许可证扫描 |

---

## 6. 分阶段实现计划

| 阶段 | 内容 | 交付物 | 状态 |
|---|---|---|---|
| **P0 调研** | 数据采集、API 实测、许可证核验 | 本文档 | ✅ |
| **P1 骨架** | FastAPI 骨架、配置、DB、认证、RBAC、审计 | 可登录、可鉴权的 API | ✅ |
| **P2 核心** | 进程监管、RCON、日志流、指标、WebSocket | 能真实拉起 MC 服务端并操作 | ✅ |
| **P3 资源** | 文件管理、配置管理、插件市场、一键装服 | 面板可完成日常运维全流程 | ✅ |
| **P4 UI** | React 全量页面、暗/亮主题、实时图表 | 可交付的 WebUI | ✅ |
| **P5 加固** | 测试、限流、审计完备、部署脚本 | pytest 全绿 + systemd/Docker | ✅ |
| **P6 演进** | 多节点（agent）、OIDC、备份调度、CurseForge | — | 规划中 |

---

## 7. 复现方式

```bash
gh api graphql -F query=@scripts/repo-metrics.graphql   # 仓库指标
curl -s https://fill.papermc.io/v3/projects/paper/versions/1.21.4/builds/latest
curl -s "https://api.modrinth.com/v2/search?limit=1" -H "User-Agent: nautilus/0.1"
```

---

## 8. Agent 能力补强（2026-11-22）

本轮目标不是换框架，而是把已有 agent 的**运行控制**补齐。先查上游，再按本项目单进程
部署的现实取舍实现；没有引入新运行时依赖。

| 上游项目 / 文档 | 采用的点 | 本项目落地方式 |
|---|---|---|
| [`pydantic/pydantic-ai`](https://github.com/pydantic/pydantic-ai) 2.71 的 [Advanced Tool Features](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/) | `CancellationToken` 从 run 外部取消；Agent/工具级 `tool_timeout`；`ToolFailed` 与重试边界 | `ai/runs.py` + `/api/ai/cancel`；工具按只读/写/安装分 60/120/300s；取消时保留已完成消息 |
| 同项目的 [ProcessHistory](https://pydantic.dev/docs/ai/capabilities/process-history/) 与 `capture_run_messages()` | 历史处理器只影响发给模型的消息；失败运行仍能取回消息 | `ai/agent.py::_compress_history` 压缩旧工具结果（保留最近 12 条、配对不乱）；取消后写回会话 |
| 同项目 [Building AI Agents SKILL.md](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/.agents/skills/building-pydantic-ai-agents/SKILL.md) | 长驻服务要进入 Agent 上下文，避免每轮重建 provider/HTTP client | `stream_turn` 用 `async with agent:` 包住 run |
| [`openai/openai-agents-python`](https://github.com/openai/openai-agents-python) 的 agent loop 与 `max_turns` | 必须有轮次上界；工具多时按领域拆分而不是让一个 agent 面对几十个入口 | 保留 `UsageLimits(request_limit=max_steps)`；用手册做场景化分组，不拆多 agent（会重复一套权限上下文） |
| [`langchain-ai/langgraph`](https://github.com/langchain-ai/langgraph) 的 [Human-in-the-loop](https://github.com/langchain-ai/langgraphjs/blob/main/docs/docs/agents/human-in-the-loop.md) | `interrupt()` / `Command(resume=...)` 的一次性审批语义；过期与重复恢复要处理 | `ai/approvals.py`：30 分钟 TTL、id 全量比对、确认后销毁；重复点击返回 409 |
| Claude Code 的 TodoWrite 与 [Agent SDK Skills](https://platform.claude.com/docs/en/agent-sdk/skills) | 多步任务显式列清单；长指令按需加载，常驻上下文只放摘要 | `ai/plan.py` + `update_plan`（SSE `plan` 事件）；`ai/playbooks.py` 6 份 SOP 按需 `load_playbook` |

**取舍**：没有引入 LangGraph / OpenAI Agents SDK —— 它们会替换掉现有的 pydantic-ai
工具层与 Casbin 权限包装，收益不足以抵消重写风险；本项目只取可独立落地的模式。
也没有启用工具搜索（ToolSearch）：40 个工具尚在可控范围，且国内 OpenAI 兼容端点对
provider 原生工具发现支持不一致，本地策略反而会引入额外往返。

> **2026-11-28 更新**：按升级要求，agent 循环已迁移到 LangGraph（react 图，
> 见 architecture.md §4.5）。上文关于 pydantic-ai 专属 API（`CancellationToken` /
> `ProcessHistory` / `capture_run_messages` / `DeferredToolRequests` / `ThinkingPart`）
> 的描述保留作研究记录，其能力现由 `ai/agent.py`、`ai/model.py`、`ai/runs.py`
> 等价实现；对话历史改为纯 dict，不再携带框架对象。

---

## 8. Agent 能力补强（2026-11-22）

本轮目标不是换框架，而是把已有 agent 的**运行控制**补齐。先查上游，再按本项目单进程
部署的现实取舍实现；没有引入新运行时依赖。

| 上游项目 / 文档 | 采用的点 | 本项目落地方式 |
|---|---|---|
| [`pydantic/pydantic-ai`](https://github.com/pydantic/pydantic-ai) 2.71 的 [Advanced Tool Features](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/) | `CancellationToken` 从 run 外部取消；Agent/工具级 `tool_timeout`；`ToolFailed` 与重试边界 | `ai/runs.py` + `/api/ai/cancel`；工具按只读/写/安装分 60/120/300s；取消时保留已完成消息 |
| 同项目的 [ProcessHistory](https://pydantic.dev/docs/ai/capabilities/process-history/) 与 `capture_run_messages()` | 历史处理器只影响发给模型的消息；失败运行仍能取回消息 | `ai/agent.py::_compress_history` 压缩旧工具结果（保留最近 12 条、配对不乱）；取消后写回会话 |
| 同项目 [Building AI Agents SKILL.md](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/.agents/skills/building-pydantic-ai-agents/SKILL.md) | 长驻服务要进入 Agent 上下文，避免每轮重建 provider/HTTP client | `stream_turn` 用 `async with agent:` 包住 run |
| [`openai/openai-agents-python`](https://github.com/openai/openai-agents-python) 的 agent loop 与 `max_turns` | 必须有轮次上界；工具多时按领域拆分而不是让一个 agent 面对几十个入口 | 保留 `UsageLimits(request_limit=max_steps)`；用手册做场景化分组，不拆多 agent（会重复一套权限上下文） |
| [`langchain-ai/langgraph`](https://github.com/langchain-ai/langgraph) 的 [Human-in-the-loop](https://github.com/langchain-ai/langgraphjs/blob/main/docs/docs/agents/human-in-the-loop.md) | `interrupt()` / `Command(resume=...)` 的一次性审批语义；过期与重复恢复要处理 | `ai/approvals.py`：30 分钟 TTL、id 全量比对、确认后销毁；重复点击返回 409 |
| Claude Code 的 TodoWrite 与 [Agent SDK Skills](https://platform.claude.com/docs/en/agent-sdk/skills) | 多步任务显式列清单；长指令按需加载，常驻上下文只放摘要 | `ai/plan.py` + `update_plan`（SSE `plan` 事件）；`ai/playbooks.py` 6 份 SOP 按需 `load_playbook` |

**取舍**：没有引入 LangGraph / OpenAI Agents SDK —— 它们会替换掉现有的 pydantic-ai
工具层与 Casbin 权限包装，收益不足以抵消重写风险；本项目只取可独立落地的模式。
也没有启用工具搜索（ToolSearch）：40 个工具尚在可控范围，且国内 OpenAI 兼容端点对
provider 原生工具发现支持不一致，本地策略反而会引入额外往返。

> **2026-11-28 更新**：按升级要求，agent 循环已迁移到 LangGraph（react 图，
> 见 architecture.md §4.5）。上文关于 pydantic-ai 专属 API（`CancellationToken` /
> `ProcessHistory` / `capture_run_messages` / `DeferredToolRequests` / `ThinkingPart`）
> 的描述保留作研究记录，其能力现由 `ai/agent.py`、`ai/model.py`、`ai/runs.py`
> 等价实现；对话历史改为纯 dict，不再携带框架对象。

---

## 9. Ollama 模型发现（2026-11-22）

用户反馈：选了 Ollama 之后还要手抄 `qwen2.5:7b` 这种模型名，容易拼错。目标是不换
provider，直接从服务端拉取可选模型。

| 上游资料 | 确认到的事实 | 落地方式 |
|---|---|---|
| [Ollama 官方 `/api/tags`](https://docs.ollama.com/api/tags) | `GET /api/tags` 返回 `models[]`，含 `name` / `size` / `modified_at` / `details.parameter_size` / `details.quantization_level` | 本地端点额外读它，补全参数量与量化等级 |
| [Ollama 官方 OpenAI 兼容页](https://docs.ollama.com/openai) | `GET /v1/models` 可用；本地服务 `api_key` 必填但被忽略；`created` 是修改时间、`owned_by` 默认是 `library` | 用通用接口拿模型名单；本地服务允许空 Key，provider 侧填占位值 `ollama` |
| Ollama 的两种 base_url 写法 | 用户可能填 `http://host:11434` 或 `http://host:11434/v1` | `endpoints()` 归一化两种写法，分别拼出 `/v1/models` 与 `/api/tags` |

**实现要点**：先问通用接口，本地端点（含 `host.docker.internal`）再问原生接口；
通用接口失败时原生接口兜底，两边结果按模型 id 合并去重，原生详情优先。
探测只发 GET，6s 超时、响应上限 2 MB，失败原因与排查提示随 JSON 返回给前端，
不当成 5xx。设置 `system:manage` 权限才可调，避免把内网服务信息暴露给只读用户。

**取舍**：没有为 Ollama 单独接 `ollama` Python SDK —— 现有 provider 已经是 OpenAI 协议，
再加一个 SDK 只会多一套依赖和鉴权分支；模型发现只需要两个 GET，直接走 `httpx` 更轻。
也没有把模型列表缓存下来：本地模型随时可能 `ollama pull`，每次点按钮现取才不会选到过期名字。

---

## 9. Ollama 模型发现（2026-11-22）

用户反馈：选了 Ollama 之后还要手抄 `qwen2.5:7b` 这种模型名，容易拼错。目标是不换
provider，直接从服务端拉取可选模型。

| 上游资料 | 确认到的事实 | 落地方式 |
|---|---|---|
| [Ollama 官方 `/api/tags`](https://docs.ollama.com/api/tags) | `GET /api/tags` 返回 `models[]`，含 `name` / `size` / `modified_at` / `details.parameter_size` / `details.quantization_level` | 本地端点额外读它，补全参数量与量化等级 |
| [Ollama 官方 OpenAI 兼容页](https://docs.ollama.com/openai) | `GET /v1/models` 可用；本地服务 `api_key` 必填但被忽略；`created` 是修改时间、`owned_by` 默认是 `library` | 用通用接口拿模型名单；本地服务允许空 Key，provider 侧填占位值 `ollama` |
| Ollama 的两种 base_url 写法 | 用户可能填 `http://host:11434` 或 `http://host:11434/v1` | `endpoints()` 归一化两种写法，分别拼出 `/v1/models` 与 `/api/tags` |

**实现要点**：先问通用接口，本地端点（含 `host.docker.internal`）再问原生接口；
通用接口失败时原生接口兜底，两边结果按模型 id 合并去重，原生详情优先。
探测只发 GET，6s 超时、响应上限 2 MB，失败原因与排查提示随 JSON 返回给前端，
不当成 5xx。设置 `system:manage` 权限才可调，避免把内网服务信息暴露给只读用户。

**取舍**：没有为 Ollama 单独接 `ollama` Python SDK —— 现有 provider 已经是 OpenAI 协议，
再加一个 SDK 只会多一套依赖和鉴权分支；模型发现只需要两个 GET，直接走 `httpx` 更轻。
也没有把模型列表缓存下来：本地模型随时可能 `ollama pull`，每次点按钮现取才不会选到过期名字。

---

## 10. 多会话、上下文压缩与反馈感（2026-11-22）

用户提出四件事：多会话、上下文压缩、提示词优化、前端反馈。上游对照如下。

| 上游资料 | 确认到的做法 | 本项目落地 |
|---|---|---|
| [Anthropic: Best practices for Claude Code](https://www.anthropic.com/engineering/claude-code-best-practices) | `--resume` / `--rename` 把会话当分支；`/compact <focus>` 可指定压缩重点；`/clear` 换任务 | 会话侧栏支持新建/切换/重命名/删除；`POST .../compact` 带 focus；新对话按钮相当于 `/clear` |
| [Claude Code 上下文窗口说明](https://github.com/estasney/myclaudecode/blob/master/my-claude-code/skills/claude-code-mastery/references/upstream/context-window.md) | 压缩后系统等仍重新注入，只摘要历史；保留最近修改过的文件/关键决策 | 静态 instructions 每轮照常发送，只摘要旧轮次；摘要里强制保留服务器 id、路径、插件 slug、未完成步骤 |
| [LangGraph 短时记忆](https://python.langchain.com/docs/modules/memory/chat_messages/) 与 [Memory 概念](https://github.com/gbaian10/langgraph/blob/780285ef916883382856c88c79d49c5a7a0060d7/docs/docs/concepts/memory.md) | 先摘要再裁剪；`start_on='human'` 保证历史以用户消息开头，工具返回必须跟在对应调用后 | `safe_cut()` 只切在用户提问开头；摘要作为完整 user/assistant 一对放回，后面接最近 10 轮原文 |
| [pydantic-ai Agents](https://pydantic.dev/docs/ai/core-concepts/agents/) | 静态 instructions 排前面利于缓存，动态/运行时指令每次重新计算；指令属于操作者权限 | 静态提示词保持前缀稳定；每轮用 run 级 `instructions` 注入时间、操作者角色，viewer 明确限制只读 |
| [Vercel AI SDK useChat](https://sdk.vercel.ai/docs/ai-sdk-ui/generative-user-interfaces) | `status` 四态机（ready/submitted/streaming/error）驱动感知质量；工具部件按 state 渲染；消息用 parts 而不是整段字符串 | 前端按 ready/连接中/生成中/出错/已停止显示状态；工具区分执行中/待确认/完成/失败并显示耗时；历史用事件序列回放 |

**取舍**：没有引入 LangGraph/Vercel AI SDK —— 它们会替换现有 pydantic-ai 工具层与 SSE
协议，收益不足以抵消重写风险；只取可独立落地的模式。压缩没有固定接 tiktoken：
按字符估算即可触发，避免为一个面板再背一份分词器；模型摘要失败时用确定性摘要兜底，
不让一次网络抖动把会话拖死。会话仍只在进程内（重启丢失），这与原有设计一致。

> **2026-11-28 注**：LangGraph 已按升级要求接管 agent 循环；SSE 事件合同保持不变。

---

## 10. 多会话、上下文压缩与反馈感（2026-11-22）

用户提出四件事：多会话、上下文压缩、提示词优化、前端反馈。上游对照如下。

| 上游资料 | 确认到的做法 | 本项目落地 |
|---|---|---|
| [Anthropic: Best practices for Claude Code](https://www.anthropic.com/engineering/claude-code-best-practices) | `--resume` / `--rename` 把会话当分支；`/compact <focus>` 可指定压缩重点；`/clear` 换任务 | 会话侧栏支持新建/切换/重命名/删除；`POST .../compact` 带 focus；新对话按钮相当于 `/clear` |
| [Claude Code 上下文窗口说明](https://github.com/estasney/myclaudecode/blob/master/my-claude-code/skills/claude-code-mastery/references/upstream/context-window.md) | 压缩后系统等仍重新注入，只摘要历史；保留最近修改过的文件/关键决策 | 静态 instructions 每轮照常发送，只摘要旧轮次；摘要里强制保留服务器 id、路径、插件 slug、未完成步骤 |
| [LangGraph 短时记忆](https://python.langchain.com/docs/modules/memory/chat_messages/) 与 [Memory 概念](https://github.com/gbaian10/langgraph/blob/780285ef916883382856c88c79d49c5a7a0060d7/docs/docs/concepts/memory.md) | 先摘要再裁剪；`start_on='human'` 保证历史以用户消息开头，工具返回必须跟在对应调用后 | `safe_cut()` 只切在用户提问开头；摘要作为完整 user/assistant 一对放回，后面接最近 10 轮原文 |
| [pydantic-ai Agents](https://pydantic.dev/docs/ai/core-concepts/agents/) | 静态 instructions 排前面利于缓存，动态/运行时指令每次重新计算；指令属于操作者权限 | 静态提示词保持前缀稳定；每轮用 run 级 `instructions` 注入时间、操作者角色，viewer 明确限制只读 |
| [Vercel AI SDK useChat](https://sdk.vercel.ai/docs/ai-sdk-ui/generative-user-interfaces) | `status` 四态机（ready/submitted/streaming/error）驱动感知质量；工具部件按 state 渲染；消息用 parts 而不是整段字符串 | 前端按 ready/连接中/生成中/出错/已停止显示状态；工具区分执行中/待确认/完成/失败并显示耗时；历史用事件序列回放 |

**取舍**：没有引入 LangGraph/Vercel AI SDK —— 它们会替换现有 pydantic-ai 工具层与 SSE
协议，收益不足以抵消重写风险；只取可独立落地的模式。压缩没有固定接 tiktoken：
按字符估算即可触发，避免为一个面板再背一份分词器；模型摘要失败时用确定性摘要兜底，
不让一次网络抖动把会话拖死。会话仍只在进程内（重启丢失），这与原有设计一致。

> **2026-11-28 注**：LangGraph 已按升级要求接管 agent 循环；SSE 事件合同保持不变。

---

## 11. 推理过程可视化与即时反馈（2026-11-22）

用户反馈：「对话 UI 反馈不够，要看到 AI 的推理过程，从一发送就要有反应」。

| 上游资料 | 确认到的做法 | 本项目落地 |
|---|---|---|
| [pydantic-ai Thinking 能力](https://pydantic.dev/docs/ai/capabilities/thinking/) 与 [流式事件](https://pydantic.dev/docs/ai/core-concepts/streaming/) | 推理内容用 `ThinkingPart` 表示，流式增量是 `PartDeltaEvent(ThinkingPartDelta)`，与 `TextPartDelta` 同属 `ModelResponsePartDelta`；OpenAI 兼容端点原生字段经 profile 解析 | `agent.py` 捕获 `ThinkingPartDelta` 发 SSE `thinking`，前端流式追加到可折叠「思考过程」区 |
| pydantic-ai `OpenAIChatModel` 源码（本机 2.71 内省） | 未配 profile 时默认检查 `reasoning`（Ollama/gpt-oss）与 `reasoning_content`（DeepSeek/Moonshot）两个字段并转成 ThinkingPart；`openai_chat_send_back_thinking_parts='auto'` 在工具轮次正确回传 | 不改 `build_model`：默认 profile 已覆盖主流国产推理模型，用假 provider 实测 reasoning_content → ThinkingPartDelta 验证通过 |
| [Vercel AI SDK：generative UI](https://sdk.vercel.ai/docs/ai-sdk-ui/generative-user-interfaces) 与 `useChat` 的 `status` / `streaming` 部件状态 | 工具/推理部件按 state 渲染；提交后立刻给出乐观 UI，不等服务端首字节 | 发送同帧插入用户消息 + 「正在思考」骨架；新增 `submitted → thinking → streaming` 状态机，推理区用 `thinking-dots`/`thinking-pulse` 动画，正文到达即标记推理完成 |

**取舍**：没有接入专用推理 SDK，也没有强制开启 `reasoning_effort`——是否推理由用户所选模型决定，
面板只负责如实呈现模型返回的思考增量；非推理模型 UI 上自然没有这一块。
SSE 用新增 `started` 事件解决「首字节前空白」的问题，而不是靠前端定时器假装在动。
思考内容随会话序列化保存（截断 2000 字符），切换回来仍可回看当时的推理。

---

## 12. Agent 运行轨迹与前端可观测性（2026-11-22）

用户反馈：「提高 agent 能力，前端页面增强，能看到 agent 干了什么、用了什么工具」。
核心不是再换一套 agent 框架，而是把**已经发生的决策与执行**如实、逐步地呈现出来，
让耗时与成败有服务端证据，而不是靠前端估算。

| 上游资料 | 确认到的做法 | 本项目落地 |
|---|---|---|
| [`munish-shah/Orchestrator-AI-Agent-Platform`](https://github.com/munish-shah/Orchestrator-AI-Agent-Platform) 的 `run_tracker.py` + `Runs.jsx` | 把每个 thought / tool call / result 记成一步，用时间线视图回看整轮运行 | `ai/trace.py`：一次 run 一条 `RunTrace`，逐步记 model / tool / plan / approval / note；前端「执行轨迹」Modal 按 seq 渲染时间线 |
| [`agentscope-ai/agentscope-studio`](https://github.com/agentscope-ai/agentscope-studio) 的统一事件流与 trace 可视化 | reasoning、工具调用、多模态走同一条事件流；trace 可视化 LLM 调用与 token | 每个 SSE 事件统一带 `run_id` + `seq` + `ts`；`done` 带 usage；轨迹用同一 run_id 串起提问、审批与续跑 |
| [`agno-agi/agent-ui`](https://github.com/agno-agi/agent-ui)（`nghyane/agent-ui` 镜像） | 工具调用与推理步骤作为结构化部件渲染，按 state 显示 | 前端按 step 的 kind/status 上色：工具失败红、进行中/待确认橙、完成灰；可展开看参数与结果 |
| [`moulishb/ai-agent-glassbox`](https://github.com/moulishb/ai-agent-glassbox) 的 Full Trajectory Visualization | 强调「每个决策、每次工具调用都可见可审计」 | 工具耗时用 `time.perf_counter()` 在服务端实测（不是浏览器收到事件的间隔）；参数/结果与审计日志一致 |

**实现要点**：审批续跑复用同一个 `run_id`，`begin()` 对已存在的 run 只改状态不丢步骤，
所以「提问 → 等确认 → 执行」是一条连续时间线；续跑时把上一轮挂着的 `approval` 步收尾成
`done`，避免轨迹永远停在 waiting。轨迹存进程内（与会话、计划、审批同生命周期），
按 `MAX_RUNS` LRU 与 6h TTL 清理；删除会话时一并清掉轨迹；越权访问轨迹按 404 处理。

**取舍**：没有引入 LangSmith / OpenTelemetry / Langfuse 这类外部观测后端 —— 它们要额外
部署服务、上报密钥和网络往返，对单用户面板的排障收益抵不上运维成本；本地进程内轨迹
已经能回答「哪一步慢、哪个工具失败、参数是什么」。也没有把轨迹落库：它与会话消息同
生命周期，重启即清，符合现有单 worker 的定位。前端没有把时间线塞进对话气泡逐条重建，
而是点开 Modal 拉服务端权威轨迹，避免流式事件丢失/乱序导致展示与真实执行不一致。

---

## 13. 同类面板对照：功能查缺补漏与 UI 换肤（2026-11-22）

用户要求「完善功能，去 GitHub 找同类项目，查缺补漏，UI 再换一套」。先对照同类面板，
再把"后端已有能力但前端没入口"的断链接上，最后换主题。

| 上游项目 | 值得借鉴的点 | 本项目落地（或取舍） |
|---|---|---|
| [`and3rn3t/minecraft`](https://github.com/and3rn3t/minecraft) | 备份保留策略 + 异地备份、版本兼容检查与引导升级、metrics 日志轮转与搜索 | 备份保留与异地存储超出单面板范围，保留接口由外部任务处理；版本列表/安装已具备，未加引导式升级向导 |
| [`alex190291/MineCP`](https://github.com/alex190291/MineCP) | 可视化 server.properties 编辑器、Modrinth/CurseForge 安装、实时 WS 监控 | 配置编辑器已有结构化/源码双模式；插件市场已有 Modrinth/Hangar；监控补了时间窗口切换 |
| [`Niki2k1/mcsm`](https://github.com/Niki2k1/mcsm) | 每台服务器独立多页（Overview/Config/Players/Backups/World/Map/Files…）、Modrinth 更新徽章、Monaco 编辑器 + YAML/JSON 校验、服务器列表内建预览 | 本项目用单页 + Tab 达到同样分区；补了插件「检查更新」与可更新徽章；编辑器保持 textarea + 后端原子写/语法校验，不引入 Monaco（体积与依赖不划算）；列表预览需要实时 ping，不在本轮范围 |
| [`septi0/mc-server-webadmin`](https://github.com/septi0/mc-server-webadmin) | 响应式布局、OIDC 登录、世界/玩家选项完整 | 响应式已有（侧栏折叠、Grid 断点）；OIDC 需引入外部 IdP 配置，超出本地面板定位；世界与玩家选项已覆盖 |
| [`Mallard-Dash/Aurora-cloud`](https://github.com/Mallard-Dash/Aurora-cloud) | 统一仪表盘 + AI 助手结合系统健康上下文 | AI 助手已能读面板信息（panel_info / server_status），不再加一套 AI；主题从暗色赛博改为工业亮色，见下 |

**查缺补漏结果**（以"后端已有、前端缺入口"为准，优先补真正的断链）：

1. **权限感知**（安全 + 体验）：后端 4 角色 13 动作、读写共用 `server:<action>`，但 9 个详情 Tab 原来
   只传 `serverId`，低权限角色能看到一点了就 403 的按钮。现在由 `/auth/me` 展开权限驱动 Tab 可见性，
   删除服务器单独看 `server:delete`，总览「新建」看 `server:create`。授权仍在服务端，前端只做视觉收敛。
2. **个人设置**：后端 `POST /auth/password` 早就有，前端菜单里却写着「个人设置（P6）」禁用项；
   现在落地为可改自己密码的页面，并展示角色与权限清单。
3. **文件管理**：接上闲置的 `POST /files/rename` 与 `files?hidden=`；新建目录从 `window.prompt` 换成 Modal；
   补当前目录搜索；列表统一空/加载/错误三态。
4. **插件页**：接上闲置的 `GET /plugins/updates`（检查更新 + 可更新徽章）与 search 的 `limit/offset` 分页。
5. **统一交互**：7 处 `window.confirm` 换成全局 ConfirmProvider（Promise 风格 Modal）；
   抽出 `AsyncState`（ListLoading/ListError/ListEmpty），先用在总览修复"查询失败被当空列表"的问题。
6. **可观测性补完**：把闲置的 `GET /system`、`GET /meta` 做成系统信息卡片；
   审计表格展开 `detail` 并导出当前页 CSV；监控加 5/15/60 分钟与全部的时间窗口。

**UI 换肤取舍**：从 `moss` 灰绿暗色终端风换成 `steelblue` 工程蓝**亮色工业控制台**——浅灰底、
深墨字、单一强调色、方角、1px 边框、无阴影渐变。只改主题色板与 CSS 变量，不换组件库：
Mantine 8 的组件行为、权限与 SSE 协议都不受影响，回归风险最小。终端与代码编辑器保留深色，
因为它们是内容面板而非界面 chrome，亮色反而刺眼。暗色仍可手动切换，两套 CSS 变量都维护。

**未做**：没有引入 LangGraph 之类替换 agent；没有接 OIDC、Monaco、异地备份、实时服务器列表预览
（前两者需外部基础设施，后两者超出"面板自身功能"的范围）；没有做国际化与全局命令面板（优先级靠后）。

---

## 14. 工业仪表动效系统（2026-11-22）

用户要求「ui/ux 动画拉满」。但面板不是营销站：它 4 秒轮询一次服务器列表、长时间停留在
监控页，动画如果"满"到会拖慢操作或干扰读数，就是负资产。所以这轮的目标不是加动画，
而是**建立一套统一的动效语言**，让每一次状态变化都有"物理含义"。

### 14.1 动效语言：`frontend/src/motion.tsx`

单一入口导出三样东西，全站只用这三样，避免每页各写一套 timing：

| 导出 | 语义 | 实现 |
|---|---|---|
| `EASE.snap` | 继电器吸合：干脆、无回弹 | `cubic-bezier(.2,.9,.25,1)` |
| `EASE.measure` | 仪表回落：连续量 | `cubic-bezier(.4,0,.2,1)` |
| `EASE.exit` | 退场比入场快 | `cubic-bezier(.4,0,1,1)` |
| `pageVariants` | 页面级进出场 | 位移 8px + 淡入 |
| `staggerContainer` / `staggerItem` | 面板上电式交错入场 | step 35ms |
| `cardVariants` / `rowVariants` | 卡片 / 表格行落位 | scale 0.985 → 1 |
| `popVariants` | Mantine 自绘弹层 | 缩放 + 上浮 |
| `useCountUp` | 数字机械滚动 | `motion.animate` 补间 |
| `useRevealOnScroll` | 长列表进入视口才动 | `useInView` |
| `useMotionPref` | 全局减弱动效开关 | `useReducedMotion` |

**关键约束**：所有动画只碰 `transform` / `opacity`，不碰 `width/height/top/left`。
这条不是审美偏好而是硬约束 —— 页面同时在跑 4 秒轮询，任何触发布局的属性动画都会让
滚动和点击掉帧。

### 14.2 CSS 层的物理反馈（`frontend/src/styles.css`）

JS 动画管"进场"，CSS 动画管"持续状态"：

- **工程网格背景**：`repeating-linear-gradient` 画 24px 网格，作用在 `.shell-main`，
  不放 `body`（登录页是全屏留白，铺网格会变成噪点）。
- **状态灯呼吸**：`.stage-led` 用 `box-shadow` 脉冲，AI 助手运行中那颗灯在"呼吸"。
- **骨架屏 shimmer**：背景位移而非整块闪烁，避免频闪。
- **扫描线**：轨迹时间线上的 `.trace-running` 有一条横扫的高光，表示"正在跑"。
- **导航指示条**：侧栏当前项左侧 2px 强调条，切换时用 `layoutId` 平移。
- **按键反馈**：`.btn-press` 提供按下的物理压感（Mantine `ActionIcon` 不接受 `whileTap`，
  所以走 CSS 而非 motion 组件）。

### 14.3 降级路径

`prefers-reduced-motion: reduce` 时：`MotionProvider` 把 `reduced` 广播下去，
`useCountUp` 直接跳到目标值，`useRevealOnScroll` 直接显示，
CSS 侧统一 `animation: none` + `transition: none`。
系统开启"减弱动态效果"的用户看到的是完全静态的界面，功能一个不少。

### 14.4 打包体积

`motion` 拆成独立 chunk（`vite.config.ts` 的 `manualChunks`），主包从 443KB 降到 309KB，
motion 独立 133KB（gzip 44KB）。不拆的话，登录页这一个静态页面也要下载整包动效库。

**未做**：没有做视差滚动、粒子背景、Lottie、自定义光标 —— 那是展示型页面的语言，
放在需要盯着看 30 秒的运维界面上只会分散注意力。
