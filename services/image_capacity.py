"""Process-local admission control shared by all image generation routes."""
from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from threading import Condition

from services.config import config


class ImageCapacityError(RuntimeError):
    pass


class ImageCapacity:
    def __init__(self, limits_getter=None):
        self._limits = limits_getter or (lambda: (
            config.image_global_concurrency, config.image_queue_capacity, config.image_queue_timeout_secs,
        ))
        self._condition = Condition()
        self._active = 0
        self._waiting = deque()

    @contextmanager
    def acquire(self, progress_callback=None):
        ticket = object()
        acquired = False
        with self._condition:
            limit, capacity, timeout = self._limits()
            if self._active >= limit or self._waiting:
                if len(self._waiting) >= capacity:
                    raise ImageCapacityError("图片请求队列已满，请稍后重试")
                if progress_callback:
                    progress_callback("waiting_global_capacity")
            self._waiting.append(ticket)
            deadline = time.monotonic() + timeout
            try:
                while self._waiting[0] is not ticket or self._active >= self._limits()[0]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ImageCapacityError("图片请求排队超时，尚未发起生成，请稍后重试")
                    self._condition.wait(min(remaining, 1.0))
                self._waiting.popleft()
                self._active += 1
                acquired = True
            finally:
                if not acquired:
                    self._waiting.remove(ticket)
                    self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            limit, capacity, timeout = self._limits()
            return {"active": self._active, "waiting": len(self._waiting), "concurrency": limit,
                    "capacity": capacity, "timeout_seconds": timeout}


image_capacity = ImageCapacity()
