"""能点、能拖图片进来的框。

首页那两张卡片（照片变清楚 / 照片转成文字文档）原来是"先弹文件对话框、
再进页面"，用户反馈要**先进页面**，在页面上再选图 —— 所以每个吃图片的页面
都要有这么一块地方：空着写「点这里选图片 / 也可以把图片拖进来」，
点一下由页面去开对话框（默认目录是工作区，那是页面才知道的事），
也可以直接把文件拖进来。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ... import texts
from ...core import convert


def repolish(widget: QWidget) -> None:
    """property 选择器改了之后要重刷样式，否则边框颜色不会跟着变。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def first_image(mime) -> Path | None:
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


class DropFrame(QFrame):
    """点一下 = 选图片，拖一张图片进来 = 选图片。框里放什么由使用方决定。"""

    pickRequested = Signal()  # 点了框：让页面去开"选图片"对话框
    dropped = Signal(object)  # 拖进来的文件路径

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.pickRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if first_image(event.mimeData()) is not None:
            event.acceptProposedAction()
            self.setProperty("dropping", True)
            repolish(self)

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("dropping", False)
        repolish(self)

    def dropEvent(self, event) -> None:
        path = first_image(event.mimeData())
        self.setProperty("dropping", False)
        repolish(self)
        if path is None:
            return
        event.acceptProposedAction()
        self.dropped.emit(path)


class ImageDropZone(DropFrame):
    """一整块"把图片放这里"的空地。页面上还没有图片时占着预览的位置。"""

    def __init__(self, hint: str = texts.PICK_IMAGE_HINT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "dropzone")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 200)

        self._label = QLabel(hint)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setProperty("role", "dropzone-text")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(self._label)

    def set_hint(self, hint: str) -> None:
        self._label.setText(hint)
