"""证件印一张纸：身份证正反面、户口本两页拼到一张 A4，按实物尺寸打。

界面上只有三件事：**选类型 → 放两张图 → 开始打印**。
尺寸、方向、排版全自动，长辈不用也不该去调 —— 算法见 core/cards.py。

一条硬规矩：**尺寸不保真就必须说出来**。派出所、银行要的是 1:1 的复印件，
悄悄缩了打出来会被退回重做，比报错更糟。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths, texts
from ..config import AppConfig
from ..core import cards, convert, intake
from ..core import enhance as enhance_mod
from .page_base import SubPage
from .widgets.big import ChoiceGroup, PrimaryButton
from .widgets.preview import ImagePreview
from .workers import run_async

logger = logging.getLogger(__name__)

_PREVIEW_DPI = 96


def _type_options() -> list[tuple[str, str]]:
    options = [(preset.key, preset.name) for preset in cards.PRESETS]
    options.append((cards.AUTO, texts.CARDS_TYPE_AUTO))
    return options


class CardSlot(QWidget):
    """一个位置：名字 + 状态 + 选图片/粘贴，**也可以直接把图片拖进来**。

    刻意**不放缩略图** —— 右边那张 A4 预览已经把两张的位置和大小都画出来了，
    再放两个小缩略图只会把预览挤小。窗口压到 1024×640 时这一点尤其要紧。
    """

    changed = Signal()
    dropped = Signal(object)  # 拖进来的文件路径，交给页面去异步读图

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: np.ndarray | None = None
        self.source: Path | None = None
        self.setAcceptDrops(True)  # 拖一张图片到这一行就等于选图片

        self._title = QLabel(label)
        self._title.setProperty("role", "section")
        self._title.setMinimumWidth(120)
        self._state = QLabel(texts.CARDS_SLOT_EMPTY)
        self._state.setProperty("role", "hint")

        self._pick = QPushButton(texts.CARDS_PICK)
        self._paste = QPushButton(texts.CARDS_PASTE)
        for button in (self._pick, self._paste):
            button.setProperty("role", "slot")
        self._pick.clicked.connect(self._on_pick)
        self._paste.clicked.connect(self._on_paste)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title)
        layout.addWidget(self._state, stretch=1)
        layout.addWidget(self._pick)
        layout.addWidget(self._paste)

    def set_label(self, label: str) -> None:
        self._title.setText(label)

    @property
    def label(self) -> str:
        return self._title.text()

    @property
    def filled(self) -> bool:
        return self.image is not None

    def set_image(self, image: np.ndarray, source: Path | None = None) -> None:
        self.image = image
        self.source = source
        self._state.setText(f"已经放好了 ✓　{source.name}" if source else "已经放好了 ✓")
        self._state.setProperty("role", "ok-small")
        self._repolish(self._state)
        self.changed.emit()

    def set_loading(self, source: Path) -> None:
        """图还在后台读，先把状态显示出来 —— 别让人以为点了没反应。"""
        self._state.setText(f"正在打开 {source.name}…")
        self._state.setProperty("role", "hint")
        self._repolish(self._state)

    def clear(self) -> None:
        self.image = None
        self.source = None
        self._state.setText(texts.CARDS_SLOT_EMPTY)
        self._state.setProperty("role", "hint")
        self._repolish(self._state)
        self.changed.emit()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def _on_pick(self) -> None:
        suffixes = " ".join(f"*{s}" for s in sorted(convert.IMAGE_SUFFIXES))
        start = paths.INBOX_DIR if paths.INBOX_DIR.is_dir() else Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择{self.label}的图片", str(start), f"图片 ({suffixes})"
        )
        if path:
            self.dropped.emit(Path(path))

    def _on_paste(self) -> None:
        image = intake.clipboard_image()
        if image is None:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                self, texts.APP_TITLE, texts.friendly_error(texts.ErrorKind.NO_IMAGE_IN_CLIPBOARD)
            )
            return
        self.set_image(image)

    # ── 拖进来 ──────────────────────────────────────────────────
    def dragEnterEvent(self, event) -> None:
        if _first_image(event.mimeData()) is not None:
            event.acceptProposedAction()
            self.setProperty("dropping", True)
            self._repolish(self)

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("dropping", False)
        self._repolish(self)

    def dropEvent(self, event) -> None:
        path = _first_image(event.mimeData())
        self.setProperty("dropping", False)
        self._repolish(self)
        if path is None:
            return
        event.acceptProposedAction()
        self.dropped.emit(path)


def _first_image(mime) -> Path | None:
    """拖进来的东西里第一张能用的图片。不是图片就当没拖。"""
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if path.suffix.lower() in convert.IMAGE_SUFFIXES:
            return path
    return None


class CardsPage(SubPage):
    printRequested = Signal(list)  # [已经拼好的 PDF 路径]

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.CARDS_TITLE, parent)
        self._config = config
        self._merged: Path | None = None

        default = config.cards.default_type
        self._types = ChoiceGroup(_type_options(), default)
        self._types.changed.connect(self._on_type_changed)

        front, back = cards.labels_for(default)
        self._slots = (CardSlot(front), CardSlot(back))
        for slot in self._slots:
            slot.changed.connect(self._on_slots_changed)
            slot.dropped.connect(lambda path, s=slot: self._load_into(s, path))

        self._preview = ImagePreview("两张都选好就会显示打印出来的样子")
        # A4 预览是这一页的主角（长辈靠它确认位置和大小），最小值给小一点，
        # 剩下的空间全给它 —— 1024×640 的小屏上也要看得见。
        self._preview.setMinimumSize(240, 200)

        self._hint = QLabel(texts.CARDS_HINT)
        self._hint.setProperty("role", "hint")
        self._hint.setWordWrap(True)

        self._save = QPushButton(texts.CARDS_SAVE_PDF)
        self._save.clicked.connect(self._save_pdf)
        self._print_button = PrimaryButton(texts.BTN_START_PRINT)
        self._print_button.clicked.connect(self._request_print)

        type_row = QHBoxLayout()
        type_row.setSpacing(12)
        type_label = QLabel(texts.CARDS_TYPE_LABEL)
        type_label.setProperty("role", "section")
        type_row.addWidget(type_label)
        type_row.addWidget(self._types, stretch=1)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addWidget(self._save, stretch=1)
        buttons.addWidget(self._print_button, stretch=3)

        # 左边放"放哪两张"，右边整列都给 A4 预览 —— 预览竖着高一点才看得清位置
        left = QVBoxLayout()
        left.setSpacing(12)
        for slot in self._slots:
            left.addWidget(slot)
        left.addWidget(self._hint)
        left.addStretch(1)

        middle = QHBoxLayout()
        middle.setSpacing(18)
        middle.addLayout(left, stretch=3)
        middle.addWidget(self._preview, stretch=2)

        self.body.addLayout(type_row)
        self.body.addLayout(middle, stretch=1)
        self.body.addLayout(buttons)
        self._sync_buttons()

    # ── 外部入口 ────────────────────────────────────────────────
    def load(self, sources: Path | list[Path] | None = None) -> None:
        """把图片放进空位。从首页/收件页跳进来时用，一次给一张或两张都行。

        位置要**一次分配好**：读图是异步的，边读边看"哪个位置还空着"的话，
        两张图会抢同一个空位（第二张把第一张顶掉）。
        """
        if sources is None:
            return
        items = [sources] if isinstance(sources, str | Path) else list(sources)
        空位 = [slot for slot in self._slots if not slot.filled]
        目标 = 空位 + [slot for slot in self._slots if slot not in 空位]
        for source, slot in zip(items, 目标, strict=False):
            self._load_into(slot, Path(source))

    def _load_into(self, slot: CardSlot, path: Path) -> None:
        """读图放到工作线程：手机拍的图十几兆，在界面线程里解码会卡住窗口。"""
        slot.set_loading(path)
        run_async(
            enhance_mod.load_image,
            path,
            on_done=lambda image: slot.set_image(image, path),
            on_failed=lambda message: self._on_load_failed(slot, message),
        )

    def _on_load_failed(self, slot: CardSlot, message: str) -> None:
        slot.clear()
        self.show_error(message)

    # ── 交互 ────────────────────────────────────────────────────
    def _on_type_changed(self, key: str) -> None:
        front, back = cards.labels_for(key)
        self._slots[0].set_label(front)
        self._slots[1].set_label(back)
        self._config.cards.default_type = key
        self._rebuild()

    def _on_slots_changed(self) -> None:
        self._sync_buttons()
        self._rebuild()

    def _sync_buttons(self) -> None:
        ready = self._merged is not None
        self._print_button.setEnabled(ready)
        self._save.setEnabled(ready)

    def _missing_label(self) -> str:
        return next((slot.label for slot in self._slots if not slot.filled), "")

    def _rebuild(self) -> None:
        self._merged = None
        self._sync_buttons()
        missing = self._missing_label()
        if missing:
            self._preview.set_message("两张都选好就会显示打印出来的样子")
            if any(slot.filled for slot in self._slots):
                self.show_error(texts.CARDS_NEED_TWO.format(missing))
            else:
                self.clear_status()
            return

        self.show_busy(texts.BUSY_CARDS)
        images = [(slot.image, slot.label) for slot in self._slots]
        run_async(
            self._compose,
            images,
            self._types.current(),
            self._config.cards.strength,
            self._config.cards.gap_mm,
            on_done=self._on_composed,
            on_failed=self.show_error,
        )

    @staticmethod
    def _compose(
        images: list[tuple[np.ndarray, str]], type_key: str, strength: int, gap_mm: float
    ) -> tuple[Path, cards.Layout, bytes]:
        """在工作线程里跑：去底 → 定尺寸 → 排版 → PDF → 预览图。"""
        options = enhance_mod.EnhanceOptions(mode=enhance_mod.MODE_MIXED, strength=strength)
        items = [
            cards.prepare_card(image, type_key, options=options, label=label)
            for image, label in images
        ]
        pdf_path, layout = cards.merge_to_pdf(items, gap_mm=gap_mm)
        return pdf_path, layout, convert.render_page_png(pdf_path, 0, dpi=_PREVIEW_DPI)

    def _on_composed(self, result: tuple[Path, cards.Layout, bytes]) -> None:
        pdf_path, layout, preview = result
        self._merged = pdf_path
        self._preview.set_png(preview)
        self._sync_buttons()
        说明 = cards.describe(layout)
        备注 = "　".join(
            dict.fromkeys(p.item.note for p in layout.placements if p.item.note)
        )  # 去重保序
        if layout.exact_size:
            self.show_done(f"{说明}\n{备注}" if 备注 else 说明)
        else:
            self.show_error(f"{说明}\n{备注}" if 备注 else 说明)

    # ── 输出 ────────────────────────────────────────────────────
    def _request_print(self) -> None:
        if self._merged is not None:
            self.printRequested.emit([self._merged])

    def _save_pdf(self) -> None:
        if self._merged is None:
            return
        target_dir = paths.output_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / self._merged.name
        try:
            target.write_bytes(self._merged.read_bytes())
        except OSError:
            logger.exception("保存证件 PDF 失败")
            self.show_error("存不下这个文件，硬盘可能满了")
            return
        self.show_done(f"{texts.DONE_SAVED}\n{target}")
