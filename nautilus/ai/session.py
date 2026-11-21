"""对话历史存放。

刻意放在**进程内**而不是数据库：这里的消息是 pydantic-ai 的对象图（含工具
调用与返回值），序列化落库反而引入版本兼容负担；而且面板本来就是单 worker
（运行态在进程里，多 worker 会各看各的）。

代价是重启后历史丢失 —— 对“让 AI 帮我操作一下”这种短对话可以接受，
真要长期记忆，那属于另一种产品形态。
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from typing import Any

# 注意：这里刻意不在运行时导入 pydantic-ai。消息对象只被当作不透明容器传递，
# 面板启动阶段不该为一个可能用不到的依赖多付 1.5 秒。

#: 同时保留多少条会话（超了淘汰最久没动的）。
MAX_CONVERSATIONS = 50
#: 单条会话保留多少条消息；超了从头丢整轮。
MAX_MESSAGES = 80
#: 闲置多久后清理（秒）。
TTL_SECONDS = 6 * 3600


def _is_user_turn_start(message: Any) -> bool:
    """判断某条消息能否当作“安全裁剪点”。

    只有以用户提问开头的请求才能作为起点：从一条“工具返回”开始的历史，
    会让下一次 run 报 “unprocessed tool calls”。

    这里按类名判断而不是 isinstance，是为了让本模块在导入时不必拉起
    pydantic-ai（面板启动阶段不该为一个可能用不到的依赖付 1.5 秒）。
    """
    if type(message).__name__ != "ModelRequest":
        return False
    return any(getattr(part, "part_kind", "") == "user-prompt" for part in getattr(message, "parts", ()))


def _trim(messages: list[Any]) -> list[Any]:
    if len(messages) <= MAX_MESSAGES:
        return messages
    cut = len(messages) - MAX_MESSAGES
    while cut < len(messages) and not _is_user_turn_start(messages[cut]):
        cut += 1
    return messages[cut:] or messages[-1:]


class Conversations:
    def __init__(self) -> None:
        self._data: OrderedDict[str, tuple[list[Any], float]] = OrderedDict()

    def new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def get(self, conversation_id: str) -> list[Any] | None:
        item = self._data.get(conversation_id)
        if item is None:
            return None
        messages, _ = item
        self._data.move_to_end(conversation_id)
        return messages

    def put(self, conversation_id: str, messages: list[Any]) -> None:
        self._data[conversation_id] = (_trim(messages), time.time())
        self._data.move_to_end(conversation_id)
        while len(self._data) > MAX_CONVERSATIONS:
            self._data.popitem(last=False)

    def drop(self, conversation_id: str) -> None:
        self._data.pop(conversation_id, None)

    def summary(self) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for cid, (messages, updated) in self._data.items():
            preview = ""
            for message in reversed(messages):
                for part in getattr(message, "parts", []):
                    content = getattr(part, "content", None)
                    if isinstance(content, str) and content.strip():
                        preview = content.strip()[:80]
                        break
                if preview:
                    break
            out.append({"id": cid, "messages": len(messages), "preview": preview, "idle": int(now - updated)})
        return out

    def prune(self) -> int:
        now = time.time()
        stale = [cid for cid, (_, updated) in self._data.items() if now - updated > TTL_SECONDS]
        for cid in stale:
            self._data.pop(cid, None)
        return len(stale)


conversations = Conversations()
