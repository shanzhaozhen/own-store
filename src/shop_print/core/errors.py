"""领域异常。

每个异常都带一个 ErrorKind，界面拿到之后直接显示 texts.friendly_error()
的人话，技术细节（detail）只写日志。这样"不给用户看堆栈"这条规矩
就落在类型系统里，而不是靠每处 try 自己记得。
"""

from __future__ import annotations

from ..texts import ErrorKind, friendly_error


class ShopPrintError(Exception):
    """所有可预期的失败都用这个抛，界面统一处理。"""

    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail or kind.name)

    @property
    def friendly(self) -> str:
        """给长辈看的一句话。"""
        return friendly_error(self.kind)


def unsupported(suffix: str) -> ShopPrintError:
    return ShopPrintError(ErrorKind.FILE_UNSUPPORTED, f"不支持的扩展名：{suffix}")


def broken(path: str, detail: str = "") -> ShopPrintError:
    return ShopPrintError(ErrorKind.FILE_BROKEN, f"{path} 打不开：{detail}")
