"""In-process pub/sub used by every WebSocket fan-out.

Back-pressure policy: each subscriber owns a bounded queue; when it is full we
drop the *oldest* entry and increment a counter. Never block the producer -
the producer here is often the Minecraft process' stdout pipe, and blocking it
would freeze the server itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class Channel:
    """One WebSocket subscriber.

    ``eq=False`` keeps the default identity hash, which is what we need: the
    bus stores channels in a ``set`` and must never collapse two subscribers
    that happen to share a key.
    """

    key: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=512))
    dropped: int = 0

    def wants(self, topic: str) -> bool:
        return self.key == "global" or topic.startswith(self.key + ":")

    def offer(self, item: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(item)


class EventBus:
    def __init__(self) -> None:
        self._channels: set[Channel] = set()

    @contextlib.asynccontextmanager
    async def subscribe(self, key: str) -> AsyncIterator[Channel]:
        ch = Channel(key=key)
        self._channels.add(ch)
        try:
            yield ch
        finally:
            self._channels.discard(ch)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        item = {"t": topic.rsplit(":", 1)[-1], "ts": time.time(), **payload}
        for ch in list(self._channels):
            if ch.wants(topic):
                ch.offer(item)

    def broadcast(self, topic: str, payload: dict[str, Any]) -> None:
        self.publish(topic, payload)

    @property
    def connections(self) -> int:
        return len(self._channels)


bus = EventBus()


def topic(server_id: int, kind: str) -> str:
    return f"server:{server_id}:{kind}"
