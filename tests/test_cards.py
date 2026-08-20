"""证件二合一。

这一块的验收标准只有一条：**打出来必须是实物大小**。
派出所、银行、学校要的是 1:1 的复印件，缩了放了都可能被退回来重做，
所以下面大部分用例都在量毫米。
"""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from shop_print.core import cards, convert
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
    crop = cards.crop_card(photo)
    assert crop.cropped is True
    高, 宽 = crop.image.shape[:2]
    assert 宽 / 高 == pytest.approx(85.6 / 54.0, rel=0.03)  # 抠出来就是卡片本身的比例
    assert 高 * 宽 < photo.shape[0] * photo.shape[1]  # 桌面被裁掉了
    卡宽, 卡高 = crop.body_px
    assert 0 < 卡宽 < 宽 and 0 < 卡高 < 高  # 外面留了一圈，本体比整张图小一点
    assert 卡宽 / 宽 > 0.9


def test_量出来的边距对得上卡片真实的边() -> None:
    """裁的时候往外放了 1.5%，量出来的边距就该在这个数附近（±1%）。

    这个数**必须量**不能算：检出的框和卡片总差零点几个百分点，实测真实照片上
    四条边差 1.4%–3.2%，按"放了多少"反推会在差得多的那一侧留一条桌面暗边。
    """
    crop = cards.crop_card(synth.card_photo(seed=12))
    高, 宽 = crop.image.shape[:2]
    左, 上, 右, 下 = crop.inset
    for 名, 值, 边长 in (("左", 左, 宽), ("上", 上, 高), ("右", 右, 宽), ("下", 下, 高)):
        assert 0.005 <= 值 / 边长 <= 0.03, f"{名}边量出来 {值 / 边长:.3f}，不像外扩的那一圈"


def test_拍歪了也能抠正() -> None:
    crop = cards.crop_card(synth.card_photo(seed=2, tilt=6.0))
    assert crop.cropped is True
    高, 宽 = crop.image.shape[:2]
    assert 宽 / 高 == pytest.approx(85.6 / 54.0, rel=0.05)


def test_一整张纸不会被当成卡片() -> None:
    """长宽比这道检查要挡住误检：A4 文档的比例是 1.41，不在卡片区间里…
    但和户口本重合，所以这里验的是"文字文档不会被当成身份证"。"""
    photo = synth.photographed_text_document()
    高, 宽 = cards.crop_card(photo).image.shape[:2]
    spec, _ = cards.identify_spec(宽, 高)
    assert spec is None or spec.key != "id"


def test_整条链路_照片到实物尺寸() -> None:
    item = cards.prepare_card(synth.card_photo(seed=3), "id", label="人像面", check_flip=False)
    assert item.card_size_mm == pytest.approx((85.6, 54.0), abs=0.01)
    assert item.cropped is True
    assert item.exact_size is True
    assert item.image.ndim == 2  # 黑白机不需要彩色
    assert item.label == "人像面"


def test_白边要算进尺寸里不能挤掉卡片() -> None:
    """裁的时候往外放了一圈白边，声明尺寸得把它算进去。

    不算的话卡片本体会小印 3.5%（真实照片实测：85.6mm 的身份证印出来 82.6mm），
    而"1:1"正是这个功能存在的理由 —— 缩了的复印件会被派出所退回来。
    """
    item = cards.prepare_card(synth.card_photo(seed=13), "id", check_flip=False)
    assert item.card_size_mm == pytest.approx((85.6, 54.0), abs=0.01)
    assert item.width_mm > 85.6  # 整张图比卡片大：多出来的是白边
    assert item.height_mm > 54.0
    assert item.width_mm < 85.6 * 1.08, "白边不该有这么宽"


def test_自动认也能定出实物尺寸() -> None:
    item = cards.prepare_card(synth.card_photo(seed=4), cards.AUTO, check_flip=False)
    assert item.card_size_mm == pytest.approx((85.6, 54.0), abs=0.01)
    assert "身份证" in item.note


def test_证件上的头像不会被二值化糊掉() -> None:
    """证件有头像、印章、底纹，要保留灰阶；二值化会糊成一团黑。"""
    item = cards.prepare_card(synth.card_photo(seed=5), "id", check_flip=False)
    assert len(np.unique(item.image)) > 32


def test_文件不存在报的是人话(tmp_path) -> None:
    with pytest.raises(ShopPrintError) as caught:
        cards.prepare_card(tmp_path / "没有这个.jpg", "id")
    assert "重新发" in caught.value.friendly


def test_比例差得多会提醒是不是选错类型() -> None:
    """拿整页文档当身份证：尺寸照用户选的出（打出来是 85.6×54），但要提醒一句。"""
    item = cards.prepare_card(synth.photographed_text_document(), "id", check_flip=False)
    # 选了身份证就按身份证摆：横着的 85.6×54（竖着拍的图会被转成横向）
    assert (item.width_mm, item.height_mm) == pytest.approx((85.6, 54.0), abs=0.01)
    assert "选错类型" in item.note


# ── 摆正：横竖 + 圆角 + 不过曝（都是真实照片反馈出来的问题）──────────
def test_竖着拍的身份证会转成横向() -> None:
    """用户反馈的第一条：拍的时候卡是竖放的，出来还是竖的。
    身份证是横的（85.6×54），形状已经确定了答案，不用猜。"""
    item = cards.prepare_card(
        synth.card_photo(85.6, 54.0, portrait=True, seed=8), "id", check_flip=False
    )
    高, 宽 = item.image.shape[:2]
    assert 宽 > 高, "竖着拍的身份证应该被转成横向"
    assert item.card_size_mm == pytest.approx((85.6, 54.0), abs=0.01)


def test_圆角外面涂白而不是留桌面色() -> None:
    """证件是圆角的，裁成方框之后四角会露出桌面。涂白才是"一张卡"的复印件。"""
    item = cards.prepare_card(synth.card_photo(seed=9), "id", check_flip=False)
    高, 宽 = item.image.shape[:2]
    角 = item.image[: max(3, 高 // 40), : max(3, 宽 // 40)]
    assert 角.mean() > 240, f"左上角应该是白的，实际 {角.mean():.0f}"


def test_卡片四周不留桌面暗边() -> None:
    """涂白按量出来的矩形走，不按阈值掩膜的波浪边 —— 后者会留一圈毛边和黑点。

    量最外面一圈：应该几乎全白（只允许卡片自己那条边和圆角的过渡）。
    """
    item = cards.prepare_card(synth.card_photo(seed=14), "id", check_flip=False)
    高, 宽 = item.image.shape[:2]
    带 = max(3, round(min(高, 宽) * 0.01))
    一圈 = np.concatenate(
        [
            item.image[:带].ravel(),
            item.image[-带:].ravel(),
            item.image[:, :带].ravel(),
            item.image[:, -带:].ravel(),
        ]
    )
    暗 = float(np.mean(一圈 < 180))
    assert 暗 < 0.05, f"最外一圈有 {暗 * 100:.0f}% 是暗的，像是桌面没刷干净"


def test_不再过曝() -> None:
    """用户反馈"处理得有点过曝"：原来的去底链路把人像和印章一起冲成白的。

    盯的是**人像有没有被冲白**（合成卡片上那块 150 的灰当人像）：
    背景提亮可以，人像必须留住层次，否则复印件上就是一张白脸。
    """
    item = cards.prepare_card(synth.card_photo(seed=10), "id", check_flip=False)
    高, 宽 = item.image.shape[:2]
    人像 = item.image[int(高 * 0.20) : int(高 * 0.80), int(宽 * 0.06) : int(宽 * 0.30)]
    assert 人像.mean() < 200, f"人像被冲白了（均值 {人像.mean():.0f}）"
    assert 人像.std() > 10, f"人像糊成一片了（标准差 {人像.std():.0f}）"
    assert np.percentile(item.image, 2) < 60, "字应该还是实心黑"


# ── 深浅滑块（用户反馈"处理得有点过度，字看不清"）────────────────────
def test_深浅滑块两头真的不一样() -> None:
    """拍照效果差别很大，一个定值伺候不了所有照片，所以界面上给了滑块。

    淡的一头把底纹推到白（字才跳出来），浓的一头留住更多层次。
    """
    plan = cards.analyze_card(synth.card_photo(seed=16), "id", check_flip=False)
    淡 = cards.render_card(plan, 0).image
    浓 = cards.render_card(plan, 100).image
    assert np.mean(淡 >= 250) > np.mean(浓 >= 250) + 0.05, "淡的一头背景该更白"
    assert 淡.mean() > 浓.mean(), "浓的一头整体该更实"
    for img in (淡, 浓):
        assert np.percentile(img, 2) < 60, "两头都得保住黑字"


def test_深浅不影响尺寸和朝向() -> None:
    """滑块只该改深浅。尺寸变了就不是 1:1 了，朝向变了长辈会以为自己点错了。"""
    plan = cards.analyze_card(synth.card_photo(seed=17), "id", check_flip=False)
    件 = [cards.render_card(plan, s) for s in (0, 30, 60, 100)]
    assert {item.image.shape for item in 件} == {件[0].image.shape}
    for item in 件:
        assert item.card_size_mm == pytest.approx((85.6, 54.0), abs=0.01)


def test_算一次就够拖滑块不用重新抠图() -> None:
    """CardPlan 存的是"怎么处理"，贵的那半截（抠图 + OCR 判上下）只跑一次。

    界面就靠这个：拖滑块只重跑 `render_card`（实测 0.3 秒），
    不然每动一格都要等 1–2 秒的 OCR，长辈会以为点了没反应。
    """
    photo = synth.card_photo(seed=18)
    plan = cards.analyze_card(photo, "id", check_flip=False)
    assert plan.preset is not None and plan.preset.key == "id"
    assert plan.cropped is True
    assert plan.image.size < photo.size  # 存的是裁好的那块，不是原照片
    # 同一份 plan 反复渲染，结果要一模一样（朝向、尺寸都不许自己变）
    甲, 乙 = cards.render_card(plan, 40), cards.render_card(plan, 40)
    assert np.array_equal(甲.image, 乙.image)
    assert 甲.card_size_mm == 乙.card_size_mm


def test_默认深浅和配置里的一致() -> None:
    """配置默认值和算法默认值必须是同一个数，不然"没动过滑块"和"刚打开"两种情况出不同的图。"""
    from shop_print.config import CardsConfig

    assert CardsConfig().strength == cards.DEFAULT_STRENGTH


def test_转一下按钮会连尺寸一起转() -> None:
    item = cards.CardItem(
        image=np.full((54, 86), 200, dtype=np.uint8),
        width_mm=88.0,
        height_mm=56.0,
        body_fraction=(0.97, 0.96),
    )
    转过 = cards.rotate_item(item, 1)
    assert (转过.width_mm, 转过.height_mm) == (56.0, 88.0)
    assert 转过.body_fraction == (0.96, 0.97)  # 本体占比也得跟着换方向
    assert 转过.card_size_mm == pytest.approx((56.0 * 0.96, 88.0 * 0.97))
    assert 转过.image.shape[:2] == item.image.shape[:2][::-1]
    assert cards.rotate_item(item, 4) is item  # 转回原样就别白折腾


def test_卡片矩形会被收到证件真实的长宽比上() -> None:
    """四条边总有一条量偏几个像素，比例就差 1%–2%，尺寸和涂白都跟着偏。
    既然知道证件的真实长宽比，就用它把矩形收准（只收不放，免得把桌面框回来）。"""
    item = cards.prepare_card(synth.card_photo(seed=15), "id", check_flip=False)
    高, 宽 = item.image.shape[:2]
    本体宽 = 宽 * item.body_fraction[0]
    本体高 = 高 * item.body_fraction[1]
    assert 本体宽 / 本体高 == pytest.approx(85.6 / 54.0, rel=0.005)


def test_灰度图也能抠出卡片() -> None:
    """扫描件、黑白照没有饱和度信息，掩膜要退回只看亮度。"""
    import cv2

    photo = cv2.cvtColor(
        cv2.cvtColor(synth.card_photo(seed=11), cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR
    )
    assert cards.crop_card(photo).cropped is True


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


# ── 彩色（用户反馈：证件也要能出彩色 PDF）─────────────────────────
def test_默认黑白_彩色要自己开() -> None:
    plan = cards.analyze_card(synth.card_photo(seed=19), "id", check_flip=False)
    assert cards.render_card(plan).image.ndim == 2
    assert cards.render_card(plan, color=True).image.ndim == 3


def test_彩色不影响尺寸和朝向() -> None:
    """颜色只该改颜色。尺寸变了就不是 1:1 了，朝向变了长辈会以为自己点错了。"""
    plan = cards.analyze_card(synth.card_photo(seed=20), "id", check_flip=False)
    黑白, 彩色 = cards.render_card(plan), cards.render_card(plan, color=True)
    assert 彩色.image.shape[:2] == 黑白.image.shape[:2]
    assert 彩色.card_size_mm == pytest.approx(黑白.card_size_mm)
    assert 彩色.card_size_mm == pytest.approx((85.6, 54.0), abs=0.01)


def test_彩色的卡片外面也涂白() -> None:
    """涂白那一步要吃三通道，不然彩色时四角留着桌面色。"""
    item = cards.render_card(
        cards.analyze_card(synth.card_photo(seed=21), "id", check_flip=False), color=True
    )
    高, 宽 = item.image.shape[:2]
    角 = item.image[: max(3, 高 // 40), : max(3, 宽 // 40)]
    assert 角.mean() > 240, f"左上角应该是白的，实际 {角.mean():.0f}"


def test_彩色拼出来的PDF是彩色的(tmp_path) -> None:
    """PDF 里存的得是彩色像素 —— 打印那条路仍然是黑白（驱动自己转灰）。"""
    import cv2

    photo = synth.card_photo(seed=22)
    photo[..., 2] = np.clip(photo[..., 2].astype(int) + 60, 0, 255)  # 整张偏红，好检出
    plan = cards.analyze_card(photo, "id", check_flip=False)
    items = [cards.render_card(plan, color=True, label=str(i)) for i in range(2)]
    path, _ = cards.merge_to_pdf(items, tmp_path / "彩色证件.pdf")
    页 = cv2.imdecode(
        np.frombuffer(convert.render_page_png(path, 0, dpi=110), np.uint8), cv2.IMREAD_COLOR
    )
    b, r = 页[..., 0].astype(int), 页[..., 2].astype(int)
    assert float(np.mean(np.abs(r - b) > 25)) > 0.01, "PDF 渲染出来是灰的，颜色丢了"
