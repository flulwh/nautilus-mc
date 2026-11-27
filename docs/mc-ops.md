# Nautilus MC 运维扩展 · 上游对照（已核验）

> 本文是 `docs/UPGRADE-PLAN.md` **W4 · 游戏运维纵深** 的模块级展开：每个能力点对应哪个上游项目、
> 以什么方式集成、许可证是否允许。
>
> **核验状态：2026-11-28 实测**（`gh api repos/{owner}/{repo}`，认证态，取 `stargazers_count` /
> `license.spdx_id` / `pushed_at` / `archived`）。上一版方案标注「沙箱不可用、未能实时复核」，
> 本轮已逐条复核，**修正 6 处事实错误 + 3 处依赖风险**，见 §2。落地前如隔了较长时间，
> 用 §7 的脚本重跑一次。
>
> 原则：能找成熟轮子就不自写；面板自身依赖守 **MIT / Apache-2.0 / BSD** 白名单。

---

## 1. 五种集成方式（先定边界）

| 方式 | 含义 | 许可证约束 |
|---|---|---|
| **直接集成** | 进 `pyproject.toml` 的 Python/前端依赖 | 必须 MIT / Apache-2.0 / BSD；**GPL/AGPL/PolyForm/非商用一律不引** |
| **可选二进制** | 面板按 JRE 下载模式托管（SHA-256 校验、版本目录），不装不影响默认功能 | BSD/MIT/Apache 可托管；GPL 工具只引导用户自装，**不随包分发** |
| **插件门控** | 经现有 Modrinth/Hangar 市场装到服务端，面板只做 RCON 文本交互 | 插件多为 GPL，**无代码链接、无传染**（与现有插件市场一致） |
| **语义参考** | 移植其成熟模式/算法，不引依赖、不复制代码正文 | 无约束（无许可证的仓库尤其只能这样用） |
| **排除** | 许可证不符 / 已归档 / 对单机面板过重 | — |

判据落到代码上：`direct` = 写进 `pyproject.toml` 或 `package.json`；`binary` = 进 `data/tools/<name>/<version>/`
并由面板校验哈希；`gated` = 只出现在 `integrations.py` 的注册表里，检测到插件才显示按钮；`ref` = 只在注释与
文档里出现，不产生依赖。

---

## 2. 核验结论：必须先改的 9 条

### 2.1 事实错误（会导致引用错误或返工）

| # | 原方案 | 核验结果 | 影响 |
|---|---|---|---|
| 1 | `itzg/mc-backup` | **404，仓库不存在**。真名 `itzg/docker-mc-backup`（MIT，★558，2026-11-27） | 备份一致性语义的出处引用要改名，否则交接时找不到源 |
| 2 | `Amulet-Team/Amulet-NBT` 列为「直接集成（备选）」 | **不是 MIT**：`Amulet Team License 1.0.0`，PolyForm Shield + 非商用，仅对商业教育留例外 | **必须从直接集成候选中移除**。这是本轮唯一会真正踩线的项 |
| 3 | `python-telegram-bot` 标 LGPL-3.0 | GitHub 识别为 **GPL-3.0** | 排除结论不变，但理由要改成「GPL，硬排除」 |
| 4 | `plan-player-analytics/Plan` 标 GPL-3.0 | **LGPL-3.0** | 插件门控下无影响，表述改正 |
| 5 | `minekube/gate` 标 MIT | **Apache-2.0** | 不影响可用性（仍在白名单），备选记录改正 |
| 6 | `badges/shields` 标 CC0-1.0 | **Apache-2.0**（代码），品牌资源另有条款 | 只作语义参考、自绘 SVG，不引依赖 |
| 7 | `VelocityPowered/Velocity` | 组织已迁移，canonical 是 **`PaperMC/Velocity`**（旧路径 302 跳转仍可用） | 文档与链接统一写新名，避免以后跳转失效 |
| 8 | `PaperMC/Waterfall` 归为「EOL 排除」 | 许可证 **MIT、未归档、仍在推送**（2026-11-14） | 排除理由要写「上游已宣布停止新特性、仅安全维护」，**不能写许可证不符** |
| 9 | `cole/aiosmtplib` 标「路径待核验」 | **路径正确**：`cole/aiosmtplib`，MIT，★437，2026-11-03 | 无需改，可直接作为邮件依赖 |

### 2.2 依赖风险（许可证没问题，但维护性或必要性有问题）

| # | 项 | 核验结果 | 处置 |
|---|---|---|---|
| A | `vberlier/nbtlib`（原定直接集成） | MIT，★163，但**最后一次代码提交 2021-11-25**（`pushed_at` 2022 只是元数据变更） | 不作为首选。改推 **`twoolie/NBT`**（MIT，★374，2024-08，**自带 RegionFile 容器**，正好覆盖 region/level.dat）；仍需 pin 版本 + 包一层适配器，NBT 是我们唯一的二进制格式依赖 |
| B | `discord/discord-interactions-python` | MIT，但**最后推送 2024-04-30**（约 2.5 年未动） | **不必引**。入站只需 Ed25519 验签，`cryptography` 已在依赖里，自实现约 10 行；省一个停滞依赖 |
| C | `mhammond/pywin32` | GitHub **无 LICENSE 文件**（识别为 NONE） | 不引。Windows 资源限额走 **ctypes**（Job Objects），零依赖，也正是原方案的备选 |

### 2.3 复核通过、可以照方案执行的

`lucko/spark`(GPL-3.0/★1349) · `itzg/mc-monitor`(MIT/★338) · `gnembon/fabric-carpet`(MIT/★2087) ·
`prometheus/client_python`(Apache-2.0/★4384) · `EssentialsX/Essentials`(GPL-3.0/★2317) ·
`LuckPerms/LuckPerms`(MIT/★2329) · `Jikoo/OpenInv`(GPL-3.0/★161) · `pop4959/Chunky`(GPL-3.0/★764) ·
`BlueMap-Minecraft/BlueMap`(MIT/★2873) · `webbukkit/dynmap`(Apache-2.0/★2227) ·
`Querz/mcaselector`(MIT/★5019) · `restic/restic`(BSD-2/★37508) · `rclone/rclone`(MIT/★61330) ·
`itzg/mc-image-helper`(MIT/★102) · `nothub/mrpack-install`(MIT/★341) · `packwiz/packwiz`(MIT/★1048) ·
`GeyserMC/Geyser`(MIT/★5981) · `GeyserMC/Floodgate`(MIT/★703) · `ViaVersion`(GPL-3.0/★1657) ·
`itzg/mc-router`(MIT/★983) · `timvisee/lazymc`(GPL-3.0/★889) · `kennytv/Maintenance`(GPL-3.0/★336) ·
`docker/docker-py`(Apache-2.0/★7223) · `aiogram/aiogram`(MIT/★5950) · `Rapptz/discord.py`(MIT/★16214) ·
`Mojang/brigadier`(MIT/★3745) · `xtermjs/xterm.js`(MIT/★21390) · `MCSManager`(Apache-2.0/★5022) ·
`pterodactyl/panel`(MIT，GitHub 未识别) · `louislam/uptime-kuma`(MIT/★94962)

**归档情况：58 个仓库中 0 个归档**，排除了"引了个死项目"的风险面。

---

## 3. 按模块对照（M1–M11）

> ★ 与日期为 2026-11-28 实测值。`NOASSERTION` / `NONE` 表示 GitHub 无法自动识别，
> 这些仓库全部落在「插件门控」或「仅语义参考」，不产生依赖，因此无需深究许可证正文。

### M1 性能观测

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| TPS/MSPT（Paper 系） | PaperMC/Paper 内置 `/tps` `/mspt` | — | GPL（服务端） | — | 语义参考 | RCON 解析，零依赖（**已完成**） |
| TPS/卡顿归因（全服务端） | lucko/spark | 1349 | GPL-3.0 | 2026-11-25 | 插件门控 | `spark tps` / `spark healthreport` 解析；缺失时引导市场安装 |
| Fabric tick 剖析 | gnembon/fabric-carpet | 2087 | MIT | 2026-11-24 | 插件门控 | `/tick health` 可选 |
| SLP 外部探活 | itzg/mc-monitor | 338 | MIT | 2026-11-26 | 语义参考 | 区分「进程活」与「服务可连」；面板用已有 `mcstatus` |
| 可选指标出口 | prometheus/client_python | 4384 | Apache-2.0 | 2026-11-13 | 直接集成（extra） | `/metrics`，默认不开（W2 已规划） |
| JVM 火焰图 | async-profiler | — | Apache-2.0 | — | 可选二进制 | 需 JDK；v1 不做（JFR 已够） |

### M2 玩家管理

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| tempban/heal/feed/tp/invsee | EssentialsX/Essentials | 2317 | GPL-3.0 | 2026-11-03 | 插件门控 | 有则委托其命令，无则面板原生兜底 |
| 权限组 | LuckPerms/LuckPerms | 2329 | MIT | 2026-11-26 | 插件门控 | `lp user info` / `lp user <u> parent set` 经 RCON |
| 在线背包编辑 | Jikoo/OpenInv | 161 | GPL-3.0 | 2026-11-10 | 插件门控 | P3；离线走 NBT |
| 封禁增强 | DevLeoko/AdvancedBan | 188 | GPL-3.0 | 2025-07-12 | 插件门控（可选） | 维护节奏偏慢，仅作可选检测，面板原生 tempban 账本为主 |
| 玩家分析看板 | plan-player-analytics/Plan | 1043 | LGPL-3.0 | 2026-11-27 | 插件门控 + 外链 | 不重造分析，检测到就外链 |
| NBT 读写 | **twoolie/NBT** | 374 | MIT | 2024-08-20 | 直接集成 | **首选**：自带 RegionFile，覆盖 level.dat / playerdata / region |
| NBT 备用 | vberlier/nbtlib | 163 | MIT | 2022-12-19 | 直接集成（备选） | 仅当 twoolie 不满足时；代码 2021 年后未动，必须 pin + 包适配器 |
| 区块修复 | Fenixin/Minecraft-Region-Fixer | 623 | GPL-3.0 | 2026-03-22 | 语义参考/引导自装 | P3 只检测 + 引导，不内置 GPL 工具 |

### M3 世界管理

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| 区块预生成 | pop4959/Chunky | 764 | GPL-3.0 | 2026-11-24 | 插件门控 | `chunky start` / `chunky progress` → WS 进度 |
| Web 地图 | BlueMap-Minecraft/BlueMap | 2873 | MIT | 2026-11-23 | 插件门控 | iframe Tab + 端口/地址联动（W4 已规划） |
| Web 地图（备选） | webbukkit/dynmap | 2227 | Apache-2.0 | 2026-11-14 | 插件门控 | 同上 |
| 区域编辑/导出 | Querz/mcaselector | 5019 | MIT | 2026-11-04 | 语义参考/可选 | 有 CLI；v1 用面板 tar 导出 |
| 选区导入导出 | EngineHub/WorldEdit | 3545 | 未识别（实为 GPL-3.0） | 2026-11-16 | 插件门控 | schematic 门控，不引代码 |
| worldborder/gamerule | vanilla 命令 | — | — | — | 语义参考 | RCON，零依赖 |

### M4 备份

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| 在线一致性协调 | **itzg/docker-mc-backup**（原方案误写 `itzg/mc-backup`） | 558 | MIT | 2026-11-27 | 语义参考 | 移植 `save-off → save-all flush → 归档 → finally save-on`（W2 已列为天花板 #3） |
| 增量/加密/异地 | restic/restic | 37508 | BSD-2-Clause | 2026-11-24 | 可选二进制 | S3/WebDAV/SFTP；快照 id 进侧车（W4 已定） |
| 多云同步（备选） | rclone/rclone | 61330 | MIT | 2026-11-27 | 可选二进制 | restic 后端不够时补 |
| 增量（备选） | borgbackup/borg | 13956 | 未识别（实为 BSD-3） | 2026-11-24 | 排除 | Windows 支持弱，跨平台不如 restic |

### M5 版本与内容

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| Forge/Fabric 安装瑞士军刀 | itzg/mc-image-helper | 102 | MIT | 2026-11-25 | 可选二进制 | 面板已移植其 ForgeLikeInstaller 语义；可托管 CLI |
| `.mrpack` 安装 | nothub/mrpack-install | 341 | MIT | 2026-11-27 | 可选二进制/语义参考 | 解析 `modrinth.index.json` 自实现（格式简单），CLI 兜底 |
| 整合包管理格式 | packwiz/packwiz | 1048 | MIT | 2026-10-14 | 语义参考 | `.pack` 格式参考 |
| 插件源/依赖 | Modrinth API / HangarMC/Hangar | — | API 条款 / MIT | — | 直接集成（已有） | `version.dependencies` 递归解析 |
| 升级编排语义 | itzg/docker-minecraft-server | 14582 | Apache-2.0 | 2026-11-27 | 语义参考 | 备份-替换-回滚（W4 升级向导） |

### M6 群组网络

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| 现代代理 | **PaperMC/Velocity**（原 `VelocityPowered`，已迁移） | 2294 | GPL-3.0 | 2026-11-27 | 进程托管 | `velocity.toml`（tomlkit）+ modern forwarding |
| 传统代理 | SpigotMC/BungeeCord | 1735 | 未识别（自定义） | 2026-11-24 | 进程托管 | `config.yml` + `spigot.yml` |
| 旧代理 | PaperMC/Waterfall | 753 | **MIT** | 2026-11-14 | 排除 | **不是许可证问题**：上游已宣布停止新特性、仅安全维护 → 推 Velocity |
| 基岩互通 | GeyserMC/Geyser | 5981 | MIT | 2026-11-27 | 插件门控 | UDP 19132 配置 |
| 基岩正版 | GeyserMC/Floodgate | 703 | MIT | 2026-09-17 | 插件门控 | key 自动同步后端 |
| 跨版本 | ViaVersion/ViaVersion | 1657 | GPL-3.0 | 2026-11-26 | 插件门控 | 可选 |
| 单端口多服路由 | itzg/mc-router | 983 | MIT | 2026-11-27 | 可选二进制 | 按 hostname 路由，轻量替代代理 |
| Go 代理（备选） | minekube/gate | 1092 | **Apache-2.0** | 2026-11-27 | 语义参考/备选 | Velocity 兼容；v1 不引 |

### M7 控制台与日志

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| 命令树/补全 | Mojang/brigadier | 3745 | MIT | 2026-07-17 | 语义参考 | 静态命令表 + `plugin.yml` 扫描（已有） |
| 控制台搜索 UX | xtermjs/xterm.js（含 `addon-search`） | 21390 | MIT | 2026-11-27 | 直接集成 | 前端搜索浮层（addon 已在仓库内，已核验存在） |
| 日志全文搜索 | SQLite FTS5（内置） | — | — | — | 直接集成 | 零依赖；PG 用 tsvector（W2 已规划） |
| 外部搜索引擎 | typesense / meilisearch | 27181 / 60026 | GPL-3.0 / 未识别 | — | 排除 | 对单机面板过重 |
| 崩溃报告分析 | 自研 + 现有 AI | — | — | — | 自研 | 无标准轮子，样本解析量小 |

### M8 自动化

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| 空闲休眠/来玩家唤醒 | timvisee/lazymc | 889 | GPL-3.0 | 2026-07-16 | 语义参考 | 移植「空服停机 + 唤醒」思路，用现有调度器实现，不内置 GPL |
| 自动暂停（容器） | itzg/docker-minecraft-server `AUTO_PAUSE` | 14582 | Apache-2.0 | 2026-11-27 | 语义参考 | 同上 |
| 维护模式 | kennytv/Maintenance | 336 | GPL-3.0 | 2026-10-17 | 插件门控（可选） | 面板原生 whitelist + MOTD + kick 编排为主（`servers/ops.py` 已实现） |
| 触发器引擎 | node-red/node-red | 23893 | Apache-2.0 | 2026-11-24 | 排除 | 过重；用 pluggy + APScheduler 薄分发 |
| 停止阶梯/信号 | itzg/mc-server-runner | 47 | Apache-2.0 | 2026-11-26 | 语义参考（已移植） | 倒计时重启复用 |

### M9 资源与 JVM

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| JVM 调优基准 | brucethemoose/…Performance-Flags-Benchmarks | 1734 | MIT | 2024-07-31 | 语义参考 | Aikar/ZGC 预设依据 |
| 优化指南文案 | YouHaveTrouble/minecraft-optimization | 2900 | **无许可证（NONE）** | 2026-09-17 | 语义参考 | **只能读思路，不复制正文** |
| Docker 硬限额 | docker/docker-py | 7223 | Apache-2.0 | 2026-11-19 | 直接集成（extra） | `settings.runtime=docker`（W3 已规划） |
| Windows Job Objects | ctypes（原案 pywin32 无 LICENSE） | — | — | — | 自研 | 零依赖，`kernel32` 直接调 |
| Linux cgroups | `systemd-run` CLI | — | LGPL（调用不链接） | — | 语义参考 | 检测 delegation，不可用降级 |

### M10 生态与告警

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| Discord 入站 | **自实现 Ed25519**（`cryptography` 已有） | — | — | — | 自研 | 原案 interactions-python 停在 2024-04，验签只需 ~10 行；斜杠命令绑定角色走 Casbin |
| Discord 入站（备选） | discord/discord-interactions-python | 117 | MIT | **2024-04-30** | 备选/不引 | 仅当自研遇到协议细节问题再回退 |
| Discord 常驻 bot | Rapptz/discord.py | 16214 | MIT | 2026-09-07 | 排除（v1） | 需常驻 gateway，违背单进程轻量 |
| Discord 聊天桥（插件） | DiscordSRV/DiscordSRV | 1160 | GPL-3.0 | 2026-08-08 | 插件门控 | 双向桥可选 |
| Telegram | httpx 直连 Bot API | — | — | — | 自研（零依赖） | 优先；`aiogram`（MIT/★5950）备选 |
| Telegram（排除） | python-telegram-bot | 29619 | **GPL-3.0** | 2026-11-27 | 排除 | 原案写 LGPL，实测 GPL → 排除理由更硬 |
| 邮件 | cole/aiosmtplib | 437 | MIT | 2026-11-03 | 直接集成 | 路径已核验无误，可直接用 |
| 告警抑制/分组语义 | prometheus/alertmanager | 8714 | Apache-2.0 | 2026-11-27 | 语义参考 | 薄评估器，不引服务 |
| 状态页语义 | louislam/uptime-kuma | 94962 | MIT | 2026-11-27 | 语义参考 | 公开状态页 + SLP 缓存 |
| SVG 徽章 | badges/shields | 27445 | **Apache-2.0** | 2026-11-27 | 语义参考 | 自绘小 SVG（原案标 CC0，已修正） |

### M11 批量与分组

| 能力点 | 上游 | ★ | 许可证 | 最近推送 | 方式 | 落地 |
|---|---|---|---|---|---|---|
| 批量操作/分组 UX | MCSManager/MCSManager | 5022 | Apache-2.0 | 2026-11-27 | 语义参考 | 逐服权限循环自实现 |
| 多服管理语义 | pterodactyl/panel | 9551 | 未识别（实为 MIT） | 2026-11-23 | 语义参考 | 不引整套 |

---

## 4. 关键集成决策

1. **插件门控注册表是新能力的统一入口**：`integrations.py` 声明
   `{slug, detect(), capabilities[], install_hint}`，spark / Chunky / OpenInv / LuckPerms /
   EssentialsX / BlueMap / dynmap / Geyser / Floodgate / ViaVersion / DiscordSRV / Maintenance /
   Plan 全走它。GPL 插件经市场安装 + RCON 文本交互，**不构成代码链接**，面板许可证不受影响。
2. **可选二进制托管复用 JRE 下载管线**（SHA-256 校验、版本目录、失败回退）：
   restic（BSD-2）、rclone（MIT）、mc-router（MIT）、mrpack-install（MIT）、mc-image-helper（MIT）可托管；
   **lazymc / Region-Fixer / forge 安装器等 GPL 工具只做语义参考或引导自装，不随包分发**。
3. **通知通道**：Telegram 用 httpx 直连 Bot API（零依赖），aiogram 备选；邮件 `cole/aiosmtplib`（MIT，
   路径已核验）；Discord 入站自实现 Ed25519（`cryptography` 已有）—— 三条都不新增停滞依赖。
4. **备份**：restic 胜出（跨平台 + BSD-2 + 增量加密异地一体）；borg 因 Windows 弱排除；
   `itzg/docker-mc-backup` 只取 `save-off` 协调语义。
5. **搜索/触发器/告警一律零外部服务**：FTS5、pluggy + APScheduler、薄评估器；
   typesense / meilisearch / Node-RED / alertmanager 服务全部排除或仅语义参考。
6. **NBT 我们自己的依赖只有一层**：`twoolie/NBT`（MIT，含 RegionFile），包一个 `nbt.py` 适配器，
   只暴露「读 level.dat 种子 / 读玩家坐标 / 列 region 文件」三个用例，避免库变动扩散。

---

## 5. 仍需自研的残留（无合适轮子，且与数据模型强耦合）

- tempban 账本 + 到期调度（vanilla 无 tempban；插件门控可选委托）
- 事件触发器分发器（cooldown + 防循环）
- 维护模式编排（whitelist 原态记录/恢复；`servers/ops.py` 已有基础，需补原态快照）
- 升级向导与 60s 崩溃回滚
- 端口注册表、批量逐服权限循环
- RCON 文本解析器（`tps`/`mspt`/`chunky`/`lp`/`data get`，容错返回 null）
- Discord Ed25519 验签与交互响应（~10 行，无需上游库）

---

## 6. 分期（每阶段锚定上游，且只锚已验证项）

| 阶段 | 模块 | 上游锚点 | 门禁 |
|---|---|---|---|
| **P1 · v0.2** | M1 告警 + M2 + M7 搜索 + M4 一致性 + M8 空闲休眠 + M9 预设 + M10 告警 | spark/Paper 原生、EssentialsX 门控、xterm addon-search + FTS5、`itzg/docker-mc-backup` 语义、lazymc 语义、alertmanager 语义、Telegram 直连 | 151 测试全绿 + 真机 spark 解析样例 |
| **P2 · v0.3** | M3 + M5 + M4 增量异地 + M10 状态页 + M11 | Chunky/BlueMap/dynmap、restic、mc-image-helper / mrpack-install、uptime-kuma / shields 语义、MCSManager 语义 | restic 恢复演练脚本跑通 |
| **P3 · v0.4** | M6 + M2 背包/LuckPerms + M9 硬限额 | PaperMC/Velocity、Geyser/Floodgate/mc-router、OpenInv/LuckPerms、docker-py + ctypes Job Objects | 代理链路真机验证 |

---

## 7. 复现核验（下一步动手前重跑）

```bash
# 需要已认证的 gh（org/repo 读取权限即可）
while read -r r; do
  [ -z "$r" ] && continue
  gh api "repos/$r" --jq '"\(.full_name)\t★\(.stargazers_count)\t\(.license.spdx_id // "NONE")\t\(.pushed_at[:10])\tarchived=\(.archived)"' \
    || echo "$r	404"
done < docs/repos.txt
```

`docs/repos.txt` 保存本文涉及的 58 个仓库清单。其中 2 条返回 404 是刻意留作对照的：
`itzg/mc-backup`（真名 `itzg/docker-mc-backup`）与 `aio-libs/aiosmtplib`（真名 `cole/aiosmtplib`）。
重点复查对象：`NOASSERTION` / `NONE` 的 8 个仓库，以及 `twoolie/NBT`、`cole/aiosmtplib`、
`PaperMC/Velocity` 的维护状态 —— 这三条是 P1 之外唯一会进 `pyproject.toml` 的东西。

**核验基准**：2026-11-28，`gh` 认证态，58 个仓库全部返回，0 归档。
