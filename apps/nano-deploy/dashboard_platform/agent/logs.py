from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class LogEvent:
    channel: str
    level: str
    message: str
    timestamp: str
    source: str = "agent"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class LogBuffer:
    def __init__(self, max_size: int) -> None:
        self._buffer: deque[LogEvent] = deque(maxlen=max_size)
        self._subscribers: set[asyncio.Queue[LogEvent]] = set()

    def add(self, channel: str, level: str, message: str, source: str = "agent") -> LogEvent:
        event = LogEvent(
            channel=channel,
            level=level,
            message=message.rstrip(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=source,
        )
        self._buffer.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def recent(self, channel: str | None = None) -> list[dict[str, Any]]:
        items = list(self._buffer)
        if channel:
            items = [item for item in items if item.channel == channel]
        return [item.as_dict() for item in items]

    def subscribe(self) -> asyncio.Queue[LogEvent]:
        queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LogEvent]) -> None:
        self._subscribers.discard(queue)
