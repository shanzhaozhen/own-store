"""打印预览页：看清纸上是什么样 → 选打印机和份数 → 开始打印。

预览画的是**整张纸**（纸多大、内容摆在哪、有没有被缩、四周打不到的边在哪），
和真打印共用同一套摆放算式（`core/printing.preview_sheet`）——
预览要是"另一套逻辑画出来的好看图"，那就是在骗人。
一切文件先归一化成 PDF 再预览再打印，见 docs/02-架构与分层.md。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import texts
from ..config import AppConfig
from ..core import convert, history, printing
from ..core.enhance import EnhanceOptions
from .page_base import SubPage
from .widgets.big import ChoiceGroup, NumberStepper, PrimaryButton
from .widgets.preview import PagedPreview
from .workers import run_async

logger = logging.getLogger(__name__)

# 打印机名字可能很长（"KONICA MINOLTA 225i PCL6"），按钮上截短，全名放 tooltip
_NAME_MAX = 22


def _short(name: str) -> str:
    return name if len(name) <= _NAME_MAX else name[: _NAME_MAX - 1] + "…"


class PrintPage(SubPage):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.PRINT_TITLE, parent)
        self._config = config
        self._sources: list[Path] = []
        self._enhance: EnhanceOptions | None = None
        self._actual_size = False
        self._pdf: Path | None = None
        self._pages = 0

        self._preview = PagedPreview("选好文件之后这里显示打印出来的样子")
        self._preview.pageRequested.connect(self._render_page)

        self._file_label = QLabel("")
        self._file_label.setProperty("role", "section")
        self._file_label.setWordWrap(True)

        # 打印机竖着排：名字长，横排三台就挤成一团、字还会被截掉
        self._printers = ChoiceGroup([("", texts.PRINTER_NONE)], "", vertical=True)
        self._printers.changed.connect(self._on_printer_changed)

        self._copies = NumberStepper(1, 99, config.printing.copies)
        self._sides = ChoiceGroup(
            [("single", texts.SIDES_SINGLE), ("double", texts.SIDES_DOUBLE)],
            "double" if config.printing.duplex else "single",
        )
        self._paper = ChoiceGroup([("A4", "A4"), ("A3", "A3")], config.printing.paper)
        self._paper.changed.connect(lambda _: self._refresh_preview())

        self._margin_hint = QLabel("")
        self._margin_hint.setProperty("role", "hint")
        self._margin_hint.setWordWrap(True)
        self._color_hint = QLabel(texts.LABEL_COLOR_DISABLED)
        self._color_hint.setProperty("role", "hint")
        # 只有证件二合一那条路会显示：告诉长辈这一张是按实物尺寸打的
        self._size_hint = QLabel(texts.PRINT_ACTUAL_SIZE)
        self._size_hint.setProperty("role", "ok-small")
        self._size_hint.setWordWrap(True)
        self._size_hint.hide()

        self._print_button = PrimaryButton(texts.BTN_START_PRINT)
        self._print_button.setEnabled(False)
        self._print_button.clicked.connect(self._start_print)

        controls = QVBoxLayout()
        controls.setSpacing(14)
        controls.addWidget(self._file_label)
        controls.addLayout(self._row(texts.LABEL_PRINTER, self._printers))
        controls.addLayout(self._row(texts.LABEL_COPIES, self._copies))
        controls.addLayout(self._row(texts.LABEL_SIDES, self._sides))
        controls.addLayout(self._row(texts.LABEL_PAPER, self._paper))
        controls.addWidget(self._color_hint)
        controls.addWidget(self._margin_hint)
        controls.addWidget(self._size_hint)
        controls.addStretch(1)

        middle = QHBoxLayout()
        middle.setSpacing(20)
        middle.addWidget(self._preview, stretch=3)
        middle.addLayout(controls, stretch=2)

        self.body.addLayout(middle, stretch=1)
        self.body.addWidget(self._print_button)
        self.reload_printers()

    @staticmethod
    def _row(label_text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        label = QLabel(label_text)
        label.setProperty("role", "section")
        label.setMinimumWidth(150)
        row.addWidget(label)
        row.addWidget(widget)
        row.addStretch(1)
        return row

    # ── 打印机 ──────────────────────────────────────────────────
    def reload_printers(self) -> None:
        """重新列打印机。设置里改过、或者插拔了打印机之后调。

        直接列在这一页上，不藏进设置里：店里可能同时有柯美和虚拟打印机，
        长辈要能自己选，而且**得看得见现在要往哪台机器打**。
        """
        try:
            printers = printing.list_printers()
        except Exception:
            logger.warning("列打印机失败", exc_info=True)
            printers = []

        if not printers:
            self._printers.set_options([("", texts.PRINTER_NONE)], "")
            self._printers.setEnabled(False)
            self._margin_hint.setText("")
            return

        options = [(p.name, _short(p.name) + ("（离线）" if p.offline else "")) for p in printers]
        记住的 = self._config.printing.printer
        当前 = 记住的 if any(p.name == 记住的 for p in printers) else printers[0].name
        self._printers.set_options(options, 当前)
        self._printers.setEnabled(True)
        for p in printers:
            self._printers.set_tooltip(p.name, p.name)
        self._config.printing.printer = 当前
        self._refresh_preview()

    def _on_printer_changed(self, name: str) -> None:
        self._config.printing.printer = name
        self._refresh_preview()

    def _current_settings(self, job_name: str = "打印助手") -> printing.PrintSettings:
        return printing.PrintSettings(
            printer=self._printers.current(),
            copies=self._copies.value(),
            duplex=self._sides.current() == "double",
            paper=self._paper.current(),
            dpi=self._config.printing.dpi,
            actual_size=self._actual_size,
            job_name=job_name,
        )

    # ── 外部入口 ────────────────────────────────────────────────
    def load(
        self,
        files: list[Path],
        enhance: EnhanceOptions | None = None,
        actual_size: bool = False,
    ) -> None:
        """接收要打印的文件。多张图片会合成一份 PDF，一张一页。

        enhance 不为空时图片会先过去底增强 —— 「照片变清楚再打印」走这条路，
        全分辨率处理在这里才做（预览阶段只用缩略图）。

        actual_size=True 时按物理尺寸 1:1 打，不缩到可打印区 ——
        证件二合一走这条，尺寸缩了复印件就作废了。
        """
        self._sources = [Path(f) for f in files]
        self._enhance = enhance
        self._actual_size = actual_size
        self._pdf = None
        self._pages = 0
        self._print_button.setEnabled(False)
        self._preview.set_total(0)
        self.reload_printers()
        self._size_hint.setVisible(actual_size)

        if not self._sources:
            self._file_label.setText("")
            self._preview.view.set_message("还没有选文件")
            return

        names = "、".join(f.name for f in self._sources[:3])
        more = f" 等 {len(self._sources)} 个文件" if len(self._sources) > 3 else ""
        self._file_label.setText(f"要打印：{names}{more}")

        self.show_busy(texts.BUSY_CONVERTING)
        run_async(
            self._build_pdf,
            list(self._sources),
            on_done=self._on_pdf_ready,
            on_failed=self.show_error,
        )

    def _build_pdf(self, sources: list[Path]) -> tuple[Path, int]:
        options = convert.ConvertOptions(paper=self._paper.current(), enhance=self._enhance)
        all_images = all(convert.classify(p) == "image" for p in sources)
        if len(sources) > 1 and all_images:
            pdf = convert.images_to_pdf_cached(sources, options)
        else:
            pdf = convert.to_pdf(sources[0], options)
        return pdf, convert.page_count(pdf)

    def _on_pdf_ready(self, result: tuple[Path, int]) -> None:
        self._pdf, self._pages = result
        self.clear_status()
        self._print_button.setEnabled(True)
        self._preview.set_total(self._pages)  # 会回调 _render_page(0)

    def _refresh_preview(self) -> None:
        """换了打印机 / 纸张之后重画预览 —— 页边距和缩放都可能跟着变。"""
        if self._pdf is not None:
            self._render_page(self._preview.current_index())

    def _render_page(self, index: int) -> None:
        """画"这一页在纸上是什么样"。纸边、可打印区虚线、内容位置都按真打印算。"""
        if self._pdf is None:
            return
        run_async(
            printing.preview_sheet,
            self._pdf,
            index,
            self._current_settings(),
            on_done=self._on_preview_ready,
            on_failed=self.show_error,
        )
        run_async(
            printing.paper_metrics,
            self._printers.current(),
            self._paper.current(),
            on_done=self._on_metrics_ready,
            on_failed=lambda _msg: None,  # 拿不到就不显示这行提示，不用打扰长辈
        )

    def _on_preview_ready(self, png: bytes) -> None:
        self._preview.view.set_png(png)

    def _on_metrics_ready(self, metrics: printing.PaperMetrics) -> None:
        self._margin_hint.setText(metrics.margin_note)

    # ── 打印 ────────────────────────────────────────────────────
    def _start_print(self) -> None:
        if self._pdf is None:
            return
        if not self._printers.current():
            self.show_error(texts.friendly_error(texts.ErrorKind.PRINTER_NOT_FOUND))
            return
        self._print_button.setEnabled(False)
        settings = self._current_settings(
            job_name=self._sources[0].name if self._sources else "打印助手"
        )
        self.show_progress(0, self._pages, texts.printing_progress(0, self._pages))
        run_async(
            self._do_print,
            self._pdf,
            settings,
            on_done=lambda pages: self._on_printed(pages, settings),
            on_failed=self._on_print_failed,
            on_progress=lambda current, total: self.show_progress(
                current, total, texts.printing_progress(current, total)
            ),
        )

    @staticmethod
    def _do_print(pdf: Path, settings: printing.PrintSettings, progress=None) -> int:
        return printing.print_pdf(pdf, settings, on_progress=progress)

    def _on_printed(self, pages: int, settings: printing.PrintSettings) -> None:
        self.show_done(texts.DONE_PRINTED)
        self._print_button.setEnabled(True)
        history.record(
            history.PrintJob(
                file_name=self._sources[0].name if self._sources else "",
                pages=pages,
                copies=settings.copies,
                duplex=settings.duplex,
                paper=settings.paper,
                printer=settings.printer or printing.default_printer(),
                amount=pages * settings.copies * self._config.printing.price_per_page,
            )
        )

    def _on_print_failed(self, message: str) -> None:
        self.show_error(message)
        self._print_button.setEnabled(True)
        history.record(
            history.PrintJob(
                file_name=self._sources[0].name if self._sources else "",
                pages=self._pages,
                copies=self._copies.value(),
                ok=False,
                note="打印失败",
            )
        )
