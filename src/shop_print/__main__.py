"""程序入口。

也负责一件不显眼但重要的事：打包成 exe 之后没有 `python -m`，
Office 转换的子进程靠 `打印助手.exe --office-worker <源> <目标>` 重新执行自己，
所以要在建 QApplication **之前**先把这个分支处理掉。
"""

from __future__ import annotations

import logging
import sys
import traceback

from . import config as config_mod
from . import logging_setup, paths, texts

logger = logging.getLogger(__name__)


def _run_office_worker(argv: list[str]) -> int:
    from .core.office_worker import main as worker_main

    return worker_main(argv)


def _install_excepthook() -> None:
    """兜住所有没被接住的异常：写日志 + 给一句人话，不让长辈看到堆栈。"""

    def handle(kind, value, tb) -> None:
        logger.critical("未捕获的异常：%s", "".join(traceback.format_exception(kind, value, tb)))
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    texts.APP_TITLE,
                    texts.friendly_error(texts.ErrorKind.UNKNOWN),
                )
        except Exception:
            logger.exception("弹错误提示时又出错了")

    sys.excepthook = handle


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # 子进程分支：必须在任何 Qt 初始化之前
    if args and args[0] == "--office-worker":
        return _run_office_worker(args[1:])

    # 自检分支：打包后排查"资源有没有打进去、这台机器上 OCR 多久"，不需要界面
    if args and args[0] == "--self-check":
        from .self_check import run as run_self_check

        paths.ensure_runtime_dirs()
        logging_setup.setup()  # 不开 verbose：报告要能直接看，别被 DEBUG 日志淹了
        return run_self_check()

    paths.ensure_runtime_dirs()
    logging_setup.setup(verbose="--debug" in args)
    logger.info("启动 %s", texts.APP_TITLE)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(texts.APP_TITLE)
    _install_excepthook()

    qss = paths.style_file()
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))
    else:  # pragma: no cover
        logger.warning("找不到样式文件：%s", qss)

    icon_path = paths.icons_dir() / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow(config_mod.load())
    # 直接最大化：长辈不会去拖窗口边框，铺满屏幕才能拿到最大的按钮和最大的预览。
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
