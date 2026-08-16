"""照片转文字文档：OCR + 版面重建 → 可编辑的 Word。

需求原话是「拍照的图片需要转成文字文档并且排版」—— 重点在**并且排版**。
交付的不是一堆文字，是一份能直接改的 Word。

引擎用本地 RapidOCR（onnxruntime CPU），模型随包分发、运行时不联网。
为什么不是 PaddleOCR：见 docs/decisions/ADR-002-OCR引擎选择.md。
版面重建的规则见 docs/05-OCR与版面重建.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .. import paths
from ..texts import ErrorKind
from . import enhance as enhance_mod
from .errors import ShopPrintError

logger = logging.getLogger(__name__)

# 低于这个置信度就在界面上标出来，提示长辈核对
LOW_CONFIDENCE = 0.70

# 输出的 Word 用 A4：店里只有 A4/A3，和 convert / cards 出的 PDF 保持同一个物理尺寸
A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

# 行聚类：两个框的 y 中心差小于「中位字高 × 这个系数」就算同一行
_ROW_TOLERANCE = 0.6
# 段落切分：行间距大于「中位行间距 × 这个系数」就算换段
_PARAGRAPH_GAP = 1.8
# 上一行右端比右边界短这么多个字宽，就认为它是段末
_SHORT_LINE_SLACK_CHARS = 2.0
# 这一行左端比左边界缩进这么多个字宽，就认为是首行缩进、另起一段
_INDENT_SLACK_CHARS = 1.5
# 相邻两行字高比例超过这个值，就认为不是同一个文本块（标题 vs 正文）
_FONT_SIZE_JUMP = 1.4
# 整页文字块宽度不到页宽的这个比例，就认为这些是独立短行而不是折行的段落
_MIN_BLOCK_WIDTH_RATIO = 0.45
# 居中判定
_CENTERED_MAX_WIDTH_RATIO = 0.60
_CENTERED_MAX_OFFSET_RATIO = 0.08
# 标题判定：字高明显大于中位字高
_HEADING_HEIGHT_RATIO = 1.3


@dataclass
class OcrLine:
    """识别出的一行（可能由多个检测框拼成）。"""

    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass
class OcrParagraph:
    lines: list[OcrLine] = field(default_factory=list)
    is_heading: bool = False
    centered: bool = False

    @property
    def text(self) -> str:
        return "".join(line.text for line in self.lines)

    @property
    def min_score(self) -> float:
        return min((line.score for line in self.lines), default=0.0)


@dataclass
class OcrResult:
    paragraphs: list[OcrParagraph] = field(default_factory=list)
    lines: list[OcrLine] = field(default_factory=list)
    page_width: int = 0
    page_height: int = 0

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)

    @property
    def is_empty(self) -> bool:
        return not self.lines

    @property
    def low_confidence_lines(self) -> list[OcrLine]:
        """置信度低的行。界面上标出来，提示长辈核对 —— 识别不可能 100% 准，
        假装完美只会让顾客拿到错的文档。"""
        return [line for line in self.lines if line.score < LOW_CONFIDENCE]


@lru_cache(maxsize=1)
def get_engine() -> Any:
    """RapidOCR 引擎（进程内单例）。初始化要几百毫秒，不能每次识别都建。"""
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:  # pragma: no cover
        raise ShopPrintError(ErrorKind.UNKNOWN, f"OCR 引擎装不上：{exc}") from exc

    model_dir = paths.models_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    # model_root_dir 指到随包的模型目录：店铺网络不一定稳，
    # 首次使用不能卡在"正在下载模型"上。
    return RapidOCR(
        params={
            "Global.model_root_dir": str(model_dir),
            "Global.log_level": "warning",
        }
    )


def _to_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def _boxes_to_lines(boxes: Any, txts: Any, scores: Any) -> list[OcrLine]:
    lines: list[OcrLine] = []
    for box, text, score in zip(boxes, txts, scores, strict=False):
        if not text:
            continue
        points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
        lines.append(
            OcrLine(
                text=str(text),
                score=float(score),
                x0=float(points[:, 0].min()),
                y0=float(points[:, 1].min()),
                x1=float(points[:, 0].max()),
                y1=float(points[:, 1].max()),
            )
        )
    return lines


def _median(values: list[float], fallback: float) -> float:
    return float(np.median(values)) if values else fallback


def _cluster_rows(boxes: list[OcrLine]) -> list[list[OcrLine]]:
    """按 y 中心把检测框聚成行。

    阈值用**中位**字高而不是平均值：一个大标题就能把平均值带偏。
    """
    if not boxes:
        return []
    median_h = _median([b.height for b in boxes], 1.0)
    tolerance = max(1.0, median_h * _ROW_TOLERANCE)

    rows: list[list[OcrLine]] = []
    current: list[OcrLine] = []
    current_center = 0.0
    for box in sorted(boxes, key=lambda b: b.y_center):
        if current and abs(box.y_center - current_center) > tolerance:
            rows.append(current)
            current = []
        current.append(box)
        current_center = float(np.mean([b.y_center for b in current]))
    if current:
        rows.append(current)
    return rows


def _merge_row(row: list[OcrLine]) -> OcrLine:
    """把一行里的多个框按 x 排序拼成一行文字。

    中文之间不加空格；水平间隙超过一个字宽时补一个空格 —— 那通常是
    分栏或制表位，不补的话"姓名张三"会连成一团。
    """
    row = sorted(row, key=lambda b: b.x0)
    char_widths = [b.width / max(1, len(b.text)) for b in row]
    char_width = _median(char_widths, 10.0)

    parts: list[str] = []
    previous: OcrLine | None = None
    for box in row:
        if previous is not None and (box.x0 - previous.x1) > char_width:
            parts.append(" ")
        parts.append(box.text)
        previous = box
    return OcrLine(
        text="".join(parts),
        score=min(b.score for b in row),
        x0=min(b.x0 for b in row),
        y0=min(b.y0 for b in row),
        x1=max(b.x1 for b in row),
        y1=max(b.y1 for b in row),
    )


def _is_centered(line: OcrLine, page_width: int) -> bool:
    if page_width <= 0:
        return False
    narrow = line.width < page_width * _CENTERED_MAX_WIDTH_RATIO
    near_center = abs(line.x_center - page_width / 2.0) < page_width * _CENTERED_MAX_OFFSET_RATIO
    return narrow and near_center


def _median_height_excluding(heights: list[float], index: int) -> float:
    """除掉第 index 行之后的中位字高。

    判断"这行是不是标题"要和**别的行**比。整页只有两三行时，把自己也算进
    中位数会把基准抬高，大标题反而判不出来。
    """
    others = heights[:index] + heights[index + 1 :]
    return _median(others, heights[index] if heights else 1.0)


def _group_paragraphs(rows: list[OcrLine], page_width: int) -> list[OcrParagraph]:
    """行 → 段落。

    只看行间距不够用，再加三条排版常识：

    - **上一行明显没写到右边界** → 它是段末，下一行另起一段。
      （证明、合同里常见"甲方：张三 / 乙方：李四"这种等距独立短行，
      光看间距会被粘成一坨。）
    - **这一行明显往右缩进** → 首行缩进，另起一段
    - **两行字号差得多** → 不是同一个文本块（标题 vs 正文）

    右边界怎么估：正常情况用**实际最右**的那一行。但整页文字块本身就很窄
    （宽度不到页宽的 45%）时，没有任何一行能暴露真实版心 —— 这种情况下
    这些行本来就是独立短行而不是折行的段落，改用"按左边距镜像"的右边界，
    让每一行都判成段末。宁可切多也不要粘连：一行一段视觉上仍接近原件，
    粘成一坨就明显是错的。
    """
    if not rows:
        return []
    heights = [r.height for r in rows]
    median_h = _median(heights, 1.0)
    char_width = _median([r.width / max(1, len(r.text)) for r in rows], median_h)

    gaps = [rows[i].y0 - rows[i - 1].y1 for i in range(1, len(rows))]
    positive = [g for g in gaps if g > 0]
    # 用 25 分位而不是中位数估"正常行距"：段内行距占多数且偏小，
    # 中位数会被少数换段的大间距抬起来，导致该切的地方切不开。
    leading = float(np.percentile(positive, 25)) if positive else median_h * 0.4
    gap_threshold = max(median_h * 0.8, leading * _PARAGRAPH_GAP)

    text_left = float(np.percentile([r.x0 for r in rows], 10))
    observed_right = max(r.x1 for r in rows)
    narrow_block = (observed_right - text_left) < page_width * _MIN_BLOCK_WIDTH_RATIO
    text_right = (page_width - text_left) if narrow_block else observed_right
    short_line_slack = char_width * _SHORT_LINE_SLACK_CHARS
    indent_slack = char_width * _INDENT_SLACK_CHARS

    paragraphs: list[OcrParagraph] = []
    current = OcrParagraph(lines=[rows[0]])
    for index in range(1, len(rows)):
        previous, line = rows[index - 1], rows[index]
        taller, shorter = max(previous.height, line.height), min(previous.height, line.height)
        if (
            (line.y0 - previous.y1) > gap_threshold
            or previous.x1 < text_right - short_line_slack
            or line.x0 > text_left + indent_slack
            or (shorter > 0 and taller / shorter > _FONT_SIZE_JUMP)
        ):
            paragraphs.append(current)
            current = OcrParagraph(lines=[line])
        else:
            current.lines.append(line)
    paragraphs.append(current)

    index_of = {id(row): i for i, row in enumerate(rows)}
    for paragraph in paragraphs:
        first = paragraph.lines[0]
        paragraph.centered = len(paragraph.lines) == 1 and _is_centered(first, page_width)
        baseline = _median_height_excluding(heights, index_of[id(first)])
        paragraph.is_heading = (
            paragraph.centered and first.height > baseline * _HEADING_HEIGHT_RATIO
        )
    return paragraphs


def recognize(image: np.ndarray, preprocess: bool = True) -> OcrResult:
    """识别一张图。

    默认先过 enhance.prepare_for_ocr（裁正 + 去阴影 + 灰度）：文字行变水平让
    检测框更准，暗部文字不被吞掉。**不做二值化** —— 过度二值化吃掉笔画细节，
    反而降低识别率。
    """
    prepared = enhance_mod.prepare_for_ocr(image) if preprocess else image
    height, width = prepared.shape[:2]

    engine = get_engine()
    try:
        raw = engine(_to_bgr(prepared))
    except Exception as exc:
        logger.exception("OCR 失败")
        raise ShopPrintError(ErrorKind.OCR_EMPTY, f"识别出错：{exc}") from exc

    boxes = getattr(raw, "boxes", None)
    txts = getattr(raw, "txts", None)
    scores = getattr(raw, "scores", None)
    if boxes is None or txts is None or not len(txts):
        return OcrResult(page_width=width, page_height=height)

    detections = _boxes_to_lines(boxes, txts, scores if scores is not None else [1.0] * len(txts))
    rows = [_merge_row(row) for row in _cluster_rows(detections)]
    rows.sort(key=lambda r: r.y0)
    return OcrResult(
        paragraphs=_group_paragraphs(rows, width),
        lines=rows,
        page_width=width,
        page_height=height,
    )


def recognize_file(path: str | Path, preprocess: bool = True) -> OcrResult:
    return recognize(enhance_mod.load_image(path), preprocess=preprocess)


def _set_cjk_font(run: Any, name: str = "宋体") -> None:
    """python-docx 设中文字体必须同时写 w:eastAsia，只设 name 中文会退回默认字体。"""
    from docx.oxml.ns import qn

    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)  # noqa: SLF001 —— python-docx 官方写法


def to_docx(result: OcrResult, out_path: str | Path) -> Path:
    """生成可编辑的 Word。**A4 纸**、正文宋体小四、行距 1.5、首行缩进 2 字符。

    页面尺寸必须显式设成 A4：python-docx 自带的模板是美国 Letter
    （215.9×279.4mm），店里只有 A4/A3 纸，拿 Letter 的文档去打，Word 会
    自己缩放或者把版面挪位 —— 和 PDF 那条路出来的尺寸也就不一致了。
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    for section in document.sections:
        section.page_width = Mm(A4_WIDTH_MM)
        section.page_height = Mm(A4_HEIGHT_MM)
        section.top_margin = section.bottom_margin = Pt(72)
        section.left_margin = section.right_margin = Pt(80)

    for paragraph in result.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        block = document.add_paragraph()
        fmt = block.paragraph_format
        fmt.line_spacing = 1.5
        fmt.space_after = Pt(0)

        run = block.add_run(text)
        if paragraph.is_heading:
            fmt.alignment = WD_ALIGN_PARAGRAPH.CENTER
            fmt.space_before = Pt(6)
            fmt.space_after = Pt(12)
            run.bold = True
            run.font.size = Pt(16)
        elif paragraph.centered:
            fmt.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.font.size = Pt(12)
        else:
            fmt.first_line_indent = Pt(24)  # 12pt 的两个字
            run.font.size = Pt(12)
        _set_cjk_font(run)

    document.save(out_path)
    return out_path


def to_txt(result: OcrResult, out_path: str | Path) -> Path:
    """另存一份纯文本，方便长辈直接复制粘贴。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.text, encoding="utf-8")
    return out_path


def image_to_document(src: str | Path, out_dir: Path | None = None) -> tuple[Path, Path, OcrResult]:
    """一步到底：图片 → (docx, txt, 识别结果)。"""
    src = Path(src)
    result = recognize_file(src)
    if result.is_empty:
        raise ShopPrintError(ErrorKind.OCR_EMPTY, f"{src.name} 没识别出文字")
    target = out_dir or paths.output_dir()
    target.mkdir(parents=True, exist_ok=True)
    docx_path = to_docx(result, target / f"{src.stem}.docx")
    txt_path = to_txt(result, target / f"{src.stem}.txt")
    return docx_path, txt_path, result
