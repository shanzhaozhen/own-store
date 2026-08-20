"""自检：`打印助手.exe --self-check`。

为什么要有这个：打包成 `--windowed` 的 exe 之后，出问题什么都看不见。而店铺机上
**没有 Python、没有命令行经验的人**，我们需要一条命令就能回答几个关键问题：

- 模型、字体、样式表这些随包资源真的打进去了吗（`--add-data` 路径写错是最常见的打包事故）
- 这台机器上的打印机叫什么、默认是哪台（柯美 225i 的准确名称就靠这个拿）
- OCR 在这台机器的 CPU 上要跑多久（店铺机比开发机弱得多，得先知道量级）
- 中文字体能不能用（txt 转 PDF 全靠它）

报告同时写到 `%LOCALAPPDATA%\\ShopPrint\\logs\\自检报告.txt`，让父母把这个文件发回来即可。
用法见 docs/07-打包与部署.md。
"""

from __future__ import annotations

import contextlib
import platform
import sys
import time
from pathlib import Path

from . import paths

_OCR_SAMPLE_TEXT = "房屋租赁合同"


class _Report:
    """收集结果。有任何一条致命项失败就返回非零退出码，方便脚本里判断。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = False

    def title(self, text: str) -> None:
        self.lines.append("")
        self.lines.append(f"── {text} ──────────────────────────")

    def item(self, text: str) -> None:
        self.lines.append(f"  {text}")

    def ok(self, text: str) -> None:
        self.item(f"√ {text}")

    def bad(self, text: str, fatal: bool = True) -> None:
        self.item(f"× {text}")
        if fatal:
            self.failed = True

    def dump(self) -> str:
        return "\n".join(self.lines)


def _check_resources(report: _Report) -> None:
    report.title("随包资源")
    report.item(f"资源根目录：{paths.bundle_root()}")

    models = sorted(paths.models_dir().glob("*.onnx"))
    if models:
        total = sum(m.stat().st_size for m in models) / 1024 / 1024
        report.ok(f"OCR 模型 {len(models)} 个，共 {total:.1f} MB")
        for model in models:
            report.item(f"    {model.name}　{model.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        report.bad(f"没有 OCR 模型（{paths.models_dir()}）—— 照片转文字会用不了")

    style = paths.style_file()
    if style.exists():
        report.ok(f"样式表：{style}")
    else:
        report.bad(f"缺样式表：{style}")

    icon = paths.icons_dir() / "app.ico"
    if icon.exists():
        report.ok("图标：app.ico")
    else:
        report.bad("缺图标 app.ico", fatal=False)


def _check_paths(report: _Report) -> None:
    from . import config as config_mod

    cfg = config_mod.load()
    report.title("运行时目录")
    全部就绪 = paths.ensure_runtime_dirs()
    for name, path in (
        ("配置文件", paths.config_file()),
        ("打印记录", paths.history_db()),
        ("缓存", paths.cache_dir()),
        ("日志", paths.log_dir()),
        ("保存到（转好的 Word、证件 PDF 等）", config_mod.save_dir(cfg.output)),
    ):
        report.item(f"{name}：{path}")
    if not 全部就绪:
        report.bad(f"有目录建不出来（看看 {paths.data_dir()} 是不是被同名文件占了）", fatal=False)
    工作区 = config_mod.workspace_dir(cfg.intake)
    if 工作区.is_dir():
        report.ok(f"工作区文件夹：{工作区}")
    else:
        report.bad(f"建不了工作区文件夹：{工作区}", fatal=False)


def _check_printers(report: _Report) -> None:
    report.title("打印机")
    from .core import printing

    try:
        printers = printing.list_printers()
    except Exception as exc:
        report.bad(f"列不出打印机：{exc}")
        return
    if not printers:
        report.bad("系统里没有任何打印机")
        return
    for info in printers:
        marks = "".join(("★" if info.is_default else " ", "（离线）" if info.offline else ""))
        report.item(f"{marks} {info.name}")
    柯美 = printing.find_printer("225") or printing.find_printer("konica")
    if 柯美:
        report.ok(f"找到柯美：{柯美}")
        说法 = {True: "支持", False: "不支持", None: "问不出来（按用户选的来）"}
        report.item(f"    自动双面：{说法[printing.duplex_support(柯美)]}")
    else:
        report.item("没找到名字带 225 / KONICA 的打印机（开发机上正常，店铺机上不正常）")


def _check_font(report: _Report) -> None:
    report.title("中文字体（txt 转 PDF 要用）")
    import tempfile

    from .core import convert

    try:
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "自检.txt"
            src.write_text("房屋租赁合同\n甲乙双方经友好协商。\n", encoding="utf-8")
            out = convert.text_to_pdf(src, Path(folder) / "自检.pdf", convert.ConvertOptions())
            import pymupdf

            with pymupdf.open(out) as doc:
                写进去了 = "房屋租赁合同" in doc[0].get_text()
        if 写进去了:
            report.ok("中文能正常写进 PDF")
        else:
            report.bad("PDF 里读不回中文 —— 字体有问题，打出来会是乱码或空白")
    except Exception as exc:
        report.bad(f"txt 转 PDF 失败：{exc}")


def _check_ocr(report: _Report) -> None:
    report.title("OCR（照片转文字）")
    import tempfile

    import pymupdf

    from .core import ocr

    try:
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "自检.png"
            document = pymupdf.open()
            page = document.new_page(width=595, height=842)
            font = pymupdf.Font("china-s")
            page.insert_font(fontname="cjk", fontbuffer=font.buffer)
            page.insert_text((70, 120), _OCR_SAMPLE_TEXT, fontname="cjk", fontsize=20)
            page.get_pixmap(dpi=200).save(image_path)
            document.close()

            开始 = time.perf_counter()
            result = ocr.recognize_file(image_path)
            用时 = time.perf_counter() - 开始

        if _OCR_SAMPLE_TEXT[:2] in result.text:
            report.ok(f"识别正常，用时 {用时:.1f} 秒（识别到「{result.text.strip()[:20]}」）")
        else:
            report.bad(
                f"识别不出预期文字（用时 {用时:.1f} 秒，结果「{result.text.strip()[:30]}」）"
            )
        if 用时 > 8:
            report.item("    ⚠ 这台机器上 OCR 偏慢，界面上要让长辈看到进度条别以为卡死")
    except Exception as exc:
        report.bad(f"OCR 跑不起来：{exc}")


def run() -> int:
    report = _Report()
    report.lines.append(f"打印助手 自检报告　{time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.item(f"机器：{platform.node()}　{platform.platform()}")
    report.item(f"Python {platform.python_version()}　打包运行：{getattr(sys, 'frozen', False)}")
    report.item(f"程序位置：{Path(sys.executable).parent}")

    _check_resources(report)
    _check_paths(report)
    _check_printers(report)
    _check_font(report)
    _check_ocr(report)

    report.lines.append("")
    report.lines.append("全部通过。" if not report.failed else "有检查没通过，看上面打 × 的几行。")

    text = report.dump()
    target = paths.log_dir() / "自检报告.txt"
    try:
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        text += f"\n\n报告已存到：{target}\n（把这个文件发给开发者就行）"
    except OSError:
        text += "\n\n（报告写不进日志目录）"

    # 打包成 --windowed 之后没有控制台，但父进程重定向过 stdout 时仍然能写出来。
    # 顺手把编码钉成 utf-8：不然重定向到文件时会按系统代码页写，中文变乱码。
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    with contextlib.suppress(Exception):
        print(text)
    return 1 if report.failed else 0
