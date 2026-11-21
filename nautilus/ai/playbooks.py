"""按需加载的运维手册。

参考 Claude Agent SDK 的 Skills 做法：常驻上下文里只放一行摘要，真正的内容
在模型判断相关时才通过工具取回来。33 个工具的细节不塞进 system prompt，
既省 token，也减少模型把无关规则套错场景的概率。
"""

from __future__ import annotations

from typing import Any

PLAYBOOKS: dict[str, dict[str, str]] = {
    "server_not_starting": {
        "title": "服务器启动失败排查",
        "summary": "服务器起不来、卡在 starting、反复退出时按这个顺序查。",
        "body": """1. server_status 看 state、pid、jar_ready 和最后几行日志。
2. jar_ready=false：说明 jar 没下载好。用 create_server 或面板的重新安装功能重建，不要手写 server.jar 路径。
3. 日志含 Java 版本错误：用 panel_info 看 java 列表，确认装了匹配 MC 版本的主版本 JRE。
4. 日志含 EULA / You need to agree：用 write_file 写 eula.txt 的 eula=true，然后 server_start。
5. 日志含 port in use：server_status 看端口，换端口前先确认没有别的进程占用。
6. 日志含插件报错：list_projects 找到对应插件，toggle_project 禁用后再启动，再去查插件版本。
7. 仍卡住：read_logs 加 grep=ERROR 看完整报错；不要把整份日志复述给用户，给出关键行和下一步。""",
    },
    "install_plugin": {
        "title": "安装插件或数据包",
        "summary": "装 Modrinth 插件、数据包，处理依赖和重启。",
        "body": """1. list_servers 拿 server_id；server_status 确认服务器类型和 MC 版本。
2. search_projects 用 slug 定位，注意结果里的 downloads 和描述，优先选与类型匹配的项目。
3. install_project 安装；如果返回依赖缺失，按返回的 slug 依次安装依赖。
4. list_projects 确认文件出现且 enabled=true。
5. 提醒用户重启生效：改完配置/装完插件要 server_restart，运行中不会热加载。
6. 装完启动失败，用 server_not_starting 手册排查。""",
    },
    "backup_restore": {
        "title": "备份与恢复",
        "summary": "备份世界/整服，以及恢复前的停机与回滚确认。",
        "body": """1. 备份前先用 server_status 看有没有玩家在线；有人在线时先通知或 server_stop，避免半写状态。
2. create_backup scope=world 只备份世界，scope=all 含插件和配置。长期留档用 all。
3. list_backups 核对备份名和创建时间，再告诉用户备份已完成。
4. 恢复是危险操作：restore_backup 前必须停机，且向用户说明“当前世界进度会被覆盖”。
5. 用户确认后才调用 restore_backup；原文件会被移到 pre-restore 目录，但不是面板内可一键回滚的快照。
6. 恢复完 server_start，并用 read_logs 确认世界正常加载。""",
    },
    "player_management": {
        "title": "玩家权限与名单",
        "summary": "加 OP、白名单、封禁，排查玩家进不去。",
        "body": """1. 不要直接改 ops.json / whitelist.json；用 player_action，面板会处理格式和审计。
2. op / deop / whitelist_add / whitelist_remove / ban / unban 是支持的 action。
3. 玩家 ID 必须是真实游戏 ID；name 参数不要带 UUID，除非你确认那就是玩家名。
4. 玩家进不去：先看 list_players 的白名单和封禁名单，再 world_settings 看 online-mode 和白名单开关。
5. 白名单开着且玩家不在名单里 -> whitelist_add；正版验证开着但玩家是离线账号 -> 说明需要关 online-mode 或改用正版账号。
6. 改完 server.properties 要重启才生效；玩家名单类操作通常即时生效。""",
    },
    "world_management": {
        "title": "世界新建与切换",
        "summary": "列世界、改玩法设置、新建和切换当前世界。",
        "body": """1. list_worlds 看有哪些世界以及当前生效的是哪个；world_settings 看难度、模式、种子、白名单。
2. update_world_settings 只传要改的键，服务器运行中会拒绝；先 server_stop 再改。
3. create_world 只是写入配置，世界在下次启动时生成；name 用合法目录名，不要带路径分隔符。
4. activate_world 改 level-name 指向已有世界，同样要停机。
5. delete_world 是危险操作，会移到回收站目录但面板内无法恢复；执行前必须确认。
6. 做完用 server_start 启动，并用 read_logs grep=ERROR 确认世界加载成功。""",
    },
    "performance_check": {
        "title": "卡顿与资源排查",
        "summary": "看 CPU/内存、在线人数、日志异常，判断该调什么。",
        "body": """1. server_status 拿 cpu_percent、rss_mb、players 和 uptime；单次采样不代表趋势，必要时隔一会儿再看一次。
2. list_players 看在线人数；list_projects 看插件数量，插件多且内存低时优先考虑插件问题。
3. read_logs grep=WARN 和 grep=ERROR，重点看 Can't keep up、tick、timeout。
4. view-distance 对内存和 CPU 影响大：world_settings 看当前值，停机后用 update_world_settings 调小。
5. 内存长期打满且 GC 日志频繁：建议增大 server.memory_mb 而不是盲目重启。
6. 只给结论和依据，不要因为一次采样就建议强杀服务器。""",
    },
}


def catalogue() -> list[dict[str, str]]:
    return [
        {"name": name, "title": item["title"], "summary": item["summary"]}
        for name, item in PLAYBOOKS.items()
    ]


def get(name: str) -> dict[str, Any] | None:
    item = PLAYBOOKS.get((name or "").strip())
    if item is None:
        return None
    return {"name": name, **item}
