"""生成桌面图标 assets/icons/app.ico。

为什么用脚本画而不是找一个图标文件：图标要能改（长辈说"看不清"就得放大对比度），
而且不能引入授权不明的素材。形状刻意只用三块：蓝底 + 白色打印机 + 吐出来的纸。
在 16×16 下也认得出是"打印"，不用读字。

    python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

蓝 = (22, 119, 255, 255)
白 = (255, 255, 255, 255)
浅灰 = (200, 208, 218, 255)
深灰 = (95, 105, 120, 255)

SIZES = (256, 128, 64, 48, 32, 16)
OUT = Path(__file__).resolve().parents[1] / "src" / "shop_print" / "assets" / "icons" / "app.ico"


def 画一张(size: int) -> Image.Image:
    """按比例画，保证每个尺寸都清晰（不是把大图缩小）。"""
    s = 32.0  # 以 32 为基准设计，再按比例放大
    k = size / s
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def r(*values: float) -> list[float]:
        return [v * k for v in values]

    # 蓝色圆角底
    radius = max(2.0, 6 * k)
    draw.rounded_rectangle(r(1, 1, 31, 31), radius=radius, fill=蓝)

    # 上方：待打印的纸（露出一截）
    draw.rectangle(r(9.5, 4.5, 22.5, 13), fill=白)
    line_w = max(1, round(1.2 * k))
    细节 = size >= 32  # 16×16 下这些线只会糊成一团灰，不如不画
    if 细节:
        for y in (7.0, 9.0, 11.0):
            draw.line(r(11.5, y, 20.5, y), fill=浅灰, width=line_w)

    # 中间：打印机机身。**描一圈蓝边**：不然 16×16 下机身和上下两张纸糊成
    # 一个白团，看不出是打印机。
    draw.rounded_rectangle(
        r(3.5, 13, 28.5, 22.5),
        radius=max(1.0, 2 * k),
        fill=白,
        outline=蓝,
        width=max(1, round(k)),
    )
    draw.rectangle(r(6.5, 15.5, 12, 18), fill=深灰)  # 机身上的深色进纸槽

    # 下方：打印出来的纸
    draw.rectangle(r(9.5, 22.5, 22.5, 28), fill=白)
    if 细节:
        for y in (24.5, 26.5):
            draw.line(r(11.5, y, 20.5, y), fill=浅灰, width=line_w)
    return image


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = [画一张(size) for size in SIZES]
    images[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[1:])
    print(f"已生成：{OUT}（{OUT.stat().st_size / 1024:.1f} KB，{len(SIZES)} 个尺寸）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
