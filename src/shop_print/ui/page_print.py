"""打印文档页：选文件 → 预览 → 开始打印。

一切文件先归一化成 PDF 再预览再打印，所以顾客看到的预览和打出来的纸
一定是同一份数据。见 docs/02-架构与分层.md。
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


class PrintPage(SubPage):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.HOME_CARD_PRINT_TITLE, parent)
        self._config = config
        self._sources: list[Path] = []
        self._enhance: EnhanceOptions | None = None
        self._actual_size = False
        self._pdf: Path | None = None
        self._pages = 0

        self._preview = PagedPreview("选好文件之后这里会显示打印效果")
        self._preview.pageRequested.connect(self._render_page)

        self._file_label = QLabel("")
        self._file_label.setProperty("role", "section")
        self._file_label.setWordWrap(True)

        self._copies = NumberStepper(1, 99, config.printing.copies)
        self._sides = ChoiceGroup(
            [("single", texts.SIDES_SINGLE), ("double", texts.SIDES_DOUBLE)],
            "double" if config.printing.duplex else "single",
        )
        self._paper = ChoiceGroup([("A4", "A4"), ("A3", "A3")], config.printing.paper)

        self._printer_label = QLabel("")
        self._printer_label.setProperty("role", "hint")
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
        controls.setSpacing(16)
        controls.addWidget(self._file_label)
        controls.addLayout(self._row(texts.LABEL_COPIES, self._copies))
        controls.addLayout(self._row(texts.LABEL_SIDES, self._sides))
        controls.addLayout(self._row(texts.LABEL_PAPER, self._paper))
        controls.addWidget(self._printer_label)
        controls.addWidget(self._color_hint)
        controls.addWidget(self._size_hint)
        controls.addStretch(1)

        middle = QHBoxLayout()
        middle.setSpacing(20)
        middle.addWidget(self._preview, stretch=3)
        middle.addLayout(controls, stretch=2)

        self.body.addLayout(middle, stretch=1)
        self.body.addWidget(self._print_button)
        self._refresh_printer_label()

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

    def _refresh_printer_label(self) -> None:
        try:
            name = self._config.printing.printer or printing.default_printer()
        except Exception:
            name = ""
        self._printer_label.setText(
            f"{texts.LABEL_PRINTER}：{name}" if name else "还没有找到打印机"
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
        self._refresh_printer_label()
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
        self._preview.set_total(self._pages)

    def _render_page(self, index: int) -> None:
        if self._pdf is None:
            return
        run_async(
            convert.render_page_png,
            self._pdf,
            index,
            on_done=self._preview.view.set_png,
            on_failed=self.show_error,
        )

    # ── 打印 ────────────────────────────────────────────────────
    def _start_print(self) -> None:
        if self._pdf is None:
            return
        self._print_button.setEnabled(False)
        settings = printing.PrintSettings(
            printer=self._config.printing.printer,
            copies=self._copies.value(),
            duplex=self._sides.current() == "double",
            paper=self._paper.current(),
            dpi=self._config.printing.dpi,
            actual_size=self._actual_size,
            job_name=self._sources[0].name if self._sources else "打印助手",
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
