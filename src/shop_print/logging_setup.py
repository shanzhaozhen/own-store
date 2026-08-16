"""日志。技术细节只进日志文件，界面上只显示 texts.friendly_error() 的人话。"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from . import paths

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5


def setup(verbose: bool = False) -> None:
    """装好文件日志（轮转）+ 控制台日志。多次调用只生效一次。"""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    try:
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            paths.log_dir() / "app.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # 日志写不了不该拦住程序 —— 长辈要的是能打印，不是能记日志。
        pass

    # 打包后是 --windowed，没有控制台，stderr 可能是 None。
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)
