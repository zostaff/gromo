"""In-process queues between agents, plus a broadcast hub for SSE / pushes.

scanner -> auditor -> analyst -> executor -> risk
"""
import asyncio
from typing import Any

auditor: asyncio.Queue = asyncio.Queue()
analyst: asyncio.Queue = asyncio.Queue()
executor: asyncio.Queue = asyncio.Queue()
risk: asyncio.Queue = asyncio.Queue()


class Hub:
    """Fan-out of events to SSE listeners. event = {"type": str, "data": dict}."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def emit(self, type_: str, data: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait({"type": type_, "data": data})
            except asyncio.QueueFull:
                pass


hub = Hub()


def emit(type_: str, data: dict[str, Any]) -> None:
    hub.emit(type_, data)
