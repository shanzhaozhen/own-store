"""照片转成文字文档。

识别不可能 100% 准，界面上要诚实说出来（见 docs/05-OCR与版面重建.md）：
- 结果是可编辑的，长辈能直接改错字
- 置信度低的段落标红，提示核对
- 文案不说"转换完成"，说"请核对一下文字有没有认错"
"""

from __future__ import annotations

import html
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import paths, texts
from ..config import AppConfig
from ..core import enhance as enhance_mod
from ..core import ocr, ocr_cloud
from .page_base import SubPage
from .widgets.big import PrimaryButton
from .widgets.preview import ImagePreview
from .workers import run_async

logger = logging.getLogger(__name__)

_LOW_CONFIDENCE_COLOR = "#c8460f"


class OcrPage(SubPage):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.HOME_CARD_OCR_TITLE, parent)
        self._config = config
        self._source: Path | None = None
        self._result: ocr.OcrResult | None = None
        self._original_text = ""

        self._preview = ImagePreview("选好照片之后这里显示原图")
        self._editor = QTextEdit()
        self._editor.setPlaceholderText("识别出来的文字会显示在这里，可以直接修改")

        self._low_confidence_hint = QLabel("")
        self._low_confidence_hint.setProperty("role", "warn")
        self._low_confidence_hint.setWordWrap(True)
        self._low_confidence_hint.hide()

        self._save_button = PrimaryButton("保存成 Word")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._save)

        self._open_folder = QPushButton(texts.BTN_OPEN_FOLDER)
        self._open_folder.clicked.connect(lambda: open_in_explorer(paths.output_dir()))

        self._cloud_button = QPushButton("高精度识别")
        self._cloud_button.clicked.connect(self._recognize_cloud)
        self.sync_cloud_button()

        middle = QHBoxLayout()
        middle.setSpacing(20)
        middle.addWidget(self._preview, stretch=2)

        right = QVBoxLayout()
        right.setSpacing(10)
        caption = QLabel("识别出来的文字（可以直接改）")
        caption.setProperty("role", "section")
        right.addWidget(caption)
        right.addWidget(self._editor, stretch=1)
        right.addWidget(self._low_confidence_hint)
        middle.addLayout(right, stretch=3)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addWidget(self._open_folder, stretch=1)
        buttons.addWidget(self._cloud_button, stretch=1)
        buttons.addWidget(self._save_button, stretch=3)

        self.body.addLayout(middle, stretch=1)
        self.body.addLayout(buttons)

    def sync_cloud_button(self) -> None:
        """云端没配好就把「高精度识别」置灰，并说明原因。设置改完之后要再调一次。"""
        configured = ocr_cloud.is_configured(self._config.ocr)
        self._cloud_button.setEnabled(configured)
        self._cloud_button.setToolTip(
            "" if configured else "还没有设置高精度识别（表格、多栏排版时更准）"
        )

    # ── 外部入口 ────────────────────────────────────────────────
    def load(self, path: Path) -> None:
        self._source = Path(path)
        self._result = None
        self.set_title(f"{texts.HOME_CARD_OCR_TITLE} —— {self._source.name}")
        self._editor.clear()
        self._low_confidence_hint.hide()
        self._save_button.setEnabled(False)

        run_async(
            enhance_mod.load_image,
            self._source,
            on_done=self._preview.set_array,
            on_failed=self.show_error,
        )
        self.show_busy(texts.BUSY_RECOGNIZING)
        run_async(
            ocr.recognize_file,
            self._source,
            on_done=self._on_recognized,
            on_failed=self.show_error,
        )

    def _on_recognized(self, result: ocr.OcrResult) -> None:
        self._result = result
        if result.is_empty:
            self.show_error("这张照片上没认出文字，可能太模糊了。\n请重新拍一张亮一点、平一点的。")
            return
        self._editor.setHtml(self._to_html(result))
        self._original_text = self._editor.toPlainText()
        self._save_button.setEnabled(True)
        self.show_done(texts.DONE_OCR)

        low = result.low_confidence_lines
        if low:
            self._low_confidence_hint.setText(f"{texts.OCR_LOW_CONFIDENCE_HINT}（{len(low)} 处）")
            self._low_confidence_hint.show()
        else:
            self._low_confidence_hint.hide()

    @staticmethod
    def _to_html(result: ocr.OcrResult) -> str:
        parts: list[str] = []
        for paragraph in result.paragraphs:
            text = html.escape(paragraph.text)
            if paragraph.min_score < ocr.LOW_CONFIDENCE:
                text = f'<span style="color:{_LOW_CONFIDENCE_COLOR}">{text}</span>'
            if paragraph.is_heading:
                parts.append(f'<p align="center"><b>{text}</b></p>')
            else:
                parts.append(f"<p>{text}</p>")
        return "".join(parts)

    # ── 保存 ────────────────────────────────────────────────────
    def _save(self) -> None:
        """另存为：让长辈自己选存哪儿（默认在"我的文档"），文件名默认用原图名。

        原来是悄悄存到 %LOCALAPPDATA% 下面的 output 目录 —— 那个路径长辈根本
        找不到，只能靠「打开文件夹」按钮。改成标准的另存为对话框更直观。
        """
        if self._source is None or self._result is None:
            return
        默认目录 = self._config.ocr.last_save_dir or str(Path.home() / "Documents")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "另存为 Word 文档",
            str(Path(默认目录) / f"{self._source.stem}.docx"),
            "Word 文档 (*.docx)",
        )
        if not target:
            return
        目标 = Path(target)
        self._config.ocr.last_save_dir = str(目标.parent)

        edited = self._editor.toPlainText()
        result = (
            self._result
            if edited.strip() == self._original_text.strip()
            else _rebuild_result(edited, self._result)
        )
        改过了 = result is not self._result
        self._save_button.setEnabled(False)
        self.show_busy("正在生成 Word…")
        run_async(
            self._do_save,
            result,
            目标,
            改过了,
            on_done=self._on_saved,
            on_failed=self._on_save_failed,
        )

    @staticmethod
    def _do_save(result: ocr.OcrResult, target: Path, 改过了: bool) -> Path:
        """默认生成**保留原有排版**的 Word（文字在文本框里，位置照着原图）。

        用户在界面上改过文字之后，坐标就不可信了（重建出来的段落是按行堆的），
        这时候退回顺排的普通段落 —— 保排版的前提是坐标还对得上。
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        # 改过文字 → 顺排；没改过 → 保留原有排版（文字放在按原位置摆的文本框里）
        docx_path = ocr.to_docx(result, target) if 改过了 else ocr.to_docx_layout(result, target)
        ocr.to_txt(result, target.with_suffix(".txt"))
        return docx_path

    def _on_saved(self, docx_path: Path) -> None:
        self._save_button.setEnabled(True)
        self.show_done(f"已经保存好了 ✓\n{docx_path}")

    def _on_save_failed(self, message: str) -> None:
        self._save_button.setEnabled(True)
        self.show_error(message)

    def _recognize_cloud(self) -> None:
        """云端高精度。v1 没有注册任何 provider，所以按钮是灰的，这里只是留好接线。"""
        if self._source is None:
            return
        self.show_busy(texts.BUSY_RECOGNIZING)
        run_async(
            self._do_cloud,
            self._source,
            on_done=self._on_cloud_done,
            on_failed=self.show_error,
        )

    def _do_cloud(self, source: Path) -> Path:
        image = enhance_mod.prepare_for_ocr(enhance_mod.load_image(source))
        markdown = ocr_cloud.recognize(image, self._config.ocr)
        target_dir = paths.output_dir()
        return ocr_cloud.markdown_to_docx(markdown, target_dir / f"{source.stem}-高精度.docx")

    def _on_cloud_done(self, docx_path: Path) -> None:
        self.show_done(f"已经保存好了 ✓\n{docx_path}")


def _rebuild_result(text: str, template: ocr.OcrResult) -> ocr.OcrResult:
    """用户改过文字之后，按行重建段落结构。

    只在第一段文字没被改动时保留"标题"格式 —— 改了就不敢假设它还是标题。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_was_heading = bool(template.paragraphs) and template.paragraphs[0].is_heading
    first_unchanged = (
        bool(lines)
        and bool(template.paragraphs)
        and lines[0] == template.paragraphs[0].text.strip()
    )

    paragraphs: list[ocr.OcrParagraph] = []
    for index, line in enumerate(lines):
        is_heading = index == 0 and first_was_heading and first_unchanged
        paragraphs.append(
            ocr.OcrParagraph(
                lines=[
                    ocr.OcrLine(
                        text=line,
                        score=1.0,
                        x0=0,
                        y0=index * 10,
                        x1=len(line) * 10,
                        y1=index * 10 + 8,
                    )
                ],
                is_heading=is_heading,
                centered=is_heading,
            )
        )
    return ocr.OcrResult(
        paragraphs=paragraphs,
        lines=[],
        page_width=template.page_width,
        page_height=template.page_height,
    )


def open_in_explorer(target: Path) -> None:
    """在资源管理器里打开文件夹。长辈找不到输出文件时点这个。"""
    target.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(target)
        else:  # pragma: no cover
            subprocess.run(["xdg-open", str(target)], check=False)
    except OSError:
        logger.warning("打不开文件夹：%s", target, exc_info=True)
