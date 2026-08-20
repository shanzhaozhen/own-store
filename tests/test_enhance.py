"""照片去底增强的测试。

判据不是"像素完全一致"（算法会调参），而是**方向性的性质**：
背景该变白、文字该保持黑、滑块该单调有效。这样调参不会天天挂测试。
"""

from __future__ import annotations

import numpy as np
import pytest

from shop_print.core import enhance

from . import synth


@pytest.fixture(scope="module")
def photo() -> np.ndarray:
    return synth.photographed_document(seed=7)


def _background_mean(gray: np.ndarray) -> float:
    """取上下左右四条边的窄带当背景采样 —— 文档四周是空白页边。"""
    band = max(4, min(gray.shape[:2]) // 40)
    edges = [
        gray[:band, :],
        gray[-band:, :],
        gray[:, :band],
        gray[:, -band:],
    ]
    return float(np.mean([float(np.mean(e)) for e in edges]))


def test_合成图的背景本来就是脏的(photo: np.ndarray) -> None:
    """先确认测试素材真的模拟出了问题，否则后面的断言没有意义。"""
    assert _background_mean(photo[:, :, 0]) < 210


def test_文字模式把背景压成白底(photo: np.ndarray) -> None:
    result = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, deskew=False))
    assert _background_mean(result.image) > 245


def test_文字模式输出是二值(photo: np.ndarray) -> None:
    result = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, deskew=False))
    assert set(np.unique(result.image)).issubset({0, 255})


def test_自动模式认出这是文字文档(photo: np.ndarray) -> None:
    result = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_AUTO, deskew=False))
    assert result.mode_used == enhance.MODE_TEXT


def test_混排模式不二值化但也能提亮背景(photo: np.ndarray) -> None:
    result = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_MIXED, deskew=False))
    assert result.mode_used == enhance.MODE_MIXED
    assert len(np.unique(result.image)) > 32  # 保留了灰阶层次
    assert _background_mean(result.image) > 235


def test_去阴影让背景变均匀(photo: np.ndarray) -> None:
    """核心指标：光照拉平之后，背景左右两侧的亮度差应该大幅缩小。"""
    gray = photo[:, :, 0]
    flat = enhance.flatten_illumination(gray)
    band = gray.shape[1] // 10

    def spread(img: np.ndarray) -> float:
        top = img[: img.shape[0] // 20, :]
        return abs(float(np.mean(top[:, :band])) - float(np.mean(top[:, -band:])))

    assert spread(flat) < spread(gray) / 3


def test_强度滑块单调有效() -> None:
    """往"浓"拉，黑色像素应该变多；这是长辈唯一会用的调节手段，不能反向。

    用带真字的样张而不是黑条合成图：黑条图是完美的双峰（要么纯黑要么纯白），
    每个像素都没有歧义，滑块**本来就该没有效果** —— 纯黑的条没法更黑。
    滑块真正起作用的地方是笔画边缘和淡字这些中间灰，只有真字样张才有。
    """
    photo = synth.photographed_text_document()

    def ink(strength: int) -> int:
        result = enhance.enhance(
            photo,
            enhance.EnhanceOptions(mode=enhance.MODE_TEXT, strength=strength, deskew=False),
        )
        return int(np.count_nonzero(result.image == 0))

    light, mid, heavy = ink(0), ink(50), ink(100)
    assert light <= mid <= heavy
    assert heavy > light * 1.05  # 全程至少差 5%，长辈才看得出区别


def test_预览缩放不改变模式判定(photo: np.ndarray) -> None:
    full = enhance.enhance(photo, enhance.EnhanceOptions(deskew=False))
    preview = enhance.enhance(photo, enhance.EnhanceOptions(deskew=False, max_side=800))
    assert preview.mode_used == full.mode_used
    assert max(preview.image.shape[:2]) <= 800


def test_倾斜校正能把转歪的图转回来() -> None:
    photo = synth.photographed_document(seed=3)
    tilted = enhance.rotate(photo, 6.0)
    estimated = enhance.estimate_skew(enhance._to_gray(tilted))  # noqa: SLF001
    assert estimated != 0.0
    assert abs(abs(estimated) - 6.0) < 2.0


def test_倾斜超过阈值时不乱转() -> None:
    """宁可不转也不能转坏 —— 长辈没法判断"这张是不是被弄歪了"。"""
    photo = synth.photographed_document(seed=4)
    tilted = enhance.rotate(photo, 40.0)
    assert enhance.estimate_skew(enhance._to_gray(tilted)) == 0.0  # noqa: SLF001


def test_照片模式保留层次不做去阴影(photo: np.ndarray) -> None:
    result = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_PHOTO, deskew=False))
    assert result.mode_used == enhance.MODE_PHOTO
    # 照片的明暗本身是内容，不该被拉平成白底
    assert _background_mean(result.image) < 245


def test_强度参数映射方向没搞反() -> None:
    light = enhance.strength_params(0)
    heavy = enhance.strength_params(100)
    # adaptiveThreshold 的 C 是反向的：越小越黑
    assert heavy.threshold_c < light.threshold_c
    assert heavy.ink_ceiling > light.ink_ceiling
    assert heavy.gamma > light.gamma
    assert heavy.unsharp_amount > light.unsharp_amount


def test_粗笔画和实心块不会被掏空() -> None:
    """回归测试：自适应阈值窗口太小会把实心区域掏空，只剩一圈轮廓。

    真实文档里的表头底色、实心标题条、印章都会中招，打出来是"空心"的。
    修法见 enhance.binarize（放大 blockSize）和 binarize_clean（ink_ceiling 补实心）。
    """
    gray = synth.make_document(seed=5)
    h, w = gray.shape
    y0 = int(h * 0.45)
    y1 = y0 + max(20, h // 60)  # 明显粗于普通笔画的实心条
    gray[y0:y1, int(w * 0.15) : int(w * 0.85)] = 15

    dirty = synth.add_noise(synth.add_paper_tint(synth.add_shadow(gray)), seed=5)
    photo = np.dstack([dirty, dirty, dirty])
    result = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, deskew=False))

    band = result.image[(y0 + y1) // 2, int(w * 0.25) : int(w * 0.75)]
    solid_ratio = np.count_nonzero(band == 0) / band.size
    assert solid_ratio > 0.98, f"实心条被掏空了，只剩 {solid_ratio:.0%} 是黑的"


def test_去杂点不会删掉标点() -> None:
    """despeckle 的阈值要小于标点的面积，否则句号逗号会消失。"""
    binary = np.full((1650, 1200), 255, dtype=np.uint8)
    binary[100:104, 100:104] = 0  # 4x4 = 16px²，相当于图宽 1200px 上的句号
    binary[200:202, 200:201] = 0  # 2x1 = 2px²，纯噪点
    cleaned = enhance.despeckle(binary)
    assert np.count_nonzero(cleaned[100:104, 100:104] == 0) == 16
    assert np.count_nonzero(cleaned[200:202, 200:201] == 0) == 0


# ── 不许"整页发黑"（下面几条是真实事故的回归测试）──────────────
def _拍成照片(clean: np.ndarray, shadow: float = 0.5, seed: int = 0) -> np.ndarray:
    """把干净渲染变成"手机拍的"：阴影 + 纸张底色 + 噪点。"""
    dirty = synth.add_noise(
        synth.add_paper_tint(synth.add_shadow(clean, strength=shadow)), seed=seed
    )
    return np.dstack([dirty, dirty, dirty])


def _假墨与漏墨(clean: np.ndarray, binary: np.ndarray) -> tuple[int, int]:
    """拿干净渲染当标准答案，数(不该黑的黑了, 该黑的没黑)。

    只看"背景够不够白"抓不住这类事故：黑块可能落在页面中间而不是四条边上。
    """
    import cv2

    ink_truth = (clean < 128).astype(np.uint8)
    tolerance = cv2.dilate(ink_truth, np.ones((5, 5), np.uint8))  # 抗锯齿边缘留余量
    ink = (binary == 0).astype(np.uint8)
    return int((ink & (1 - tolerance)).sum()), int((ink_truth & (1 - ink)).sum())


@pytest.mark.parametrize("strength", [0, 50, 100])
def test_满页文字在任何强度下都不会整页发黑(strength: int) -> None:
    clean = synth.text_document()
    result = enhance.enhance(
        _拍成照片(clean),
        enhance.EnhanceOptions(mode=enhance.MODE_TEXT, strength=strength, deskew=False),
    )
    假墨, 漏墨 = _假墨与漏墨(clean, result.image)
    真墨 = int((clean < 128).sum())
    assert 假墨 < 真墨 * 0.05, f"强度{strength}：{假墨} 个像素不该是黑的（真墨 {真墨}）"
    assert 漏墨 < 真墨 * 0.10, f"强度{strength}：丢了 {漏墨} 个墨点"


@pytest.mark.parametrize("strength", [0, 50, 100])
def test_只有几行字的稀疏页也不会发黑(strength: int) -> None:
    """真实事故：证明、收据这类页面墨只占 0.5%，低百分位会切进纸的分布，
    lo 被当成纸的灰度，拉伸后半页变黑（最坏实测 67 万像素）。
    修法见 stretch_contrast 的黑点上限。"""
    clean = synth.text_document()
    clean[int(clean.shape[0] * 0.2) :] = 255  # 只留最上面几行
    result = enhance.enhance(
        _拍成照片(clean),
        enhance.EnhanceOptions(mode=enhance.MODE_TEXT, strength=strength, deskew=False),
    )
    假墨, _ = _假墨与漏墨(clean, result.image)
    assert 假墨 < 5000, f"强度{strength}：{假墨} 个像素不该是黑的"


def test_黑点上限挡住了纸被当成墨() -> None:
    """整张几乎全白的纸：低百分位一定落在纸里，这时不能把纸拉黑。"""
    paper = np.full((600, 800), 240, dtype=np.uint8)
    paper[10:20, 10:400] = 30  # 一小条字，占比远小于 1%
    out = enhance.stretch_contrast(paper, clip_low=2.0, clip_high=99.5)
    assert out[300:400, 300:400].mean() > 200  # 空白处还是纸
    assert out[10:20, 10:400].mean() < 80  # 字还是黑的


# ── 认出整张 A4/A3 纸就裁正拉平（用户反馈的第 2 条）───────────────
def test_认出整张纸并按标准比例拉平() -> None:
    """A4 和 A3 的长宽比是同一个数（√2）。既然认出是标准纸，
    透视校正之后残留的那一两个百分点也该按已知比例修掉。"""
    result = enhance.enhance(
        synth.paper_photo(seed=1), enhance.EnhanceOptions(mode=enhance.MODE_TEXT)
    )
    assert result.paper == enhance.PAPER_A4_A3
    assert result.cropped is True
    高, 宽 = result.image.shape[:2]
    assert max(高, 宽) / min(高, 宽) == pytest.approx(enhance.PAPER_RATIO, rel=0.005)


def test_没有纸就不硬裁() -> None:
    """整幅都是文档（没有桌面边界）时认不出纸，这时不能瞎裁。"""
    result = enhance.enhance(
        synth.photographed_text_document(), enhance.EnhanceOptions(mode=enhance.MODE_TEXT)
    )
    assert result.paper == ""


def test_按比例拉平只动一条边() -> None:
    """长边不动、短边跟着算：不丢分辨率，也不会把内容拉扁。"""
    图 = np.full((1400, 1000, 3), 200, dtype=np.uint8)  # 比例 1.40，差一点点
    出 = enhance.snap_to_paper_ratio(图)
    assert 出.shape[0] == 1400
    assert 出.shape[1] == pytest.approx(round(1400 / enhance.PAPER_RATIO), abs=1)


# ── 黑白 / 彩色（用户反馈：红章蓝笔要留住）─────────────────────────
def test_默认出黑白_彩色要自己开() -> None:
    photo = synth.paper_photo(seed=2, stamp=True)
    assert enhance.enhance(photo, enhance.EnhanceOptions()).image.ndim == 2
    assert enhance.enhance(photo, enhance.EnhanceOptions(color=True)).image.ndim == 3


def test_彩色模式留住红章_黑白模式不留() -> None:
    """红色印章是复印件上最常见的彩色内容。彩色那条路必须保住它的颜色。"""
    photo = synth.paper_photo(seed=3, stamp=True)
    彩 = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, color=True)).image
    高, 宽 = 彩.shape[:2]
    块 = 彩[int(高 * 0.70) : int(高 * 0.94), int(宽 * 0.60) : int(宽 * 0.84)]
    偏红 = (块[..., 2].astype(int) - 块[..., 0].astype(int)) > 40
    assert 偏红.mean() > 0.02, f"红章没留住（偏红像素只占 {偏红.mean() * 100:.1f}%）"


def test_彩色模式的纸一样是白的() -> None:
    """彩色不代表脏：背景照样要压白，不然打出来一片灰。"""
    result = enhance.enhance(
        synth.paper_photo(seed=4), enhance.EnhanceOptions(mode=enhance.MODE_TEXT, color=True)
    )
    亮度 = result.image.max(axis=2)
    assert float(np.mean(亮度 > 245)) > 0.5


def test_彩色不二值化_灰阶要留着() -> None:
    """二值化只剩黑白两色，彩色那条路必须绕开它。"""
    result = enhance.enhance(
        synth.paper_photo(seed=5, stamp=True),
        enhance.EnhanceOptions(mode=enhance.MODE_TEXT, color=True),
    )
    assert len(np.unique(result.image)) > 32


# ── 裁剪边缘可调（用户反馈"有时候裁剪得太过了"）─────────────────────
def test_边缘往外放能多留一圈() -> None:
    """同一张照片，边缘往外放之后裁出来的图应该更大（多留了纸边和一点桌面）。"""
    photo = synth.paper_photo(seed=6)
    紧 = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, crop_margin=-0.03))
    正常 = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT))
    松 = enhance.enhance(photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, crop_margin=0.04))
    assert 紧.image.shape[0] < 正常.image.shape[0] < 松.image.shape[0]
    # 三种都还是认出的那张纸、还是标准比例
    for result in (紧, 正常, 松):
        assert result.paper == enhance.PAPER_A4_A3
        高, 宽 = result.image.shape[:2]
        assert max(高, 宽) / min(高, 宽) == pytest.approx(enhance.PAPER_RATIO, rel=0.01)


def test_不裁就一点不动尺寸() -> None:
    """「不裁」是给"裁坏了"兜底的：整幅原样处理，尺寸和原图一致。"""
    photo = synth.paper_photo(seed=7)
    result = enhance.enhance(
        photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, deskew=False, crop_margin=0.05)
    )
    assert result.cropped is False
    assert result.paper == ""
    assert result.image.shape[:2] == photo.shape[:2]


# ── 彩色还原原色（用户反馈"尽量还原文件的原色"）─────────────────────
def test_暖光下拍的纸也修成白的() -> None:
    """同一份文件在暖光和正常光下拍，处理完的纸应该都是白的、颜色也该接近 ——
    这才叫"还原原色"（白平衡是全局三个增益，不会造成局部伪色）。"""
    正常 = synth.paper_photo(seed=8, stamp=True)
    暖光 = np.clip(正常.astype(np.float32) * np.float32([0.78, 0.92, 1.0]), 0, 255).astype(np.uint8)
    出正常, 出暖光 = (
        enhance.enhance(img, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, color=True)).image
        for img in (正常, 暖光)
    )
    for out in (出正常, 出暖光):
        纸 = out[out.min(axis=2) > 200]  # 亮的那一片就是纸
        assert 纸.size > 0
        assert float(np.ptp(纸.reshape(-1, 3).mean(axis=0))) < 8, "纸还带着色偏"


def test_彩色不会把红章压成近黑() -> None:
    """踩过的坑：黑白那套对比拉伸 + gamma 用在彩色上，章的亮度 209 → 26。
    彩色链路对"有颜色"的像素走轻口味，亮度必须保住。"""
    import cv2

    photo = synth.paper_photo(seed=9, stamp=True)
    hsv0 = cv2.cvtColor(photo, cv2.COLOR_BGR2HSV)
    章 = (hsv0[..., 1] > 120) & (hsv0[..., 2] > 60)
    assert 章.sum() > 500
    out = enhance.enhance(
        photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, color=True, deskew=False)
    ).image
    原亮 = float(hsv0[..., 2][章].mean())
    新亮 = float(cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 2][章].mean())
    assert 新亮 > 原亮 * 0.75, f"章被压暗了：{原亮:.0f} → {新亮:.0f}"
    # 色相不许漂：红还是红
    原色相 = float(hsv0[..., 0][章].mean())
    新色相 = float(cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 0][章].mean())
    assert abs(新色相 - 原色相) < 8, f"色相漂了：{原色相:.1f} → {新色相:.1f}"


def test_彩色时黑字还是黑的() -> None:
    """彩色不能变成"什么都不处理"：没颜色的像素照样走重口味，字要够黑。"""
    import cv2

    photo = synth.paper_photo(seed=10)
    hsv0 = cv2.cvtColor(photo, cv2.COLOR_BGR2HSV)
    字 = hsv0[..., 2] < np.percentile(hsv0[..., 2], 3)
    out = enhance.enhance(
        photo, enhance.EnhanceOptions(mode=enhance.MODE_TEXT, color=True, deskew=False)
    ).image
    assert float(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)[字].mean()) < 120
