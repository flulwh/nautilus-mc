"""会话级任务清单。

对应 Claude Code 的 TodoWrite：让模型把“要做几件事、做到哪一步”显式写出来，
前端不用从模型自然语言里猜进度。状态放在进程内，和对话历史同生命周期；
面板本来就是单 worker，进程管理也在内存里，这里没必要再引一套持久化。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["pending", "doing", "done", "blocked"]


class StepIn(BaseModel):
    """模型提交的单步任务；用模型约束而不是让前端解析自由文本。"""

    text: str = Field(min_length=1, max_length=160)
    status: Status = "pending"


@dataclass
class Plan:
    title: str
    steps: list[StepIn]
    updated: float

    def public(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "steps": [item.model_dump() for item in self.steps],
        }


#: 长时间没动的清单直接丢掉；和对话 TTL 保持一致。
TTL_SECONDS = 6 * 3600
MAX_STEPS = 20


class Plans:
    def __init__(self) -> None:
        self._data: dict[str, Plan] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        item = self._data.get(key)
        return item.public() if item else None

    def set(self, key: str, title: str, steps: list[StepIn]) -> dict[str, Any]:
        plan = Plan(
            title=(title or "任务").strip()[:120],
            steps=steps[:MAX_STEPS],
            updated=time.time(),
        )
        self._data[key] = plan
        return plan.public()

    def drop(self, key: str) -> None:
        self._data.pop(key, None)

    def prune(self) -> int:
        now = time.time()
        stale = [key for key, item in self._data.items() if now - item.updated > TTL_SECONDS]
        for key in stale:
            self._data.pop(key, None)
        return len(stale)


plans = Plans()
