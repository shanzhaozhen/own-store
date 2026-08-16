"""合成"手机拍的文档"用于测试。

**这不能替代真实样张。**合成图只能证明代码跑通、参数方向没搞反；
去底效果好不好只有顾客真实照片能说明（见 samples/README.md）。
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def make_document(width: int = 1200, height: int = 1650, seed: int = 0) -> np.ndarray:
    """干净的白底文档：几段"文字"（用细长黑条模拟）+ 一个标题。返回灰度。"""
    rng = np.random.default_rng(seed)
    page = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(page)

    margin = int(width * 0.12)
    line_h = max(6, height // 110)
    gap = line_h * 2

    # 居中标题
    title_w = int(width * 0.4)
    draw.rectangle(
        [(width - title_w) // 2, margin, (width + title_w) // 2, margin + line_h * 2],
        fill=20,
    )

    y = margin + line_h * 6
    while y < height - margin:
        # 段落：几行满宽 + 最后一行短一点
        for _ in range(int(rng.integers(3, 7))):
            if y >= height - margin:
                break
            right = width - margin
            if rng.random() < 0.25:
                right = margin + int((width - 2 * margin) * rng.uniform(0.3, 0.8))
            draw.rectangle([margin, y, right, y + line_h], fill=int(rng.integers(10, 60)))
            y += line_h + gap
        y += gap * 2

    return np.array(page)  # np.asarray 出来是只读的，调用方要能往上画东西


def add_shadow(gray: np.ndarray, strength: float = 0.55) -> np.ndarray:
    """乘一个不均匀光照场：一侧被手机挡出阴影，再加一块局部暗斑。

    这正是"全局调对比度一定失败"的原因 —— 背景不是均匀的。
    """
    h, w = gray.shape
    xs = np.linspace(0.0, 1.0, w, dtype=np.float32)
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)
    # 横向线性衰减
    field = 1.0 - strength * xs[None, :]
    # 局部暗斑（手挡光）
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    blob = np.exp(-(((xx - 0.75) ** 2 + (yy - 0.3) ** 2) / 0.05)).astype(np.float32)
    field = field * (1.0 - 0.35 * blob)
    out = gray.astype(np.float32) * field
    return np.clip(out, 0, 255).astype(np.uint8)


def add_paper_tint(gray: np.ndarray, amount: float = 0.12) -> np.ndarray:
    """纸张发黄发灰：整体压低亮度上限。"""
    out = gray.astype(np.float32) * (1.0 - amount)
    return np.clip(out, 0, 255).astype(np.uint8)


def add_noise(gray: np.ndarray, sigma: float = 4.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma, gray.shape).astype(np.float32)
    return np.clip(gray.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def photographed_document(seed: int = 0, shadow: float = 0.55) -> np.ndarray:
    """一整套：文档 → 阴影 → 纸张底色 → 噪点。返回 BGR（和 load_image 一致）。"""
    gray = make_document(seed=seed)
    gray = add_shadow(gray, strength=shadow)
    gray = add_paper_tint(gray)
    gray = add_noise(gray, seed=seed)
    return np.dstack([gray, gray, gray])


# 一份像样的中文正文，用来做**真的能 OCR 的**合成样张。
# 上面 make_document 画的是黑条，只能验图像处理；验 OCR 和版面重建必须有真字。
_CONTRACT_LINES: tuple[tuple[str, float, bool], ...] = (
    ("房屋租赁合同", 0.0, True),  # (文字, 首行缩进比例, 是否标题居中)
    ("出租方：张建国　　承租方：李秀兰", 0.0, False),
    ("甲乙双方经友好协商，就房屋租赁事项达成如下协议，双方共同遵守。", 0.08, False),
    ("一、甲方将位于本市和平路八十八号三单元二零一室的房屋出租给乙方居住使用。", 0.08, False),
    ("二、租赁期限自二零二六年九月一日起至二零二七年八月三十一日止，共计十二个月。", 0.08, False),
    ("三、月租金为人民币一千八百元整，乙方应于每月五日前交付当月租金。", 0.08, False),
    ("四、水费、电费、燃气费及物业管理费由乙方按实际用量自行承担。", 0.08, False),
    ("五、乙方不得擅自改变房屋结构，如需装修应事先取得甲方书面同意。", 0.08, False),
    ("六、本合同一式两份，甲乙双方各持一份，自双方签字之日起生效。", 0.08, False),
    ("签订日期：二零二六年八月十六日", 0.0, False),
)


def text_document(width: int = 1240, height: int = 1754, dpi: int = 150) -> np.ndarray:
    """渲染一页**真的中文文字**的干净文档，返回灰度。

    字体用 PyMuPDF 内置的 "china-s"，不依赖系统装了什么字体 ——
    换台机器渲染结果一致，回归测试才有意义。
    """
    import pymupdf

    scale = dpi / 72.0
    document = pymupdf.open()
    page = document.new_page(width=width / scale, height=height / scale)
    font = pymupdf.Font("china-s")
    page.insert_font(fontname="cjk", fontbuffer=font.buffer)

    left = page.rect.width * 0.12
    usable = page.rect.width * 0.76
    y = page.rect.height * 0.10
    for text, indent, heading in _CONTRACT_LINES:
        size = 18.0 if heading else 11.0
        x = left + usable * indent
        if heading:
            x = left + (usable - font.text_length(text, fontsize=size)) / 2.0
        page.insert_text((x, y), text, fontname="cjk", fontsize=size)
        y += size * (2.4 if heading else 2.0)

    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width).copy()


def photographed_text_document(seed: int = 0, shadow: float = 0.5) -> np.ndarray:
    """带真字的"手机拍的文档"：阴影 + 纸张底色 + 噪点。返回 BGR。

    这是本机唯一能端到端验证「去底 → OCR → 排版」的输入。仍然**替代不了
    真实样张** —— 合成图没有折痕、没有反光、没有镜头畸变。
    """
    gray = add_noise(add_paper_tint(add_shadow(text_document(), strength=shadow)), seed=seed)
    return np.dstack([gray, gray, gray])


def card_photo(
    width_mm: float = 85.6,
    height_mm: float = 54.0,
    *,
    px_per_mm: float = 11.0,
    margin: int = 120,
    tilt: float = 0.0,
    seed: int = 0,
    portrait: bool = False,
) -> np.ndarray:
    """合成一张"桌面上的证件照片"：深色桌面上一张浅色卡片，卡片上有字和头像框。

    用来验证证件二合一那条链路：抠卡片 → 透视校正 → 按长宽比认出尺寸 →
    按毫米摆到纸上。`px_per_mm` 只影响卡片在照片里的像素大小，
    **算法不该依赖它** —— 手机照片里没有"每毫米多少像素"这个信息。
    """
    import cv2

    # portrait 的含义是"照片里卡片是竖着的"（长边朝上），传进来的两个尺寸谁大谁小都行
    if portrait == (width_mm > height_mm):
        width_mm, height_mm = height_mm, width_mm
    card_w = max(40, round(width_mm * px_per_mm))
    card_h = max(40, round(height_mm * px_per_mm))
    canvas_w, canvas_h = card_w + margin * 2, card_h + margin * 2

    rng = np.random.default_rng(seed)
    desk = np.full((canvas_h, canvas_w), 90, dtype=np.uint8)  # 深色桌面，和卡片有明显反差
    desk = add_noise(desk, sigma=3.0, seed=seed)

    card = Image.new("L", (card_w, card_h), 236)
    draw = ImageDraw.Draw(card)
    # 头像框 + 几行字，让卡片内部有内容（不然抠出来是一片纯色，看不出效果）
    draw.rectangle(
        [int(card_w * 0.06), int(card_h * 0.18), int(card_w * 0.30), int(card_h * 0.82)], fill=150
    )
    line_h = max(2, card_h // 22)
    y = int(card_h * 0.22)
    while y < card_h * 0.8:
        right = int(card_w * rng.uniform(0.6, 0.94))
        draw.rectangle([int(card_w * 0.36), y, right, y + line_h], fill=40)
        y += line_h * 3

    patch = np.array(card)
    if tilt:
        patch = _rotate_keep(patch, tilt, fill=236)
        card_h, card_w = patch.shape[:2]

    top, left = (canvas_h - card_h) // 2, (canvas_w - card_w) // 2
    if tilt:
        # 旋转后的四角是背景色，用掩膜只贴卡片本体
        mask = _rotate_keep(np.full((card.height, card.width), 255, np.uint8), tilt, fill=0)
        region = desk[top : top + card_h, left : left + card_w]
        desk[top : top + card_h, left : left + card_w] = np.where(mask > 127, patch, region)
    else:
        desk[top : top + card_h, left : left + card_w] = patch

    dirty = add_noise(add_paper_tint(add_shadow(desk, strength=0.3), amount=0.06), seed=seed)
    return cv2.cvtColor(dirty, cv2.COLOR_GRAY2BGR)


def _rotate_keep(gray: np.ndarray, degrees: float, fill: int) -> np.ndarray:
    """旋转并扩大画布装下全图，空白补 fill。"""
    import cv2

    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), degrees, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += new_w / 2.0 - w / 2.0
    matrix[1, 2] += new_h / 2.0 - h / 2.0
    return cv2.warpAffine(gray, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC, borderValue=fill)
