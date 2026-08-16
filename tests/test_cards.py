"""证件二合一。

这一块的验收标准只有一条：**打出来必须是实物大小**。
派出所、银行、学校要的是 1:1 的复印件，缩了放了都可能被退回来重做，
所以下面大部分用例都在量毫米。
"""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from shop_print.core import cards
from shop_print.core.errors import ShopPrintError

from . import synth


def 假证件(width_mm: float, height_mm: float, label: str = "") -> cards.CardItem:
    """跳过图像处理，只验排版和尺寸。"""
    return cards.CardItem(
        image=np.full((60, 90), 220, dtype=np.uint8),
        width_mm=width_mm,
        height_mm=height_mm,
        label=label,
    )


# ── 预设尺寸 ────────────────────────────────────────────────────
def test_身份证是标准卡片尺寸() -> None:
    """ISO/IEC 7810 ID-1：85.6 × 54，身份证/银行卡/社保卡都是这个。"""
    preset = cards.spec_by_key("id")
    assert preset is not None
    assert (preset.width_mm, preset.height_mm) == (85.6, 54.0)
    assert preset.verified is True


def test_没核实的尺寸要标出来() -> None:
    """户口本、驾驶证的尺寸是常见值不是标准值，`verified` 记的就是这个出处。
    两者都照样按实物尺寸出图 —— 验收看的是"PDF 里声明的尺寸和预设表一致"
    （tests/test_physical_size.py），实物对不上就改预设表里那一个数。"""
    未核实 = [p.key for p in cards.PRESETS if not p.verified]
    assert "household" in 未核实
    for key in 未核实:
        assert cards.spec_by_key(key).source  # 必须写清依据


def test_两个位置的名字跟着证件类型变() -> None:
    """户口本是"户主页/本人页"，身份证是"人像面/国徽面" —— 长辈照着名字放图。"""
    assert cards.labels_for("id") == ("人像面", "国徽面")
    assert cards.labels_for("household") == ("户主页", "本人页")
    assert cards.labels_for(cards.AUTO) == ("第一张", "第二张")


def test_竖着拍的证件尺寸也要转过来() -> None:
    preset = cards.spec_by_key("id")
    assert cards.physical_size(preset, portrait=False) == (85.6, 54.0)
    assert cards.physical_size(preset, portrait=True) == (54.0, 85.6)


# ── 自动认类型 ──────────────────────────────────────────────────
def test_按长宽比认出身份证() -> None:
    spec, note = cards.identify_spec(1712, 1080)  # 85.6:54
    assert spec is not None and spec.key == "id"
    assert "身份证" in note


def test_竖着拍也能认出来() -> None:
    spec, _ = cards.identify_spec(1080, 1712)
    assert spec is not None and spec.key == "id"


def test_长宽比像两种证件时拒绝猜() -> None:
    """护照(1.42) 和户口本(1.41) 长宽比几乎一样，尺寸差 16%。
    猜错了顾客要跑第二趟，不如让长辈自己点一下。"""
    spec, note = cards.identify_spec(1415, 1000)
    assert spec is None
    assert "分不清" in note and "自己选" in note


def test_离谱的长宽比不猜() -> None:
    spec, note = cards.identify_spec(3000, 1000)
    assert spec is None
    assert "认不出" in note


def test_扫描件尺寸对不上会提示() -> None:
    """按长宽比像身份证，但 300dpi 下量出来 30cm —— 那是放大的扫描件。"""
    spec, note = cards.identify_spec(3600, 2272, dpi=300)
    assert spec is not None and spec.key == "id"
    assert "对不上" in note


# ── 排版：能 1:1 就 1:1 ─────────────────────────────────────────
def test_身份证正反面上下排在纵向A4() -> None:
    layout = cards.plan_layout([假证件(85.6, 54, "正"), 假证件(85.6, 54, "反")])
    assert layout.landscape is False
    assert layout.scale == pytest.approx(1.0)
    assert layout.exact_size is True
    第一, 第二 = layout.placements
    assert 第一.width_mm == pytest.approx(85.6)
    assert 第一.height_mm == pytest.approx(54.0)
    assert 第一.x_mm == pytest.approx(第二.x_mm)  # 左右对齐
    assert 第二.y_mm > 第一.y_mm  # 上下排
    assert 第二.y_mm - (第一.y_mm + 第一.height_mm) == pytest.approx(10.0)  # 间隔


def test_户口本两页并排要换成横向纸() -> None:
    """两页竖着的 A6 并排要 220mm，超过 A4 的可打印宽度；
    换横向 A4 就放得下，而且两页都还是正着看的。"""
    layout = cards.plan_layout([假证件(105, 148), 假证件(105, 148)])
    assert layout.landscape is True
    assert layout.scale == pytest.approx(1.0)
    第一, 第二 = layout.placements
    assert 第一.y_mm == pytest.approx(第二.y_mm)  # 并排
    assert 第一.width_mm == pytest.approx(105.0)
    assert 第一.height_mm == pytest.approx(148.0)


def test_四张身份证也能一张纸() -> None:
    layout = cards.plan_layout([假证件(85.6, 54) for _ in range(4)])
    assert layout.scale == pytest.approx(1.0)
    assert len(layout.placements) == 4
    assert all(p.height_mm == pytest.approx(54.0) for p in layout.placements)


def test_放不下才缩_而且要说出来() -> None:
    """两页 A5 怎么排都超出 A4 可打印区。缩可以，但不能悄悄缩。"""
    layout = cards.plan_layout([假证件(148, 210), 假证件(148, 210)])
    assert layout.scale < 1.0
    assert layout.exact_size is False
    assert "缩小到" in cards.describe(layout)


def test_尺寸不可靠时也要说出来() -> None:
    item = 假证件(90, 60)
    item.exact_size = False
    layout = cards.plan_layout([item])
    assert layout.scale == pytest.approx(1.0)
    assert layout.exact_size is False
    assert "尺寸可能不是实际大小" in cards.describe(layout)


def test_都在纸里面不出边() -> None:
    layout = cards.plan_layout([假证件(85.6, 54), 假证件(85.6, 54)])
    page_w, page_h = layout.page_size_mm
    for p in layout.placements:
        assert p.x_mm >= 0 and p.y_mm >= 0
        assert p.x_mm + p.width_mm <= page_w + 0.01
        assert p.y_mm + p.height_mm <= page_h + 0.01


def test_没有图片时报错而不是出白纸() -> None:
    with pytest.raises(ShopPrintError):
        cards.plan_layout([])


# ── 出 PDF：量毫米 ──────────────────────────────────────────────
def test_PDF里就是85_6乘54毫米(tmp_path) -> None:
    """整条链路最要紧的一条断言：打出来的框必须是实物尺寸。"""
    items = [假证件(85.6, 54, "正"), 假证件(85.6, 54, "反")]
    path, _ = cards.merge_to_pdf(items, tmp_path / "证件.pdf")
    assert path.exists()

    with pymupdf.open(path) as doc:
        assert doc.page_count == 1
        page = doc[0]
        assert page.rect.width / cards.PT_PER_MM == pytest.approx(210, abs=0.5)  # A4 纵向
        assert page.rect.height / cards.PT_PER_MM == pytest.approx(297, abs=0.5)
        boxes = [info["bbox"] for info in page.get_image_info()]
        assert len(boxes) == 2
        for box in boxes:
            assert (box[2] - box[0]) / cards.PT_PER_MM == pytest.approx(85.6, abs=0.2)
            assert (box[3] - box[1]) / cards.PT_PER_MM == pytest.approx(54.0, abs=0.2)


def test_户口本出的是横向PDF(tmp_path) -> None:
    path, _ = cards.merge_to_pdf([假证件(105, 148), 假证件(105, 148)], tmp_path / "户口本.pdf")
    with pymupdf.open(path) as doc:
        page = doc[0]
        assert page.rect.width > page.rect.height  # 横向，打印时 DEVMODE 会跟着转
        assert page.rect.width / cards.PT_PER_MM == pytest.approx(297, abs=0.5)


def test_不给路径就落到缓存目录() -> None:
    path, _ = cards.merge_to_pdf([假证件(85.6, 54)])
    assert path.exists()
    assert path.parent.name == "cache"
    assert path.name.startswith("证件-")


# ── 抠卡片 + 定尺寸（用合成照片跑整条链路）────────────────────────
def test_从桌面照片里把卡片抠出来() -> None:
    photo = synth.card_photo(seed=1)
    cropped, ok = cards.crop_card(photo)
    assert ok is True
    高, 宽 = cropped.shape[:2]
    assert 宽 / 高 == pytest.approx(85.6 / 54.0, rel=0.03)  # 抠出来就是卡片本身的比例
    assert 高 * 宽 < photo.shape[0] * photo.shape[1]  # 桌面被裁掉了


def test_拍歪了也能抠正() -> None:
    cropped, ok = cards.crop_card(synth.card_photo(seed=2, tilt=6.0))
    assert ok is True
    高, 宽 = cropped.shape[:2]
    assert 宽 / 高 == pytest.approx(85.6 / 54.0, rel=0.05)


def test_一整张纸不会被当成卡片() -> None:
    """长宽比这道检查要挡住误检：A4 文档的比例是 1.41，不在卡片区间里…
    但和户口本重合，所以这里验的是"文字文档不会被当成身份证"。"""
    photo = synth.photographed_text_document()
    cropped, _ = cards.crop_card(photo)
    高, 宽 = cropped.shape[:2]
    spec, _ = cards.identify_spec(宽, 高)
    assert spec is None or spec.key != "id"


def test_整条链路_照片到实物尺寸() -> None:
    item = cards.prepare_card(synth.card_photo(seed=3), "id", label="人像面")
    assert (item.width_mm, item.height_mm) == (85.6, 54.0)
    assert item.cropped is True
    assert item.exact_size is True
    assert item.image.ndim == 2  # 黑白机不需要彩色
    assert item.label == "人像面"


def test_自动认也能定出实物尺寸() -> None:
    item = cards.prepare_card(synth.card_photo(seed=4), cards.AUTO)
    assert (item.width_mm, item.height_mm) == (85.6, 54.0)
    assert "身份证" in item.note


def test_证件上的头像不会被二值化糊掉() -> None:
    """证件有头像、印章、底纹，走"图文混排"保留灰阶；二值化会糊成一团黑。"""
    item = cards.prepare_card(synth.card_photo(seed=5), "id")
    assert len(np.unique(item.image)) > 32


def test_文件不存在报的是人话(tmp_path) -> None:
    with pytest.raises(ShopPrintError) as caught:
        cards.prepare_card(tmp_path / "没有这个.jpg", "id")
    assert "重新发" in caught.value.friendly


def test_比例差得多会提醒是不是选错类型() -> None:
    """拿整页文档当身份证：尺寸照用户选的出（打出来是 85.6×54），但要提醒一句。"""
    item = cards.prepare_card(synth.photographed_text_document(), "id")
    assert (item.width_mm, item.height_mm) == (54.0, 85.6)  # 竖着的图，尺寸转过来
    assert "选错类型" in item.note


def test_图片比例和框差一点也要撑满框(tmp_path) -> None:
    """PyMuPDF 默认保持长宽比，会把框缩小 —— 那样尺寸保证就没了。
    实测 90×60 的图放进 85.6×54 的框会缩成 81mm。"""
    item = cards.CardItem(
        image=np.full((60, 90), 210, dtype=np.uint8), width_mm=85.6, height_mm=54.0
    )
    path = cards.render_pdf(cards.plan_layout([item]), tmp_path / "撑满.pdf")
    with pymupdf.open(path) as doc:
        box = doc[0].get_image_info()[0]["bbox"]
        assert (box[2] - box[0]) / cards.PT_PER_MM == pytest.approx(85.6, abs=0.2)
        assert (box[3] - box[1]) / cards.PT_PER_MM == pytest.approx(54.0, abs=0.2)
