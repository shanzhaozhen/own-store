"""生成 README / docs 里用的界面插图，存到 docs/images/。

为什么要有这个脚本：README 里的截图会过时，而"界面好不好用"只能靠眼睛看
（`ruff` 和 `pytest` 查不出来）。改完界面跑一次这个脚本，插图和代码就同步了。

**样张是合成的**（`tests/synth.py` 用 PyMuPDF 内置中文字体渲染一份假合同再叠阴影），
绝对不要拿顾客的真实文件截图 —— 那些是身份证、合同、成绩单。

    python scripts/make_screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from tests.synth import card_photo, photographed_text_document  # noqa: E402

from shop_print import config as config_mod  # noqa: E402
from shop_print import paths  # noqa: E402
from shop_print.core import convert  # noqa: E402
from shop_print.core import enhance as enhance_mod  # noqa: E402
from shop_print.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "images"
WINDOW = (1280, 830)
# 对比图缩到这个长边再存：全分辨率有 1.3 MB，插图不值得让仓库背这个体积
_COMPARISON_MAX_SIDE = 1600
# 后台线程（增强预览、OCR）要时间出结果，截图前得等它
_WAIT_STEPS = 120
_WAIT_MS = 50


def _settle(app: QApplication, steps: int = _WAIT_STEPS) -> None:
    for _ in range(steps):
        app.processEvents()
        app.thread().msleep(_WAIT_MS)


def _enhance_comparison(sample: Path) -> None:
    """去底增强的前后对比 —— 这是整个 v1 的核心价值，README 里要放。"""
    original = enhance_mod.load_image(sample)
    result = enhance_mod.enhance(original, enhance_mod.EnhanceOptions(strength=55))
    comparison = enhance_mod.side_by_side(original, result.image)
    enhance_mod.save_image(
        enhance_mod.downscale(comparison, _COMPARISON_MAX_SIDE), OUT / "增强前后对比.png"
    )
    print("已生成 增强前后对比.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    paths.ensure_runtime_dirs()
    paths.ensure_inbox_dir()

    sample_dir = paths.output_dir() / "截图样张"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample = sample_dir / "拍照的合同.png"
    enhance_mod.save_image(photographed_text_document(), sample)
    _enhance_comparison(sample)

    # 证件二合一要两张：合成一张"桌面上的身份证"照片，正反两面各一张
    card_front = sample_dir / "身份证-人像面.png"
    card_back = sample_dir / "身份证-国徽面.png"
    enhance_mod.save_image(card_photo(seed=1)[:, :, 0], card_front)
    enhance_mod.save_image(card_photo(seed=2)[:, :, 0], card_back)

    app = QApplication(sys.argv)
    app.setStyleSheet(paths.style_file().read_text(encoding="utf-8"))

    # 往「待打印」里放两个假文件，好让收件页显示真实的文件卡片
    demo = [paths.INBOX_DIR / "身份证正反面.png", paths.INBOX_DIR / "租房合同扫描件.pdf"]
    if paths.INBOX_DIR.is_dir():
        enhance_mod.save_image(photographed_text_document(seed=3), demo[0])
        demo[1].write_bytes(convert.to_pdf(demo[0]).read_bytes())

    window = MainWindow(config_mod.load())
    window.resize(*WINDOW)
    window.show()
    _settle(app, steps=6)

    for name, action in (
        ("首页", window.go_home),
        ("照片变清楚", lambda: window.open_photo(sample)),
        ("照片转文字", lambda: window.open_ocr(sample)),
        ("证件二合一", lambda: window.open_cards([card_front, card_back])),
        ("打印预览", lambda: window.open_print([sample])),
        ("微信收到的文件", window.open_inbox),
    ):
        action()
        _settle(app)
        window.repaint()  # 后台线程刚更新完预览，先重画一次再截，免得截到画一半的界面
        _settle(app, steps=4)
        window.grab().save(str(OUT / f"{name}.png"))
        print(f"已截图 {name}.png")

    # 最坏情况：店铺机可能是 1366×768，窗口只能给到 1024×640
    window.resize(1024, 640)
    window.open_photo(sample)
    _settle(app, steps=60)
    window.grab().save(str(OUT / "小屏-照片变清楚.png"))
    print("已截图 小屏-照片变清楚.png")

    window.close()  # closeEvent 里会停掉目录监控
    _settle(app, steps=10)
    for path in [*demo, sample, card_front, card_back]:
        path.unlink(missing_ok=True)
    print(f"输出目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
