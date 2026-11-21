"""Per-server console buffer and log parsing.

The parser turns raw lines into panel events (ready / player join / leave /
death / error). It follows the vanilla log format, which Paper and Fabric keep:

    2026-11-22 01:23:45 [Server thread/INFO]: Steve joined the game
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

MAX_LINE = 4000

_RE_JOIN = re.compile(r"\]: ([A-Za-z0-9_]{1,16}) joined the game")
_RE_LEAVE = re.compile(r"\]: ([A-Za-z0-9_]{1,16}) left the game")
_RE_READY = re.compile(r"Done \([\d.]+s\)! For help, type")
_RE_LIST = re.compile(r"There are (\d+)(?: of a max of \d+)? players online")
_RE_SEVERE = re.compile(r"\b(ERROR|SEVERE|FATAL)\b")
_RE_WARN = re.compile(r"\bWARN(?:ING)?\b")


@dataclass
class ParsedLine:
    raw: str
    level: str = "info"
    event: str = ""
    player: str = ""
    count: int | None = None


def parse(line: str) -> ParsedLine:
    out = ParsedLine(raw=line)
    if _RE_READY.search(line):
        out.event = "ready"
        out.level = "info"
        return out
    if m := _RE_JOIN.search(line):
        out.event, out.player = "join", m.group(1)
    elif m := _RE_LEAVE.search(line):
        out.event, out.player = "leave", m.group(1)
    elif m := _RE_LIST.search(line):
        out.event, out.count = "list", int(m.group(1))
    if _RE_SEVERE.search(line):
        out.level = "error"
    elif _RE_WARN.search(line):
        out.level = "warn"
    return out


@dataclass
class ConsoleBuffer:
    """Ring buffer for one server's console output."""

    capacity: int = 2000
    lines: deque[str] = field(default_factory=deque)
    players: set[str] = field(default_factory=set)
    last_error: str = ""
    total: int = 0

    def __post_init__(self) -> None:
        self.lines = deque(maxlen=self.capacity)

    def push(self, line: str, stream: str = "stdout") -> ParsedLine:
        line = line.rstrip("\r\n")
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE] + "…"
        self.lines.append(line)
        self.total += 1
        parsed = parse(line)
        if parsed.event == "join" and parsed.player:
            self.players.add(parsed.player)
        elif parsed.event == "leave" and parsed.player:
            self.players.discard(parsed.player)
        elif parsed.event == "ready":
            self.players.clear()
        if parsed.level == "error":
            self.last_error = line
        return parsed

    def clear(self) -> None:
        self.lines.clear()
        self.players.clear()
        self.total = 0

    def snapshot(self, limit: int = 500) -> list[dict]:
        items = list(self.lines)[-limit:]
        now = time.time()
        return [{"line": ln, "ts": now} for ln in items]
