"""保留原有排版的 Word（文本框）。

「照片转成文字文档」的验收标准是**排版还在**：表格、多栏、签名的位置都还在
原处，而且文字仍然能选中、复制、改错字。所以这里量的是文本框的坐标。
"""

from __future__ import annotations

import pytest

from shop_print.core import ocr
from shop_print.core.errors import ShopPrintError

PT_PER_MM = 72.0 / 25.4


def 行(text: str, x0: float, y0: float, x1: float, y1: float) -> ocr.OcrLine:
    return ocr.OcrLine(text=text, score=0.99, x0=x0, y0=y0, x1=x1, y1=y1)


@pytest.fixture
def 识别结果() -> ocr.OcrResult:
    """假一页 1240×1754 的识别结果（150dpi 的 A4）：标题居中 + 两段正文。"""
    标题 = ocr.OcrParagraph(lines=[行("房屋租赁合同", 500, 200, 740, 236)], is_heading=True)
    正文 = ocr.OcrParagraph(lines=[行("甲方：张建国", 150, 320, 500, 348)])
    右下 = ocr.OcrParagraph(lines=[行("签订日期：八月十六日", 700, 1500, 1100, 1528)])
    return ocr.OcrResult(paragraphs=[标题, 正文, 右下], lines=[], page_width=1240, page_height=1754)


def 取文本框(path) -> list[dict]:
    """从 docx 里把每个文本框的位置、字号和文字抠出来（位置单位是磅）。"""
    import re
    import zipfile

    with zipfile.ZipFile(path) as bundle:
        xml = bundle.read("word/document.xml").decode("utf-8")

    boxes = []
    for style, body in re.findall(r'<v:rect[^>]*style="([^"]+)"[^>]*>(.*?)</v:rect>', xml, re.S):

        def 取(name: str, text: str = style) -> float:
            return float(re.search(rf"{name}:([\d.-]+)pt", text).group(1))

        boxes.append(
            {
                "x": 取("margin-left"),
                "y": 取("margin-top"),
                "w": 取("width"),
                "text": "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", body)),
                "size_half_pt": int(re.search(r'w:sz w:val="(\d+)"', body).group(1)),
            }
        )
    return boxes


def test_每段一个文本框(识别结果, tmp_path) -> None:
    path = ocr.to_docx_layout(识别结果, tmp_path / "排版.docx")
    boxes = 取文本框(path)
    assert len(boxes) == 3
    assert [box["text"] for box in boxes] == [
        "房屋租赁合同",
        "甲方：张建国",
        "签订日期：八月十六日",
    ]


def test_文本框落在原来的位置(识别结果, tmp_path) -> None:
    """1240px 对应 210mm，所以 1px = 0.169mm。标题在 x=500px ≈ 84.7mm。"""
    path = ocr.to_docx_layout(识别结果, tmp_path / "排版.docx")
    boxes = 取文本框(path)
    mm_per_px = 210.0 / 1240
    for box, 段 in zip(boxes, 识别结果.paragraphs, strict=True):
        期望x = min(line.x0 for line in 段.lines) * mm_per_px
        期望y = min(line.y0 for line in 段.lines) * mm_per_px
        assert box["x"] / PT_PER_MM == pytest.approx(期望x, abs=0.3)
        assert box["y"] / PT_PER_MM == pytest.approx(期望y, abs=0.3)


def test_右下角那段不会跑到左上角(识别结果, tmp_path) -> None:
    """顺排的 to_docx() 会把它排到第三行行首 —— 保排版就是为了避免这个。"""
    path = ocr.to_docx_layout(识别结果, tmp_path / "排版.docx")
    右下 = 取文本框(path)[2]
    assert 右下["x"] / PT_PER_MM > 100  # 页面右半边
    assert 右下["y"] / PT_PER_MM > 200  # 页面下半部分


def test_字号跟着检测框高度走(识别结果, tmp_path) -> None:
    """标题框高 36px、正文 28px，标题字号应当明显更大。"""
    path = ocr.to_docx_layout(识别结果, tmp_path / "排版.docx")
    标题, 正文, _ = 取文本框(path)
    assert 标题["size_half_pt"] > 正文["size_half_pt"]
    # 28px × (210/1240) mm × 2.8346 pt/mm × 0.78 ≈ 10.5pt → 21 半磅
    assert 15 <= 正文["size_half_pt"] <= 28


def test_没有页面尺寸时报错而不是乱排(tmp_path) -> None:
    空 = ocr.OcrResult(paragraphs=[], lines=[], page_width=0, page_height=0)
    with pytest.raises(ShopPrintError):
        ocr.to_docx_layout(空, tmp_path / "空.docx")


def test_文字里的尖括号不会破坏文档(tmp_path) -> None:
    """顾客的文件里出现 <、& 很正常，直接拼进 XML 会把文档写坏。"""
    result = ocr.OcrResult(
        paragraphs=[ocr.OcrParagraph(lines=[行("甲方 & 乙方 <见附件>", 100, 100, 600, 130)])],
        lines=[],
        page_width=1240,
        page_height=1754,
    )
    path = ocr.to_docx_layout(result, tmp_path / "转义.docx")
    assert 取文本框(path)[0]["text"] == "甲方 &amp; 乙方 &lt;见附件&gt;"


@pytest.mark.needs_office
def test_用Word打开后位置仍然对得上(识别结果, tmp_path) -> None:
    """最硬的一条：交给真的 Word 转成 PDF，量字落在哪。偏差要在 1mm 以内 ——
    这才叫"保留原有排版"。

    量 span 而不是 block：PyMuPDF 的 block 会把邻近的空段落和文本框归到一组，
    bbox 就跑到页边距上去了，字本身的位置是对的。
    """
    import pymupdf

    from shop_print.core import convert

    docx = ocr.to_docx_layout(识别结果, tmp_path / "排版.docx")
    pdf = convert.office_to_pdf(docx, tmp_path / "排版.pdf")
    mm_per_px = 210.0 / 1240

    with pymupdf.open(pdf) as doc:
        spans = [
            span
            for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"].strip()
        ]
    assert len(spans) == 3
    for span, 段 in zip(spans, 识别结果.paragraphs, strict=True):
        期望x = min(line.x0 for line in 段.lines) * mm_per_px
        期望y = min(line.y0 for line in 段.lines) * mm_per_px
        assert span["bbox"][0] / PT_PER_MM == pytest.approx(期望x, abs=1.0)
        assert span["bbox"][1] / PT_PER_MM == pytest.approx(期望y, abs=1.5)
        assert span["text"] == 段.text
    assert spans[0]["size"] > spans[1]["size"]  # 标题字号更大
