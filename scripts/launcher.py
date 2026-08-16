"""PyInstaller 的入口脚本。

为什么需要这么一个文件：PyInstaller 会把入口脚本当成 `__main__` 直接执行，
而 `src/shop_print/__main__.py` 里用的是相对导入（`from . import config`），
被当成顶层脚本跑时没有包上下文，导入会直接失败。
所以让 PyInstaller 打这个壳，由它以**正常包导入**的方式调进去。

平时开发不用它，走 `python -m shop_print`。
"""

from __future__ import annotations

import multiprocessing


def main() -> int:
    # 打包后进程会被 Office 转换那条路重新执行自己（--office-worker），
    # 这里先做好冻结环境下的多进程保护，避免出现递归启动窗口。
    multiprocessing.freeze_support()
    from shop_print.__main__ import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
