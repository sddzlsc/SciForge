"""Pipeline 运行控制：暂停 / 继续 / 停止（可被 AgentLoop 在步边界检查）。"""

from __future__ import annotations

import threading


class RunControl:
    """线程安全的三态控制：running / paused / stopped。

    - ``pause()`` 后，AgentLoop 会在下一次 LLM 调用前等待；阶段引擎会在下一阶段前等待。
    - ``resume()`` 唤醒等待方继续。
    - ``stop()`` 会立即唤醒并让循环在下一个检查点退出。
    """

    def __init__(self) -> None:
        self._resume = threading.Event()
        self._resume.set()
        self._stop = threading.Event()
        self._paused = threading.Event()

    def pause(self) -> bool:
        if self._stop.is_set():
            return False
        self._paused.set()
        self._resume.clear()
        return True

    def resume(self) -> None:
        self._paused.clear()
        self._resume.set()

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()
        self._resume.set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def wait_if_paused(self, timeout: float = 0.5) -> bool:
        """暂停时阻塞；返回 False 表示已被停止，调用方应退出循环。"""
        while not self._resume.wait(timeout):
            if self._stop.is_set():
                return False
        return not self._stop.is_set()
