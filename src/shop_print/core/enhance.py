"""照片去底增强 —— v1 最核心的功能。

顾客发来手机拍的纸质文档，带阴影、纸张发黄发灰，黑白激光机直接打出来
一片脏灰、文字发虚。这里把背景压成干净的白、把文字提成实心的黑。

关键在于**先估计出这张图的光照场再除掉它**（flat-field correction）。
直接调全局对比度或全局阈值一定失败：拍照文档的背景不均匀，参数照顾了
亮的一侧，暗的一侧文字就糊成整片黑；照顾暗的一侧，亮的一侧文字就消失。

算法原理与参数含义见 docs/03-图片增强算法.md。

命令行调试：
    python -m shop_print.core.enhance <图片路径> --mode auto --strength 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

MODE_AUTO = "auto"
MODE_TEXT = "text"
MODE_MIXED = "mixed"
MODE_PHOTO = "photo"
MODES = (MODE_AUTO, MODE_TEXT, MODE_MIXED, MODE_PHOTO)

# 背景光照场是低频信息，缩小到这个宽度再估计：快很多，效果没有区别。
_BG_ESTIMATE_WIDTH = 800
# 形态学核宽相对图宽的比例。必须明显大于笔画宽度，又不能大到把整段文字当前景。
_BG_KERNEL_RATIO = 20
# 找到的四边形要占到这么大面积才认为是"一张纸"，否则宁可不裁。
_MIN_QUAD_AREA_RATIO = 0.25
# 倾斜校正只在这个范围内做。超过说明判断很可能是错的，不动比动坏好。
_MAX_DESKEW_DEG = 15.0
_MIN_DESKEW_DEG = 0.3
# 对比拉伸之后，亮过这个值就当成"确定是白纸"，二值化时强制留白。
# 拍照最暗那一侧的噪点在光照拉平时会被同比放大，不拦住就会在空白页边
# 撒一层黑麻点 —— 打出来就是脏纸。
# 定在 215 而不是更高：实测（docs/03 调参记录）阴影区残留的噪点落在 215–235
# 之间，卡在 235 时它们全都被判成墨；压到 215 之后假墨少了两个数量级，
# 而灰度 190 的淡字（铅笔、褪色墨）仍然保住 98%。
_PAPER_FLOOR = 215
# 对比拉伸的黑点上限：算出来的黑点不能高过白点的这个比例。
# 见 stretch_contrast —— 这条是防"整页发黑"的保险。
_BLACK_POINT_MAX_RATIO = 0.4


@dataclass
class EnhanceOptions:
    """界面上只暴露 mode 和 strength 两项，其余由算法自己决定。"""

    mode: str = MODE_AUTO
    strength: int = 50  # 0–100，界面上的"淡 ←→ 浓"
    deskew: bool = True  # 自动裁正/旋正，不确信时算法会自己跳过
    max_side: int | None = None  # 预览时缩到 1000，打印时留 None 用全分辨率


@dataclass
class EnhanceResult:
    image: np.ndarray  # 单通道：mixed/photo 是灰度，text 是二值
    mode_used: str  # auto 实际落到了哪一档
    cropped: bool  # 有没有做透视校正
    rotated_deg: float  # 旋正了多少度（0 表示没转）


@dataclass
class _StrengthParams:
    """把界面上那一个滑块摊开成算法真正需要的几个参数。"""

    threshold_c: float  # adaptiveThreshold 的 C：越小越黑越粗
    ink_ceiling: float  # 暗过这个值就直接判成墨（补实心大块）
    clip_low: float  # 对比拉伸的低百分位
    clip_high: float  # 对比拉伸的高百分位
    gamma: float  # >1 压暗中间调，字更实
    unsharp_amount: float


def strength_params(strength: int) -> _StrengthParams:
    """strength 0（淡）→ 100（浓）。

    注意 adaptiveThreshold 的 C 是**反向**的：C 越大阈值越低，越多像素判成白，
    字越细越淡。所以"浓"对应小 C。

    `ink_ceiling` 的上限刻意压在 160：它是无条件把像素判成墨的兜底，
    抬太高的话阴影里没拉平干净的纸（拉伸后落在 160–190）会被整片判成墨，
    打出来是"半页全黑"。见 docs/03 的调参记录。
    """
    s = float(np.clip(strength, 0, 100))
    return _StrengthParams(
        threshold_c=20.0 - 0.16 * s,  # 0→20  50→12  100→4
        ink_ceiling=130.0 + 0.3 * s,  # 0→130 50→145 100→160
        clip_low=0.5 + 0.015 * s,  # 0→0.5 50→1.25 100→2.0
        clip_high=99.8 - 0.006 * s,  # 0→99.8 50→99.5 100→99.2
        gamma=0.75 + 0.010 * s,  # 0→0.75 50→1.25 100→1.75
        unsharp_amount=0.3 + 0.012 * s,  # 0→0.3 50→0.9 100→1.5
    )


def load_image(path: str | Path) -> np.ndarray:
    """读图并按 EXIF 旋正。返回 BGR uint8。

    用 Pillow 而不是 cv2.imread：cv2 不认中文路径（顾客文件名基本都是中文），
    也不处理手机拍照普遍带的 EXIF 方向标记。
    """
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        rgb = im.convert("RGB")
        arr = np.asarray(rgb)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def downscale(image: np.ndarray, max_side: int | None) -> np.ndarray:
    """按长边缩小。预览用，让滑块能实时出效果。"""
    if not max_side:
        return image
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return image
    scale = max_side / longest
    return cv2.resize(
        image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA
    )


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """把四个点排成 左上、右上、右下、左下。"""
    pts = pts.reshape(4, 2).astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    total = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(total)]  # 左上：x+y 最小
    ordered[2] = pts[np.argmax(total)]  # 右下：x+y 最大
    diff = np.diff(pts, axis=1).ravel()
    ordered[1] = pts[np.argmin(diff)]  # 右上：y-x 最小
    ordered[3] = pts[np.argmax(diff)]  # 左下：y-x 最大
    return ordered


def detect_page_quad(image: np.ndarray) -> np.ndarray | None:
    """在缩略图上找纸张的四个角，返回原图坐标系下的四点；找不到返回 None。

    宁可不裁也不能裁坏 —— 长辈没法判断"这张是不是被裁错了"，
    所以判据故意保守：必须是凸四边形、占面积够大、四角接近直角。
    """
    small = downscale(image, 900)
    scale = image.shape[1] / small.shape[1]
    gray = _to_gray(small)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    small_area = float(small.shape[0] * small.shape[1])

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = cv2.contourArea(contour)
        if area < small_area * _MIN_QUAD_AREA_RATIO:
            break  # 后面的更小，不用看了
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = _order_quad(approx)
        if not _has_right_angles(quad):
            continue
        return quad * scale
    return None


def _has_right_angles(quad: np.ndarray, tolerance_deg: float = 25.0) -> bool:
    """四个内角都接近 90° 才算一张纸。拍歪的纸在图上是梯形，但角度不会离谱。"""
    for i in range(4):
        a = quad[(i - 1) % 4] - quad[i]
        b = quad[(i + 1) % 4] - quad[i]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na < 1e-6 or nb < 1e-6:
            return False
        cos = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
        if abs(np.degrees(np.arccos(cos)) - 90.0) > tolerance_deg:
            return False
    return True


def warp_to_quad(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """把四边形拉成正矩形，顺带裁掉桌面背景。"""
    tl, tr, br, bl = quad
    width = round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    width, height = max(width, 1), max(height, 1)
    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255)
    )


def estimate_skew(gray: np.ndarray) -> float:
    """估计文字整体的倾斜角（度）。返回 0 表示不该转。

    做法：粗二值化取出文字像素，对整块文字求最小外接矩形的角度。
    整页文字时这个估计很稳；文字太少时角度会乱，所以用 ±15° 兜住。
    """
    small = downscale(gray, 1000)
    blurred = cv2.GaussianBlur(small, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    # 横向膨胀把同一行的字连起来，让外接矩形反映文字行的方向而不是单字。
    binary = cv2.dilate(binary, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3)), iterations=1)
    points = cv2.findNonZero(binary)
    if points is None or len(points) < 200:
        return 0.0
    angle = cv2.minAreaRect(points)[-1]
    if angle > 45.0:
        angle -= 90.0
    if not (_MIN_DESKEW_DEG <= abs(angle) <= _MAX_DESKEW_DEG):
        return 0.0
    return float(angle)


def rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    """旋转并扩大画布装下全图，空白补白色（而不是默认的黑色）。"""
    if abs(degrees) < 1e-3:
        return image
    h, w = image.shape[:2]
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, degrees, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)
    matrix[0, 2] += new_w / 2.0 - center[0]
    matrix[1, 2] += new_h / 2.0 - center[1]
    border = 255 if image.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(image, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC, borderValue=border)


def flatten_illumination(gray: np.ndarray) -> np.ndarray:
    """去阴影的关键一步：估计光照场，然后把它除掉。

    用大核形态学闭运算把文字"填掉"，剩下的就是纸张本身的明暗分布；
    原图除以它，阴影、纸张底色、光照渐变被整体抹平。

    背景是低频信息，所以在缩小的图上估计再放大回来 —— 快得多，效果一样。
    """
    h, w = gray.shape[:2]
    if w <= 0 or h <= 0:
        return gray

    scale = min(1.0, _BG_ESTIMATE_WIDTH / float(w))
    small_w = max(32, round(w * scale))
    small_h = max(32, round(h * scale))
    small = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_AREA)

    k = max(9, (small_w // _BG_KERNEL_RATIO) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    background = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    background = cv2.GaussianBlur(background, (0, 0), max(1.0, k / 3.0))

    background = cv2.resize(background, (w, h), interpolation=cv2.INTER_LINEAR)
    background = np.maximum(background, 1)  # 防除零
    return cv2.divide(gray, background, scale=255)


def stretch_contrast(gray: np.ndarray, clip_low: float, clip_high: float) -> np.ndarray:
    """按百分位裁剪后线性拉伸到 0–255。

    用百分位而不是 min/max：一个噪点就能把 min/max 带偏，百分位不会。

    **但低百分位有个陷阱**：它假设"最暗的那 clip_low% 像素是墨"。一张只有
    几行字的证明，墨可能只占 0.5%，这时 1.25% 的低百分位就切进了**纸**的分布，
    lo 被算成纸的灰度，拉伸之后半页纸变成黑的 —— 实测最坏时 A4 上有 67 万个
    像素被判成墨（docs/03 调参记录）。

    所以给黑点加一条上限：光照拉平之后纸接近白点，真正的墨离白点很远；
    算出来的黑点高过白点的 40%，说明切掉的暗部其实是纸，把它压回去。
    """
    lo, hi = np.percentile(gray, [clip_low, clip_high])
    lo = min(lo, hi * _BLACK_POINT_MAX_RATIO)
    if hi - lo < 1.0:
        return gray
    out = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_gamma(gray: np.ndarray, gamma: float) -> np.ndarray:
    """gamma > 1 压暗中间调（字更实），< 1 提亮（字更淡）。"""
    if abs(gamma - 1.0) < 1e-3:
        return gray
    table = np.clip(((np.arange(256) / 255.0) ** gamma) * 255.0, 0, 255).astype(np.uint8)
    return cv2.LUT(gray, table)


def unsharp(gray: np.ndarray, amount: float) -> np.ndarray:
    """轻度锐化，让笔画边缘更利。amount 过大会出白边，所以上限压在 1.5。"""
    if amount <= 0:
        return gray
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.2)
    return cv2.addWeighted(gray, 1.0 + amount, blurred, -amount, 0)


def binarize(gray: np.ndarray, threshold_c: float) -> np.ndarray:
    """自适应阈值二值化。

    blockSize 取图宽的 1/25（约十来个字宽），比常见的"两三个字"大得多。
    这是刻意的：**窗口太小会把粗笔画和实心块掏空**（窗口整个落在墨里，
    局部均值等于墨色，中心像素就被判成白）。前面已经做过光照拉平，
    背景本来就均匀了，不需要小窗口去追局部亮度。
    """
    w = gray.shape[1]
    block = int(np.clip((w // 25) | 1, 25, 151))
    if block % 2 == 0:
        block += 1
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, threshold_c
    )


def despeckle(binary: np.ndarray, min_area: int | None = None) -> np.ndarray:
    """删掉面积过小的黑色连通域，也就是噪点。阈值随图像尺寸走。

    阈值刻意压得很低。实测（150dpi 渲染的中文合同）：整页 535 个连通域里
    有 77 个面积 ≤ 10px² —— 顿号的点、逗号的尾巴都在这个量级。把阈值从 5
    抬到 11 能多清掉一些阴影残留，代价是又丢 6 个真的标点。
    **宁可留几个看不见的麻点，也不能把合同里的标点吃掉。**
    脏背景要靠 _PAPER_FLOOR 和黑点上限去解决，不是靠这里加大力度。
    """
    h, w = binary.shape[:2]
    if min_area is None:
        min_area = max(4, round(w * h * 2.5e-6))
    ink = (binary == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if count <= 1:
        return binary
    too_small = np.zeros(count, dtype=bool)
    too_small[1:] = stats[1:, cv2.CC_STAT_AREA] < min_area
    out = binary.copy()
    out[too_small[labels]] = 255
    return out


def binarize_clean(gray: np.ndarray, threshold_c: float, ink_ceiling: float) -> np.ndarray:
    """二值化 + 补实心 + 去脏。四道处理，少一道打出来就难看：

    1. 中值滤波干掉椒盐噪点（比高斯合适：不糊笔画）
    2. 自适应阈值出主体
    3. 明确暗过 ink_ceiling 的像素直接判成墨 —— 补上被自适应阈值掏空的
       粗笔画、实心标题条、表头底色、印章
    4. 明显是白纸的像素强制留白（_PAPER_FLOOR），再删掉过小的黑色连通域
    """
    smoothed = cv2.medianBlur(gray, 3)
    binary = binarize(smoothed, threshold_c)
    binary[smoothed < ink_ceiling] = 0
    binary[smoothed >= _PAPER_FLOOR] = 255
    return despeckle(binary)


def classify_content(flat_gray: np.ndarray) -> str:
    """判断这张图是"以文字为主"还是"图文混排"，供 auto 模式落档。

    文字文档的特征很明显：绝大部分像素是白纸，墨迹只占一小块。
    """
    small = downscale(flat_gray, 700)
    white_ratio = float(np.count_nonzero(small > 200)) / small.size
    otsu, _ = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    ink_ratio = float(np.count_nonzero(small < otsu)) / small.size
    if white_ratio >= 0.55 and 0.005 <= ink_ratio <= 0.35:
        return MODE_TEXT
    return MODE_MIXED


def _straighten(image: np.ndarray) -> tuple[np.ndarray, bool, float]:
    """裁正或旋正。返回 (图, 是否透视校正, 旋转角度)。"""
    quad = detect_page_quad(image)
    if quad is not None:
        return warp_to_quad(image, quad), True, 0.0
    degrees = estimate_skew(_to_gray(image))
    if degrees:
        return rotate(image, degrees), False, degrees
    return image, False, 0.0


def prepare_for_ocr(image: np.ndarray) -> np.ndarray:
    """给 OCR 用的前处理：裁正 + 去阴影 + 对比拉伸，输出灰度。

    **故意不二值化。**过度二值化会吃掉笔画细节，反而降低识别率。
    和"照片变清楚再打印"共用同一条前处理链，见 docs/05-OCR与版面重建.md。
    """
    work, _, _ = _straighten(image)
    gray = _to_gray(work)
    return stretch_contrast(flatten_illumination(gray), 1.0, 99.5)


def enhance(image: np.ndarray, options: EnhanceOptions | None = None) -> EnhanceResult:
    """完整的去底增强。输入 BGR 或灰度，输出单通道（黑白打印机不需要彩色）。"""
    opts = options or EnhanceOptions()
    mode = opts.mode if opts.mode in MODES else MODE_AUTO
    params = strength_params(opts.strength)

    work = downscale(image, opts.max_side)
    cropped, rotated = False, 0.0
    if opts.deskew:
        work, cropped, rotated = _straighten(work)
    gray = _to_gray(work)

    if mode == MODE_PHOTO:
        # 真照片的明暗本身就是内容，不能拿去阴影把它抹平；
        # 只做局部对比增强，避免暗部在黑白机上糊成一团黑。
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        out = apply_gamma(clahe.apply(gray), 1.0 + (params.gamma - 1.0) * 0.5)
        return EnhanceResult(out, MODE_PHOTO, cropped, rotated)

    flat = stretch_contrast(flatten_illumination(gray), params.clip_low, params.clip_high)
    if mode == MODE_AUTO:
        mode = classify_content(flat)

    if mode == MODE_TEXT:
        # 这条路不做锐化：锐化会把噪点一起放大，紧接着二值化只会更脏。
        out = binarize_clean(flat, params.threshold_c, params.ink_ceiling)
    else:
        out = apply_gamma(unsharp(flat, params.unsharp_amount), params.gamma)
    return EnhanceResult(out, mode, cropped, rotated)


def enhance_file(path: str | Path, options: EnhanceOptions | None = None) -> EnhanceResult:
    return enhance(load_image(path), options)


def save_image(image: np.ndarray, path: str | Path) -> None:
    """用 Pillow 存：cv2.imwrite 不认中文路径，而顾客文件名基本都是中文。"""
    if image.ndim == 2:
        Image.fromarray(image, mode="L").save(path)
    else:
        Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).save(path)


def side_by_side(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """把原图和处理后拼在一起，方便肉眼比对调参效果。"""
    left = _to_gray(before)
    right = after if after.ndim == 2 else _to_gray(after)
    height = max(left.shape[0], right.shape[0])

    def _pad(img: np.ndarray) -> np.ndarray:
        scale = height / img.shape[0]
        resized = cv2.resize(
            img, (max(1, round(img.shape[1] * scale)), height), interpolation=cv2.INTER_AREA
        )
        return resized

    left, right = _pad(left), _pad(right)
    gap = np.full((height, 16), 128, dtype=np.uint8)
    return np.hstack([left, gap, right])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m shop_print.core.enhance",
        description="照片去底增强的命令行调试入口：跑一张图，输出原图/处理后的对比图。",
    )
    parser.add_argument("image", type=Path, help="图片路径")
    parser.add_argument("--mode", choices=MODES, default=MODE_AUTO)
    parser.add_argument("--strength", type=int, default=50, help="0（淡）– 100（浓）")
    parser.add_argument("--no-deskew", action="store_true", help="关掉自动裁正/旋正")
    parser.add_argument("--max-side", type=int, default=None, help="先缩到这个长边（模拟预览）")
    parser.add_argument("-o", "--out", type=Path, default=None, help="对比图输出路径")
    parser.add_argument("--only-result", action="store_true", help="只存处理后的图，不拼对比")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_parser().parse_args(argv)

    if not args.image.exists():
        print(f"找不到图片：{args.image}", file=sys.stderr)
        return 1

    original = load_image(args.image)
    result = enhance(
        original,
        EnhanceOptions(
            mode=args.mode,
            strength=args.strength,
            deskew=not args.no_deskew,
            max_side=args.max_side,
        ),
    )

    out_path = args.out or args.image.with_name(f"{args.image.stem}.enhanced.png")
    save_image(result.image if args.only_result else side_by_side(original, result.image), out_path)

    h, w = result.image.shape[:2]
    print(
        f"模式={result.mode_used} 强度={args.strength} "
        f"裁正={'是' if result.cropped else '否'} 旋转={result.rotated_deg:.2f}° "
        f"尺寸={w}x{h}\n已保存：{out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
