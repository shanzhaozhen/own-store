"""后台任务的生命周期。

这里盯的是一个**会静默吃掉回调**的坑：`run_async` 返回的 Worker 如果没人拿着，
它一被垃圾回收，`signals` 跟着没了，连接断掉，回调再也不会到 ——
界面上的表现是"点了没反应"，而日志里什么都没有。

用 lambda 当回调时最容易踩到（用 QObject 的绑定方法时会侥幸活着）。
所以 `run_async` 自己留了一份强引用，跑完才放。
"""

from __future__ import annotations

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 测试里不开真窗口

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from shop_print.core.errors import ShopPrintError
from shop_print.texts import ErrorKind
from shop_print.ui.workers import run_async


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    application = existing or QApplication([])
    yield application


def 等回调(app, 结果: list, 超时毫秒: int = 5000) -> None:
    等了 = 0
    while not 结果 and 等了 < 超时毫秒:
        QCoreApplication.processEvents()
        app.thread().msleep(20)
        等了 += 20


def test_lambda回调也能收到(app) -> None:
    """这条就是那个 bug 的回归：调用方不接返回值 + 回调是 lambda。"""
    结果: list = []
    run_async(lambda: 6 * 7, on_done=lambda value: 结果.append(value))
    gc.collect()  # 逼一下垃圾回收，模拟真实运行里的时机
    等回调(app, 结果)
    assert 结果 == [42]


def test_出错时给的是人话(app) -> None:
    结果: list = []

    def 炸():
        raise ShopPrintError(ErrorKind.FILE_BROKEN, "文件坏了")

    run_async(炸, on_failed=lambda message: 结果.append(message))
    gc.collect()
    等回调(app, 结果)
    assert 结果 and "重新发" in 结果[0]  # texts 里那句友好话术


def test_没预料到的异常也翻译成人话(app) -> None:
    结果: list = []
    run_async(lambda: 1 / 0, on_failed=lambda message: 结果.append(message))
    等回调(app, 结果)
    assert 结果 and "出了点问题" in 结果[0]


def test_跑完之后不再占着引用(app) -> None:
    from shop_print.ui import workers

    结果: list = []
    run_async(lambda: "好了", on_done=lambda value: 结果.append(value))
    等回调(app, 结果)
    # finished 排在 done 后面，所以回调到了之后还要再转几圈才会放引用
    for _ in range(20):
        QCoreApplication.processEvents()
        app.thread().msleep(10)
    assert 结果 == ["好了"]
    assert not workers._RUNNING  # noqa: SLF001 —— 就是要验这份引用被放掉了


def test_进度回调会被注进去(app) -> None:
    """core 里的函数只认 `progress` 这个关键字参数，不需要知道 Qt。"""
    进度: list = []
    结果: list = []

    def 干活(progress=None):
        for i in (1, 2, 3):
            if progress:
                progress(i, 3)
        return "完"

    run_async(
        干活,
        on_done=lambda value: 结果.append(value),
        on_progress=lambda current, total: 进度.append((current, total)),
    )
    等回调(app, 结果)
    for _ in range(10):
        QCoreApplication.processEvents()
        app.thread().msleep(10)
    assert 结果 == ["完"]
    assert 进度 == [(1, 3), (2, 3), (3, 3)]
