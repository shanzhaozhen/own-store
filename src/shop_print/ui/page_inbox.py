"""微信收到的文件。新文件自动出现在这里，长辈不用去文件夹里翻。

监控哪些目录、为什么聊天里的图片要走"粘贴"而不是监控，见 core/intake.py
和 docs/01-环境与设备.md。
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import paths, texts
from ..config import AppConfig
from ..core import intake
from .page_base import SubPage
from .page_ocr import open_in_explorer
from .workers import run_async

logger = logging.getLogger(__name__)

_KIND_ICONS = {"pdf": "📕", "image": "🖼️", "office": "📄", "text": "📝"}


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} 字节"


def _human_time(timestamp: float) -> str:
    delta = time.time() - timestamp
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return time.strftime("%m 月 %d 日 %H:%M", time.localtime(timestamp))


class FileCard(QFrame):
    """一个文件一张大卡片。按钮写的是"要做的事"，不是功能名。"""

    printRequested = Signal(object)
    enhanceRequested = Signal(object)
    ocrRequested = Signal(object)
    cardRequested = Signal(object)

    def __init__(self, source: intake.SourceFile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "filecard")
        self._source = source

        icon = QLabel(_KIND_ICONS.get(source.kind, "📄"))
        icon.setStyleSheet("font-size: 36pt;")
        icon.setFixedWidth(80)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name = QLabel(source.name)
        name.setStyleSheet("font-size: 19pt; font-weight: bold;")
        name.setWordWrap(True)
        meta = QLabel(f"{_human_size(source.size)}　{_human_time(source.modified)}")
        meta.setProperty("role", "hint")

        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(name)
        info.addWidget(meta)

        print_button = QPushButton("打印")
        print_button.clicked.connect(lambda: self.printRequested.emit(self._source))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        layout.addWidget(icon)
        layout.addLayout(info, stretch=1)

        if source.is_image:
            enhance_button = QPushButton("变清楚再打印")
            enhance_button.clicked.connect(lambda: self.enhanceRequested.emit(self._source))
            layout.addWidget(enhance_button)
            card_button = QPushButton("证件拼一张")
            card_button.clicked.connect(lambda: self.cardRequested.emit(self._source))
            layout.addWidget(card_button)
            ocr_button = QPushButton("转文字")
            ocr_button.clicked.connect(lambda: self.ocrRequested.emit(self._source))
            layout.addWidget(ocr_button)

        layout.addWidget(print_button)


class InboxPage(SubPage):
    printRequested = Signal(list)
    enhanceRequested = Signal(object)
    ocrRequested = Signal(object)
    cardRequested = Signal(object)
    pasteRequested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.HOME_CARD_INBOX_TITLE, parent)
        self._config = config
        self._files: list[intake.SourceFile] = []

        self._empty = QLabel(
            "还没有收到文件。\n\n"
            "顾客在微信里发过来的文件会自动出现在这里。\n"
            "如果发来的是聊天里的图片，请在微信里右键点图片、选「复制」，\n"
            "再回到这里点下面的「粘贴图片」。"
        )
        self._empty.setProperty("role", "hint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(12)
        self._show_empty()

        container = QWidget()
        container.setLayout(self._list_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)

        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        paste = QPushButton(texts.BTN_PASTE_IMAGE)
        paste.clicked.connect(self.pasteRequested.emit)
        open_inbox = QPushButton("打开「待打印」文件夹")
        open_inbox.clicked.connect(lambda: open_in_explorer(paths.INBOX_DIR))

        bar = QHBoxLayout()
        bar.setSpacing(14)
        bar.addWidget(refresh)
        bar.addWidget(paste)
        bar.addWidget(open_inbox)
        bar.addStretch(1)

        self.body.addLayout(bar)
        self.body.addWidget(scroll, stretch=1)

    def reload(self) -> None:
        """重新扫一遍监控目录。

        扫描放到工作线程：微信目录可能有上万个文件，`rglob` 在界面线程里跑
        会让窗口卡住几秒（Windows 这时画的是定格的旧画面，看着像"卡死变形"）。
        """
        self.show_busy("正在看有没有新文件…")
        run_async(
            intake.scan,
            intake.watch_dirs(self._config.intake),
            self._config.intake.recent_days,
            on_done=self._on_scanned,
            on_failed=self.show_error,
        )

    def _on_scanned(self, files: list[intake.SourceFile]) -> None:
        self.clear_status()
        self.set_files(files)

    def _show_empty(self) -> None:
        """空状态上下都留白，让说明文字落在视线正中，而不是缩在页面顶上。"""
        self._list_layout.addStretch(1)
        self._list_layout.addWidget(self._empty)
        self._empty.show()
        self._list_layout.addStretch(1)

    def set_files(self, files: list[intake.SourceFile]) -> None:
        self._files = files
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._empty:
                widget.deleteLater()

        if not files:
            self._show_empty()
            return

        self._empty.hide()
        for source in files:
            card = FileCard(source)
            card.printRequested.connect(lambda s: self.printRequested.emit([s.path]))
            card.enhanceRequested.connect(lambda s: self.enhanceRequested.emit(s.path))
            card.ocrRequested.connect(lambda s: self.ocrRequested.emit(s.path))
            card.cardRequested.connect(lambda s: self.cardRequested.emit(s.path))
            self._list_layout.addWidget(card)
        self._list_layout.addStretch(1)

    def count(self) -> int:
        return len(self._files)
