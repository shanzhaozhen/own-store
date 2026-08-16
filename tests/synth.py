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
