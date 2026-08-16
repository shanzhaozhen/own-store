"""把耗时的活儿丢到线程池，界面不许卡。

长辈看到界面"卡住不动"就会反复点或者直接关掉，所以 OCR、全分辨率图像处理、
Office 转换、打印光栅化一律走这里。规矩见 docs/02-架构与分层.md。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

from ..core.errors import ShopPrintError
from ..texts import ErrorKind, friendly_error

logger = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)  # 已经翻译成人话的提示
    progress = Signal(int, int)


class Worker(QRunnable):
    """在线程池里跑一个函数。

    如果目标函数接受 `progress` 关键字参数，就把进度回调注进去 ——
    这样 core/ 里的函数不需要知道 Qt 的存在。
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _Signals()

    def run(self) -> None:  # pragma: no cover —— 在 Qt 线程里跑
        try:
            result = self._fn(*self._args, **self._kwargs)
        except ShopPrintError as exc:
            logger.error("任务失败（%s）：%s", exc.kind.name, exc.detail)
            self.signals.failed.emit(exc.friendly)
        except Exception:
            logger.exception("任务出现未预料的错误")
            self.signals.failed.emit(friendly_error(ErrorKind.UNKNOWN))
        else:
            self.signals.done.emit(result)


def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    **kwargs: Any,
) -> Worker:
    """起一个后台任务。回调都在主线程执行，可以直接改界面。"""
    worker = Worker(fn, *args, **kwargs)
    if on_done is not None:
        worker.signals.done.connect(on_done, Qt.ConnectionType.QueuedConnection)
    if on_failed is not None:
        worker.signals.failed.connect(on_failed, Qt.ConnectionType.QueuedConnection)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress, Qt.ConnectionType.QueuedConnection)
        kwargs["progress"] = worker.signals.progress.emit
        worker._kwargs = kwargs  # noqa: SLF001 —— 同模块内，注入进度回调
    QThreadPool.globalInstance().start(worker)
    return worker
