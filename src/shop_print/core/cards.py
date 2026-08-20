"""证件二合一：把身份证正反面、户口本两页这类小件拍照后拼到一张纸上。

复印店最常见的活之一。要求和普通打印不一样，**尺寸必须是实物大小**：
派出所、银行、学校要的是 1:1 的复印件，缩了放了都可能被退回来重做。

难点在于"照片里只有像素，没有毫米"。恢复真实尺寸只有三条路：

1. 用户明确选了证件类型 → 查预设表拿毫米数（最可靠，界面上默认走这条）
2. 扫描件带 DPI 元信息 → 像素 ÷ DPI = 英寸（扫描仪出的图才有，手机拍的没有）
3. 自动认：把卡片从照片里抠出来，用**长宽比**去匹配预设表

所以流程是：抠出卡片 → 透视校正 → 温和增强 → 卡片外面涂白 → 摆正 → 定尺寸 →
按毫米摆到纸上。认不出来时不硬猜，界面上明确说"尺寸可能不是实际大小"。

这条流程**切成两半**（`analyze_card` / `render_card`）：前一半贵（抠图、认类型、
OCR 判上下，1–2 秒），后一半便宜（增强、涂白，几十毫秒）。界面上拖「淡 ←→ 浓」
滑块时只重跑后一半 —— 顺带让朝向稳定，不会因为深浅变了就自己翻个面。

裁的时候会**刻意往外放一圈**（证件有一圈边、四角是圆的，贴着边裁就切掉了），
所以定尺寸算的是"整张图该印多大"，让卡片本体正好落在实物尺寸上 ——
把白边一起当成卡片会小印 3.5%（实测 85.6mm 印出来只有 82.6mm）。

排版会自己在纵向/横向纸、上下/左右排之间挑一个**能按实物尺寸放下**的方案；
实在放不下才缩，并且把缩放比例报给界面（不能悄悄缩）。

尺寸依据和现场校准办法见 docs/10-证件二合一.md。
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field, replace
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
    corner_mm: float = 0.0  # 圆角半径。卡式证件是圆角的，纸质页是方角
    verified: bool = True  # 尺寸是否已核实。没核实的要在界面和文档里说清楚
    source: str = ""  # 尺寸依据

    @property
    def ratio(self) -> float:
        """长边 / 短边。用来和照片里量出来的比例对齐。"""
        return max(self.width_mm, self.height_mm) / min(self.width_mm, self.height_mm)

    @property
    def corner_ratio(self) -> float:
        """圆角半径 / 短边。涂白时按这个比例画圆角，和像素大小无关。"""
        return self.corner_mm / min(self.width_mm, self.height_mm)


# 预设表。`verified` 记的是**尺寸的出处**：True = 有国际标准可查，
# False = 常见值。两者都会被当成实物尺寸原样写进 PDF；
# 验收标准是"PDF/Word 里声明的物理尺寸和这张表一致"（tests/test_physical_size.py 盯着），
# 实物万一对不上，改这里一个数就行，见 docs/10。
PRESETS: tuple[CardSpec, ...] = (
    CardSpec(
        "id",
        "身份证",
        85.6,
        54.0,
        front_label="人像面",
        back_label="国徽面",
        corner_mm=3.18,
        source="ISO/IEC 7810 ID-1，身份证/银行卡/社保卡都是这个尺寸",
    ),
    CardSpec(
        "bank",
        "银行卡 / 社保卡",
        85.6,
        54.0,
        front_label="正面",
        back_label="反面",
        corner_mm=3.18,
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
        source="按 A6（105×148）",
    ),
    CardSpec(
        "driver",
        "驾驶证 / 行驶证",
        88.0,
        60.0,
        front_label="主页",
        back_label="副页",
        corner_mm=3.0,
        verified=False,
        source="常见值 88×60",
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

# 长宽比匹配容差。卡片抠出来总有一两个百分点的误差，6% 能区分开
# 身份证(1.585)、驾驶证(1.467)、护照(1.420)、A6(1.410) —— 后两者太近，
# 所以自动认只在明显匹配时才下结论，拿不准就让用户自己选。
_RATIO_TOLERANCE = 0.06
# 卡片在照片里至少要占这么大面积才认；占得太满说明框到的是桌面本身
_MIN_CARD_AREA_RATIO = 0.06
_MAX_CARD_AREA_RATIO = 0.95
# 凸包面积 / 外接矩形面积。真实证件实测 0.94–1.00；把卡片和桌面连成一片的
# 误检只有 0.88–0.90，卡在 0.92 刚好分开（数据见 docs/10）
_MIN_FILL_RATIO = 0.92
# 卡片的长宽比一定落在这个区间，用来排除"把整张桌子当成卡片"
_CARD_RATIO_RANGE = (1.15, 2.10)
# 往外放这么多再裁：证件有一圈边、四角是圆的，贴着边裁会把这些切掉。
# 这个值只要"够大"就行，不用准 —— 裁完会再量一次卡片边缘的确切位置（_measure_inset），
# 涂白和定尺寸都以量到的为准
_EDGE_MARGIN_RATIO = 0.015
# 量卡片边缘时最多往里找这么远（占对应边长的比例）。再往里就不像"外扩的那一圈"了
_INSET_SEARCH_RATIO = 0.05
# 桌面和卡片的亮度至少差这么多才敢量边缘，差太小说明背景本来就是白的
_MIN_DESK_CONTRAST = 25.0
# 按预设长宽比校正卡片矩形时最多收这么多。超过说明是别的问题，宁可不校正
_MAX_RATIO_FIX = 0.03
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
        说明 += f"（按 {best.width_mm:.0f}×{best.height_mm:.0f} 毫米算）"
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
    """一张准备好的证件图：已裁正、已去底，并且知道自己该是多大。

    `width_mm/height_mm` 是**整张图**该印多大。图里比卡片多一圈白边
    （裁的时候刻意往外放的，为了不切掉证件的边和圆角），所以它比证件本身大一点点；
    卡片本体该印多大看 `card_size_mm` —— 顾客拿尺子量的是那个。
    """

    image: np.ndarray  # 黑白时单通道灰度，彩色时 BGR 三通道
    width_mm: float
    height_mm: float
    label: str = ""
    spec_key: str = ""
    cropped: bool = False  # 有没有从照片里抠出来
    exact_size: bool = True  # 尺寸是不是可靠的实物尺寸
    note: str = ""
    body_fraction: tuple[float, float] = (1.0, 1.0)  # 卡片本体占整张图的比例（宽, 高）

    @property
    def card_size_mm(self) -> tuple[float, float]:
        """卡片本体印出来是多少毫米 —— 验收就看这个数。"""
        return (self.width_mm * self.body_fraction[0], self.height_mm * self.body_fraction[1])


def _card_mask(small: np.ndarray) -> np.ndarray:
    """在缩略图上抠出"卡片在哪"。返回 0/1 掩膜。

    判据是**又亮又不鲜艳**：证件是浅色低饱和的，木桌/布面是暖色高饱和的。
    实测（真实身份证照片，见 docs/10）：卡片中心 S≈32、桌面 S≈68，分得很开；
    只按亮度做 Otsu 会把过曝的桌面一起圈进来（实测把长宽比从 1.585 带偏到 1.49）。

    输入本来就是灰的（扫描件、黑白照）时没有饱和度可用，退回只看亮度。
    """
    import cv2

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    _, 亮 = cv2.threshold(
        cv2.GaussianBlur(gray, (0, 0), 2), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )
    saturation = hsv[..., 1]
    if float(saturation.std()) < 6.0:  # 灰图，没有颜色信息
        mask = 亮
    else:
        _, 淡 = cv2.threshold(
            cv2.GaussianBlur(saturation, (0, 0), 2),
            0,
            255,
            cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
        )
        mask = cv2.bitwise_and(亮, 淡)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # 补掉证件上的深色字
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)  # 去掉桌面上的零碎亮点
    return (mask > 0).astype(np.uint8)


@dataclass
class CardRect:
    """照片里卡片的位置。box 是原图坐标下的四个角（已按左上→右上→右下→左下排好）。

    box 已经按 `_EDGE_MARGIN_RATIO` 往外放过一圈：证件有一圈边、四角是圆的，
    贴着边裁会把这些切掉。多出来的那一圈是桌面，裁完再量一次位置刷掉。
    """

    box: np.ndarray
    ratio: float  # 长边/短边
    fill: float  # 凸包面积 / 外接矩形面积，圆角矩形约 0.97
    angle: float  # 拍歪了多少度


def detect_card(image: np.ndarray) -> CardRect | None:
    """找卡片。返回 None 表示没找到（那就不裁，宁可不裁也不能裁坏）。

    用 `minAreaRect` 而不是 `approxPolyDP` 找四边形：**证件是圆角的**，
    多边形近似会把角切掉，出来的框比卡片小一圈 —— 用户反馈的"裁剪过强"就是这个。
    外接矩形天然包住圆角，再靠"填充率"判断这个框是不是真的贴着一张卡片。
    """
    import cv2

    small = enhance_mod.downscale(image, 900)
    scale = image.shape[1] / small.shape[1]
    mask = _card_mask(small)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 1:
        return None
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    component = (labels == index).astype(np.uint8)
    if component.sum() < mask.size * _MIN_CARD_AREA_RATIO:
        return None
    if component.sum() > mask.size * _MAX_CARD_AREA_RATIO:
        return None  # 几乎占满整张照片：大概是桌面本身，不是卡片

    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # 取**凸包**再量：卡片上有一道斜光时，阈值会从边上啃掉一块，轮廓本身的
    # 填充率会掉到 0.82，凸包能把这块凹陷补回来（实测 0.94–1.00）。
    # 而"把卡片和桌面连成一片"那种误检，凸包填充率只有 0.88–0.90，仍然拦得住。
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    (center_x, center_y), (width, height), angle = cv2.minAreaRect(hull)
    if min(width, height) < 20:
        return None
    fill = float(cv2.contourArea(hull) / (width * height))
    ratio = max(width, height) / min(width, height)
    if fill < _MIN_FILL_RATIO or not (_CARD_RATIO_RANGE[0] <= ratio <= _CARD_RATIO_RANGE[1]):
        return None

    # 往外放一点再裁：证件本身有一圈边，圆角也要留住 —— 顾客要的是"一张卡"的复印件
    grown = (width * (1 + 2 * _EDGE_MARGIN_RATIO), height * (1 + 2 * _EDGE_MARGIN_RATIO))
    box = cv2.boxPoints(((center_x, center_y), grown, angle)) * scale
    return CardRect(box=enhance_mod.order_quad(box), ratio=ratio, fill=fill, angle=float(angle))


@dataclass
class CardCrop:
    """裁好的证件图，外加"卡片本体在图里的哪一块"。

    `inset` 是卡片本体到图片四条边的像素数，顺序 (左, 上, 右, 下)。裁的时候刻意
    往外放了一圈，这一圈里是桌面 —— 涂白按它把桌面刷掉，定尺寸按"卡片本体占图多大"
    反算，这样卡片本体印出来才正好是实物尺寸。
    """

    image: np.ndarray
    inset: tuple[int, int, int, int] = (0, 0, 0, 0)
    cropped: bool = False

    @property
    def body_px(self) -> tuple[int, int]:
        """卡片本体的像素宽高。"""
        return _body_px(self.image.shape, self.inset)


def _body_px(shape: tuple[int, ...], inset: tuple[int, int, int, int]) -> tuple[int, int]:
    height, width = shape[:2]
    left, top, right, bottom = inset
    return (max(width - left - right, 1), max(height - top - bottom, 1))


def _measure_inset(
    image: np.ndarray, fallback: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """量卡片本体到裁出图四条边各差多少像素，顺序 (左, 上, 右, 下)。

    为什么要量而不是"按外扩比例反推"：检出的框和卡片总差零点几个百分点
    （实测四条边分别差 1.4%–3.2%，理论值 1.46%），差多的那一侧会留一条桌面暗边，
    打出来就是一条脏边；尺寸也会跟着偏。

    裁出来的图已经拉正了，四条边都是直的 —— 一维扫一下找"桌面→卡片"的亮度跳变
    就行，比在原图上凭掩膜猜稳。量不到的那条边退回 `fallback`（按外扩比例算的值）。
    """
    import cv2

    gray = cv2.GaussianBlur(enhance_mod._to_gray(image), (0, 0), 2)  # noqa: SLF001 —— 同包内部工具
    height, width = gray.shape[:2]
    if min(height, width) < 40:
        return fallback
    桌面 = float(np.median(np.r_[gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]]))
    卡片 = float(np.median(gray[height // 3 : height * 2 // 3, width // 3 : width * 2 // 3]))
    if 卡片 - 桌面 < _MIN_DESK_CONTRAST:
        return fallback  # 分不开桌面和卡片（背景本来就白、或者根本没裁到桌面）
    阈值 = (桌面 + 卡片) / 2.0

    def 一条边(取线, 沿着: int, 上限: int) -> int:
        """沿一条边取若干条扫描线，各自找第一处跳变，取中位数（挡得住反光）。"""
        位置: list[int] = []
        for p in range(int(沿着 * 0.2), int(沿着 * 0.8), max(1, 沿着 // 40)):
            过 = 取线(p)[: 上限 + 5] > 阈值
            for i in range(len(过) - 4):
                if 过[i : i + 5].all():
                    位置.append(i)
                    break
        return int(np.median(位置)) if 位置 else -1

    横向上限 = round(width * _INSET_SEARCH_RATIO)
    竖向上限 = round(height * _INSET_SEARCH_RATIO)
    量到 = (
        一条边(lambda p: gray[p, :], height, 横向上限),  # 左
        一条边(lambda p: gray[:, p], width, 竖向上限),  # 上
        一条边(lambda p: gray[p, ::-1], height, 横向上限),  # 右
        一条边(lambda p: gray[::-1, p], width, 竖向上限),  # 下
    )
    return tuple(fallback[i] if v < 0 else v for i, v in enumerate(量到))  # type: ignore[return-value]


def _fit_inset_to_ratio(
    inset: tuple[int, int, int, int], shape: tuple[int, ...], ratio: float
) -> tuple[int, int, int, int]:
    """把量出来的卡片矩形收到预设的长宽比上。

    四条边里总有一条会偏几个像素（实测左边偏 8px），比例就跟着差 1%–2%，
    尺寸和涂白都会带上这个误差。既然已经知道这张证件的真实长宽比，就用它把矩形收准。
    **只收不放** —— 放大会把桌面重新框进来；收得太多（>3%）说明是量歪了以外的问题，那就不动。
    """
    height, width = shape[:2]
    left, top, right, bottom = inset
    body_w, body_h = _body_px(shape, inset)
    if ratio <= 0:
        return inset
    if body_w >= body_h:  # 横放：宽 = 高 × 比例
        target_w, target_h = body_h * ratio, float(body_h)
        if target_w > body_w:  # 宽不够长，那就改成把高收窄
            target_w, target_h = float(body_w), body_w / ratio
    else:
        target_w, target_h = float(body_w), body_w * ratio
        if target_h > body_h:
            target_w, target_h = body_h / ratio, float(body_h)
    收宽, 收高 = body_w - target_w, body_h - target_h
    if 收宽 > width * _MAX_RATIO_FIX or 收高 > height * _MAX_RATIO_FIX:
        return inset
    return (
        left + round(收宽 / 2),
        top + round(收高 / 2),
        right + round(收宽 / 2),
        bottom + round(收高 / 2),
    )


def _paint_outside(
    gray: np.ndarray, inset: tuple[int, int, int, int], corner_ratio: float
) -> np.ndarray:
    """卡片矩形外面涂白，四角按证件的圆角切。灰度和彩色都吃。

    不用阈值掩膜涂：阈值边界是波浪形的（反光、木纹都会让它抖），照它涂白，
    卡片四周会留一圈毛边和黑点（真实照片上看得很清楚）。裁出来的图已经拉正，
    卡片就是一个正矩形 —— 直接按矩形涂，边是直的；角按证件自己的圆角半径画
    （身份证是 ISO 7810 的 3.18mm），出来就是"一张卡"的样子。
    """
    import cv2

    height, width = gray.shape[:2]
    left, top, right, bottom = inset
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(width - 1 - right, width - 1), min(height - 1 - bottom, height - 1)
    if x1 <= x0 or y1 <= y0:
        return gray
    radius = round(min(x1 - x0, y1 - y0) * max(corner_ratio, 0.0))
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(mask, (x0 + radius, y0), (x1 - radius, y1), 255, thickness=-1)
    if radius > 0:
        cv2.rectangle(mask, (x0, y0 + radius), (x1, y1 - radius), 255, thickness=-1)
        for cx, cy in (
            (x0 + radius, y0 + radius),
            (x1 - radius, y0 + radius),
            (x0 + radius, y1 - radius),
            (x1 - radius, y1 - radius),
        ):
            cv2.circle(mask, (cx, cy), radius, 255, thickness=-1)
    # 边界糊一点点再混色：硬切的圆角会有锯齿，打出来是一圈台阶
    软 = cv2.GaussianBlur(mask, (0, 0), 1.2).astype(np.float32) / 255.0
    if gray.ndim == 3:
        软 = 软[..., None]
    return np.clip(gray.astype(np.float32) * 软 + 255.0 * (1.0 - 软), 0, 255).astype(np.uint8)


def crop_card(image: np.ndarray) -> CardCrop:
    """把卡片抠出来并拉正，同时量出卡片本体在裁出图里的位置。

    没找到卡片就原样返回（`cropped=False`）—— 宁可不裁也不能裁坏。
    """
    import cv2

    found = detect_card(image)
    if found is None:
        return CardCrop(image=image)
    matrix, size = enhance_mod.quad_transform(found.box)
    cropped = cv2.warpPerspective(
        image, matrix, size, flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255)
    )
    margin = _EDGE_MARGIN_RATIO / (1 + 2 * _EDGE_MARGIN_RATIO)  # 外扩的一圈占裁出图的比例
    按外扩算 = (
        round(size[0] * margin),
        round(size[1] * margin),
        round(size[0] * margin),
        round(size[1] * margin),
    )
    return CardCrop(image=cropped, inset=_measure_inset(cropped, 按外扩算), cropped=True)


def _enhance_card(image: np.ndarray, strength: int, color: bool = False) -> np.ndarray:
    """证件专用的增强。`strength` 就是界面上那个「淡 ←→ 浓」滑块。

    黑白时返回单通道，`color=True` 时返回 BGR —— 彩色是给「另存为 PDF」用的
    （店里打印机只有黑白，打印时驱动自己会转灰），红色国徽、蓝色签章要留住。

    **不走「图文混排」那条去底链路。**证件是小卡片、光照本来就均匀，
    `flatten_illumination` 的除法在这里只会把高光推到纯白 —— 用户的原话是
    "处理得有点过曝"，实测那条链路有 26–29% 的像素顶到 250 以上，人像糊成一片白。

    这里是"温和还原"，三个参数一起跟着强度走（拍照效果差别很大，一个定值不够用）：

    | | 淡（0） | 默认（30） | 浓（100） |
    |---|---|---|---|
    | CLAHE clipLimit | 1.1 | 1.7 | 3.0 |
    | 高光提亮的白点 | 205 | 220 | 255（等于不提） |
    | 锐化 | 0.22 | 0.30 | 0.50 |

    **提亮那一步是为"字看不清"加的**（用户反馈）：身份证满版防伪底纹，
    局部对比一提，底纹和字一样黑，字就被埋进网纹里。把底纹所在的亮区
    往白推、纯黑的字不动，字才跳出来。实测底纹均值 189 → 217（淡的一头）。
    """
    import cv2

    s = float(np.clip(strength, 0, 100))

    def 调子(gray: np.ndarray) -> np.ndarray:
        clahe = cv2.createCLAHE(clipLimit=1.1 + 0.019 * s, tileGridSize=(8, 8))
        out = enhance_mod.stretch_contrast(clahe.apply(gray), 0.3, 99.7)
        out = _lift_highlights(out, white=255.0 - 50.0 * (1.0 - s / 100.0))
        return enhance_mod.unsharp(out, 0.22 + 0.0028 * s)

    if color and image.ndim == 3:
        # 亮度按上面那条链路整，再同比贴回三个通道 —— 色相和相对饱和度一个字节不变。
        # 试过"只动 LAB 的 L"：饱和的红章一提亮就出色域，低通道被削到 0，
        # 出来是一块死红还偏色（见 enhance.recolor_by_luma 的注释）
        return enhance_mod.recolor_by_luma(image, 调子(enhance_mod._to_gray(image)))  # noqa: SLF001
    return 调子(enhance_mod._to_gray(image))  # noqa: SLF001 —— 同包内的内部工具


def _lift_highlights(gray: np.ndarray, white: float, knee_span: float = 40.0) -> np.ndarray:
    """把 `white` 以上的都推成纯白，`white - knee_span` 到 `white` 之间平滑过渡。

    比"按百分位裁"稳：底纹占的像素数量随证件、随拍照角度差很多，
    按百分位裁会时轻时重；按**亮度**裁，多亮算背景就是多亮算背景。
    膝点以下一个字节都不动，所以黑字和人像的暗部不会受影响。
    """
    import cv2

    if white >= 254.5:
        return gray
    knee = max(white - knee_span, 1.0)
    x = np.arange(256, dtype=np.float32)
    上升 = np.clip((x - knee) / (white - knee), 0.0, 1.0) ** 0.8
    lut = np.where(x <= knee, x, knee + (255.0 - knee) * 上升)
    return cv2.LUT(gray, np.clip(lut, 0, 255).astype(np.uint8))


def _rotate90(image: np.ndarray, times: int) -> np.ndarray:
    return np.rot90(image, k=times).copy() if times % 4 else image


def _ocr_score(gray: np.ndarray) -> float:
    """这张图上认出来的字有多"像字"。用来判断证件是不是拿反了 180°。

    只在证件这条路上用，而且是**判断朝向**，不是要识别内容 ——
    所以缩小到 900px 先，快一点。识别不出来（没模型、报错）就返回 0，
    调用方会保持原样不转，宁可不转也不能转反。
    """
    from . import ocr as ocr_mod

    try:
        result = ocr_mod.recognize(enhance_mod.downscale(gray, 900), preprocess=False)
    except Exception:
        logger.warning("判断证件朝向时 OCR 失败，保持原样", exc_info=True)
        return 0.0
    return float(sum(line.score * len(line.text) for line in result.lines))


def _upright(
    gray: np.ndarray, preset: CardSpec | None, check_flip: bool = True
) -> tuple[np.ndarray, int]:
    """把证件摆正。返回 (摆正后的图, 转了几个 90°)。

    横竖是**确定的**：身份证是横的（85.6×54），照片里竖着说明拍的时候卡是竖放的，
    转 90° 就对了 —— 用户反馈的"没有旋转到横向"就是这一步之前没做。

    转 90° 之后还剩一个二选一：可能上下颠倒。这个没法从形状看出来，
    拿 OCR 比一下两种朝向哪种更像字（内容我们不关心，只看分数）。

    转了几次要报出去：调用方拿它把"卡片本体占图多大"的横竖也跟着换过来。
    """
    if preset is None:
        return gray, 0
    高, 宽 = gray.shape[:2]
    竖着 = 高 > 宽
    预设竖着 = preset.height_mm > preset.width_mm
    turns = 1 if 竖着 != 预设竖着 else 0
    out = _rotate90(gray, turns)
    if not check_flip:
        return out, turns
    正 = _ocr_score(out)
    倒 = _ocr_score(_rotate90(out, 2))
    if 倒 > 正 * 1.2:  # 明显更像字才翻，拿不准就别动
        logger.info("证件上下颠倒了（正 %.0f / 倒 %.0f），转 180°", 正, 倒)
        return _rotate90(out, 2), turns + 2
    return out, turns


def rotate_item(item: CardItem, times: int = 1) -> CardItem:
    """把准备好的证件图再转 90°×times。界面上那个「转一下」按钮用。

    自动摆正偶尔会判错（比如卡上字太少、OCR 认不出来），留个人工兜底 ——
    长辈点一下就好，比让他重新拍一张强。尺寸跟着一起转过来。
    """
    if times % 4 == 0:
        return item
    rotated = _rotate90(item.image, times)
    width_mm, height_mm = item.width_mm, item.height_mm
    fraction = item.body_fraction
    if times % 2:
        width_mm, height_mm = height_mm, width_mm
        fraction = (fraction[1], fraction[0])
    return replace(
        item, image=rotated, width_mm=width_mm, height_mm=height_mm, body_fraction=fraction
    )


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


DEFAULT_STRENGTH = 30  # 界面上滑块的默认位置，也是判朝向时用的那一档


@dataclass
class CardPlan:
    """一张照片"该怎么处理"的结论：裁在哪、是什么证件、要转几下。

    分成两半是为了界面上那个「淡 ←→ 浓」滑块：贵的活儿（抠图 + 认类型 +
    OCR 判上下，实测 1–2 秒）只做一次，拖滑块只重跑便宜的那半截（几十毫秒）。

    顺带还治了一个隐患：每次都重新 OCR 判上下，深浅不同可能给出不一样的结论，
    滑块一动证件就自己翻个面。朝向存在这里，从此是稳的。
    """

    image: np.ndarray  # 裁好拉正的图（含刻意外扩的一圈）
    inset: tuple[int, int, int, int]  # 卡片本体到四条边的像素数（左, 上, 右, 下）
    turns: int = 0  # 摆正要转几个 90°
    preset: CardSpec | None = None
    cropped: bool = False
    dpi: float | None = None
    note: str = ""

    @property
    def body_px(self) -> tuple[int, int]:
        return _body_px(self.image.shape, self.inset)


def analyze_card(
    source: str | Path | np.ndarray, spec_key: str = AUTO, check_flip: bool = True
) -> CardPlan:
    """照片 → CardPlan：抠出卡片、量准边缘、认出类型、定好朝向。**贵的那一半。**"""
    dpi: float | None = None
    if isinstance(source, np.ndarray):
        image = source
    else:
        path = Path(source)
        if not path.exists():
            raise broken(path.name, "文件不存在")
        image = enhance_mod.load_image(path)
        dpi = _image_dpi(path)

    crop = crop_card(image)
    # 认类型用**卡片本体**的比例，不用整张图的 —— 白边虽然是等比放的，
    # 但四条边量出来各不相同，用本体的更准
    body_w, body_h = crop.body_px
    preset = spec_by_key(spec_key) if spec_key != AUTO else None
    note = ""
    if preset is None:
        preset, note = identify_spec(body_w, body_h, dpi)
    elif not _ratio_matches(body_w, body_h, preset):
        # 用户明确选了类型就按他选的尺寸出，但要提醒一句 —— 比例差这么多
        # 通常是选错了类型（比如拿整页文档当身份证），拉伸出来一眼就能看出不对
        note = f"这张图的比例和{preset.name}差得多，是不是选错类型了？"

    inset = crop.inset
    if crop.cropped and preset is not None and _ratio_matches(body_w, body_h, preset):
        inset = _fit_inset_to_ratio(inset, crop.image.shape, preset.ratio)

    turns = 0
    if preset is not None:
        # 判上下要拿"处理过的图"去 OCR，深浅用默认档就行 —— 认的是字形不是色调
        probe = _enhance_card(crop.image, DEFAULT_STRENGTH)
        if crop.cropped:
            probe = _paint_outside(probe, inset, preset.corner_ratio)
        _, turns = _upright(probe, preset, check_flip=check_flip)
    return CardPlan(
        image=crop.image,
        inset=inset,
        turns=turns,
        preset=preset,
        cropped=crop.cropped,
        dpi=dpi,
        note=note,
    )


def render_card(
    plan: CardPlan,
    strength: int = DEFAULT_STRENGTH,
    label: str = "",
    color: bool = False,
) -> CardItem:
    """CardPlan + 深浅 → 可以摆到纸上的 CardItem。**便宜的那一半**，滑块动一下就跑这个。

    `color=True` 出彩色（红章、蓝签留住），给「保存成 PDF / 另存为」用 ——
    店里那台柯美只有黑白，打印时驱动自己会转灰，所以彩色不影响打印那条路。

    尺寸算的是**整张图**该印多大：图里比卡片多一圈刻意留的白边，不把这一圈
    折算进去，卡片就会小印 3.5%（实测 85.6mm 的身份证印出来只有 82.6mm）。
    """
    gray = _enhance_card(plan.image, strength, color=color)
    preset = plan.preset
    if plan.cropped:
        gray = _paint_outside(gray, plan.inset, preset.corner_ratio if preset else 0.0)

    body_w, body_h = plan.body_px
    图高, 图宽 = gray.shape[:2]
    fx, fy = body_w / 图宽, body_h / 图高
    gray = _rotate90(gray, plan.turns)
    if plan.turns % 2:  # 转了 90°，"占多宽/多高"也跟着换个方向
        fx, fy = fy, fx
    height_px, width_px = gray.shape[:2]
    portrait = height_px > width_px

    if preset is not None:
        card_w_mm, card_h_mm = physical_size(preset, portrait)
        return CardItem(
            image=gray,
            width_mm=card_w_mm / fx,  # 声明整张图的尺寸，卡片本体才正好是实物尺寸
            height_mm=card_h_mm / fy,
            label=label,
            spec_key=preset.key,
            cropped=plan.cropped,
            exact_size=True,
            note=plan.note,
            body_fraction=(fx, fy),
        )

    # 认不出来：按扫描 DPI 算，没有 DPI 就按纸宽的一半摆，并明确告诉用户尺寸不保真
    if plan.dpi:
        return CardItem(
            image=gray,
            width_mm=width_px / plan.dpi * 25.4,  # 像素直接换毫米，白边自然也在里面
            height_mm=height_px / plan.dpi * 25.4,
            label=label,
            cropped=plan.cropped,
            exact_size=True,
            note=f"按扫描分辨率 {plan.dpi:.0f}dpi 还原尺寸",
            body_fraction=(fx, fy),
        )
    fallback_w = 90.0
    return CardItem(
        image=gray,
        width_mm=fallback_w,
        height_mm=fallback_w * height_px / max(width_px, 1),
        label=label,
        cropped=plan.cropped,
        exact_size=False,
        note=plan.note or "认不出证件类型，尺寸可能不是实际大小",
        body_fraction=(fx, fy),
    )


def prepare_card(
    source: str | Path | np.ndarray,
    spec_key: str = AUTO,
    options: enhance_mod.EnhanceOptions | None = None,
    label: str = "",
    check_flip: bool = True,
) -> CardItem:
    """一张照片 → 可以摆到纸上的 CardItem。`analyze_card` + `render_card` 一步走完。

    顺序是：抠卡片（含拉正、量出卡片本体的位置）→ 定证件类型 → 定朝向 →
    温和增强 → 卡片外面涂白（直边 + 圆角）→ 摆正 → 定实物尺寸。

    增强走 `_enhance_card()` 而不是「图文混排」那条去底链路：证件上有人像、
    印章、底纹，去底会把它们冲成一片白（用户原话"有点过曝"）。
    """
    plan = analyze_card(source, spec_key, check_flip=check_flip)
    strength = options.strength if options is not None else DEFAULT_STRENGTH
    color = options.color if options is not None else False
    return render_card(plan, strength, label=label, color=color)


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
    """灰度和彩色都吃。彩色是给「另存为 PDF」用的（打印那条路照样是黑白）。"""
    import cv2

    buffer = io.BytesIO()
    if array.ndim == 3:
        Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB)).save(
            buffer, format="PNG", optimize=True
        )
    else:
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
