"""对话历史存放。

刻意放在**进程内**而不是数据库：这里的消息是 pydantic-ai 的对象图（含工具
调用与返回值），序列化落库反而引入版本兼容负担；而且面板本来就是单 worker
（运行态在进程里，多 worker 会各看各的）。

代价是重启后历史丢失 —— 对“让 AI 帮我操作一下”这种短对话可以接受，
真要长期记忆，那属于另一种产品形态。

会话元数据（标题、压缩摘要）也放这里，和消息同生命周期。
"""

from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# 注意：这里刻意不在运行时导入 pydantic-ai。消息对象只被当作不透明容器传递，
# 面板启动阶段不该为一个可能用不到的依赖多付 1.5 秒。

#: 同时保留多少条会话（超了淘汰最久没动的）。
MAX_CONVERSATIONS = 50
#: 单条会话保留多少条消息；超了从头丢整轮。
MAX_MESSAGES = 80
#: 闲置多久后清理（秒）。
TTL_SECONDS = 6 * 3600
#: 自动生成的标题最多多少字符。
TITLE_CHARS = 28


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


def _text_of(part: Any) -> str:
    content = getattr(part, "content", "")
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _first_question(messages: list[Any]) -> str:
    for message in messages:
        for part in getattr(message, "parts", ()):
            if getattr(part, "part_kind", "") == "user-prompt":
                text = _text_of(part).strip().replace("\n", " ")
                return text[:TITLE_CHARS]
    return ""


@dataclass
class Conversation:
    messages: list[Any] = field(default_factory=list)
    updated: float = 0.0
    title: str = ""
    summary: str = ""
    compactions: int = 0


class Conversations:
    def __init__(self) -> None:
        self._data: OrderedDict[str, Conversation] = OrderedDict()

    def new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def has(self, conversation_id: str) -> bool:
        return conversation_id in self._data

    def get(self, conversation_id: str) -> list[Any] | None:
        item = self._data.get(conversation_id)
        if item is None:
            return None
        self._data.move_to_end(conversation_id)
        item.updated = time.time()
        return item.messages

    def put(
        self,
        conversation_id: str,
        messages: list[Any],
        *,
        title: str | None = None,
        summary: str | None = None,
        compactions: int | None = None,
    ) -> None:
        old = self._data.get(conversation_id)
        trimmed = _trim(messages)
        if title is None:
            title = old.title if old else ""
        if not title:
            title = _first_question(trimmed) or "新对话"
        self._data[conversation_id] = Conversation(
            messages=trimmed,
            updated=time.time(),
            title=title,
            summary=summary if summary is not None else (old.summary if old else ""),
            compactions=compactions if compactions is not None else (old.compactions if old else 0),
        )
        self._data.move_to_end(conversation_id)
        while len(self._data) > MAX_CONVERSATIONS:
            self._data.popitem(last=False)

    def set_title(self, conversation_id: str, title: str) -> bool:
        item = self._data.get(conversation_id)
        if item is None:
            return False
        item.title = title.strip()[:TITLE_CHARS] or "新对话"
        item.updated = time.time()
        self._data.move_to_end(conversation_id)
        return True

    def meta(self, conversation_id: str) -> dict[str, Any] | None:
        item = self._data.get(conversation_id)
        if item is None:
            return None
        return {
            "id": conversation_id,
            "title": item.title,
            "summary": item.summary[:200],
            "compactions": item.compactions,
            "messages": len(item.messages),
        }

    def drop(self, conversation_id: str) -> None:
        self._data.pop(conversation_id, None)

    def summary(self) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for cid, item in self._data.items():
            preview = ""
            for message in reversed(item.messages):
                for part in getattr(message, "parts", []):
                    content = getattr(part, "content", None)
                    if isinstance(content, str) and content.strip():
                        preview = content.strip()[:80]
                        break
                if preview:
                    break
            out.append(
                {
                    "id": cid,
                    "title": item.title,
                    "preview": preview,
                    "messages": len(item.messages),
                    "idle": int(now - item.updated),
                    "compactions": item.compactions,
                }
            )
        return out

    def prune(self) -> int:
        now = time.time()
        stale = [cid for cid, item in self._data.items() if now - item.updated > TTL_SECONDS]
        for cid in stale:
            self._data.pop(cid, None)
        return len(stale)


conversations = Conversations()


def serialize(messages: list[Any]) -> list[dict[str, Any]]:
    """把 pydantic-ai 消息还原成前端可回放的事件序列。

    按类名而不是 isinstance 判断，和本模块“不在导入期加载 pydantic-ai”的约定一致。
    """
    events: list[dict[str, Any]] = []
    for message in messages:
        kind = type(message).__name__
        for part in getattr(message, "parts", ()):
            part_kind = getattr(part, "part_kind", "")
            if kind == "ModelRequest" and part_kind == "user-prompt":
                text = _text_of(part)
                # 压缩摘要也是 user-prompt，但不能伪装成用户真的说过的话。
                if text.startswith("[历史摘要"):
                    events.append({"type": "note", "text": text.split("]", 1)[-1].strip()})
                else:
                    events.append({"type": "user", "text": text})
            elif kind == "ModelRequest" and part_kind == "tool-return":
                content = _text_of(part)
                events.append(
                    {
                        "type": "tool_result",
                        "id": getattr(part, "tool_call_id", ""),
                        "name": getattr(part, "tool_name", ""),
                        "preview": content[:600],
                        "failed": '"ok": false' in content or "'ok': False" in content,
                    }
                )
            elif kind == "ModelResponse" and part_kind == "text":
                text = _text_of(part)
                if text.strip():
                    events.append({"type": "text", "text": text})
            elif kind == "ModelResponse" and part_kind == "tool-call":
                args = getattr(part, "args", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        pass
                events.append(
                    {
                        "type": "tool_call",
                        "id": getattr(part, "tool_call_id", ""),
                        "name": getattr(part, "tool_name", ""),
                        "args": args,
                    }
                )
    return events
