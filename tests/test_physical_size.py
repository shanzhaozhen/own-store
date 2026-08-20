"""物理尺寸一致性：**每一种产物在文件里声明的尺寸都必须对得上**。

打印链路上所有产物都要能拿尺子对：
- 图片 / txt 转出的 PDF —— A4 210×297mm
- OCR 转出的 Word —— A4（python-docx 默认模板是美国 Letter，必须显式设）
- 证件二合一的 PDF —— 页面 A4，每张证件是预设表里的实物尺寸

这一份专门盯这件事：尺寸错了打出来的复印件就作废，而这类错误肉眼很难发现
（Letter 和 A4 只差 6mm 宽，缩放 4% 也看不出来），只能靠断言。
"""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from shop_print.core import cards, convert, ocr

from . import synth

PT_PER_MM = 72.0 / 25.4
A4 = (210.0, 297.0)


def 量页面(page) -> tuple[float, float]:
    return (page.rect.width / PT_PER_MM, page.rect.height / PT_PER_MM)


def 量图片框(page, index: int = 0) -> tuple[float, float]:
    box = page.get_image_info()[index]["bbox"]
    return ((box[2] - box[0]) / PT_PER_MM, (box[3] - box[1]) / PT_PER_MM)


# ── PDF：页面就是 A4 ────────────────────────────────────────────
def test_图片转的PDF是A4(tmp_path) -> None:
    from PIL import Image

    src = tmp_path / "一张.png"
    Image.fromarray(np.full((600, 400), 220, dtype=np.uint8), mode="L").save(src)
    out = convert.images_to_pdf([src], tmp_path / "图.pdf", convert.ConvertOptions())
    with pymupdf.open(out) as doc:
        assert 量页面(doc[0]) == pytest.approx(A4, abs=0.5)


def test_txt转的PDF是A4(tmp_path) -> None:
    src = tmp_path / "说明.txt"
    src.write_text("房屋租赁合同", encoding="utf-8")
    out = convert.text_to_pdf(src, tmp_path / "txt.pdf", convert.ConvertOptions())
    with pymupdf.open(out) as doc:
        assert 量页面(doc[0]) == pytest.approx(A4, abs=0.5)


def test_A3选项出的就是A3(tmp_path) -> None:
    from PIL import Image

    src = tmp_path / "一张.png"
    Image.fromarray(np.full((600, 400), 220, dtype=np.uint8), mode="L").save(src)
    out = convert.images_to_pdf(
        [src], tmp_path / "a3.pdf", convert.ConvertOptions(paper="A3", auto_orient=False)
    )
    with pymupdf.open(out) as doc:
        assert 量页面(doc[0]) == pytest.approx((297.0, 420.0), abs=0.5)


# ── Word：也得是 A4 ─────────────────────────────────────────────
def test_OCR转出的Word是A4而不是Letter(tmp_path) -> None:
    """python-docx 自带模板是 Letter（215.9×279.4mm）。店里只有 A4 纸，
    拿 Letter 的文档去打，Word 会自己缩放或者把版面挪位。"""
    from docx import Document

    result = ocr.OcrResult(
        paragraphs=[ocr.OcrParagraph(lines=[ocr.OcrLine("房屋租赁合同", 0.99, 0, 0, 100, 20)])]
    )
    path = ocr.to_docx(result, tmp_path / "转出来.docx")
    section = Document(path).sections[0]
    assert (round(section.page_width.mm, 1), round(section.page_height.mm, 1)) == A4


# ── 证件：每一种预设都要按实物尺寸落到 PDF 里 ─────────────────────
def 造件(width_mm: float, height_mm: float) -> cards.CardItem:
    # 像素比例故意和目标框不完全一致（抠图总有误差），尺寸也不许因此缩水
    return cards.CardItem(
        image=np.full((100, 160), 215, dtype=np.uint8),
        width_mm=width_mm,
        height_mm=height_mm,
    )


@pytest.mark.parametrize("preset", cards.PRESETS, ids=lambda p: p.key)
@pytest.mark.parametrize("portrait", [False, True], ids=["横放", "竖放"])
def test_每种证件在PDF里都是预设的实物尺寸(preset, portrait: bool, tmp_path) -> None:
    宽, 高 = cards.physical_size(preset, portrait)
    path, layout = cards.merge_to_pdf(
        [造件(宽, 高), 造件(宽, 高)], tmp_path / f"{preset.key}-{portrait}.pdf"
    )
    with pymupdf.open(path) as doc:
        page = doc[0]
        # 页面是 A4（可能是横放的 A4）
        assert sorted(量页面(page)) == pytest.approx(sorted(A4), abs=0.5)
        boxes = [量图片框(page, i) for i in range(2)]
        for box in boxes:
            assert box == pytest.approx((宽 * layout.scale, 高 * layout.scale), abs=0.3)
    # 这些尺寸都能 1:1 放下，不该出现缩放
    assert layout.scale == pytest.approx(1.0)


def test_身份证就是85_6乘54毫米(tmp_path) -> None:
    """最要紧的一条，单独写出来：派出所、银行要的就是这个尺寸。"""
    preset = cards.spec_by_key("id")
    path, _ = cards.merge_to_pdf(
        [造件(preset.width_mm, preset.height_mm)] * 2, tmp_path / "身份证.pdf"
    )
    with pymupdf.open(path) as doc:
        for index in range(2):
            assert 量图片框(doc[0], index) == pytest.approx((85.6, 54.0), abs=0.3)


def test_照片进去_纸上量出来就是85_6乘54(tmp_path) -> None:
    """整条链路的验收标准：拿尺子量**纸上印出来那块**，必须是 85.6×54。

    前面几条量的是"PDF 里声明的图片框"，可是图片里比卡片多一圈白边
    （裁的时候刻意留的，为了不切掉证件的边和圆角）。只盯图片框就会漏掉
    "白边挤掉了卡片"这种错 —— 真实照片实测过：卡片本体只印出 82.6mm，短了 3mm。

    所以这里把页面渲染成位图，量**有墨的范围**：白边是白的，量到的就是卡片本身。
    """
    import cv2

    items = [
        cards.prepare_card(synth.card_photo(seed=seed), "id", check_flip=False) for seed in (21, 22)
    ]
    path, layout = cards.merge_to_pdf(items, tmp_path / "拍照的身份证.pdf")
    assert layout.scale == pytest.approx(1.0)
    for item in items:
        assert item.card_size_mm == pytest.approx((85.6, 54.0), abs=0.05)

    dpi = 300
    页 = cv2.imdecode(
        np.frombuffer(convert.render_page_png(path, 0, dpi=dpi), np.uint8), cv2.IMREAD_GRAYSCALE
    )
    墨 = 页 < 245
    有墨的行 = np.where(墨.any(axis=1))[0]
    assert 有墨的行.size > 0
    块 = np.split(有墨的行, np.where(np.diff(有墨的行) > 20)[0] + 1)  # 两张之间是空白，按行断开
    assert len(块) == 2, f"一张纸上应该看到两块墨迹，实际 {len(块)}"
    for 块行 in 块:
        子图 = 墨[块行[0] : 块行[-1] + 1]
        列 = np.where(子图.any(axis=0))[0]
        宽mm = (列[-1] - 列[0] + 1) / dpi * 25.4
        高mm = (块行[-1] - 块行[0] + 1) / dpi * 25.4
        # ±1mm：抠图本身有零点几毫米误差，再加上涂白边界那两三个像素的过渡
        assert (宽mm, 高mm) == pytest.approx((85.6, 54.0), abs=1.0)
