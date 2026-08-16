"""Office 文档转 PDF 的**独立子进程** worker。

为什么单独一个进程：COM 自动化调的是真的 Word/Excel 进程，遇到损坏文件、
宏、缺字体时可能弹窗或直接卡死。跑在子进程里，主程序设超时就能杀掉它，
界面照样能用。见 docs/04-文档转换与打印.md。

调用方式（由 convert.py 拼命令，不用手敲）：
    python -m shop_print.core.office_worker <源文件> <目标pdf>
打包成 exe 之后走 `打印助手.exe --office-worker <源文件> <目标pdf>`。
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKER_FLAG = "--office-worker"

WORD_SUFFIXES = {".doc", ".docx", ".docm", ".rtf", ".odt"}
EXCEL_SUFFIXES = {".xls", ".xlsx", ".xlsm", ".csv", ".ods"}
PPT_SUFFIXES = {".ppt", ".pptx", ".pptm", ".odp"}

_WD_EXPORT_PDF = 17
_XL_TYPE_PDF = 0
_PP_FIXED_FORMAT_PDF = 2
_XL_LANDSCAPE = 2


def _word_to_pdf(src: Path, dst: Path) -> None:
    import win32com.client

    # DispatchEx 起独立实例：Dispatch 会复用长辈正开着的 Word，
    # 我们的 Quit() 会把人家的窗口一起关掉。
    app = win32com.client.DispatchEx("Word.Application")
    app.Visible = False
    app.DisplayAlerts = 0
    doc = None
    try:
        doc = app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        doc.ExportAsFixedFormat(OutputFileName=str(dst), ExportFormat=_WD_EXPORT_PDF)
    finally:
        if doc is not None:
            doc.Close(False)
        app.Quit()


def _excel_to_pdf(src: Path, dst: Path) -> None:
    import win32com.client

    app = win32com.client.DispatchEx("Excel.Application")
    app.Visible = False
    app.DisplayAlerts = False
    wb = None
    try:
        wb = app.Workbooks.Open(str(src), ReadOnly=True, UpdateLinks=0)
        for ws in wb.Worksheets:
            setup = ws.PageSetup
            # 复印店最常见的浪费纸事故：十几列的表被横着切成 5 页。
            # Zoom 和 FitToPages 互斥，必须先关 Zoom，顺序不能颠倒。
            setup.Zoom = False
            setup.FitToPagesWide = 1
            setup.FitToPagesTall = False
            used = ws.UsedRange
            if used.Width > used.Height:
                setup.Orientation = _XL_LANDSCAPE
        wb.ExportAsFixedFormat(_XL_TYPE_PDF, str(dst))
    finally:
        if wb is not None:
            wb.Close(False)
        app.Quit()


def _ppt_to_pdf(src: Path, dst: Path) -> None:
    import win32com.client

    app = win32com.client.DispatchEx("PowerPoint.Application")
    pres = None
    try:
        pres = app.Presentations.Open(str(src), ReadOnly=True, WithWindow=False)
        pres.ExportAsFixedFormat(str(dst), _PP_FIXED_FORMAT_PDF)
    finally:
        if pres is not None:
            pres.Close()
        app.Quit()


def convert(src: Path, dst: Path) -> None:
    """按扩展名分派。COM 只认绝对路径字符串，不认 Path、不认相对路径。"""
    import pythoncom

    src, dst = src.resolve(), dst.resolve()
    suffix = src.suffix.lower()
    pythoncom.CoInitialize()
    try:
        if suffix in WORD_SUFFIXES:
            _word_to_pdf(src, dst)
        elif suffix in EXCEL_SUFFIXES:
            _excel_to_pdf(src, dst)
        elif suffix in PPT_SUFFIXES:
            _ppt_to_pdf(src, dst)
        else:
            raise ValueError(f"office_worker 不处理这种扩展名：{suffix}")
    finally:
        pythoncom.CoUninitialize()


def main(argv: list[str]) -> int:
    """argv 是去掉 worker 标志之后的参数：[源文件, 目标pdf]。"""
    if len(argv) != 2:
        print("用法：office_worker <源文件> <目标pdf>", file=sys.stderr)
        return 2
    src, dst = Path(argv[0]), Path(argv[1])
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        convert(src, dst)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not dst.exists() or dst.stat().st_size == 0:
        print("转换后没有生成 PDF", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
