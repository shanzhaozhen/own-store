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
