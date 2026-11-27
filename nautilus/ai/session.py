"""对话历史存放。

刻意放在**进程内**而不是数据库：面板本来就是单 worker（运行态在进程里，
多 worker 会各看各的），重启丢历史对“让 AI 帮我操作一下”这种短对话可以接受。

消息是**纯 dict**（OpenAI 风格：user / assistant(+tool_calls, thinking) / tool），
不与任何 agent 框架的对象耦合 —— LangGraph 循环按它读写，前端回放按它序列化，
将来落库持久化（升级方案 W2）也直接是 JSON。

会话元数据（标题、压缩摘要）也放这里，和消息同生命周期。
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: 同时保留多少条会话（超了淘汰最久没动的）。
MAX_CONVERSATIONS = 50
#: 单条会话保留多少条消息；超了从头丢整轮。
MAX_MESSAGES = 80
#: 闲置多久后清理（秒）。
TTL_SECONDS = 6 * 3600
#: 自动生成的标题最多多少字符。
TITLE_CHARS = 28

MARKER = "[历史摘要]"


def _is_user_turn_start(message: dict[str, Any]) -> bool:
    """只有以用户提问开头的历史才能作为裁剪/压缩切点。

    从一条“工具返回”开始的历史会让下一轮模型调用报未处理的 tool_calls。
    """
    return message.get("role") == "user"


def _trim(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= MAX_MESSAGES:
        return messages
    cut = len(messages) - MAX_MESSAGES
    while cut < len(messages) and not _is_user_turn_start(messages[cut]):
        cut += 1
    return messages[cut:] or messages[-1:]


def _first_question(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if _is_user_turn_start(message):
            text = str(message.get("content", "")).strip().replace("\n", " ")
            if text.startswith(MARKER):
                continue  # 压缩摘要伪装成 user，不能当标题
            return text[:TITLE_CHARS]
    return ""


@dataclass
class Conversation:
    messages: list[dict[str, Any]] = field(default_factory=list)
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

    def get(self, conversation_id: str) -> list[dict[str, Any]] | None:
        item = self._data.get(conversation_id)
        if item is None:
            return None
        self._data.move_to_end(conversation_id)
        item.updated = time.time()
        return item.messages

    def put(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
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
                if message.get("role") in {"user", "assistant"}:
                    content = str(message.get("content", "")).strip()
                    if content and not content.startswith(MARKER):
                        preview = content[:80]
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


def serialize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 dict 历史还原成前端可回放的事件序列。"""
    events: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            text = str(message.get("content", ""))
            if text.startswith(MARKER):
                events.append({"type": "note", "text": text[len(MARKER) :].strip()})
            else:
                events.append({"type": "user", "text": text})
        elif role == "assistant":
            thinking = str(message.get("thinking", ""))
            if thinking.strip():
                events.append({"type": "thinking", "text": thinking[:2000]})
            text = str(message.get("content", ""))
            if text.strip():
                events.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                events.append(
                    {
                        "type": "tool_call",
                        "id": call.get("id", ""),
                        "name": call.get("name", ""),
                        "args": call.get("args", {}),
                    }
                )
        elif role == "tool":
            content = str(message.get("content", ""))
            events.append(
                {
                    "type": "tool_result",
                    "id": message.get("tool_call_id", ""),
                    "name": message.get("name", ""),
                    "preview": content[:600],
                    "failed": '"ok": false' in content or "'ok': False" in content,
                }
            )
    return events
