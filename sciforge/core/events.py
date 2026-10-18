"""事件总线：CLI / Web / Pipeline 共享的可订阅事件流。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "payload": self.payload, "ts": self.ts}


Handler = Callable[[Event], None]


class EventBus:
    """线程安全的最小事件总线。

    kind 取值约定：
      log / tool_call / tool_result / llm_delta / stage / artifact / error / usage
    """

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._lock = threading.RLock()
        self.history: list[Event] = []

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def emit(self, kind: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(kind=kind, payload=payload or {})
        with self._lock:
            self.history.append(event)
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # 单个订阅者失败不影响其它订阅者
                continue
        return event

    def since(self, ts: float) -> Iterable[Event]:
        with self._lock:
            return [e for e in self.history if e.ts > ts]

    def clear(self) -> None:
        with self._lock:
            self.history.clear()


class CallbackBus(EventBus):
    """额外支持一个兜底回调，方便工具/Agent 在无 UI 场景下收集事件。"""

    def __init__(self, callback: Handler | None = None) -> None:
        super().__init__()
        if callback is not None:
            self.subscribe(callback)


__all__ = ["Event", "EventBus", "CallbackBus", "Handler"]
