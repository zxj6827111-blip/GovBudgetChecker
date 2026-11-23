"""Isolate a slow local rule while the rule worker still has time to continue."""

from contextlib import contextmanager
from contextvars import ContextVar
import signal
import threading


_item_seconds = ContextVar("rule_item_seconds", default=None)


class RuleItemTimeout(TimeoutError):
    """An individual rule exhausted its execution budget."""


class _AlarmExpired(BaseException):
    # 规则内部会捕获普通 Exception 并继续探测，计时中断必须传到规则边界。
    pass


@contextmanager
def rule_worker_budget(total_seconds):
    """Leave time for other rules inside the existing process deadline."""
    ceiling = min(30.0, float(total_seconds) / 3)
    token = _item_seconds.set(ceiling if ceiling > 0 else None)
    try:
        yield
    finally:
        _item_seconds.reset(token)


@contextmanager
def rule_item_deadline(document):
    seconds = _item_seconds.get()
    if (seconds is None or not hasattr(signal, "setitimer")
            or threading.current_thread() is not threading.main_thread()):
        yield
        return

    def expire(_signum, _frame):
        raise _AlarmExpired()

    # 规则之间共享原件与当前事实。撤掉这条规则尚未返回的回执、缓存和投递抑制，
    # 防止超时前写了一半的输出影响后续规则。原件与事实不复制，也不改写。
    state = vars(document) if hasattr(document, "__dict__") else None
    snapshot = {
        key: value.copy() if isinstance(value, (dict, list, set)) else value
        for key, value in (state or {}).items()
    }
    previous_handler = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    except _AlarmExpired:
        if state is not None:
            state.clear()
            state.update(snapshot)
        raise RuleItemTimeout(f"本规则执行超过 {seconds:g} 秒，结果待复核") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
