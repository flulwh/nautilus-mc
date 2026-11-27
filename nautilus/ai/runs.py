"""运行中对话的注册表：服务端取消与重复提交保护。

前端关掉 fetch 只是断开展示，工具还会继续跑；真正要“停下”，必须把信号
送进 agent run 本身。LangGraph 的图执行在我们自己的流式循环里，取消就是
一个 asyncio.Event：循环每个事件检查一次，并取消进行中的模型请求任务。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


class CancellationToken:
    """Minimal cooperative cancellation token (LangGraph has no built-in one)."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class RunHandle:
    token: CancellationToken
    started: float


class Runs:
    def __init__(self) -> None:
        self._data: dict[str, RunHandle] = {}

    def active(self, key: str) -> bool:
        return key in self._data

    def register(self, key: str) -> CancellationToken:
        """开一轮新 run。同一个会话已有 run 时先取消它，避免两份历史互相覆盖。"""
        self.prune()
        previous = self._data.get(key)
        if previous is not None:
            previous.token.cancel()
        token = CancellationToken()
        self._data[key] = RunHandle(token=token, started=time.time())
        return token

    def cancel(self, key: str) -> bool:
        handle = self._data.get(key)
        if handle is None:
            return False
        handle.token.cancel()
        return True

    def finish(self, key: str, token: CancellationToken) -> None:
        handle = self._data.get(key)
        if handle is not None and handle.token is token:
            self._data.pop(key, None)

    def drop(self, key: str) -> None:
        handle = self._data.pop(key, None)
        if handle is not None:
            handle.token.cancel()

    def prune(self, ttl_seconds: float = 30 * 60) -> int:
        """流从未被消费时 finally 不会跑；定期清掉这种僵尸登记表项。"""
        now = time.time()
        stale = [key for key, item in self._data.items() if now - item.started > ttl_seconds]
        for key in stale:
            self._data.pop(key, None)
        return len(stale)


runs = Runs()
