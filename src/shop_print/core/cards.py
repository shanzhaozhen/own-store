"""证件二合一：把身份证正反面、户口本两页这类小件拍照后拼到一张纸上。

复印店最常见的活之一。要求和普通打印不一样，**尺寸必须是实物大小**：
派出所、银行、学校要的是 1:1 的复印件，缩了放了都可能被退回来重做。

难点在于"照片里只有像素，没有毫米"。恢复真实尺寸只有三条路：

1. 用户明确选了证件类型 → 查预设表拿毫米数（最可靠，界面上默认走这条）
2. 扫描件带 DPI 元信息 → 像素 ÷ DPI = 英寸（扫描仪出的图才有，手机拍的没有）
3. 自动认：把卡片从照片里抠出来，用**长宽比**去匹配预设表

所以流程是：抠出卡片 → 透视校正 → 去底增强 → 定尺寸 → 按毫米摆到纸上。
认不出来时不硬猜，界面上明确说"尺寸可能不是实际大小"。

排版会自己在纵向/横向纸、上下/左右排之间挑一个**能按实物尺寸放下**的方案；
实在放不下才缩，并且把缩放比例报给界面（不能悄悄缩）。

尺寸依据和现场校准办法见 docs/10-证件二合一.md。
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from .. import paths
from ..texts import ErrorKind
from . import enhance as enhance_mod
from .errors import ShopPrintError, broken

logger = logging.getLogger(__name__)

PT_PER_MM = 72.0 / 25.4

AUTO = "auto"  # 自动认证件类型


@dataclass(frozen=True)
class CardSpec:
    """一种证件的实物尺寸。"""

    key: str
    name: str
    width_mm: float  # 横放时的宽
    height_mm: float  # 横放时的高
    front_label: str = "第一张"
    back_label: str = "第二张"
    verified: bool = True  # 尺寸是否已核实。没核实的要在界面和文档里说清楚
    source: str = ""  # 尺寸依据

    @property
    def ratio(self) -> float:
        """长边 / 短边。用来和照片里量出来的比例对齐。"""
        return max(self.width_mm, self.height_mm) / min(self.width_mm, self.height_mm)


# 预设表。**只有标了 verified 的才是有据可查的国际/国家标准尺寸**，
# 其余是常见值，交付前要拿真件用尺子量一次（见 docs/10）。
PRESETS: tuple[CardSpec, ...] = (
    CardSpec(
        "id",
        "身份证",
        85.6,
        54.0,
        front_label="人像面",
        back_label="国徽面",
        source="ISO/IEC 7810 ID-1，身份证/银行卡/社保卡都是这个尺寸",
    ),
    CardSpec(
        "bank",
        "银行卡 / 社保卡",
        85.6,
        54.0,
        front_label="正面",
        back_label="反面",
        source="ISO/IEC 7810 ID-1",
    ),
    CardSpec(
        "household",
        "户口本",
        105.0,
        148.0,
        front_label="户主页",
        back_label="本人页",
        verified=False,
        source="暂按 A6（105×148），交付前要用真件量一次",
    ),
    CardSpec(
        "driver",
        "驾驶证 / 行驶证",
        88.0,
        60.0,
        front_label="主页",
        back_label="副页",
        verified=False,
        source="常见值 88×60，交付前要用真件量一次",
    ),
    CardSpec(
        "passport",
        "护照",
        125.0,
        88.0,
        front_label="资料页",
        back_label="签注页",
        source="ISO/IEC 7810 ID-3",
    ),
)

# 长宽比匹配容差。卡片抠出来总有一两个百分点的误差，8% 能区分开
# 身份证(1.585)、驾驶证(1.467)、护照(1.420)、A6(1.410) —— 后两者太近，
# 所以自动认只在明显匹配时才下结论，拿不准就让用户自己选。
_RATIO_TOLERANCE = 0.06
# 卡片在照片里至少要占这么大面积才认。比整页文档那条(25%)松得多
_MIN_CARD_AREA_RATIO = 0.06
# 卡片的长宽比一定落在这个区间，用来排除"把整张桌子当成卡片"
_CARD_RATIO_RANGE = (1.15, 2.10)
# 打印机吃边，四周留出安全边
_MARGIN_MM = 6.0
# 两张之间的间隔，留一点好剪
_GAP_MM = 10.0


def spec_by_key(key: str) -> CardSpec | None:
    for preset in PRESETS:
        if preset.key == key:
            return preset
    return None


def labels_for(key: str) -> tuple[str, str]:
    """界面上两个位置该叫什么。户口本是"户主页/本人页"，身份证是"人像面/国徽面"。"""
    preset = spec_by_key(key)
    if preset is None:
        return ("第一张", "第二张")
    return (preset.front_label, preset.back_label)


def identify_spec(
    width_px: int, height_px: int, dpi: float | None = None
) -> tuple[CardSpec | None, str]:
    """按长宽比猜证件类型。返回 (预设, 给人看的说明)。

    **拿不准就不猜。**护照（125×88，比例 1.42）和户口本（105×148，比例 1.41）的
    长宽比几乎一样，认错了尺寸要差 16% —— 顾客拿去办事被退回来的代价，
    远大于让长辈自己点一下类型。所以要求最优解至少比"尺寸不同的次优解"近一倍。

    带扫描 DPI 时用物理尺寸再验一次：两条证据都指向同一个预设才敢下结论。
    """
    if width_px <= 0 or height_px <= 0:
        return None, "图片尺寸不对"
    ratio = max(width_px, height_px) / min(width_px, height_px)

    def deviation(preset: CardSpec) -> float:
        return abs(preset.ratio - ratio) / preset.ratio

    within = sorted((p for p in PRESETS if deviation(p) <= _RATIO_TOLERANCE), key=deviation)
    if not within:
        return None, f"认不出是什么证件（长宽比 {ratio:.2f}），请自己选类型"

    best = within[0]
    别的尺寸 = [
        p for p in within[1:] if (p.width_mm, p.height_mm) != (best.width_mm, best.height_mm)
    ]
    if 别的尺寸 and deviation(别的尺寸[0]) < deviation(best) * 2:
        return None, f"长宽比像{best.name}也像{别的尺寸[0].name}，分不清，请自己选类型"

    说明 = f"认出是{best.name}"
    if not best.verified:
        说明 += "（尺寸按常见值，对不上请告诉开发者）"
    if dpi and dpi > 1:
        long_mm = max(width_px, height_px) / dpi * 25.4
        expect = max(best.width_mm, best.height_mm)
        if abs(long_mm - expect) / expect > 0.15:
            return best, f"按长宽比像{best.name}，但扫描尺寸对不上（量出来 {long_mm:.0f}mm）"
    return best, 说明


def physical_size(preset: CardSpec, portrait: bool) -> tuple[float, float]:
    """预设尺寸按图片的朝向摆正。竖着拍的身份证是 54 宽 × 85.6 高。"""
    long_side = max(preset.width_mm, preset.height_mm)
    short_side = min(preset.width_mm, preset.height_mm)
    return (short_side, long_side) if portrait else (long_side, short_side)


def _ratio_matches(width_px: int, height_px: int, preset: CardSpec) -> bool:
    """图片比例和这个证件是不是差不多。差得多说明类型选错了。

    用和自动识别同一个容差：既然比例偏这么多我们都不敢认成这个证件，
    那用户手选成这个证件时也该提醒一句。
    """
    if min(width_px, height_px) <= 0:
        return False
    ratio = max(width_px, height_px) / min(width_px, height_px)
    return abs(ratio - preset.ratio) / preset.ratio <= _RATIO_TOLERANCE


@dataclass
class CardItem:
    """一张准备好的证件图：已裁正、已去底，并且知道自己该是多大。"""

    image: np.ndarray  # 单通道灰度
    width_mm: float
    height_mm: float
    label: str = ""
    spec_key: str = ""
    cropped: bool = False  # 有没有从照片里抠出来
    exact_size: bool = True  # 尺寸是不是可靠的实物尺寸
    note: str = ""


def crop_card(image: np.ndarray) -> tuple[np.ndarray, bool]:
    """把卡片从照片里抠出来并拉正。抠不到就原样返回。

    判据比整页文档松（卡片占的面积小），但多了一条长宽比检查 ——
    卡片的长宽比是已知的，靠它能挡掉"把桌面边缘当成卡片"这类误检。
    """
    quad = enhance_mod.detect_page_quad(
        image, min_area_ratio=_MIN_CARD_AREA_RATIO, ratio_range=_CARD_RATIO_RANGE
    )
    if quad is None:
        return image, False
    return enhance_mod.warp_to_quad(image, quad), True


def _image_dpi(path: Path) -> float | None:
    """扫描件常带 DPI，手机拍的一般没有（或者是 72 这种没意义的值）。"""
    try:
        with Image.open(path) as probe:
            dpi = probe.info.get("dpi")
    except OSError:
        return None
    if not dpi or not dpi[0] or float(dpi[0]) <= 72.0:
        return None
    return float(dpi[0])


def prepare_card(
    source: str | Path | np.ndarray,
    spec_key: str = AUTO,
    options: enhance_mod.EnhanceOptions | None = None,
    label: str = "",
) -> CardItem:
    """一张照片 → 可以摆到纸上的 CardItem。

    去底用「图文混排」而不是「文字为主」：证件上有照片、印章、底纹，
    二值化会把人像糊成一团黑。
    """
    dpi: float | None = None
    if isinstance(source, np.ndarray):
        image = source
    else:
        path = Path(source)
        if not path.exists():
            raise broken(path.name, "文件不存在")
        image = enhance_mod.load_image(path)
        dpi = _image_dpi(path)

    cropped_image, cropped = crop_card(image)
    opts = options or enhance_mod.EnhanceOptions(mode=enhance_mod.MODE_MIXED, strength=45)
    # deskew=False：上面已经透视校正过了，再来一次容易把好图转歪
    result = enhance_mod.enhance(
        cropped_image,
        enhance_mod.EnhanceOptions(
            mode=opts.mode if opts.mode != enhance_mod.MODE_AUTO else enhance_mod.MODE_MIXED,
            strength=opts.strength,
            deskew=False,
        ),
    )
    height_px, width_px = result.image.shape[:2]
    portrait = height_px > width_px

    preset = spec_by_key(spec_key) if spec_key != AUTO else None
    note = ""
    if preset is None:
        preset, note = identify_spec(width_px, height_px, dpi)
    elif not _ratio_matches(width_px, height_px, preset):
        # 用户明确选了类型就按他选的尺寸出，但要提醒一句 —— 比例差这么多
        # 通常是选错了类型（比如拿整页文档当身份证），拉伸出来一眼就能看出不对
        note = f"这张图的比例和{preset.name}差得多，是不是选错类型了？"
    if preset is not None:
        width_mm, height_mm = physical_size(preset, portrait)
        return CardItem(
            image=result.image,
            width_mm=width_mm,
            height_mm=height_mm,
            label=label,
            spec_key=preset.key,
            cropped=cropped,
            exact_size=True,
            note=note,
        )

    # 认不出来：按扫描 DPI 算，没有 DPI 就按纸宽的一半摆，并明确告诉用户尺寸不保真
    if dpi:
        return CardItem(
            image=result.image,
            width_mm=width_px / dpi * 25.4,
            height_mm=height_px / dpi * 25.4,
            label=label,
            cropped=cropped,
            exact_size=True,
            note=f"按扫描分辨率 {dpi:.0f}dpi 还原尺寸",
        )
    fallback_w = 90.0
    return CardItem(
        image=result.image,
        width_mm=fallback_w,
        height_mm=fallback_w * height_px / max(width_px, 1),
        label=label,
        cropped=cropped,
        exact_size=False,
        note=note or "认不出证件类型，尺寸可能不是实际大小",
    )


# ── 排版 ────────────────────────────────────────────────────────
@dataclass
class Placement:
    item: CardItem
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float


@dataclass
class Layout:
    paper: str = "A4"
    landscape: bool = False
    scale: float = 1.0  # 1.0 = 实物大小；小于 1 说明放不下缩过了
    placements: list[Placement] = field(default_factory=list)

    @property
    def exact_size(self) -> bool:
        """是不是实物大小 —— 界面上必须如实显示，不能悄悄缩了还说是原大。"""
        return self.scale > 0.999 and all(p.item.exact_size for p in self.placements)

    @property
    def page_size_mm(self) -> tuple[float, float]:
        return _paper_mm(self.paper, self.landscape)


def _paper_mm(paper: str, landscape: bool) -> tuple[float, float]:
    from . import convert

    width_pt, height_pt = convert.PAPER_SIZES.get(paper, convert.PAPER_SIZES["A4"])
    width, height = width_pt / PT_PER_MM, height_pt / PT_PER_MM
    return (height, width) if landscape else (width, height)


def _arrangements(count: int) -> list[list[list[int]]]:
    """可选的排法，按优先级。元素是"每行放哪几个（下标）"。"""
    order = list(range(count))
    options: list[list[list[int]]] = [[[i] for i in order]]  # 一列，上下排
    if count > 1:
        options.append([order])  # 一行，左右排
    if count > 2:
        options.append([order[i : i + 2] for i in range(0, count, 2)])  # 两列的格子
    return options


def _measure(items: list[CardItem], rows: list[list[int]], gap: float) -> tuple[float, float]:
    total_h = 0.0
    total_w = 0.0
    for index, row in enumerate(rows):
        row_w = sum(items[i].width_mm for i in row) + gap * (len(row) - 1)
        row_h = max(items[i].height_mm for i in row)
        total_w = max(total_w, row_w)
        total_h += row_h + (gap if index else 0.0)
    return total_w, total_h


def plan_layout(
    items: list[CardItem],
    paper: str = "A4",
    gap_mm: float = _GAP_MM,
    margin_mm: float = _MARGIN_MM,
) -> Layout:
    """挑一个能按实物尺寸放下的排法。

    候选顺序：纵向纸上下排 → 纵向纸左右排 → 横向纸…… 只要有一个能 1:1 放下就用它。
    全都放不下才缩，并把 scale 记下来让界面显示「已缩小到 xx%」——
    **绝不能悄悄缩**：顾客拿去办事的复印件，尺寸不对可能被退回来。

    户口本就是靠这条逻辑：两页 A6 竖着并排要 220mm，超过 A4 的可打印宽度；
    换成横向 A4 并排就放得下，而且两页都还是正着看的。
    """
    if not items:
        raise ShopPrintError(ErrorKind.FILE_BROKEN, "没有要合并的图片")

    best: Layout | None = None
    for landscape in (False, True):
        page_w, page_h = _paper_mm(paper, landscape)
        usable_w, usable_h = page_w - 2 * margin_mm, page_h - 2 * margin_mm
        for rows in _arrangements(len(items)):
            need_w, need_h = _measure(items, rows, gap_mm)
            if need_w <= 0 or need_h <= 0:
                continue
            scale = min(1.0, usable_w / need_w, usable_h / need_h)
            layout = _place(items, rows, gap_mm, paper, landscape, scale)
            # 候选按优先级遍历，同分时保留先来的（纵向纸、上下排最常用）
            if best is None or _better(layout, best):
                best = layout
    if best is None:  # pragma: no cover —— items 非空时必然有候选
        raise ShopPrintError(ErrorKind.UNKNOWN, "排不出版面")
    return best


def _better(candidate: Layout, current: Layout) -> bool:
    """先看能不能 1:1（缩得越少越好），同样能放下时优先纵向纸。"""
    if round(candidate.scale, 4) != round(current.scale, 4):
        return candidate.scale > current.scale
    return current.landscape and not candidate.landscape


def _place(
    items: list[CardItem],
    rows: list[list[int]],
    gap: float,
    paper: str,
    landscape: bool,
    scale: float,
) -> Layout:
    page_w, page_h = _paper_mm(paper, landscape)
    _, total_h = _measure(items, rows, gap)
    origin_y = (page_h - total_h * scale) / 2.0

    placements: list[Placement] = []
    y = origin_y
    for index, row in enumerate(rows):
        row_w = sum(items[i].width_mm for i in row) + gap * (len(row) - 1)
        row_h = max(items[i].height_mm for i in row)
        x = (page_w - row_w * scale) / 2.0  # 每一行各自居中
        if index:
            y += gap * scale
        for i in row:
            item = items[i]
            width, height = item.width_mm * scale, item.height_mm * scale
            placements.append(
                Placement(
                    item=item,
                    x_mm=x,
                    y_mm=y + (row_h * scale - height) / 2.0,  # 行内竖向居中
                    width_mm=width,
                    height_mm=height,
                )
            )
            x += width + gap * scale
        y += row_h * scale
    return Layout(paper=paper, landscape=landscape, scale=scale, placements=placements)


def _encode_png(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array, mode="L").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_pdf(layout: Layout, out_path: str | Path) -> Path:
    """把排好的版渲染成 PDF。位置和大小直接用毫米换算成点，保证打出来是实物尺寸。

    `keep_proportion=False` 是刻意的：PyMuPDF 默认会**保持图片长宽比**，
    图片比例和证件比例差一点点（抠图总有一两个百分点误差）就会把框缩小，
    实测 90×60 的图放进 85.6×54 的框会缩成 81mm —— 尺寸保证就没了。
    抠出来的框就是证件本身，把它映射到证件的真实矩形正是要做的校正。
    比例差得多的时候 `prepare_card` 会提示"是不是选错类型了"，由人来判断。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w_mm, page_h_mm = layout.page_size_mm

    document = pymupdf.open()
    try:
        page = document.new_page(width=page_w_mm * PT_PER_MM, height=page_h_mm * PT_PER_MM)
        for placement in layout.placements:
            rect = pymupdf.Rect(
                placement.x_mm * PT_PER_MM,
                placement.y_mm * PT_PER_MM,
                (placement.x_mm + placement.width_mm) * PT_PER_MM,
                (placement.y_mm + placement.height_mm) * PT_PER_MM,
            )
            page.insert_image(rect, stream=_encode_png(placement.item.image), keep_proportion=False)
        document.save(out_path, deflate=True, garbage=3)
    finally:
        document.close()
    return out_path


def merge_to_pdf(
    items: list[CardItem],
    out_path: str | Path | None = None,
    paper: str = "A4",
    gap_mm: float = _GAP_MM,
) -> tuple[Path, Layout]:
    """证件图 → 一张纸的 PDF。返回 (PDF 路径, 排版结果)。"""
    layout = plan_layout(items, paper=paper, gap_mm=gap_mm)
    if out_path is None:
        target = paths.cache_dir()
        target.mkdir(parents=True, exist_ok=True)
        out_path = target / f"证件-{time.strftime('%Y%m%d-%H%M%S')}.pdf"
    path = render_pdf(layout, out_path)
    logger.info(
        "证件合并完成：%s（%s%s，%.0f%%）",
        path.name,
        layout.paper,
        "横向" if layout.landscape else "纵向",
        layout.scale * 100,
    )
    return path, layout


def describe(layout: Layout) -> str:
    """给界面用的一句人话。"""
    纸 = f"{layout.paper}{'横放' if layout.landscape else ''}"
    if layout.exact_size:
        return f"已按实际大小拼到一张{纸}上"
    if layout.scale < 0.999:
        return f"证件比纸大，已缩小到 {layout.scale * 100:.0f}% 拼到一张{纸}上"
    return f"已拼到一张{纸}上（认不出证件类型，尺寸可能不是实际大小）"
