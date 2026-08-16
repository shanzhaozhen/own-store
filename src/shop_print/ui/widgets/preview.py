"""预览控件：一张大图 + 翻页。子页面统一是「大预览 → 少量大控件 → 主按钮」。"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import texts


def pixmap_from_array(array: np.ndarray) -> QPixmap:
    """numpy → QPixmap。灰度和 BGR 都吃。

    必须 .copy()：QImage 只是包了 numpy 的内存，数组一被回收图就花了。
    """
    array = np.ascontiguousarray(array)
    if array.ndim == 2:
        height, width = array.shape
        image = QImage(array.data, width, height, array.strides[0], QImage.Format.Format_Grayscale8)
    else:
        height, width = array.shape[:2]
        image = QImage(array.data, width, height, array.strides[0], QImage.Format.Format_BGR888)
    return QPixmap.fromImage(image.copy())


class ImagePreview(QFrame):
    """按窗口大小等比缩放显示一张图。"""

    def __init__(self, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "preview")
        # 最小值给得保守：一屏要同时放下预览、几个大控件和主按钮，
        # 这里要得太多会把整页挤爆（1366×768 的老屏上尤其明显）。
        self.setMinimumSize(300, 240)
        self._placeholder = placeholder
        self._pixmap: QPixmap | None = None

        self._label = QLabel(placeholder)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setProperty("role", "hint")
        self._label.setWordWrap(True)
        # Ignored：**图有多大都不能反过来撑大布局**。QLabel 设了 pixmap 之后
        # 最小尺寸提示会变成图的尺寸，父布局给不了这么多空间时就会裁掉下半张图。
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._label.setMinimumSize(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._label)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        if pixmap is None:
            self._label.setPixmap(QPixmap())
            self._label.setText(self._placeholder)
        else:
            self._rescale()

    def set_array(self, array: np.ndarray) -> None:
        self.set_pixmap(pixmap_from_array(array))

    def set_png(self, data: bytes) -> None:
        pixmap = QPixmap()
        pixmap.loadFromData(data, "PNG")
        self.set_pixmap(pixmap)

    def set_message(self, message: str) -> None:
        self._pixmap = None
        self._label.setPixmap(QPixmap())
        self._label.setText(message)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        target = self._label.size()
        if target.width() < 8 or target.height() < 8:
            return
        self._label.setPixmap(
            self._pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class PagedPreview(QWidget):
    """带翻页的预览。页码用大字，翻页按钮够大。"""

    pageRequested = Signal(int)  # 需要第几页（0 起），由外面渲染后回填

    def __init__(self, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = 0
        self._total = 0

        self.view = ImagePreview(placeholder)
        self._previous = QPushButton("◀ 上一页")
        self._next = QPushButton("下一页 ▶")
        self._counter = QLabel("")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter.setStyleSheet("font-size: 18pt; font-weight: bold; min-width: 160px;")

        self._previous.clicked.connect(lambda: self.go_to(self._index - 1))
        self._next.clicked.connect(lambda: self.go_to(self._index + 1))

        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addStretch(1)
        bar.addWidget(self._previous)
        bar.addWidget(self._counter)
        bar.addWidget(self._next)
        bar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.view, stretch=1)
        layout.addLayout(bar)
        self._sync()

    def set_total(self, total: int) -> None:
        self._total = max(0, total)
        self._index = 0
        self._sync()
        if self._total:
            self.pageRequested.emit(0)

    def current_index(self) -> int:
        return self._index

    def go_to(self, index: int) -> None:
        if not self._total:
            return
        index = max(0, min(index, self._total - 1))
        if index == self._index:
            return
        self._index = index
        self._sync()
        self.pageRequested.emit(index)

    def _sync(self) -> None:
        has_pages = self._total > 0
        self._counter.setText(f"第 {self._index + 1} 页 / 共 {self._total} 页" if has_pages else "")
        multi = self._total > 1
        self._previous.setVisible(multi)
        self._next.setVisible(multi)
        self._counter.setVisible(has_pages)
        self._previous.setEnabled(self._index > 0)
        self._next.setEnabled(self._index < self._total - 1)


class BeforeAfter(QWidget):
    """原图 / 处理后 左右对比。长辈能直接看出"变清楚了"。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.before = ImagePreview(texts.LABEL_BEFORE)
        self.after = ImagePreview(texts.LABEL_AFTER)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        for title, view in ((texts.LABEL_BEFORE, self.before), (texts.LABEL_AFTER, self.after)):
            column = QVBoxLayout()
            column.setSpacing(6)
            caption = QLabel(title)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setProperty("role", "section")
            column.addWidget(caption)
            column.addWidget(view, stretch=1)
            layout.addLayout(column, stretch=1)
