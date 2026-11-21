"""待人工确认的工具调用。

上一版把待确认项只放在前端状态里，服务端对 /approve 传来的 id 照单全收：
过期页面、重复点击都能触发一次“确认”。这里按会话记住真正暂停的调用与
截止时间，确认时逐项核对，用掉即失效（对齐 LangGraph interrupt/resume 的
一次性语义，但不引入 checkpointer）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

TTL_SECONDS = 30 * 60


@dataclass
class Pending:
    items: list[dict[str, Any]]
    expires: float


class Approvals:
    def __init__(self) -> None:
        self._data: dict[str, Pending] = {}

    def remember(self, key: str, items: list[dict[str, Any]]) -> None:
        self._data[key] = Pending(items=items, expires=time.time() + TTL_SECONDS)

    def peek(self, key: str) -> list[dict[str, Any]] | None:
        item = self._data.get(key)
        if item is None:
            return None
        if time.time() > item.expires:
            self._data.pop(key, None)
            return None
        return item.items

    def validate(self, key: str, approvals: dict[str, bool]) -> str | None:
        """返回错误说明；None 表示这批审批合法。"""
        items = self.peek(key)
        if items is None:
            return "没有待确认的操作，或确认已过期，请重新发起请求"
        expected = {str(item["id"]) for item in items}
        given = set(approvals)
        if given != expected:
            missing = sorted(expected - given)
            extra = sorted(given - expected)
            parts = []
            if missing:
                parts.append("缺少：" + ", ".join(missing))
            if extra:
                parts.append("多余：" + ", ".join(extra))
            return "确认项与实际暂停的工具调用不一致（" + "；".join(parts) + "）"
        return None

    def consume(self, key: str) -> None:
        self._data.pop(key, None)

    def drop(self, key: str) -> None:
        self._data.pop(key, None)


approvals = Approvals()
