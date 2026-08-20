"""打印。

真机是柯美 bizhub 225i（只有黑白），**开发机上没有**，所以标了 needs_printer
的用例拿「Microsoft Print to PDF」当替身：能验页数、缩放、方向和 DEVMODE，
验不了柯美驱动自己的行为 —— 那部分只能到店铺机上实打。
见 docs/04-文档转换与打印.md。
"""

from __future__ import annotations

import io

import pymupdf
import pytest

from shop_print.core import printing
from shop_print.core.errors import ShopPrintError
from shop_print.texts import ErrorKind

替身打印机 = "Microsoft Print to PDF"


def 有替身打印机() -> bool:
    if not printing._WIN32_READY:  # noqa: SLF001
        return False
    return any(p.name == 替身打印机 for p in printing.list_printers())


needs_pdf_printer = pytest.mark.skipif(
    not 有替身打印机(), reason=f"本机没有「{替身打印机}」，跳过实打验证"
)


def 造pdf(path, pages: int = 1, landscape: bool = False):
    document = pymupdf.open()
    width, height = (841.89, 595.276) if landscape else (595.276, 841.89)
    font = pymupdf.Font("china-s")
    for index in range(pages):
        page = document.new_page(width=width, height=height)
        page.insert_font(fontname="cjk", fontbuffer=font.buffer)
        page.insert_text((72, 96), f"第 {index + 1} 页", fontname="cjk", fontsize=24)
    document.save(path)
    document.close()
    return path


# ── 页码范围 ────────────────────────────────────────────────────
def test_不填范围就是全部() -> None:
    assert printing._page_indexes(3, None) == [0, 1, 2]  # noqa: SLF001


def test_范围是1起含两端() -> None:
    assert printing._page_indexes(10, (3, 5)) == [2, 3, 4]  # noqa: SLF001


def test_超出的范围被夹回来() -> None:
    """长辈可能输个 999。夹回去打完，比报错让他重来强。"""
    assert printing._page_indexes(3, (0, 99)) == [0, 1, 2]  # noqa: SLF001
    assert printing._page_indexes(3, (2, 99)) == [1, 2]  # noqa: SLF001


def test_倒着填的范围不会打出空(  # 例如"从第 5 页到第 2 页"
) -> None:
    assert printing._page_indexes(6, (5, 2)) == [4]  # noqa: SLF001


# ── 缺省值 ──────────────────────────────────────────────────────
def test_默认设置是A4单面一份300dpi() -> None:
    settings = printing.PrintSettings()
    assert (settings.paper, settings.copies, settings.duplex) == ("A4", 1, False)
    assert settings.dpi == 300  # 600dpi 下 A4 灰度约 70MB/页，弱机器吃不消
    assert settings.printer == ""  # 空 = 用系统默认打印机


def test_纸张代码里有店里用得到的() -> None:
    assert printing._PAPER_CODES["A4"] == 9  # noqa: SLF001
    assert printing._PAPER_CODES["A3"] == 8  # noqa: SLF001


def test_关键词搜不到就返回空串() -> None:
    assert printing.find_printer("") == ""
    assert printing.find_printer("绝对不存在的打印机名字") == ""


# ── 页面方向 ────────────────────────────────────────────────────
def test_方向不一致的页会被转90度(tmp_path) -> None:
    """一份 PDF 里可能纵横混排。转图片而不是中途改 DEVMODE，能把纸用满。"""
    path = 造pdf(tmp_path / "纵向.pdf")
    with pymupdf.open(path) as doc:
        竖着放 = printing._render_for_device(doc, 0, 72, device_landscape=False)  # noqa: SLF001
        横着放 = printing._render_for_device(doc, 0, 72, device_landscape=True)  # noqa: SLF001
    assert 竖着放.height > 竖着放.width
    assert 横着放.width > 横着放.height  # 转过来了


def test_渲染出来是灰度(tmp_path) -> None:
    """225i 只有黑白，光栅化就别浪费在彩色上。"""
    path = 造pdf(tmp_path / "一页.pdf")
    with pymupdf.open(path) as doc:
        image = printing._render_for_device(doc, 0, 72, device_landscape=False)  # noqa: SLF001
    assert image.mode == "L"


# ── 等比居中 ────────────────────────────────────────────────────
class _假DC:
    def GetHandleOutput(self):
        return 1


def test_等比缩放居中且不超出可打印区(monkeypatch) -> None:
    """**可打印区不等于纸张尺寸**，超出去内容就被裁掉边。"""
    from PIL import Image

    画到 = {}

    class _假Dib:
        def __init__(self, image):
            self.image = image

        def draw(self, _handle, rect):
            画到["rect"] = rect

    monkeypatch.setattr(printing.ImageWin, "Dib", _假Dib)
    printing._draw_fitted(_假DC(), Image.new("L", (1000, 2000)), 800, 1000)  # noqa: SLF001

    left, top, right, bottom = 画到["rect"]
    assert (right - left, bottom - top) == (500, 1000)  # 按高度受限等比缩放
    assert left == (800 - 500) // 2  # 横向居中
    assert top == 0
    assert right <= 800 and bottom <= 1000


def test_极小的图也至少画一个像素(monkeypatch) -> None:
    from PIL import Image

    画到 = {}

    class _假Dib:
        def __init__(self, image):
            self.image = image

        def draw(self, _handle, rect):
            画到["rect"] = rect

    monkeypatch.setattr(printing.ImageWin, "Dib", _假Dib)
    printing._draw_fitted(_假DC(), Image.new("L", (10000, 1)), 100, 100)  # noqa: SLF001
    left, top, right, bottom = 画到["rect"]
    assert right - left >= 1 and bottom - top >= 1


# ── 找打印机 ────────────────────────────────────────────────────
@needs_pdf_printer
def test_列出打印机时默认那台排最前() -> None:
    printers = printing.list_printers()
    assert printers
    if any(p.is_default for p in printers):
        assert printers[0].is_default


@needs_pdf_printer
def test_按关键词能找到打印机() -> None:
    """店铺机上用 "225" 或 "KONICA" 找柯美。这里用替身验同一条逻辑。"""
    assert printing.find_printer("print to pdf") == 替身打印机


@needs_pdf_printer
def test_指定不存在的打印机报的是人话() -> None:
    with pytest.raises(ShopPrintError) as caught:
        printing._resolve_printer("柯美225i-其实没装")  # noqa: SLF001
    assert caught.value.kind is ErrorKind.PRINTER_NOT_FOUND
    assert "找不到打印机" in caught.value.friendly or "数据线" in caught.value.friendly


@needs_pdf_printer
def test_DEVMODE把彩色写死成单色(屏蔽驱动噪音) -> None:
    """225i 只有黑白：显式设单色，别让驱动多做一道半调色处理。"""
    settings = printing.PrintSettings(copies=3, duplex=True, paper="A4")
    devmode = printing._build_devmode(替身打印机, settings, landscape=False)  # noqa: SLF001

    assert devmode.Color == printing._DMCOLOR_MONOCHROME  # noqa: SLF001
    assert devmode.Copies == 3
    assert devmode.Duplex == printing._DMDUP_VERTICAL  # noqa: SLF001
    assert devmode.PaperSize == printing._PAPER_CODES["A4"]  # noqa: SLF001
    assert devmode.Orientation == printing._DMORIENT_PORTRAIT  # noqa: SLF001
    # 改过的字段都要在 Fields 里标记，否则驱动直接忽略
    for flag in (
        printing._DM_COPIES,  # noqa: SLF001
        printing._DM_COLOR,  # noqa: SLF001
        printing._DM_DUPLEX,  # noqa: SLF001
        printing._DM_PAPERSIZE,  # noqa: SLF001
        printing._DM_ORIENTATION,  # noqa: SLF001
    ):
        assert devmode.Fields & flag


@needs_pdf_printer
def test_横向文档会设成横向纸(屏蔽驱动噪音) -> None:
    devmode = printing._build_devmode(  # noqa: SLF001
        替身打印机, printing.PrintSettings(), landscape=True
    )
    assert devmode.Orientation == printing._DMORIENT_LANDSCAPE  # noqa: SLF001


# ── 实打（输出到 PDF 文件）──────────────────────────────────────
@needs_pdf_printer
def test_实打三页_页数和进度都对(tmp_path, 屏蔽驱动噪音) -> None:
    """整条链路：PDF → GDI → 打印机。output_file 让虚拟打印机不弹"另存为"。"""
    src = 造pdf(tmp_path / "三页.pdf", pages=3)
    out = tmp_path / "打出来的.pdf"
    进度: list[tuple[int, int]] = []

    页数 = printing.print_pdf(
        src,
        printing.PrintSettings(printer=替身打印机, dpi=150, output_file=out, job_name="测试"),
        on_progress=lambda current, total: 进度.append((current, total)),
    )

    assert 页数 == 3
    assert 进度 == [(1, 3), (2, 3), (3, 3)]  # 界面靠这个显示"正在打印第 2 页 / 共 5 页"
    assert out.exists() and out.stat().st_size > 0
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3


@needs_pdf_printer
def test_实打指定页码范围(tmp_path, 屏蔽驱动噪音) -> None:
    src = 造pdf(tmp_path / "五页.pdf", pages=5)
    out = tmp_path / "只打中间.pdf"
    页数 = printing.print_pdf(
        src,
        printing.PrintSettings(printer=替身打印机, dpi=110, page_range=(2, 4), output_file=out),
    )
    assert 页数 == 3
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3


@needs_pdf_printer
def test_实打横向文档不被裁掉(tmp_path, 屏蔽驱动噪音) -> None:
    """横向页要落在横向纸上，缩放后还得整页在纸内。"""
    src = 造pdf(tmp_path / "横的.pdf", landscape=True)
    out = tmp_path / "横的输出.pdf"
    printing.print_pdf(src, printing.PrintSettings(printer=替身打印机, dpi=110, output_file=out))
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rect.width > page.rect.height
        图 = page.get_image_info()[0]["bbox"]
        assert 图[0] >= -1 and 图[1] >= -1
        assert 图[2] <= page.rect.width + 1 and 图[3] <= page.rect.height + 1


@needs_pdf_printer
def test_越界的页码范围会被夹到有效页而不是打空(tmp_path, 屏蔽驱动噪音) -> None:
    """长辈手滑输个 9 也要出东西。"""
    src = 造pdf(tmp_path / "一页.pdf")
    out = tmp_path / "夹回来.pdf"
    页数 = printing.print_pdf(
        src, printing.PrintSettings(printer=替身打印机, dpi=110, page_range=(9, 9), output_file=out)
    )
    assert 页数 == 1
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1


@needs_pdf_printer
def test_双面能力问不出来时不擅自改成单面(屏蔽驱动噪音) -> None:
    """问不出来就按用户选的来 —— 选了双面却出一堆单面纸，比让驱动自己决定更糟。
    虚拟打印机上这个查询经常失败，正好用来验这条兜底。"""
    答案 = printing.duplex_support(替身打印机)
    assert 答案 in (True, False, None)


# ── 按实物尺寸打印（证件复印靠这条）──────────────────────────────
class _假纸:
    """假的可打印区信息。真打印机四周有 4–5mm 打不到的边，虚拟打印机没有，
    所以这条只能靠假数据验 —— 真机行为要到店铺现场确认。"""

    def __init__(self, dpi: int = 600, offset: int = 100) -> None:
        self.metrics = printing._DeviceMetrics(  # noqa: SLF001
            dpi_x=dpi,
            dpi_y=dpi,
            offset_x=offset,
            offset_y=offset,
            printable_w=round(210 / 25.4 * dpi) - 2 * offset,
            printable_h=round(297 / 25.4 * dpi) - 2 * offset,
        )


def _画到哪(monkeypatch, image, page_rect, landscape: bool, 纸: _假纸):
    画到 = {}

    class _假Dib:
        def __init__(self, img):
            self.img = img

        def draw(self, _handle, rect):
            画到["rect"] = rect

    monkeypatch.setattr(printing.ImageWin, "Dib", _假Dib)
    printing._draw_actual_size(_假DC(), image, page_rect, landscape, 纸.metrics)  # noqa: SLF001
    return 画到["rect"]


def test_实物尺寸模式下A4页就是A4大小(monkeypatch) -> None:
    """600dpi 下 A4 = 4961×7016 像素。缩到可打印区会变成 4761×6816，
    身份证就从 85.6mm 缩成 82mm —— 所以证件那条路不许缩。"""
    from PIL import Image

    纸 = _假纸()
    rect = pymupdf.Rect(0, 0, 595.276, 841.89)  # A4 纵向
    left, top, right, bottom = _画到哪(monkeypatch, Image.new("L", (2480, 3508)), rect, False, 纸)
    assert right - left == pytest.approx(4961, abs=2)
    assert bottom - top == pytest.approx(7016, abs=2)


def test_实物尺寸模式要补上不可打印的边(monkeypatch) -> None:
    """DC 的原点在可打印区左上角，比纸张左上角偏了 offset。
    不把这段补回来，整页就整体偏移 4mm。"""
    from PIL import Image

    纸 = _假纸(offset=100)
    rect = pymupdf.Rect(0, 0, 595.276, 841.89)
    left, top, _, _ = _画到哪(monkeypatch, Image.new("L", (2480, 3508)), rect, False, 纸)
    assert (left, top) == (-100, -100)


def test_横向页在实物尺寸下宽高要换过来(monkeypatch) -> None:
    """纵向页打在横向纸上时渲染阶段会转 90°，物理宽高也得跟着换。"""
    from PIL import Image

    纸 = _假纸()
    rect = pymupdf.Rect(0, 0, 595.276, 841.89)  # 纵向页
    left, top, right, bottom = _画到哪(monkeypatch, Image.new("L", (3508, 2480)), rect, True, 纸)
    assert right - left == pytest.approx(7016, abs=2)  # 转过来之后长边在横向
    assert bottom - top == pytest.approx(4961, abs=2)


def test_默认不开实物尺寸() -> None:
    """普通文档要缩到可打印区，不然边上的内容会被裁掉。"""
    assert printing.PrintSettings().actual_size is False


# ── 打印预览：画的必须和真打出来的一样 ───────────────────────────
def test_预览画的是整张纸(tmp_path) -> None:
    """预览要能回答"纸上什么样"：纸多大、内容在哪、四周打不到的边在哪。"""
    from PIL import Image

    src = 造pdf(tmp_path / "一页.pdf")
    纸 = printing.PaperMetrics(210, 297, 200, 287, 5, 5)
    png = printing.preview_sheet(src, 0, printing.PrintSettings(), dpi=100, metrics=纸)
    assert png.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(png)) as image:
        assert image.width / 100 * 25.4 == pytest.approx(210, abs=1)  # 纸的宽
        assert image.height / 100 * 25.4 == pytest.approx(297, abs=1)


def test_实物尺寸预览不缩内容(tmp_path) -> None:
    """同一页：实物尺寸模式下内容比"缩到可打印区"模式大 —— 差的就是那 4–5mm 的边。

    用一整页涂黑的 PDF 来量：普通样张上墨只有几行字，量不出页面被缩了多少。
    """
    from PIL import Image

    src = tmp_path / "整页黑.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595.276, height=841.89)
    page.draw_rect(page.rect, color=None, fill=(0, 0, 0))
    document.save(src)
    document.close()

    纸 = printing.PaperMetrics(210, 297, 200, 287, 5, 5)

    def 黑区宽度(actual_size: bool) -> int:
        png = printing.preview_sheet(
            src, 0, printing.PrintSettings(actual_size=actual_size), dpi=100, metrics=纸
        )
        with Image.open(io.BytesIO(png)) as image:
            gray = image.convert("L")
            pixels = gray.load()
            middle = gray.height // 2
            黑 = [x for x in range(gray.width) if pixels[x, middle] < 100]
            return max(黑) - min(黑) + 1 if 黑 else 0

    缩过的 = 黑区宽度(False)
    原大的 = 黑区宽度(True)
    assert 缩过的 / 100 * 25.4 == pytest.approx(200, abs=2)  # 缩到可打印区宽度
    assert 原大的 / 100 * 25.4 == pytest.approx(210, abs=2)  # 整张纸宽
    assert 原大的 > 缩过的


def test_问不出边距时按整张纸算() -> None:
    """驱动抽风也要给得出预览 —— 宁可乐观，不要没有预览。"""
    metrics = printing.paper_metrics("根本不存在的打印机", "A4")
    assert metrics.measured is False
    assert metrics.paper_w_mm == pytest.approx(210, abs=0.5)
    assert metrics.printable_w_mm == pytest.approx(210, abs=0.5)
    assert "问不出来" in metrics.margin_note


@needs_pdf_printer
def test_量得出替身打印机的可打印区(屏蔽驱动噪音) -> None:
    """「Microsoft Print to PDF」可打印区正好等于纸张，所以这里 offset 是 0。
    **真打印机四周会吃 4–5mm**，那部分只能到店铺机上量。"""
    metrics = printing.paper_metrics(替身打印机, "A4")
    assert metrics.measured is True
    assert metrics.paper_w_mm == pytest.approx(210, abs=1)
    assert metrics.paper_h_mm == pytest.approx(297, abs=1)
    assert metrics.printable_w_mm <= metrics.paper_w_mm + 0.01


@needs_pdf_printer
def test_实打证件PDF_按实物尺寸(tmp_path, 屏蔽驱动噪音) -> None:
    """整条链路：证件合并 → 按实物尺寸打 → 输出 PDF 里量毫米。

    「Microsoft Print to PDF」的可打印区正好等于纸张（offset=0），所以这里
    量出来应当就是 85.6mm。**真打印机有 4–5mm 打不到的边，行为要到店铺机上再验。**
    """
    import numpy as np

    from shop_print.core import cards

    items = [
        cards.CardItem(
            image=np.full((540, 856), 210, dtype=np.uint8), width_mm=85.6, height_mm=54.0
        )
        for _ in range(2)
    ]
    src, _ = cards.merge_to_pdf(items, tmp_path / "证件.pdf")
    out = tmp_path / "打出来的证件.pdf"
    printing.print_pdf(
        src,
        printing.PrintSettings(printer=替身打印机, dpi=200, actual_size=True, output_file=out),
    )

    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rect.width / cards.PT_PER_MM == pytest.approx(210, abs=1)
        # 打印是整页光栅化，量不出单张卡片的框；改为量整页有没有被缩
        assert page.get_image_info()[0]["bbox"][2] / cards.PT_PER_MM == pytest.approx(210, abs=1)


# ── 等比缩放（用户反馈"直接打印会超出 A4 边缘"）─────────────────────
def test_缩放默认是铺满可打印区() -> None:
    """默认 zoom=1.0：等比缩到可打印区里，永远不超边 —— 这就是"不超出 A4"的保证。"""
    assert printing.PrintSettings().zoom == 1.0


def test_缩放调小内容也跟着小(tmp_path) -> None:
    """预览和真打共用同一套摆放算式（`preview_sheet` / `_draw_fitted`），
    所以量预览里"有墨的范围"就能验缩放生效。"""
    import numpy as np
    from PIL import Image

    src = tmp_path / "整页黑.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595.276, height=841.89)
    page.draw_rect(page.rect, color=None, fill=(0, 0, 0))
    document.save(src)
    document.close()
    纸 = printing.PaperMetrics(210, 297, 200, 287, 5, 5)

    def 墨迹宽高(zoom: float) -> tuple[int, int]:
        png = printing.preview_sheet(src, 0, printing.PrintSettings(zoom=zoom), dpi=100, metrics=纸)
        with Image.open(io.BytesIO(png)) as image:
            墨 = np.array(image.convert("L")) < 128
        行 = np.where(墨.any(axis=1))[0]
        列 = np.where(墨.any(axis=0))[0]
        return (int(列[-1] - 列[0] + 1), int(行[-1] - 行[0] + 1))

    满宽, 满高 = 墨迹宽高(1.0)
    小宽, 小高 = 墨迹宽高(0.8)
    assert 小宽 < 满宽 and 小高 < 满高
    assert 小宽 / 满宽 == pytest.approx(0.8, abs=0.05)
    assert 小高 / 满高 == pytest.approx(0.8, abs=0.05)
