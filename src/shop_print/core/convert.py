"""把任意文件归一化成 PDF。

打印路径只有一条，所以先统一格式：后面的预览、打印都只认 PDF。
顾客看到的预览和打出来的纸因此一定是同一份数据。

转换结果按**源文件内容 hash + 选项**缓存，同一份文件重复打印不重复转换。
见 docs/04-文档转换与打印.md。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from .. import paths
from ..texts import ErrorKind
from . import enhance as enhance_mod
from . import office_worker
from .errors import ShopPrintError, broken, unsupported

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
TEXT_SUFFIXES = {".txt", ".log", ".md"}
OFFICE_SUFFIXES = (
    office_worker.WORD_SUFFIXES | office_worker.EXCEL_SUFFIXES | office_worker.PPT_SUFFIXES
)
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | TEXT_SUFFIXES | OFFICE_SUFFIXES | PDF_SUFFIXES

# 纸张尺寸（纵向，单位 pt = 1/72 英寸）
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A4": (595.276, 841.890),
    "A3": (841.890, 1190.551),
    "B5": (498.898, 708.661),
    "Letter": (612.0, 792.0),
}
_MM = 72.0 / 25.4
_FIT_MARGIN = 5.0 * _MM  # 适应纸张模式留 5mm 边

FIT_FIT = "fit"  # 适应纸张：等比缩放到页内，留边
FIT_ACTUAL = "actual"  # 原尺寸：按图片自带 DPI 摆放，放不下时退回 fit
FIT_FILL = "fill"  # 铺满：等比放大盖满整页，超出部分裁掉
FIT_MODES = (FIT_FIT, FIT_ACTUAL, FIT_FILL)

# Office 子进程的超时。长辈等不了更久，超时就当文件有问题。
_OFFICE_TIMEOUT_SEC = 120
# 中文内置字体（PyMuPDF 自带，不依赖店铺机装了什么字体）
_CJK_FONT = "china-s"


@dataclass
class ConvertOptions:
    paper: str = "A4"
    fit: str = FIT_FIT
    auto_orient: bool = True  # 横图自动用横向纸，省纸
    # 图片是否走去底增强。None = 不增强，原样放进 PDF。
    enhance: enhance_mod.EnhanceOptions | None = None

    def signature(self) -> str:
        data = {
            "paper": self.paper,
            "fit": self.fit,
            "auto_orient": self.auto_orient,
            "enhance": asdict(self.enhance) if self.enhance else None,
        }
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:12]


def classify(path: str | Path) -> str:
    """返回 pdf | image | office | text | unsupported。"""
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in OFFICE_SUFFIXES:
        return "office"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "unsupported"


def is_supported(path: str | Path) -> bool:
    return classify(path) != "unsupported"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _cache_path(src: Path, options: ConvertOptions) -> Path:
    cache = paths.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"{_file_hash(src)}-{options.signature()}.pdf"


def _page_size(paper: str, landscape: bool) -> tuple[float, float]:
    width, height = PAPER_SIZES.get(paper, PAPER_SIZES["A4"])
    return (height, width) if landscape else (width, height)


def open_pdf(path: str | Path) -> pymupdf.Document:
    """打开 PDF 并做可用性检查。加密/损坏都翻译成友好错误。"""
    path = Path(path)
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise broken(path.name, str(exc)) from exc
    if doc.needs_pass:
        doc.close()
        raise ShopPrintError(ErrorKind.FILE_ENCRYPTED, f"{path.name} 需要密码")
    if doc.page_count == 0:
        doc.close()
        raise broken(path.name, "没有任何页面")
    return doc


def page_count(path: str | Path) -> int:
    with open_pdf(path) as doc:
        return doc.page_count


def _placement(
    img_w_pt: float, img_h_pt: float, page_w: float, page_h: float, fit: str
) -> pymupdf.Rect:
    """算出图片在页面上的摆放矩形（等比，居中）。"""
    if img_w_pt <= 0 or img_h_pt <= 0:
        return pymupdf.Rect(0, 0, page_w, page_h)

    if fit == FIT_FILL:
        scale = max(page_w / img_w_pt, page_h / img_h_pt)
    elif fit == FIT_ACTUAL:
        scale = 1.0
        if img_w_pt > page_w or img_h_pt > page_h:
            # 原尺寸放不下就退回"适应纸张"，宁可缩小也不能裁掉内容。
            scale = min(
                (page_w - 2 * _FIT_MARGIN) / img_w_pt, (page_h - 2 * _FIT_MARGIN) / img_h_pt
            )
    else:
        scale = min((page_w - 2 * _FIT_MARGIN) / img_w_pt, (page_h - 2 * _FIT_MARGIN) / img_h_pt)

    width, height = img_w_pt * scale, img_h_pt * scale
    left, top = (page_w - width) / 2.0, (page_h - height) / 2.0
    return pymupdf.Rect(left, top, left + width, top + height)


def _encode_png(array: np.ndarray, binary: bool) -> bytes:
    """numpy → PNG 字节。二值图存成 1-bit，体积小很多，黑白打印也更利。"""
    image = Image.fromarray(array, mode="L")
    if binary:
        image = image.convert("1")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _prepare_image(src: Path, options: ConvertOptions) -> tuple[bytes, int, int, float]:
    """返回 (PNG 字节, 宽 px, 高 px, dpi)。需要增强时先过 enhance。"""
    with Image.open(src) as probe:
        dpi_pair = probe.info.get("dpi") or (0, 0)
    dpi = float(dpi_pair[0]) if dpi_pair and dpi_pair[0] else 96.0

    if options.enhance is None:
        image = enhance_mod.load_image(src)
        gray = enhance_mod._to_gray(image)  # noqa: SLF001 —— 同包内的内部工具函数
        data = _encode_png(gray, binary=False)
        return data, gray.shape[1], gray.shape[0], dpi

    result = enhance_mod.enhance_file(src, options.enhance)
    binary = result.mode_used == enhance_mod.MODE_TEXT
    data = _encode_png(result.image, binary=binary)
    return data, result.image.shape[1], result.image.shape[0], dpi


def images_to_pdf(sources: list[Path], out_path: Path, options: ConvertOptions) -> Path:
    """多张图合并成一份 PDF，一张一页。"""
    if not sources:
        raise broken("（空）", "没有可用的图片")
    doc = pymupdf.open()
    try:
        for src in sources:
            data, px_w, px_h, dpi = _prepare_image(src, options)
            landscape = options.auto_orient and px_w > px_h
            page_w, page_h = _page_size(options.paper, landscape)
            page = doc.new_page(width=page_w, height=page_h)
            img_w_pt, img_h_pt = px_w * 72.0 / dpi, px_h * 72.0 / dpi
            rect = _placement(img_w_pt, img_h_pt, page_w, page_h, options.fit)
            page.insert_image(rect, stream=data)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(out_path, deflate=True, garbage=3)
    finally:
        doc.close()
    return out_path


def _load_cjk_font() -> pymupdf.Font:
    """中文字体。优先 PyMuPDF 内置的简体宋体，拿不到再找 assets/fonts 里的。

    刻意不依赖"系统装了微软雅黑"—— 店铺机装了什么字体不受我们控制。
    """
    try:
        return pymupdf.Font(_CJK_FONT)
    except Exception:
        logger.warning("内置中文字体 %s 拿不到，改用 assets/fonts", _CJK_FONT)
    for pattern in ("*.ttf", "*.otf", "*.ttc"):
        for candidate in sorted(paths.fonts_dir().glob(pattern)):
            try:
                return pymupdf.Font(fontfile=str(candidate))
            except Exception:
                continue
    raise ShopPrintError(ErrorKind.UNKNOWN, "找不到可用的中文字体")


def _read_text(src: Path) -> str:
    """顾客的 txt 编码很杂：utf-8、GBK、带 BOM 的都有，逐个试。"""
    raw = src.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _wrap_lines(text: str, font: pymupdf.Font, fontsize: float, width: float) -> list[str]:
    """按实际字宽折行。中文没有词边界，所以逐字累加测量。"""
    wrapped: list[str] = []
    for raw_line in text.replace("\t", "    ").splitlines() or [""]:
        if not raw_line:
            wrapped.append("")
            continue
        current = ""
        for char in raw_line:
            if font.text_length(current + char, fontsize) > width and current:
                wrapped.append(current)
                current = char
            else:
                current += char
        wrapped.append(current)
    return wrapped


def text_to_pdf(src: Path, out_path: Path, options: ConvertOptions) -> Path:
    font = _load_cjk_font()
    fontsize = 11.0
    line_height = fontsize * 1.6
    margin = 20.0 * _MM
    page_w, page_h = _page_size(options.paper, landscape=False)
    usable_w = page_w - 2 * margin
    rows_per_page = max(1, int((page_h - 2 * margin) // line_height))

    lines = _wrap_lines(_read_text(src), font, fontsize, usable_w)
    doc = pymupdf.open()
    try:
        for start in range(0, max(len(lines), 1), rows_per_page):
            page = doc.new_page(width=page_w, height=page_h)
            page.insert_font(fontname="cjk", fontbuffer=font.buffer)
            y = margin + fontsize
            for line in lines[start : start + rows_per_page]:
                if line:
                    page.insert_text((margin, y), line, fontname="cjk", fontsize=fontsize)
                y += line_height
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(out_path, deflate=True, garbage=3)
    finally:
        doc.close()
    return out_path


def _worker_command(src: Path, dst: Path) -> list[str]:
    """打包成 exe 之后没有 `python -m`，改成自己带标志重新执行一次。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, office_worker.WORKER_FLAG, str(src), str(dst)]
    return [sys.executable, "-m", "shop_print.core.office_worker", str(src), str(dst)]


def office_to_pdf(src: Path, out_path: Path, timeout: int = _OFFICE_TIMEOUT_SEC) -> Path:
    """交给独立子进程调 Office COM。超时就杀掉，不让 Word 卡死拖住界面。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            _worker_command(src, out_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ShopPrintError(ErrorKind.OFFICE_TIMEOUT, f"{src.name} 转换超时") from exc
    except OSError as exc:
        raise ShopPrintError(ErrorKind.UNKNOWN, f"启动转换进程失败：{exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        logger.error("Office 转换失败：%s\n%s", src, detail)
        # 没装 Office / COM 注册坏了，和"文件本身坏了"要给不同的提示。
        if any(
            token in detail for token in ("Invalid class string", "-2147221005", "无效的类字符串")
        ):
            raise ShopPrintError(ErrorKind.OFFICE_MISSING, detail)
        raise broken(src.name, detail)
    return out_path


def merged_cache_path(sources: list[Path], options: ConvertOptions) -> Path:
    """多张图合并的缓存路径。按内容 hash，不用 hash() —— 那个每次进程都会变。"""
    digest = hashlib.sha256()
    for source in sources:
        digest.update(_file_hash(source).encode("ascii"))
    cache = paths.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"合并{len(sources)}张-{digest.hexdigest()[:16]}-{options.signature()}.pdf"


def images_to_pdf_cached(sources: list[Path], options: ConvertOptions) -> Path:
    out_path = merged_cache_path(sources, options)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    return images_to_pdf(sources, out_path, options)


def to_pdf(src: str | Path, options: ConvertOptions | None = None, use_cache: bool = True) -> Path:
    """任意受支持的文件 → PDF 路径。PDF 原样返回，其余落到缓存目录。"""
    src = Path(src)
    opts = options or ConvertOptions()
    kind = classify(src)

    # 先判类型再判存在：扩展名就打不了的时候，「请让顾客发成 PDF 或者图片」
    # 比「文件坏了，请重新发一次」更有用 —— 重发一次同样的 mp4 还是打不了。
    if kind == "unsupported":
        raise unsupported(src.suffix)
    if not src.exists():
        raise broken(src.name, "文件不存在")
    if kind == "pdf":
        open_pdf(src).close()  # 只为校验可读、没加密
        return src

    out_path = _cache_path(src, opts)
    if use_cache and out_path.exists() and out_path.stat().st_size > 0:
        logger.debug("命中转换缓存：%s", out_path.name)
        return out_path

    if kind == "image":
        return images_to_pdf([src], out_path, opts)
    if kind == "text":
        return text_to_pdf(src, out_path, opts)
    return office_to_pdf(src, out_path)


def render_page_png(src: str | Path, index: int, dpi: int = 110) -> bytes:
    """渲染某页成 PNG 字节，给界面预览用。

    刻意返回字节而不是 QPixmap —— core/ 不许 import Qt，见 docs/02-架构与分层.md。
    """
    with open_pdf(src) as doc:
        index = max(0, min(index, doc.page_count - 1))
        pixmap = doc[index].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")


def render_page_gray(doc: pymupdf.Document, index: int, dpi: int) -> tuple[np.ndarray, int, int]:
    """渲染某页成灰度 numpy，给 GDI 打印用。返回 (数组, 宽, 高)。"""
    pixmap = doc[index].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    array = np.frombuffer(pixmap.samples, dtype=np.uint8)
    array = array.reshape(pixmap.height, pixmap.stride)[:, : pixmap.width]
    return array, pixmap.width, pixmap.height
