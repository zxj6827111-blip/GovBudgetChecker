"""Per-pipeline queue accounting shared with its child tasks via contextvars."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time


@dataclass
class AIQueueWaitTimer:
    accumulated: float = 0.0
    waiting_since: float | None = None

    @property
    def elapsed(self) -> float:
        current = time.monotonic() - self.waiting_since if self.waiting_since is not None else 0.0
        return self.accumulated + current


_queue_timer: ContextVar[AIQueueWaitTimer | None] = ContextVar('ai_queue_timer', default=None)


@contextmanager
def ai_queue_wait_scope():
    timer = AIQueueWaitTimer()
    token = _queue_timer.set(timer)
    try:
        yield timer
    finally:
        _queue_timer.reset(token)


@contextmanager
def record_ai_queue_wait():
    timer = _queue_timer.get()
    if timer is None:
        yield
        return
    timer.waiting_since = time.monotonic()
    try:
        yield
    finally:
        timer.accumulated += time.monotonic() - timer.waiting_since
        timer.waiting_since = None
