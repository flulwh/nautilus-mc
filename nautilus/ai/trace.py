"""Agent 逐步运行轨迹（step-by-step trace）。

为什么单独存一份，而不是直接看 ``AuditLog`` 或会话消息：

* ``AuditLog`` 只记写操作的参数和成败，看不到模型推理、只读工具、计划与审批，
  也记不了每步的真实耗时；
* 会话消息是 pydantic-ai 的对象图，按模型版本变化，直接拿来给 UI 渲染耦合太重；
* 运维排障时最常问的是「它到底干了什么、哪一步慢、哪个工具失败了」，
  这需要一个稳定、扁平、带序号和耗时的步骤序列。

设计沿用 Orchestrator-AI-Agent-Platform 的 run tracker 思路（每个 thought /
tool call / result 记一步），但存进程内而非数据库 —— 与会话、计划、审批同生命周期，
重启即清，符合面板单 worker 的定位。一次 run 一个 ``RunTrace``；审批续跑复用同一个
run_id，所以跨「提问 → 等确认 → 续跑」的步骤仍能连成一条完整时间线。
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: 同时保留多少条 run（全局 LRU，超了淘汰最久没动的）。
MAX_RUNS = 100
#: 单条 run 最多多少步，防止跑飞的模型把内存写爆。
MAX_STEPS = 200
#: 闲置多久后清理（秒），与会话同量级。
TTL_SECONDS = 6 * 3600
#: 结果预览落进轨迹的上限（字符）。
RESULT_CHARS = 2000


@dataclass
class Step:
    """轨迹里的一步。"""

    seq: int
    kind: str  # model | tool | plan | approval | note
    name: str = ""
    status: str = "running"  # running | done | failed | waiting | stopped
    started_at: float = field(default_factory=time.time)
    duration_ms: int | None = None
    args: Any = None
    result: str = ""
    detail: str = ""
    #: 单调时钟起点，只用于算耗时，不对外导出。
    _perf: float = field(default_factory=time.perf_counter, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "at": int(self.started_at * 1000),
            "duration_ms": self.duration_ms,
            "args": self.args,
            "result": self.result[:RESULT_CHARS],
            "detail": self.detail[:RESULT_CHARS],
        }


@dataclass
class RunTrace:
    """一次 run 的完整轨迹。"""

    run_id: str
    conversation_id: str  # 已带用户前缀的隔离 key
    model: str
    prompt: str
    started_at: float = field(default_factory=time.time)
    status: str = "running"  # running | done | waiting_approval | cancelled | error
    finished_at: float | None = None
    usage: dict[str, Any] | None = None
    steps: list[Step] = field(default_factory=list)
    _next_seq: int = field(default=1, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "model": self.model,
            "prompt": self.prompt,
            "started_at": int(self.started_at * 1000),
            "finished_at": int(self.finished_at * 1000) if self.finished_at else None,
            "status": self.status,
            "usage": self.usage,
            "steps": [step.public() for step in self.steps],
        }

    def summary(self) -> dict[str, Any]:
        done = [s for s in self.steps if s.duration_ms is not None]
        return {
            "run_id": self.run_id,
            "started_at": int(self.started_at * 1000),
            "finished_at": int(self.finished_at * 1000) if self.finished_at else None,
            "status": self.status,
            "model": self.model,
            "prompt": self.prompt,
            "steps": len(self.steps),
            "tools": sum(1 for s in self.steps if s.kind == "tool"),
            "failed": sum(1 for s in self.steps if s.status == "failed"),
            "duration_ms": sum(s.duration_ms or 0 for s in done),
        }


class TraceStore:
    """进程内的 run 轨迹仓库。所有方法只碰内存，无 I/O。"""

    def __init__(self) -> None:
        self._data: OrderedDict[str, RunTrace] = OrderedDict()

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def begin(
        self,
        run_id: str,
        conversation_id: str,
        model: str,
        prompt: str = "",
    ) -> RunTrace:
        """开一条轨迹。审批续跑传入已存在的 run_id：复用步骤、接着往后记。"""
        existing = self._data.get(run_id)
        if existing is not None:
            existing.status = "running"
            existing.finished_at = None
            self._data.move_to_end(run_id)
            return existing
        trace = RunTrace(
            run_id=run_id,
            conversation_id=conversation_id,
            model=model,
            prompt=prompt[:80],
        )
        self._data[run_id] = trace
        self._evict()
        return trace

    def get(self, run_id: str) -> RunTrace | None:
        trace = self._data.get(run_id)
        if trace is not None:
            self._data.move_to_end(run_id)
        return trace

    def add_step(
        self,
        run_id: str,
        kind: str,
        name: str = "",
        *,
        status: str = "running",
        args: Any = None,
        detail: str = "",
        result: str = "",
    ) -> Step | None:
        trace = self._data.get(run_id)
        if trace is None or len(trace.steps) >= MAX_STEPS:
            return None
        step = Step(
            seq=trace._next_seq,
            kind=kind,
            name=name,
            status=status,
            args=args,
            detail=detail,
            result=result[:RESULT_CHARS],
        )
        trace._next_seq += 1
        trace.steps.append(step)
        return step

    def finish_step(
        self,
        run_id: str,
        seq: int | None,
        *,
        status: str = "done",
        result: str = "",
        detail: str | None = None,
    ) -> int | None:
        """收尾一步并返回服务端实测耗时（毫秒）。seq 为空表示无从对应。"""
        trace = self._data.get(run_id)
        if trace is None or seq is None:
            return None
        for step in trace.steps:
            if step.seq != seq:
                continue
            step.status = status
            if result:
                step.result = result[:RESULT_CHARS]
            if detail is not None:
                step.detail = detail[:RESULT_CHARS]
            step.duration_ms = max(0, int((time.perf_counter() - step._perf) * 1000))
            return step.duration_ms
        return None

    def running_step(self, run_id: str, kind: str | None = None) -> Step | None:
        trace = self._data.get(run_id)
        if trace is None:
            return None
        for step in reversed(trace.steps):
            if step.status == "running" and (kind is None or step.kind == kind):
                return step
        return None

    def finish(
        self,
        run_id: str,
        status: str = "done",
        usage: dict[str, Any] | None = None,
        *,
        step_status: str | None = None,
    ) -> None:
        """收尾整条 run，并把还挂着的步骤一并结束，不留“永远执行中”。"""
        trace = self._data.get(run_id)
        if trace is None:
            return
        trace.status = status
        trace.finished_at = time.time()
        if usage is not None:
            trace.usage = usage
        if step_status is None:
            step_status = "done" if status == "done" else ("stopped" if status == "cancelled" else "failed")
        for step in trace.steps:
            if step.status == "running":
                self.finish_step(run_id, step.seq, status=step_status)

    def latest(self, conversation_id: str, status: str | None = None) -> RunTrace | None:
        for trace in reversed(self._data.values()):
            if trace.conversation_id == conversation_id and (status is None or trace.status == status):
                return trace
        return None

    def list_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        return [t.summary() for t in reversed(self._data.values()) if t.conversation_id == conversation_id]

    def export(self, run_id: str) -> dict[str, Any] | None:
        trace = self.get(run_id)
        return trace.public() if trace else None

    def drop(self, run_id: str) -> None:
        self._data.pop(run_id, None)

    def drop_conversation(self, conversation_id: str) -> None:
        for run_id in [k for k, t in self._data.items() if t.conversation_id == conversation_id]:
            self._data.pop(run_id, None)

    def prune(self) -> int:
        now = time.time()
        stale = [k for k, t in self._data.items() if now - t.started_at > TTL_SECONDS]
        for run_id in stale:
            self._data.pop(run_id, None)
        return len(stale)

    def _evict(self) -> None:
        while len(self._data) > MAX_RUNS:
            self._data.popitem(last=False)


traces = TraceStore()
