"""把 PDF 打到打印机上（店铺里是柯美 bizhub 225i，只能黑白）。

为什么自己用 GDI 打而不是调外部程序：DEVMODE 完全可控，份数/双面/纸张/
单色都是显式设死的，不靠驱动默认值；而且能拿到逐页进度，界面上才能显示
「正在打印第 2 页 / 共 5 页」—— 这个反馈对长辈很重要。
取舍过程见 docs/decisions/ADR-003-打印后端选择.md。

**开发机上只有虚拟打印机**，柯美驱动的真实行为没法在这里验证。
开发期用「Microsoft Print to PDF」当替身，交付前必须在店铺机实打一次。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf
from PIL import Image, ImageWin

from ..texts import ErrorKind
from . import convert
from .errors import ShopPrintError

logger = logging.getLogger(__name__)

# 只有 Windows 上才有这些模块。导入失败不该让整个程序起不来
# （比如在 CI 或 Linux 上跑 core 的单元测试）。
try:
    import win32con
    import win32gui
    import win32print
    import win32ui

    _WIN32_READY = True
except ImportError:  # pragma: no cover —— 非 Windows 环境
    _WIN32_READY = False

# DEVMODE 相关常量。win32con 里名字不全，这里显式写出来，免得某些
# pywin32 版本缺属性就崩。
_DM_ORIENTATION = 0x00000001
_DM_PAPERSIZE = 0x00000002
_DM_COPIES = 0x00000100
_DM_COLOR = 0x00000800
_DM_DUPLEX = 0x00001000

_DMORIENT_PORTRAIT = 1
_DMORIENT_LANDSCAPE = 2
_DMCOLOR_MONOCHROME = 1
_DMDUP_SIMPLEX = 1
_DMDUP_VERTICAL = 2  # 长边翻转，纵向文档的常规装订方式

_PAPER_CODES = {"A4": 9, "A3": 8, "Letter": 1, "B5": 13}

_PRINTER_STATUS_OFFLINE = 0x00000080
_PRINTER_STATUS_ERROR = 0x00000002
_PRINTER_STATUS_NOT_AVAILABLE = 0x00001000
_PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400
_DC_DUPLEX = 7


@dataclass
class PrinterInfo:
    name: str
    is_default: bool = False
    offline: bool = False


@dataclass
class PrintSettings:
    printer: str = ""  # 空 = 系统默认打印机
    copies: int = 1
    duplex: bool = False
    paper: str = "A4"
    dpi: int = 300  # 600dpi 下 A4 灰度约 70MB/页，弱机器吃不消
    page_range: tuple[int, int] | None = None  # 1 起、含两端；None = 全部
    job_name: str = "打印助手"
    # 把输出重定向到文件。给「Microsoft Print to PDF」这类虚拟打印机用：
    # 不填它会弹"另存为"对话框，填了就直接写文件、不弹窗。
    # 开发机上没有柯美 225i，全链路验证就靠这个。
    output_file: str | Path | None = None


ProgressCallback = Callable[[int, int], None]  # (当前页, 总页数)


def _require_win32() -> None:
    if not _WIN32_READY:
        raise ShopPrintError(ErrorKind.PRINTER_NOT_FOUND, "当前系统没有 Windows 打印接口")


def default_printer() -> str:
    _require_win32()
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return ""


def _printer_port(name: str) -> str:
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for entry in win32print.EnumPrinters(flags, None, 2):
        if entry["pPrinterName"] == name:
            return str(entry.get("pPortName", "") or "")
    return ""


def duplex_support(name: str) -> bool | None:
    """这台打印机能不能自动双面。**返回 None 表示问不出来。**

    只在真要双面打印时才问，不在列打印机时顺手问一遍：某些驱动在
    DeviceCapabilities 里会抛 Windows 结构化异常（开发机上是 0x80040155），
    Python 层虽然能接住，但每次列打印机都触发一次既慢又会刷一堆吓人的日志。

    问不出来时**不要擅自改成单面** —— 长辈选了双面却出来一堆单面纸，
    比让驱动自己决定更糟。
    """
    _require_win32()
    try:
        return int(win32print.DeviceCapabilities(name, _printer_port(name), _DC_DUPLEX)) == 1
    except Exception:
        logger.warning("问不出打印机「%s」的双面能力，按用户选的来", name, exc_info=True)
        return None


def list_printers() -> list[PrinterInfo]:
    """列出所有本机可用打印机。界面上默认选中记住的那台。"""
    _require_win32()
    default = default_printer()
    result: list[PrinterInfo] = []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for entry in win32print.EnumPrinters(flags, None, 2):
        name = entry["pPrinterName"]
        status = int(entry.get("Status", 0) or 0)
        attributes = int(entry.get("Attributes", 0) or 0)
        offline = bool(
            status & (_PRINTER_STATUS_OFFLINE | _PRINTER_STATUS_NOT_AVAILABLE)
            or attributes & _PRINTER_ATTRIBUTE_WORK_OFFLINE
        )
        result.append(PrinterInfo(name=name, is_default=(name == default), offline=offline))
    result.sort(key=lambda p: (not p.is_default, p.name))
    return result


def find_printer(keyword: str) -> str:
    """按关键词找打印机（店铺机上可以用"225"或"KONICA"找柯美）。找不到返回空串。"""
    keyword = keyword.strip().lower()
    if not keyword:
        return ""
    for printer in list_printers():
        if keyword in printer.name.lower():
            return printer.name
    return ""


def _resolve_printer(name: str) -> str:
    _require_win32()
    target = (name or "").strip() or default_printer()
    if not target:
        raise ShopPrintError(ErrorKind.PRINTER_NOT_FOUND, "系统里没有任何打印机")
    available = list_printers()
    match = next((p for p in available if p.name == target), None)
    if match is None:
        raise ShopPrintError(
            ErrorKind.PRINTER_NOT_FOUND,
            f"找不到打印机「{target}」，现有：{[p.name for p in available]}",
        )
    if match.offline:
        raise ShopPrintError(ErrorKind.PRINTER_OFFLINE, f"打印机「{target}」离线")
    return target


def _build_devmode(printer: str, settings: PrintSettings, landscape: bool):
    """取打印机当前 DEVMODE 再改我们关心的字段。

    只改 Fields 里标记过的字段，其余保持驱动默认 —— 店里可能已经在驱动里
    设过省粉之类的选项，不要覆盖掉。
    """
    handle = win32print.OpenPrinter(printer)
    try:
        devmode = win32print.GetPrinter(handle, 2)["pDevMode"]
    finally:
        win32print.ClosePrinter(handle)
    if devmode is None:
        raise ShopPrintError(ErrorKind.UNKNOWN, f"拿不到打印机「{printer}」的 DEVMODE")

    fields = int(devmode.Fields or 0)

    devmode.Copies = max(1, int(settings.copies))
    fields |= _DM_COPIES

    paper_code = _PAPER_CODES.get(settings.paper)
    if paper_code:
        devmode.PaperSize = paper_code
        fields |= _DM_PAPERSIZE

    devmode.Orientation = _DMORIENT_LANDSCAPE if landscape else _DMORIENT_PORTRAIT
    fields |= _DM_ORIENTATION

    # 225i 只有黑白：显式设单色，别让驱动多做一道半调色处理。
    devmode.Color = _DMCOLOR_MONOCHROME
    fields |= _DM_COLOR

    devmode.Duplex = _DMDUP_VERTICAL if settings.duplex else _DMDUP_SIMPLEX
    fields |= _DM_DUPLEX

    devmode.Fields = fields
    return devmode


def _page_indexes(total: int, page_range: tuple[int, int] | None) -> list[int]:
    if not page_range:
        return list(range(total))
    start, end = page_range
    start = max(1, min(start, total))
    end = max(start, min(end, total))
    return list(range(start - 1, end))


def _render_for_device(
    doc: pymupdf.Document, index: int, dpi: int, device_landscape: bool
) -> Image.Image:
    """渲染一页成灰度 PIL 图；页面方向和纸张方向不一致时转 90°。

    转图片而不是切换 DEVMODE：一份 PDF 里可能纵横混排，中途改 DEVMODE 要
    ResetDC，容易和驱动打架。转图片能把纸用满，代价只是那几页要横着看。
    """
    page = doc[index]
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
    page_landscape = pixmap.width > pixmap.height
    if page_landscape != device_landscape:
        image = image.rotate(90, expand=True)
    return image


def print_pdf(
    pdf_path: str | Path,
    settings: PrintSettings | None = None,
    on_progress: ProgressCallback | None = None,
) -> int:
    """把 PDF 打出去，返回实际送出的页数。

    StartDoc/StartPage/EndPage/EndDoc 严格配对，异常路径走 AbortDoc ——
    否则打印队列里会留一个卡死的任务，长辈遇到这个只能重启电脑。
    """
    _require_win32()
    conf = settings or PrintSettings()
    printer = _resolve_printer(conf.printer)

    if conf.duplex and duplex_support(printer) is False:
        logger.warning("打印机「%s」不支持自动双面，改成单面", printer)
        conf = replace(conf, duplex=False)

    with convert.open_pdf(pdf_path) as doc:
        indexes = _page_indexes(doc.page_count, conf.page_range)
        if not indexes:
            raise ShopPrintError(ErrorKind.FILE_BROKEN, "没有要打印的页")

        first = doc[indexes[0]].rect
        device_landscape = first.width > first.height
        devmode = _build_devmode(printer, conf, device_landscape)

        hdc = win32gui.CreateDC("WINSPOOL", printer, devmode)
        dc = win32ui.CreateDCFromHandle(hdc)
        printable_w = dc.GetDeviceCaps(win32con.HORZRES)
        printable_h = dc.GetDeviceCaps(win32con.VERTRES)

        started = False
        try:
            if conf.output_file:
                dc.StartDoc(conf.job_name, str(conf.output_file))
            else:
                dc.StartDoc(conf.job_name)
            started = True
            for ordinal, index in enumerate(indexes, start=1):
                image = _render_for_device(doc, index, conf.dpi, device_landscape)
                dc.StartPage()
                _draw_fitted(dc, image, printable_w, printable_h)
                dc.EndPage()
                if on_progress:
                    on_progress(ordinal, len(indexes))
            dc.EndDoc()
            started = False
        except ShopPrintError:
            raise
        except Exception as exc:
            logger.exception("打印失败：%s", pdf_path)
            raise ShopPrintError(ErrorKind.PRINTER_OFFLINE, f"打印时出错：{exc}") from exc
        finally:
            if started:
                try:
                    dc.AbortDoc()  # 不留卡死的队列任务
                except Exception:
                    logger.warning("AbortDoc 也失败了", exc_info=True)
            dc.DeleteDC()
        return len(indexes)


def _draw_fitted(dc, image: Image.Image, printable_w: int, printable_h: int) -> None:
    """等比缩放居中画到可打印区域。

    打印机 DC 的原点就在可打印区左上角，所以不用再加 PHYSICALOFFSET；
    但**可打印区不等于纸张尺寸**，必须用 HORZRES/VERTRES 而不是纸张点数，
    否则内容会被裁掉边。
    """
    scale = min(printable_w / image.width, printable_h / image.height)
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    left = (printable_w - width) // 2
    top = (printable_h - height) // 2
    dib = ImageWin.Dib(image)
    dib.draw(dc.GetHandleOutput(), (left, top, left + width, top + height))
